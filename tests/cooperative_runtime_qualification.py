"""Five ordered REMOTE-ONLY inert cases; never a local trial/PTY qualification.

Imported by the existing packaged replay-query check, not unittest discovery.
No games, candidate data, supervisor-death test or production adapter test hook.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from runtime.artifacts import execute, write
from runtime.cancellation import Cancellation
import services.local_trial as trial_module
from services.local_trial import AdapterWorker, DevelopmentGame, LocalTrial, cooperative_signals, finish_mailbox

CASE_NAMES = ('packaged_fd_controls', 'source_nested_sigint', 'source_sync_sigint',
              'source_nested_sigterm', 'source_receipt_failure')
MAX_WRAPPER_CALLS = 3
MAX_INNER_LIFETIME = 8
CASE_SECONDS = 40
HARNESS_SECONDS = 600
ACK = {'schema': 'bcenv.cooperative-cleanup.v1', 'direct_child_waits_completed': True,
       'scope': 'normal adapter return only; not descendant containment'}


def require_remote():
    if os.environ.get('BCENV_REMOTE_COOPERATIVE_QUALIFICATION') != '1':
        raise RuntimeError('remote-only qualification is not enabled; no process launched')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fixture_adapter(root, fd, ack, deadline):
    """Inert source fixture models ack contract; not the packaged adapter main."""
    require_remote()
    root = Path(root)
    ownership = {}
    inner = ('import sys,time; from pathlib import Path; '
             'Path(sys.argv[1]).write_text("ready"); time.sleep(8)')
    result = execute([sys.executable, '-c', inner, str(root / 'inner-ready')],
                     root / 'inner.log', 10, cancel_fd=fd,
                     deadline_monotonic=deadline, ownership=ownership)
    write(root / 'inner-result.json', {'receipt': result, 'ownership': ownership})
    if not ownership.get('direct_child_wait_completed'):
        raise RuntimeError('inner wait unverified')
    write(ack, ACK)
    write(root / 'fixture-ack-observation.json', {'ack_written_monotonic': time.monotonic()})
    return 0


def packaged_case(root, replay, events):
    command = Path(shutil.which('bcenv') or '')
    if not command.is_file():
        raise RuntimeError('packaged CLI unavailable; unqualified')
    installed = command.resolve().parent.parent / 'lib/bcenv'
    paths = ('runtime/artifacts.py', 'runtime/cancellation.py', 'seasons/2023/adapter.py')
    identity = {'wrapper': str(command.resolve()), 'wrapper_sha256': sha(command),
                'installed': {p: sha(installed / p) for p in paths},
                'source': {p: sha(ROOT / p) for p in paths}}
    assert identity['installed'] == identity['source'], identity
    events['identity'] = identity
    results = []
    for label in ('writer_open', 'writer_eof', 'invalid_fd'):
        token = Cancellation()
        proc = None
        observation = {'control': label, 'outer_wait_completed': False}
        results.append(observation)
        ack, output = root / (label + '-ack.json'), root / label
        fd = -1 if label == 'invalid_fd' else token.read_fd
        if label == 'writer_eof':
            token.cancel()
        deadline = time.monotonic() + 10
        args = [str(command), 'replay', 'query', '--replay', str(replay),
                '--round-start', '35', '--round-end', '40', '--robot-id', '43',
                '--limit', '3', '--output', str(output), '--timeout', '10',
                '--deadline-monotonic', str(deadline), '--cancel-fd', str(fd),
                '--cleanup-ack', str(ack)]
        try:
            with (root / (label + '.log')).open('wb') as log:
                proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT,
                                        start_new_session=True,
                                        pass_fds=(() if fd < 0 else (fd,)))
                try:
                    observation['exit_code'] = proc.wait(timeout=15)
                    observation['outer_wait_completed'] = True
                    observation['outer_wait_monotonic'] = time.monotonic()
                except subprocess.TimeoutExpired:
                    observation['uncertain_timeout'] = True
                    raise RuntimeError('packaged owner timeout; no later case authorized')
        finally:
            token.cancel()
            if proc is not None and not observation['outer_wait_completed']:
                # Cleanup only this original handle; never infer nested reaping.
                try:
                    proc.wait(timeout=5)
                    observation['cleanup_outer_wait_completed'] = True
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                    observation['cleanup_outer_killed'] = True
            token.close()
        observation['ack'] = json.loads(ack.read_text()) if ack.is_file() else None
        if label == 'invalid_fd':
            assert observation['exit_code'] == 2 and observation['ack'] is None
            assert not (output / 'query-result.json').exists()
        else:
            result = json.loads((output / 'query-result.json').read_text())
            observation['inner_receipt'] = result['process']
            assert observation['ack'] == ACK
            if label == 'writer_open':
                assert observation['exit_code'] == 0 and result['status'] == 'completed'
                assert result['process']['child_reaped'] is True
            else:
                assert observation['exit_code'] == 1
                assert result['process']['status'] == 'cancelled'
                assert result['process']['not_started'] is True
                assert result['process']['child_reaped'] is False
        assert (root / (label + '.log')).stat().st_size <= 1048576
    events.update(probes=results, wrapper_calls=len(results), new_query_jvms=1,
                  qualified='packaged baseline/EOF/invalid FD functional controls only',
                  unqualified=['installed in-flight cancellation', 'sole writer/inode proof', 'VM wrapper and tool PTY'])


class ObservedTrial(LocalTrial):
    def cancel(self):
        self.observations.append({'event': 'cancel_entered', 'monotonic': time.monotonic()})
        super().cancel()
        self.observations.append({'event': 'cancel_returned', 'monotonic': time.monotonic(), 'closed': self.closed})


class InertReplayWorker(AdapterWorker):
    def replay(self, root, *args, **kwargs):
        self.invoke(self.fixture_operation_root, 'active', ['replay', 'query'])
        raise ValueError('inert_cancelled_replay')


def source_case(root, kind, events):
    started = time.monotonic()
    worker = InertReplayWorker([sys.executable, str(Path(__file__).resolve()),
                               '--fixture-adapter', str(root)],
                              [DevelopmentGame('inert', 'unused', 'unused', 'A')], timeout=10)
    worker.fixture_operation_root = root
    trial = ObservedTrial(root / 'trial', worker)
    trial.observations = []
    worker.deadline_monotonic = started + 20
    worker.remaining_seconds = lambda: worker.deadline_monotonic - time.monotonic()
    mailbox = root / 'mailbox'
    mailbox.mkdir()
    if kind == 'source_receipt_failure':
        # Actual rename failure, not injected production behavior or full disk.
        (mailbox / 'stopped.json').mkdir()
        (mailbox / 'stopped.json' / 'keep').write_text('nonempty directory')
    previous = {n: signal.getsignal(n) for n in (signal.SIGINT, signal.SIGTERM)}
    stop_sender, sender_lock = threading.Event(), threading.Lock()
    sender_errors, sent = [], []
    signal_number = signal.SIGTERM if kind == 'source_nested_sigterm' else signal.SIGINT

    def send_when_ready():
        try:
            limit = time.monotonic() + 10
            while not stop_sender.wait(.01):
                if (root / 'inner-ready').is_file():
                    with sender_lock:
                        if stop_sender.is_set():
                            return
                        sent.append({'signal': signal_number, 'monotonic': time.monotonic(),
                                     'scope': 'known current CI harness process, not PTY'})
                        os.kill(os.getpid(), signal_number)
                    return
                if time.monotonic() >= limit:
                    sender_errors.append('inner readiness missing')
                    return
        except BaseException as error:
            sender_errors.append(type(error).__name__ + ': ' + str(error))

    sender = threading.Thread(target=send_when_ready, name='owned-signal-sender')
    queued = future = None
    original_execute = trial_module.execute
    outer_receipts = []

    def observed_execute(*args, **kwargs):
        # Observe the real source execute call; never replace a process or wait.
        if outer_receipts:
            raise AssertionError('source case adapter-invocation ceiling exceeded; no extra spawn')
        observation = {'entered_monotonic': time.monotonic()}
        outer_receipts.append(observation)
        try:
            result = original_execute(*args, **kwargs)
            observation['result'] = result
            return result
        finally:
            observation['ownership'] = dict(kwargs.get('ownership', {}))
            observation['returned_or_raised_monotonic'] = time.monotonic()
            write(root / 'outer-receipts.json', outer_receipts)

    trial_module.execute = observed_execute
    try:
        with cooperative_signals(trial):
            try:
                sender.start()
                if kind == 'source_sync_sigint':
                    sid = 's_' + 'a' * 32
                    trial.records[sid] = {'status': 'complete', 'source_sha256': '0' * 64,
                                         'games': [{'status': 'completed', 'replay_sha256': '0' * 64}]}
                    try:
                        trial.dispatch({'operation': 'replay', 'submission_id': sid, 'game_index': 0,
                                        'query': {'round_start': 1, 'round_end': 1, 'robot_id': None,
                                                  'record_offset': 0, 'record_limit': 1}})
                    except ValueError as error:
                        assert str(error) == 'replay_unavailable'
                    else:
                        raise AssertionError('cancelled inert replay unexpectedly succeeded')
                else:
                    future = trial.executor.submit(worker.invoke, root, 'active', ['build'])
                    if kind == 'source_nested_sigint':
                        queued = trial.executor.submit(worker.invoke, root, 'queued', ['build'])
                    assert future.result(timeout=30) is True
                assert sent and not sender_errors, (sent, sender_errors)
                assert trial.closed and trial.halted
                try:
                    trial.dispatch({'operation': 'submit'})
                except ValueError as error:
                    assert str(error) == 'invalid_request'
                else:
                    raise AssertionError('post-fence admission accepted')
                before_calls = len(outer_receipts)
                try:
                    worker.invoke(root, 'after-fence', ['build'])
                except ValueError as error:
                    assert str(error) == 'run_halted'
                else:
                    raise AssertionError('valid worker operation admitted after fence')
                assert len(outer_receipts) == before_calls and not list(root.glob('after-fence*'))
                events['valid_post_fence_worker_rejected_without_execute'] = True
                before_finish = time.monotonic()
                try:
                    finish_mailbox(trial, mailbox, 0)
                except OSError:
                    assert kind == 'source_receipt_failure'
                    events['expected_publication_failure'] = True
                else:
                    assert kind != 'source_receipt_failure'
                events['finish_returned_monotonic'] = time.monotonic()
                events['finish_entered_monotonic'] = before_finish
                assert trial.cleanup_complete and not worker.cleanup_unverified
            finally:
                # The sender can never issue a delayed signal after restoration.
                with sender_lock:
                    stop_sender.set()
                sender.join(timeout=2)
                assert not sender.is_alive(), 'signal sender join unverified'
                trial.close()
    finally:
        # Idempotent cancellation/join in the original owning harness only.
        with sender_lock:
            stop_sender.set()
        if sender.ident is not None:
            sender.join(timeout=2)
        try:
            trial.close()
        finally:
            trial_module.execute = original_execute
    assert all(signal.getsignal(n) == previous[n] for n in previous)
    if queued is not None:
        if not queued.cancelled():
            try:
                queued.result(timeout=1)
            except ValueError as error:
                assert str(error) == 'run_halted'
            else:
                raise AssertionError('queued operation was not rejected')
        assert not list(root.glob('queued*')), 'queued work launched after fence'
    inner = json.loads((root / 'inner-result.json').read_text())
    ack = json.loads((root / 'fixture-ack-observation.json').read_text())
    assert inner['ownership']['direct_child_wait_completed'] is True
    assert inner['receipt']['child_reaped'] is True and inner['receipt']['status'] == 'cancelled'
    assert len(outer_receipts) == 1 and outer_receipts[0]['ownership'].get('direct_child_wait_completed') is True
    assert inner['receipt']['reaped_monotonic'] <= ack['ack_written_monotonic'] <= outer_receipts[0]['returned_or_raised_monotonic'] <= events['finish_entered_monotonic']
    assert len(list(root.glob('active.cleanup-*.json'))) == 1
    assert not list(mailbox.glob('stopped.tmp-*'))
    if kind == 'source_receipt_failure':
        assert (mailbox / 'stopped.json').is_dir()
    else:
        marker = mailbox / 'stopped.json'
        assert marker.stat().st_size <= 4096
        assert json.loads(marker.read_text())['supervisor_death_containment'] is False
    assert time.monotonic() - started <= CASE_SECONDS
    events.update(signals=sent, cancellation_observations=trial.observations,
                  inner=inner, fixture_ack=ack, outer_receipts=outer_receipts, cleanup_complete=trial.cleanup_complete,
                  outer_wait_and_ack_verified=not worker.cleanup_unverified,
                  signal_handlers_restored=True, source_adapter_invocations=1, inner_helpers=1,
                  qualified='source inert direct-wait/ack/handler/receipt path only',
                  unqualified=['packaged in-flight cleanup', 'VM wrapper', 'any PTY/tool signal route'])


def run_remote(root, replay):
    require_remote()
    root, replay = Path(root), Path(replay).resolve()
    root.mkdir(exist_ok=False)
    started = time.monotonic()
    evidence = {'schema': 'bcenv.remote-cooperative-five.v1', 'status': 'running',
                'source_root': str(ROOT), 'fixture_sha256': sha(__file__),
                'source_files': {p: sha(ROOT / p) for p in ('runtime/artifacts.py', 'runtime/cancellation.py',
                    'services/local_trial.py', 'seasons/2023/adapter.py')}, 'cases': [],
                'limits': {'whole_seconds': HARNESS_SECONDS, 'case_seconds': CASE_SECONDS,
                           'max_wrappers': 3, 'max_source_adapters': 4, 'max_inner_helpers': 4},
                'limitations': 'Remote check topology only; no012 helper-quiescence, supervisor-death, installed in-flight, VM wrapper or tool-PTY proof.'}
    try:
        for name in CASE_NAMES:
            assert time.monotonic() - started < HARNESS_SECONDS
            case = {'name': name, 'status': 'running', 'started_monotonic': time.monotonic()}
            evidence['cases'].append(case)
            where = root / name
            where.mkdir()
            try:
                if name == CASE_NAMES[0]:
                    packaged_case(where, replay, case)
                else:
                    source_case(where, name, case)
                assert time.monotonic() - case['started_monotonic'] <= CASE_SECONDS
                case['status'] = 'passed_with_explicit_scope_limits'
            except BaseException as error:
                case.update(status='failed_or_unqualified', error=type(error).__name__ + ': ' + str(error))
                raise
            finally:
                case['finished_monotonic'] = time.monotonic()
                write(root / 'evidence.json', evidence)
        evidence['status'] = 'passed_five_narrow_cases'
    except BaseException:
        evidence['status'] = 'failed_stop_no_later_cases'
        raise
    finally:
        evidence['finished_monotonic'] = time.monotonic()
        write(root / 'evidence.json', evidence)
        print('COOPERATIVE_QUALIFICATION_EVIDENCE ' + json.dumps(evidence, sort_keys=True), flush=True)
    return evidence


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--fixture-adapter', required=True)
    p.add_argument('--cancel-fd', required=True, type=int)
    p.add_argument('--cleanup-ack', required=True)
    p.add_argument('--deadline-monotonic', required=True, type=float)
    args, _ = p.parse_known_args()
    raise SystemExit(fixture_adapter(args.fixture_adapter, args.cancel_fd, args.cleanup_ack, args.deadline_monotonic))
