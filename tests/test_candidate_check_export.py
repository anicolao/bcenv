"""Pure export/import closure regression: never execute a helper or child process.

Runtime pins are inert temporary files. Imports run in-process with subprocess,
process/signal, and cancellation-pipe creation guarded. The negative control
removes the copied dependency and must fail despite source modules being cached.
"""
from contextlib import contextmanager, ExitStack
import hashlib
import importlib.util
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import sysconfig
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def runtime_modules():
    return {name: module for name, module in sys.modules.items()
            if name == 'runtime' or name.startswith('runtime.')}


@contextmanager
def no_process_or_signal():
    """Fail, rather than simulate success, on accidental external execution."""
    with ExitStack() as stack:
        for module, names in (
                (subprocess, ('Popen', 'run', 'call', 'check_call', 'check_output')),
                (os, ('system', 'popen', 'fork', 'forkpty', 'posix_spawn', 'posix_spawnp',
                      'spawnl', 'spawnle', 'spawnlp', 'spawnlpe', 'spawnv', 'spawnve',
                      'spawnvp', 'spawnvpe', 'execl', 'execle', 'execlp', 'execlpe',
                      'execv', 'execve', 'execvp', 'execvpe', 'kill', 'killpg', 'pipe', 'pipe2')),
                (signal, ('signal', 'raise_signal', 'pthread_kill', 'alarm', 'setitimer'))):
            for name in names:
                if hasattr(module, name):
                    stack.enter_context(patch.object(module, name,
                        side_effect=AssertionError('pure test attempted ' + module.__name__ + '.' + name)))
        yield


@contextmanager
def isolated_export_import(workspace):
    """No cached runtime namespace, repository path, site packages, or CWD fallback."""
    original_modules, original_path, original_cwd = runtime_modules(), list(sys.path), os.getcwd()
    try:
        for name in runtime_modules():
            del sys.modules[name]
        paths = sysconfig.get_paths()
        sys.path[:] = list(dict.fromkeys((paths['stdlib'], paths['platstdlib'],
                                         str(Path(paths['stdlib']) / 'lib-dynload'))))
        os.chdir(workspace)
        yield
    finally:
        for name in runtime_modules():
            del sys.modules[name]
        sys.modules.update(original_modules)
        sys.path[:] = original_path
        os.chdir(original_cwd)


class CandidateCheckExportClosureTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(no_process_or_signal())
        # The exporter imports public modules but never invokes their runners.
        saved_path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), saved_path))
        spec = importlib.util.spec_from_file_location('pure_candidate_check_exporter',
                                                      ROOT / 'tools/export_candidate_check.py')
        self.exporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.exporter)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.base = Path(self.temp)
        self.workspace = self.base / 'workspace'; self.workspace.mkdir()
        pins = {}
        for key in ('BCENV_ENGINE_JAR', 'BCENV_JAVA', 'BCENV_JAVAC'):
            path = self.base / key
            path.write_bytes(('inert public test pin ' + key).encode())
            pins[key] = str(path)
        self.stack.enter_context(patch.dict(os.environ, pins))
        self.manifest = self.exporter.export(self.workspace)
        self.export = self.workspace / '.candidate-check'

    def test_export_manifest_and_isolated_import_dependency_closure(self):
        expected = {'tools/candidate_check.py', 'seasons/2023/adapter.py',
                    'runtime/artifacts.py', 'runtime/cancellation.py'}
        self.assertEqual(set(self.manifest['files']), expected)
        for name in expected:
            copied = (self.export / name).read_bytes()
            self.assertEqual(copied, (ROOT / name).read_bytes())
            self.assertEqual(self.manifest['files'][name], hashlib.sha256(copied).hexdigest())
        before = runtime_modules(); before_path = list(sys.path)
        with isolated_export_import(self.workspace):
            self.assertNotIn('runtime.artifacts', sys.modules)
            self.assertNotIn('runtime.cancellation', sys.modules)
            self.assertNotIn('', sys.path)
            self.assertNotIn(str(ROOT), sys.path)
            helper = runpy.run_path(str(self.export / 'tools/candidate_check.py'),
                                    run_name='pure_exported_helper_import')
            self.assertTrue(callable(helper['main']))  # Do not invoke it.
            for name, relative in (('runtime.artifacts', 'runtime/artifacts.py'),
                                   ('runtime.cancellation', 'runtime/cancellation.py')):
                self.assertEqual(Path(sys.modules[name].__file__).resolve(), self.export / relative)
            self.assertEqual(Path(helper['adapter'].__file__).resolve(),
                             self.export / 'seasons/2023/adapter.py')
        self.assertEqual(runtime_modules(), before)
        self.assertEqual(sys.path, before_path)

    def test_missing_dependency_fails_despite_cached_source_runtime(self):
        # Exporter has loaded source artifacts and cancellation. They must not
        # mask a broken export, as they would in a same-process naive import.
        self.assertIn('runtime.cancellation', sys.modules)
        (self.export / 'runtime/cancellation.py').unlink()
        before = runtime_modules(); before_path = list(sys.path)
        with isolated_export_import(self.workspace):
            with self.assertRaises(ModuleNotFoundError) as error:
                runpy.run_path(str(self.export / 'tools/candidate_check.py'),
                               run_name='pure_exported_helper_missing_dependency')
            self.assertEqual(error.exception.name, 'runtime.cancellation')
        self.assertEqual(runtime_modules(), before)
        self.assertEqual(sys.path, before_path)


if __name__ == '__main__':
    unittest.main()
