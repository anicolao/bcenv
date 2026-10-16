"""Builder export contract tests; synthetic store files, no engine or agent run."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PilotExportTests(unittest.TestCase):
    def test_export_is_self_contained_and_has_no_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            store = temp / 'store-root'
            runtime = store / 'nix/store/runtime/lib/bcenv'
            for relative in ['runtime/artifacts.py', 'seasons/2023/adapter.py', 'replay-classes/fixture.class']:
                path = runtime / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('synthetic fixture')
            engine = store / 'nix/store/engine/share'
            (engine / 'battlecode23').mkdir(parents=True)
            (engine / 'battlecode23/engine.jar').write_text('synthetic fixture')
            for folder in ['api', 'api-source', 'specs']:
                (engine / 'doc/battlecode23' / folder).mkdir(parents=True)
            for dependency in ['python', 'jdk']:
                (store / 'nix/store' / dependency).mkdir(parents=True)
            (store / 'unrelated-secret').write_text('must not be exported')
            closure = temp / 'closure'
            closure.write_text('/nix/store/python\n/nix/store/jdk\n')
            out = temp / 'export'
            command = [sys.executable, str(ROOT / 'scripts/materialize-local-pilot.py'),
                       '--store-root', str(store), '--closure', str(closure),
                       '--bcenv', '/nix/store/runtime', '--engine', '/nix/store/engine',
                       '--python', '/nix/store/python', '--jdk', '/nix/store/jdk', '--output', str(out)]
            subprocess.run(command, check=True, capture_output=True)
            workspace = out / 'root/workspace'
            manifest = json.loads((workspace / 'manifest.json').read_text())
            for relative in [manifest['rules'], *manifest['api'], *manifest['skills'],
                             manifest['tools'], manifest['trial'], 'START_HERE.md']:
                self.assertTrue((workspace / relative).exists(), relative)
            self.assertEqual(list((workspace / 'competitor/src').iterdir()), [])
            self.assertEqual(list((workspace / 'work').iterdir()), [])
            self.assertEqual((out / 'KICKOFF.txt').read_bytes(), (ROOT / 'prompts/2023/local-pilot.txt').read_bytes())
            trial = json.loads((workspace / 'trial.json').read_text())
            self.assertEqual((trial['minutes'], trial['max_matches'], trial['concurrency']), (20, 12, 1))
            self.assertFalse(trial['replica_required'])
            release = json.loads((out / 'release.json').read_text())
            for relative, expected in release['files'].items():
                self.assertEqual(hashlib.sha256((out / relative).read_bytes()).hexdigest(), expected)
            self.assertFalse(any('unrelated-secret' in str(p) for p in out.rglob('*')))
            # Export cannot reset an existing competitor lineage in place.
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
