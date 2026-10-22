"""Real atomic client/consumer interleavings; synthetic requests, no games."""
import json
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

import test_continuity_profile as fixtures
from services import local_trial, local_trial_client


class MailboxPublicationTests(unittest.TestCase):
    setUp = fixtures.ContinuityProfileTests.setUp
    cleanup = fixtures.ContinuityProfileTests.cleanup
    schedule = fixtures.ContinuityProfileTests.schedule

    def real_client(self, rename_after_snapshot=False):
        mailbox = self.base / 'real-mailbox'
        ready, staged, inspected, publish, published = [threading.Event() for _ in range(5)]
        stop = threading.Event()
        errors, answers, paths = [], [], []
        replace, lstat, iterdir, unlink = os.replace, Path.lstat, Path.iterdir, Path.unlink

        def producer_replace(source, target, *args, **kwargs):
            if Path(source).parent == mailbox / 'requests' and Path(source).suffix == '.tmp':
                paths.append(Path(source)); staged.set()
                if not publish.wait(15): raise TimeoutError('test publication gate')
                result = replace(source, target, *args, **kwargs)
                published.set()
                return result
            return replace(source, target, *args, **kwargs)

        def consumer_lstat(path, *args, **kwargs):
            result = lstat(path, *args, **kwargs)
            if path in paths: inspected.set()
            return result

        def consumer_snapshot(path):
            snapshot = list(iterdir(path))
            if rename_after_snapshot and path == mailbox / 'requests' and any(p.suffix == '.tmp' for p in snapshot):
                publish.set()
                if not published.wait(15): raise TimeoutError('test rename gate')
                inspected.set()
            return iter(snapshot)

        def observe_unlink(path, *args, **kwargs):
            result = unlink(path, *args, **kwargs)
            if path in paths: inspected.set()  # also makes the old deletion bug fail promptly
            return result

        def server():
            try: local_trial.serve_mailbox(self.trial, mailbox, stop=stop)
            except BaseException as error: errors.append(error)

        def client():
            try: answers.append(local_trial_client.mailbox_request(mailbox, {'operation': 'validation_status'}, timeout=15))
            except BaseException as error: errors.append(error)

        server_thread, client_thread = threading.Thread(target=server), threading.Thread(target=client)
        with patch('builtins.print', side_effect=lambda *a, **k: ready.set()), \
                patch.object(os, 'replace', producer_replace), patch.object(Path, 'lstat', consumer_lstat), \
                patch.object(Path, 'iterdir', consumer_snapshot), patch.object(Path, 'unlink', observe_unlink):
            try:
                server_thread.start(); self.assertTrue(ready.wait(15))
                client_thread.start(); self.assertTrue(staged.wait(15)); self.assertTrue(inspected.wait(15))
                if not rename_after_snapshot:
                    self.assertTrue(paths[0].exists(), 'consumer removed uncommitted staging')
                    self.assertEqual(self.trial.usage_snapshot()['mailbox_attempts'], 0)
                    self.assertEqual(self.trial.requests, 0)
                    publish.set()
                client_thread.join(20); self.assertFalse(client_thread.is_alive())
            finally:
                publish.set(); stop.set()
                if client_thread.ident is not None: client_thread.join(20)
                server_thread.join(20)
        self.assertFalse(server_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(answers), 1)
        self.assertEqual(answers[0]['usage']['mailbox_attempts'], 1)
        self.assertEqual(answers[0]['usage']['dispatch_attempts'], 1)
        self.assertEqual(self.trial.requests, 1)
        stopped = json.loads((mailbox / 'stopped.json').read_text())
        self.assertEqual(stopped['processed'], 1)
        self.assertEqual(list((mailbox / 'requests').iterdir()), [])

    def test_real_client_staging_survives_consumer_before_commit(self):
        self.real_client()

    def test_real_client_rename_after_pending_snapshot_is_not_an_attempt(self):
        self.real_client(rename_after_snapshot=True)

    def seeded(self, seed, stop=None, **kwargs):
        mailbox = self.base / 'seeded-mailbox'
        with patch('builtins.print', side_effect=lambda *a, **k: seed(mailbox)):
            local_trial.serve_mailbox(self.trial, mailbox, stop=stop, **kwargs)
        return mailbox

    def staging_failure(self, kind):
        def seed(mailbox):
            path = mailbox / 'requests' / ('a' * 32 + '.tmp')
            if kind == 'oversize': path.write_bytes(b'x' * (local_trial.MAX_REQUEST + 1))
            elif kind == 'directory': path.mkdir()
            elif kind == 'fifo': os.mkfifo(path)
            else: path.symlink_to(self.maps[0])
        with self.assertRaisesRegex(ValueError, '^mailbox_invalid_staging$'):
            self.seeded(seed)
        self.assertEqual(self.trial.usage_snapshot()['mailbox_attempts'], 0)
        self.assertEqual(self.trial.requests, 0)
        self.assertTrue(self.trial.halted)

    def test_staging_oversize_fails_closed(self): self.staging_failure('oversize')
    def test_staging_symlink_fails_closed(self): self.staging_failure('symlink')
    def test_staging_directory_fails_closed(self): self.staging_failure('directory')
    def test_staging_fifo_fails_closed(self): self.staging_failure('fifo')

    def test_staging_counts_toward_queue_32(self):
        def seed(mailbox):
            for i in range(33): (mailbox / 'requests' / ('%032x.tmp' % i)).touch()
        with self.assertRaisesRegex(ValueError, '^mailbox_queue_limit$'):
            self.seeded(seed)
        self.assertEqual(self.trial.usage_snapshot()['mailbox_attempts'], 0)

    def test_empty_and_maximum_staging_are_ignored_but_storage_accounted(self):
        stop = threading.Event(); samples = []
        def seed(mailbox):
            (mailbox / 'requests' / ('a' * 32 + '.tmp')).touch()
            (mailbox / 'requests' / ('b' * 32 + '.tmp')).write_bytes(b'x' * local_trial.MAX_REQUEST)
        def idle(_):
            samples.append(self.trial.storage_guard.check('staging_test')); stop.set()
        with patch.object(local_trial.time, 'sleep', idle): mailbox = self.seeded(seed, stop)
        self.assertEqual(self.trial.usage_snapshot()['mailbox_attempts'], 0)
        self.assertEqual(len(list((mailbox / 'requests').iterdir())), 2)
        self.assertGreaterEqual(samples[0]['logical_bytes'], local_trial.MAX_REQUEST)

    def test_staging_cannot_extend_absolute_deadline(self):
        def seed(mailbox):
            (mailbox / 'requests' / ('a' * 32 + '.tmp')).touch()
            self.trial.finalist.deadline_monotonic = time.monotonic() - 1
        self.seeded(seed)
        self.assertEqual(self.trial.requests, 0)
        self.assertTrue(self.trial.halted)

    def disappearance(self, before_read):
        mailbox = self.base / 'seeded-mailbox'; request = mailbox / 'requests' / ('a' * 32 + '.json')
        stop = threading.Event(); removed = [0]; vanished = [False]
        open_file, unlink, dispatch = os.open, Path.unlink, self.trial.dispatch
        def seed(_): request.write_text('{"operation":"validation_status"}')
        def read(path, *args, **kwargs):
            if before_read and path == request and not vanished[0]:
                vanished[0] = True; unlink(request)
            return open_file(path, *args, **kwargs)
        def run(body):
            response = dispatch(body)
            if not before_read and not vanished[0]:
                vanished[0] = True; unlink(request)
            return response
        def cleanup(path, *args, **kwargs):
            result = unlink(path, *args, **kwargs)
            if path == request:
                removed[0] += 1
                if removed[0] == 1: seed(mailbox)  # repeated ID cannot become a retry
                else: stop.set()
            return result
        with patch.object(os, 'open', read), patch.object(self.trial, 'dispatch', side_effect=run), patch.object(Path, 'unlink', cleanup):
            self.seeded(seed, stop)
        self.assertEqual(self.trial.usage_snapshot()['mailbox_attempts'], 2)
        self.assertEqual(self.trial.requests, 0 if before_read else 1)
        response = json.loads((mailbox / 'responses' / request.name).read_text())
        if before_read: self.assertEqual(response['reason'], 'invalid_request')
        else: self.assertEqual(response['usage']['dispatch_attempts'], 1)

    def test_committed_disappearance_before_read_retains_debit_and_seen_id(self): self.disappearance(True)
    def test_disappearance_after_dispatch_preserves_response_without_redispatch(self): self.disappearance(False)

    def test_invalid_published_name_disappearance_is_charged_once(self):
        mailbox = self.base / 'seeded-mailbox'; request = mailbox / 'requests' / 'not-canonical.tmp'
        stop = threading.Event(); check = self.trial.storage_checkpoint; unlink = Path.unlink
        def seed(_): request.write_text('{}')
        def checkpoint(stage):
            if stage == 'mailbox_invalid_name': unlink(request); stop.set()
            return check(stage)
        with patch.object(self.trial, 'storage_checkpoint', side_effect=checkpoint): self.seeded(seed, stop)
        self.assertEqual(self.trial.usage_snapshot()['mailbox_attempts'], 1)
        self.assertEqual(self.trial.requests, 0)

    def test_other_cleanup_errors_are_not_suppressed(self):
        mailbox = self.base / 'seeded-mailbox'; request = mailbox / 'requests' / ('a' * 32 + '.json')
        unlink = Path.unlink
        def seed(_): request.write_text('{"operation":"validation_status"}')
        def deny(path, *args, **kwargs):
            if path == request: raise PermissionError('synthetic cleanup denial')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', deny), self.assertRaisesRegex(PermissionError, 'synthetic cleanup denial'):
            self.seeded(seed)
        self.assertEqual(self.trial.requests, 1)
        self.assertTrue((mailbox / 'responses' / request.name).is_file())
        self.assertTrue(self.trial.halted)

    def test_malformed_and_oversize_committed_requests_still_charged(self):
        mailbox = self.base / 'seeded-mailbox'; stop = threading.Event(); unlink = Path.unlink; count = [0]
        def seed(_):
            (mailbox / 'requests' / ('a' * 32 + '.json')).write_text('{')
            (mailbox / 'requests' / ('b' * 32 + '.json')).write_bytes(b'x' * (local_trial.MAX_REQUEST + 1))
        def cleanup(path, *args, **kwargs):
            result = unlink(path, *args, **kwargs)
            if path.parent == mailbox / 'requests':
                count[0] += 1
                if count[0] == 2: stop.set()
            return result
        with patch.object(Path, 'unlink', cleanup): self.seeded(seed, stop)
        self.assertEqual(self.trial.usage_snapshot()['mailbox_attempts'], 2)
        self.assertEqual(self.trial.requests, 0)
        self.assertEqual(len(list((mailbox / 'responses').glob('*.json'))), 2)


if __name__ == '__main__': unittest.main()
