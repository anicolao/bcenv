"""Non-executing, year-neutral scrimmage protocol scaffold (Python stdlib only).

No listener, authentication, durable storage, scheduler, compiler, engine, raw
replay access, or sandbox is provided. See contracts/scrimmage-v1.md. This code
must live on the trusted side of a separately validated deployment boundary.
"""
from collections import deque
from dataclasses import dataclass
import hashlib
import io
import json
import re
import stat
import struct
import threading
import time
import uuid
import zipfile
import zlib

SCHEMA = 'bcenv.scrimmage.v1'
_ID = re.compile(r's_[0-9a-f]{32}\Z')
_WORD = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')
_KEYWORDS = frozenset(('abstract assert boolean break byte case catch char class '
    'const continue default do double else enum extends final finally float for '
    'goto if implements import instanceof int interface long native new package '
    'private protected public return short static strictfp super switch synchronized '
    'this throw throws transient try void volatile while true false null _').split())
_WINDOWS_NAMES = frozenset(['con', 'prn', 'aux', 'nul'] +
    [prefix + str(n) for prefix in ('com', 'lpt') for n in range(1, 10)])
_REJECTIONS = frozenset(('invalid_archive', 'archive_limit', 'invalid_package',
                        'invalid_source', 'compile_rejected'))
_FAILURES = frozenset(('worker_unavailable', 'resource_limit', 'invalid_evidence',
                      'boundary_unverified', 'internal_error'))


class SubmissionRejected(ValueError):
    """Contains a closed, public-safe reason code, never attacker text or paths."""

    def __init__(self, code):
        self.code = code if code in _REJECTIONS else 'invalid_archive'
        super().__init__(self.code)


class QuotaExceeded(RuntimeError):
    pass


class BoundaryUnverified(RuntimeError):
    pass


@dataclass(frozen=True)
class IntakeLimits:
    max_archive_bytes: int = 1_048_576
    max_files: int = 128
    max_file_bytes: int = 524_288
    max_source_bytes: int = 2_097_152
    max_expansion_ratio: int = 100
    max_path_bytes: int = 240
    max_package_depth: int = 16

    def __post_init__(self):
        for value in vars(self).values():
            if type(value) is not int or value <= 0:
                raise ValueError('invalid_intake_limits')


@dataclass(frozen=True)
class SourceFile:
    path: str
    data: bytes


@dataclass(frozen=True)
class AcceptedSubmission:
    """Immutable in-memory normalized content, not a filesystem security claim."""
    package: str
    files: tuple
    content_sha256: str


def _word(value):
    return bool(_WORD.fullmatch(value)) and value not in _KEYWORDS and value.casefold() not in _WINDOWS_NAMES


def _package(package, limits):
    if (type(package) is not str or not package or
            len(package) > limits.max_path_bytes or
            len(package.split('.')) > limits.max_package_depth or
            not all(_word(part) for part in package.split('.'))):
        raise SubmissionRejected('invalid_package')
    return package.replace('.', '/') + '/'


def _path(info, prefix, limits):
    name = info.filename
    # Do not normalize hostile names into acceptable ones. Reject them.
    if (info.orig_filename != name or not name.isascii() or
            len(name) > limits.max_path_bytes or '\\' in name or
            not name.startswith(prefix) or not name.endswith('.java')):
        raise SubmissionRejected('invalid_archive')
    parts = name.split('/')
    if (len(parts) - 1 > limits.max_package_depth or
            not all(_word(part) for part in parts[:-1]) or
            not _word(parts[-1][:-5])):
        raise SubmissionRejected('invalid_archive')
    mode = (info.external_attr >> 16) & 0xffff
    if (stat.S_IFMT(mode) not in (0, stat.S_IFREG) or
            info.external_attr & 0x10 or info.is_dir()):
        raise SubmissionRejected('invalid_archive')
    return name


def _zip_directory(data, limits):
    # Narrow ZIP subset: no ZIP64, comments, prefix/trailer, multipart disks,
    # extra fields, encryption or data descriptors. These are unnecessary for
    # the normalized source ZIP supplied by bcenv's existing pack command.
    if len(data) < 22:
        raise SubmissionRejected('invalid_archive')
    end = struct.unpack('<4s4H2LH', data[-22:])
    signature, disk, central_disk, disk_count, count, size, offset, comment = end
    if (signature != b'PK\x05\x06' or disk or central_disk or comment or
            disk_count != count or offset + size != len(data) - 22):
        raise SubmissionRejected('invalid_archive')
    if count < 1 or count > limits.max_files:
        raise SubmissionRejected('archive_limit')
    # ZipFile otherwise materializes every central record before callers can
    # inspect infolist(). Do a bounded raw walk first: EOCD counts can lie.
    cursor = offset
    for _ in range(count):
        if cursor + 46 > offset + size or data[cursor:cursor + 4] != b'PK\x01\x02':
            raise SubmissionRejected('invalid_archive')
        name_size, extra_size, comment_size = struct.unpack_from('<3H', data, cursor + 28)
        if name_size > limits.max_path_bytes or extra_size or comment_size:
            raise SubmissionRejected('invalid_archive')
        cursor += 46 + name_size
        if cursor > offset + size:
            raise SubmissionRejected('invalid_archive')
    if cursor != offset + size:
        raise SubmissionRejected('invalid_archive')
    return count, size, offset


def accept_source_zip(data, package, limits=None):
    """Validate bytes without filesystem extraction or candidate execution.

    Content identity ignores ZIP timestamps, compression and entry order. Paths
    and exact UTF-8 source bytes remain identity inputs. This is layout/text
    validation only; Java syntax, actual declarations and bytecode need the
    season's compiler/verifier inside a separately isolated worker.
    """
    limits = limits or IntakeLimits()
    prefix = _package(package, limits)
    if type(data) is not bytes:
        raise SubmissionRejected('invalid_archive')
    if len(data) > limits.max_archive_bytes:
        raise SubmissionRejected('archive_limit')
    count, central_size, central_offset = _zip_directory(data, limits)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) != count:
                raise SubmissionRejected('invalid_archive')
            names, folded, total, metadata_size = set(), set(), 0, 0
            for info in entries:
                name = _path(info, prefix, limits)
                if name in names or name.casefold() in folded:
                    raise SubmissionRejected('invalid_archive')
                names.add(name)
                folded.add(name.casefold())
                # Reject ambiguous case in any directory, not only full paths.
                metadata_size += 46 + len(name)
                if (info.extra or info.comment or info.flag_bits & ~0x800 or
                        info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) or
                        info.extract_version > 20 or info.volume):
                    raise SubmissionRejected('invalid_archive')
                total += info.file_size
                if (info.file_size > limits.max_file_bytes or
                        total > limits.max_source_bytes or
                        info.file_size > max(1, info.compress_size) * limits.max_expansion_ratio):
                    raise SubmissionRejected('archive_limit')
            if metadata_size != central_size:
                raise SubmissionRejected('invalid_archive')
            components = {}
            for name in names:
                parts = name.split('/')
                for n in range(1, len(parts) + 1):
                    component = '/'.join(parts[:n])
                    previous = components.setdefault(component.casefold(), component)
                    if previous != component:
                        raise SubmissionRejected('invalid_archive')
            files, cursor = [], 0
            for info in sorted(entries, key=lambda item: item.header_offset):
                # Cross-check local metadata and prohibit gaps/overlap/junk.
                if info.header_offset != cursor or cursor + 30 > central_offset:
                    raise SubmissionRejected('invalid_archive')
                header = struct.unpack('<4s5H3L2H', data[cursor:cursor + 30])
                sig, version, flags, method, _, _, crc, packed, size, name_size, extra_size = header
                start = cursor + 30 + name_size + extra_size
                stop = start + packed
                if (sig != b'PK\x03\x04' or version > 20 or extra_size or
                        flags != info.flag_bits or method != info.compress_type or
                        crc != info.CRC or packed != info.compress_size or size != info.file_size or
                        data[cursor + 30:cursor + 30 + name_size] != info.filename.encode('ascii') or
                        stop > central_offset):
                    raise SubmissionRejected('invalid_archive')
                compressed = data[start:stop]
                if method == zipfile.ZIP_STORED:
                    source = compressed
                else:
                    inflater = zlib.decompressobj(-15)
                    source = inflater.decompress(compressed, size + 1)
                    if (not inflater.eof or inflater.unused_data or inflater.unconsumed_tail):
                        raise SubmissionRejected('invalid_archive')
                if len(source) != size or zlib.crc32(source) & 0xffffffff != crc:
                    raise SubmissionRejected('invalid_archive')
                try:
                    source.decode('utf-8')
                except UnicodeDecodeError:
                    raise SubmissionRejected('invalid_source') from None
                if not source or b'\x00' in source:
                    raise SubmissionRejected('invalid_source')
                files.append(SourceFile(info.filename, source))
                cursor = stop
            if cursor != central_offset:
                raise SubmissionRejected('invalid_archive')
    except SubmissionRejected:
        raise
    except (zipfile.BadZipFile, UnicodeError, ValueError, OSError, EOFError,
            NotImplementedError, struct.error, zlib.error):
        raise SubmissionRejected('invalid_archive') from None
    files = tuple(sorted(files, key=lambda item: item.path))
    manifest = {'schema': 'bcenv.scrimmage-source.v1', 'package': package,
                'files': [{'path': item.path, 'size': len(item.data),
                           'sha256': hashlib.sha256(item.data).hexdigest()} for item in files]}
    encoded = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return AcceptedSubmission(package, files, hashlib.sha256(encoded).hexdigest())


def _submission_id(value):
    if type(value) is not str or not _ID.fullmatch(value):
        raise LookupError('unknown_submission')
    return value


def public_result(submission_id, evidence, *, max_games=128, max_bytes=4096):
    """Project trusted normalized evidence onto a closed public schema.

    Never copy free text or unknown keys, including nested keys. This validates
    shape, not honesty: a trusted adapter must establish complete evidence.
    """
    result = {'schema': SCHEMA, 'submission_id': _submission_id(submission_id)}
    if type(evidence) is not dict:
        evidence = {}
    state = evidence.get('status')
    if type(state) is str and state == 'accepted':
        result['status'] = state
    elif type(state) is str and state == 'rejected':
        reason = evidence.get('reason')
        result.update(status=state, reason=reason if type(reason) is str and reason in _REJECTIONS else 'invalid_source')
    elif type(state) is str and state == 'infrastructure_failure':
        reason = evidence.get('reason')
        result.update(status=state, reason=reason if type(reason) is str and reason in _FAILURES else 'internal_error')
    elif type(state) is str and state == 'complete' and evidence.get('validated_complete') is True:
        summary = evidence.get('summary')
        fields = ('games', 'wins', 'draws', 'losses')
        if (type(summary) is dict and
                all(type(summary.get(key)) is int and 0 <= summary[key] <= max_games for key in fields) and
                summary['games'] > 0 and
                sum(summary[key] for key in fields[1:]) == summary['games']):
            result.update(status='complete', summary={key: summary[key] for key in fields})
    if 'status' not in result:
        result.update(status='infrastructure_failure', reason='invalid_evidence')
    encoded = json.dumps(result, sort_keys=True, separators=(',', ':')).encode('utf-8')
    if len(encoded) > max_bytes:
        raise ValueError('public_response_limit')
    return result


@dataclass(frozen=True)
class Quotas:
    submissions_per_window: int = 20
    submission_window_seconds: int = 3600
    reads_per_window: int = 120
    read_window_seconds: int = 60
    max_pending: int = 4
    max_records: int = 64
    max_games: int = 128
    max_public_bytes: int = 4096

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in vars(self).values()) or self.max_public_bytes < 512:
            raise ValueError('invalid_quotas')


class ScrimmageFacade:
    """Single-owner in-memory interface scaffold. Does not schedule/run anything.

    Trusted hosts own instances and invoke record_result/accepted_submission.
    Public clients are limited to submit/result/replay. IDs are not authorization.
    Restart loses all state; deployments must supply durable quotas and ownership.
    """

    def __init__(self, *, limits=None, quotas=None, clock=time.monotonic):
        self.limits = limits or IntakeLimits()
        self.quotas = quotas or Quotas()
        self._clock = clock
        self._records = {}
        self._submissions = deque()
        self._reads = deque()
        self._lock = threading.RLock()

    def _charge(self, events, maximum, window):
        now = self._clock()
        while events and events[0] <= now - window:
            events.popleft()
        if len(events) >= maximum:
            raise QuotaExceeded('rate_limit')
        events.append(now)

    def _lookup(self, submission_id):
        _submission_id(submission_id)
        if submission_id not in self._records:
            raise LookupError('unknown_submission')
        return self._records[submission_id]

    def submit(self, archive, package):
        with self._lock:
            self._charge(self._submissions, self.quotas.submissions_per_window, self.quotas.submission_window_seconds)
            if len(self._records) >= self.quotas.max_records:
                raise QuotaExceeded('record_limit')
            if sum(item[1]['status'] == 'accepted' for item in self._records.values()) >= self.quotas.max_pending:
                raise QuotaExceeded('pending_limit')
            submission_id = 's_' + uuid.uuid4().hex
            try:
                artifact = accept_source_zip(archive, package, self.limits)
                evidence = {'status': 'accepted'}
            except SubmissionRejected as error:
                artifact = None
                evidence = {'status': 'rejected', 'reason': error.code}
            response = self._project(submission_id, evidence)
            self._records[submission_id] = (artifact, response)
            return dict(response)

    def _project(self, submission_id, evidence):
        return public_result(submission_id, evidence, max_games=self.quotas.max_games,
                             max_bytes=self.quotas.max_public_bytes)

    def result(self, submission_id):
        with self._lock:
            self._charge(self._reads, self.quotas.reads_per_window, self.quotas.read_window_seconds)
            # Return a detached JSON object, including nested summary objects.
            return json.loads(json.dumps(self._lookup(submission_id)[1]))

    def replay(self, submission_id):
        with self._lock:
            self._charge(self._reads, self.quotas.reads_per_window, self.quotas.read_window_seconds)
            self._lookup(submission_id)
            return {'schema': SCHEMA, 'submission_id': submission_id,
                    'status': 'unavailable', 'reason': 'replay_withheld'}

    def accepted_submission(self, submission_id):
        """Trusted-side accessor. Never expose source/identity via public routes."""
        with self._lock:
            return self._lookup(submission_id)[0]

    def record_result(self, submission_id, evidence):
        """Trusted-side terminal report. First terminal report is immutable."""
        with self._lock:
            artifact, current = self._lookup(submission_id)
            if current['status'] != 'accepted':
                raise ValueError('terminal_result')
            response = self._project(submission_id, evidence)
            if response['status'] == 'accepted':
                raise ValueError('terminal_report_required')
            self._records[submission_id] = (artifact, response)
            return json.loads(json.dumps(response))


@dataclass(frozen=True)
class BoundaryAttestation:
    deployment_id: str
    issued_at: int
    expires_at: int
    tool_boundary_validated: bool
    filesystem_boundary_validated: bool
    network_boundary_validated: bool
    evidence_sha256: str


def require_heldout_attestation(attestation=None, *, deployment_id=None):
    """Reserved typed integration hook: real-heldout access is unsupported.

    Always fails closed, even for an all-true, fresh-looking attestation. These
    fields are claims, not evidence of tool/filesystem/network isolation. There
    is deliberately no flag or callback that can enable heldouts in this module.
    A future trusted deployment needs independently validated enforcement and
    attestation verification before replacing this unsupported hook.
    """
    raise BoundaryUnverified('boundary_unverified')
