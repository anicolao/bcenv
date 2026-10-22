"""Pure/mocked wiring proofs; not installed runtime, PTY or process qualification."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from services import local_trial as service
from services import launch_local_trial as launch
from runtime.cancellation import Cancellation, cancelled


class ForbiddenLock:
    def __enter__(self): raise AssertionError('blocking trial lock acquired')
    def __exit__(self, *args): pass


class OperatorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for target in ('subprocess.Popen', 'os.system', 'os.fork', 'os.kill', 'os.killpg', 'signal.signal'):
            guard = patch(target, side_effect=AssertionError('real process/signal forbidden'))
            guard.start(); self.addCleanup(guard.stop)
    def trial(self):
        t = object.__new__(service.LocalTrial)
        t.lock = threading.RLock(); t.close_lock = threading.Lock()
        t.closed = t.halted = t.cleanup_complete = False
        t.stop_requested = threading.Event(); t.cancellation = Cancellation()
        self.addCleanup(t.cancellation.close)
        t.root = self.root / 'service'; t.profile = 'legacy'; t.finalist = None
        t.worker = Mock(cleanup_unverified=False); t.executor = Mock()
        return t
    def worker(self):
        w = service.AdapterWorker(['/fake'], [service.DevelopmentGame('generic', '/build', '/map', 'A')])
        w.stop_requested = threading.Event(); w.cancellation = Cancellation()
        self.addCleanup(w.cancellation.close); w.cancel_trial = Mock(); w.cleanup_unverified = False
        return w
    def test_fence_precedes_writer_close_without_trial_lock(self):
        t = self.trial(); t.lock = ForbiddenLock(); original = t.cancellation.cancel
        def close_writer():
            self.assertTrue(t.stop_requested.is_set()); original()
        with patch.object(t.cancellation, 'cancel', side_effect=close_writer):
            t.request_cancel(); t.request_cancel()
        self.assertTrue(cancelled(t.cancellation.read_fd))
        self.assertFalse(t.closed)  # watcher deliberately does not mutate locked state
    def test_writer_failure_retains_admission_fence(self):
        t = self.trial(); t.lock = ForbiddenLock()
        with patch.object(t.cancellation, 'cancel', side_effect=OSError('mock close failure')):
            with self.assertRaises(OSError): t.request_cancel()
        self.assertTrue(t.stop_requested.is_set())
        for dispatch in (t.dispatch, t._dispatch):
            with self.assertRaisesRegex(ValueError, 'invalid_request'): dispatch({'operation': 'validation_status'})
    def test_dispatch_rechecks_after_lock_acquisition(self):
        t = self.trial()
        class CancelOnEnter:
            def __enter__(self): t.stop_requested.set()
            def __exit__(self, *args): pass
        t.lock = CancelOnEnter()
        with self.assertRaisesRegex(ValueError, 'invalid_request'): t.dispatch({})
    def test_worker_fence_before_storage_and_execution(self):
        w = self.worker(); w.stop_requested.set(); w.storage_checkpoint = Mock()
        with patch.object(service, 'execute', side_effect=AssertionError('must not execute')):
            with self.assertRaisesRegex(ValueError, 'run_halted'): w.invoke(self.root, 'build', ['build'])
        w.storage_checkpoint.assert_not_called(); self.assertFalse(w.cleanup_unverified)
    def test_worker_rechecks_after_preparation_before_execution(self):
        w = self.worker(); w.storage_checkpoint = lambda stage: w.stop_requested.set()
        with patch.object(service, 'execute', side_effect=AssertionError('must not execute')):
            with self.assertRaisesRegex(ValueError, 'run_halted'): w.invoke(self.root, 'build', ['build'])
        self.assertFalse(w.cleanup_unverified); self.assertEqual(list(self.root.iterdir()), [])
    def test_join_then_cleanup_then_receipt(self):
        t = self.trial(); events = []; watcher = Mock()
        watcher.stop_and_join.side_effect = lambda: events.append('watcher_join')
        t.executor.shutdown.side_effect = lambda **kwargs: events.append('workers_join')
        watcher.raise_if_failed.side_effect = lambda: events.append('failure_check')
        watcher.snapshot.return_value = {'schema': 'synthetic', 'watcher_joined': True}
        service.finish_mailbox(t, self.root, 0, operator_stop=watcher)
        self.assertEqual(events, ['watcher_join', 'workers_join', 'failure_check'])
        self.assertTrue(json.loads((self.root / 'stopped.json').read_text())['operator_stop']['watcher_joined'])
    def test_join_failure_still_cleans_without_marker(self):
        t = self.trial(); watcher = Mock()
        watcher.stop_and_join.side_effect = ValueError('operator_watcher_join_timeout')
        with self.assertRaisesRegex(ValueError, 'operator_watcher_join_timeout'):
            service.finish_mailbox(t, self.root, 0, operator_stop=watcher)
        t.executor.shutdown.assert_called_once(); self.assertFalse((self.root / 'stopped.json').exists())
    def test_latched_control_error_withholds_marker_after_cleanup(self):
        t = self.trial(); watcher = Mock(); watcher.raise_if_failed.side_effect = ValueError('operator_control_invalid')
        with self.assertRaisesRegex(ValueError, 'operator_control_invalid'):
            service.finish_mailbox(t, self.root, 0, operator_stop=watcher)
        t.executor.shutdown.assert_called_once(); self.assertFalse((self.root / 'stopped.json').exists())
    def test_stop_receipt_write_failure_does_not_bypass_join(self):
        t = self.trial(); watcher = Mock(); watcher.snapshot.return_value = {}
        with patch.object(Path, 'open', side_effect=OSError('mock persistence failure')):
            with self.assertRaises(OSError): service.finish_mailbox(t, self.root, 0, operator_stop=watcher)
        watcher.stop_and_join.assert_called_once(); t.executor.shutdown.assert_called_once()
        self.assertFalse((self.root / 'stopped.json').exists())
    def test_setup_failure_always_closes_trial(self):
        t = self.trial()
        with self.assertRaisesRegex(ValueError, 'invalid_operator_control'):
            service.run_mailbox(t, self.root / 'mailbox', operator_control=self.root / 'control')
        t.executor.shutdown.assert_called_once()
    def test_legacy_no_watcher(self):
        t = self.trial()
        with patch.object(service, 'OperatorStop', side_effect=AssertionError('not opt-in')), patch.object(service, 'serve_mailbox') as serve:
            service.run_mailbox(t, self.root / 'mailbox')
        serve.assert_called_once_with(t, self.root / 'mailbox')
    def test_readiness_failure_joins_watcher_and_trial(self):
        t = self.trial(); watcher = Mock()
        with patch.object(service, 'OperatorStop', return_value=watcher), patch.object(service, 'serve_mailbox', side_effect=OSError('ready failed')):
            with self.assertRaises(OSError): service.run_mailbox(t, self.root / 'mailbox', operator_control=self.root / 'control', run_id='a'*32)
        watcher.start.assert_called_once(); watcher.stop_and_join.assert_called_once(); t.executor.shutdown.assert_called_once()
    def test_other_thread_cancels_while_synchronous_owner_holds_rlock(self):
        t = self.trial(); done = threading.Event(); errors = []
        def request():
            try: t.request_cancel()
            except BaseException as error: errors.append(error)
            finally: done.set()
        thread = threading.Thread(target=request)
        with t.lock:
            thread.start()
            completed_without_release = done.wait(1)
        thread.join(1)
        self.assertFalse(thread.is_alive()); self.assertFalse(errors)
        self.assertTrue(completed_without_release); self.assertTrue(cancelled(t.cancellation.read_fd))
    def test_pending_mailbox_fence_prevents_debit_or_dispatch(self):
        t = self.trial(); t.max_mailbox_requests = 10; t.storage_guard = None
        t.storage_checkpoint = Mock(); t.mailbox_debit = Mock()
        mailbox = self.root / 'mailbox'; original = Path.iterdir
        def pending(path):
            if path == mailbox / 'requests':
                (path / ('a'*32 + '.json')).write_text('{}')
                t.stop_requested.set()
            return original(path)
        with patch.object(Path, 'iterdir', pending):
            service.serve_mailbox(t, mailbox)
        t.mailbox_debit.assert_not_called()
        self.assertTrue((mailbox/'requests'/('a'*32+'.json')).exists())
        self.assertEqual(json.loads((mailbox/'stopped.json').read_text())['processed'], 0)
    def test_same_event_reaches_both_phase_workers(self):
        w = self.worker(); finalist = Mock(); finalist.worker.cleanup_unverified = False
        with patch.object(service, 'FinalistValidation', return_value=finalist), patch.object(service, 'ThreadPoolExecutor'):
            t = service.LocalTrial(self.root/'real-root', w, validation_games=[])
            try:
                self.assertIs(w.stop_requested, t.stop_requested)
                self.assertIs(finalist.worker.stop_requested, t.stop_requested)
            finally: t.close()
    def config(self):
        return {'command': [sys.executable], 'games': [{'label': 'generic', 'opponent_build': '/public/build', 'map_path': '/public/map', 'own_slot': 'A'}]}
    def args(self, name):
        p = self.root / (name + '.json'); p.write_text(json.dumps(self.config()))
        return ['--config', str(p), '--root', str(self.root / (name+'-service')), '--mailbox', str(self.root / (name+'-mailbox')), '--evidence', str(self.root / name), '--check-only']
    def test_mode_bound_but_run_identity_is_private(self):
        with patch.object(launch, 'probe', return_value={'status': 'succeeded'}):
            self.assertEqual(launch.main(self.args('one') + ['--operator-control', str(self.root/'ctrl1'), '--run-id', 'a'*32]), 0)
            self.assertEqual(launch.main(self.args('two') + ['--operator-control', str(self.root/'ctrl2'), '--run-id', 'b'*32, '--expect-parity', str(self.root/'one/launch.json')]), 0)
        one = json.loads((self.root/'one/launch.json').read_text()); two = json.loads((self.root/'two/launch.json').read_text())
        self.assertEqual(one['identity'], two['identity']); self.assertNotEqual(one['operator_control'], two['operator_control'])
        self.assertEqual(one['identity']['operator_stop']['max_bytes'], 256)
        self.assertFalse((self.root/'ctrl1').exists())
        with patch.object(launch, 'probe', side_effect=AssertionError('parity must reject first')):
            self.assertEqual(launch.main(self.args('three') + ['--expect-parity', str(self.root/'one/launch.json')]), 1)
    def test_malformed_flags_reject_before_probe(self):
        with patch.object(launch, 'probe', side_effect=AssertionError('invalid args must reject first')):
            with self.assertRaisesRegex(ValueError, 'invalid_operator_control'):
                launch.main(self.args('bad') + ['--run-id', 'a'*32])


if __name__ == '__main__': unittest.main()
