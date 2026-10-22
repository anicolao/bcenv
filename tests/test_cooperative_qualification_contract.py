"""Pure prospective contract checks; never execute the remote lifecycle harness."""
import ast
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tests import cooperative_runtime_qualification as q


class QualificationContractTests(unittest.TestCase):
    def test_exact_five_order_and_invocation_bounds(self):
        self.assertEqual(q.CASE_NAMES, ('packaged_fd_controls', 'source_nested_sigint',
            'source_sync_sigint', 'source_nested_sigterm', 'source_receipt_failure'))
        self.assertEqual(q.MAX_WRAPPER_CALLS, 3)
        self.assertEqual(q.MAX_INNER_LIFETIME, 8)
        self.assertEqual(q.CASE_SECONDS, 40)
        self.assertEqual(q.HARNESS_SECONDS, 600)

    def test_local_entry_fails_before_creating_output_or_process(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=True), \
                patch('subprocess.Popen', side_effect=AssertionError('spawn prohibited')), \
                patch('os.kill', side_effect=AssertionError('signal prohibited')):
            root = Path(d) / 'not-created'
            with self.assertRaisesRegex(RuntimeError, 'remote-only'):
                q.run_remote(root, Path(d) / 'not-a-replay')
            self.assertFalse(root.exists())

    def test_open_worker_reaches_execute_but_cancelled_worker_does_not(self):
        with tempfile.TemporaryDirectory() as d, \
                patch('subprocess.Popen', side_effect=AssertionError('spawn prohibited')), \
                patch('os.kill', side_effect=AssertionError('signal prohibited')):
            worker = q.AdapterWorker(['inert'], [q.DevelopmentGame('inert', 'unused', 'unused', 'A')])
            token = q.Cancellation()
            worker.cancellation, worker.cancel_trial = token, token.cancel
            def no_start(*args, **kwargs):
                kwargs['ownership']['not_started'] = True
                return {'exit_code': 0}
            try:
                with patch.object(q.trial_module, 'execute', side_effect=no_start) as execute:
                    self.assertTrue(worker.invoke(Path(d), 'open', ['build']))
                    execute.assert_called_once()
                    token.cancel()
                    with self.assertRaisesRegex(ValueError, 'run_halted'):
                        worker.invoke(Path(d), 'closed', ['build'])
                    execute.assert_called_once()
            finally:
                token.close()

    def test_single_check_entry_and_no_workflow_expansion(self):
        root = Path(__file__).resolve().parents[1]
        entry = (root / 'nix/verify-replay-query.py').read_text()
        self.assertEqual(entry.count("run_remote('cooperative-qualification', 'synthetic.bc23')"), 1)
        nix = (root / 'nix/checks.nix').read_text()
        self.assertEqual(nix.count('BCENV_REMOTE_COOPERATIVE_QUALIFICATION = "1"'), 1)
        self.assertIn('BCENV_QUALIFICATION_SOURCE = src;', nix)
        # New harness has no direct match/engine command or process enumeration.
        tree = ast.parse((root / 'tests/cooperative_runtime_qualification.py').read_text())
        called = [n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        self.assertNotIn('system', called)
        self.assertNotIn('fork', called)

    def test_first_failure_stops_later_cases_pure_mock(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'BCENV_REMOTE_COOPERATIVE_QUALIFICATION': '1'}), \
                patch('subprocess.Popen', side_effect=AssertionError('spawn prohibited')), \
                patch('os.kill', side_effect=AssertionError('signal prohibited')), \
                patch.object(q, 'packaged_case', side_effect=RuntimeError('unknown owner')) as packaged, \
                patch.object(q, 'source_case', side_effect=AssertionError('later case forbidden')) as source:
            with self.assertRaisesRegex(RuntimeError, 'unknown owner'):
                q.run_remote(Path(d) / 'record', Path(d) / 'unused')
            packaged.assert_called_once()
            source.assert_not_called()


if __name__ == '__main__':
    unittest.main()
