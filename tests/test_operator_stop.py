"""Pure/in-process operator control tests: no child processes or signals."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from services.operator_stop import OperatorStop, validate_control_args, SCHEMA, MAX_BYTES

RUN_ID = 'a' * 32


class Trial:
    def __init__(self):
        self.calls = 0
        self.observed = threading.Event()
    @property
    def lock(self): raise AssertionError('watcher accessed trial.lock')
    def cancel(self): raise AssertionError('watcher called trial.cancel')
    def close(self): raise AssertionError('watcher called trial.close')
    def request_cancel(self):
        self.calls += 1
        self.observed.set()


class OperatorStopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name); self.trial = Trial(); self.controls = []
        self.addCleanup(self.close_controls)
    def close_controls(self):
        for control in self.controls:
            try: control.stop_and_join()
            except ValueError: pass  # Failures are explicitly asserted by each negative test.
    def control(self, name='operator', **kwargs):
        control = OperatorStop(self.trial, self.base / name, RUN_ID, **kwargs)
        self.controls.append(control)
        return control
    def publish(self, control, **changes):
        data = {'schema': SCHEMA, 'run_id': RUN_ID, 'operation': 'cancel'}
        data.update(changes)
        staging = control.directory / 'cancel.tmp'
        staging.write_text(json.dumps(data))
        os.replace(staging, control.directory / 'cancel.json')
    def test_optional_paired_validation_before_creation(self):
        self.assertIsNone(validate_control_args(None, None))
        path = self.base / 'fresh'
        self.assertEqual(validate_control_args(path, RUN_ID), (path, RUN_ID))
        self.assertFalse(path.exists())
        for directory, run_id in ((None, RUN_ID), (path, None), (path, 'A'*32),
                                  (path, 'a'*31), (path, '../bad'), (path, 1)):
            with self.assertRaisesRegex(ValueError, 'invalid_operator_control'):
                validate_control_args(directory, run_id)
    def test_constructor_fstat_failure_closes_acquired_directory_fd(self):
        acquired = []
        def failed_fstat(fd):
            acquired.append(fd)
            raise OSError('synthetic fstat failure')
        with patch('services.operator_stop.os.fstat', side_effect=failed_fstat):
            with self.assertRaisesRegex(ValueError, '^invalid_operator_control$'):
                self.control()
        self.assertEqual(len(acquired), 1)
        with self.assertRaises(OSError):
            os.fstat(acquired[0])  # Actual in-process descriptor is now closed.
    def test_fresh_directory_required(self):
        self.control()
        with self.assertRaisesRegex(ValueError, 'operator_control_not_fresh'): self.control()
    def test_symlink_ancestors_and_final_component_rejected(self):
        real = self.base / 'real'; real.mkdir()
        link = self.base / 'link'; link.symlink_to(real)
        with self.assertRaises(ValueError): validate_control_args(link / 'child', RUN_ID)
        with self.assertRaises(ValueError): validate_control_args(link, RUN_ID)
    def test_forbidden_scope_overlaps_in_both_directions(self):
        forbidden = self.base / 'candidate'; forbidden.mkdir()
        for directory, roots in ((forbidden / 'control', [forbidden]),
                                  (self.base / 'ancestor', [self.base / 'ancestor' / 'mailbox']),
                                  (self.base / 'same', [self.base / 'same'])):
            with self.assertRaisesRegex(ValueError, 'operator_control_scope_overlap'):
                OperatorStop(self.trial, directory, RUN_ID, roots)
        self.assertFalse((forbidden / 'control').exists())
    def test_missing_file_normal_no_cancellation_or_writes(self):
        control = self.control()
        self.assertFalse(control._observe()); self.assertFalse(control._observe())
        self.assertEqual(self.trial.calls, 0); self.assertEqual(list(control.directory.iterdir()), [])
        control.stop_and_join(); control.raise_if_failed()
        self.assertTrue(control.snapshot()['watcher_joined'])
    def test_valid_request_observed_once_without_trial_lock(self):
        control = self.control(); self.publish(control)
        self.assertTrue(control._observe()); self.assertTrue(control._observe())
        self.assertEqual(self.trial.calls, 1)
        snapshot = control.snapshot()
        self.assertTrue(snapshot['valid_request']); self.assertIsNone(snapshot['error'])
        self.assertLessEqual(snapshot['observed_monotonic_ns'], snapshot['cancel_requested_monotonic_ns'])
        self.assertLessEqual(snapshot['cancel_requested_monotonic_ns'], snapshot['cancel_returned_monotonic_ns'])
        snapshot['error'] = 'forged'; self.assertIsNone(control.snapshot()['error'])
        self.assertLess(len(json.dumps(control.snapshot())), 1024)
        self.assertNotIn(str(self.base), json.dumps(control.snapshot()))
        self.assertNotIn(RUN_ID, json.dumps(control.snapshot()))
    def test_stale_uuid_and_extra_fields_fail_closed_once(self):
        for i, changes in enumerate(({'run_id': 'b'*32}, {'operation': 'stop'},
                                    {'schema': 'wrong'}, {'pid': 123}, {'command': 'anything'})):
            control = self.control(str(i)); self.publish(control, **changes)
            before = self.trial.calls; control._observe(); control._observe()
            self.assertEqual(self.trial.calls, before + 1)
            self.assertFalse(control.snapshot()['valid_request'])
            with self.assertRaisesRegex(ValueError, 'operator_control_invalid'): control.raise_if_failed()
    def test_malformed_duplicate_keys_and_non_utf8(self):
        for i, raw in enumerate((b'not-json', b'[]', b'\xff', b'{"operation":"cancel","operation":"cancel"}')):
            control = self.control(str(i)); (control.directory / 'cancel.json').write_bytes(raw)
            control._observe()
            with self.assertRaisesRegex(ValueError, 'operator_control_invalid'): control.raise_if_failed()
    def test_symlink_nonregular_oversize_fail_without_following(self):
        outside = self.base / 'outside'; outside.write_bytes(b'untouched')
        for i, kind in enumerate(('symlink', 'directory', 'fifo', 'oversize')):
            control = self.control(str(i)); path = control.directory / 'cancel.json'
            if kind == 'symlink': path.symlink_to(outside)
            elif kind == 'directory': path.mkdir()
            elif kind == 'fifo': os.mkfifo(path)
            else: path.write_bytes(b' ' * (MAX_BYTES + 1))
            control._observe()
            with self.assertRaisesRegex(ValueError, 'operator_control_read_failed'): control.raise_if_failed()
        self.assertEqual(outside.read_bytes(), b'untouched')
    def test_directory_replacement_rejected(self):
        control = self.control(); old = self.base / 'old'
        control.directory.rename(old); control.directory.mkdir()
        self.publish(control); control._observe()
        with self.assertRaisesRegex(ValueError, 'operator_control_read_failed'): control.raise_if_failed()
        self.assertEqual(self.trial.calls, 1)
    def test_read_failure_latches_and_does_not_retry(self):
        control = self.control(); self.publish(control)
        with patch('services.operator_stop.os.read', side_effect=OSError('private detail')) as read:
            control._observe(); control._observe(); self.assertEqual(read.call_count, 1)
        self.assertEqual(self.trial.calls, 1)
        self.assertNotIn('private detail', json.dumps(control.snapshot()))
        with self.assertRaisesRegex(ValueError, 'operator_control_read_failed'): control.stop_and_join()
    def test_cancellation_failure_never_claimed_successful(self):
        control = self.control(); self.publish(control)
        with patch.object(self.trial, 'request_cancel', side_effect=OSError('token failure')) as cancel:
            control._observe(); control._observe(); cancel.assert_called_once()
        with self.assertRaisesRegex(ValueError, 'operator_cancel_failed'): control.raise_if_failed()
        self.assertIsNone(control.snapshot()['cancel_returned_monotonic_ns'])
    def test_invalid_control_and_failed_cancel_both_remain_observable(self):
        control = self.control(); self.publish(control, run_id='b' * 32)
        with patch.object(self.trial, 'request_cancel', side_effect=OSError('token failure')):
            control._observe()
        self.assertEqual(control.snapshot()['error'], 'operator_control_invalid')
        self.assertTrue(control.snapshot()['cancel_failed'])
        with self.assertRaisesRegex(ValueError, 'operator_control_invalid'): control.raise_if_failed()
    def test_real_inprocess_watcher_normal_lifetime(self):
        control = self.control(); control.start(); self.publish(control)
        self.assertTrue(self.trial.observed.wait(1))
        control.stop_and_join(); control.stop_and_join()
        self.assertTrue(control.snapshot()['watcher_joined']); self.assertEqual(self.trial.calls, 1)
        with self.assertRaisesRegex(ValueError, 'already_started'): control.start()
    def test_poll_interval_fixed_and_thread_constructor_failure_closed(self):
        control = self.control()
        with patch.object(control, '_observe', side_effect=[False, True]), patch.object(control._stop, 'wait') as wait:
            control._run()
        wait.assert_called_once_with(0.1)
        with patch('services.operator_stop.threading.Thread', side_effect=RuntimeError('allocation failed')):
            with self.assertRaisesRegex(ValueError, 'operator_watcher_start_failed'): control.start()
        self.assertEqual(self.trial.calls, 1)
    def test_mocked_stalled_join_is_failure_not_receipt(self):
        control = self.control()
        fake = Mock(); fake.is_alive.return_value = True
        with patch('services.operator_stop.threading.Thread', return_value=fake):
            control.start()
            with self.assertRaisesRegex(ValueError, 'operator_watcher_join_timeout'): control.stop_and_join()
        fake.join.assert_called_once_with(1.0)
        self.assertFalse(control.snapshot()['watcher_joined']); self.assertEqual(self.trial.calls, 1)
        fake.is_alive.return_value = False  # No real watcher; permit descriptor cleanup.
    def test_unstarted_join_failure_closes_fd_without_successful_join(self):
        control = self.control(); fd = control._fd
        fake = Mock(); fake.start.side_effect = RuntimeError('unstarted')
        fake.join.side_effect = RuntimeError('cannot join before start')
        fake.is_alive.return_value = False
        with patch('services.operator_stop.threading.Thread', return_value=fake):
            with self.assertRaisesRegex(ValueError, 'operator_watcher_start_failed'): control.start()
        with self.assertRaisesRegex(ValueError, 'operator_watcher_start_failed'): control.stop_and_join()
        with self.assertRaises(OSError): os.fstat(fd)
        self.assertIsNone(control._fd); self.assertFalse(control.snapshot()['joined'])
        self.assertEqual(self.trial.calls, 1)
    def test_uncertain_or_live_join_failure_retains_owned_fd(self):
        for index, uncertain in enumerate((False, True)):
            control = self.control('uncertain' + str(index)); fd = control._fd
            fake = Mock(); fake.join.side_effect = RuntimeError('join failed')
            if uncertain: fake.is_alive.side_effect = RuntimeError('unknown')
            else: fake.is_alive.return_value = True
            with patch('services.operator_stop.threading.Thread', return_value=fake): control.start()
            with self.assertRaisesRegex(ValueError, 'operator_watcher_join_failed'): control.stop_and_join()
            self.assertEqual(control._fd, fd); os.fstat(fd)
            self.assertFalse(control.snapshot()['joined'])
            # Only a mocked thread exists; allow test cleanup to release the fd.
            fake.join.side_effect = None; fake.is_alive.side_effect = None; fake.is_alive.return_value = False
    def test_mocked_start_failure_cancels_and_latches(self):
        control = self.control()
        fake = Mock(); fake.start.side_effect = RuntimeError('private detail'); fake.is_alive.return_value = False
        with patch('services.operator_stop.threading.Thread', return_value=fake):
            with self.assertRaisesRegex(ValueError, 'operator_watcher_start_failed'): control.start()
        self.assertEqual(self.trial.calls, 1)


if __name__ == '__main__': unittest.main()
