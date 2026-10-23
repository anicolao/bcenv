"""Existing runtime ownership receipts only; pure mocks, never launch or signal."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from runtime import artifacts


class ExistingTimeoutOwnership(unittest.TestCase):
    def setUp(self):
        # Nested tests may replace these guards only with their explicit fakes.
        self.popen_guard = patch('subprocess.Popen', side_effect=AssertionError('real Popen forbidden'))
        self.signal_guard = patch('os.killpg', side_effect=AssertionError('real killpg forbidden'))
        self.popen_guard.start()
        self.signal_guard.start()
        self.addCleanup(self.popen_guard.stop)
        self.addCleanup(self.signal_guard.stop)

    def timed(self, deadline, clock, *, failed_reap=False, cancel_fd=None):
        proc = Mock(pid=123, returncode=-9)
        proc.wait.side_effect = [subprocess.TimeoutExpired('inert', 300),
                                 OSError('owned wait failed') if failed_reap else -9]
        ownership = {}
        with tempfile.TemporaryDirectory() as root, \
                patch.object(artifacts.subprocess, 'Popen', return_value=proc), \
                patch.object(artifacts.os, 'killpg') as kill, \
                patch.object(artifacts.time, 'monotonic', side_effect=clock), \
                patch.object(artifacts, 'cancelled', return_value=False):
            result = artifacts.execute(['inert-not-executed'], Path(root) / 'log', 300,
                                       deadline_monotonic=deadline, cancel_fd=cancel_fd,
                                       ownership=ownership)
        return result, ownership, proc, kill

    def test_original_global_future_timeout_has_positive_wait_and_order(self):
        receipt, owned, proc, kill = self.timed(1000, [0, 1, 2, 301, 302, 303])
        self.assertEqual(receipt['status'], 'timeout')
        self.assertTrue(receipt['child_reaped'])
        self.assertTrue(owned['direct_child_wait_completed'])
        self.assertEqual(proc.wait.call_args_list[0].kwargs, {'timeout': 300})
        self.assertEqual(proc.wait.call_args_list[1].args, ())
        self.assertEqual(receipt['exit_code'], -9)
        self.assertLessEqual(receipt['termination_requested_monotonic'], receipt['reaped_monotonic'])
        self.assertLessEqual(receipt['reaped_monotonic'], receipt['finished_monotonic'])
        self.assertLess(receipt['finished_monotonic'], receipt['deadline_monotonic'])
        kill.assert_called_once_with(proc.pid, artifacts.signal.SIGKILL)

    def test_cancel_enabled_inner_timeout_also_requires_actual_wait(self):
        receipt, owned, proc, _ = self.timed(1000, [0, 1, 2, 300, 301, 302, 303], cancel_fd=99)
        self.assertEqual(receipt['status'], 'timeout')
        self.assertTrue(receipt['child_reaped'])
        self.assertTrue(owned['direct_child_wait_completed'])
        self.assertEqual(proc.wait.call_count, 2)
        self.assertEqual(proc.wait.call_args_list[0].kwargs, {'timeout': .1})
        self.assertLess(receipt['finished_monotonic'], receipt['deadline_monotonic'])

    def test_global_expiry_cannot_supply_positive_remaining_budget(self):
        receipt, owned, proc, _ = self.timed(10, [0, 1, 2, 10, 11, 12])
        self.assertEqual(proc.wait.call_args_list[0].kwargs, {'timeout': 8})
        self.assertTrue(owned['direct_child_wait_completed'])
        self.assertEqual(receipt['status'], 'timeout')
        self.assertFalse(receipt['finished_monotonic'] < receipt['deadline_monotonic'])

    def test_failed_owned_wait_returns_no_normal_receipt(self):
        with self.assertRaisesRegex(OSError, 'owned wait failed'):
            self.timed(1000, [0, 1, 2, 301], failed_reap=True)

    def test_default_receipt_and_single_wait_are_unchanged(self):
        proc = Mock(pid=123, returncode=0)
        proc.wait.return_value = 0
        with tempfile.TemporaryDirectory() as root, \
                patch.object(artifacts.subprocess, 'Popen', return_value=proc), \
                patch.object(artifacts.time, 'monotonic', side_effect=[0, 1]), \
                patch.object(artifacts, 'cancelled', return_value=False):
            receipt = artifacts.execute(['inert-not-executed'], Path(root) / 'log', 300)
        proc.wait.assert_called_once_with(timeout=300)
        self.assertEqual(set(receipt), {'command', 'exit_code', 'status', 'elapsed_seconds', 'log', 'log_sha256'})
        self.assertEqual(receipt['status'], 'succeeded')
        self.assertNotIn('child_reaped', receipt)
        self.assertNotIn('observation', receipt)


if __name__ == '__main__':
    unittest.main()
