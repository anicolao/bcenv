"""Real SQLite/files + fake Git roundtrip; no subprocess/network/game."""
import hashlib
import json
import unittest
from tests import test_public_retention_snapshot as fixtures
from tests import test_public_retention_git as git_fixtures
from tools.public_retention import restore_evidence
from tools.public_retention_git import publish_checkpoint, PublicationUncertain


class PublicEvidenceRoundtripTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.SnapshotTests('test_real_authority_snapshot_sanitizes_and_chunks_only_approved_evidence')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.projection()
        test = b'def test_public(): assert True\n'
        test_root = self.f.root / 'closed-tests'; test_root.mkdir()
        (test_root / 'test_public.py').write_bytes(test)
        test_sha = hashlib.sha256(test).hexdigest()
        self.f.approved.add(test_sha)
        self.inventory = self.f.export(own_records={'case': {'root': str(test_root),
            'path': 'test_public.py', 'sha256': test_sha, 'bytes': len(test),
            'kind': 'test', 'source_sha256': self.f.source}})
        self.checkout = self.f.root / 'checkout'
        self.checkout.mkdir()
        self.git = git_fixtures.FakeGit(self.checkout)
        self.readback = self.f.root / 'remote-readback'

    def publish(self):
        return publish_checkpoint(self.checkout, self.f.root / 'out', self.inventory,
            'synthetic-run-epoch0-checkpoint0', 'main', git_fixtures.OLD, self.readback,
            git_runner=self.git, repository_id=1431035719)

    def test_sql_closed_export_remote_bytes_and_fresh_evidence(self):
        receipt = self.publish()
        self.assertEqual(receipt['status'], 'verified')
        remote = self.readback / receipt['verified_export']
        pin = self.inventory['files']['inventory.json']['sha256']
        self.assertEqual(hashlib.sha256((remote / 'inventory.json').read_bytes()).hexdigest(), pin)
        restored = self.f.root / 'fresh-workspace'
        result = restore_evidence(remote, restored, approved_inventory_sha256=pin,
            expected_authority_id=self.f.a.authority_id, expected_config_digest=self.f.a.config_digest)
        self.assertTrue(result['evidence_verified'])
        self.assertFalse(result['service_authority_restored'])
        source = restored / 'own-files/sources' / self.f.source / 'own/RobotPlayer.java'
        self.assertEqual(source.read_bytes(), self.f.code)
        self.assertEqual((restored / 'own-files/batches' / self.f.sid / 'game-0/replay.bc23').read_bytes(),
                         (self.f.artifacts / 'game-0/replay.bc23').read_bytes())
        self.assertEqual(list((restored / 'src').iterdir()), [])
        self.assertEqual((restored / 'own-files/records/case/test_public.py').read_bytes(),
                         b'def test_public(): assert True\n')
        evidence = json.loads((restored / 'recovered/evidence.json').read_bytes())
        self.assertEqual(len(evidence['projection_receipts']), 1)
        self.assertEqual(json.loads((restored / 'recovered/evidence.json').read_bytes())['counters']['versions']['used'], 1)
        self.assertEqual(sum(a[0] == 'push' for a, _ in self.git.calls), 1)

    def test_remote_mismatch_never_retained_or_restored(self):
        self.git.corrupt_readback = True
        with self.assertRaises(PublicationUncertain):
            self.publish()
        self.assertFalse((self.f.root / 'fresh-workspace').exists())
        self.assertEqual(sum(a[0] == 'push' for a, _ in self.git.calls), 1)


if __name__ == '__main__':
    unittest.main()
