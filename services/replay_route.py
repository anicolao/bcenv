"""Offline durable replay route. Shipped callback tests are synthetic fixtures.

A separately approved public-artifact harness may inject a fixed offline parser;
that remains trusted external execution, not an implemented service boundary.

Identity echoes and first fingerprints are trusted assertions, never proof that a
real game or parser ran. No listener, authentication, paths, engine or RPC exists.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Protocol

from services.controller import ResultRequest, SubmitRequest
from services.controller_ledger import ArtifactError, DurableScrimmageAdapter, SourceArtifactStore
from services.ledger import LedgerError, _context
from services.replay_feedback_v2 import (FeedbackLimits, FeedbackRejected, ReplayPageQuery,
                                         project_replay_feedback_page)
from services.replay_protocol import (ReplayReply, ReplayRequest, encoded, parse_request,
                                      project_reply, unavailable)
from services.replay_store import PreparedReplay, ReplayStore


@dataclass(frozen=True)
class ReplayProviderRequest:
    source_bytes: bytes
    source_sha256: str
    expected_spec_version: str
    producer_identity: str
    query: ReplayPageQuery


@dataclass(frozen=True)
class ReplayProviderReceipt:
    query_json: bytes
    source_sha256: str
    spec_version: str
    producer_identity: str


class SyntheticReplayProvider(Protocol):
    def query(self, request: ReplayProviderRequest) -> ReplayProviderReceipt: ...


@dataclass(frozen=True)
class ReplayFixture:
    registration_key: str
    source: bytes
    own_team_id: int


class SyntheticReplayRoute:
    def __init__(self, store, provider):
        if type(store) is not ReplayStore:
            raise LedgerError('invalid_request')
        self.store, self.provider = store, provider
        self.projection_limits = FeedbackLimits(max_records=store.limits.max_records,
            max_output_encoded_bytes=store.limits.payload_bytes - 1024)

    def _project(self, source, digest, own_team, query, submission, job):
        request = ReplayProviderRequest(source, digest, self.store.policy.spec_version,
                                        self.store.policy.producer_identity, query)
        receipt = self.provider.query(request)
        if (type(receipt) is not ReplayProviderReceipt or type(receipt.query_json) is not bytes or
                len(receipt.query_json) > self.projection_limits.max_input_encoded_bytes or
                receipt.source_sha256 != digest or type(receipt.source_sha256) is not str or
                receipt.spec_version != request.expected_spec_version or type(receipt.spec_version) is not str or
                receipt.producer_identity != request.producer_identity or type(receipt.producer_identity) is not str):
            raise LedgerError('invalid_evidence')
        return project_replay_feedback_page(receipt.query_json, submission_id=submission, job_id=job,
            own_team_id=own_team, expected_spec_version=request.expected_spec_version,
            expected_query=query, limits=self.projection_limits)

    def complete_with_replay(self, principal, run_id, reservation, evidence, fixtures):
        """Only registration path: one authoritative transaction completes + binds.

        Preflight is racy; callback work has no physical lock or preemption. The
        final transaction rejects stale/expired claims. Failure leaves no partial
        source publication and does not turn the job into a score or auto-retry it.
        """
        if type(fixtures) is not tuple or not 1 <= len(fixtures) <= self.store.limits.sources_per_job:
            raise LedgerError('invalid_request')
        keys = set()
        for fixture in fixtures:
            if (type(fixture) is not ReplayFixture or type(fixture.source) is not bytes or
                    not 1 <= len(fixture.source) <= self.store.limits.source_bytes or
                    type(fixture.own_team_id) is not int or fixture.own_team_id not in (1, 2)):
                raise LedgerError('invalid_evidence')
            _context(fixture.registration_key)
            if fixture.registration_key in keys:
                raise LedgerError('invalid_evidence')
            keys.add(fixture.registration_key)
        job, prior = self.store.completion_state(principal, run_id, reservation, evidence,
            tuple((fixture.registration_key, fixture.source, fixture.own_team_id) for fixture in fixtures))
        if prior is not None:
            return prior
        prepared = []
        try:
            for fixture in fixtures:
                page = json.loads(self._project(fixture.source, hashlib.sha256(fixture.source).hexdigest(),
                    fixture.own_team_id, ReplayPageQuery(0, 0, None, 0, 1), reservation.submission_id, job))
                rounds = max(row['reported_total_rounds'] or 0 for row in page['matches'])
                prepared.append(PreparedReplay(fixture.registration_key, fixture.source,
                    fixture.own_team_id, len(page['matches']), rounds))
        except Exception:
            raise LedgerError('invalid_evidence') from None
        # Never catch/normalize an uncertain commit as a callback failure or retry.
        return self.store.complete(principal, run_id, reservation, evidence, tuple(prepared))

    def replay(self, principal, run_id, request):
        if type(request) is not ReplayRequest:
            raise LedgerError('invalid_request')
        parse_request(asdict(request))
        try:
            if request.operation == 'manifest':
                result = self.store.manifest(principal, run_id, request.submission_id,
                                              request.manifest_offset, request.manifest_limit)
                try:
                    return ReplayReply(encoded(project_reply(ReplayReply(encoded(result)), request)))
                except Exception:
                    return ReplayReply(unavailable(request.submission_id, 'invalid_evidence'))
            if request.operation == 'window':
                claim = self.store.reserve_page(principal, run_id, request.submission_id,
                    replay_id=request.replay_id, round_start=request.round_start, round_end=request.round_end,
                    robot_id=request.robot_id, record_limit=request.record_limit)
            else:
                claim = self.store.reserve_page(principal, run_id, request.submission_id,
                    view_id=request.view_id, record_offset=request.record_offset)
            try:
                page_bytes = self._project(claim.source, claim.source_sha256, claim.own_team_id,
                    ReplayPageQuery(claim.round_start, claim.round_end, claim.robot_id, claim.record_offset, claim.record_limit),
                    claim.submission_id, claim.job_id)
                result = {'schema': 'bcenv.replay-route.v2', 'submission_id': claim.submission_id,
                          'status': 'available', 'operation': request.operation, 'replay_id': claim.replay_id,
                          'view_id': claim.view_id, 'page': json.loads(page_bytes)}
                public = ReplayReply(encoded(project_reply(ReplayReply(encoded(result)), request)))
                if len(public.payload) > self.store.limits.payload_bytes:
                    raise LedgerError('invalid_evidence')
            except Exception:
                self.store.finish_page(principal, run_id, claim)  # failure is fenced too
                return ReplayReply(unavailable(request.submission_id, 'invalid_evidence'))
            self.store.finish_page(principal, run_id, claim, page_bytes)
            return public
        except LedgerError as error:
            # A storage-unavailable reply is an UNKNOWN publication outcome. No
            # implicit retry is performed; durable attempts remain charged.
            return ReplayReply(unavailable(request.submission_id, error.code))


class ReplayScrimmageAdapter:
    """Explicit v2 adapter, fixed trusted owner/run and provider. No ephemeral
    submission set: durable run bindings govern post-restart visibility.
    """
    def __init__(self, route, artifacts, principal, run_id):
        if (type(route) is not SyntheticReplayRoute or type(artifacts) is not SourceArtifactStore or
                route.store.ledger.intake != artifacts.intake):
            raise ArtifactError('invalid_configuration')
        self.route, self.artifacts = route, artifacts
        self.principal, self.run_id = _context(principal), _context(run_id)
        self._legacy = DurableScrimmageAdapter(route.store.ledger, artifacts, principal, run_id)

    def submit(self, request):
        if type(request) is not SubmitRequest:
            raise ArtifactError('invalid_request')
        source = self.artifacts.resolve(self.principal, self.run_id, request.artifact_id)
        if type(request.package) is not str or request.package != source.package:
            raise ArtifactError('package_mismatch')
        key = 'replay_' + hashlib.sha256(encoded(['bcenv.controller-ledger.v2', self.run_id,
            source.content_sha256, self.route.store.policy_json])).hexdigest()
        return self._legacy._reply(self.route.store.submit(self.principal, self.run_id, key, source.archive, source.package))

    def result(self, request):
        if type(request) is not ResultRequest:
            raise ArtifactError('invalid_request')
        return self._legacy._reply(self.route.store.result(self.principal, self.run_id, request.submission_id))

    def replay(self, request):
        if type(request) is ResultRequest:
            # Even this opt-in adapter cannot open a v1 controller replay route.
            self.route.store.result(self.principal, self.run_id, request.submission_id)
            return None
        return self.route.replay(self.principal, self.run_id, request)
