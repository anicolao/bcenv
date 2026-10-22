"""Cooperative single-user BC23 trial service. NOT a hostile-tenant sandbox.

Only public development opponents may be configured while an agent is active.
Loopback and bounded schemas prevent accidental routing/path mistakes; same-user
agents can still access host files/tools. Stop the agent before heldout evaluation.
No BoundaryAttestation or production launch gate is changed by this profile.
"""
import argparse
import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import stat
from pathlib import Path
import re
import threading
import time
import uuid

from runtime.artifacts import execute, digest
from services.public_finalist import FinalistValidation, FINALIST_ERRORS
from services.trial_profiles import UNSET, CONTINUITY, resolve_profile, trial_options
from services.storage_guard import StorageGuard
from services.scrimmage import accept_source_zip, SubmissionRejected
from services.replay_feedback_v2 import ReplayPageQuery, project_replay_feedback_page
from services.replay_feedback import FeedbackLimits
from services.own_diagnostics import project_own_diagnostics

SCHEMA = 'bcenv.local-trial.v1'
ID = re.compile(r's_[0-9a-f]{32}\Z')
MAX_REQUEST = 1500000
MAX_RESPONSE = 65536
NOTE_ERRORS = {'notes_disabled', 'note_limit', 'invalid_note', 'unknown_replay_reference',
               'note_required', 'unknown_note', 'note_mismatch', 'note_already_used'}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def read_json(path, cap=2_000_000):
    path = Path(path)
    if path.stat().st_size > cap:
        raise ValueError('artifact_limit')
    return json.loads(path.read_bytes())


@dataclass(frozen=True)
class DevelopmentGame:
    label: str
    opponent_build: str
    map_path: str
    own_slot: str

    def __post_init__(self):
        if (not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', self.label) or
                self.own_slot not in ('A', 'B')):
            raise ValueError('invalid_game')


DEFAULT_MATCH_TIMEOUT = object()


class AdapterWorker:
    """Trusted fixed executable + fixed development schedule; no client paths."""
    def __init__(self, command, games, timeout=90, spec_version='3.0.13', *,
                 match_timeout=DEFAULT_MATCH_TIMEOUT):
        if match_timeout is DEFAULT_MATCH_TIMEOUT:
            match_timeout = timeout
        if (not command or not all(type(x) is str for x in command) or
                not 1 <= len(games) <= 8 or type(timeout) is not int or not 1 <= timeout <= 120 or
                spec_version != '3.0.13' or type(match_timeout) is not int or
                not 1 <= match_timeout <= 300):
            raise ValueError('invalid_worker_config')
        self.command, self.games, self.timeout, self.spec_version = list(command), tuple(games), timeout, spec_version
        self.match_timeout = match_timeout

    def invoke(self, root, name, arguments, multiplier=1):
        checkpoint = getattr(self, 'storage_checkpoint', lambda stage: None)
        checkpoint('adapter_before')
        # Adapter subcommands have their own per-process wall timeouts. Outer
        # limit covers its bounded sequential processes, not aggregate resources.
        timeout = self.match_timeout if arguments and arguments[0] == 'run' else self.timeout
        outer_timeout = timeout * multiplier + 15
        if getattr(self, 'remaining_seconds', None):
            remaining = self.remaining_seconds()
            if remaining <= 0:
                raise ValueError('run_halted')
            # The adapter enforces this same absolute cutoff for every detached
            # child. Do not kill it first: allow bounded child-reaping/log grace.
            outer_timeout = remaining + 5
            arguments = arguments + ['--deadline-monotonic', str(self.deadline_monotonic)]
        try:
            result = execute(self.command + arguments + ['--timeout', str(timeout)],
                             root / (name + '.log'), outer_timeout)
        finally:
            checkpoint('adapter_after')
        return result['exit_code'] == 0

    def run(self, root, submission):
        source = root / 'source'
        source.mkdir()
        for item in submission.files:
            path = source / item.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(item.data)
        if not self.invoke(root, 'build', ['build', '--source', str(source), '--package', submission.package,
                           '--output', str(root / 'build')], 2):
            artifact = read_json(root / 'build' / 'artifact.json')
            steps = artifact.get('steps')
            if (artifact.get('status') != 'failed' or type(steps) is not list or not steps or
                    any(step.get('status') == 'timeout' or step.get('launch_error') or
                        type(step.get('exit_code')) is not int or step['exit_code'] < 0 for step in steps) or
                    steps[-1].get('status') != 'failed' or steps[-1]['exit_code'] == 0):
                raise ValueError('build_infrastructure_failure')
            diagnostics = ''
            for name in ('compile.log', 'verify.log'):
                path = root / 'build' / name
                if path.is_file():
                    with path.open('rb') as handle:
                        diagnostics += handle.read(8192).decode('utf-8', 'replace')
            diagnostics = diagnostics.replace(str(root), '<trial>')[:8192]
            return {'status': 'rejected', 'reason': 'compile_rejected', 'diagnostics': diagnostics}
        if not self.invoke(root, 'package', ['package', '--build', str(root / 'build'), '--output', str(root / 'package')]):
            raise ValueError('package_failure')
        return self.run_games(root)

    def run_games(self, root):
        games = []
        for index, game in enumerate(self.games):
            if getattr(self, "before_game", None):
                self.before_game(root.name, index)
            own, opponent = str(root / 'build'), game.opponent_build
            a, b = (own, opponent) if game.own_slot == 'A' else (opponent, own)
            out = root / ('game-' + str(index))
            invoked = self.invoke(root, 'game-' + str(index), ['run', '--team-a', a, '--team-b', b,
                        '--map', game.map_path, '--output', str(out)], 2)
            result = read_json(out / 'result.json')
            status = result.get('status')
            completed = (invoked and status == 'completed' and result.get('winner') in ('A', 'B')
                         and result.get('engine_version') == self.spec_version)
            rounds = None
            if completed:
                summary = read_json(out / 'replay-summary.json')
                matches = summary.get('matches')
                if type(matches) is not list or len(matches) != 1:
                    completed = False
                else:
                    rounds = matches[0].get('total_rounds')
                    if type(rounds) is not int or not 1 <= rounds <= 1000000:
                        completed, rounds = False, None
            games.append({'index': index, 'label': game.label, 'own_slot': game.own_slot,
                          'status': 'completed' if completed else 'infrastructure_failure',
                          'outcome': ('win' if result['winner'] == game.own_slot else 'loss') if completed else None,
                          'replay_sha256': digest(out / 'replay.bc23') if completed else None,
                          'total_rounds': rounds, 'terminal_reason': None})
            # No terminal-reason field is extracted by ReplaySummary. Do not
            # invent a victory condition from round count or winner.
            if not completed:
                break
        return {'status': 'complete' if all(g['status'] == 'completed' for g in games) else 'infrastructure_failure',
                'games': games, 'wins': sum(g['outcome'] == 'win' for g in games),
                'losses': sum(g['outcome'] == 'loss' for g in games)}

    def replay(self, root, submission_id, game_index, query, expected_replay_sha256, *, mode="actions"):
        if mode not in ('actions', 'own_diagnostics'):
            raise ValueError('invalid_query')
        game = self.games[game_index]
        match = root / ('game-' + str(game_index))
        result = read_json(match / 'result.json')
        if result.get('status') != 'completed':
            raise ValueError('replay_unavailable')
        replay_path = match / 'replay.bc23'
        if digest(replay_path) != expected_replay_sha256:
            raise ValueError('replay_changed')
        out = root / ('query-' + uuid.uuid4().hex)
        args = ['replay', 'diagnostics' if mode == 'own_diagnostics' else 'page', '--replay', str(match / 'replay.bc23'), '--round-start', str(query.round_start),
                '--round-end', str(query.round_end), '--record-offset', str(query.record_offset),
                '--limit', str(query.record_limit), '--output', str(out)]
        if mode == 'own_diagnostics':
            args += ['--own-team-id', '1' if game.own_slot == 'A' else '2']
        if query.robot_id is not None:
            args += ['--robot-id', str(query.robot_id)]
        if not self.invoke(root, out.name, args):
            raise ValueError('replay_unavailable')
        query_path = out / 'replay-query.json'
        if query_path.stat().st_size > 2_000_000:
            raise ValueError('query_limit')
        raw = query_path.read_bytes()
        if digest(replay_path) != expected_replay_sha256:
            raise ValueError('replay_changed')
        projector = project_own_diagnostics if mode == "own_diagnostics" else project_replay_feedback_page
        projected = projector(raw, submission_id=submission_id,
                      job_id='j_' + submission_id[2:], own_team_id=1 if game.own_slot == 'A' else 2,
                      expected_spec_version=self.spec_version, expected_query=query,
                      limits=FeedbackLimits(max_records=128, max_output_encoded_bytes=32768))
        return json.loads(projected)


class LocalTrial:
    def __init__(self, root, worker, *, max_submissions=UNSET, max_reads=UNSET, max_replay_queries=UNSET,
                 require_experiment_notes=UNSET, max_notes=UNSET, validation_games=None,
                 max_run_seconds=UNSET, max_mailbox_requests=UNSET, profile=UNSET):
        self.profile_bounds = resolve_profile(profile, max_submissions=max_submissions, max_reads=max_reads,
            max_replay_queries=max_replay_queries, require_experiment_notes=require_experiment_notes,
            max_notes=max_notes, max_run_seconds=max_run_seconds, max_mailbox_requests=max_mailbox_requests)
        self.profile = self.profile_bounds['profile']
        if self.profile == CONTINUITY and validation_games is None:
            raise ValueError('invalid_continuity_config')
        for key in ('max_submissions', 'max_reads', 'max_replay_queries', 'require_experiment_notes',
                    'max_notes', 'max_mailbox_requests'):
            setattr(self, key, self.profile_bounds[key])
        self.notes, self.used_notes, self.replay_references = {}, set(), {}
        self.audit_sequence, self.audit_hash = 0, '0' * 64
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=False)  # fresh-run ownership, never silently resume
        self.worker = worker
        self.records, self.reads, self.replays, self.requests = {}, 0, 0, 0
        self.mailbox_attempts = 0
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.closed = False
        self.halted = False
        self.finalist = None
        self.storage_guard = StorageGuard(self.root) if self.profile == CONTINUITY else None
        self.worker.storage_checkpoint = self.storage_checkpoint
        self.finalist = FinalistValidation(self, validation_games, self.profile_bounds['max_run_seconds']) if validation_games is not None else None
        self.storage_checkpoint('initialization')

    def usage_snapshot(self):
        """Actual debits, under the same lock as admission; not inferred successes."""
        with self.lock:
            return {'read_debits': self.reads, 'replay_debits': self.replays,
                    'note_debits': len(self.notes),
                    'training_version_debits': sum(r.get('phase', 'training') != 'validation'
                                                  for r in self.records.values()),
                    'mailbox_attempts': self.mailbox_attempts, 'dispatch_attempts': self.requests}

    def mailbox_debit(self, processed):
        if self.profile == CONTINUITY:
            with self.lock:
                if processed != self.mailbox_attempts + 1 or processed > self.max_mailbox_requests:
                    self.halted = True
                    raise ValueError('request_limit')
                self.mailbox_attempts = processed

    def storage_checkpoint(self, stage):
        if self.storage_guard is None:
            return None
        with self.lock:
            try:
                return self.storage_guard.check(stage)
            except (OSError, ValueError):
                self.halted = True
                if self.finalist and self.finalist.final_choice is None:
                    self.finalist.phase = 'halted'
                raise ValueError('run_halted') from None

    def audit(self, operation, response):
        # Called under self.lock. Sequence, not wall time, establishes order.
        event = {'sequence': self.audit_sequence + 1, 'time_ns': time.time_ns(),
                 'previous_sha256': self.audit_hash, 'operation': operation, 'response': response}
        event_hash = hashlib.sha256(encoded(event)).hexdigest()
        try:
            with (self.root / 'audit.jsonl').open('ab') as handle:
                handle.write(encoded(dict(event, event_sha256=event_hash)) + b'\n')
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            self.halted = True
            raise
        self.audit_sequence, self.audit_hash = event['sequence'], event_hash
        return {'sequence': event['sequence'], 'time_ns': event['time_ns'], 'event_sha256': event_hash}

    def _accept_request_source(self, request):
        value = request['source_zip_base64']
        if type(value) is not str or len(value) > 1400000:
            raise ValueError('invalid_archive')
        try:
            archive = base64.b64decode(value, validate=True)
        except (ValueError, TypeError):
            raise ValueError('invalid_archive') from None
        return accept_source_zip(archive, request['package'])

    def _note(self, request):
        if not self.require_experiment_notes:
            raise ValueError('notes_disabled')
        if self.halted:
            raise ValueError('run_halted')
        if set(request) != {'operation', 'package', 'source_zip_base64', 'text', 'replay_references'}:
            raise ValueError('invalid_note')
        if len(self.notes) >= self.max_notes:
            raise ValueError('note_limit')
        text, refs = request['text'], request['replay_references']
        if type(text) is not str or not text.strip() or len(text.encode('utf-8')) > 4096:
            raise ValueError('invalid_note')
        if (type(refs) is not list or len(refs) > 8 or any(type(r) is not str for r in refs) or
                len(set(refs)) != len(refs)):
            raise ValueError('invalid_note')
        if any(r not in self.replay_references for r in refs):
            raise ValueError('unknown_replay_reference')
        accepted = self._accept_request_source(request)
        note = {'note_id': 'n_' + uuid.uuid4().hex, 'source_sha256': accepted.content_sha256, 'package': accepted.package,
                'text': text, 'replay_references': [self.replay_references[r] for r in refs]}
        note_hash = hashlib.sha256(encoded(note)).hexdigest()
        response = dict(note, schema=SCHEMA, status='noted', note_sha256=note_hash)
        try:
            receipt = self.audit('experiment_note', response)
        except OSError:
            self.halted = True
            raise ValueError('run_halted') from None
        response['audit_receipt'] = receipt
        self.notes[note['note_id']] = json.loads(encoded(response))
        return json.loads(encoded(response))

    def _lookup(self, value):
        if type(value) is not str or not ID.fullmatch(value) or value not in self.records:
            raise ValueError('unknown_submission')
        return self.records[value]

    def _run(self, sid, accepted):
        root = self.root / sid
        try:
            root.mkdir()
            with self.lock:
                halted = self.halted or self.closed or (self.finalist is not None and self.finalist.remaining_seconds() <= 0)
            self.storage_checkpoint('training_before')
            try:
                result = ({'status': 'infrastructure_failure', 'reason': 'run_halted'} if halted
                          else self.worker.run(root, accepted))
            finally:
                self.storage_checkpoint('training_after')
            if type(result) is not dict or result.get('status') not in ('complete', 'rejected', 'infrastructure_failure'):
                raise ValueError('invalid_worker_result')
        except Exception as error:
            # Exact trusted error remains local; never release host paths.
            try:
                (root / 'failure.txt').write_text(type(error).__name__ + ': ' + str(error))
            except OSError:
                pass
            result = {'status': 'infrastructure_failure', 'reason': 'worker_failure'}
        with self.lock:
            if self.finalist and (self.closed or self.halted or self.finalist.remaining_seconds() <= 0):
                result = {'status': 'infrastructure_failure', 'reason': 'run_halted'}
            if self.finalist and result.get('status') == 'complete':
                try:
                    self.finalist.capture(sid, accepted.content_sha256)
                except Exception:
                    result = {'status': 'infrastructure_failure', 'reason': 'build_identity_failure'}
            binding = {key: self.records[sid][key] for key in ('note_id', 'note_sha256', 'note_sequence', 'submission_receipt')
                       if key in self.records[sid]}
            response = dict(result, schema=SCHEMA, submission_id=sid, source_sha256=accepted.content_sha256, **binding)
            if len(encoded(response)) > MAX_RESPONSE:
                response = {'schema': SCHEMA, 'submission_id': sid, 'source_sha256': accepted.content_sha256,
                            'status': 'infrastructure_failure', 'reason': 'output_limit', **binding}
            if response['status'] == 'infrastructure_failure':
                self.halted = True
            self.records[sid] = response
            try:
                (root / 'public-result.json').write_bytes(encoded(response))
                self.audit('complete', response)
            except OSError:
                self.halted = True
                self.records[sid] = {'schema': SCHEMA, 'submission_id': sid, 'source_sha256': accepted.content_sha256,
                                     'status': 'infrastructure_failure', 'reason': 'persistence_failure', **binding}

    def dispatch(self, request):
        with self.lock:
            if self.profile == CONTINUITY:
                self.requests += 1
                if self.requests > self.max_mailbox_requests:
                    self.halted = True
                    raise ValueError('request_limit')
            self.storage_checkpoint('request_before')
            try:
                response = self._dispatch(request)
            except Exception:
                self.storage_checkpoint('request_failed')
                raise
            try:
                self.storage_checkpoint('request_after')
            except ValueError:
                # A valid final selection is not retroactively revoked when its
                # own bounded control receipt crosses the sampled high-water mark.
                # Further work is halted and the local checkpoint records this.
                if not (type(request) is dict and request.get('operation') == 'select_finalist'
                        and self.finalist and self.finalist.final_choice is not None):
                    raise
            return response

    def _dispatch(self, request):
        with self.lock:
            if self.closed or type(request) is not dict:
                raise ValueError('invalid_request')
            op = request.get('operation')
            if self.finalist and self.finalist.remaining_seconds() <= 0:
                self.halted = True
                if self.finalist.final_choice is None:
                    self.finalist.phase = 'halted'
                self.finalist.persist()
                if op not in ('result', 'replay', 'validation_status'):
                    raise ValueError('run_halted')
            if op in ('lock_finalists', 'validation_status', 'select_finalist'):
                if self.finalist is None:
                    raise ValueError('validation_disabled')
                return self.finalist.dispatch(request)
            if op == 'note':
                return self._note(request)
            if op == 'submit':
                if self.finalist and self.finalist.phase != 'training':
                    raise ValueError('training_closed')
                if self.halted:
                    raise ValueError('run_halted')
                expected = {'operation', 'package', 'source_zip_base64'}
                if self.require_experiment_notes:
                    expected |= {'note_id', 'note_sha256'}
                    if 'note_id' not in request or 'note_sha256' not in request:
                        raise ValueError('note_required')
                if set(request) != expected:
                    raise ValueError('invalid_request')
                if len(self.records) >= self.max_submissions or sum(r['status'] == 'accepted' for r in self.records.values()) >= 2:
                    raise ValueError('submission_limit')
                accepted = self._accept_request_source(request)
                binding = {}
                if self.require_experiment_notes:
                    note_id = request['note_id']
                    if type(note_id) is not str or note_id not in self.notes:
                        raise ValueError('unknown_note')
                    note = self.notes[note_id]
                    if note_id in self.used_notes:
                        raise ValueError('note_already_used')
                    if (note['source_sha256'] != accepted.content_sha256 or note['package'] != accepted.package or
                            request['note_sha256'] != note['note_sha256'] or
                            not 0 < note['audit_receipt']['sequence'] <= self.audit_sequence):
                        raise ValueError('note_mismatch')
                    binding = {'note_id': note_id, 'note_sha256': note['note_sha256'],
                               'note_sequence': note['audit_receipt']['sequence']}
                sid = 's_' + uuid.uuid4().hex
                response = {'schema': SCHEMA, 'submission_id': sid, 'status': 'accepted', 'source_sha256': accepted.content_sha256, **binding}
                try:
                    receipt = self.audit('submit', response)
                except OSError:
                    self.halted = True
                    raise ValueError('run_halted') from None
                if self.require_experiment_notes:
                    self.used_notes.add(note_id)
                    response['submission_receipt'] = receipt
                self.records[sid] = response
                try:
                    self.executor.submit(self._run, sid, accepted)
                except Exception:
                    # Scheduling failure is terminal; never retry an uncertain dispatch.
                    self.halted = True
                    response = dict(response, status='infrastructure_failure', reason='scheduling_failure')
                    self.records[sid] = response
                    try:
                        (self.root / ('submission-failure-' + sid + '.json')).write_bytes(encoded(response))
                        self.audit('complete', response)
                    except OSError:
                        pass
                return json.loads(encoded(response))
            self.reads += 1
            if self.reads > self.max_reads:
                raise ValueError('read_limit')
            if op == 'result' and set(request) == {'operation', 'submission_id'}:
                response = json.loads(encoded(self._lookup(request['submission_id'])))
                self.audit('result', response)
                return response
            if op == 'replay' and set(request) == {'operation', 'submission_id', 'game_index', 'query'}:
                sid, index = request['submission_id'], request['game_index']
                record = self._lookup(sid)
                if (record['status'] not in ('complete', 'infrastructure_failure') or type(index) is not int or
                        not 0 <= index < len(record.get('games', [])) or
                        record['games'][index].get('status') != 'completed'):
                    raise ValueError('replay_unavailable')
                q = request['query']
                if type(q) is not dict or set(q) not in (
                        {'round_start', 'round_end', 'robot_id', 'record_offset', 'record_limit'},
                        {'round_start', 'round_end', 'robot_id', 'record_offset', 'record_limit', 'mode'}):
                    raise ValueError('invalid_query')
                mode = q.get("mode", "actions")
                if type(mode) is not str or mode not in ("actions", "own_diagnostics"):
                    raise ValueError("invalid_query")
                query = ReplayPageQuery(**{key: value for key, value in q.items() if key != "mode"})
                if query.record_limit > 128:
                    raise ValueError('invalid_query')
                self.replays += 1
                if self.replays > self.max_replay_queries:
                    raise ValueError('replay_limit')
                try:
                    replay_worker = self.finalist.worker if self.finalist and sid in self.finalist.validation_ids.values() else self.worker
                    response = replay_worker.replay(self.root / sid, sid, index, query,
                                                  record['games'][index]['replay_sha256'],
                                                  **({'mode': mode} if mode == 'own_diagnostics' else {}))
                except Exception:
                    self.halted = True
                    self.audit('halt', {'reason': 'replay_failure', 'submission_id': sid, 'game_index': index})
                    raise ValueError('replay_unavailable') from None
                replay_hash = record['games'][index]['replay_sha256']
                reference = {'reference_id': 'r_' + uuid.uuid4().hex, 'submission_id': sid,
                             'game_index': index, 'source_sha256': record['source_sha256'],
                             'replay_sha256': replay_hash, 'query': q,
                             'feedback_sha256': hashlib.sha256(encoded(response)).hexdigest()}
                if self.finalist:
                    reference.update(phase=record.get('phase', 'training'),
                                     original_submission_id=record.get('original_submission_id', sid))
                receipt = self.audit('replay', reference if self.require_experiment_notes or self.finalist else
                                     {'submission_id': sid, 'game_index': index, 'query': q})
                result = {'schema': SCHEMA, 'status': 'replay', 'submission_id': sid,
                          'game_index': index, 'replay_sha256': replay_hash, 'feedback': response}
                if self.finalist:
                    result.update(phase=reference['phase'], original_submission_id=reference['original_submission_id'],
                                  source_sha256=record['source_sha256'])
                if self.require_experiment_notes:
                    reference['audit_receipt'] = receipt
                    self.replay_references[reference['reference_id']] = json.loads(encoded(reference))
                    result['replay_reference'] = json.loads(encoded(reference))
                return result
            raise ValueError('invalid_request')

    def close(self):
        with self.lock:
            self.closed = True
        self.executor.shutdown(wait=True)
        if self.finalist and self.finalist.final_choice is None:
            with self.lock:
                self.halted = True
                self.finalist.phase = 'halted'
                try:
                    self.finalist.event('trial_closed', self.finalist.state())
                except OSError:
                    pass


def make_server(trial, port=0):
    if trial.profile == CONTINUITY:
        raise ValueError('continuity_requires_mailbox')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                self.connection.settimeout(10)
                if self.path != '/v1' or self.headers.get('Transfer-Encoding') is not None:
                    raise ValueError('invalid_request')
                size = int(self.headers.get('Content-Length', '-1'))
                if not 0 < size <= MAX_REQUEST:
                    raise ValueError('request_limit')
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise ValueError('invalid_request')
                response = trial.dispatch(json.loads(raw))
                payload = encoded(response)
                if len(payload) > MAX_RESPONSE:
                    raise ValueError('output_limit')
                code = 200
            except Exception as error:
                allowed = {'invalid_request','request_limit','invalid_archive','archive_limit','invalid_package','invalid_source',
                           'run_halted','submission_limit','unknown_submission','read_limit','replay_limit','invalid_query','replay_unavailable'}
                reason = str(error) if str(error) in allowed | NOTE_ERRORS | FINALIST_ERRORS else 'request_failed'
                payload, code = encoded({'schema': SCHEMA, 'status': 'error', 'reason': reason}), 400
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(payload)
    return HTTPServer(('127.0.0.1', port), Handler)



def checked_directory(path):
    path = Path(path).absolute()
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError('mailbox_symlink')
    if not path.is_dir():
        raise ValueError('mailbox_not_directory')
    return path


def closed_dispatch(trial, payload):
    try:
        response = trial.dispatch(json.loads(payload))
        output = encoded(response)
        if len(output) > MAX_RESPONSE:
            raise ValueError('output_limit')
        return output
    except Exception as error:
        allowed = {'invalid_request','request_limit','invalid_archive','archive_limit','invalid_package','invalid_source',
                   'run_halted','submission_limit','unknown_submission','read_limit','replay_limit','invalid_query','replay_unavailable'}
        return encoded({'schema': SCHEMA, 'status': 'error',
                        'reason': str(error) if str(error) in allowed | NOTE_ERRORS | FINALIST_ERRORS else 'request_failed'})


def serve_mailbox(trial, mailbox, *, stop=None, max_requests=UNSET, max_seconds=86400):
    """Cooperative file RPC, not authentication or protection from same-user writes.

    Fresh owner-created directories; UUID names, no-follow bounded file reads,
    atomic request/response publication. Request files are consumed; public audit
    remains in trusted run root. A crash after dispatch can leave an uncertain
    request: clients must not resubmit automatically.
    """
    if max_requests is UNSET:
        max_requests = trial.max_mailbox_requests
    if trial.profile == CONTINUITY and (type(max_requests) is not int or
            max_requests != trial.max_mailbox_requests or trial.closed or trial.halted):
        raise ValueError('invalid_mailbox_profile')
    if trial.profile == CONTINUITY:
        if type(max_seconds) is not int or max_seconds <= 0:
            raise ValueError('invalid_mailbox_profile')
        max_seconds = min(max_seconds, trial.profile_bounds['mailbox_max_seconds'])
    mailbox = Path(mailbox).absolute()
    for component in (mailbox, *mailbox.parents):
        if component.is_symlink():
            raise ValueError('mailbox_symlink')
    mailbox.mkdir(parents=True, exist_ok=False)
    requests, responses = mailbox / 'requests', mailbox / 'responses'
    requests.mkdir(); responses.mkdir()
    if trial.storage_guard is not None:
        trial.storage_guard.add_mailbox(mailbox)
        trial.storage_checkpoint('mailbox_initialization')
    ready = {'schema': SCHEMA, 'profile': 'cooperative-file-rpc'}
    if trial.finalist:
        ready['public_validation'] = trial.finalist.state()
    (mailbox / 'ready.json').write_bytes(encoded(ready))
    print(json.dumps({'mailbox': str(mailbox), 'profile': 'cooperative-file-rpc'}), flush=True)
    deadline = time.monotonic() + max_seconds
    processed = 0
    seen = set()
    try:
        while (processed < max_requests and time.monotonic() < deadline and not (stop and stop.is_set()) and
               (trial.profile != CONTINUITY or not trial.halted) and
               (trial.finalist is None or trial.finalist.remaining_seconds() > 0)):
            # Revalidate fixed directories each iteration; never follow replacement links.
            checked_directory(mailbox); checked_directory(requests); checked_directory(responses)
            pending = sorted(requests.iterdir())
            if len(pending) > 32:
                trial.halted = True
                raise ValueError('mailbox_queue_limit')
            handled = False
            for request in pending:
                if not re.fullmatch(r'[0-9a-f]{32}\.json', request.name):
                    if trial.profile == CONTINUITY:
                        handled = True; processed += 1
                        trial.mailbox_debit(processed)
                        trial.storage_checkpoint('mailbox_invalid_name')
                        request.unlink()
                        if processed >= max_requests:
                            break
                    continue
                handled = True; processed += 1
                trial.mailbox_debit(processed)
                trial.storage_checkpoint('mailbox_request_before')
                if request.name in seen:
                    request.unlink()
                    if processed >= max_requests:
                        break
                    continue
                seen.add(request.name)
                response = encoded({'schema': SCHEMA, 'status': 'error', 'reason': 'invalid_request'})
                try:
                    fd = os.open(request, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    with os.fdopen(fd, 'rb') as handle:
                        metadata = os.fstat(handle.fileno())
                        if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= MAX_REQUEST:
                            raise ValueError('request_limit')
                        payload = handle.read(MAX_REQUEST + 1)
                    if len(payload) != metadata.st_size:
                        raise ValueError('request_limit')
                    response = closed_dispatch(trial, payload)
                except (OSError, ValueError):
                    pass
                temporary = responses / (request.stem + '.tmp-' + uuid.uuid4().hex)
                with temporary.open('xb') as handle:
                    handle.write(response)
                os.replace(temporary, responses / request.name)
                trial.storage_checkpoint('mailbox_response_after')
                request.unlink()
                if processed >= max_requests:
                    break
            if not handled:
                time.sleep(.05)
    finally:
        trial.halted = True
        (mailbox / 'stopped.json').write_bytes(encoded({'schema': SCHEMA, 'processed': processed,
            **({'dispatch_attempts': trial.requests, 'usage': trial.usage_snapshot()}
               if trial.profile == CONTINUITY else {})}))
        trial.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, help='Trusted JSON configuration, never supplied by competitor')
    parser.add_argument('--root', required=True)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--mailbox', help='Fresh shared directory for cooperative file RPC instead of HTTP')
    args = parser.parse_args()
    config = read_json(args.config)
    worker = AdapterWorker(config['command'], [DevelopmentGame(**g) for g in config['games']], config.get('timeout', 90),
                           match_timeout=config.get('match_timeout', DEFAULT_MATCH_TIMEOUT))
    options = trial_options(config)
    if options['profile'] == CONTINUITY and not args.mailbox:
        raise ValueError('continuity_requires_mailbox')
    trial = LocalTrial(args.root, worker, **options)
    if args.mailbox:
        serve_mailbox(trial, args.mailbox)
        return
    server = make_server(trial, args.port)
    print(json.dumps({'endpoint': 'http://127.0.0.1:' + str(server.server_port) + '/v1', 'profile': 'cooperative-public-development'}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        trial.close()


if __name__ == '__main__':
    main()
