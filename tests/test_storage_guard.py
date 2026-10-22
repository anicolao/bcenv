"""Synthetic logical storage accounting; no engine games or external sources."""
import json
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from services.storage_guard import StorageGuard, LOG_NAME, MAX_CHECKPOINT_BYTES


class StorageGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / 'service'; self.root.mkdir()
    def tearDown(self): self.temp.cleanup()
    def records(self):
        return [json.loads(line) for line in (self.root / LOG_NAME).read_bytes().splitlines()]
    def test_recursive_temporary_and_checkpoint_accounting(self):
        (self.root / 'child').mkdir()
        (self.root / 'child' / '.partial.tmp').write_bytes(b'12345')
        (self.root / 'output').write_bytes(b'123')
        guard = StorageGuard(self.root)
        first = guard.check('adapter.before')
        self.assertEqual(first['logical_bytes'], 8)
        self.assertEqual(first['regular_files'], 2)
        log_size = (self.root / LOG_NAME).stat().st_size
        second = guard.check('adapter.after')
        self.assertEqual(second['logical_bytes'], 8 + log_size)
        self.assertEqual(second['regular_files'], 3)
        self.assertEqual(second['high_water_bytes'], second['logical_bytes'])
        self.assertEqual([r['sequence'] for r in self.records()], [1, 2])
        self.assertTrue(all(len(line) + 1 <= MAX_CHECKPOINT_BYTES for line in (self.root / LOG_NAME).read_bytes().splitlines()))
    def test_full_mailbox_scope_and_candidate_sibling_excluded(self):
        candidate = self.base / 'candidate'; candidate.mkdir()
        (candidate / 'source.java').write_bytes(b'x' * 5000)
        mailbox = candidate / 'mailbox'; mailbox.mkdir()
        for name, count in [('requests', 2), ('responses', 3), ('.tmp', 5), ('control', 7)]:
            (mailbox / name).mkdir(); (mailbox / name / 'entry').write_bytes(b'x' * count)
        guard = StorageGuard(self.root); guard.add_mailbox(mailbox)
        result = guard.check('dispatch.before')
        self.assertEqual(result['logical_bytes'], 17); self.assertEqual(result['scope_count'], 2)
        self.assertNotIn(str(self.base), (self.root / LOG_NAME).read_text())
    def test_overlapping_mailboxes_not_double_counted(self):
        mailbox = self.root / 'mailbox'; mailbox.mkdir()
        (mailbox / 'nested').mkdir(); (mailbox / 'nested' / 'message').write_bytes(b'four')
        guard = StorageGuard(self.root)
        guard.add_mailbox(mailbox); guard.add_mailbox(mailbox / 'nested'); guard.add_mailbox(mailbox)
        result = guard.check('overlap')
        self.assertEqual(result['logical_bytes'], 4); self.assertEqual(result['scope_count'], 1)
    def test_exact_threshold_allowed_then_log_overhead_detected(self):
        (self.root / 'payload').write_bytes(b'12345')
        guard = StorageGuard(self.root, limit_bytes=5)
        self.assertEqual(guard.check('threshold')['status'], 'ok')
        self.assertGreater(sum(p.stat().st_size for p in self.root.iterdir()), 5)
        with self.assertRaisesRegex(ValueError, '^storage_limit$'): guard.check('next')
        self.assertEqual(self.records()[-1]['status'], 'storage_limit')
    def test_growth_detected_and_failure_latched_after_deletion(self):
        payload = self.root / 'temporary-output'
        guard = StorageGuard(self.root, limit_bytes=10000)
        guard.check('before')
        payload.write_bytes(b'x' * 10001)
        with self.assertRaisesRegex(ValueError, '^storage_limit$'): guard.check('after')
        observed = guard.high_water_bytes
        payload.unlink()
        with self.assertRaisesRegex(ValueError, '^storage_limit$'): guard.check('no_reset')
        self.assertEqual(guard.high_water_bytes, observed)
    def test_sparse_file_uses_logical_not_allocated_bytes(self):
        with (self.root / 'sparse').open('wb') as handle: handle.truncate(100000)
        result = StorageGuard(self.root).check('sparse')
        self.assertEqual(result['logical_bytes'], 100000)
    def test_hard_links_count_each_entry(self):
        (self.root / 'first').write_bytes(b'123')
        os.link(self.root / 'first', self.root / 'second')
        self.assertEqual(StorageGuard(self.root).check('hardlinks')['logical_bytes'], 6)
    def test_symlink_entry_and_symlink_directory_fail_without_following(self):
        outside = self.base / 'outside'; outside.mkdir()
        (outside / 'canary').write_bytes(b'untouched')
        for target in (outside / 'canary', outside):
            link = self.root / 'link'; link.symlink_to(target)
            with self.assertRaisesRegex(ValueError, '^storage_scan_failed$'):
                StorageGuard(self.root).check('symlink')
            link.unlink()
        self.assertEqual((outside / 'canary').read_bytes(), b'untouched')
    def test_symlink_ancestor_or_root_rejected(self):
        link = self.base / 'link'; link.symlink_to(self.root)
        (self.root / 'child').mkdir()
        for target in (link, link / 'child'):
            with self.assertRaisesRegex(ValueError, '^storage_scan_failed$'): StorageGuard(target)
        guard = StorageGuard(self.root)
        with self.assertRaisesRegex(ValueError, '^storage_scan_failed$'): guard.add_mailbox(link)
    def test_special_file_fails_without_blocking(self):
        os.mkfifo(self.root / 'fifo')
        with self.assertRaisesRegex(ValueError, '^storage_scan_failed$'):
            StorageGuard(self.root).check('fifo')
    def test_evidence_symlink_cannot_modify_target(self):
        target = self.base / 'secret'; target.write_bytes(b'unchanged')
        (self.root / LOG_NAME).symlink_to(target)
        with self.assertRaisesRegex(ValueError, '^storage_scan_failed$'):
            StorageGuard(self.root).check('evidence')
        self.assertEqual(target.read_bytes(), b'unchanged')
    def test_disappearing_entry_is_latched_failure_not_ignored(self):
        (self.root / 'vanishing.tmp').write_text('temporary child output')
        original = os.scandir
        @contextmanager
        def disappearing(fd):
            with original(fd) as entries:
                snapshot = list(entries)
                for entry in snapshot:
                    if entry.name == 'vanishing.tmp':
                        os.unlink(entry.name, dir_fd=fd)
                yield iter(snapshot)
        guard = StorageGuard(self.root)
        with patch('services.storage_guard.os.scandir', side_effect=disappearing):
            with self.assertRaisesRegex(ValueError, '^storage_scan_failed$'):
                guard.check('concurrent_cleanup')
        self.assertEqual(self.records()[-1]['status'], 'storage_scan_failed')
        with self.assertRaisesRegex(ValueError, '^storage_scan_failed$'): guard.check('later')

    def test_scan_failure_closed_and_best_effort_evidence(self):
        guard = StorageGuard(self.root)
        with patch('services.storage_guard.os.scandir', side_effect=PermissionError('private-canary')):
            with self.assertRaisesRegex(ValueError, '^storage_scan_failed$'): guard.check('scan')
        record = self.records()[-1]
        self.assertEqual(record['status'], 'storage_scan_failed'); self.assertIsNone(record['logical_bytes'])
        self.assertNotIn('private-canary', (self.root / LOG_NAME).read_text())
    def test_write_and_fsync_failures_close_without_claiming_success(self):
        for target in ('os.write', 'os.fsync'):
            guard = StorageGuard(self.root)
            with patch('services.storage_guard.' + target, side_effect=OSError('full disk')):
                with self.assertRaisesRegex(ValueError, '^storage_scan_failed$'): guard.check('write')
            self.assertEqual(guard.failure, 'storage_scan_failed')
    def test_fsync_failure_retry_is_marked_not_a_second_checkpoint(self):
        guard = StorageGuard(self.root)
        with patch('services.storage_guard.os.fsync', side_effect=[OSError('disk'), None]):
            with self.assertRaisesRegex(ValueError, '^storage_scan_failed$'): guard.check('fsync')
        records = self.records()
        self.assertEqual([r['sequence'] for r in records], [1, 1])
        self.assertEqual([r['evidence_attempt'] for r in records], [1, 2])
        self.assertEqual(records[-1]['status'], 'storage_scan_failed')
    def test_short_write_fails_closed(self):
        guard = StorageGuard(self.root)
        with patch('services.storage_guard.os.write', return_value=1):
            with self.assertRaisesRegex(ValueError, '^storage_scan_failed$'): guard.check('short')
    def test_missing_root_mailbox_and_deleted_mailbox_fail(self):
        with self.assertRaisesRegex(ValueError, '^storage_scan_failed$'): StorageGuard(self.base / 'missing')
        guard = StorageGuard(self.root)
        with self.assertRaisesRegex(ValueError, '^storage_scan_failed$'): guard.add_mailbox(self.base / 'missing')
        mailbox = self.base / 'mailbox'; mailbox.mkdir()
        guard = StorageGuard(self.root); guard.add_mailbox(mailbox); mailbox.rmdir()
        with self.assertRaisesRegex(ValueError, '^storage_scan_failed$'): guard.check('removed')
    def test_invalid_stage_is_bounded_sanitized_failure(self):
        guard = StorageGuard(self.root)
        with self.assertRaisesRegex(ValueError, '^storage_scan_failed$'): guard.check('private/' + 'x' * 5000)
        self.assertEqual(self.records()[-1]['stage'], 'invalid_stage')
        self.assertLess((self.root / LOG_NAME).stat().st_size, MAX_CHECKPOINT_BYTES)
    def test_invalid_limits(self):
        for value in (0, -1, True, 1.5, '100'):
            with self.assertRaisesRegex(ValueError, '^invalid_storage_limit$'): StorageGuard(self.root, value)


if __name__ == '__main__': unittest.main()
