"""Offline fixture lifecycle: no game, provider call, auth or process boundary."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.controller import (ControllerLimits, FrozenContext, ResultRequest, SubmitRequest,
    TOOL_NAMES, ToolCall, TurnReply, TurnRequest, _dispatch, launch_real, run_synthetic, tool_schemas)
from services.controller_ledger import SourceArtifactStore, SourceExport
from services.ledger import DurableLedger, LedgerError, LedgerLimits
from services.replay_protocol import (ReplayReply, encoded, parse_request, project_reply, replay_fields)
from services.replay_route import (ReplayFixture, ReplayProviderReceipt, ReplayScrimmageAdapter, SyntheticReplayRoute)
from services.replay_store import PreparedReplay, ReplayLimits, ReplayPolicy, ReplayStore
from services.responses_transport import OfflineResponsesTransport, TransportError
from test_ledger import archive, complete
from test_replay_feedback import fixture, SECRET
from test_responses_transport import FixtureCounter, FixtureExchange, response


POLICY = ReplayPolicy('2.0.3', '1' * 64, '2' * 64)
RAW = b'inert fixture replay bytes, not a BC23 engine output'


def request(submission, operation='manifest', **values):
    args = dict.fromkeys(replay_fields())
    args.update(submission_id=submission, operation=operation)
    if operation == 'manifest':
        args.update(manifest_offset=0, manifest_limit=8)
    args.update(values)
    return parse_request(args)


class Provider:
    def __init__(self):
        self.calls = []
        self.change = None
        self.callback = None

    def query(self, request):
        self.calls.append(request)
        if self.callback:
            self.callback(request)
        q = request.query
        full = fixture(start=q.round_start, end=q.round_end, robot=q.robot_id, limit=10000)
        rows = full['records'][q.record_offset:q.record_offset + q.record_limit]
        end, total = q.record_offset + len(rows), full['matching_record_count']
        full.update(format='bcenv.bc2023.replay-query.v2', records=rows,
                    returned_record_count=len(rows), truncated=len(rows) < total,
                    selected_records_complete=len(rows) == total, page_records_complete=True,
                    page_end_offset=end, next_record_offset=end if end < total else None)
        full['query'].update(record_offset=q.record_offset, record_limit=q.record_limit)
        if self.change:
            self.change(full)
        return ReplayProviderReceipt(encoded(full), request.source_sha256,
                                     request.expected_spec_version, request.producer_identity)


class ReplayRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'ledger.db'
        self.now = 1000
        self.ledger = DurableLedger(self.path, clock=lambda: self.now)
        self.store = ReplayStore(self.ledger, POLICY)
        self.provider = Provider()
        self.route = SyntheticReplayRoute(self.store, self.provider)

    def sql(self, query, args=()):
        with closing(sqlite3.connect(self.path)) as c, c:
            return c.execute(query, args).fetchall()

    def error(self, code, fn, *args, **kwargs):
        with self.assertRaises(LedgerError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def submit(self, owner='alice', run='run', key='key'):
        return self.store.submit(owner, run, key, archive(), 'bot')['submission_id']

    def finished(self, owner='alice', run='run', key='key'):
        sid = self.submit(owner, run, key)
        claim = self.ledger.reserve_job(owner, sid, 'worker')
        self.route.complete_with_replay(owner, run, claim, complete(), (ReplayFixture('one', RAW, 1),))
        return sid, claim

    def reply(self, req, owner='alice', run='run'):
        reply = self.route.replay(owner, run, req)
        project_reply(reply, req)
        return json.loads(reply.payload)

    def window(self, sid, **kwargs):
        manifest = self.reply(request(sid))
        rid = manifest['replays'][0]['replay_id']
        return request(sid, 'window', replay_id=rid, round_start=35, round_end=40,
                       robot_id=43, record_limit=kwargs.pop('record_limit', 5), **kwargs)

    def test_atomic_submit_binding_and_normalized_idempotency_restart(self):
        sid = self.submit()
        restarted = ReplayStore(DurableLedger(self.path, clock=lambda: self.now), POLICY)
        self.assertEqual(restarted.submit('alice', 'run', 'key', archive(), 'bot')['submission_id'], sid)
        self.assertEqual(self.sql('SELECT count(*) FROM replay_job_runs'), [(1,)])
        self.error('idempotency_conflict', restarted.submit, 'alice', 'other', 'key', archive(), 'bot')
        self.error('not_found', restarted.result, 'alice', 'other', sid)

    def test_invalid_source_is_bound_terminal_but_never_replayable(self):
        result = self.store.submit('alice', 'run', 'invalid', b'not zip', 'bot')
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(self.reply(request(result['submission_id']))['reason'], 'replay_not_available')
        self.assertEqual(self.sql('SELECT count(*) FROM replay_sources'), [(0,)])

    def test_atomic_completion_retry_and_changed_batch_conflict(self):
        sid, claim = self.finished()
        self.assertEqual(self.store.complete(
            'alice', 'run', claim, complete(), (PreparedReplay('one', RAW, 1, 1, 45),))['status'], 'complete')
        self.error('terminal_conflict', self.store.complete, 'alice', 'run', claim, complete(),
                   (PreparedReplay('one', RAW + b'changed', 1, 1, 45),))
        self.assertEqual(self.sql('SELECT count(*) FROM replay_sources'), [(1,)])

    def test_exact_route_completion_repeat_after_expiry_is_not_new_publication(self):
        sid, claim = self.finished()
        self.now += 7200
        prior_calls = len(self.provider.calls)
        result = self.route.complete_with_replay('alice', 'run', claim, complete(), (ReplayFixture('one', RAW, 1),))
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(len(self.provider.calls), prior_calls)
        for changed in ((ReplayFixture('other', RAW, 1),), (ReplayFixture('one', RAW + b'x', 1),),
                        (ReplayFixture('one', RAW, 2),)):
            self.error('terminal_conflict', self.route.complete_with_replay, 'alice', 'run', claim, complete(), changed)
        self.assertEqual(self.reply(request(sid))['reason'], 'replay_not_available')
        self.assertEqual(self.sql('SELECT count(*) FROM replay_sources'), [(1,)])

    def test_legacy_unbound_and_legacy_finish_cannot_retroactively_register(self):
        legacy = self.ledger.submit('alice', 'legacy', archive(), 'bot')['submission_id']
        self.assertEqual(self.reply(request(legacy))['reason'], 'not_found')
        self.assertEqual(self.reply(request(legacy), run='unrelated_run')['reason'], 'not_found')
        self.error('not_found', self.store.result, 'alice', 'unrelated_run', legacy)
        sid = self.submit()
        claim = self.ledger.reserve_job('alice', sid, 'worker')
        self.ledger.finish('alice', sid, 'worker', claim.generation, complete())
        self.error('terminal_conflict', self.route.complete_with_replay, 'alice', 'run', claim,
                   complete(), (ReplayFixture('one', RAW, 1),))
        self.assertEqual(self.reply(request(sid))['reason'], 'replay_not_available')

    def test_cross_owner_run_submission_and_guessed_handles_are_uniform(self):
        sid, _ = self.finished()
        req = self.window(sid)
        first = self.reply(req)
        for owner, run, foreign in [('bob', 'run', sid), ('alice', 'wrong', sid), ('alice', 'run', 's_' + 'f'*32)]:
            for check in (request(foreign), request(foreign, 'window', replay_id=req.replay_id,
                    round_start=35, round_end=40, robot_id=43, record_limit=5),
                    request(foreign, 'page', view_id=first['view_id'], record_offset=5)):
                self.assertEqual(self.reply(check, owner, run)['reason'], 'not_found')
        self.assertEqual(len(self.provider.calls), 2)

    def test_restart_pages_exact_progress_to_eof_and_no_sensitive_fields(self):
        sid, _ = self.finished()
        first = self.reply(self.window(sid))
        rows = first['page']['records'][:]
        self.store = ReplayStore(DurableLedger(self.path, clock=lambda: self.now), POLICY)
        self.route = SyntheticReplayRoute(self.store, self.provider)
        page = first
        while page['page']['coverage']['next_record_offset'] is not None:
            offset = page['page']['coverage']['next_record_offset']
            page = self.reply(request(sid, 'page', view_id=first['view_id'], record_offset=offset))
            self.assertEqual(page['page']['query']['record_offset'], offset)
            self.assertGreater(len(page['page']['records']), 0)
            rows.extend(page['page']['records'])
            self.assertLessEqual(len(encoded(page)), 12288)
            self.assertNotIn(SECRET.encode(), encoded(page))
            for private in ('source_sha256', 'source_replay', 'producer_identity', 'projector_identity'):
                self.assertNotIn(private.encode(), encoded(page))
        self.assertEqual(len(rows), 26)
        self.assertEqual(rows[-1]['kind'], 'death')
        self.assertEqual(self.sql('SELECT count(*) FROM replay_views'), [(1,)])

    def test_same_page_requery_fingerprint_and_conflict(self):
        sid, _ = self.finished()
        req = self.window(sid)
        first = self.reply(req)
        again = self.reply(request(sid, 'page', view_id=first['view_id'], record_offset=0))
        self.assertEqual(first['page'], again['page'])
        def change(value):
            value['records'][1]['adamantium_delta'] -= 1
        self.provider.change = change
        self.assertEqual(self.reply(req)['reason'], 'replay_conflict')
        self.assertEqual(self.sql('SELECT state,attempt_count FROM replay_attempts'), [('failed', 3)])

    def test_provider_exception_malformed_or_wrong_echo_never_publishes(self):
        sid, _ = self.finished()
        req = self.window(sid)
        for value in (None, b'/private/path', ReplayProviderReceipt(b'{}', '0'*64, '2.0.3', '1'*64)):
            with patch.object(self.provider, 'query', return_value=value):
                self.assertEqual(self.reply(req)['reason'], 'invalid_evidence')
        with patch.object(self.provider, 'query', side_effect=RuntimeError(SECRET)):
            self.assertEqual(self.reply(req)['reason'], 'invalid_evidence')
        self.assertEqual(self.sql('SELECT state,attempt_count,fingerprint FROM replay_attempts'), [('failed', 4, None)])

    def test_failed_registration_and_expiry_during_prepare_leave_no_orphans(self):
        sid = self.submit()
        claim = self.ledger.reserve_job('alice', sid, 'worker')
        self.provider.change = lambda value: value.update(source_stream_complete=False)
        self.error('invalid_evidence', self.route.complete_with_replay, 'alice', 'run', claim, complete(), (ReplayFixture('one', RAW, 1),))
        self.assertEqual(self.sql('SELECT state FROM jobs'), [('running',)])
        self.assertEqual(self.sql('SELECT count(*) FROM replay_sources'), [(0,)])
        self.provider.change = None
        self.provider.callback = lambda req: setattr(self, 'now', 1031)
        self.error('terminal_conflict', self.route.complete_with_replay, 'alice', 'run', claim, complete(), (ReplayFixture('one', RAW, 1),))
        self.assertEqual(self.ledger.result('alice', sid)['status'], 'infrastructure_failure')
        self.assertEqual(self.sql('SELECT count(*) FROM replay_completions'), [(0,)])

    def test_expiry_during_callback_fences_result_and_explicit_requery(self):
        sid, _ = self.finished()
        req = self.window(sid)
        self.provider.callback = lambda unused: setattr(self, 'now', 1031)
        self.assertEqual(self.reply(req)['reason'], 'stale_reservation')
        self.assertEqual(self.sql('SELECT state,fingerprint FROM replay_attempts'), [('expired', None)])
        self.provider.callback = None
        self.assertEqual(self.reply(req)['status'], 'available')
        self.assertEqual(self.sql('SELECT generation,attempt_count FROM replay_attempts'), [(2, 2)])

    def test_rollback_terminalizes_attempt_and_persists_across_connection(self):
        sid, _ = self.finished()
        req = self.window(sid)
        claim = self.store.reserve_page('alice', 'run', sid, replay_id=req.replay_id,
            round_start=35, round_end=40, robot_id=43, record_limit=5)
        self.now = 999.5
        self.error('clock_regression', self.store.finish_page, 'alice', 'run', claim, b'{}')
        self.assertEqual(self.sql('SELECT state,fingerprint FROM replay_attempts'), [('expired', None)])
        self.now = 1000
        restarted = ReplayStore(DurableLedger(self.path, clock=lambda: self.now), POLICY)
        self.error('stale_reservation', restarted.finish_page, 'alice', 'run', claim, b'{}')

    def test_stale_generation_and_concurrent_active_limit(self):
        sid, _ = self.finished()
        req = self.window(sid)
        def reserve():
            try:
                return self.store.reserve_page('alice', 'run', sid, replay_id=req.replay_id,
                    round_start=35, round_end=40, robot_id=43, record_limit=5)
            except LedgerError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: reserve(), range(8)))
        claims = [result for result in results if not isinstance(result, str)]
        self.assertEqual(len(claims), 1)
        self.assertEqual(results.count('resource_limit'), 7)
        self.error('stale_reservation', self.store.finish_page, 'alice', 'run', replace(claims[0], generation=2), b'{}')
        self.store.finish_page('alice', 'run', claims[0])

    def test_source_and_view_expiry_never_reopen_after_restart(self):
        sid, _ = self.finished()
        req = self.window(sid)
        first = self.reply(req)
        self.now += 1800
        self.assertEqual(self.reply(request(sid, 'page', view_id=first['view_id'], record_offset=5))['reason'], 'replay_not_available')
        self.assertEqual(self.reply(req)['reason'], 'replay_not_available')
        self.now += 1800
        self.assertEqual(self.reply(request(sid))['reason'], 'replay_not_available')
        self.assertEqual(self.sql('SELECT count(*) FROM replay_sources'), [(1,)])

    def test_tiny_attempt_and_view_caps_fail_explicitly_not_eof(self):
        path = Path(self.temp.name) / 'caps.db'
        self.ledger = DurableLedger(path, clock=lambda: self.now)
        self.store = ReplayStore(self.ledger, POLICY, limits=replace(ReplayLimits(), attempts_per_run=1, views_per_job=1))
        self.route = SyntheticReplayRoute(self.store, self.provider)
        sid, _ = self.finished()
        req = self.window(sid)
        first = self.reply(req)
        self.assertIsNotNone(first['page']['coverage']['next_record_offset'])
        exhausted = self.reply(request(sid, 'page', view_id=first['view_id'], record_offset=5))
        self.assertEqual(exhausted['reason'], 'resource_limit')
        self.assertNotIn('page', exhausted)

    def test_identical_source_bytes_charge_full_logical_capacity(self):
        self.path = Path(self.temp.name) / 'sources.db'
        self.ledger = DurableLedger(self.path, clock=lambda: self.now)
        self.store = ReplayStore(self.ledger, POLICY, limits=replace(ReplayLimits(), global_source_bytes=len(RAW)))
        self.route = SyntheticReplayRoute(self.store, self.provider)
        self.finished()
        sid = self.submit('bob', 'run', 'other')
        claim = self.ledger.reserve_job('bob', sid, 'worker')
        self.error('resource_limit', self.route.complete_with_replay, 'bob', 'run', claim, complete(), (ReplayFixture('one', RAW, 1),))
        self.assertEqual(self.sql('SELECT count(*) FROM replay_sources'), [(1,)])
        self.assertEqual(self.ledger.result('bob', sid)['status'], 'accepted')

    def test_storage_failure_rolls_back_terminal_and_sources_atomically(self):
        sid = self.submit()
        claim = self.ledger.reserve_job('alice', sid, 'worker')
        self.sql("CREATE TRIGGER fail_replay BEFORE INSERT ON replay_sources BEGIN SELECT RAISE(ABORT,'private-error'); END")
        self.error('storage_unavailable', self.route.complete_with_replay, 'alice', 'run', claim, complete(), (ReplayFixture('one', RAW, 1),))
        self.assertEqual(self.sql('SELECT state FROM jobs'), [('running',)])
        self.assertEqual(self.sql('SELECT count(*) FROM replay_completions'), [(0,)])

    def test_uncertain_publication_has_no_automatic_retry_or_failure_write(self):
        sid, _ = self.finished()
        req = self.window(sid)
        original = self.store.finish_page
        def uncertain(*args, **kwargs):
            original(*args, **kwargs)
            raise LedgerError('storage_unavailable')
        with patch.object(self.store, 'finish_page', side_effect=uncertain) as finish:
            self.assertEqual(self.reply(req)['reason'], 'storage_unavailable')
            self.assertEqual(finish.call_count, 1)
        self.assertEqual(len(self.provider.calls), 2)
        self.assertEqual(self.sql('SELECT state,attempt_count FROM replay_attempts'), [('complete', 1)])

    def test_configuration_init_races_and_version_drift(self):
        path = Path(self.temp.name) / 'race.db'
        def create(_):
            return ReplayStore(DurableLedger(path, clock=lambda: self.now), POLICY).configuration
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(len(set(pool.map(create, range(8)))), 1)
        self.error('configuration_mismatch', ReplayStore, self.ledger, replace(POLICY, producer_identity='3'*64))
        self.error('configuration_mismatch', ReplayStore, self.ledger, POLICY, limits=replace(ReplayLimits(), max_records=4))

    def test_source_result_policy_drift_fails_closed(self):
        sid, _ = self.finished()
        req = self.window(sid)
        self.sql("UPDATE jobs SET generation=generation+1 WHERE id=?", (sid,))
        self.assertEqual(self.reply(req)['reason'], 'storage_integrity')
        self.assertEqual(len(self.provider.calls), 1)

    def test_adapter_run_binding_survives_restart_and_v1_still_withholds(self):
        artifacts = SourceArtifactStore()
        aid = artifacts.capture('alice', 'run', SourceExport('bot', archive()))
        adapter = ReplayScrimmageAdapter(self.route, artifacts, 'alice', 'run')
        sid = adapter.submit(SubmitRequest(aid, 'bot')).submission_id
        second = ReplayScrimmageAdapter(self.route, artifacts, 'alice', 'run')
        self.assertEqual(second.submit(SubmitRequest(aid, 'bot')).submission_id, sid)
        self.assertEqual(second.result(ResultRequest(sid)).submission_id, sid)
        self.assertIsNone(second.replay(ResultRequest(sid)))
        self.error('not_found', ReplayScrimmageAdapter(self.route, artifacts, 'alice', 'other').result, ResultRequest(sid))

    def test_flat_schema_default_v1_and_registry_selection(self):
        v1, v2 = tool_schemas(), tool_schemas(protocol='v2')
        self.assertEqual(v1, tool_schemas(protocol='v1'))
        self.assertEqual(v1[:-1], v2[:-1])
        schema = json.loads(v2[-1])['parameters']
        self.assertEqual(set(schema['required']), set(schema['properties']))
        self.assertFalse(schema['additionalProperties'])
        self.assertNotIn('anyOf', schema)
        for key, value in schema['properties'].items():
            if key not in ('submission_id','operation'):
                self.assertIn('null', value['type'])
        transport = OfflineResponsesTransport(FixtureExchange([response()]), FixtureCounter(), controller_protocol='v2')
        ctx = FrozenContext(b'system', b'task')
        with self.assertRaisesRegex(TransportError, 'registry_changed'):
            transport.next_turn(TurnRequest(ctx, TOOL_NAMES, v1, (), 1))
        good = OfflineResponsesTransport(FixtureExchange([response()]), FixtureCounter(), controller_protocol='v2')
        self.assertEqual(good.next_turn(TurnRequest(ctx, TOOL_NAMES, v2, (), 1)).final_text, 'done')

    def test_strict_arguments_and_closed_second_projection(self):
        sid, _ = self.finished()
        req = self.window(sid)
        result = self.route.replay('alice', 'run', req)
        self.assertEqual(project_reply(result, req)['status'], 'available')
        for key, value in [('log', SECRET), ('status', SECRET)]:
            changed = json.loads(result.payload)
            changed[key] = value
            with self.assertRaises(ValueError):
                project_reply(ReplayReply(encoded(changed)), req)
        changed = json.loads(result.payload)
        changed['page']['records'][0]['path'] = SECRET
        with self.assertRaises(ValueError):
            project_reply(ReplayReply(encoded(changed)), req)
        for values in ({'record_offset': 0}, {'round_start': True}, {'host_path': SECRET}):
            arguments = asdict(req); arguments.update(values)
            with self.assertRaises(ValueError):
                parse_request(arguments)
        with self.assertRaises(ValueError):
            project_reply(ReplayReply(b' ' * 12289), req)

    def test_v1_dispatch_never_opens_v2_and_unknown_handles_fail_first(self):
        sid, _ = self.finished()
        adapter = ReplayScrimmageAdapter(self.route, SourceArtifactStore(), 'alice', 'run')
        call = ToolCall('a', 'scrimmage_replay', json.dumps({'submission_id': sid}))
        out = _dispatch(call, None, adapter, ControllerLimits(), set(), {sid})
        self.assertEqual(out['reason'], 'replay_withheld')
        req = request(sid)
        call = replace(call, arguments_json=json.dumps(asdict(req)))
        self.assertEqual(_dispatch(call, None, adapter, ControllerLimits(), set(), {sid}, 'v2')['status'], 'available')
        with self.assertRaisesRegex(ValueError, 'unknown_handle'):
            _dispatch(call, None, adapter, ControllerLimits(), set(), set(), 'v2')
        self.assertEqual(len(self.provider.calls), 1)

    def test_valid_controller_six_tool_flow_retrieves_multiple_pages(self):
        from services.controller import ExecReply
        artifacts = SourceArtifactStore()
        aid = artifacts.capture('alice', 'run', SourceExport('bot', archive()))
        adapter = ReplayScrimmageAdapter(self.route, artifacts, 'alice', 'run')
        outer = self
        class Workspace:
            def read(inner, req): return b'fixture rules'
            def write(inner, req): return None
            def exec(inner, req): return ExecReply('succeeded', artifact_id=aid)
        class Transport:
            def next_turn(inner, turn):
                outer.assertEqual(turn.tool_schemas, tool_schemas(protocol='v2'))
                results = [json.loads(row)['result'] for row in turn.transcript if 'result' in json.loads(row)]
                n = len(results)
                if n == 0:
                    name, args = 'workspace_read', {'path': 'docs/rules', 'offset': 0, 'limit': 100}
                elif n == 1:
                    name, args = 'workspace_write', {'path': 'src/test.java', 'content': 'synthetic'}
                elif n == 2:
                    name, args = 'workspace_exec', {'argv': ['fixture'], 'cwd': '.', 'timeout_seconds': 1}
                elif n == 3:
                    name, args = 'scrimmage_submit', {'artifact_id': aid, 'package': 'bot'}
                elif n == 4:
                    inner.sid = results[-1]['submission_id']
                    claim = outer.ledger.reserve_job('alice', inner.sid, 'worker')
                    outer.route.complete_with_replay('alice', 'run', claim, complete(), (ReplayFixture('one', RAW, 1),))
                    name, args = 'scrimmage_result', {'submission_id': inner.sid}
                elif n == 5:
                    name, args = 'scrimmage_replay', asdict(request(inner.sid))
                elif n == 6:
                    name, args = 'scrimmage_replay', asdict(request(inner.sid, 'window',
                        replay_id=results[-1]['replays'][0]['replay_id'], round_start=0,
                        round_end=45, robot_id=None, record_limit=64))
                elif results[-1]['page']['coverage']['next_record_offset'] is not None:
                    last = results[-1]
                    name, args = 'scrimmage_replay', asdict(request(inner.sid, 'page',
                        view_id=last['view_id'], record_offset=last['page']['coverage']['next_record_offset']))
                else:
                    return TurnReply(final_text='finished fixture pages')
                return TurnReply((ToolCall('c' * 60 + str(n), name, json.dumps(args)),))
        report = run_synthetic(FrozenContext(b'exact system', b'exact task'), Transport(), Workspace(), adapter, protocol='v2')
        self.assertEqual(report.status, 'complete')
        values = [json.loads(row) for row in report.transcript]
        outputs = [value['result'] for value in values if 'result' in value]
        pages = [out['page'] for out in outputs if 'page' in out]
        self.assertGreater(len(pages), 1)
        self.assertEqual(sum(len(page['records']) for page in pages), fixture(start=0, end=45, robot=None, limit=10000)['matching_record_count'])
        self.assertEqual({call['name'] for value in values for call in value.get('calls', [])}, set(TOOL_NAMES))
        self.assertTrue(all(len(encoded(value)) <= 16384 for value in values if 'result' in value))
        self.assertTrue(all(len(encoded(value['result'])) <= 12288 for value in values if 'result' in value and 'page' in value['result']))
        self.assertLessEqual(sum(len(encoded(value)) for value in values if 'result' in value), ControllerLimits().max_total_output_bytes)

    def test_malformed_registration_metadata_never_invokes_provider(self):
        sid = self.submit()
        claim = self.ledger.reserve_job('alice', sid, 'worker')
        for fixtures in ((ReplayFixture('key', bytearray(RAW), 1),),
                         (ReplayFixture('key', RAW, True),),
                         (ReplayFixture('key', RAW, 1), ReplayFixture('key', RAW, 1))):
            self.error('invalid_evidence', self.route.complete_with_replay, 'alice', 'run', claim, complete(), fixtures)
        self.error('invalid_context', self.route.complete_with_replay, 'alice', 'run', claim, complete(), (ReplayFixture('../path', RAW, 1),))
        self.error('invalid_request', self.store.preflight, 'alice', 'run', replace(claim, generation=True))
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.sql('SELECT count(*) FROM replay_sources'), [(0,)])

    def test_triggers_and_blob_integrity_and_guarded_submit_configuration(self):
        sid, _ = self.finished()
        with self.assertRaises(sqlite3.IntegrityError):
            self.sql('UPDATE replay_sources SET source=?', (b'changed',))
        self.sql('DROP TRIGGER immutable_replay_sources_update')
        self.sql('UPDATE replay_sources SET source=?,size=?', (b'changed', len(b'changed')))
        self.assertEqual(self.reply(request(sid))['reason'], 'storage_integrity')
        self.sql("UPDATE metadata SET value='{}' WHERE key='replay_configuration'")
        self.error('configuration_mismatch', self.store.submit, 'alice', 'run', 'next', archive(), 'bot')
        self.assertEqual(self.sql('SELECT count(*) FROM jobs'), [(1,)])

    def test_manifest_wide_ledger_clock_and_ttl_use_bounded_route_integers(self):
        self.now = 2**53 + 3
        sid, _ = self.finished()
        manifest = self.reply(request(sid))
        self.assertEqual(manifest['replays'][0]['expires_at'], self.now + 3600)
        # Game integers remain narrow even though expiry uses ledger timestamps.
        changed = dict(manifest)
        changed['replays'] = [dict(manifest['replays'][0], max_reported_rounds=2**31)]
        with self.assertRaises(ValueError):
            project_reply(ReplayReply(encoded(changed)), request(sid))
        with patch.object(self.store, 'manifest', return_value=changed):
            self.assertEqual(self.reply(request(sid))['reason'], 'invalid_evidence')
        for payload in (b'{"x":1e1000}', b'{"x":' + b'9'*21 + b'}', b'{"x":1,"x":2}',
                        b'['*33 + b'0' + b']'*33):
            with self.assertRaises(ValueError):
                project_reply(ReplayReply(payload), request(sid))

    def test_second_projection_rejects_boolean_manifest_offset_and_false_coverage(self):
        sid, _ = self.finished()
        manifest = self.reply(request(sid))
        manifest['total_replays'] = 2
        manifest['next_manifest_offset'] = True
        req = request(sid, manifest_limit=1)
        with self.assertRaises(ValueError):
            project_reply(ReplayReply(encoded(manifest)), req)
        window = self.window(sid)
        raw = self.reply(window)
        for key, value in [('source_returned_record_count', 1), ('source_page_end_offset', 1),
                           ('selected_records_complete', True), ('truncation_reasons', [])]:
            changed = json.loads(encoded(raw))
            changed['page']['coverage'][key] = value
            with self.assertRaises(ValueError):
                project_reply(ReplayReply(encoded(changed)), window)

    def test_controller_complete_envelopes_and_total_budget(self):
        sid, _ = self.finished()
        adapter = ReplayScrimmageAdapter(self.route, SourceArtifactStore(), 'alice', 'run')
        req = request(sid)
        class Transport:
            def __init__(inner): inner.turn = 0
            def next_turn(inner, turn):
                inner.turn += 1
                if inner.turn == 1:
                    return TurnReply((ToolCall('x'*64, 'scrimmage_replay', json.dumps(asdict(req))),))
                return TurnReply(final_text='done')
        # Controller additionally requires a submission learned in this run; the
        # denied guess still has a fully measured envelope and aggregate charge.
        report = run_synthetic(FrozenContext(b's', b't'), Transport(), None, adapter, protocol='v2')
        results = [row for row in report.transcript if b'"result"' in row]
        self.assertEqual(report.status, 'complete')
        self.assertTrue(all(len(row) <= 16384 for row in results))
        limited = run_synthetic(FrozenContext(b's', b't'), Transport(), None, adapter,
            protocol='v2', limits=replace(ControllerLimits(), max_total_output_bytes=1))
        self.assertEqual(limited.status, 'output_limit')
        self.assertEqual(len(limited.transcript), 1)
        with self.assertRaisesRegex(RuntimeError, 'real_launch_unsupported'):
            launch_real(protocol='v2', replay=True)
