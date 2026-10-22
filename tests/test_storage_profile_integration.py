"""Synthetic continuity storage/selection wiring; no engine or candidate runs."""
import unittest
import json
import threading
from pathlib import Path
from unittest.mock import patch
import test_continuity_profile as fixtures
from services import local_trial


class StorageProfileIntegrationTests(unittest.TestCase):
    setUp = fixtures.ContinuityProfileTests.setUp
    cleanup = fixtures.ContinuityProfileTests.cleanup
    schedule = fixtures.ContinuityProfileTests.schedule
    note = fixtures.ContinuityProfileTests.note
    submit = fixtures.ContinuityProfileTests.submit
    flush = fixtures.ContinuityProfileTests.flush

    def cross_at(self, target):
        original = self.trial.storage_guard.check
        def check(stage):
            if stage == target:
                self.trial.storage_guard.limit_bytes = 1
            return original(stage)
        return patch.object(self.trial.storage_guard, 'check', side_effect=check)

    def finalists(self):
        ids = []
        for _ in range(2):
            ids.append(self.submit()); self.flush()
        return ids

    def lock(self, ids):
        return self.trial.dispatch({'operation': 'lock_finalists', 'submission_ids': ids,
                                   'provisional_choice': ids[0]})

    def test_status_usage_includes_current_request_and_actual_debits(self):
        self.trial.mailbox_debit(1)
        first = self.trial.dispatch({'operation': 'validation_status'})['usage']
        self.assertEqual(first, {'read_debits': 1, 'replay_debits': 0, 'note_debits': 0,
                                'training_version_debits': 0, 'mailbox_attempts': 1, 'dispatch_attempts': 1})
        self.submit(); self.flush()
        self.trial.replays = 451  # rejected replay debits are not clamped to successful responses
        second = self.trial.dispatch({'operation': 'validation_status'})['usage']
        self.assertEqual(second['read_debits'], 2)
        self.assertEqual(second['note_debits'], 1)
        self.assertEqual(second['training_version_debits'], 1)
        self.assertEqual(second['replay_debits'], 451)
        self.assertEqual(second['dispatch_attempts'], self.trial.requests)
        self.assertEqual(second, self.trial.usage_snapshot())

    def test_actual_mailbox_status_counts_itself_and_binds_receipt(self):
        mailbox = self.base / 'status-mailbox'
        request_id = 'a' * 32 + '.json'
        stop = threading.Event()
        unlink = Path.unlink
        def seed(*args, **kwargs):
            (mailbox / 'requests' / request_id).write_text('{"operation":"validation_status"}')
        def remove(path, *args, **kwargs):
            result = unlink(path, *args, **kwargs)
            if path == mailbox / 'requests' / request_id:
                stop.set()
            return result
        with patch('builtins.print', side_effect=seed), patch.object(Path, 'unlink', remove):
            local_trial.serve_mailbox(self.trial, mailbox, stop=stop)
        response = json.loads((mailbox / 'responses' / request_id).read_text())
        self.assertEqual(response['usage']['mailbox_attempts'], 1)
        self.assertEqual(response['usage']['dispatch_attempts'], 1)
        self.assertEqual(response['usage']['read_debits'], 1)
        events = [json.loads(line) for line in (self.trial.root / 'audit.jsonl').read_text().splitlines()]
        event = next(e for e in events if e['operation'] == 'validation_status')
        self.assertEqual(event['response']['usage'], response['usage'])
        self.assertEqual(event['time_ns'], response['audit_receipt']['time_ns'])

    def test_legacy_status_does_not_add_usage_schema(self):
        worker = fixtures.FakeAdapter(self.worker.command, self.worker.games)
        trial = local_trial.LocalTrial(self.base / 'legacy-status', worker,
            max_submissions=8, validation_games=self.validation)
        self.trials.append(trial)
        status = trial.dispatch({'operation': 'validation_status'})
        self.assertNotIn('usage', status)
        self.assertNotIn('resolved_profile', status)
        self.assertEqual(trial.reads, 1)

    def test_usage_excludes_validation_copies_from_version_debit(self):
        ids = self.finalists(); self.lock(ids); self.flush()
        self.assertEqual(len(self.trial.records), 4)
        self.assertEqual(self.trial.usage_snapshot()['training_version_debits'], 2)

    def test_storage_threshold_matches_resolved_profile(self):
        self.assertEqual(self.trial.storage_guard.limit_bytes,
                         self.trial.profile_bounds['service_storage_high_water_bytes'])

    def test_legacy_entrypoint_ignored_knobs_remain_ignored(self):
        options = local_trial.trial_options({'max_reads': 1, 'max_replay_queries': 1, 'max_mailbox_requests': 1})
        self.assertEqual((options['max_reads'], options['max_replay_queries'], options['max_mailbox_requests']),
                         (2000, 150, 2200))

    def test_admission_exceedance_halts_before_training(self):
        self.trial.storage_guard.limit_bytes = 1
        with self.assertRaisesRegex(ValueError, 'run_halted'): self.submit()
        self.assertTrue(self.trial.halted)
        self.assertEqual(self.trial.finalist.attempts, [])
        self.assertEqual(self.trial.records, {})

    def test_adapter_output_crossing_halts_before_next_operation(self):
        self.trial.storage_guard.limit_bytes = 100000
        def output(*args):
            (self.trial.root / 'synthetic-child.tmp').write_bytes(b'x' * 110000)
            return {'exit_code': 0}
        with patch.object(local_trial, 'execute', side_effect=output) as execute:
            with self.assertRaisesRegex(ValueError, 'run_halted'):
                self.worker.invoke(self.trial.root, 'synthetic', ['build'])
            self.assertEqual(execute.call_count, 1)
            with self.assertRaisesRegex(ValueError, 'run_halted'):
                self.worker.invoke(self.trial.root, 'synthetic', ['build'])
            self.assertEqual(execute.call_count, 1)
        self.assertTrue(self.trial.halted)

    def test_failed_adapter_still_checks_output_growth(self):
        self.trial.storage_guard.limit_bytes = 100000
        def output(*args):
            (self.trial.root / 'failed-child.tmp').write_bytes(b'x' * 110000)
            raise OSError('synthetic child failure')
        with patch.object(local_trial, 'execute', side_effect=output):
            with self.assertRaisesRegex(ValueError, 'run_halted'):
                self.worker.invoke(self.trial.root, 'synthetic', ['build'])
        self.assertTrue(self.trial.halted)
        self.assertEqual(self.trial.storage_guard.failure, 'storage_limit')

    def test_copy_crossing_prevents_validation_game(self):
        ids = self.finalists()
        with self.cross_at('validation_copy_after'):
            self.lock(ids); self.flush()
        self.assertTrue(self.trial.halted)
        self.assertEqual(self.trial.finalist.state()['validation_games_attempted'], 0)
        self.assertIsNone(self.trial.finalist.final_choice)
        self.assertTrue(any((self.trial.root / vid / 'build').exists()
                            for vid in self.trial.finalist.validation_ids.values()))

    def test_preselection_crossing_rejects_choice(self):
        ids = self.finalists(); self.lock(ids); self.flush()
        with self.cross_at('selection_authorization'):
            with self.assertRaisesRegex(ValueError, 'run_halted'):
                self.trial.dispatch({'operation': 'select_finalist', 'submission_id': ids[0]})
        self.assertIsNone(self.trial.finalist.final_choice)
        self.assertTrue(self.trial.halted)

    def test_postselection_receipt_crossing_preserves_authorized_choice_but_halts(self):
        ids = self.finalists(); self.lock(ids); self.flush()
        with self.cross_at('request_after'):
            response = self.trial.dispatch({'operation': 'select_finalist', 'submission_id': ids[0]})
        self.assertEqual(response['final_choice'], ids[0])
        self.assertEqual(self.trial.finalist.final_choice, ids[0])
        self.assertEqual(self.trial.finalist.phase, 'selected')
        self.assertTrue(self.trial.halted)
        with self.assertRaisesRegex(ValueError, 'run_halted'): self.note()

    def test_scan_failure_halts_no_fallback(self):
        with patch.object(self.trial.storage_guard, 'check', side_effect=ValueError('storage_scan_failed')):
            with self.assertRaisesRegex(ValueError, 'run_halted'): self.note()
        self.assertTrue(self.trial.halted)
        self.assertEqual(self.trial.finalist.attempts, [])


if __name__ == '__main__': unittest.main()
