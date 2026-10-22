"""Same-host absolute deadlines, using synthetic processes only (no JVM/games)."""
import ast
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from runtime.artifacts import execute

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('deadline_test_adapter', ROOT / 'seasons/2023/adapter.py')
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


class AbsoluteDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_expired_deadline_never_spawns_and_retains_empty_logs(self):
        with patch('runtime.artifacts.subprocess.Popen') as popen:
            result = execute(['never-spawn'], self.root / 'out', 60,
                             stderr=self.root / 'err', deadline_monotonic=time.monotonic() - 1)
        popen.assert_not_called()
        self.assertEqual(result['status'], 'timeout')
        self.assertIsNone(result['exit_code'])
        self.assertTrue(result['not_started'])
        self.assertGreaterEqual(result['launch_checked_monotonic'], result['deadline_monotonic'])
        self.assertGreaterEqual(result['finished_monotonic'], result['launch_checked_monotonic'])
        self.assertFalse(result['child_reaped'])
        self.assertNotIn('termination_requested_monotonic', result)
        self.assertNotIn('reaped_monotonic', result)
        self.assertEqual((self.root / 'out').read_bytes(), b'')
        self.assertEqual((self.root / 'err').read_bytes(), b'')
        self.assertIn('log_sha256', result)
        self.assertIn('stderr_sha256', result)

    def test_nonfinite_or_invalid_deadline_rejected_before_spawn(self):
        for value in (float('inf'), float('-inf'), float('nan'), True, '1'):
            with self.subTest(value=value), patch('runtime.artifacts.subprocess.Popen') as popen:
                with self.assertRaises(ValueError):
                    execute(['never-spawn'], self.root / 'out', 60, deadline_monotonic=value)
                popen.assert_not_called()

    def test_legacy_call_without_deadline_still_executes(self):
        result = execute([sys.executable, '-c', 'print("legacy")'], self.root / 'out', 5)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual((self.root / 'out').read_text().strip(), 'legacy')
        self.assertNotIn('not_started', result)
        for field in ('deadline_monotonic', 'launch_checked_monotonic', 'finished_monotonic',
                      'termination_requested_monotonic', 'child_reaped', 'reaped_monotonic'):
            self.assertNotIn(field, result)

    def test_deadline_caps_wait_after_spawn(self):
        with patch('runtime.artifacts.time.monotonic', return_value=10), \
                patch('runtime.artifacts.subprocess.Popen') as popen:
            popen.return_value.wait.return_value = 0
            execute(['synthetic'], self.root / 'out', 600, deadline_monotonic=12.5)
            popen.return_value.wait.assert_called_once_with(timeout=2.5)

    def test_nested_adapter_reaps_detached_child_and_refuses_follow_on(self):
        child = "import os,time;from pathlib import Path;Path(%r).write_text(str(os.getpid())+','+str(os.getpgrp()));time.sleep(30)" % str(self.root / 'child-pid')
        deadline = time.monotonic() + 1.5
        program = f'''import json,os,sys
from pathlib import Path
sys.path.insert(0, {str(ROOT)!r})
from runtime.artifacts import execute
root=Path({str(self.root)!r})
first=execute([sys.executable,'-c',{child!r}],root/'child.log',30,deadline_monotonic={deadline!r})
second=execute([sys.executable,'-c',"from pathlib import Path;Path("+repr(str(root/'forbidden'))+").touch()"],root/'second.log',30,deadline_monotonic={deadline!r})
(root/'summary.json').write_text(json.dumps({{'first':first,'second':second,'parent_pid':os.getpid()}}))
'''
        try:
            outer = execute([sys.executable, '-c', program], self.root / 'adapter.log', 6.5)
            self.assertEqual(outer['status'], 'succeeded')
            summary = json.loads((self.root / 'summary.json').read_text())
            pid, pgid = map(int, (self.root / 'child-pid').read_text().split(','))
            self.assertEqual(pid, pgid)
            self.assertNotEqual(pgid, summary['parent_pid'])
            self.assertEqual(summary['first']['status'], 'timeout')
            self.assertEqual(summary['first']['exit_code'], -signal.SIGKILL)
            first = summary['first']
            self.assertEqual(first['deadline_monotonic'], deadline)
            self.assertLess(first['launch_checked_monotonic'], deadline)
            self.assertGreaterEqual(first['termination_requested_monotonic'], deadline)
            self.assertGreaterEqual(first['reaped_monotonic'], first['termination_requested_monotonic'])
            self.assertGreaterEqual(first['finished_monotonic'], first['reaped_monotonic'])
            self.assertTrue(first['child_reaped'])
            self.assertGreaterEqual(summary['second']['launch_checked_monotonic'], deadline)
            self.assertFalse(summary['second']['child_reaped'])
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
            self.assertTrue(summary['second']['not_started'])
            self.assertEqual(summary['second']['status'], 'timeout')
            self.assertFalse((self.root / 'forbidden').exists())
        finally:
            if (self.root / 'child-pid').exists():
                pid = int((self.root / 'child-pid').read_text().split(',')[0])
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def commands(self):
        return [('build', ['build', '--source', 's', '--package', 'bot']),
                ('run', ['run', '--team-a', 'a', '--team-b', 'b', '--map', 'm']),
                ('pack', ['package', '--build', 'b']),
                ('replay_query', ['replay', 'query', '--replay', 'r', '--round-start', '1', '--round-end', '2']),
                ('replay_page', ['replay', 'page', '--replay', 'r', '--round-start', '1', '--round-end', '2', '--record-offset', '0'])]

    def test_all_leaf_commands_accept_same_deadline_and_legacy_none(self):
        for function, command in self.commands():
            for flags, expected in (([], None), (['--deadline-monotonic', '1234.5'], 1234.5)):
                with self.subTest(command=command, flags=flags), patch.object(adapter, function, return_value=0) as fn, \
                        patch.object(sys, 'argv', ['bcenv'] + command + ['--output', 'out'] + flags):
                    self.assertEqual(adapter.main(), 0)
                    self.assertEqual(fn.call_args.args[0].deadline_monotonic, expected)

    def test_all_leaf_commands_reject_nonfinite_deadline(self):
        for function, command in self.commands():
            for value in ('nan', 'inf', '-inf'):
                with self.subTest(command=command, value=value), patch.object(adapter, function) as fn, \
                        patch.object(sys, 'argv', ['bcenv'] + command + ['--output', 'out', '--deadline-monotonic=' + value]), \
                        contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        adapter.main()
                    self.assertEqual(error.exception.code, 2)
                    fn.assert_not_called()

    def test_every_adapter_subprocess_forwards_absolute_deadline(self):
        tree = ast.parse((ROOT / 'seasons/2023/adapter.py').read_text())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == 'execute']
        self.assertEqual(len(calls), 5)
        for call in calls:
            self.assertTrue(any(keyword.arg is None and isinstance(keyword.value, ast.Call)
                                and isinstance(keyword.value.func, ast.Name)
                                and keyword.value.func.id == 'deadline_options'
                                for keyword in call.keywords))
        self.assertEqual(adapter.deadline_options(type('Legacy', (), {})()), {})
        self.assertEqual(adapter.deadline_options(type('Timed', (), {'deadline_monotonic': 42.0})()),
                         {'deadline_monotonic': 42.0})
