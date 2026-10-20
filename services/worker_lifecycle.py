"""Synthetic worker lifecycle seam. No engine, provider, scheduler or supervision.

A valid callback receipt may be completely fabricated. Identity echoes only bind
it consistently to this synthetic request; they do not establish game evidence,
authenticity, source execution, isolation or actual resource release.
"""
from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Protocol

from services.ledger import DurableLedger
from services.scrimmage import AcceptedSubmission, public_result

_RECEIPT_SCHEMA = 'bcenv.synthetic-worker-receipt.v1'
_RECEIPT_BYTES = 4096
_RECEIPT_DEPTH = 4
_WORKER = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
_FAILURES = frozenset(('worker_unavailable', 'resource_limit', 'invalid_evidence',
                      'boundary_unverified', 'internal_error'))


@dataclass(frozen=True)
class SyntheticJobSpecification:
    """One fixed inert test profile, never constructed from model input."""
    schema: str = field(default='bcenv.synthetic-job.v1', init=False)
    profile: str = field(default='inert-single-game', init=False)
    games: int = field(default=1, init=False)
    sha256: str = field(init=False)

    def __post_init__(self):
        encoded = json.dumps({'schema': self.schema, 'profile': self.profile, 'games': self.games},
                             sort_keys=True, separators=(',', ':')).encode('ascii')
        object.__setattr__(self, 'sha256', hashlib.sha256(encoded).hexdigest())


@dataclass(frozen=True)
class SyntheticWorkRequest:
    submission_id: str
    generation: int
    source: AcceptedSubmission
    specification: SyntheticJobSpecification


class SyntheticWorker(Protocol):
    def __call__(self, request: SyntheticWorkRequest) -> bytes: ...


class InvalidReceipt(ValueError):
    """Internal parser failure; text and callback bytes are never published."""


def _pairs(pairs):
    if len(pairs) > 8:
        raise InvalidReceipt()
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidReceipt()
        result[key] = value
    return result


def _constant(value):
    raise InvalidReceipt()


def _bounded_json(data):
    if type(data) is not bytes or len(data) > _RECEIPT_BYTES:
        raise InvalidReceipt()
    text = data.decode('utf-8')
    depth, quoted, escaped = 0, False, False
    # Bound containers before allocating a decoded JSON tree. Delimiters inside
    # JSON strings/escapes do not contribute to structural depth.
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == '\\':
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in '[{':
            depth += 1
            if depth > _RECEIPT_DEPTH:
                raise InvalidReceipt()
        elif character in ']}':
            depth -= 1
            if depth < 0:
                raise InvalidReceipt()
    if depth != 0 or quoted:
        raise InvalidReceipt()
    return json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)


def _normalize(request, receipt):
    failure = {'status': 'infrastructure_failure', 'reason': 'invalid_evidence'}
    try:
        value = _bounded_json(receipt)
        if (type(value) is not dict or set(value) != {'schema', 'submission_id', 'generation',
                'source_sha256', 'spec_sha256', 'outcome'} or
                value['schema'] != _RECEIPT_SCHEMA or value['submission_id'] != request.submission_id or
                type(value['generation']) is not int or value['generation'] != request.generation or
                value['source_sha256'] != request.source.content_sha256 or
                value['spec_sha256'] != request.specification.sha256):
            return failure
        outcome = value['outcome']
        if type(outcome) is not dict:
            return failure
        status = outcome.get('status')
        if status == 'complete':
            if set(outcome) != {'status', 'summary'}:
                return failure
            summary = outcome['summary']
            if (type(summary) is not dict or set(summary) != {'games', 'wins', 'draws', 'losses'} or
                    any(type(count) is not int or not 0 <= count <= 1 for count in summary.values()) or
                    summary['games'] != request.specification.games or
                    summary['wins'] + summary['draws'] + summary['losses'] != summary['games']):
                return failure
            evidence = {'status': 'complete', 'validated_complete': True, 'summary': summary}
        elif status == 'rejected':
            if set(outcome) != {'status', 'reason'} or outcome['reason'] != 'compile_rejected':
                return failure
            evidence = {'status': status, 'reason': 'compile_rejected'}
        elif status == 'infrastructure_failure':
            if (set(outcome) != {'status', 'reason'} or type(outcome['reason']) is not str or
                    outcome['reason'] not in _FAILURES):
                return failure
            evidence = {'status': status, 'reason': outcome['reason']}
        else:
            return failure
        result = public_result(request.submission_id, evidence, max_games=1, max_bytes=_RECEIPT_BYTES)
        if result['status'] == 'complete':
            result['validated_complete'] = True
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return failure


class SyntheticWorkerBridge:
    """At most one callback attempt per successful call to run_job.

    The callback is trusted injected test code, never a model-selected command.
    No exception from a ledger operation is caught, retried or reinterpreted as
    successful completion; an uncertain commit needs explicit reconciliation.
    """
    def __init__(self, ledger, callback, *, worker='synthetic-worker'):
        if (type(ledger) is not DurableLedger or not callable(callback) or
                type(worker) is not str or not _WORKER.fullmatch(worker)):
            raise ValueError('invalid_worker_configuration')
        self.ledger, self.callback, self.worker = ledger, callback, worker
        self.specification = SyntheticJobSpecification()

    def run_job(self, principal, submission_id):
        claim = self.ledger.reserve_job(principal, submission_id, self.worker)
        checked = self.ledger.check_reservation(principal, claim.submission_id,
                                               self.worker, claim.generation)
        # The check-to-callback gap is not atomic. A concurrent terminal write,
        # clock change or expiry can invalidate this claim after this line.
        request = SyntheticWorkRequest(checked.submission_id, checked.generation,
                                       checked.source, self.specification)
        try:
            receipt = self.callback(request)
        except Exception:
            evidence = {'status': 'infrastructure_failure', 'reason': 'internal_error'}
        else:
            evidence = _normalize(request, receipt)
        # This transaction is the authoritative post-callback fence. Keep it
        # outside exception normalization: uncertain writes must not be retried.
        return self.ledger.finish(principal, checked.submission_id, self.worker,
                                  checked.generation, evidence)
