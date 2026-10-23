"""Publication seam tests: fake Git only, no network/process/push execution."""
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.public_retention_git import (PUBLIC_REMOTE, MAX_BLOB, MAX_HISTORY,
    PublicationUncertain, publish_checkpoint, validate_inventory, _read_bounded)

OLD = 'a' * 40
NEW = 'b' * 40


class FakeGit:
    def __init__(self, checkout):
        self.checkout = checkout
        self.head = OLD
        self.calls = []
        self.blobs = {}
        self.paths = {}
        self.push_error = False
        self.readback_bad = False
        self.old_mismatch = False
        self.history_size = 100
        self.reuse = False
        self.parent = OLD
        self.pushed = False
        self.extra_ref = False
        self.shallow = False
        self.corrupt_readback = False
        self.mutate_prior = False

    def __call__(self, argv, *, cwd):
        assert argv[:2] == ['git', '--no-replace-objects']
        a = argv[2:]
        self.calls.append((a, cwd))
        if a[0] == 'status': return b''
        if a[0] == 'rev-parse':
            if a[1] == '--git-path': return b'.git/info/grafts'
            if a[1] == '--is-shallow-repository': return b'true' if self.shallow else b'false'
            if a[1] == 'FETCH_HEAD': return ((OLD if self.readback_bad else NEW) + '\n').encode()
            return (self.head + '\n').encode()
        if a[0] == 'log': return (OLD + '\n').encode() if self.reuse else b''
        if a[0] == 'ls-remote':
            return ((NEW if self.old_mismatch or self.pushed else OLD) + '\trefs/heads/main\n' + (OLD + '\trefs/tags/extra\n' if self.extra_ref else '')).encode()
        if a[0] == 'ls-tree':
            if a[-1] == OLD: return b''
            return b''.join(('100644 blob ' + oid + '\t' + name).encode() + b'\0' for name, oid in sorted(self.paths.items()))
        if a[0] == 'rev-list':
            if a[1] == '--parents': return (NEW + ' ' + self.parent + '\n').encode()
            return ('\n'.join([OLD] + ([NEW] + list(self.blobs) if a[-1] == NEW else [])) + '\n').encode()
        if a[0] == 'cat-file':
            oid = a[-1]
            if a[1] == '-t': return b'blob\n' if oid in self.blobs else b'commit\n'
            if a[1] == '-s': return str(len(self.blobs[oid]) if oid in self.blobs else self.history_size).encode()
            if self.corrupt_readback and cwd != self.checkout:
                return b'corrupted'
            return self.blobs[oid]
        if a[0] == 'commit':
            self.head = NEW
            if self.mutate_prior:
                (self.checkout/'unrelated').write_bytes(b'changed')
            for path in self.checkout.rglob('*'):
                if path.is_file():
                    value = path.read_bytes()
                    oid = hashlib.sha1(value).hexdigest()
                    self.paths[path.relative_to(self.checkout).as_posix()] = oid
                    self.blobs[oid] = value
            return b'created\n'
        if a[0] == 'push':
            if self.push_error: raise OSError('uncertain push')
            self.pushed = True
            return b''
        if a[0] in ('add', 'init', 'fetch'): return b''
        raise AssertionError(a)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.checkout = self.root / 'checkout'; self.checkout.mkdir()
        self.export = self.root / 'export'; self.export.mkdir()
        value = b'{"public":true}\n'
        (self.export / 'public-state.json').write_bytes(value)
        self.inventory = {'schema':'bcenv.public-retention-inventory.v1', 'files':{
            'public-state.json':{'bytes':len(value),'sha256':hashlib.sha256(value).hexdigest()}}}
        self.git = FakeGit(self.checkout)
        for name in ('subprocess.Popen', 'os.system', 'os.killpg'):
            guard = patch(name, side_effect=AssertionError('actual process/signal forbidden'))
            guard.start(); self.addCleanup(guard.stop)

    def publish(self, **kw):
        return publish_checkpoint(self.checkout, self.export, self.inventory, 'cp_001', 'main', OLD,
                                  self.root / 'readback', git_runner=self.git, repository_id=1431035719, **kw)

    def test_success_requires_one_cas_push_and_fresh_exact_readback(self):
        receipt = self.publish()
        self.assertEqual(receipt['status'], 'verified')
        self.assertEqual(receipt['commit'], NEW)
        validate_inventory(self.root/'readback'/'verified-export', self.inventory)
        pushes = [a for a, _ in self.git.calls if a[0] == 'push']
        self.assertEqual(pushes, [['push', '--force-with-lease=refs/heads/main:' + OLD,
                                 PUBLIC_REMOTE, NEW + ':refs/heads/main']])
        self.assertTrue(any(a == ['fetch', '--no-tags', PUBLIC_REMOTE, NEW] and cwd == self.root/'readback'
                            for a, cwd in self.git.calls))
        self.assertTrue((self.checkout/'checkpoints/cp_001/checkpoint-inventory.json').is_file())

    def test_push_failure_is_uncertain_and_never_retried(self):
        self.git.push_error = True
        with self.assertRaises(PublicationUncertain): self.publish()
        self.assertEqual(sum(a[0] == 'push' for a, _ in self.git.calls), 1)
        self.assertFalse(any(a[0] == 'fetch' for a, _ in self.git.calls))

    def test_readback_mismatch_never_claims_success_or_repushes(self):
        self.git.readback_bad = True
        with self.assertRaises(PublicationUncertain): self.publish()
        self.assertEqual(sum(a[0] == 'push' for a, _ in self.git.calls), 1)

    def test_remote_commit_matching_but_bytes_corrupted_is_uncertain(self):
        self.git.corrupt_readback = True
        with self.assertRaises(PublicationUncertain): self.publish()
        self.assertEqual(sum(a[0] == 'push' for a, _ in self.git.calls), 1)

    def test_new_history_exceeding_cap_not_pushed(self):
        self.git.history_size = MAX_HISTORY // 2 + 1
        with self.assertRaisesRegex(ValueError, 'history exceeds'): self.publish()
        self.assertFalse(any(a[0] == 'push' for a, _ in self.git.calls))

    def test_commit_cannot_mutate_unrelated_files(self):
        self.git.mutate_prior = True
        with self.assertRaisesRegex(ValueError, 'prior checkpoint or unrelated'): self.publish()
        self.assertFalse(any(a[0] == 'push' for a, _ in self.git.calls))

    def test_remote_old_mismatch_stops_before_commit_push(self):
        self.git.old_mismatch = True
        with self.assertRaisesRegex(ValueError, 'remote expected-old'): self.publish()
        self.assertFalse(any(a[0] in ('commit','push') for a, _ in self.git.calls))

    def test_extra_remote_refs_refused_without_repair(self):
        self.git.extra_ref = True
        with self.assertRaisesRegex(ValueError, 'remote expected-old'): self.publish()
        self.assertFalse(any(a[0] in ('commit', 'push') for a, _ in self.git.calls))

    def test_history_reused_checkpoint_is_rejected(self):
        self.git.reuse = True
        with self.assertRaisesRegex(ValueError, 'already used'): self.publish()

    def test_non_descendant_not_pushed(self):
        self.git.parent = 'c'*40
        with self.assertRaisesRegex(ValueError, 'direct descendant'): self.publish()
        self.assertFalse(any(a[0] == 'push' for a, _ in self.git.calls))

    def test_entire_reachable_history_cap_before_publication(self):
        self.git.history_size = MAX_HISTORY + 1
        with self.assertRaisesRegex(ValueError, 'history exceeds'): self.publish()
        self.assertFalse(any(a[0] == 'push' for a, _ in self.git.calls))

    def test_blob_size_limit_no_large_allocation(self):
        self.inventory['files']['public-state.json']['bytes'] = MAX_BLOB + 1
        with self.assertRaisesRegex(ValueError, 'oversized'): self.publish()
        self.assertEqual(self.git.calls, [])

    def test_unlisted_and_changed_bytes_rejected(self):
        (self.export/'extra').write_bytes(b'no')
        with self.assertRaisesRegex(ValueError, 'unlisted'): self.publish()
        (self.export/'extra').unlink()
        (self.export/'public-state.json').write_bytes(b'x'*self.inventory['files']['public-state.json']['bytes'])
        with self.assertRaisesRegex(ValueError, 'hash mismatch'): self.publish()

    def test_symlink_and_path_traversal_rejected(self):
        (self.export/'link').symlink_to(self.export/'public-state.json')
        with self.assertRaisesRegex(ValueError, 'symlink'): self.publish()
        (self.export/'link').unlink()
        self.inventory['files']['../escape'] = self.inventory['files'].pop('public-state.json')
        with self.assertRaisesRegex(ValueError, 'unsafe'): self.publish()

    def test_legacy_graft_ancestry_is_rejected(self):
        path = self.checkout/'.git/info/grafts'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'')
        with self.assertRaisesRegex(ValueError, 'graft'): self.publish()
        self.assertFalse(any(a[0] == 'push' for a, _ in self.git.calls))

    def test_shallow_history_refused_before_copy_or_push(self):
        self.git.shallow = True
        with self.assertRaisesRegex(ValueError, 'non-shallow'): self.publish()
        self.assertFalse((self.checkout/'checkpoints').exists())
        self.assertFalse(any(a[0] == 'push' for a, _ in self.git.calls))

    def test_growing_source_read_is_bounded_and_rejected(self):
        class RecordingStream(io.BytesIO):
            requested = []
            def read(self, size=-1):
                self.requested.append(size)
                return super().read(size)
        stream = RecordingStream(b'growth')
        with patch.object(Path, 'open', return_value=stream):
            with self.assertRaisesRegex(ValueError, 'size changed'):
                _read_bounded(self.export/'public-state.json', 3)
        self.assertEqual(stream.requested, [4])

    def test_wrong_repository_id_rejected(self):
        with self.assertRaisesRegex(ValueError, 'identity'):
            publish_checkpoint(self.checkout, self.export, self.inventory, 'cp', 'main', OLD,
                               self.root/'readback', git_runner=self.git, repository_id=123)
        self.assertEqual(self.git.calls, [])

    def test_symlink_checkpoint_parent_rejected_before_copy(self):
        elsewhere = self.root/'elsewhere'; elsewhere.mkdir()
        (self.checkout/'checkpoints').symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'parent'): self.publish()
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_workflow_inventory_path_rejected(self):
        self.inventory['files']['.github/workflows/run.yml'] = self.inventory['files'].pop('public-state.json')
        with self.assertRaisesRegex(ValueError, 'workflow'): self.publish()

    def test_existing_readback_or_credential_url_rejected(self):
        (self.root/'readback').mkdir()
        with self.assertRaisesRegex(ValueError, 'fresh'): self.publish()
        (self.root/'readback').rmdir()
        with self.assertRaisesRegex(ValueError, 'identity'):
            self.publish(remote_url='https://secret@github.com/anicolao/bcenv-artifacts.git')
        self.assertEqual(self.git.calls, [])


if __name__ == '__main__':
    unittest.main()
