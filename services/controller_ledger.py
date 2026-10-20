"""Synthetic six-tool composition with immutable, scoped source artifacts.

The export side channel is a trusted adapter assertion, not proof of isolation,
process exit or safe filesystem capture. This module opens no candidate paths,
executes no commands and implements no authentication, provider or real launcher.
"""
from dataclasses import dataclass
import hashlib
import json
import re
import threading
from typing import Protocol
import uuid

from services.controller import (ExecReply, ExecRequest, ReadRequest, ResultRequest,
                                 ScrimmageReply, SubmitRequest, WriteRequest)
from services.ledger import DurableLedger
from services.scrimmage import IntakeLimits, SubmissionRejected, accept_source_zip, public_result

_CONTEXT = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
_ARTIFACT = re.compile(r'a_[0-9a-f]{32}\Z')
_SUBMISSION = re.compile(r's_[0-9a-f]{32}\Z')
_CODES = frozenset(('invalid_context', 'invalid_request', 'invalid_export',
                    'source_rejected', 'artifact_limit', 'unknown_artifact',
                    'unknown_submission', 'package_mismatch', 'invalid_configuration'))


class ArtifactError(ValueError):
    def __init__(self, code):
        self.code = code if code in _CODES else 'invalid_export'
        super().__init__(self.code)


def _context(value):
    if type(value) is not str or not _CONTEXT.fullmatch(value):
        raise ArtifactError('invalid_context')
    return value


@dataclass(frozen=True)
class ArtifactLimits:
    run_records: int = 16
    global_records: int = 64
    run_archive_bytes: int = 8 * 1048576
    global_archive_bytes: int = 64 * 1048576

    def __post_init__(self):
        if any(type(v) is not int or not 0 < v < 2**60 for v in vars(self).values()):
            raise ValueError('invalid_artifact_limits')


@dataclass(frozen=True)
class SourceExport:
    """Trusted-side asserted snapshot; exact immutable bytes required at capture."""
    package: str
    archive: bytes


@dataclass(frozen=True)
class ExportedExecReply:
    """No caller-selected artifact ID or path can be provided in this reply."""
    status: str
    stdout: bytes = b''
    stderr: bytes = b''
    source: SourceExport | None = None


@dataclass(frozen=True)
class SourceSnapshot:
    package: str
    archive: bytes
    content_sha256: str


@dataclass(frozen=True)
class _OwnedArtifact:
    principal: str
    run_id: str
    snapshot: SourceSnapshot


class SourceArtifactStore:
    """Bounded process-local snapshots. Host-supplied scopes are not credentials."""
    def __init__(self, *, limits=None, intake=None):
        self.limits = limits or ArtifactLimits()
        self.intake = intake or IntakeLimits()
        self._artifacts = {}
        self._lock = threading.Lock()

    def capture(self, principal, run_id, source):
        principal, run_id = _context(principal), _context(run_id)
        if (type(source) is not SourceExport or type(source.archive) is not bytes or
                type(source.package) is not str or
                len(source.archive) > self.intake.max_archive_bytes or
                len(source.package) > self.intake.max_path_bytes):
            raise ArtifactError('invalid_export')
        # No handle is published until all validation and quota checks finish.
        try:
            accepted = accept_source_zip(source.archive, source.package, self.intake)
        except SubmissionRejected:
            raise ArtifactError('source_rejected') from None
        snapshot = SourceSnapshot(accepted.package, source.archive, accepted.content_sha256)
        with self._lock:
            owned = [item for item in self._artifacts.values()
                     if (item.principal, item.run_id) == (principal, run_id)]
            if (len(owned) >= self.limits.run_records or
                    len(self._artifacts) >= self.limits.global_records or
                    sum(len(item.snapshot.archive) for item in owned) + len(snapshot.archive) > self.limits.run_archive_bytes or
                    sum(len(item.snapshot.archive) for item in self._artifacts.values()) + len(snapshot.archive) > self.limits.global_archive_bytes):
                raise ArtifactError('artifact_limit')
            identifier = 'a_' + uuid.uuid4().hex
            self._artifacts[identifier] = _OwnedArtifact(principal, run_id, snapshot)
            return identifier

    def resolve(self, principal, run_id, artifact_id):
        principal, run_id = _context(principal), _context(run_id)
        with self._lock:
            if type(artifact_id) is not str or not _ARTIFACT.fullmatch(artifact_id):
                raise ArtifactError('unknown_artifact')
            item = self._artifacts.get(artifact_id)
            if item is None or (item.principal, item.run_id) != (principal, run_id):
                raise ArtifactError('unknown_artifact')
            return item.snapshot


class ExportingSandbox(Protocol):
    def read(self, request: ReadRequest) -> bytes: ...
    def write(self, request: WriteRequest) -> None: ...
    def exec(self, request: ExecRequest) -> ExportedExecReply: ...


class CapturingSandboxAdapter:
    """Translate trusted byte exports into run-owned handles, never parse stdout."""
    def __init__(self, backend, artifacts, principal, run_id):
        self.backend, self.artifacts = backend, artifacts
        self.principal, self.run_id = _context(principal), _context(run_id)

    def read(self, request):
        return self.backend.read(request)

    def write(self, request):
        return self.backend.write(request)

    def exec(self, request):
        if type(request) is not ExecRequest:
            raise ArtifactError('invalid_request')
        reply = self.backend.exec(request)
        if (type(reply) is not ExportedExecReply or type(reply.status) is not str or
                reply.status not in ('succeeded', 'failed', 'timeout') or
                type(reply.stdout) is not bytes or type(reply.stderr) is not bytes or
                len(reply.stdout) > request.output_bytes or len(reply.stderr) > request.output_bytes or
                (reply.source is not None and reply.status != 'succeeded')):
            raise ArtifactError('invalid_export')
        identifier = None
        if reply.source is not None:
            identifier = self.artifacts.capture(self.principal, self.run_id, reply.source)
        return ExecReply(reply.status, reply.stdout, reply.stderr, identifier)


class DurableScrimmageAdapter:
    """Controller adapter; principal/run/store/ledger are fixed trusted context.

    Identical normalized content within one run is one durable job, even after
    trusted recapture. Artifact/submission visibility sets themselves are ephemeral.
    """
    def __init__(self, ledger, artifacts, principal, run_id):
        if (type(ledger) is not DurableLedger or type(artifacts) is not SourceArtifactStore or
                ledger.intake != artifacts.intake or ledger.limits.max_games > 128):
            raise ArtifactError('invalid_configuration')
        self.ledger, self.artifacts = ledger, artifacts
        self.principal, self.run_id = _context(principal), _context(run_id)
        self._submissions = set()
        self._lock = threading.Lock()

    def _reply(self, result):
        evidence = dict(result)
        # The ledger's complete state is a trusted normalized assertion. This
        # marker is needed for the controller's second closed projection; it
        # does not independently validate any engine/game evidence.
        evidence['validated_complete'] = result['status'] == 'complete'
        projected = public_result(result['submission_id'], evidence,
                                  max_games=self.ledger.limits.max_games,
                                  max_bytes=self.ledger.limits.max_public_bytes)
        if projected['status'] == 'complete':
            projected['validated_complete'] = True
        return ScrimmageReply(projected['submission_id'], projected)

    def submit(self, request):
        if type(request) is not SubmitRequest:
            raise ArtifactError('invalid_request')
        source = self.artifacts.resolve(self.principal, self.run_id, request.artifact_id)
        if type(request.package) is not str or request.package != source.package:
            raise ArtifactError('package_mismatch')
        identity = json.dumps(['bcenv.controller-ledger.v1', self.run_id, source.content_sha256],
                              separators=(',', ':')).encode('ascii')
        key = 'controller_' + hashlib.sha256(identity).hexdigest()
        result = self.ledger.submit(self.principal, key, source.archive, source.package)
        with self._lock:
            self._submissions.add(result['submission_id'])
        return self._reply(result)

    def _known(self, request):
        if type(request) is not ResultRequest:
            raise ArtifactError('invalid_request')
        with self._lock:
            if (type(request.submission_id) is not str or not _SUBMISSION.fullmatch(request.submission_id) or
                    request.submission_id not in self._submissions):
                raise ArtifactError('unknown_submission')
        return request.submission_id

    def result(self, request):
        return self._reply(self.ledger.result(self.principal, self._known(request)))

    def replay(self, request):
        self.ledger.replay(self.principal, self._known(request))
        return None  # Existing controller contract deliberately withholds replay.
