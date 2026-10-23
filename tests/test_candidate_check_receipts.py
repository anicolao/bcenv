"""Pure mocked receipts: never start Java, helpers, or send actual signals."""
import argparse
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('receipt_check', ROOT / 'tools/candidate_check.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class Receipts(unittest.TestCase):
    def setUp(self):
        self.guard_process = patch('subprocess.Popen', side_effect=AssertionError('real Popen forbidden'))
        self.guard_signal = patch('os.killpg', side_effect=AssertionError('real signal forbidden'))
        self.guard_process.start()
        self.guard_signal.start()
        self.addCleanup(self.guard_process.stop)
        self.addCleanup(self.guard_signal.stop)

    def phase(self, *, kill_error=None, wait_error=None, close_error=None, timeout=False, launch_error=False, eof_timeout=False, overflow=False, execution_error=False, exit_code=0):
        process = Mock(pid=123, returncode=-9 if timeout else exit_code)
        process.wait.side_effect = [process.returncode, wait_error] if wait_error else None
        process.wait.return_value = process.returncode
        if eof_timeout:
            process.wait.side_effect = [subprocess.TimeoutExpired('javac', 1), process.returncode]
        if close_error:
            process.stdout.close.side_effect = close_error
        selector = Mock()
        selector.__enter__ = Mock(return_value=selector)
        selector.__exit__ = Mock(return_value=False)
        selector.get_map.return_value = {1: 1} if timeout or overflow or execution_error else {}
        selector.select.return_value = [(Mock(fileobj=process.stdout), 1)]
        if execution_error:
            selector.select.side_effect = OSError('read selector error')
        clock = iter([0, 2, 3, 4, 5]) if timeout else None
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(check.subprocess, 'Popen', side_effect=OSError('launch') if launch_error else None,
                             return_value=process), \
                patch.object(check.os, 'killpg', side_effect=kill_error) as kill, \
                patch.object(check.selectors, 'DefaultSelector', return_value=selector), \
                patch.object(check.os, 'read', return_value=b'x' * (check.MAX_LOG + 1)), \
                patch.object(check.time, 'monotonic', side_effect=clock, return_value=0):
            result = check.execute(['/p/javac', 'Input.java'], Path(temp) / 'compile.log', 1)
        return result, process, kill

    def test_success_positive_wait_and_effective_command(self):
        result, process, _ = self.phase()
        self.assertEqual(result['status'], 'succeeded')
        self.assertTrue(result['owned_cleanup_verified'])
        self.assertEqual(result['requested_timeout_seconds'], 1)
        self.assertEqual(result['command'], ['/p/javac', '-J-Xmx256m', 'Input.java'])
        self.assertTrue(result['direct_child_wait']['completed'])
        self.assertEqual(result['direct_child_wait']['exit_code'], 0)
        self.assertEqual(process.wait.call_count, 2)
        self.assertTrue(result['stdout_close']['completed'])

    def test_ordinary_nonzero_exit_has_positive_cleanup_not_executor_error(self):
        result, _, _ = self.phase(exit_code=1)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['exit_code'], 1)
        self.assertTrue(result['owned_cleanup_verified'])
        self.assertNotIn('execution_error', result)
        self.assertNotIn('cleanup_failed', result)

    def test_timeout_has_wait_not_exit_code_inference(self):
        result, process, _ = self.phase(timeout=True)
        self.assertEqual(result['status'], 'timeout')
        self.assertEqual(result['exit_code'], -9)
        self.assertTrue(result['direct_child_wait']['completed'])
        self.assertEqual(process.wait.call_count, 1)

    def test_signal_failure_still_waits_and_closes(self):
        result, process, _ = self.phase(kill_error=PermissionError('denied'))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['group_cleanup']['outcome'], 'error')
        self.assertTrue(result['direct_child_wait']['completed'])
        process.stdout.close.assert_called_once()

    def test_missing_group_is_not_descendant_proof(self):
        result, _, _ = self.phase(kill_error=ProcessLookupError())
        self.assertEqual(result['group_cleanup']['outcome'], 'group_not_found')
        self.assertTrue(result['direct_child_wait']['completed'])

    def test_failed_final_wait_does_not_claim_success(self):
        result, process, _ = self.phase(wait_error=OSError('wait failed'))
        self.assertFalse(result['direct_child_wait']['completed'])
        self.assertFalse(result['owned_cleanup_verified'])
        self.assertEqual(result['status'], 'failed')
        process.stdout.close.assert_called_once()

    def test_close_failure_retains_successful_wait(self):
        result, _, _ = self.phase(close_error=OSError('close failed'))
        self.assertTrue(result['direct_child_wait']['completed'])
        self.assertFalse(result['stdout_close']['completed'])
        self.assertEqual(result['status'], 'failed')

    def test_launch_failure_has_no_wait_or_signal(self):
        result, process, kill = self.phase(launch_error=True)
        self.assertFalse(result['direct_child_started'])
        self.assertFalse(result['direct_child_wait']['completed'])
        self.assertFalse(result['owned_cleanup_verified'])
        process.wait.assert_not_called()
        kill.assert_not_called()

    def test_post_eof_timeout_still_waits(self):
        result, process, _ = self.phase(eof_timeout=True)
        self.assertEqual(result['status'], 'timeout')
        self.assertEqual(process.wait.call_count, 2)
        self.assertTrue(result['owned_cleanup_verified'])

    def test_output_limit_is_not_timeout(self):
        result, _, _ = self.phase(overflow=True)
        self.assertEqual(result['status'], 'output_limit')
        self.assertTrue(result['owned_cleanup_verified'])

    def test_execution_error_is_explicit(self):
        result, _, _ = self.phase(execution_error=True)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('selector', result['execution_error'])
        self.assertTrue(result['owned_cleanup_verified'])

    def test_explicit_timeout_and_bounds(self):
        with tempfile.TemporaryDirectory() as temp:
            base = ['--workspace', temp, 'build', '--source', temp, '--package', 'sample', '--output', temp + '/out']
            for timeout in (1, 120):
                with patch.object(check, 'build', return_value=({}, 0)) as build, patch('builtins.print'):
                    self.assertEqual(check.main(base + ['--timeout', str(timeout)]), 0)
                    self.assertEqual(build.call_args.args[0].timeout, timeout)
            for timeout in (0, 121):
                with patch.object(check, 'build') as build, patch('sys.stderr'), self.assertRaises(SystemExit):
                    check.main(base + ['--timeout', str(timeout)])
                build.assert_not_called()

    def test_cleanup_interrupt_waits_closes_then_propagates(self):
        process = Mock(pid=123, returncode=0)
        selector = Mock()
        selector.__enter__ = Mock(return_value=selector)
        selector.__exit__ = Mock(return_value=False)
        selector.get_map.return_value = {}
        for stage in ('kill', 'wait', 'close'):
            process.reset_mock(side_effect=True)
            process.wait.side_effect = [0, KeyboardInterrupt()] if stage == 'wait' else None
            process.stdout.close.side_effect = KeyboardInterrupt() if stage == 'close' else None
            with tempfile.TemporaryDirectory() as temp, \
                    patch.object(check.subprocess, 'Popen', return_value=process), \
                    patch.object(check.os, 'killpg', side_effect=KeyboardInterrupt() if stage == 'kill' else None), \
                    patch.object(check.selectors, 'DefaultSelector', return_value=selector), \
                    self.assertRaises(KeyboardInterrupt):
                check.execute(['/p/javac'], Path(temp) / 'log', 30)
            self.assertEqual(process.wait.call_count, 2)
            process.stdout.close.assert_called_once()

    def test_direct_api_does_not_invent_argv(self):
        value = check.caller_receipt(argparse.Namespace(timeout=30, source='relative', fn=lambda: None), 1)
        self.assertIsNone(value['invocation']['argv'])
        self.assertEqual(value['invocation']['effective_options'], {'timeout': 30, 'source': 'relative'})
        self.assertFalse(value['helper_return_prepared']['external_terminal_observed'])

    def test_main_records_exact_argv_and_default_timeout(self):
        with tempfile.TemporaryDirectory() as temp:
            argv = ['--workspace', temp, 'build', '--source', temp, '--package', 'sample', '--output', temp + '/out']
            captured = []
            def fake(args):
                captured.append(check.caller_receipt(args, 0))
                return {}, 0
            with patch.object(check, 'build', side_effect=fake), patch('builtins.print'):
                self.assertEqual(check.main(argv), 0)
            self.assertEqual(captured[0]['invocation']['argv'], argv)
            self.assertEqual(captured[0]['invocation']['effective_options']['timeout'], 30)


if __name__ == '__main__':
    unittest.main()
