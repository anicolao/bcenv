"""Offline composition tests: inert workspace/transport, real temporary SQLite."""
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
from services.controller import (ControllerLimits, ExecReply, ExecRequest, FrozenContext,
    ResultRequest, SubmitRequest, TOOL_NAMES, ToolCall, TurnReply, launch_real, run_synthetic)
from services.controller_ledger import (ArtifactError, ArtifactLimits, CapturingSandboxAdapter,
    DurableScrimmageAdapter, ExportedExecReply, SourceArtifactStore, SourceExport)
from services.ledger import DurableLedger, LedgerError, LedgerLimits
from services.scrimmage import BoundaryUnverified, require_heldout_attestation

SOURCE = b'package bot; public class RobotPlayer {}\n'
FAKE = 'a_' + 'f' * 32


def archive(source=SOURCE, compression=zipfile.ZIP_STORED, path='bot/RobotPlayer.java'):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=compression) as z:
        z.writestr(path, source)
    return output.getvalue()


def call(name, arguments, identifier):
    return ToolCall(identifier, name, json.dumps(arguments))


def exec_call(identifier='exec'):
    return call('workspace_exec', {'argv': ['candidate-test', '--package'],
                                  'cwd': 'src', 'timeout_seconds': 1}, identifier)


def results(request_or_report):
    return [v['result'] for v in map(json.loads, request_or_report.transcript) if 'result' in v]


def complete():
    return {'status': 'complete', 'validated_complete': True,
            'summary': {'games': 1, 'wins': 1, 'draws': 0, 'losses': 0},
            'private_path': '/private/opponent', 'logs': 'never publish'}


class MemorySandbox:
    def __init__(self):
        self.files = {'src/bot/RobotPlayer.java': SOURCE}
        self.reply = None
        self.requests = []

    def read(self, request):
        self.requests.append(request)
        return self.files[request.path][request.offset:request.offset + request.limit]

    def write(self, request):
        self.requests.append(request)
        self.files[request.path] = request.content

    def exec(self, request):
        self.requests.append(request)
        return self.reply if self.reply is not None else ExportedExecReply(
            'succeeded', b'synthetic export', source=SourceExport('bot', archive(self.files['src/bot/RobotPlayer.java'])))


class Steps:
    def __init__(self, *steps):
        self.steps = iter(steps)
        self.requests = []

    def next_turn(self, request):
        self.requests.append(request)
        step = next(self.steps)
        return step(request) if callable(step) else step


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'ledger.db'
        self.now = 1000
        self.ledger = DurableLedger(self.path, clock=lambda: self.now)
        self.store = SourceArtifactStore()
        self.backend = MemorySandbox()
        self.sandbox = CapturingSandboxAdapter(self.backend, self.store, 'alice', 'run1')
        self.adapter = DurableScrimmageAdapter(self.ledger, self.store, 'alice', 'run1')
        self.context = FrozenContext(b'exact system\r\n', b'exact task\n')
        self.exec_request = ExecRequest(('inert-candidate-test',), 'src', 1, 1048576, 16384)

    def capture(self, source=SOURCE, owner='alice', run='run1', compression=zipfile.ZIP_STORED):
        return self.store.capture(owner, run, SourceExport('bot', archive(source, compression)))

    def submit(self, handle=None):
        return self.adapter.submit(SubmitRequest(handle or self.capture(), 'bot')).submission_id

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises((ArtifactError, LedgerError)) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def sql(self, statement):
        with closing(sqlite3.connect(self.path)) as db, db:
            return db.execute(statement).fetchall()

    def run_steps(self, *steps, **kwargs):
        self.transport = Steps(*steps)
        return run_synthetic(self.context, self.transport, self.sandbox, self.adapter, **kwargs)

    def test_all_six_tools_compose_with_real_durable_state_and_bounded_feedback(self):
        def submit(request):
            handle = results(request)[-1]['artifact_id']
            return TurnReply((call('scrimmage_submit', {'artifact_id': handle, 'package': 'bot'}, 'submit'),))
        def feedback(request):
            identifier = results(request)[-1]['submission_id']
            claim = self.ledger.reserve_job('alice', identifier, 'synthetic-worker')
            self.assertEqual(claim.source.files[0].data, SOURCE)
            self.ledger.finish('alice', identifier, claim.worker, claim.generation, complete())
            return TurnReply((call('scrimmage_result', {'submission_id': identifier}, 'result'),
                              call('scrimmage_replay', {'submission_id': identifier}, 'replay')))
        first = TurnReply((call('workspace_write', {'path': 'src/bot/RobotPlayer.java', 'content': SOURCE.decode()}, 'write'),
                           call('workspace_read', {'path': 'src/bot/RobotPlayer.java', 'offset': 0, 'limit': 100}, 'read'),
                           exec_call()))
        with patch('subprocess.Popen', side_effect=AssertionError('no host execution')):
            report = self.run_steps(first, submit, feedback, TurnReply(final_text='synthetic only'))
        self.assertEqual((report.status, report.tool_calls), ('complete', 6))
        self.assertEqual(results(report)[-2]['summary']['wins'], 1)
        self.assertEqual(results(report)[-1]['reason'], 'replay_withheld')
        self.assertNotIn('private', b'\n'.join(report.transcript).decode())
        self.assertNotIn('validated_complete', b'\n'.join(report.transcript).decode())
        self.assertEqual(self.sql('SELECT state FROM jobs'), [('complete',)])
        for request in self.transport.requests:
            self.assertIs(request.context, self.context)
            self.assertEqual(request.tool_names, TOOL_NAMES)
            self.assertEqual(len(request.tool_schemas), 6)

    def test_stdout_claims_and_paths_never_issue_artifact_authority(self):
        self.backend.reply = ExportedExecReply('succeeded', json.dumps(
            {'artifact_id': FAKE, 'path': '/private/source.zip'}).encode())
        report = self.run_steps(TurnReply((exec_call(),)),
            TurnReply((call('scrimmage_submit', {'artifact_id': FAKE, 'package': 'bot'}, 'submit'),)),
            TurnReply(final_text='done'))
        self.assertNotIn('artifact_id', results(report)[0])
        self.assertEqual(results(report)[1]['reason'], 'unknown_handle')
        self.assertEqual(self.sql('SELECT count(*) FROM jobs'), [(0,)])
        self.error('unknown_artifact', self.store.resolve, 'alice', 'run1', FAKE)

    def test_backend_cannot_supply_an_exec_reply_with_chosen_handle(self):
        self.backend.reply = ExecReply('succeeded', artifact_id=FAKE)
        report = self.run_steps(TurnReply((exec_call(),)), TurnReply(final_text='done'))
        self.assertEqual(results(report)[0], {'status': 'infrastructure_failure', 'reason': 'adapter_failure'})
        self.error('unknown_artifact', self.store.resolve, 'alice', 'run1', FAKE)

    def test_cross_owner_and_cross_run_artifacts_are_uniformly_denied(self):
        handle = self.capture()
        for owner, run in (('bob', 'run1'), ('alice', 'run2')):
            other = DurableScrimmageAdapter(self.ledger, self.store, owner, run)
            for guessed in (handle, FAKE, '/host/file.zip', None):
                self.error('unknown_artifact', other.submit, SubmitRequest(guessed, 'bot'))
        self.assertEqual(self.sql('SELECT count(*) FROM jobs'), [(0,)])

    def test_same_owner_different_run_cannot_read_existing_submission(self):
        identifier = self.submit()
        for owner, run in (('bob', 'run1'), ('alice', 'run2')):
            other = DurableScrimmageAdapter(self.ledger, self.store, owner, run)
            for guessed in (identifier, 's_' + 'f' * 32):
                self.error('unknown_submission', other.result, ResultRequest(guessed))
                self.error('unknown_submission', other.replay, ResultRequest(guessed))

    def test_mutation_after_capture_cannot_change_submitted_source(self):
        exported = self.sandbox.exec(self.exec_request)
        frozen = self.store.resolve('alice', 'run1', exported.artifact_id)
        original_archive = frozen.archive
        self.backend.files['src/bot/RobotPlayer.java'] = SOURCE + b'// changed later'
        self.backend.reply = ExportedExecReply('failed', b'new stdout')
        with self.assertRaises(FrozenInstanceError):
            frozen.archive = archive(b'changed')
        identifier = self.submit(exported.artifact_id)
        claim = self.ledger.reserve_job('alice', identifier, 'worker')
        self.assertEqual(claim.source.files[0].data, SOURCE)
        self.assertEqual(frozen.archive, original_archive)

    def test_mutable_buffers_and_duck_typed_exports_are_rejected(self):
        class Duck:
            archive = archive()
            package = 'bot'
        for source in (Duck(), {'package': 'bot', 'archive': archive()},
                       SourceExport('bot', bytearray(archive())), SourceExport('bot', memoryview(archive())),
                       SourceExport('bot', '/private/source.zip')):
            self.error('invalid_export', self.store.capture, 'alice', 'run1', source)
        self.assertEqual(self.capture()[:2], 'a_')

    def test_bad_export_never_consumes_a_record_or_publishes_handle(self):
        store = SourceArtifactStore(limits=ArtifactLimits(run_records=1, global_records=1))
        wrapper = CapturingSandboxAdapter(self.backend, store, 'alice', 'run1')
        for reply in (ExportedExecReply('failed', source=SourceExport('bot', archive())),
                      ExportedExecReply('timeout', source=SourceExport('bot', archive())),
                      ExportedExecReply('succeeded', 'not bytes'),
                      ExportedExecReply('succeeded', b'x' * 16385),
                      ExportedExecReply('succeeded', source=SourceExport('bot', b'bad zip'))):
            self.backend.reply = reply
            with self.assertRaises(ArtifactError):
                wrapper.exec(self.exec_request)
        self.backend.reply = None
        handle = wrapper.exec(self.exec_request).artifact_id
        self.assertEqual(store.resolve('alice', 'run1', handle).package, 'bot')
        self.error('artifact_limit', wrapper.exec, self.exec_request)

    def test_package_mismatch_rejected_before_durable_submit(self):
        handle = self.capture()
        for package in ('other', '', None, 'bot\x00'):
            self.error('package_mismatch', self.adapter.submit, SubmitRequest(handle, package))
        self.assertEqual(self.sql('SELECT count(*) FROM jobs'), [(0,)])
        self.assertEqual(self.sql('SELECT count(*) FROM rate_events'), [(0,)])

    def test_archive_intake_blocks_traversal_duplicate_and_non_source(self):
        for path in ('../bot/RobotPlayer.java', '/bot/RobotPlayer.java', 'bot/A.class', 'bot\\A.java'):
            self.error('source_rejected', self.store.capture, 'alice', 'run1', SourceExport('bot', archive(path=path)))
        self.error('source_rejected', self.store.capture, 'alice', 'run1', SourceExport('bot', archive(source=b'\x00')))
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as z:
            z.writestr('bot/A.java', SOURCE)
            z.writestr('bot/a.java', SOURCE)
        self.error('source_rejected', self.store.capture, 'alice', 'run1', SourceExport('bot', output.getvalue()))
        self.error('invalid_export', self.store.capture, 'alice', 'run1', SourceExport('bot', b'x' * 1048577))
        self.assertEqual(self.sql('SELECT count(*) FROM jobs'), [(0,)])

    def test_run_and_global_artifact_record_quotas_are_atomic(self):
        self.store = SourceArtifactStore(limits=ArtifactLimits(run_records=2, global_records=3))
        self.capture()
        self.capture()
        self.error('artifact_limit', self.capture)
        self.capture(owner='bob')
        self.error('artifact_limit', self.capture, owner='carol')

    def test_run_and_global_artifact_byte_quotas_charge_duplicate_snapshots(self):
        size = len(archive())
        store = SourceArtifactStore(limits=ArtifactLimits(run_archive_bytes=size, global_archive_bytes=2*size))
        source = SourceExport('bot', archive())
        store.capture('alice', 'run1', source)
        self.error('artifact_limit', store.capture, 'alice', 'run1', source)
        store.capture('alice', 'run2', source)
        self.error('artifact_limit', store.capture, 'bob', 'run1', source)

    def test_concurrent_capture_enforces_global_quota_and_unique_handles(self):
        store = SourceArtifactStore(limits=ArtifactLimits(global_records=4))
        def capture(index):
            try:
                return store.capture('alice', 'run1', SourceExport('bot', archive()))
            except ArtifactError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=12) as pool:
            outcomes = list(pool.map(capture, range(12)))
        handles = [value for value in outcomes if value.startswith('a_')]
        self.assertEqual((len(handles), len(set(handles)), outcomes.count('artifact_limit')), (4, 4, 8))

    def test_same_run_content_idempotency_survives_adapter_and_ledger_restart(self):
        old_handle = self.capture()
        identifier = self.submit(old_handle)
        self.ledger = DurableLedger(self.path, clock=lambda: self.now)
        self.store = SourceArtifactStore()
        self.adapter = DurableScrimmageAdapter(self.ledger, self.store, 'alice', 'run1')
        self.error('unknown_artifact', self.adapter.submit, SubmitRequest(old_handle, 'bot'))
        self.error('unknown_submission', self.adapter.result, ResultRequest(identifier))
        new_handle = self.capture(compression=zipfile.ZIP_DEFLATED)
        self.assertNotEqual(new_handle, old_handle)
        self.assertEqual(self.submit(new_handle), identifier)
        self.assertEqual(self.adapter.result(ResultRequest(identifier)).evidence['status'], 'accepted')
        self.assertEqual(self.sql('SELECT count(*) FROM jobs'), [(1,)])

    def test_different_source_run_or_owner_is_a_separate_job(self):
        first = self.submit()
        second = self.submit(self.capture(SOURCE + b' '))
        handle = self.capture(run='run2')
        second_run = DurableScrimmageAdapter(self.ledger, self.store, 'alice', 'run2')
        third = second_run.submit(SubmitRequest(handle, 'bot')).submission_id
        handle = self.capture(owner='bob')
        bob = DurableScrimmageAdapter(self.ledger, self.store, 'bob', 'run1')
        fourth = bob.submit(SubmitRequest(handle, 'bot')).submission_id
        self.assertEqual(len({first, second, third, fourth}), 4)

    def test_repeat_submit_charges_ledger_rate_without_duplicate_job(self):
        path = Path(self.temp.name) / 'limited.db'
        ledger = DurableLedger(path, limits=LedgerLimits(owner_submits_per_window=2), clock=lambda: self.now)
        adapter = DurableScrimmageAdapter(ledger, self.store, 'alice', 'run1')
        request = SubmitRequest(self.capture(), 'bot')
        first = adapter.submit(request)
        self.assertEqual(adapter.submit(request), first)
        self.error('rate_limit', adapter.submit, request)

    def test_ledger_quota_failure_does_not_publish_a_submission_or_damage_artifact(self):
        path = Path(self.temp.name) / 'pending.db'
        ledger = DurableLedger(path, limits=LedgerLimits(owner_pending=1), clock=lambda: self.now)
        adapter = DurableScrimmageAdapter(ledger, self.store, 'alice', 'run1')
        identifier = adapter.submit(SubmitRequest(self.capture(), 'bot')).submission_id
        handle = self.capture(SOURCE + b' ')
        self.error('pending_limit', adapter.submit, SubmitRequest(handle, 'bot'))
        claim = ledger.reserve_job('alice', identifier, 'worker')
        ledger.finish('alice', identifier, claim.worker, claim.generation,
                      {'status': 'rejected', 'reason': 'compile_rejected'})
        second = adapter.submit(SubmitRequest(handle, 'bot'))
        self.assertNotEqual(second.submission_id, identifier)

    def test_worker_failure_and_restart_recovery_remain_unscored(self):
        identifier = self.submit()
        claim = self.ledger.reserve_job('alice', identifier, 'worker')
        self.now = claim.lease_until
        self.ledger = DurableLedger(self.path, clock=lambda: self.now)
        self.adapter = DurableScrimmageAdapter(self.ledger, self.store, 'alice', 'run1')
        # Re-submit immutable content to recover this run's opaque job reference.
        self.assertEqual(self.submit(), identifier)
        result = self.adapter.result(ResultRequest(identifier)).evidence
        self.assertEqual((result['status'], result['reason']), ('infrastructure_failure', 'worker_unavailable'))
        self.assertNotIn('summary', result)
        self.error('terminal_conflict', self.ledger.finish, 'alice', identifier, claim.worker, claim.generation, complete())

    def test_invalid_worker_evidence_remains_a_failure_through_controller_projection(self):
        identifier = self.submit()
        claim = self.ledger.reserve_job('alice', identifier, 'worker')
        self.ledger.finish('alice', identifier, claim.worker, claim.generation, {'status': 'complete'})
        result = self.adapter.result(ResultRequest(identifier)).evidence
        self.assertEqual(result['reason'], 'invalid_evidence')
        self.assertNotIn('summary', result)
        self.assertNotIn('validated_complete', result)

    def test_adapter_errors_are_closed_in_controller_output(self):
        self.backend.reply = ExportedExecReply('succeeded', source=SourceExport('bot', b'private invalid zip'))
        report = self.run_steps(TurnReply((exec_call(),)), TurnReply(final_text='done'))
        self.assertEqual(results(report)[0], {'status': 'infrastructure_failure', 'reason': 'adapter_failure'})
        self.assertNotIn('private', b'\n'.join(report.transcript).decode())

    def test_malformed_direct_requests_configuration_and_real_launch_are_closed(self):
        for request in ({'artifact_id': FAKE, 'package': 'bot'}, None):
            self.error('invalid_request', self.adapter.submit, request)
        self.error('invalid_request', self.adapter.result, {'submission_id': 'x'})
        self.error('invalid_context', self.store.capture, '../owner', 'run1', SourceExport('bot', archive()))
        other = SourceArtifactStore(intake=replace(self.store.intake, max_files=1))
        self.error('invalid_configuration', DurableScrimmageAdapter, self.ledger, other, 'alice', 'run1')
        larger = DurableLedger(Path(self.temp.name) / 'larger.db', limits=LedgerLimits(max_games=129))
        self.error('invalid_configuration', DurableScrimmageAdapter, larger, self.store, 'alice', 'run1')
        for value in (True, 0, -1):
            with self.assertRaises(ValueError):
                ArtifactLimits(run_records=value)
        with self.assertRaisesRegex(RuntimeError, 'real_launch_unsupported'):
            launch_real(adapter=self.adapter, validated=True)
        with self.assertRaises(BoundaryUnverified):
            require_heldout_attestation()


if __name__ == '__main__':
    unittest.main()
