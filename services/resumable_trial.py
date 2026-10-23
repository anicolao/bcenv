"""Opt-in real adapter orchestration; SQL is authority, epoch files are evidence.

No legacy LocalTrial recovery. Cooperative same-user fencing is not physical
process containment. Construction never schedules recovered/queued work.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import threading
import time
import uuid

from runtime.artifacts import digest
from runtime.cancellation import Cancellation
from services.epoch_authority import AuthorityDenied, AuthorityUnavailable, ClockSample
from services.local_trial import AdapterWorker, DevelopmentGame
from services.public_finalist import build_identity
from services.replay_feedback_v2 import ReplayPageQuery
from services.resumable_authority import prepare
from services.storage_guard import _open_directory, _scan_directory

PROFILE = 'resumable-v1'
MAX_MANIFEST = 1048576
MAX_EVIDENCE = 65536
MAX_STORAGE = 1073741824


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def identifier(prefix):
    return prefix + '_' + uuid.uuid4().hex


def clock():
    domain = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if not domain:
        raise ValueError('clock_domain_missing')
    return ClockSample(domain, time.time(), time.monotonic())


def paths_for(snapshot, epoch):
    return {r['epoch']: json.loads(r['paths']) for r in snapshot['epochs']}[epoch]


def publish(path, value):
    """Derived epoch evidence only, never the canonical SQL commit."""
    data = canonical(value)
    if len(data) > MAX_EVIDENCE:
        raise ValueError('evidence_limit')
    path = Path(path)
    temporary = path.with_name(path.name + '.pending-' + uuid.uuid4().hex)
    with temporary.open('xb') as handle:
        if handle.write(data) != len(data):
            raise OSError('short_evidence_write')
    os.replace(temporary, path)


def fixed_inputs(config):
    """Hash trusted public schedule/config outside every authoritative transaction."""
    if (type(config) is not dict or set(config) != {'profile','command','games','validation_games','timeout','match_timeout'} or
            config['profile'] != PROFILE or config['timeout'] != 120 or config['match_timeout'] != 300 or
            type(config['command']) is not list or len(config['command']) != 1 or
            not Path(config['command'][0]).is_absolute()):
        raise ValueError('invalid_resumable_config')
    schedules = []
    files = {config['command'][0]: digest(config['command'][0])}
    for key in ('games','validation_games'):
        games = tuple(DevelopmentGame(**g) for g in config[key])
        cells = {(str(Path(g.opponent_build).resolve()), str(Path(g.map_path).resolve()), g.own_slot) for g in games}
        if (len(games) != 8 or len(cells) != 8 or len({g.label for g in games}) != 8 or
                len({c[0] for c in cells}) != 2 or len({c[1] for c in cells}) != 2):
            raise ValueError('invalid_schedule')
        schedules.append(cells)
        for g in games:
            files[g.map_path] = digest(g.map_path)
            root = Path(g.opponent_build)
            for p in root.rglob('*'):
                if p.is_symlink():
                    raise ValueError('public_input_symlink')
                if p.is_file():
                    files[str(p)] = digest(p)
    if ({x[0] for x in schedules[0]} != {x[0] for x in schedules[1]} or
            {x[1] for x in schedules[0]} & {x[1] for x in schedules[1]}):
        raise ValueError('invalid_schedule')
    return files


def bounded_digest(path, size):
    if type(size) is not int or not 0 <= size <= MAX_STORAGE:
        raise ValueError('artifact_size_limit')
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        remaining = size
        while remaining:
            block = handle.read(min(65536, remaining))
            if not block:
                raise ValueError('artifact_short')
            h.update(block); remaining -= len(block)
        if handle.read(1):
            raise ValueError('artifact_grew')
    return h.hexdigest()


def checked_manifest(root, manifest):
    """Rehash committed names only, never promote loose files into authority."""
    root = Path(root)
    if type(manifest) is not dict or type(manifest.get('files')) is not dict:
        raise ValueError('invalid_manifest')
    if len(canonical(manifest)) > MAX_MANIFEST:
        raise ValueError('manifest_limit')
    sizes = manifest.get('sizes')
    if type(sizes) is not dict or set(sizes) != set(manifest['files']) or any(type(v) is not int or v < 0 for v in sizes.values()) or sum(sizes.values()) > MAX_STORAGE:
        raise ValueError('invalid_manifest_sizes')
    for name, expected in manifest['files'].items():
        rel = PurePosixPath(name)
        if rel.is_absolute() or '..' in rel.parts or not rel.parts or rel.parts[0] not in ('source','build','package',*(f'game-{i}' for i in range(8))):
            raise ValueError('invalid_manifest_path')
        path = root
        for part in rel.parts:
            path /= part
            if path.is_symlink():
                raise ValueError('artifact_symlink')
        if not path.is_file() or bounded_digest(path, sizes[name]) != expected:
            raise ValueError('committed_artifact_changed')
    if build_identity(root, manifest['source_sha256']) != manifest['build_identity']:
        raise ValueError('committed_build_changed')
    return hashlib.sha256(canonical(manifest)).hexdigest()


def capture_batch(root, batch, result, deadline, games):
    if result.get('status') != 'complete':
        return None
    root = Path(root)
    binding = build_identity(root, batch['source'])
    def process(receipt):
        if (receipt.get('status') != 'succeeded' or receipt.get('exit_code') != 0 or
                receipt.get('child_reaped') is not True or receipt.get('deadline_monotonic') != deadline or
                type(receipt.get('reaped_monotonic')) not in (int,float) or receipt['reaped_monotonic'] > deadline):
            raise ValueError('child_evidence_unverified')
    build = json.loads((root/'build/artifact.json').read_bytes())
    for receipt in build['steps']:
        process(receipt)
    if len(result.get('games', [])) != 8:
        raise ValueError('incomplete_batch')
    for i, game in enumerate(games):
        raw = json.loads((root/f'game-{i}/result.json').read_bytes())
        process(raw['process']); process(raw['extraction'])
        entry = result['games'][i]
        if (entry['index'] != i or entry['label'] != game.label or entry['own_slot'] != game.own_slot or
                entry['status'] != 'completed' or digest(root/f'game-{i}/replay.bc23') != entry['replay_sha256'] or
                raw['engine_sha256'] != binding['engine_sha256'] or raw['map_sha256'] != digest(game.map_path)):
            raise ValueError('game_binding_changed')
    files, sizes = {}, {}
    for name in ('source','build','package',*(f'game-{i}' for i in range(8))):
        for path in (root/name).rglob('*'):
            if path.is_symlink():
                raise ValueError('artifact_symlink')
            if path.is_file():
                name = path.relative_to(root).as_posix()
                sizes[name] = path.stat().st_size
                files[name] = bounded_digest(path, sizes[name])
    manifest = dict(files=files, sizes=sizes, build_identity=binding, source_sha256=batch['source'], batch_id=batch['id'])
    checked_manifest(root, manifest)
    return manifest


def storage_size(authority, snapshot, *, recovery_epoch=None):
    total = 0
    for e in snapshot['epochs']:
        for key, value in json.loads(e['paths']).items():
            if key == 'author':
                continue  # Preserve prior service-owned storage scope, not candidate workspace.
            path = Path(value)
            if e['epoch'] == recovery_epoch and not path.exists():
                continue  # Only the explicitly closed new epoch is still being created.
            fd = _open_directory(path)
            try:
                total += _scan_directory(fd)[0]
            finally:
                os.close(fd)
    for suffix in ('','-wal','-shm'):
        path = Path(str(authority.path)+suffix)
        if path.exists():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('authority_storage_changed')
            total += info.st_size
    return total


class ResumableTrial:
    def __init__(self, authority, epoch, *, worker_factory=AdapterWorker,
                 executor_factory=ThreadPoolExecutor, capture=capture_batch):
        self.authority, self.epoch, self.capture = authority, epoch, capture
        snap = authority.inspect()
        if type(epoch) is not int or snap['run']['epoch'] != epoch or snap['run']['state'] != 'ACTIVE':
            raise AuthorityDenied('admission_closed')
        self.paths = paths_for(snap, epoch)
        bound = json.loads(snap['run']['config'])['config']
        self.config, self.inputs = bound['trial'], bound['inputs']
        if fixed_inputs(self.config) != self.inputs:
            raise ValueError('public_input_changed')
        self.root, self.artifacts = Path(self.paths['service']), Path(self.paths['artifacts'])
        self.root.mkdir(parents=True, exist_ok=False)
        if epoch == 0:
            self.artifacts.mkdir(parents=True, exist_ok=False)
        elif not self.artifacts.is_dir():
            raise ValueError('recovery_artifacts_missing')
        self.deadline = snap['run']['mono_deadline']
        self.wall_deadline = snap['run']['wall_deadline']
        self.stop_requested = threading.Event()
        self.cancellation = Cancellation()
        self.close_lock = threading.Lock()
        self.cleanup_complete = False
        self.close_failure = None
        self.monitor_stop = threading.Event()
        self.monitor = None
        self.monitor_error = None
        self.workers = []
        self.executor = None
        try:
            for key in ('games','validation_games'):
                worker = worker_factory(self.config['command'], tuple(DevelopmentGame(**g) for g in self.config[key]),
                                        120, match_timeout=300)
                worker.stop_requested = self.stop_requested
                worker.cancellation = self.cancellation
                worker.cancel_trial = self.request_cancel
                worker.cleanup_unverified = False
                worker.remaining_seconds = self.remaining_seconds
                worker.deadline_monotonic = self.deadline
                worker.storage_checkpoint = self.storage_checkpoint
                worker.before_game = self.before_game
                self.workers.append(worker)
            self.executor = executor_factory(max_workers=1)
        except BaseException:
            self.cancellation.close()
            raise

    def remaining_seconds(self):
        return max(0., min(self.deadline-time.monotonic(), self.wall_deadline-time.time()))

    def current_epoch_active(self):
        snap = self.authority.current_state()
        ok = (snap['run']['epoch'] == self.epoch and snap['run']['state'] == 'ACTIVE' and
              not snap['meta']['halted'] and not snap['control']['closed'])
        if not ok:
            self.request_cancel()
        return ok

    def start_monitor(self):
        if self.monitor is not None:
            raise ValueError('monitor_already_started')
        def observe():
            try:
                while not self.monitor_stop.wait(.1):
                    if self.remaining_seconds() <= 0 or not self.current_epoch_active():
                        self.request_cancel()
                        return
            except BaseException as error:
                self.monitor_error = error
                self.request_cancel()
        self.monitor = threading.Thread(target=observe, name='epoch-observer', daemon=True)
        self.monitor.start()

    def request_cancel(self):
        self.stop_requested.set()
        self.cancellation.cancel()

    def storage_checkpoint(self, stage):
        snap = self.authority.inspect()
        if snap['run']['epoch'] != self.epoch:
            self.request_cancel(); raise AuthorityDenied('stale_epoch')
        total = 0
        error = None
        try:
            total = storage_size(self.authority, snap)
        except (OSError, ValueError, RecursionError):
            error = 'storage_scan_failed'
        result = self.authority.storage_observation(self.epoch, identifier('storage'), total, error)
        if result['halted']:
            self.request_cancel(); raise ValueError('run_halted')

    def before_game(self, sid, index):
        self.storage_checkpoint('before_game')
        if fixed_inputs(self.config) != self.inputs:
            self.authority.halt(self.epoch, identifier('inputs'), 'public_input_changed')
            raise ValueError('public_input_changed')
        if self.stop_requested.is_set():
            raise ValueError('run_halted')
        # Admission is durable; OS process creation afterward is NOT atomic with it.
        self.authority.before_game(self.epoch, identifier('game'), sid, index)

    def _verified(self, ids):
        return {sid: checked_manifest(self.artifacts/sid, json.loads(self.authority.batch(sid)['manifest'])) for sid in ids}

    def request(self, request_id, body):
        if self.stop_requested.is_set():
            raise ValueError('run_halted')
        self.storage_checkpoint('request_before')
        prepared = prepare(body)
        op = body.get('operation') if type(body) is dict else None
        if op in ('lock_finalists','select_finalist') and not prepared.get('error'):
            try:
                if op == 'lock_finalists':
                    ids = body.get('submission_ids', [])
                else:
                    meta = self.authority.inspect()['meta']
                    ids = json.loads(meta['finalists']) + list(json.loads(meta['validation_ids']).values())
                prepared['verified_manifests'] = self._verified(ids)
            except (OSError, ValueError, KeyError, TypeError, AuthorityDenied):
                prepared['error'] = 'evidence_unverified'
        admitted = self.authority.request(self.epoch, request_id, body, prepared=prepared,
                                          attempt_id=identifier('ingress'))
        work = admitted['work']
        response = admitted['response']
        if work is not None:
            if work['kind'] == 'replay':
                self._replay(work['id'])  # Original admission remains immutable; replay_result is a charged read.
            else:
                ids = [work['id']] if work['kind']=='batch' else work['ids']
                try:
                    self.executor.submit(self._batches, ids)
                except BaseException:
                    # Scheduling may already have begun: no substitute terminal result.
                    # Owner close or operator advance abandons every outstanding job.
                    self.request_cancel()
                    raise
        self.storage_checkpoint('request_after')
        return response

    def _batches(self, ids):
        for sid in ids:
            if self.stop_requested.is_set():
                return
            try:
                self.authority.start_batch(self.epoch, identifier('start'), sid)
                batch = self.authority.batch(sid)
                root = self.artifacts/sid
                root.mkdir(exist_ok=False)
                worker = self.workers[batch['phase']=='validation']
                if batch['phase']=='validation':
                    original = self.authority.batch(batch['original'])
                    manifest = json.loads(original['manifest'])
                    checked_manifest(self.artifacts/original['id'], manifest)
                    for name in ('source','build','package'):
                        shutil.copytree(self.artifacts/original['id']/name, root/name)
                    if build_identity(root, batch['source']) != manifest['build_identity']:
                        raise ValueError('copy_changed')
                    result = worker.run_games(root)
                else:
                    result = worker.run(root, self.authority.source(batch['source']))
                if self.stop_requested.is_set():
                    return
                manifest = self.capture(root, batch, result, self.deadline, worker.games)
                self.storage_checkpoint('batch_complete')
                self.authority.finish_batch(self.epoch, identifier('complete'), sid, result, manifest)
                if result['status']=='infrastructure_failure':
                    self.request_cancel(); return
            except (AuthorityDenied, AuthorityUnavailable):
                self.request_cancel(); return  # Stale or closed authority never receives a fallback result.
            except BaseException:
                try:
                    self.authority.finish_batch(self.epoch, identifier('failed'), sid,
                        {'status':'infrastructure_failure','reason':'worker_failure'})
                finally:
                    self.request_cancel()
                return

    def _replay(self, replay_id):
        try:
            replay = self.authority.replay(replay_id)
            batch = self.authority.batch(replay['batch'])
            checked_manifest(self.artifacts/batch['id'], json.loads(batch['manifest']))
            q = json.loads(replay['query']); mode = q.get('mode','actions')
            query = ReplayPageQuery(**{k:v for k,v in q.items() if k!='mode'})
            worker = self.workers[batch['phase']=='validation']
            result = json.loads(batch['response'])
            feedback = worker.replay(self.artifacts/batch['id'], batch['id'], replay['game_index'], query,
                                     result['games'][replay['game_index']]['replay_sha256'], mode=mode)
            checked_manifest(self.artifacts/batch['id'], json.loads(batch['manifest']))
            if self.stop_requested.is_set():
                raise AuthorityDenied('run_halted')
            return self.authority.finish_replay(self.epoch, identifier('replay_done'), replay_id, feedback)['response']
        except (AuthorityDenied, AuthorityUnavailable):
            self.request_cancel(); raise
        except BaseException:
            try:
                response = self.authority.finish_replay(self.epoch, identifier('replay_failed'), replay_id,
                                                       error='replay_failure')['response']
            finally:
                self.request_cancel()
            return response

    def close(self):
        with self.close_lock:
            if self.cleanup_complete:
                if self.close_failure is not None:
                    raise self.close_failure
                return
            error = None
            try:
                self.request_cancel()
            except BaseException as caught:
                error = caught
            # Logical close linearizes here, before physical waits. Notification
            # itself is not a SQL transaction or a physical no-spawn guarantee.
            try:
                self.authority.logical_close(self.epoch, identifier('closed'))
            except AuthorityDenied as caught:
                if caught.code not in ('stale_epoch','admission_closed','deadline_expired','run_halted'):
                    error = error or caught
            except BaseException as caught:
                error = error or caught
            try:
                if self.executor is not None:
                    self.executor.shutdown(wait=True, cancel_futures=True)
            except BaseException as caught:
                error = error or caught
            self.monitor_stop.set()
            try:
                if self.monitor is not None:
                    self.monitor.join(2)
                    if self.monitor.is_alive():
                        raise ValueError('epoch_monitor_join_unverified')
                if any(w.cleanup_unverified for w in self.workers):
                    raise ValueError('cooperative_cleanup_unverified')
            except BaseException as caught:
                error = error or caught
            try:
                self.cancellation.close()
            except BaseException as caught:
                error = error or caught
            error = error or self.monitor_error
            self.cleanup_complete = True  # Finalization attempted once, never retry ambiguous close.
            if error is None:
                try:
                    publish(self.root/'cleanup.json', dict(epoch=self.epoch, authority_id=self.authority.authority_id,
                            direct_owned_cleanup=True, supervisor_death_containment=False))
                except BaseException as caught:
                    error = caught
            self.close_failure = error
            if error is not None:
                raise error


def recover_committed(authority, epoch, transition_id, proof):
    """Operator-only copy of committed own evidence; NEVER scan for resumable jobs.

    A proof is a trusted operator assertion about the exact existing native task,
    not a task-creation action or authenticated platform attestation.
    """
    snap = authority.inspect()
    run = snap['run']
    if (type(epoch) is not int or run['epoch'] != epoch or run['state'] != 'RECOVERING' or
            type(proof) is not dict or proof.get('author') != run['author'] or
            proof.get('epoch') != epoch or proof.get('same_existing_task') is not True or
            proof.get('continuation_method') != 'followup_task' or
            proof.get('followup_succeeded') is not True or
            proof.get('retained_context_confirmed') is not True or
            proof.get('canonical_task') != run['author']):
        raise AuthorityDenied('recovery_proof_missing')
    # No fallback to a new author or unchanged mutable workspace.
    expected = paths_for(snap, epoch)
    artifacts, author = Path(expected['artifacts']), Path(expected['author'])
    for path in (artifacts, author):
        if path.exists():
            raise ValueError('recovery_destination_not_fresh')
    def fence():
        now = authority.clock()
        if (now.domain != run['domain'] or now.wall < run['last_wall'] or now.mono < run['last_mono'] or
                now.wall >= run['wall_deadline'] or now.mono >= run['mono_deadline']):
            raise AuthorityDenied('recovery_clock')
        current = authority.current_state()['run']
        if current['epoch'] != epoch or current['state'] != 'RECOVERING':
            raise AuthorityDenied('recovery_changed')
    fence()
    initial_size = storage_size(authority, snap, recovery_epoch=epoch)
    if authority.storage_observation(epoch, identifier('recovery_storage'), initial_size, recovery=True)['halted']:
        raise ValueError('recovery_storage_limit')
    artifacts.mkdir(parents=True, exist_ok=False)
    author.mkdir(parents=True, exist_ok=False)
    for name in ('src','tests','work','recovered'):
        (author/name).mkdir()
    validated = {}
    # Only a terminal SQL result with its previously committed manifest survives.
    # Copy exact names into a destination never supplied to old helpers.
    for batch in snap['batches']:
        if batch['state'] != 'COMPLETE':
            continue
        manifest = json.loads(batch['manifest'])
        origin = Path(paths_for(snap, batch['epoch'])['artifacts'])/batch['id']
        checked_manifest(origin, manifest)
        target = artifacts/batch['id']
        target.mkdir()
        for name in manifest['files']:
            fence()
            destination = target/name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with (origin/name).open('rb') as source, destination.open('xb') as output:
                remaining = manifest['sizes'][name]
                while remaining:
                    fence()
                    block = source.read(min(65536, remaining))
                    if not block:
                        raise ValueError('recovery_short_copy')
                    if output.write(block) != len(block):
                        raise OSError('short_recovery_write')
                    remaining -= len(block)
                if source.read(1):
                    raise ValueError('recovery_source_grew')
        validated[batch['id']] = checked_manifest(target, manifest)
    sources = {r['source'] for r in snap['batches']} | {r['source'] for r in snap['notes']}
    for fingerprint in sorted(sources):
        fence()
        accepted = authority.source(fingerprint)
        target = author/'recovered'/'sources'/fingerprint
        target.mkdir(parents=True)
        for item in accepted.files:
            path = target/item.path
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as handle:
                handle.write(item.data)
    # Bounded individual records, not a potentially oversized combined snapshot.
    for category in ('notes','references'):
        directory = author/'recovered'/category
        directory.mkdir()
        for record in snap[category]:
            publish(directory/(record['id']+'.json'), json.loads(record['response']))
    receipts = author/'recovered'/'receipts'
    receipts.mkdir()
    for receipt in authority.candidate_history():
        fence()
        publish(receipts/(receipt['id']+'.json'), receipt)
    checkpoint = json.loads(snap['meta']['checkpoint']) if snap['meta']['checkpoint'] else None
    if checkpoint is not None:
        publish(author/'recovered/checkpoint.json', checkpoint)
    publish(author/'work/RECOVERY.json', dict(epoch=epoch, authority_id=authority.authority_id,
        unknown_submissions=[b['id'] for b in snap['batches'] if b['state']=='UNKNOWN'],
        original_deadline_monotonic=run['mono_deadline'], original_deadline_unix=run['wall_deadline'],
        uncommitted_edits_tests_and_work_lost=True, source_restored_to_active_src=False,
        physical_old_helpers='unknown', continuation='same canonical task only'))
    fence()
    final_size = storage_size(authority, snap, recovery_epoch=epoch)
    if authority.storage_observation(epoch, identifier('recovery_storage'), final_size, recovery=True)['halted']:
        raise ValueError('recovery_storage_limit')
    # Register only against the exact SQL manifests and caller-established task.
    return authority.register_recovery(epoch, transition_id, run['author'], proof, validated)


def run_service(trial):
    """Existing operator EOF cancellation + read-only epoch observer; no signals."""
    from services.operator_stop import OperatorStop
    from services.resumable_mailbox import serve_mailbox
    watcher = None
    result = None
    try:
        watcher = OperatorStop(trial, trial.paths['control'], trial.authority.authority_id,
                               forbidden_roots=[p for k,p in trial.paths.items() if k!='control'])
        watcher.start()
        if trial.monitor is None:
            trial.start_monitor()
        result = serve_mailbox(trial, trial.paths['mailbox'])
    finally:
        try:
            if watcher is not None:
                watcher.stop_and_join()
        finally:
            trial.close()
    if watcher is not None:
        watcher.raise_if_failed()
    receipt = dict(authority_id=trial.authority.authority_id, epoch=trial.epoch,
                   operator_stop=watcher.snapshot(), transport=result,
                   cleanup_scope='owned workers/direct-child waits only', supervisor_death_containment=False)
    if len(canonical(receipt)) > 4096:
        raise ValueError('stop_receipt_limit')
    publish(Path(trial.paths['mailbox'])/'stopped.json', receipt)
    return receipt
