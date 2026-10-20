"""Synthetic worker receipts and real temporary SQLite, without source execution."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import FrozenInstanceError, replace
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.controller import launch_real
from services.ledger import DurableLedger, LedgerError, LedgerLimits
from services.scrimmage import BoundaryUnverified, require_heldout_attestation
from services.worker_lifecycle import SyntheticJobSpecification, SyntheticWorkerBridge

SOURCE = b'package bot; class RobotPlayer {}'


def archive():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        z.writestr('bot/RobotPlayer.java', SOURCE)
    return output.getvalue()


def receipt(request, outcome=None):
    return {'schema': 'bcenv.synthetic-worker-receipt.v1',
            'submission_id': request.submission_id, 'generation': request.generation,
            'source_sha256': request.source.content_sha256,
            'spec_sha256': request.specification.sha256,
            'outcome': outcome or {'status': 'complete',
                'summary': {'games': 1, 'wins': 1, 'draws': 0, 'losses': 0}}}


def encoded(request, outcome=None):
    return json.dumps(receipt(request, outcome), separators=(',', ':')).encode()


class WorkerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'jobs.db'
        self.now = 1000
        self.ledger = DurableLedger(self.path, clock=lambda: self.now)
        self.calls = []

    def submit(self, key='one', owner='alice'):
        return self.ledger.submit(owner, key, archive(), 'bot')['submission_id']

    def callback(self, request):
        self.calls.append(request)
        return encoded(request)

    def bridge(self, callback=None):
        return SyntheticWorkerBridge(self.ledger, self.callback if callback is None else callback)

    def error(self, code, function, *args):
        with self.assertRaises(LedgerError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def sql(self, statement, args=()):
        with closing(sqlite3.connect(self.path)) as db, db:
            return db.execute(statement, args).fetchall()

    def test_valid_fabricated_receipt_completes_one_synthetic_job(self):
        identifier = self.submit()
        result = self.bridge().run_job('alice', identifier)
        self.assertEqual(result['summary'], {'games': 1, 'wins': 1, 'draws': 0, 'losses': 0})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(set(result), {'schema', 'submission_id', 'status', 'summary'})
        self.assertNotIn('validated_complete', result)
        self.assertEqual(self.sql('SELECT state,generation,lease_until FROM jobs'), [('complete', 1, None)])
        # No actual game was run: accepting this fabricated callback is precisely
        # the synthetic trust seam, not artifact validation or strength evidence.

    def test_fixed_specification_and_exact_immutable_source_reach_callback(self):
        def callback(request):
            self.assertEqual(request.source.files[0].data, SOURCE)
            self.assertEqual(request.source.files[0].path, 'bot/RobotPlayer.java')
            self.assertEqual(request.specification.profile, 'inert-single-game')
            self.assertEqual(request.specification.games, 1)
            self.assertFalse(hasattr(request, 'opponent'))
            self.assertFalse(hasattr(request, 'host_path'))
            with self.assertRaises(FrozenInstanceError):
                request.source.files[0].data = b'mutation'
            with self.assertRaises(FrozenInstanceError):
                request.specification.games = 100
            with self.assertRaises(TypeError):
                SyntheticJobSpecification(profile='candidate-selected')
            return encoded(request)
        self.bridge(callback).run_job('alice', self.submit())

    def test_wrong_owner_and_unqueued_job_prevent_callback(self):
        identifier = self.submit()
        bridge = self.bridge()
        for guessed in (identifier, 's_' + 'f' * 32, '../private'):
            self.error('not_found', bridge.run_job, 'bob', guessed)
        bridge.run_job('alice', identifier)
        self.error('job_not_queued', bridge.run_job, 'alice', identifier)
        self.assertEqual(len(self.calls), 1)

    def test_stale_generation_between_reserve_and_precheck_prevents_callback(self):
        reserve = self.ledger.reserve_job
        def stale(*args):
            return replace(reserve(*args), generation=999)
        with patch.object(self.ledger, 'reserve_job', side_effect=stale):
            self.error('stale_reservation', self.bridge().run_job, 'alice', self.submit())
        self.assertEqual(self.calls, [])
        self.assertEqual(self.sql('SELECT state FROM jobs'), [('running',)])

    def test_expiry_between_reserve_and_precheck_prevents_callback(self):
        reserve = self.ledger.reserve_job
        def expire(*args):
            claim = reserve(*args)
            self.now = claim.lease_until
            return claim
        with patch.object(self.ledger, 'reserve_job', side_effect=expire):
            self.error('stale_reservation', self.bridge().run_job, 'alice', self.submit())
        self.assertEqual(self.calls, [])
        self.assertEqual(self.sql('SELECT state FROM jobs'), [('infrastructure_failure',)])

    def test_precheck_does_not_consume_public_read_quota(self):
        identifier = self.submit()
        claim = self.ledger.reserve_job('alice', identifier, 'worker')
        checked = self.ledger.check_reservation('alice', identifier, 'worker', claim.generation)
        self.assertEqual(checked, claim)
        self.assertEqual(self.sql("SELECT count(*) FROM rate_events WHERE kind='read'"), [(0,)])
        self.error('stale_reservation', self.ledger.check_reservation, 'alice', identifier, 'other', claim.generation)
        self.error('invalid_request', self.ledger.check_reservation, 'alice', identifier, 'worker', True)
        self.error('not_found', self.ledger.check_reservation, 'bob', identifier, 'worker', claim.generation)

    def test_precheck_revalidates_source_integrity(self):
        identifier = self.submit()
        claim = self.ledger.reserve_job('alice', identifier, 'worker')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DROP TRIGGER immutable_source_files_update')
            db.execute('UPDATE source_files SET data=?', (b'x' * len(SOURCE),))
        self.error('storage_integrity', self.ledger.check_reservation, 'alice', identifier, 'worker', claim.generation)

    def test_expiry_during_callback_prevents_late_score(self):
        def callback(request):
            self.calls.append(request)
            self.now += 30
            return encoded(request)
        identifier = self.submit()
        self.error('terminal_conflict', self.bridge(callback).run_job, 'alice', identifier)
        result = self.ledger.result('alice', identifier)
        self.assertEqual(result['reason'], 'worker_unavailable')
        self.assertNotIn('summary', result)
        self.assertEqual(len(self.calls), 1)

    def test_clock_regression_during_callback_commits_failure_before_refusal(self):
        def callback(request):
            self.now -= 1
            return encoded(request)
        identifier = self.submit()
        self.error('clock_regression', self.bridge(callback).run_job, 'alice', identifier)
        self.assertEqual(self.sql('SELECT state FROM jobs'), [('infrastructure_failure',)])
        self.now = 1000
        self.assertNotIn('summary', self.ledger.result('alice', identifier))

    def test_concurrent_terminal_write_wins_without_overwrite(self):
        def callback(request):
            self.ledger.finish('alice', request.submission_id, 'synthetic-worker', request.generation,
                               {'status': 'rejected', 'reason': 'compile_rejected'})
            return encoded(request)
        identifier = self.submit()
        self.error('terminal_conflict', self.bridge(callback).run_job, 'alice', identifier)
        self.assertEqual(self.ledger.result('alice', identifier)['status'], 'rejected')

    def test_callback_exception_is_closed_terminal_failure_and_never_retried(self):
        def callback(request):
            self.calls.append(request)
            raise RuntimeError('/private/opponent source logs')
        identifier = self.submit()
        bridge = self.bridge(callback)
        result = bridge.run_job('alice', identifier)
        self.assertEqual((result['status'], result['reason']), ('infrastructure_failure', 'internal_error'))
        self.assertNotIn('private', json.dumps(result))
        self.assertNotIn('summary', result)
        self.error('job_not_queued', bridge.run_job, 'alice', identifier)
        self.assertEqual(len(self.calls), 1)

    def test_base_exception_leaves_reserved_job_for_terminal_recovery(self):
        def callback(request):
            raise KeyboardInterrupt()
        identifier = self.submit()
        with self.assertRaises(KeyboardInterrupt):
            self.bridge(callback).run_job('alice', identifier)
        self.assertEqual(self.sql('SELECT state FROM jobs'), [('running',)])
        self.ledger = DurableLedger(self.path, clock=lambda: self.now)
        self.now += 30
        self.assertEqual(self.ledger.recover_expired(), 1)
        self.assertNotIn('summary', self.ledger.result('alice', identifier))
        self.error('job_not_queued', self.bridge().run_job, 'alice', identifier)
        self.assertEqual(self.calls, [])

    def test_uncertain_commit_is_propagated_and_not_changed_into_failure_or_retried(self):
        finish = self.ledger.finish
        def commit_then_raise(*args):
            finish(*args)
            raise LedgerError('storage_unavailable')
        identifier = self.submit()
        bridge = self.bridge()
        with patch.object(self.ledger, 'finish', side_effect=commit_then_raise):
            self.error('storage_unavailable', bridge.run_job, 'alice', identifier)
        self.assertEqual(self.ledger.result('alice', identifier)['status'], 'complete')
        self.error('job_not_queued', bridge.run_job, 'alice', identifier)
        self.assertEqual(len(self.calls), 1)

    def test_concurrent_bridge_calls_invoke_worker_exactly_once(self):
        identifier = self.submit()
        bridge = self.bridge()
        def run(index):
            try:
                return bridge.run_job('alice', identifier)['status']
            except LedgerError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(run, range(8)))
        self.assertEqual(outcomes.count('complete'), 1)
        self.assertEqual(outcomes.count('job_not_queued'), 7)
        self.assertEqual(len(self.calls), 1)

    def test_receipt_identity_mismatches_all_become_invalid_evidence(self):
        changes = {'schema': 'other', 'submission_id': 's_' + 'f'*32, 'generation': True,
                   'source_sha256': '0'*64, 'spec_sha256': '0'*64}
        for index, (key, value) in enumerate(changes.items()):
            def callback(request, key=key, value=value):
                body = receipt(request)
                body[key] = value
                return json.dumps(body).encode()
            result = self.bridge(callback).run_job('alice', self.submit(str(index)))
            self.assertEqual(result['reason'], 'invalid_evidence')
            self.assertNotIn('summary', result)

    def test_receipt_bytes_utf8_depth_duplicate_keys_and_types_are_bounded(self):
        def duplicate(request):
            raw = encoded(request)
            return raw[:-1] + b',"generation":1}'
        callbacks = [lambda r: b'x' * 4097, lambda r: b'\xff', lambda r: 'not bytes',
                     lambda r: bytearray(encoded(r)), lambda r: b'[]', lambda r: b'{',
                     lambda r: b'[[[[[0]]]]]', lambda r: b'{"x":NaN}', duplicate]
        for index, callback in enumerate(callbacks):
            # Different principals avoid a test-only submit-rate cap interaction.
            owner = 'owner%d' % index
            result = self.bridge(callback).run_job(owner, self.submit(str(index), owner))
            self.assertEqual(result['reason'], 'invalid_evidence')
            self.assertNotIn('summary', result)

    def test_unknown_private_fields_and_incorrect_single_game_counts_are_rejected(self):
        def top_extra(request):
            body = receipt(request)
            body['private_path'] = '/private/opponent'
            return json.dumps(body).encode()
        bad = [top_extra,
               lambda r: encoded(r, {'status': 'complete', 'summary': {'games': 1, 'wins': 1, 'draws': 0, 'losses': 0}, 'logs': 'private'}),
               lambda r: encoded(r, {'status': 'complete', 'summary': {'games': 1, 'wins': True, 'draws': 0, 'losses': 0}}),
               lambda r: encoded(r, {'status': 'complete', 'summary': {'games': 2, 'wins': 2, 'draws': 0, 'losses': 0}}),
               lambda r: encoded(r, {'status': 'complete', 'summary': {'games': 1, 'wins': 1, 'draws': 1, 'losses': 0}}),
               lambda r: encoded(r, {'status': 'accepted'})]
        for index, callback in enumerate(bad):
            result = self.bridge(callback).run_job('alice', self.submit(str(index)))
            self.assertEqual(result['reason'], 'invalid_evidence')
            self.assertNotIn('private', json.dumps(result))

    def test_closed_failure_and_compile_rejection_receipts_never_get_scores(self):
        outcomes = [{'status': 'rejected', 'reason': 'compile_rejected'},
                    {'status': 'infrastructure_failure', 'reason': 'resource_limit'},
                    {'status': 'infrastructure_failure', 'reason': '/private/logs'}]
        for index, outcome in enumerate(outcomes):
            result = self.bridge(lambda r, outcome=outcome: encoded(r, outcome)).run_job('alice', self.submit(str(index)))
            self.assertNotIn('summary', result)
            self.assertEqual(result['reason'], outcome['reason'] if index < 2 else 'invalid_evidence')

    def test_constructor_and_real_launch_remain_closed(self):
        for callback, worker in ((None, 'worker'), (self.callback, '../worker')):
            with self.assertRaisesRegex(ValueError, 'invalid_worker_configuration'):
                SyntheticWorkerBridge(self.ledger, callback, worker=worker)
        with self.assertRaises(TypeError):
            SyntheticWorkerBridge(self.ledger, self.callback, specification={'opponent': 'chosen'})
        with self.assertRaisesRegex(RuntimeError, 'real_launch_unsupported'):
            launch_real(worker=self.bridge(), validated=True)
        with self.assertRaises(BoundaryUnverified):
            require_heldout_attestation()


if __name__ == '__main__':
    unittest.main()
