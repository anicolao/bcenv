import argparse
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('candidate_check', ROOT / 'tools/candidate_check.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
spec2 = importlib.util.spec_from_file_location('export_candidate_check', ROOT / 'tools/export_candidate_check.py')
exporter = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(exporter)


class CandidateCheckTests(unittest.TestCase):
    def test_workspace_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(helper.main(['--workspace', temp, 'build', '--source', '/outside',
                '--package', 'example', '--output', temp + '/out']), 2)

    def test_output_symlink_escape(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as other:
            Path(temp, 'alias').symlink_to(other, target_is_directory=True)
            self.assertEqual(helper.main(['--workspace', temp, 'build', '--source', temp,
                '--package', 'example', '--output', temp + '/alias/out']), 2)

    def test_invalid_launch_prefix(self):
        with tempfile.TemporaryDirectory() as temp:
            for prefix in ([], 'shell', ['relative'], ['/bin/sh', 3], ['/bin/sh', '']):
                with self.assertRaises(ValueError):
                    exporter.export(temp, prefix)
            self.assertFalse(Path(temp, '.candidate-check').exists())

    def test_launch_error(self):
        with tempfile.TemporaryDirectory() as temp:
            r = helper.execute(['/nonexistent/java'], Path(temp, 'log'), 1)
            self.assertEqual(r['status'], 'failed')
            self.assertIn('launch_error', r)

    def test_runtime_environment_stripped(self):
        # Inspect launch arguments without launching a JVM.
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'JAVA_TOOL_OPTIONS': 'bad'}):
            with patch.object(helper.subprocess, 'Popen', side_effect=OSError('mock')) as p:
                helper.execute(['/pinned/javac'], Path(temp, 'log'), 1)
            self.assertNotIn('JAVA_TOOL_OPTIONS', p.call_args.kwargs['env'])
            self.assertEqual(p.call_args.args[0], ['/pinned/javac', '-J-Xmx256m'])


@unittest.skipUnless(all(os.environ.get(k) for k in ('BCENV_ENGINE_JAR', 'BCENV_JAVA', 'BCENV_JAVAC')),
                     'run inside pinned nix develop for real Java integration')
class RealJavaChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'src'
        (self.source / 'example').mkdir(parents=True)
        (self.source / 'example/RobotPlayer.java').write_text(
            'package example; import battlecode.common.RobotController; '
            'public class RobotPlayer { public static void run(RobotController rc) {} '
            'public static int twice(int x) { return x * 2; } }')
        self.built = self.root / 'build'
        summary, code = helper.build(argparse.Namespace(source=str(self.source), package='example',
                                  output=str(self.built), timeout=30))
        self.assertEqual(code, 0, summary)
        self.assertEqual(summary['official_verifier_status'], 'succeeded')
        self.tests = self.root / 'tests'
        self.tests.mkdir()

    def run_java(self, body, timeout=10):
        (self.tests / 'Main.java').write_text('public class Main { public static void main(String[] a) { ' + body + ' }}')
        return helper.test(argparse.Namespace(build=str(self.built), tests=str(self.tests),
                   main_class='Main', output=str(self.root / 'result'), timeout=timeout))

    def test_actual_candidate_method_pass(self):
        result, code = self.run_java('assert example.RobotPlayer.twice(7) == 14;')
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(len(result['steps']), 2)

    def test_actual_candidate_method_assertion_failure(self):
        result, code = self.run_java('assert example.RobotPlayer.twice(7) == 15;')
        self.assertEqual(code, 1)
        self.assertEqual(result['steps'][-1]['status'], 'failed')

    def test_timeout(self):
        result, code = self.run_java('while (true) {}', timeout=1)
        self.assertEqual(code, 1)
        self.assertEqual(result['steps'][-1]['status'], 'timeout')

    def test_output_limit(self):
        result, code = self.run_java('for (int i=0;i<100000;i++) System.out.println("1234567890");')
        self.assertEqual(code, 1)
        self.assertEqual(result['steps'][-1]['status'], 'output_limit')
        self.assertLessEqual((self.root / 'result/test.log').stat().st_size, helper.MAX_LOG)

    def test_compile_failure(self):
        result, code = self.run_java('this is not Java;')
        self.assertEqual(code, 1)
        self.assertEqual(len(result['steps']), 1)

    def test_candidate_tamper_rejected(self):
        (self.built / 'classes/example/RobotPlayer.class').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.run_java('')

    def test_shadow_candidate_rejected(self):
        (self.tests / 'example').mkdir()
        (self.tests / 'example/RobotPlayer.java').write_text('package example; public class RobotPlayer {}')
        result, code = self.run_java('')
        self.assertEqual(code, 1)
        self.assertEqual(result['reason'], 'test_classes_shadow_candidate')

    def test_build_failure_visibly_nonzero(self):
        (self.source / 'example/RobotPlayer.java').write_text('bad Java')
        result, code = helper.build(argparse.Namespace(source=str(self.source), package='example',
                                  output=str(self.root / 'bad-build'), timeout=30))
        self.assertEqual(code, 1)
        self.assertEqual(result['compile_status'], 'failed')
        self.assertEqual(result['official_verifier_status'], 'not_run')
        self.assertEqual(result['status'], 'failed')

    def test_public_api_shadow_rejected(self):
        (self.tests / 'battlecode/common').mkdir(parents=True)
        (self.tests / 'battlecode/common/MapLocation.java').write_text(
            'package battlecode.common; public class MapLocation {}')
        result, code = self.run_java('')
        self.assertEqual(code, 1)
        self.assertEqual(result['reason'], 'test_classes_shadow_public_api')

    def test_test_binary_mutation_detected(self):
        result, code = self.run_java('new java.io.File("test-classes/Main.class").delete();')
        self.assertEqual(code, 1)
        self.assertEqual(result['reason'], 'snapshot_changed')

    def test_official_verifier_rejection_visible(self):
        (self.source / 'example/RobotPlayer.java').write_text(
            'package example; import battlecode.common.RobotController; '
            'public class RobotPlayer { public static void run(RobotController rc) '
            '{ new java.io.File("forbidden"); } }')
        result, code = helper.build(argparse.Namespace(source=str(self.source), package='example',
                                  output=str(self.root / 'unverified-build'), timeout=30))
        self.assertEqual(code, 1)
        self.assertEqual(result['compile_status'], 'succeeded')
        self.assertEqual(result['official_verifier_status'], 'failed')
        self.assertEqual(result['status'], 'failed')

    def test_export_fixed_prefix(self):
        # A transparent argv-preserving fixed operator prefix, not a Java fallback.
        prefix = self.root / 'prefix with spaces'
        prefix.write_text('#!/bin/sh\nexec "$@"\n')
        prefix.chmod(0o755)
        manifest = exporter.export(self.root, [str(prefix)])
        self.assertEqual(manifest['launch_prefix'], [str(prefix)])
        import subprocess
        p = subprocess.run([str(self.root / 'candidate-check'), 'build', '--source', str(self.source),
                            '--package', 'example', '--output', str(self.root / 'prefixed-build')],
                           capture_output=True, text=True, timeout=40)
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        self.assertEqual(json.loads(p.stdout)['status'], 'verified')
        for flag in ('--workspace', '--work', '--workspace=/tmp'):
            args = [str(self.root / 'candidate-check'), flag]
            if '=' not in flag:
                args += ['/tmp']
            args += ['build', '--source', str(self.source), '--package', 'example', '--output', '/tmp/never']
            p = subprocess.run(args, capture_output=True, text=True, timeout=10)
            self.assertEqual(p.returncode, 2)

    def test_export_and_cli(self):
        manifest = exporter.export(self.root)
        self.assertEqual(manifest['runtime']['BCENV_JAVA'], str(Path(os.environ['BCENV_JAVA']).resolve()))
        import subprocess
        p = subprocess.run([str(self.root / 'candidate-check'), 'build', '--source', str(self.source),
                            '--package', 'example', '--output', str(self.root / 'export-build')],
                           capture_output=True, text=True, timeout=40)
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        self.assertEqual(json.loads(p.stdout)['status'], 'verified')
        outside = subprocess.run([str(self.root / 'candidate-check'), '--workspace', '/tmp',
                                  'build', '--source', str(self.source), '--package', 'example',
                                  '--output', '/tmp/unwanted-build'], capture_output=True, text=True, timeout=10)
        self.assertEqual(outside.returncode, 2)
        with self.assertRaises(FileExistsError):
            exporter.export(self.root)


if __name__ == '__main__':
    unittest.main()
