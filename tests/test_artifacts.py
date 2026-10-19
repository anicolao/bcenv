import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime.artifacts import digest, identity, inventory, execute, new_output
spec = importlib.util.spec_from_file_location('bc23', ROOT / 'seasons/2023/adapter.py')
bc23 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bc23)


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_identity_order_independent(self):
        self.assertEqual(identity({'a': 1, 'b': 2}), identity({'b': 2, 'a': 1}))

    def test_inventory_rejects_symlink(self):
        (self.root / 'secret').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):
            inventory(self.root)

    def test_outputs_never_overwrite(self):
        with self.assertRaises(FileExistsError):
            new_output(self.root)

    def test_timeout_and_nonzero_distinct(self):
        timed = execute([sys.executable, '-c', 'import time; time.sleep(10)'], self.root / 'timed', 0.01)
        failed = execute([sys.executable, '-c', 'raise SystemExit(3)'], self.root / 'failed', 2)
        self.assertEqual(timed['status'], 'timeout')
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['exit_code'], 3)

    def test_separate_machine_output_from_stderr(self):
        execute([sys.executable, '-c', 'import sys; print("{} "); print("warning", file=sys.stderr)'],
                self.root / 'stdout.json', 2, stderr=self.root / 'stderr.log')
        self.assertEqual(json.loads((self.root / 'stdout.json').read_text()), {})
        self.assertIn('warning', (self.root / 'stderr.log').read_text())

    def test_launch_failure_is_evidence(self):
        result = execute(['/nonexistent/bcenv-fixture-executable'], self.root / 'launch.log', 2)
        self.assertEqual(result['status'], 'failed')
        self.assertIsNone(result['exit_code'])
        self.assertIn('launch_error', result)

    def test_replay_identity_binding(self):
        summary = {'status': 'complete', 'spec_version': '2.0.3', 'winner_slot': 'B',
                   'teams': [{'slot': 'B', 'package': 'beta'}, {'slot': 'A', 'package': 'alpha'}],
                   'matches': [{'map': 'Map', 'winner_slot': 'B'}]}
        self.assertTrue(bc23.replay_matches(summary, 'Map', 'alpha', 'beta', '2.0.3'))
        self.assertFalse(bc23.replay_matches(summary, 'OtherMap', 'alpha', 'beta', '2.0.3'))
        self.assertFalse(bc23.replay_matches(summary, 'Map', 'beta', 'alpha', '2.0.3'))
        for invalid in [None, [], {}, {'matches': None}]:
            self.assertFalse(bc23.replay_matches(invalid, 'Map', 'alpha', 'beta', '2.0.3'))
        self.assertFalse(bc23.replay_matches(summary, 'Map', 'alpha', 'beta', '3.0.13'))
        summary['spec_version'] = '3.0.13'
        self.assertTrue(bc23.replay_matches(summary, 'Map', 'alpha', 'beta', '3.0.13'))
        self.assertFalse(bc23.replay_matches(summary, 'Map', 'alpha', 'beta', '2.0.3'))
        summary['spec_version'] = '2.0.3'
        summary['matches'][0]['winner_slot'] = 'A'
        self.assertFalse(bc23.replay_matches(summary, 'Map', 'alpha', 'beta', '2.0.3'))

    def test_embedded_engine_version(self):
        import zipfile
        for version in ('2.0.3', '3.0.13', '3.0.14'):
            jar = self.root / (version + '.jar')
            with zipfile.ZipFile(jar, 'w') as z:
                z.writestr('battlecode_version', version + '\n')
            self.assertEqual(bc23.engine_version(jar), version)
        for i, version in enumerate(('untrusted', '3.0.13\n3.0.14', '0' * 65)):
            jar = self.root / ('invalid-' + str(i) + '.jar')
            with zipfile.ZipFile(jar, 'w') as z:
                z.writestr('battlecode_version', version)
            with self.assertRaises(ValueError):
                bc23.engine_version(jar)

    def test_package_validation(self):
        for name in ['-Jfoo', '../escape', '', 'a/b', 'x;echo hacked']:
            with self.assertRaises(ValueError):
                bc23.package_name(name)
        self.assertEqual(bc23.package_name('legal.bot'), 'legal.bot')

    def artifact(self):
        (self.root / 'source' / 'bot').mkdir(parents=True)
        (self.root / 'source' / 'bot' / 'RobotPlayer.java').write_text('fixture')
        (self.root / 'classes').mkdir()
        (self.root / 'classes' / 'test.class').write_bytes(b'fixture')
        engine = self.root / 'engine.jar'
        engine.write_bytes(b'engine')
        data = {'schema': bc23.VERSION, 'kind': 'build', 'status': 'verified', 'package': 'bot',
                'engine_sha256': digest(engine)}
        for folder, key in [('source', 'source'), ('classes', 'binary')]:
            data[key + '_files'] = inventory(self.root / folder)
            data[key + '_sha256'] = identity(data[key + '_files'])
        (self.root / 'artifact.json').write_text(json.dumps(data))
        return engine

    def test_mutation_rejected(self):
        engine = self.artifact()
        bc23.load_build(self.root, engine)
        (self.root / 'classes' / 'test.class').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            bc23.load_build(self.root, engine)

    def test_engine_mismatch_rejected(self):
        engine = self.artifact()
        engine.write_bytes(b'new version')
        with self.assertRaisesRegex(ValueError, 'Engine differs'):
            bc23.load_build(self.root, engine)

    def test_zip_deterministic_and_source_only(self):
        engine = self.artifact()
        class Args:
            build = str(self.root)
        args = Args()
        with patch.object(bc23, 'runtime', return_value={'BCENV_ENGINE_JAR': str(engine)}):
            args.output = self.root.parent / (self.root.name + '-package1')
            bc23.pack(args)
            args.output = self.root.parent / (self.root.name + '-package2')
            bc23.pack(args)
        first = self.root.parent / (self.root.name + '-package1')
        second = self.root.parent / (self.root.name + '-package2')
        self.addCleanup(__import__('shutil').rmtree, first)
        self.addCleanup(__import__('shutil').rmtree, second)
        self.assertEqual(digest(first / 'source.zip'), digest(second / 'source.zip'))
        import zipfile
        with zipfile.ZipFile(first / 'source.zip') as z:
            self.assertEqual(z.namelist(), ['bot/RobotPlayer.java'])


if __name__ == '__main__':
    unittest.main()
