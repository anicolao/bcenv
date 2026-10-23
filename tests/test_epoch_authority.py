"""Stage-one authority contract tests: SQLite and in-process fakes only.

These tests do not establish LocalTrial/profile enforcement, process cleanup,
canonical-author recovery, or execution isolation.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.epoch_authority import (
    AuthorityDenied, AuthorityUnavailable, ClockSample, CommitUnknown,
    EpochAuthority, LIMITS, MAX_JSON, MAX_SOURCE,
)


def digest(value):
    return hashlib.sha256(value).hexdigest()


class FakeClock:
    def __init__(self):
        self.domain = 'synthetic-boot-domain'
        self.wall = 10000.0
        self.mono = 500.0

    def __call__(self):
        return ClockSample(self.domain, self.wall, self.mono)

    def advance(self, seconds):
        self.wall += seconds
        self.mono += seconds


class EpochAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'authority.sqlite'
        self.clock = FakeClock()
        # A regression must never turn these mock/SQLite tests into real work.
        guard = mock.patch.object(subprocess, 'Popen', side_effect=AssertionError('no child processes'))
        guard.start()
        self.addCleanup(guard.stop)
        self.authority = EpochAuthority.create(
            self.db, profile='resumable-authority-v1', config={'fixture': 'unit-only'},
            author='same-canonical-author', paths=self.paths('epoch0'), clock=self.clock,
        )
        self.epoch = self.authority.snapshot()['run']['epoch']

    def paths(self, suffix):
        return {key: str(self.root / suffix / key)
                for key in ('service', 'artifacts', 'mailbox', 'control', 'author')}

    def fresh_connection(self):
        return EpochAuthority(self.db, authority_id=self.authority.authority_id, config_digest=self.authority.config_digest, clock=self.clock)

    def admit(self, request='request1', *, epoch=None, kind='submit', payload=None,
              source=b'normalized source fixture', fingerprint=None, debits=None, effect=None):
        if kind != 'submit' and effect is None:
            effect = {'query': 'fixed-own-team-query', 'page': 0}
        return self.authority.admit(
            self.epoch if epoch is None else epoch, request, kind,
            {} if payload is None else payload, source,
            digest(source) if fingerprint is None else fingerprint,
            {'versions': 1} if debits is None else debits, effect=effect,
        )

    def advance(self):
        self.authority.advance(self.epoch, 'advance1', self.paths('epoch1'))
        return self.authority.snapshot()['run']['epoch']

    def reopen(self, epoch):
        return self.authority.reopen(
            epoch, 'reopen1', 'same-canonical-author', digest(b'independently verified assertion'))

    def test_admission_has_immutable_receipt_and_source_binding(self):
        accepted = self.admit()
        self.assertEqual(accepted['job_id'], 'request1')
        self.assertEqual(accepted['status'], 'QUEUED')
        self.assertEqual(self.authority.receipt('request1'), accepted)
        before = self.authority.snapshot()
        with self.assertRaises(AuthorityDenied):
            self.admit()
        self.assertEqual(self.authority.snapshot(), before)
        with self.assertRaises(AuthorityDenied):
            self.admit(source=b'changed source')
        self.assertEqual(self.authority.snapshot(), before)
        self.assertEqual(self.authority.receipt('request1'), accepted)

    def test_successful_terminal_is_immutable(self):
        self.admit()
        self.authority.start(self.epoch, 'start1', 'request1')
        result = self.authority.finalize(self.epoch, 'finish1', 'request1', {'status': 'ok', 'hash': digest(b'result')})
        self.assertEqual(result['status'], 'COMPLETE')
        before = self.authority.snapshot()
        with self.assertRaises(AuthorityDenied):
            self.authority.finalize(self.epoch, 'finish2', 'request1', {'status': 'different'})
        self.assertEqual(self.authority.snapshot(), before)
        self.assertEqual(self.authority.receipt('finish1'), result)

    def test_transition_id_cannot_be_reused_for_different_operation(self):
        self.admit()
        before = self.authority.snapshot()
        with self.assertRaises(AuthorityDenied):
            self.authority.start(self.epoch, 'request1', 'request1')
        self.assertEqual(self.authority.snapshot(), before)

    def test_unknown_includes_queued_and_running_without_refunds(self):
        first = self.admit('queued')
        self.admit('running', source=b'other source')
        self.authority.start(self.epoch, 'start1', 'running')
        before = self.authority.snapshot()
        next_epoch = self.advance()
        after = self.authority.snapshot()
        self.assertEqual(next_epoch, self.epoch + 1)
        self.assertEqual(after['run']['state'], 'RECOVERING')
        self.assertEqual(after['counters'], before['counters'])
        self.assertTrue(all(job['state'] == 'UNKNOWN' for job in after['jobs']))
        self.assertEqual(self.authority.receipt('queued'), first)
        with self.assertRaises(AuthorityDenied):
            self.authority.start(self.epoch, 'late-start', 'queued')
        with self.assertRaises(AuthorityDenied):
            self.authority.finalize(self.epoch, 'late-finish', 'running', {'status': 'ok'})
        self.assertEqual(self.authority.snapshot(), after)

    def test_committed_terminal_survives_advance_as_history_only(self):
        self.admit('complete')
        self.authority.start(self.epoch, 'start1', 'complete')
        receipt = self.authority.finalize(self.epoch, 'finish1', 'complete', {'result_hash': digest(b'result')})
        self.admit('queued', source=b'another source')
        self.advance()
        states = sorted(job['state'] for job in self.authority.snapshot()['jobs'])
        self.assertEqual(states, ['COMPLETE', 'UNKNOWN'])
        self.assertEqual(self.authority.receipt('finish1'), receipt)

    def test_deadline_rechecked_before_late_terminal_commit(self):
        self.admit()
        self.authority.start(self.epoch, 'start1', 'request1')
        before = self.authority.snapshot()
        self.clock.advance(21600)
        with self.assertRaises(AuthorityDenied):
            self.authority.finalize(self.epoch, 'late-finish', 'request1', {'result_hash': digest(b'late')})
        self.assertEqual(self.authority.snapshot(), before)
        self.assertIsNone(self.authority.receipt('late-finish'))

    def test_unknown_submit_new_request_and_payload_cannot_revive(self):
        self.admit('first', payload={'note_id': 'old'})
        next_epoch = self.advance()
        self.reopen(next_epoch)
        before = self.authority.snapshot()
        with self.assertRaises(AuthorityDenied):
            self.admit('new-id', epoch=next_epoch, payload={'note_id': 'new', 'delivery_id': 'different'})
        self.assertEqual(self.authority.snapshot(), before)
        accepted = self.admit('different-source', epoch=next_epoch, source=b'genuinely changed bytes')
        self.assertEqual(accepted['status'], 'QUEUED')

    def test_normalized_submission_fingerprint_is_distinct_from_blob_digest(self):
        normalized = digest(b'trusted canonical source-plus-package identity')
        first = self.admit('first', source=b'captured envelope A', fingerprint=normalized)
        self.assertEqual(first['source_digest'], digest(b'captured envelope A'))
        self.assertNotEqual(first['source_digest'], normalized)
        next_epoch = self.advance()
        self.reopen(next_epoch)
        with self.assertRaises(AuthorityDenied) as denied:
            self.admit('new-envelope', epoch=next_epoch, source=b'captured envelope B', fingerprint=normalized)
        self.assertEqual(denied.exception.code, 'unknown_fingerprint')
        # Derivation/authentication of normalized is the future policy adapter's duty.

    def test_outcome_identifier_cannot_be_reassigned_to_another_job(self):
        self.admit('first')
        self.authority.start(self.epoch, 'start-first', 'first')
        outcome = self.authority.finalize(self.epoch, 'immutable-outcome', 'first', {'result': 'first'})
        self.admit('second', source=b'different source')
        self.authority.start(self.epoch, 'start-second', 'second')
        before = self.authority.snapshot()
        with self.assertRaises(AuthorityDenied):
            self.authority.finalize(self.epoch, 'immutable-outcome', 'second', {'result': 'second'})
        self.assertEqual(self.authority.snapshot(), before)
        self.assertEqual(self.authority.receipt('immutable-outcome'), outcome)

    def test_unknown_replay_new_delivery_id_cannot_revive_same_effect(self):
        self.admit('first', kind='replay', payload={'delivery': 'old'}, debits={'replays': 1, 'reads': 1})
        next_epoch = self.advance()
        self.reopen(next_epoch)
        before = self.authority.snapshot()
        with self.assertRaises(AuthorityDenied):
            self.admit('new-id', epoch=next_epoch, kind='replay', payload={'delivery': 'new'},
                       debits={'replays': 1, 'reads': 1})
        self.assertEqual(self.authority.snapshot(), before)

    def test_admission_closed_until_same_author_assertion(self):
        next_epoch = self.advance()
        with self.assertRaises(AuthorityDenied):
            self.admit(epoch=next_epoch)
        before = self.authority.snapshot()
        with self.assertRaises(AuthorityDenied):
            self.authority.reopen(next_epoch, 'wrong-author', 'replacement-author', digest(b'proof'))
        self.assertEqual(self.authority.snapshot(), before)
        self.reopen(next_epoch)
        self.assertEqual(self.authority.snapshot()['run']['state'], 'ACTIVE')

    def test_same_epoch_advance_cas_has_exactly_one_winner(self):
        peers = [self.fresh_connection(), self.fresh_connection()]
        barrier = threading.Barrier(2)
        results = []
        lock = threading.Lock()

        def advance_one(index):
            barrier.wait(timeout=5)
            try:
                peers[index].advance(self.epoch, f'advance-{index}', self.paths(f'new-{index}'))
                value = 'accepted'
            except AuthorityDenied:
                value = 'denied'
            with lock:
                results.append(value)

        threads = [threading.Thread(target=advance_one, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(results, ['accepted', 'denied'])
        self.assertEqual(self.authority.snapshot()['run']['epoch'], self.epoch + 1)

    def test_stale_epoch_never_changes_new_authority(self):
        self.admit()
        next_epoch = self.advance()
        self.reopen(next_epoch)
        before = self.authority.snapshot()
        calls = [
            lambda: self.admit('old-admit'),
            lambda: self.authority.start(self.epoch, 'old-start', 'request1'),
            lambda: self.authority.finalize(self.epoch, 'old-final', 'request1', {'error': 'stale'}),
            lambda: self.authority.advance(self.epoch, 'old-advance', self.paths('bad')),
            lambda: self.authority.reopen(self.epoch, 'old-open', 'same-canonical-author', digest(b'proof')),
        ]
        for call in calls:
            with self.assertRaises(AuthorityDenied):
                call()
            self.assertEqual(self.authority.snapshot(), before)

    def test_unknown_does_not_extend_original_deadline(self):
        original = self.authority.snapshot()['run']
        self.clock.advance(20)
        next_epoch = self.advance()
        self.clock.advance(20)
        self.reopen(next_epoch)
        current = self.authority.snapshot()['run']
        for key in original:
            if 'deadline' in key or 'started' in key or 'domain' in key:
                self.assertEqual(current[key], original[key], key)
        self.clock.advance(21600)
        with self.assertRaises(AuthorityDenied):
            self.admit(epoch=next_epoch)

    def test_clock_domain_and_regression_reject_without_writes(self):
        for change in ('domain', 'wall', 'mono'):
            with self.subTest(change=change):
                old = getattr(self.clock, change)
                before = self.authority.snapshot()
                setattr(self.clock, change, 'other-domain' if change == 'domain' else old - 1)
                with self.assertRaises(AuthorityDenied):
                    self.admit('bad-' + change)
                setattr(self.clock, change, old)
                self.assertEqual(self.authority.snapshot(), before)

    def test_bad_epoch_types_and_negative_values_rejected(self):
        before = self.authority.snapshot()
        for epoch in (True, False, -1, 1.0, '1', None):
            with self.subTest(epoch=epoch), self.assertRaises(AuthorityDenied):
                self.admit(epoch=epoch if epoch is not None else 'None')
        self.assertEqual(self.authority.snapshot(), before)

    def test_paths_cannot_reuse_prior_epoch_or_overlap(self):
        before = self.authority.snapshot()
        for paths in (self.paths('epoch0'), {**self.paths('new'), 'control': str(self.root / 'new')}):
            with self.assertRaises(AuthorityDenied):
                self.authority.advance(self.epoch, 'bad-path', paths)
            self.assertEqual(self.authority.snapshot(), before)

    def test_config_identity_is_not_silently_reinitialized(self):
        with self.assertRaises((AuthorityDenied, AuthorityUnavailable)):
            EpochAuthority(self.db, authority_id=self.authority.authority_id, config_digest=digest(b'wrong configuration'), clock=self.clock)
        before = self.authority.snapshot()
        with self.assertRaises((AuthorityDenied, AuthorityUnavailable)):
            EpochAuthority.create(self.db, profile='resumable-authority-v1', config={},
                                  author='new-author', paths=self.paths('fresh'), clock=self.clock)
        self.assertEqual(self.authority.snapshot(), before)

    def test_blocked_external_work_does_not_hold_authority_transaction(self):
        self.admit()
        self.authority.start(self.epoch, 'start1', 'request1')
        entered = threading.Event()
        release = threading.Event()
        result = []

        def external_then_finalize():
            entered.set()
            if not release.wait(timeout=10):
                result.append('test-timeout')
                return
            try:
                self.authority.finalize(self.epoch, 'late', 'request1', {'status': 'ok'})
                result.append('accepted')
            except AuthorityDenied:
                result.append('fenced')

        thread = threading.Thread(target=external_then_finalize)
        thread.start()
        try:
            self.assertTrue(entered.wait(timeout=5))
            peer = self.fresh_connection()
            peer.advance(self.epoch, 'advance1', self.paths('epoch1'))
        finally:
            release.set()
            thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, ['fenced'])
        self.assertIsNone(self.authority.receipt('late'))


    def test_all_fixed_counters_accept_ceiling_then_reject_without_partial_debit(self):
        self.admit('at-ceilings', debits=dict(LIMITS))
        expected = self.authority.snapshot()
        self.assertEqual(expected['counters'], LIMITS)
        for counter in LIMITS:
            with self.subTest(counter=counter), self.assertRaises(AuthorityDenied):
                self.admit('over-' + counter, source=b'new bytes', debits={counter: 1})
            self.assertEqual(self.authority.snapshot(), expected)
        epoch = self.advance()
        self.reopen(epoch)
        self.assertEqual(self.authority.snapshot()['counters'], LIMITS)

    def test_mixed_quota_denial_is_all_or_none(self):
        before = self.authority.snapshot()
        with self.assertRaises(AuthorityDenied):
            self.admit(debits={'versions': 1, 'replays': LIMITS['replays'] + 1})
        self.assertEqual(self.authority.snapshot(), before)
        self.assertIsNone(self.authority.receipt('request1'))

    def test_charge_policy_is_explicitly_trusted_not_inferred_from_kind(self):
        # Deliberately establishes this internal seam's limit, not profile enforcement.
        self.admit(debits={})
        self.assertEqual(self.authority.snapshot()['counters'], {name: 0 for name in LIMITS})

    def test_invalid_debits_sources_and_payloads_do_not_mutate(self):
        before = self.authority.snapshot()
        calls = [
            lambda: self.admit(debits={'versions': True}),
            lambda: self.admit(debits={'versions': -1}),
            lambda: self.admit(debits={'versions': 1.0}),
            lambda: self.admit(debits={'unknown': 1}),
            lambda: self.admit(source=b''),
            lambda: self.admit(source=b'x' * (MAX_SOURCE + 1)),
            lambda: self.admit(payload={'bad': float('nan')}),
            lambda: self.admit(payload={'bad': object()}),
            lambda: self.admit(payload={'oversize': 'x' * MAX_JSON}),
            lambda: self.admit(fingerprint='not-a-hash'),
        ]
        for call in calls:
            with self.assertRaises(AuthorityDenied):
                call()
            self.assertEqual(self.authority.snapshot(), before)

    def test_after_lock_expiry_rejects_before_any_admission(self):
        before = self.authority.snapshot()
        expired = ClockSample(self.clock.domain, self.clock.wall + 21600, self.clock.mono + 21600)
        with mock.patch.object(self.authority, 'clock', return_value=expired):
            with self.assertRaises(AuthorityDenied):
                self.admit()
        self.assertEqual(self.authority.snapshot(), before)

    def test_clock_is_rechecked_after_waiting_for_sql_write_lock(self):
        holder = sqlite3.connect(self.db, isolation_level=None)
        holder.execute('BEGIN IMMEDIATE')
        attempted = threading.Event()
        original_connect = self.authority._connect
        observed = []

        class ConnectionProxy:
            def __init__(self, db):
                self.db = db

            def execute(self, sql, *args):
                if sql == 'BEGIN IMMEDIATE':
                    attempted.set()
                return self.db.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self.db, name)

        def connect(*args, **kwargs):
            return ConnectionProxy(original_connect(*args, **kwargs))

        def admit_after_wait():
            try:
                self.admit()
                observed.append('accepted')
            except AuthorityDenied as error:
                observed.append(error.code)
            except AuthorityUnavailable as error:
                observed.append(error.code)

        thread = threading.Thread(target=admit_after_wait)
        with mock.patch.object(self.authority, '_connect', side_effect=connect):
            thread.start()
            try:
                self.assertTrue(attempted.wait(timeout=5))
                self.clock.advance(21600)
                holder.commit()
            finally:
                holder.close()
                thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(observed, ['deadline_expired'])
        self.assertIsNone(self.authority.receipt('request1'))
        self.assertEqual(self.authority.snapshot()['counters']['versions'], 0)

    def test_second_clock_check_rolls_back_prepared_payload_and_debits(self):
        before = self.authority.snapshot()
        expired = ClockSample(self.clock.domain, self.clock.wall + 21600, self.clock.mono + 21600)
        with mock.patch.object(self.authority, 'clock', side_effect=[self.clock(), expired]):
            with self.assertRaises(AuthorityDenied):
                self.admit()
        self.assertEqual(self.authority.snapshot(), before)
        self.assertIsNone(self.authority.receipt('request1'))
        db = sqlite3.connect(self.db)
        try:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sources').fetchone()[0], 0)
        finally:
            db.close()

    def test_sql_failure_after_mutations_rolls_back_and_closes_writer(self):
        db = sqlite3.connect(self.db)
        try:
            db.execute("CREATE TRIGGER reject_job BEFORE INSERT ON jobs BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
            db.commit()
        finally:
            db.close()
        before = self.authority.snapshot()
        with self.assertRaises(AuthorityUnavailable):
            self.admit()
        self.assertEqual(self.authority.snapshot(), before)
        self.assertIsNone(self.authority.receipt('request1'))
        with self.assertRaises(AuthorityUnavailable):
            self.authority.advance(self.epoch, 'after-failure', self.paths('new'))
        db = sqlite3.connect(self.db)
        try:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sources').fetchone()[0], 0)
        finally:
            db.close()

    def test_uncertain_commit_before_actual_commit_is_not_retried(self):
        before = self.authority.snapshot()
        with mock.patch.object(self.authority, '_commit', side_effect=sqlite3.OperationalError('synthetic IO')) as commit:
            with self.assertRaises(CommitUnknown):
                self.admit()
            self.assertEqual(commit.call_count, 1)
        self.assertEqual(self.authority.snapshot(), before)
        self.assertIsNone(self.authority.receipt('request1'))
        with self.assertRaises(AuthorityUnavailable):
            self.admit()
        # Absence permits inspection only; this test does not authorize a new writer retry.
        self.assertIsNone(self.fresh_connection().receipt('request1'))

    def test_committed_but_unknown_admission_reconciles_immutable_receipt(self):
        def commit_then_uncertain(db):
            db.commit()
            raise sqlite3.OperationalError('synthetic lost commit acknowledgment')
        with mock.patch.object(self.authority, '_commit', side_effect=commit_then_uncertain) as commit:
            with self.assertRaises(CommitUnknown):
                self.admit()
            self.assertEqual(commit.call_count, 1)
        receipt = self.authority.receipt('request1')
        self.assertEqual(receipt['status'], 'QUEUED')
        self.assertEqual(self.authority.snapshot()['counters']['versions'], 1)
        with self.assertRaises(AuthorityUnavailable):
            self.admit()
        peer = self.fresh_connection()
        with self.assertRaises(AuthorityDenied):
            peer.admit(self.epoch, 'request1', 'submit', {}, b'normalized source fixture',
                       digest(b'normalized source fixture'), {'versions': 1})
        self.assertEqual(peer.receipt('request1'), receipt)
        self.assertEqual(peer.snapshot()['counters']['versions'], 1)

    def test_committed_but_unknown_advance_reconciles_without_second_cas(self):
        self.admit()
        def commit_then_uncertain(db):
            db.commit()
            raise sqlite3.OperationalError('synthetic lost CAS acknowledgment')
        with mock.patch.object(self.authority, '_commit', side_effect=commit_then_uncertain) as commit:
            with self.assertRaises(CommitUnknown):
                self.advance()
            self.assertEqual(commit.call_count, 1)
        state = self.authority.snapshot()
        self.assertEqual(state['run']['epoch'], self.epoch + 1)
        self.assertEqual(state['jobs'][0]['state'], 'UNKNOWN')
        self.assertEqual(self.authority.receipt('advance1')['unknown_jobs'], 1)
        with self.assertRaises(AuthorityUnavailable):
            self.authority.advance(self.epoch, 'retry', self.paths('different'))
        peer = self.fresh_connection()
        with self.assertRaises(AuthorityDenied):
            peer.advance(self.epoch, 'retry', self.paths('different'))
        self.assertEqual(peer.snapshot(), state)


    def test_baseexception_during_commit_is_unknown_before_or_after_commit(self):
        for exception_type in (KeyboardInterrupt, SystemExit):
            for committed in (False, True):
                with self.subTest(exception=exception_type.__name__, committed=committed):
                    label = exception_type.__name__ + str(committed)
                    authority = EpochAuthority.create(
                        self.root / (label + '.sqlite'), profile='resumable-authority-v1',
                        config={}, author='same', paths=self.paths(label), clock=self.clock)
                    def fail_commit(db):
                        if committed:
                            db.commit()
                        raise exception_type('synthetic exception, not a signal')
                    with mock.patch.object(authority, '_commit', side_effect=fail_commit) as commit:
                        with self.assertRaises(CommitUnknown):
                            authority.admit(0, 'request', 'submit', {}, b'source', digest(b'source'), {'versions': 1})
                        self.assertEqual(commit.call_count, 1)
                    receipt = authority.receipt('request')
                    self.assertEqual(receipt is not None, committed)
                    self.assertEqual(authority.snapshot()['counters']['versions'], int(committed))
                    with self.assertRaises(AuthorityUnavailable):
                        authority.advance(0, 'after', self.paths(label + '-next'))

    def test_commit_unknown_survives_rollback_and_close_exceptions(self):
        original_connect = self.authority._connect
        class BrokenCleanupConnection:
            def __init__(self, db):
                self.db = db
            def rollback(self):
                self.db.rollback()
                raise KeyboardInterrupt('synthetic rollback acknowledgment loss')
            def close(self):
                self.db.close()
                raise SystemExit('synthetic close acknowledgment loss')
            def __getattr__(self, name):
                return getattr(self.db, name)
        def connect(*args, **kwargs):
            return BrokenCleanupConnection(original_connect(*args, **kwargs))
        def commit_then_uncertain(db):
            db.commit()
            raise sqlite3.OperationalError('synthetic commit acknowledgment loss')
        with mock.patch.object(self.authority, '_connect', side_effect=connect), \
                mock.patch.object(self.authority, '_commit', side_effect=commit_then_uncertain):
            with self.assertRaises(CommitUnknown):
                self.admit()
        self.assertEqual(self.authority.receipt('request1')['status'], 'QUEUED')
        with self.assertRaises(AuthorityUnavailable):
            self.admit('not-a-retry')

    def test_admit_close_failure_inside_unrelated_except_is_commit_unknown(self):
        original_connect = self.authority._connect
        class CloseFailureConnection:
            def __init__(self, db):
                self.db = db
            def close(self):
                self.db.close()
                raise sqlite3.OperationalError('synthetic close acknowledgment loss')
            def __getattr__(self, name):
                return getattr(self.db, name)
        def connect(*args, **kwargs):
            return CloseFailureConnection(original_connect(*args, **kwargs))
        try:
            raise ValueError('unrelated caller exception')
        except ValueError:
            with mock.patch.object(self.authority, '_connect', side_effect=connect):
                with self.assertRaises(CommitUnknown):
                    self.admit()
        self.assertEqual(self.authority.receipt('request1')['status'], 'QUEUED')
        self.assertEqual(self.authority.snapshot()['counters']['versions'], 1)
        with self.assertRaises(AuthorityUnavailable):
            self.admit('no-retry')

    def test_create_close_failure_inside_unrelated_except_returns_no_instance(self):
        original_connect = sqlite3.connect
        class CloseFailureConnection:
            def __init__(self, db):
                self.db = db
            def close(self):
                self.db.close()
                raise sqlite3.OperationalError('synthetic close acknowledgment loss')
            def __getattr__(self, name):
                return getattr(self.db, name)
        def connect(*args, **kwargs):
            return CloseFailureConnection(original_connect(*args, **kwargs))
        returned = []
        path = self.root / 'uncertain-create.sqlite'
        try:
            raise ValueError('unrelated caller exception')
        except ValueError:
            with mock.patch.object(sqlite3, 'connect', side_effect=connect):
                with self.assertRaises(AuthorityUnavailable) as failure:
                    returned.append(EpochAuthority.create(
                        path, profile='resumable-authority-v1', config={}, author='same',
                        paths=self.paths('close-failure'), clock=self.clock))
                self.assertEqual(failure.exception.code, 'initialization_unknown')
        self.assertEqual(returned, [])
        # Preserve the uncertain committed database; do not retry creation.
        self.assertTrue(path.is_file())
        db = original_connect(path)
        try:
            self.assertEqual(db.execute('SELECT epoch,state FROM run').fetchone(), (0, 'ACTIVE'))
        finally:
            db.close()

    def test_authority_uuid_is_distinct_from_config_and_required_on_open(self):
        other = EpochAuthority.create(
            self.root / 'same-config.sqlite', profile='resumable-authority-v1',
            config={'fixture': 'unit-only'}, author='same-canonical-author',
            paths=self.paths('other'), clock=self.clock)
        self.assertEqual(other.config_digest, self.authority.config_digest)
        self.assertNotEqual(other.authority_id, self.authority.authority_id)
        with self.assertRaises(AuthorityDenied) as denied:
            EpochAuthority(other.path, authority_id=self.authority.authority_id,
                           config_digest=self.authority.config_digest, clock=self.clock)
        self.assertEqual(denied.exception.code, 'authority_mismatch')

    def test_replaced_same_config_database_rejects_reads_and_writes(self):
        self.admit()
        other = EpochAuthority.create(
            self.root / 'replacement.sqlite', profile='resumable-authority-v1',
            config={'fixture': 'unit-only'}, author='same-canonical-author',
            paths=self.paths('replacement'), clock=self.clock)
        self.assertEqual(other.config_digest, self.authority.config_digest)
        # SQLite backup replaces the logical database, including authority UUID.
        source = sqlite3.connect(other.path)
        target = sqlite3.connect(self.db)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        for operation in (self.authority.snapshot,
                          lambda: self.authority.receipt('request1'),
                          lambda: self.admit('after-replacement')):
            with self.subTest(operation=operation):
                with self.assertRaises(AuthorityDenied) as denied:
                    operation()
                self.assertEqual(denied.exception.code, 'authority_mismatch')
        db = sqlite3.connect(self.db)
        try:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT SUM(used) FROM counters').fetchone()[0], 0)
        finally:
            db.close()

    def test_authority_uuid_is_conserved_across_epoch_and_receipts(self):
        expected = self.authority.authority_id
        self.assertRegex(expected, r'^[0-9a-f]{32}$')
        self.admit()
        epoch = self.advance()
        self.reopen(epoch)
        self.assertEqual(self.authority.snapshot()['run']['authority_id'], expected)
        self.assertEqual(self.fresh_connection().authority_id, expected)
        for transition in ('request1', 'advance1', 'reopen1'):
            self.assertEqual(self.authority.receipt(transition)['authority_id'], expected)

    def test_invalid_authority_uuid_is_rejected(self):
        for invalid in (None, 1, '', 'a' * 31, 'g' * 32, 'A' * 32,
                        '00000000-0000-0000-0000-000000000000'):
            with self.subTest(value=invalid):
                with self.assertRaises(AuthorityDenied) as denied:
                    EpochAuthority(self.db, authority_id=invalid,
                                   config_digest=self.authority.config_digest, clock=self.clock)
                self.assertEqual(denied.exception.code, 'invalid_authority_id')

    def test_double_leading_slash_path_alias_is_rejected(self):
        paths = self.paths('new')
        paths['service'] = '/' + paths['service']
        before = self.authority.snapshot()
        with self.assertRaises(AuthorityDenied):
            self.authority.advance(self.epoch, 'bad-alias', paths)
        self.assertEqual(self.authority.snapshot(), before)

    def test_operator_read_rechecks_replaced_configuration_identity(self):
        self.admit()
        db = sqlite3.connect(self.db)
        try:
            db.execute('UPDATE run SET config_digest=? WHERE id=1', (digest(b'replaced configuration'),))
            db.commit()
        finally:
            db.close()
        for read in (self.authority.snapshot, lambda: self.authority.receipt('request1')):
            with self.assertRaises(AuthorityDenied) as denied:
                read()
            self.assertEqual(denied.exception.code, 'configuration_mismatch')

    def test_payload_and_debits_are_detached_before_sql_acquisition(self):
        payload = {'nested': {'value': 'original'}}
        effect = {'page': 0}
        debits = {'replays': 1, 'reads': 1}
        original_connect = self.authority._connect
        def connect(*args, **kwargs):
            payload['nested']['value'] = 'mutated'
            effect['page'] = 99
            debits['reads'] = 999
            return original_connect(*args, **kwargs)
        with mock.patch.object(self.authority, '_connect', side_effect=connect):
            self.admit(kind='replay', payload=payload, effect=effect, debits=debits)
        snapshot = self.authority.snapshot()
        request = json.loads(snapshot['jobs'][0]['request'])
        self.assertEqual(request['payload'], {'nested': {'value': 'original'}})
        self.assertEqual(request['effect'], {'page': 0})
        self.assertEqual(snapshot['counters']['reads'], 1)

    def test_clock_regression_inside_one_transaction_rejects(self):
        before = self.authority.snapshot()
        first = ClockSample(self.clock.domain, self.clock.wall + 10, self.clock.mono + 10)
        second = ClockSample(self.clock.domain, self.clock.wall + 9, self.clock.mono + 9)
        with mock.patch.object(self.authority, 'clock', side_effect=[first, second]):
            with self.assertRaises(AuthorityDenied) as denied:
                self.admit()
        self.assertEqual(denied.exception.code, 'clock_regression')
        self.assertEqual(self.authority.snapshot(), before)


    def test_operator_read_storage_failure_poisons_writer_not_reconciliation(self):
        accepted = self.admit()
        for method in ('receipt', 'snapshot'):
            with self.subTest(method=method):
                authority = self.fresh_connection()
                with mock.patch.object(authority, '_connect',
                                       side_effect=sqlite3.OperationalError('synthetic read failure')):
                    with self.assertRaises(AuthorityUnavailable):
                        if method == 'receipt':
                            authority.receipt('request1')
                        else:
                            authority.snapshot()
                self.assertEqual(authority.receipt('request1'), accepted)
                with self.assertRaises(AuthorityUnavailable) as denied:
                    authority.start(self.epoch, 'never-start', 'request1')
                self.assertEqual(denied.exception.code, 'writer_closed')


if __name__ == '__main__':
    unittest.main()
