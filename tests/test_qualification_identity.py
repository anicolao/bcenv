"""Pure evidence-retention tests; never runs Git/Nix/runtime or a fixture."""
import copy
import json
import hashlib
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools.record_qualification_identity import record, git_identities


class QualificationIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        names = ['packaged_fd_controls', 'source_nested_sigint', 'source_sync_sigint',
                 'source_nested_sigterm', 'source_receipt_failure']
        self.evidence = {'status': 'passed_five_narrow_cases', 'fixture_sha256': 'synthetic',
                         'source_files': {'synthetic': 'digest'}, 'cases': [
            {'name': n, 'status': 'passed_with_explicit_scope_limits',
             'source_adapter_invocations': 1, 'inner_helpers': 1} for n in names]}
        self.evidence['cases'][0].update(wrapper_calls=3, new_query_jvms=1,
            identity={'wrapper': '/nix/store/synthetic/bin/bcenv', 'installed': {'module': 'hash'}, 'source': {'module': 'hash'}})
        (self.root / 'store-path.json').write_text(json.dumps({'/nix/store/synthetic': {'narHash': 'synthetic'}}))
        self.source = self.root / 'source'
        paths = ('runtime/artifacts.py', 'runtime/cancellation.py', 'services/local_trial.py', 'seasons/2023/adapter.py', 'tests/cooperative_runtime_qualification.py')
        for name in paths:
            p = self.source / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('synthetic ' + name)
        hashes = {name: hashlib.sha256((self.source / name).read_bytes()).hexdigest() for name in paths}
        self.evidence['fixture_sha256'] = hashes.pop('tests/cooperative_runtime_qualification.py')
        self.evidence['source_files'] = hashes
        modules = {p: h for p, h in hashes.items() if p != 'services/local_trial.py'}
        self.evidence['cases'][0]['identity'].update(installed=dict(modules), source=dict(modules))
        for name, selected in [('active', True), ('diagnostic', False)]:
            (self.root / (name + '-selection.json')).write_text(json.dumps({'selected': selected, 'status': 'selected' if selected else 'not_selected'}))
        self.guard = patch('subprocess.Popen', side_effect=AssertionError('no processes')); self.guard.start(); self.addCleanup(self.guard.stop)

    def publish(self):
        (self.root / 'cooperative-qualification.json').write_text(json.dumps(self.evidence))
        return record(self.root, 'a' * 40, 'b' * 40, 'c' * 40, 'c' * 40, self.source)

    def test_binds_distinct_head_tested_revision_tree_once(self):
        result = self.publish()
        self.assertEqual(result['pr_head'], 'a' * 40)
        self.assertEqual(result['tested_revision'], 'b' * 40)
        self.assertEqual(result['tested_tree'], 'c' * 40)
        before = (self.root / 'qualification-identity.json').read_bytes()
        with self.assertRaises(FileExistsError): self.publish()
        self.assertEqual(before, (self.root / 'qualification-identity.json').read_bytes())

    def test_mismatched_installed_identity_rejected(self):
        self.evidence['cases'][0]['identity']['installed']['module'] = 'different'
        with self.assertRaisesRegex(ValueError, 'identity mismatch'): self.publish()
        self.assertFalse((self.root / 'qualification-identity.json').exists())

    def test_duplicate_sequence_or_extra_invocations_rejected(self):
        original = copy.deepcopy(self.evidence)
        self.evidence['cases'] *= 2
        with self.assertRaisesRegex(ValueError, 'single expected sequence'): self.publish()
        self.evidence = original; self.evidence['cases'][1]['inner_helpers'] = 2
        with self.assertRaisesRegex(ValueError, 'invocation counts'): self.publish()

    def test_invalid_git_identity_rejected_before_publication(self):
        with self.assertRaisesRegex(ValueError, 'Git identity'):
            record(self.root, 'not-a-head', 'b' * 40, 'c' * 40, 'c' * 40, self.source)
        self.assertFalse((self.root / 'qualification-identity.json').exists())

    def test_duplicate_diagnostic_selection_rejected(self):
        (self.root / 'diagnostic-selection.json').write_text(json.dumps({'selected': True, 'status': 'selected'}))
        with self.assertRaisesRegex(ValueError, 'diagnostic selection'): self.publish()

    def test_checkout_hash_mismatch_rejected(self):
        (self.source / 'runtime/artifacts.py').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'checkout source identity'): self.publish()

    def test_head_tree_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, 'trees differ'):
            record(self.root, 'a' * 40, 'b' * 40, 'c' * 40, 'd' * 40, self.source)

    def test_shallow_head_lookup_one_bounded_exact_fetch_only_mocked(self):
        with patch('tools.record_qualification_identity.subprocess.run', side_effect=[SimpleNamespace(returncode=1), SimpleNamespace(returncode=0)]) as run, \
                patch('tools.record_qualification_identity.subprocess.check_output', side_effect=['b' * 40, 'c' * 40, 'c' * 40]):
            self.assertEqual(git_identities('a' * 40), ('b' * 40, 'c' * 40, 'c' * 40))
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args.args[0], ['git', 'fetch', '--no-tags', '--depth=1', 'origin', 'a' * 40])
            self.assertEqual(run.call_args.kwargs['timeout'], 30)
            with self.assertRaisesRegex(ValueError, 'invalid Git head'):
                git_identities('unsafe;notsha')
            self.assertEqual(run.call_count, 2)
