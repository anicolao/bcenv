import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from tools.public_retention import restore_evidence, verify_closed_export


def digest(b):
    return hashlib.sha256(b).hexdigest()


class RetentionRestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'export'
        self.root.mkdir()
        (self.root / 'evidence.json').write_bytes(self.evidence())
        self.seal()

    def evidence(self, **extra):
        return json.dumps(dict(schema='bcenv.public-retention-snapshot.v1',
            authority_id='a_test', config_digest='a' * 64, **extra)).encode()

    def seal(self, files=None):
        if files is None:
            b = (self.root / 'evidence.json').read_bytes()
            files = {'evidence.json': {'sha256': digest(b), 'bytes': len(b)}}
        raw = json.dumps({'schema': 'bcenv.public-retention-inventory.v1', 'files': files}).encode()
        (self.root / 'inventory.json').write_bytes(raw)
        self.pin = digest(raw)

    def test_fresh_restore_is_evidence_only(self):
        out = self.base / 'restored'
        r = restore_evidence(self.root, out, approved_inventory_sha256=self.pin, expected_authority_id='a_test', expected_config_digest='a' * 64)
        self.assertTrue(r['evidence_verified'])
        self.assertFalse(r['service_authority_restored'])
        self.assertFalse(r['author_resumed'])
        self.assertEqual(list((out / 'src').iterdir()), [])
        self.assertEqual((out / 'recovered/evidence.json').read_bytes(), (self.root / 'evidence.json').read_bytes())

    def test_chunked_own_file_restoration(self):
        data = b'public own source'
        h = digest(data)
        (self.root / 'blobs').mkdir()
        (self.root / 'blobs' / h).write_bytes(data)
        evidence = self.evidence(own_files={'sources/' + 'a' * 64 + '/Player.java':
            {'bytes': len(data), 'sha256': h, 'chunks': [h]}})
        (self.root / 'evidence.json').write_bytes(evidence)
        self.seal({'evidence.json': {'bytes': len(evidence), 'sha256': digest(evidence)},
                   'blobs/' + h: {'bytes': len(data), 'sha256': h}})
        out = self.base / 'restored'
        restore_evidence(self.root, out, approved_inventory_sha256=self.pin, expected_authority_id='a_test', expected_config_digest='a' * 64)
        self.assertEqual((out / 'own-files/sources' / ('a' * 64) / 'Player.java').read_bytes(), data)
        self.assertEqual(list((out / 'src').iterdir()), [])

    def test_chunked_logical_file_above_blob_ceiling(self):
        chunk = b'x' * (1024 * 1024)
        h = digest(chunk)
        (self.root / 'blobs').mkdir()
        (self.root / 'blobs' / h).write_bytes(chunk)
        full = hashlib.sha256()
        for _ in range(50):
            full.update(chunk)
        evidence = self.evidence(own_files={'batches/s_public/game-0/replay.bc23':
            {'bytes': len(chunk) * 50, 'sha256': full.hexdigest(), 'chunks': [h] * 50}})
        (self.root / 'evidence.json').write_bytes(evidence)
        self.seal({'evidence.json': {'bytes': len(evidence), 'sha256': digest(evidence)},
                   'blobs/' + h: {'bytes': len(chunk), 'sha256': h}})
        out = self.base / 'large-restored'
        restore_evidence(self.root, out, approved_inventory_sha256=self.pin,
                         expected_authority_id='a_test', expected_config_digest='a' * 64)
        self.assertEqual((out / 'own-files/batches/s_public/game-0/replay.bc23').stat().st_size,
                         50 * 1024 * 1024)

    def test_uncommitted_chunk_refused(self):
        evidence = self.evidence(own_files={'sources/a/Player.java':
            {'bytes': 1, 'sha256': digest(b'x'), 'chunks': [digest(b'x')]}})
        (self.root / 'evidence.json').write_bytes(evidence)
        self.seal()
        out = self.base / 'partial'
        with self.assertRaises(ValueError):
            restore_evidence(self.root, out, approved_inventory_sha256=self.pin, expected_authority_id='a_test', expected_config_digest='a' * 64)
        self.assertFalse((out / 'work/RESTORE.json').exists())

    def test_wrong_authority(self):
        with self.assertRaises(ValueError):
            restore_evidence(self.root, self.base / 'out', approved_inventory_sha256=self.pin,
                             expected_authority_id='wrong', expected_config_digest='a' * 64)
        self.assertFalse((self.base / 'out').exists())

    def test_approval_binding(self):
        with self.assertRaises(ValueError):
            verify_closed_export(self.root, approved_inventory_sha256='0' * 64)

    def test_extra_file(self):
        (self.root / 'secret').write_text('not allowed')
        with self.assertRaises(ValueError):
            verify_closed_export(self.root, approved_inventory_sha256=self.pin)

    def test_drift(self):
        (self.root / 'evidence.json').write_text('changed')
        with self.assertRaises(ValueError):
            restore_evidence(self.root, self.base / 'out', approved_inventory_sha256=self.pin, expected_authority_id='a_test', expected_config_digest='a' * 64)
        self.assertFalse((self.base / 'out').exists())

    def test_symlink(self):
        (self.root / 'link').symlink_to(self.root / 'evidence.json')
        with self.assertRaises(ValueError):
            verify_closed_export(self.root, approved_inventory_sha256=self.pin)

    def test_traversal(self):
        self.seal({'../outside': {'bytes': 0, 'sha256': digest(b'')}})
        with self.assertRaises(ValueError):
            verify_closed_export(self.root, approved_inventory_sha256=self.pin)

    def test_existing_destination(self):
        out = self.base / 'existing'; out.mkdir()
        with self.assertRaises(ValueError):
            restore_evidence(self.root, out, approved_inventory_sha256=self.pin, expected_authority_id='a_test', expected_config_digest='a' * 64)

    def test_bool_size(self):
        self.seal({'evidence.json': {'bytes': True, 'sha256': '0' * 64}})
        with self.assertRaises(ValueError):
            verify_closed_export(self.root, approved_inventory_sha256=self.pin)

    def test_duplicate_json_key(self):
        raw = b'{"schema":"bcenv.public-retention-inventory.v1","files":{},"files":{}}'
        (self.root / 'inventory.json').write_bytes(raw)
        with self.assertRaises(ValueError):
            verify_closed_export(self.root, approved_inventory_sha256=digest(raw))


if __name__ == '__main__':
    unittest.main()
