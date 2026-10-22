"""Opt-in public selection phase. Cooperative integrity, not hostile isolation.

Never resumes existing roots: persisted phase and hash-chain audit are evidence,
not a recovery queue. No uncertain game or lock operation may be retried.
"""
import json
import hashlib
import os
from pathlib import Path
import shutil
import time
import uuid

from runtime.artifacts import digest, identity, inventory
from services.scrimmage import accept_source_zip

FINALIST_ERRORS = {'validation_disabled', 'training_closed', 'invalid_finalists',
                   'training_pending', 'finalists_locked', 'validation_incomplete',
                   'selection_complete', 'game_limit'}


def build_identity(root, normalized_source):
    """Bind verified source/classes/package, not merely a mutable status label."""
    root = Path(root)
    artifact = json.loads((root / 'build/artifact.json').read_text())
    source = inventory(root / 'source')
    classes = inventory(root / 'build/classes')
    package = json.loads((root / 'package/package.json').read_text())
    accepted = accept_source_zip((root / 'package/source.zip').read_bytes(), artifact['package'])
    if (artifact.get('status') != 'verified' or not source or not classes or
            source != artifact['source_files'] or source != inventory(root / 'build/source') or
            identity(source) != artifact['source_sha256'] or classes != artifact['binary_files'] or
            identity(classes) != artifact['binary_sha256'] or
            accepted.content_sha256 != normalized_source or
            {f.path: hashlib.sha256(f.data).hexdigest() for f in accepted.files} != source or
            package.get('source_sha256') != artifact['source_sha256'] or
            package.get('binary_sha256') != artifact['binary_sha256'] or
            package.get('verified_engine_sha256') != artifact['engine_sha256'] or
            package.get('zip_sha256') != digest(root / 'package/source.zip')):
        raise ValueError('changed_verified_build')
    return {'source_sha256': normalized_source, 'binary_sha256': artifact['binary_sha256'],
            'engine_sha256': artifact['engine_sha256'],
            'files': {name: inventory(root / name) for name in ('source', 'build', 'package')}}


class FinalistValidation:
    def __init__(self, trial, validation_games, max_run_seconds=2700):
        from services.local_trial import AdapterWorker, DevelopmentGame
        self.trial = trial
        if type(max_run_seconds) is not int or not 1 <= max_run_seconds <= 2700:
            raise ValueError('invalid_deadline_config')
        self.max_run_seconds = max_run_seconds
        self.started_unix = time.time()
        self.started_monotonic = time.monotonic()
        self.deadline_monotonic = self.started_monotonic + max_run_seconds
        games = tuple(DevelopmentGame(**g) for g in validation_games)
        if (type(trial.max_submissions) is not int or trial.max_submissions != 8 or
                len(trial.worker.games) != 8 or len(games) != 8):
            raise ValueError('invalid_validation_config')
        # Both phases have two opponents/maps in both slots, with no duplicate cell.
        def cells(schedule):
            result = {(str(Path(g.opponent_build).resolve()), str(Path(g.map_path).resolve()), g.own_slot) for g in schedule}
            if (len(result) != 8 or len({x[0] for x in result}) != 2 or
                    len({x[1] for x in result}) != 2 or len({g.label for g in schedule}) != 8):
                raise ValueError('invalid_validation_config')
            return result
        training, validation = cells(trial.worker.games), cells(games)
        if ({x[0] for x in training} != {x[0] for x in validation} or
                {x[1] for x in training} & {x[1] for x in validation}):
            raise ValueError('invalid_validation_config')
        self.worker = AdapterWorker(trial.worker.command, games, trial.worker.timeout, trial.worker.spec_version,
                                    match_timeout=trial.worker.match_timeout)
        self.phase, self.finalists, self.validation_ids = 'training', [], {}
        self.provisional_choice = self.final_choice = None
        self.identities = {}
        self.attempts = []
        self.locked_monotonic = self.selected_monotonic = None
        self.schedule_identity = self._schedule_identity()
        trial.worker.before_game = self.before_game
        self.worker.before_game = self.before_game
        trial.worker.remaining_seconds = self.execution_budget
        self.worker.remaining_seconds = self.execution_budget
        trial.worker.deadline_monotonic = self.deadline_monotonic
        self.worker.deadline_monotonic = self.deadline_monotonic
        self.persist()

    def _schedule_identity(self):
        games = (*self.trial.worker.games, *self.worker.games)
        return {'maps': {str(Path(g.map_path).resolve()): digest(g.map_path) for g in games},
                'opponents': {str(Path(g.opponent_build).resolve()): inventory(g.opponent_build) for g in games},
                'command': list(self.worker.command), 'command_sha256': digest(self.worker.command[0]),
                'training_command': list(self.trial.worker.command),
                'training_command_sha256': digest(self.trial.worker.command[0])}

    def remaining_seconds(self):
        return max(0., self.deadline_monotonic - time.monotonic())

    def execution_budget(self):
        return 0. if self.trial.closed or self.trial.halted else self.remaining_seconds()

    def state(self):
        training = [a for a in self.attempts if a['phase'] == 'training']
        return {'schema': 'bcenv.public-finalists.v1', 'phase': self.phase,
                'finalists': list(self.finalists), 'validation_ids': dict(self.validation_ids),
                'provisional_choice': self.provisional_choice, 'final_choice': self.final_choice,
                'locked_monotonic': self.locked_monotonic, 'selected_monotonic': self.selected_monotonic,
                'halted': self.trial.halted,
                'game_budget': 80, 'max_run_seconds': self.max_run_seconds,
                'started_unix': self.started_unix,
                'deadline_unix': self.started_unix + self.max_run_seconds,
                'started_monotonic': self.started_monotonic,
                'deadline_monotonic': self.deadline_monotonic,
                'remaining_seconds': self.remaining_seconds(), 'training_games_attempted': len(training),
                'validation_games_attempted': len(self.attempts) - len(training),
                'games_attempted': len(self.attempts),
                'validation_games_reserved': 16 if self.finalists else 0}

    def persist(self):
        # Atomic evidence replacement; failure halts before subsequent dispatch.
        target = self.trial.root / 'phase.json'
        temporary = self.trial.root / ('phase-' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('x') as f:
                json.dump(self.state(), f, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, target)
            fd = os.open(self.trial.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            self.trial.halted = True
            raise

    def event(self, operation, response):
        self.persist()
        return self.trial.audit(operation, response)

    def before_game(self, sid, index):
        with self.trial.lock:
            if self.trial.halted or self.trial.closed or self.remaining_seconds() <= 0:
                raise ValueError('run_halted')
            if (sid not in self.trial.records or type(index) is not int or not 0 <= index < 8):
                raise ValueError('game_limit')
            validation = sid in self.validation_ids.values()
            phase = 'validation' if validation else 'training'
            limit = 16 if validation else 64
            if (len([a for a in self.attempts if a['phase'] == phase]) >= limit or
                    len(self.attempts) >= 80 or any(a['submission_id'] == sid and a['index'] == index for a in self.attempts)):
                raise ValueError('game_limit')
            if self._schedule_identity() != self.schedule_identity:
                self.trial.halted = True
                raise ValueError('run_halted')
            checked_monotonic = time.monotonic()
            if self.trial.closed or self.trial.halted or checked_monotonic >= self.deadline_monotonic:
                raise ValueError('run_halted')
            attempt = {'submission_id': sid, 'index': index, 'phase': phase,
                       'checked_monotonic': checked_monotonic, 'deadline_monotonic': self.deadline_monotonic}
            self.attempts.append(attempt)
            self.event('game_attempt', attempt)  # charged before launching, including failures

    def capture(self, sid, source_sha256):
        charged = [a['index'] for a in self.attempts if a['submission_id'] == sid]
        if sorted(charged) != list(range(8)) or self._schedule_identity() != self.schedule_identity:
            raise ValueError('changed_training_evidence')
        self.identities[sid] = build_identity(self.trial.root / sid, source_sha256)
        with (self.trial.root / sid / 'verified-identity.json').open('x') as f:
            json.dump(self.identities[sid], f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())

    def dispatch(self, request):
        t = self.trial
        op = request['operation']
        if op == 'validation_status' and set(request) == {'operation'}:
            t.reads += 1
            if t.reads > t.max_reads:
                raise ValueError('read_limit')
            result = self.state()
            result['halted'] = t.halted
            result['audit_receipt'] = t.audit('validation_status', result)
            return result
        if t.halted:
            raise ValueError('run_halted')
        if op == 'select_finalist':
            if self.final_choice is not None:
                raise ValueError('selection_complete')
            if self.phase != 'complete':
                raise ValueError('validation_incomplete')
            if set(request) != {'operation', 'submission_id'} or request['submission_id'] not in self.finalists:
                raise ValueError('invalid_finalists')
            self.verify_finalists()
            decision_time = time.monotonic()
            if t.closed or t.halted or decision_time >= self.deadline_monotonic:
                t.halted = True
                self.phase = 'halted'
                self.persist()
                raise ValueError('run_halted')
            self.selected_monotonic = decision_time
            self.final_choice = request['submission_id']
            self.phase = 'selected'
            result = self.state()
            result['audit_receipt'] = self.event('select_finalist', result)
            return result
        if op != 'lock_finalists' or set(request) != {'operation', 'submission_ids', 'provisional_choice'}:
            raise ValueError('invalid_request')
        if self.phase != 'training':
            raise ValueError('finalists_locked')
        ids = request['submission_ids']
        if (type(ids) is not list or len(ids) != 2 or any(type(s) is not str for s in ids) or
                len(set(ids)) != 2 or request['provisional_choice'] not in ids):
            raise ValueError('invalid_finalists')
        if any(r['status'] == 'accepted' for r in t.records.values()):
            raise ValueError('training_pending')
        if any(s not in self.identities or t.records.get(s, {}).get('status') != 'complete' or
               len(t.records[s].get('games', [])) != 8 or
               any(g.get('status') != 'completed' for g in t.records[s]['games']) for s in ids):
            raise ValueError('invalid_finalists')
        try:
            for sid in ids:
                if build_identity(t.root / sid, t.records[sid]['source_sha256']) != self.identities[sid]:
                    raise ValueError('changed_verified_build')
            if self._schedule_identity() != self.schedule_identity:
                raise ValueError('changed_schedule')
        except Exception:
            t.halted = True
            raise ValueError('run_halted') from None
        decision_time = time.monotonic()
        if t.closed or t.halted or decision_time >= self.deadline_monotonic:
            t.halted = True
            self.phase = 'halted'
            self.persist()
            raise ValueError('run_halted')
        self.locked_monotonic = decision_time
        self.finalists, self.provisional_choice = list(ids), request['provisional_choice']
        self.validation_ids = {sid: 's_' + uuid.uuid4().hex for sid in ids}
        self.phase = 'locked'
        result = self.state()
        try:
            receipt = self.event('lock_finalists', result)
            for sid, vid in self.validation_ids.items():
                pin = self.identities[sid]
                t.records[vid] = {'schema': 'bcenv.local-trial.v1', 'status': 'accepted',
                    'phase': 'validation', 'submission_id': vid, 'original_submission_id': sid,
                    'source_sha256': pin['source_sha256'], 'binary_sha256': pin['binary_sha256'],
                    'lock_receipt': receipt}
            t.executor.submit(self.run)
        except Exception:
            t.halted = True
            self.phase = 'halted'
            try:
                self.event('validation_halt', self.state())
            except OSError:
                pass
            raise ValueError('run_halted') from None
        result['audit_receipt'] = receipt
        return result

    def verify_finalists(self):
        try:
            for sid in self.finalists:
                if build_identity(self.trial.root / sid, self.identities[sid]['source_sha256']) != self.identities[sid]:
                    raise ValueError('changed_verified_build')
        except Exception:
            self.trial.halted = True
            raise ValueError('run_halted') from None

    def run(self):
        t = self.trial
        with t.lock:
            if t.halted or t.closed or self.remaining_seconds() <= 0:
                t.halted = True
                self.phase = 'halted'
                for vid in self.validation_ids.values():
                    t.records[vid] = dict(t.records[vid], status='not_run', reason='run_halted')
                try:
                    self.event('validation_halt', self.state())
                except OSError:
                    pass
                return
            self.phase = 'validation'
            try:
                self.event('validation_start', self.state())
            except OSError:
                self.phase = 'halted'
                for vid in self.validation_ids.values():
                    t.records[vid] = dict(t.records[vid], status='not_run', reason='persistence_failure')
                return
        for sid in self.finalists:
            vid = self.validation_ids[sid]
            root = t.root / vid
            try:
                with t.lock:
                    if t.halted or t.closed or self.remaining_seconds() <= 0:
                        raise ValueError('run_halted')
                    self.verify_finalists()
                root.mkdir()
                for name in ('source', 'build', 'package'):
                    shutil.copytree(t.root / sid / name, root / name)
                if build_identity(root, self.identities[sid]['source_sha256']) != self.identities[sid]:
                    raise ValueError('snapshot_changed')
                result = self.worker.run_games(root)
                charged = [a['index'] for a in self.attempts if a['submission_id'] == vid]
                if (sorted(charged) != list(range(8)) or t.halted or t.closed or self.remaining_seconds() <= 0 or
                        result.get('status') != 'complete' or len(result.get('games', [])) != 8 or
                        any(g.get('status') != 'completed' for g in result.get('games', []))):
                    result['status'] = 'infrastructure_failure'
                if build_identity(root, self.identities[sid]['source_sha256']) != self.identities[sid]:
                    raise ValueError('snapshot_changed')
                with t.lock:
                    self.verify_finalists()
                    if self._schedule_identity() != self.schedule_identity:
                        raise ValueError('changed_schedule')
            except Exception as error:
                result = {'status': 'infrastructure_failure', 'reason': 'validation_worker_failure'}
                try:
                    root.mkdir(exist_ok=True)
                    (root / 'failure.txt').write_text(type(error).__name__ + ': ' + str(error))
                except OSError:
                    pass
            with t.lock:
                response = dict(t.records[vid], **result)
                try:
                    from services.local_trial import encoded, MAX_RESPONSE
                    if len(encoded(response)) > MAX_RESPONSE:
                        raise ValueError('output_limit')
                    t.records[vid] = response
                    (root / 'public-result.json').write_bytes(encoded(response))
                    t.audit('validation_complete', response)
                except Exception:
                    response = dict(t.records[vid], status='infrastructure_failure', reason='persistence_failure')
                    t.records[vid] = response
                if response['status'] != 'complete':
                    t.halted = True
                    self.phase = 'halted'
                    for original, pending in self.validation_ids.items():
                        if t.records[pending]['status'] == 'accepted':
                            t.records[pending] = dict(t.records[pending], status='not_run', reason='validation_halted')
                    try:
                        self.event('validation_halt', self.state())
                    except OSError:
                        pass
                    return
        with t.lock:
            self.phase = 'complete'
            try:
                self.event('validation_finished', self.state())
            except OSError:
                t.halted = True
