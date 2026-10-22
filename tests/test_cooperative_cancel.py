"""Offline pure/mocked tests. No child, service, probe, engine or JVM is launched."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from runtime.artifacts import execute
from runtime.cancellation import Cancellation, cancelled
from services.local_trial import LocalTrial, AdapterWorker, DevelopmentGame, finish_mailbox

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('cancel_adapter', ROOT / 'seasons/2023/adapter.py')
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


class CooperativeCancelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Fail closed if any tested path accidentally tries a real process/signal.
        self.spawn = patch('runtime.artifacts.subprocess.Popen', side_effect=AssertionError('real spawn forbidden')).start()
        self.kill = patch('runtime.artifacts.os.killpg', side_effect=AssertionError('real signal forbidden')).start()
        self.addCleanup(patch.stopall)

    def fake_process(self):
        proc = Mock(pid=123, returncode=0)
        proc.wait.return_value = 0
        self.spawn.side_effect = None
        self.spawn.return_value = proc
        self.kill.side_effect = None
        return proc

    def trial(self):
        t = object.__new__(LocalTrial)
        t.lock = threading.RLock(); t.close_lock = threading.Lock()
        t.closed = t.halted = t.cleanup_complete = False
        t.cancellation = Cancellation(); self.addCleanup(t.cancellation.close)
        t.executor = Mock(); t.worker = Mock(cleanup_unverified=False)
        t.finalist = None; t.profile = 'legacy'
        return t

    def test_pipe_token_idempotent_and_writer_not_inherited(self):
        token = Cancellation()
        self.addCleanup(token.close)
        self.assertFalse(os.get_inheritable(token.write_fd))
        self.assertFalse(cancelled(token.read_fd))
        token.cancel(); token.cancel()
        self.assertTrue(cancelled(token.read_fd))
        token.close(); token.close()

    def test_cancel_before_spawn(self):
        token = Cancellation(); self.addCleanup(token.close); token.cancel()
        r = execute(['fake'], self.root / 'log', 10, cancel_fd=token.read_fd)
        self.spawn.assert_not_called()
        self.assertEqual(r['status'], 'cancelled'); self.assertTrue(r['not_started'])

    def test_cancel_between_precheck_and_spawn_waits_direct_child(self):
        token = Cancellation(); self.addCleanup(token.close)
        proc = self.fake_process()
        def spawn(*args, **kwargs):
            token.cancel()
            return proc
        self.spawn.side_effect = spawn
        r = execute(['fake'], self.root / 'log', 10, cancel_fd=token.read_fd)
        self.assertEqual(r['status'], 'cancelled')
        self.kill.assert_called_once(); proc.wait.assert_called_once_with()

    def test_cancel_during_wait(self):
        token = Cancellation(); self.addCleanup(token.close)
        proc = self.fake_process()
        def wait(timeout=None):
            if timeout is not None:
                token.cancel(); raise subprocess.TimeoutExpired('fake', timeout)
            return -9
        proc.wait.side_effect = wait
        r = execute(['fake'], self.root / 'log', 10, cancel_fd=token.read_fd, deadline_monotonic=10**12)
        self.assertEqual(r['status'], 'cancelled'); self.assertTrue(r['child_reaped'])
        self.assertEqual(proc.wait.call_count, 2)

    def test_outer_does_not_kill_cooperative_adapter_on_cancel(self):
        token = Cancellation(); self.addCleanup(token.close)
        proc = self.fake_process()
        def wait(timeout=None):
            token.cancel()
            return 2
        proc.wait.side_effect = wait
        r = execute(['fake'], self.root / 'log', 10, cancel_fd=token.read_fd, cooperative_child=True)
        self.kill.assert_not_called()
        self.assertEqual(r['exit_code'], 2)
        self.assertEqual(self.spawn.call_args.kwargs['pass_fds'], (token.read_fd,))
        self.assertNotIn(token.write_fd, self.spawn.call_args.kwargs['pass_fds'])

    def test_replay_interrupt_signals_before_waiting_not_outer_kill(self):
        token = Cancellation(); self.addCleanup(token.close)
        proc = self.fake_process(); events = []
        def wait(timeout=None):
            if not events:
                events.append('interrupted'); raise KeyboardInterrupt()
            self.assertTrue(cancelled(token.read_fd)); events.append('adapter_reaped'); return 2
        proc.wait.side_effect = wait
        def interrupt():
            events.append('fenced'); token.cancel()
        with self.assertRaises(KeyboardInterrupt):
            execute(['fake'], self.root / 'log', 10, cancel_fd=token.read_fd,
                    cooperative_child=True, on_interrupt=interrupt)
        self.assertEqual(events, ['interrupted', 'fenced', 'adapter_reaped'])
        self.kill.assert_not_called()

    def test_deadline_clips_poll_timeout(self):
        token = Cancellation(); self.addCleanup(token.close)
        proc = self.fake_process()
        with patch('runtime.artifacts.time.monotonic', return_value=10):
            execute(['fake'], self.root / 'log', 10, cancel_fd=token.read_fd, deadline_monotonic=10.05)
        self.assertAlmostEqual(proc.wait.call_args.kwargs['timeout'], .05)

    def test_failed_launch_does_not_signal_none(self):
        self.spawn.side_effect = OSError('no launch')
        r = execute(['fake'], self.root / 'log', 10)
        self.assertEqual(r['status'], 'failed'); self.kill.assert_not_called()

    def test_close_fences_before_signal_and_joins_before_return(self):
        t = self.trial(); events = []
        original = t.cancellation.cancel
        def cancel():
            self.assertTrue(t.closed and t.halted); events.append('cancel'); original()
        with patch.object(t.cancellation, 'cancel', side_effect=cancel):
            t.executor.shutdown.side_effect = lambda **kw: events.append('join')
            t.close()
        self.assertEqual(events[:2], ['cancel', 'join'])
        t.executor.shutdown.assert_called_once_with(wait=True, cancel_futures=True)
        t.close(); self.assertEqual(t.executor.shutdown.call_count, 1)
        with self.assertRaises(ValueError):
            t._dispatch({'operation': 'submit'})

    def test_token_publication_failure_still_joins_and_no_stopped(self):
        t = self.trial()
        with patch.object(t.cancellation, 'cancel', side_effect=OSError('close failed')):
            with self.assertRaises(OSError): finish_mailbox(t, self.root, 0)
        t.executor.shutdown.assert_called_once()
        self.assertFalse((self.root / 'stopped.json').exists())
        self.assertFalse(t.cleanup_complete)

    def test_stopped_write_failure_occurs_only_after_join(self):
        t = self.trial(); original_open = Path.open
        def fail(path, mode):
            t.executor.shutdown.assert_called_once(); self.assertTrue(t.cleanup_complete)
            self.assertEqual(mode, 'xb')
            with original_open(path, mode) as handle:
                handle.write(b'{')  # simulate a partial write before disk failure
            raise OSError('disk full')
        with patch.object(Path, 'open', side_effect=fail, autospec=True), patch('services.local_trial.os.replace') as replace:
            with self.assertRaises(OSError): finish_mailbox(t, self.root, 0)
            replace.assert_not_called()
        self.assertFalse((self.root / 'stopped.json').exists())
        self.assertEqual(list(self.root.glob('stopped.tmp-*')), [])

    def test_stopped_replace_observes_complete_closed_temporary_after_join(self):
        t = self.trial(); original_replace = os.replace
        def replace(source, target):
            t.executor.shutdown.assert_called_once()
            self.assertFalse(target.exists())
            self.assertLessEqual(source.stat().st_size, 4096)
            self.assertFalse(json.loads(source.read_text())['supervisor_death_containment'])
            original_replace(source, target)
        with patch('services.local_trial.os.replace', side_effect=replace) as publish:
            finish_mailbox(t, self.root, 0)
            publish.assert_called_once()
        self.assertTrue((self.root / 'stopped.json').exists())
        self.assertEqual(list(self.root.glob('stopped.tmp-*')), [])

    def test_stopped_replace_failure_does_not_publish_terminal_filename(self):
        t = self.trial()
        with patch('services.local_trial.os.replace', side_effect=OSError('replace failed')):
            with self.assertRaises(OSError): finish_mailbox(t, self.root, 0)
        t.executor.shutdown.assert_called_once()
        self.assertFalse((self.root / 'stopped.json').exists())
        self.assertEqual(list(self.root.glob('stopped.tmp-*')), [])

    def test_missing_inner_ack_prevents_stopped(self):
        t = self.trial(); t.worker.cleanup_unverified = True
        with self.assertRaisesRegex(ValueError, 'cleanup_unverified'):
            finish_mailbox(t, self.root, 0)
        t.executor.shutdown.assert_called_once()
        self.assertFalse((self.root / 'stopped.json').exists())

    def test_stopped_receipt_after_join_is_narrow(self):
        t = self.trial(); finish_mailbox(t, self.root, 0)
        receipt = json.loads((self.root / 'stopped.json').read_text())
        self.assertFalse(receipt['supervisor_death_containment'])
        self.assertIn('direct-child', receipt['cleanup_scope'])
        t.executor.shutdown.assert_called_once()

    def worker(self):
        worker = AdapterWorker(['/fake'], [DevelopmentGame('a', '/b', '/m', 'A')])
        worker.cancellation = Cancellation(); self.addCleanup(worker.cancellation.close)
        worker.cancel_trial = worker.cancellation.cancel; worker.cleanup_unverified = False
        return worker

    def test_worker_propagates_read_fd_and_ack(self):
        worker = self.worker()
        def invoke(command, output, timeout, **kwargs):
            kwargs['ownership']['direct_child_wait_completed'] = True
            self.assertEqual(kwargs['cancel_fd'], worker.cancellation.read_fd)
            self.assertTrue(kwargs['cooperative_child'])
            self.assertEqual(command[command.index('--cancel-fd') + 1], str(worker.cancellation.read_fd))
            ack = Path(command[command.index('--cleanup-ack') + 1])
            ack.write_text(json.dumps({'schema': 'bcenv.cooperative-cleanup.v1',
                'direct_child_waits_completed': True,
                'scope': 'normal adapter return only; not descendant containment'}))
            return {'exit_code': 0}
        with patch('services.local_trial.execute', side_effect=invoke):
            self.assertTrue(worker.invoke(self.root, 'build', ['build']))
        self.assertFalse(worker.cleanup_unverified)

    def test_worker_cancelled_refuses_new_invoke(self):
        worker = self.worker(); worker.cancellation.cancel()
        with patch('services.local_trial.execute') as fn:
            with self.assertRaisesRegex(ValueError, 'run_halted'):
                worker.invoke(self.root, 'build', ['build'])
            fn.assert_not_called()

    def test_worker_ack_write_failure_does_not_claim_cleanup(self):
        worker = self.worker()
        with patch('services.local_trial.execute', return_value={'exit_code': 2}):
            self.assertFalse(worker.invoke(self.root, 'build', ['build']))
        self.assertTrue(worker.cleanup_unverified)

    def test_all_adapter_executes_forward_shared_options(self):
        tree = ast.parse((ROOT / 'seasons/2023/adapter.py').read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'execute']
        self.assertEqual(len(calls), 5)
        for call in calls:
            self.assertTrue(any(k.arg is None and isinstance(k.value, ast.Call) and k.value.func.id == 'deadline_options' for k in call.keywords))
        args = type('Args', (), {'deadline_monotonic': 42., 'cancel_fd': 7})()
        self.assertEqual(adapter.deadline_options(args), {'deadline_monotonic': 42., 'cancel_fd': 7})

    def test_queued_training_job_does_not_call_worker_after_cancel(self):
        t = self.trial(); t.root = self.root
        t.storage_checkpoint = Mock(); t.audit = Mock(); t.records = {'s_test': {}}
        accepted = Mock(content_sha256='a' * 64)
        t.cancel(); t._run('s_test', accepted)
        t.worker.run.assert_not_called()
        self.assertEqual(t.records['s_test']['status'], 'infrastructure_failure')

    def test_signal_handlers_fence_without_raising_and_restore(self):
        from services.local_trial import cooperative_signals
        t = self.trial()
        with patch('services.local_trial.signal.signal', return_value='old') as install:
            with cooperative_signals(t):
                handler = install.call_args_list[0].args[1]
                handler(2, None)
                self.assertTrue(t.closed and cancelled(t.cancellation.read_fd))
            self.assertEqual(install.call_count, 4)
            self.assertEqual(install.call_args_list[-1].args[1], 'old')

    def test_adapter_writes_ack_only_after_normal_return(self):
        import sys
        ack = self.root / 'ack'
        argv = ['adapter', 'build', '--source', 's', '--package', 'bot', '--output', 'o', '--cleanup-ack', str(ack)]
        with patch.object(adapter, 'build', return_value=1), patch.object(sys, 'argv', argv):
            self.assertEqual(adapter.main(), 1)
        self.assertTrue(json.loads(ack.read_text())['direct_child_waits_completed'])
        ack.unlink()
        with patch.object(adapter, 'build', side_effect=OSError('uncertain')), patch.object(sys, 'argv', argv):
            self.assertEqual(adapter.main(), 2)
        self.assertFalse(ack.exists())

    def test_interrupt_during_poll_publishes_before_outer_wait(self):
        token = Cancellation(); self.addCleanup(token.close)
        proc = self.fake_process(); ownership = {}
        with patch('runtime.artifacts.cancelled', side_effect=[False, KeyboardInterrupt()]):
            with self.assertRaises(KeyboardInterrupt):
                execute(['fake'], self.root / 'log', 10, cancel_fd=token.read_fd,
                        cooperative_child=True, on_interrupt=token.cancel, ownership=ownership)
        self.assertTrue(cancelled(token.read_fd)); self.kill.assert_not_called()
        self.assertTrue(ownership['direct_child_wait_completed'])
        proc.wait.assert_called_once_with(timeout=5.)

    def test_valid_inner_ack_without_outer_wait_is_unverified(self):
        worker = self.worker()
        def failed_wait(command, *args, **kwargs):
            Path(command[command.index('--cleanup-ack') + 1]).write_text(json.dumps({
                'schema': 'bcenv.cooperative-cleanup.v1', 'direct_child_waits_completed': True,
                'scope': 'normal adapter return only; not descendant containment'}))
            raise OSError('outer wait failed')
        with patch('services.local_trial.execute', side_effect=failed_wait):
            with self.assertRaises(OSError): worker.invoke(self.root, 'build', ['build'])
        self.assertTrue(worker.cleanup_unverified)

    def test_no_spawn_after_precheck_needs_no_inner_ack(self):
        worker = self.worker()
        def no_spawn(command, *args, **kwargs):
            kwargs['ownership']['not_started'] = True
            return {'exit_code': None, 'status': 'cancelled', 'not_started': True}
        with patch('services.local_trial.execute', side_effect=no_spawn):
            self.assertFalse(worker.invoke(self.root, 'build', ['build']))
        self.assertFalse(worker.cleanup_unverified)

    def test_reentrant_token_cancel_closes_writer_once(self):
        token = Cancellation(); self.addCleanup(token.close)
        close = os.close; writer = token.write_fd; observed = []
        def nested(fd):
            if fd == writer:
                observed.append(fd); token.cancel()
            close(fd)
        with patch('runtime.cancellation.os.close', side_effect=nested):
            token.cancel()
        self.assertEqual(observed, [writer]); self.assertTrue(cancelled(token.read_fd))

    def test_signal_handlers_restore_on_exception(self):
        from services.local_trial import cooperative_signals
        t = self.trial()
        with patch('services.local_trial.signal.signal', return_value='old') as install:
            with self.assertRaises(ValueError):
                with cooperative_signals(t): raise ValueError('failure')
        self.assertEqual(install.call_count, 4)

    def test_outer_timeout_cannot_supply_missing_inner_ack(self):
        worker = self.worker()
        def expired(*args, **kwargs):
            kwargs['ownership']['direct_child_wait_completed'] = True
            return {'exit_code': -9, 'status': 'timeout'}
        with patch('services.local_trial.execute', side_effect=expired):
            self.assertFalse(worker.invoke(self.root, 'build', ['build']))
        self.assertTrue(worker.cleanup_unverified)

    def test_short_stopped_write_never_replaces(self):
        t = self.trial()
        with patch.object(Path, 'open') as opened, patch('services.local_trial.os.replace') as replace:
            opened.return_value.__enter__.return_value.write.return_value = 1
            with self.assertRaisesRegex(OSError, 'short_stop_receipt_write'):
                finish_mailbox(t, self.root, 0)
            replace.assert_not_called()
        self.assertFalse((self.root / 'stopped.json').exists())

    def test_stop_receipt_size_is_bounded_before_temporary_creation(self):
        t = self.trial(); t.profile = 'continuity-v1'; t.requests = 0
        t.usage_snapshot = Mock(return_value={'oversize': 'x' * 5000})
        with patch.object(Path, 'open') as opened:
            with self.assertRaisesRegex(ValueError, 'stop_receipt_limit'):
                finish_mailbox(t, self.root, 0)
            opened.assert_not_called()
        t.executor.shutdown.assert_called_once()
