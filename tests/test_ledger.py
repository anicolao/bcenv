"""Synthetic SQLite transactions/recovery only; no bots, games or credentials."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, asdict, replace
import base64
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.ledger import DurableLedger, LedgerError, LedgerLimits
from services.scrimmage import BoundaryUnverified, require_heldout_attestation
from services.controller import launch_real

SOURCE = b'package bot; public class RobotPlayer {}\n'
UNKNOWN = 's_' + 'f' * 32


def archive(content=SOURCE, compression=zipfile.ZIP_STORED):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=compression) as output:
        output.writestr('bot/RobotPlayer.java', content)
    return stream.getvalue()


def complete():
    return {'status': 'complete', 'validated_complete': True,
            'summary': {'games': 1, 'wins': 1, 'draws': 0, 'losses': 0}}


PROCESS_SUBMIT = '''
import base64,json,sys
from services.ledger import DurableLedger,LedgerError,LedgerLimits
ledger=DurableLedger(sys.argv[1],limits=LedgerLimits(**json.loads(sys.argv[4])),clock=lambda:1000)
try:
    result=ledger.submit('alice',sys.argv[2],base64.b64decode(sys.argv[3]),'bot')
    print(json.dumps(result))
except LedgerError as error:
    print(json.dumps({'error':error.code}))
'''


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'ledger.db'
        self.now = 1000
        self.limits = LedgerLimits()
        self.ledger = self.open()

    def open(self, **kwargs):
        return DurableLedger(self.path, limits=self.limits, clock=lambda: self.now, **kwargs)

    def configured(self, **changes):
        self.path = self.root / ('configured-%d.db' % len(list(self.root.glob('*.db'))))
        self.limits = replace(LedgerLimits(), **changes)
        self.ledger = self.open()
        return self.ledger

    def submit(self, owner='alice', key='key', data=None):
        return self.ledger.submit(owner, key, archive() if data is None else data, 'bot')['submission_id']

    def error(self, code, operation, *args, **kwargs):
        with self.assertRaises(LedgerError) as caught:
            operation(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def sql(self, query, args=()):
        with sqlite3.connect(self.path) as connection:
            return connection.execute(query, args).fetchall()

    def reserve(self, identifier, owner='alice', worker='worker'):
        return self.ledger.reserve_job(owner, identifier, worker)

    def finish(self, reservation, evidence=None, owner='alice'):
        return self.ledger.finish(owner, reservation.submission_id, reservation.worker,
                                  reservation.generation, complete() if evidence is None else evidence)

    def test_real_sqlite_restart_preserves_source_identity_and_public_result(self):
        identifier = self.submit()
        self.assertEqual(self.sql('SELECT state FROM jobs')[0][0], 'queued')
        self.ledger = self.open()
        self.assertEqual(self.ledger.result('alice', identifier)['status'], 'accepted')
        reservation = self.reserve(identifier)
        self.assertEqual(reservation.source.files[0].data, SOURCE)
        with self.assertRaises(FrozenInstanceError):
            reservation.source.files[0].data = b'changed'
        result = self.finish(reservation)
        self.ledger = self.open()
        self.assertEqual(self.ledger.result('alice', identifier), result)
        self.assertEqual(result['status'], 'complete')

    def test_idempotency_uses_complete_normalized_source_not_zip_metadata(self):
        identifier = self.submit()
        receipt = self.ledger.submit('alice', 'key', archive(compression=zipfile.ZIP_DEFLATED), 'bot')
        self.assertEqual(receipt['submission_id'], identifier)
        self.error('idempotency_conflict', self.ledger.submit, 'alice', 'key', archive(SOURCE + b' '), 'bot')
        self.error('idempotency_conflict', self.ledger.submit, 'alice', 'key', archive(), 'other')
        self.assertEqual(self.sql('SELECT count(*) FROM jobs')[0][0], 1)
        result = self.finish(self.reserve(identifier))
        self.assertEqual(self.ledger.submit('alice', 'key', archive(), 'bot'), result)

    def test_idempotency_keys_are_owner_scoped_and_source_blob_deduplicated(self):
        first = self.submit('alice')
        second = self.submit('bob')
        self.assertNotEqual(first, second)
        self.assertEqual(self.sql('SELECT count(*) FROM sources')[0][0], 1)
        self.assertEqual(self.sql('SELECT count(*) FROM source_files')[0][0], 1)
        self.assertEqual(self.sql('SELECT count(*) FROM jobs')[0][0], 2)

    def test_rejected_archive_is_durable_and_idempotent_without_source_blob(self):
        identifier = self.submit(data=b'not a zip')
        self.ledger = self.open()
        result = self.ledger.result('alice', identifier)
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(self.ledger.submit('alice', 'key', b'not a zip', 'bot'), result)
        self.error('idempotency_conflict', self.ledger.submit, 'alice', 'key', b'different invalid zip', 'bot')
        self.assertEqual(self.sql('SELECT count(*) FROM sources')[0][0], 0)
        self.error('job_not_queued', self.reserve, identifier)

    def test_cross_owner_guesses_and_missing_ids_have_same_closed_errors(self):
        identifier = self.submit('alice')
        reservation = self.reserve(identifier)
        for guessed in (identifier, UNKNOWN, '../private', None):
            self.error('not_found', self.ledger.result, 'bob', guessed)
            self.error('not_found', self.ledger.replay, 'bob', guessed)
            self.error('not_found', self.ledger.reserve_job, 'bob', guessed, 'worker')
            self.error('not_found', self.ledger.finish, 'bob', guessed, 'worker', reservation.generation, complete())
        self.assertEqual(self.ledger.result('alice', identifier)['status'], 'accepted')

    def test_source_updates_and_deletes_are_blocked_by_database_invariants(self):
        identifier = self.submit()
        with sqlite3.connect(self.path) as connection:
            for query in ('UPDATE source_files SET data=data', 'DELETE FROM source_files',
                          "UPDATE sources SET package='changed'", 'DELETE FROM sources'):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(query)
        self.assertEqual(self.reserve(identifier).source.files[0].data, SOURCE)

    def test_source_integrity_is_rechecked_before_reservation(self):
        identifier = self.submit()
        # Trusted test corrupts its own DB to simulate storage damage. Database
        # ownership can bypass triggers, so triggers are not an isolation claim.
        with sqlite3.connect(self.path) as connection:
            connection.execute('DROP TRIGGER immutable_source_files_update')
            connection.execute('UPDATE source_files SET data=?', (b'x' * len(SOURCE),))
        self.error('storage_integrity', self.reserve, identifier)
        self.assertEqual(self.sql('SELECT state FROM jobs')[0][0], 'queued')

    def test_terminal_report_is_immutable_with_idempotent_exact_retries(self):
        reservation = self.reserve(self.submit())
        result = self.finish(reservation)
        self.assertEqual(self.finish(reservation), result)
        changed = complete()
        changed['summary'].update(wins=0, losses=1)
        self.error('terminal_conflict', self.finish, reservation, changed)
        self.assertEqual(self.ledger.result('alice', reservation.submission_id), result)

    def test_trusted_worker_and_monotonic_generation_fence_reports(self):
        first = self.reserve(self.submit(key='first'))
        self.ledger = self.open()
        second = self.reserve(self.submit(key='second'))
        self.assertGreater(second.generation, first.generation)
        self.error('stale_reservation', self.ledger.finish, 'alice', first.submission_id, 'other-worker', first.generation, complete())
        self.error('stale_reservation', self.ledger.finish, 'alice', first.submission_id, 'worker', second.generation, complete())
        self.error('stale_reservation', self.ledger.finish, 'alice', second.submission_id, 'worker', first.generation, complete())
        self.assertEqual(self.finish(first)['status'], 'complete')

    def test_queued_or_unreserved_job_cannot_report_a_score(self):
        identifier = self.submit()
        self.error('stale_reservation', self.ledger.finish, 'alice', identifier, 'worker', 1, complete())
        reservation = self.reserve(identifier)
        self.error('invalid_terminal_result', self.finish, reservation, {'status': 'accepted'})
        self.assertEqual(self.ledger.result('alice', identifier)['status'], 'accepted')

    def test_invalid_or_failed_evidence_never_becomes_a_score(self):
        for index, evidence in enumerate(({}, {'status': 'succeeded', 'exit_code': 0},
                {'status': 'complete'}, {'status': 'infrastructure_failure', 'reason': '/private/log'})):
            result = self.finish(self.reserve(self.submit(key='key%d' % index)), evidence)
            self.assertEqual(result['status'], 'infrastructure_failure')
            self.assertNotIn('summary', result)
            self.assertNotIn('/private', json.dumps(result))

    def test_internal_fields_do_not_leak_before_or_after_restart(self):
        evidence = complete()
        evidence.update(opponent='/private/heldout', source='private', schedule=['private'], logs='private')
        evidence['summary']['internal_path'] = '/private/worker'
        reservation = self.reserve(self.submit())
        result = self.finish(reservation, evidence)
        self.ledger = self.open()
        self.assertEqual(self.ledger.result('alice', reservation.submission_id), result)
        self.assertNotIn('private', json.dumps(result))
        self.assertEqual(set(result), {'schema', 'submission_id', 'status', 'summary'})

    def test_expired_lease_recovers_terminally_without_retry_or_score(self):
        identifier = self.submit()
        reservation = self.reserve(identifier)
        self.ledger = self.open()
        self.now = reservation.lease_until - 1
        self.assertEqual(self.ledger.result('alice', identifier)['status'], 'accepted')
        self.error('job_not_queued', self.reserve, identifier)
        self.now = reservation.lease_until
        self.assertEqual(self.ledger.recover_expired(), 1)
        self.assertEqual(self.ledger.recover_expired(), 0)
        result = self.ledger.result('alice', identifier)
        self.assertEqual((result['status'], result['reason']), ('infrastructure_failure', 'worker_unavailable'))
        self.assertNotIn('summary', result)
        self.error('terminal_conflict', self.finish, reservation)
        self.error('job_not_queued', self.reserve, identifier)

    def test_result_poll_also_recovers_expired_job_after_restart(self):
        reservation = self.reserve(self.submit())
        self.now = reservation.lease_until + 1
        self.ledger = self.open()
        self.assertEqual(self.ledger.result('alice', reservation.submission_id)['status'], 'infrastructure_failure')

    def test_clock_regression_refuses_work_fails_running_claims_and_preserves_quota(self):
        self.configured(owner_submits_per_window=2)
        reservation = self.reserve(self.submit(key='one'))
        self.submit(key='two')
        self.now = 900
        self.ledger = self.open()
        self.error('clock_regression', self.finish, reservation)
        self.assertEqual(self.sql("SELECT value FROM metadata WHERE key='clock'")[0][0], '1000')
        self.assertEqual(self.sql('SELECT state FROM jobs WHERE id=?', (reservation.submission_id,))[0][0], 'infrastructure_failure')
        self.ledger = self.open()  # Regression recovery committed before refusal.
        self.error('clock_regression', self.submit, key='three')
        self.now = 1000
        self.error('rate_limit', self.submit, key='three')
        self.error('terminal_conflict', self.finish, reservation)
        self.assertNotIn('summary', self.ledger.result('alice', reservation.submission_id))

    def test_subsecond_clock_regression_also_commits_terminal_failure(self):
        self.now = 1000.9
        reservation = self.reserve(self.submit())
        self.now = 1000.1
        self.error('clock_regression', self.finish, reservation)
        self.ledger = self.open()
        self.assertEqual(self.sql("SELECT value FROM metadata WHERE key='clock'")[0][0], '1000.9')
        self.assertEqual(self.sql('SELECT state FROM jobs')[0][0], 'infrastructure_failure')
        self.now = 1000.9
        self.assertNotIn('summary', self.ledger.result('alice', reservation.submission_id))

    def test_invalid_clock_never_changes_job_state(self):
        identifier = self.submit()
        for value in (float('nan'), float('inf'), -1, True, '1000', 2**1024, -(2**1024)):
            self.now = value
            self.error('invalid_clock', self.ledger.result, 'alice', identifier)
        self.now = 1000
        self.assertEqual(self.ledger.result('alice', identifier)['status'], 'accepted')

    def test_large_integer_clock_identity_does_not_round_into_false_regression(self):
        self.now = 2**53 + 3
        identifier = self.submit()
        self.ledger = self.open()
        self.assertEqual(self.ledger.result('alice', identifier)['status'], 'accepted')

    def test_large_integer_clock_regression_cannot_round_back_to_equality(self):
        self.now = 2**53 + 1
        reservation = self.reserve(self.submit())
        self.now = 2**53
        self.error('clock_regression', self.finish, reservation)
        self.ledger = self.open()
        self.assertEqual(self.sql('SELECT state FROM jobs')[0][0], 'infrastructure_failure')
        self.assertEqual(self.sql("SELECT value FROM metadata WHERE key='clock'")[0][0], str(2**53 + 1))

    def test_submit_and_read_rate_limits_survive_restart_and_count_retries_or_guesses(self):
        self.configured(owner_submits_per_window=2, owner_reads_per_window=2)
        identifier = self.submit()
        self.submit()  # Same idempotency key still consumes request-rate budget.
        self.ledger = self.open()
        self.error('rate_limit', self.submit)
        self.error('not_found', self.ledger.result, 'alice', UNKNOWN)
        self.ledger.replay('alice', identifier)
        self.ledger = self.open()
        self.error('rate_limit', self.ledger.result, 'alice', identifier)
        self.now += self.limits.read_window_seconds
        self.assertEqual(self.ledger.result('alice', identifier)['status'], 'accepted')
        self.now = 1000 + self.limits.submit_window_seconds
        self.assertEqual(self.submit(), identifier)

    def test_rejected_attempts_and_quota_denials_are_charged(self):
        self.configured(owner_submits_per_window=3, owner_pending=1)
        self.submit(data=b'invalid')
        self.submit(key='valid')
        self.error('pending_limit', self.submit, key='denied')
        self.ledger = self.open()
        self.error('rate_limit', self.submit, key='denied-again')
        self.assertEqual(self.sql('SELECT count(*) FROM rate_events WHERE kind="submit"')[0][0], 3)

    def test_record_retention_caps_include_terminal_and_rejected_jobs(self):
        self.configured(owner_records=2, global_records=3)
        self.submit(data=b'invalid')
        self.finish(self.reserve(self.submit(key='second')))
        self.error('record_limit', self.submit, key='third')
        self.submit('bob')
        self.error('record_limit', self.submit, 'carol')

    def test_source_caps_are_durable_and_logically_charged_even_when_deduplicated(self):
        self.configured(owner_source_bytes=len(SOURCE), global_source_bytes=2 * len(SOURCE))
        identifier = self.submit()
        self.assertEqual(self.submit(), identifier)
        self.error('source_limit', self.submit, key='duplicate-new-job')
        self.submit('bob')  # Physically deduplicated but charged in full.
        for data in (archive(), archive(SOURCE.replace(b'public', b'final '))):
            self.error('source_limit', self.submit, 'carol', data=data)
        self.ledger = self.open()
        self.assertEqual(self.sql('SELECT sum(source_bytes) FROM sources')[0][0], len(SOURCE))
        self.assertEqual(self.sql('SELECT sum(source_bytes) FROM jobs')[0][0], 2 * len(SOURCE))

    def test_cross_owner_deduplication_never_discounts_quota_at_global_cap(self):
        self.configured(global_source_bytes=len(SOURCE))
        self.submit('alice')
        for owner in ('bob', 'carol'):
            for data in (archive(), archive(SOURCE.replace(b'public', b'final '))):
                self.error('source_limit', self.submit, owner, data=data)
        self.assertEqual(self.sql('SELECT count(*) FROM jobs')[0][0], 1)

    def test_pending_and_running_limits_are_distinct(self):
        self.configured(owner_pending=2, global_pending=3, owner_running=1, global_running=2)
        first = self.reserve(self.submit(key='first'))
        second_id = self.submit(key='second')
        self.error('pending_limit', self.submit, key='third')
        self.error('active_limit', self.reserve, second_id)
        bob = self.reserve(self.submit('bob'), owner='bob')
        self.error('pending_limit', self.submit, 'carol')
        self.finish(first)
        second = self.reserve(second_id)
        carol = self.submit('carol')
        self.error('active_limit', self.reserve, carol, owner='carol')
        self.assertEqual(second.source.files[0].data, SOURCE)
        self.assertEqual(bob.source.files[0].data, SOURCE)

    def test_concurrent_threads_can_reserve_a_job_only_once(self):
        identifier = self.submit()
        def reserve(index):
            try:
                return self.ledger.reserve_job('alice', identifier, 'worker%d' % index)
            except LedgerError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(reserve, range(12)))
        self.assertEqual(sum(not isinstance(item, str) for item in results), 1)
        self.assertEqual(results.count('job_not_queued'), 11)
        self.assertEqual(self.sql("SELECT value FROM metadata WHERE key='generation'")[0][0], '1')

    def process_submissions(self, keys):
        arguments = [str(self.path), None, base64.b64encode(archive()).decode(), json.dumps(asdict(self.limits))]
        processes = []
        for key in keys:
            args = list(arguments)
            args[1] = key
            processes.append(subprocess.Popen([sys.executable, '-c', PROCESS_SUBMIT, *args], cwd=ROOT,
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        outputs = []
        for process in processes:
            output, error = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, error)
            outputs.append(json.loads(output))
        return outputs

    def test_cross_process_idempotency_is_atomic(self):
        outputs = self.process_submissions(['same'] * 8)
        self.assertEqual(len({item['submission_id'] for item in outputs}), 1)
        self.assertEqual(self.sql('SELECT count(*) FROM jobs')[0][0], 1)
        self.assertEqual(self.sql('SELECT count(*) FROM source_files')[0][0], 1)

    def test_concurrent_first_open_serializes_database_identity_and_schema(self):
        self.path = self.root / 'cold-open.db'
        outputs = self.process_submissions(['same'] * 8)
        self.assertTrue(all(item.get('status') == 'accepted' for item in outputs), outputs)
        self.assertEqual(len({item['submission_id'] for item in outputs}), 1)
        self.assertEqual(self.sql('SELECT count(*) FROM jobs')[0][0], 1)

    def test_cross_process_pending_quota_is_atomic(self):
        self.configured(owner_pending=3)
        outputs = self.process_submissions(['key%d' % n for n in range(8)])
        self.assertEqual(sum(item.get('status') == 'accepted' for item in outputs), 3)
        self.assertEqual(sum(item.get('error') == 'pending_limit' for item in outputs), 5)
        self.assertEqual(self.sql('SELECT count(*) FROM jobs')[0][0], 3)

    def test_abrupt_worker_process_exit_preserves_claim_then_recovers_failure(self):
        code = '''
import base64,json,os,sys
from services.ledger import DurableLedger
ledger=DurableLedger(sys.argv[1],clock=lambda:1000)
r=ledger.submit('alice','crash',base64.b64decode(sys.argv[2]),'bot')
c=ledger.reserve_job('alice',r['submission_id'],'crashed-worker')
print(json.dumps({'id':c.submission_id,'generation':c.generation,'until':c.lease_until}),flush=True)
os._exit(9)
'''
        process = subprocess.run([sys.executable, '-c', code, str(self.path), base64.b64encode(archive()).decode()],
                                 cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 9, process.stderr)
        claim = json.loads(process.stdout)
        self.ledger = self.open()
        self.assertEqual(self.ledger.result('alice', claim['id'])['status'], 'accepted')
        self.now = claim['until']
        self.assertEqual(self.ledger.recover_expired(), 1)
        self.error('terminal_conflict', self.ledger.finish, 'alice', claim['id'], 'crashed-worker', claim['generation'], complete())
        self.assertNotIn('summary', self.ledger.result('alice', claim['id']))

    def test_recovery_fences_a_live_process_but_does_not_terminate_it(self):
        code = '''
import base64,json,sys
from services.ledger import DurableLedger,LedgerError
clock=[1000]
ledger=DurableLedger(sys.argv[1],clock=lambda:clock[0])
r=ledger.submit('alice','live',base64.b64decode(sys.argv[2]),'bot')
c=ledger.reserve_job('alice',r['submission_id'],'live-worker')
print(json.dumps({'id':c.submission_id,'until':c.lease_until}),flush=True)
clock[0]=int(sys.stdin.readline())
try:
    ledger.finish('alice',c.submission_id,c.worker,c.generation,
        {'status':'complete','validated_complete':True,
         'summary':{'games':1,'wins':1,'draws':0,'losses':0}})
except LedgerError as error:
    print(json.dumps({'still_alive':True,'error':error.code}),flush=True)
'''
        process = subprocess.Popen([sys.executable, '-c', code, str(self.path), base64.b64encode(archive()).decode()],
                                   cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        try:
            claim = json.loads(process.stdout.readline())
            self.now = claim['until']
            self.assertEqual(self.ledger.recover_expired(), 1)
            self.assertIsNone(process.poll())  # DB fencing does not kill a worker.
            output, error = process.communicate(str(self.now) + '\n', timeout=20)
            self.assertEqual(process.returncode, 0, error)
            self.assertEqual(json.loads(output), {'still_alive': True, 'error': 'terminal_conflict'})
            self.assertNotIn('summary', self.ledger.result('alice', claim['id']))
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=20)

    def test_crash_before_transaction_commit_rolls_back_job_reservation(self):
        identifier = self.submit()
        code = '''
import os,sqlite3,sys
connection=sqlite3.connect(sys.argv[1],isolation_level=None)
connection.execute('BEGIN IMMEDIATE')
connection.execute("UPDATE jobs SET state='running',generation=99,worker='uncommitted',lease_until=1030 WHERE id=?",(sys.argv[2],))
os._exit(9)
'''
        process = subprocess.run([sys.executable, '-c', code, str(self.path), identifier], cwd=ROOT, timeout=20)
        self.assertEqual(process.returncode, 9)
        self.ledger = self.open()
        self.assertEqual(self.sql('SELECT state,generation FROM jobs')[0], ('queued', 0))
        self.assertEqual(self.reserve(identifier).generation, 1)

    def test_configuration_schema_and_foreign_database_fail_closed(self):
        self.submit()
        self.error('configuration_mismatch', DurableLedger, self.path, limits=replace(self.limits, owner_records=99))
        with sqlite3.connect(self.path) as connection:
            connection.execute('PRAGMA user_version=99')
        self.error('incompatible_database', self.open)
        foreign = self.root / 'foreign.db'
        with sqlite3.connect(foreign) as connection:
            connection.execute('CREATE TABLE unrelated (data TEXT)')
        self.error('incompatible_database', DurableLedger, foreign)

    def test_fixed_safe_errors_request_shapes_and_no_real_launch(self):
        self.error('invalid_context', self.ledger.submit, '../owner', 'key', archive(), 'bot')
        self.error('invalid_request', self.ledger.submit, 'alice', 'bad key', archive(), 'bot')
        self.error('invalid_request', self.ledger.submit, 'alice', 'key', bytearray(archive()), 'bot')
        self.error('request_limit', self.ledger.submit, 'alice', 'key', b'x' * 1048577, 'bot')
        self.error('storage_unavailable', DurableLedger, self.root / 'missing/private.db')
        self.error('invalid_request', DurableLedger, ':memory:')
        with self.assertRaises(BoundaryUnverified):
            require_heldout_attestation()
        with self.assertRaisesRegex(RuntimeError, 'real_launch_unsupported'):
            launch_real(boundary_validated=True)
        for value in (True, 0, -1, 1.5):
            with self.assertRaises(ValueError):
                LedgerLimits(owner_records=value)


if __name__ == '__main__':
    unittest.main()
