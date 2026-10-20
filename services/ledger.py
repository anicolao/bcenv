"""Synthetic SQLite ownership/job ledger; no authentication or execution.

Principal and worker names are trusted test context, not proof of identity. The
host owns the database, configuration, clock and adapters. No listener, scheduler,
credential, compiler, game, model or heldout execution is implemented here.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import uuid

from services.scrimmage import (AcceptedSubmission, IntakeLimits, SourceFile,
                               SubmissionRejected, accept_source_zip, public_result)

_APPLICATION_ID = 0x4243454E
_SCHEMA_VERSION = 1
_NAME = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
_KEY = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_ID = re.compile(r's_[0-9a-f]{32}\Z')
_TERMINAL = ('complete', 'rejected', 'infrastructure_failure')
_CODES = frozenset(('invalid_context', 'invalid_request', 'request_limit', 'not_found',
    'idempotency_conflict', 'rate_limit', 'record_limit', 'source_limit', 'pending_limit',
    'active_limit', 'job_not_queued', 'stale_reservation', 'terminal_conflict',
    'invalid_terminal_result', 'storage_unavailable', 'storage_integrity',
    'incompatible_database', 'configuration_mismatch', 'invalid_clock', 'clock_regression'))


class LedgerError(RuntimeError):
    """A closed public-safe code, never a SQL message, path or owner identifier."""
    def __init__(self, code):
        self.code = code if code in _CODES else 'storage_unavailable'
        super().__init__(self.code)


@dataclass(frozen=True)
class LedgerLimits:
    owner_records: int = 64
    global_records: int = 256
    owner_pending: int = 4
    global_pending: int = 16
    owner_running: int = 2
    global_running: int = 4
    owner_source_bytes: int = 16 * 1048576
    global_source_bytes: int = 64 * 1048576
    owner_submits_per_window: int = 20
    global_submits_per_window: int = 200
    submit_window_seconds: int = 3600
    owner_reads_per_window: int = 120
    global_reads_per_window: int = 1200
    read_window_seconds: int = 60
    lease_seconds: int = 30
    max_games: int = 128
    max_public_bytes: int = 4096

    def __post_init__(self):
        if (any(type(value) is not int or not 0 < value < 2**60 for value in vars(self).values()) or
                self.max_public_bytes < 512):
            raise ValueError('invalid_ledger_limits')


@dataclass(frozen=True)
class JobReservation:
    """Trusted-side data. Generation is a non-secret fencing integer."""
    submission_id: str
    generation: int
    worker: str
    lease_until: int
    source: AcceptedSubmission


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def _identity(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _manifest(source):
    return {'schema': 'bcenv.scrimmage-source.v1', 'package': source.package,
            'files': [{'path': item.path, 'size': len(item.data),
                       'sha256': hashlib.sha256(item.data).hexdigest()} for item in source.files]}


def _context(value):
    if type(value) is not str or not _NAME.fullmatch(value):
        raise LedgerError('invalid_context')
    return value


_SCHEMA = (
    'CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)',
    '''CREATE TABLE IF NOT EXISTS sources (
        digest TEXT PRIMARY KEY, package TEXT NOT NULL, manifest TEXT NOT NULL,
        source_bytes INTEGER NOT NULL CHECK(source_bytes > 0))''',
    '''CREATE TABLE IF NOT EXISTS source_files (
        source_digest TEXT NOT NULL REFERENCES sources(digest), path TEXT NOT NULL,
        digest TEXT NOT NULL, size INTEGER NOT NULL CHECK(size > 0), data BLOB NOT NULL,
        PRIMARY KEY(source_digest, path), CHECK(length(data) = size))''',
    '''CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, owner TEXT NOT NULL, idempotency_key TEXT NOT NULL,
        request_digest TEXT NOT NULL, source_digest TEXT REFERENCES sources(digest),
        source_bytes INTEGER NOT NULL CHECK(source_bytes >= 0),
        state TEXT NOT NULL CHECK(state IN ('queued','running','complete','rejected','infrastructure_failure')),
        generation INTEGER NOT NULL DEFAULT 0 CHECK(generation >= 0),
        worker TEXT, lease_until INTEGER, result_json TEXT,
        UNIQUE(owner, idempotency_key),
        CHECK((source_digest IS NULL) = (source_bytes = 0)),
        CHECK((state = 'running') = (lease_until IS NOT NULL)),
        CHECK((generation = 0 AND worker IS NULL) OR (generation > 0 AND worker IS NOT NULL)),
        CHECK((state IN ('complete','rejected','infrastructure_failure')) = (result_json IS NOT NULL)),
        CHECK(state != 'queued' OR generation = 0),
        CHECK(state != 'complete' OR source_digest IS NOT NULL))''',
    'CREATE INDEX IF NOT EXISTS jobs_owner_state ON jobs(owner, state)',
    '''CREATE TABLE IF NOT EXISTS rate_events (
        kind TEXT NOT NULL CHECK(kind IN ('submit','read')),
        owner TEXT NOT NULL, observed_at INTEGER NOT NULL)''',
    'CREATE INDEX IF NOT EXISTS rate_events_lookup ON rate_events(kind, owner, observed_at)',
    '''CREATE TRIGGER IF NOT EXISTS immutable_sources_update BEFORE UPDATE ON sources
       BEGIN SELECT RAISE(ABORT, 'immutable_source'); END''',
    '''CREATE TRIGGER IF NOT EXISTS immutable_sources_delete BEFORE DELETE ON sources
       BEGIN SELECT RAISE(ABORT, 'immutable_source'); END''',
    '''CREATE TRIGGER IF NOT EXISTS immutable_source_files_update BEFORE UPDATE ON source_files
       BEGIN SELECT RAISE(ABORT, 'immutable_source'); END''',
    '''CREATE TRIGGER IF NOT EXISTS immutable_source_files_delete BEFORE DELETE ON source_files
       BEGIN SELECT RAISE(ABORT, 'immutable_source'); END''',
)


class DurableLedger:
    """Small durable test API. Database paths/configuration are trusted inputs.

    Each operation uses a fresh connection and BEGIN IMMEDIATE, so quotas and
    transitions serialize across threads/processes. No open connection survives
    a call. The database must reside on a supported local SQLite filesystem.
    """
    def __init__(self, path, *, limits=None, intake=None, clock=time.time):
        if not isinstance(path, (str, Path)) or str(path) == ':memory:':
            raise LedgerError('invalid_request')
        self.path = Path(path).absolute()
        self.limits = limits or LedgerLimits()
        self.intake = intake or IntakeLimits()
        self.clock = clock
        self.configuration = _json({'schema': _SCHEMA_VERSION,
                                    'limits': asdict(self.limits), 'intake': asdict(self.intake)})
        connection = None
        try:
            connection = self._connect()
            # Identity, schema and configuration share one serialized snapshot,
            # including concurrent constructors on a previously empty file.
            connection.execute('BEGIN IMMEDIATE')
            app_id = connection.execute('PRAGMA application_id').fetchone()[0]
            tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if (app_id not in (0, _APPLICATION_ID) or (app_id == 0 and tables) or
                    (app_id == _APPLICATION_ID and version != _SCHEMA_VERSION)):
                raise LedgerError('incompatible_database')
            for statement in _SCHEMA:
                connection.execute(statement)
            existing = connection.execute("SELECT value FROM metadata WHERE key='configuration'").fetchone()
            if existing is not None and existing[0] != self.configuration:
                raise LedgerError('configuration_mismatch')
            connection.execute('INSERT OR IGNORE INTO metadata VALUES (?, ?)', ('configuration', self.configuration))
            connection.execute('INSERT OR IGNORE INTO metadata VALUES (?, ?)', ('clock', '0'))
            connection.execute('INSERT OR IGNORE INTO metadata VALUES (?, ?)', ('generation', '0'))
            connection.execute('PRAGMA application_id=' + str(_APPLICATION_ID))
            connection.execute('PRAGMA user_version=' + str(_SCHEMA_VERSION))
            connection.commit()
            connection.execute('PRAGMA journal_mode=WAL')
        except LedgerError:
            if connection is not None:
                connection.rollback()
            raise
        except sqlite3.Error:
            if connection is not None:
                connection.rollback()
            raise LedgerError('storage_unavailable') from None
        finally:
            if connection is not None:
                connection.close()

    def _connect(self):
        connection = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA synchronous=FULL')
        except sqlite3.Error:
            connection.close()
            raise
        return connection

    def _now(self, connection):
        observed = self.clock()
        if type(observed) not in (int, float) or not 0 <= observed < 2**60 or not math.isfinite(observed):
            raise LedgerError('invalid_clock')
        previous = json.loads(connection.execute("SELECT value FROM metadata WHERE key='clock'").fetchone()[0])
        if type(previous) not in (int, float) or not math.isfinite(previous) or not 0 <= previous < 2**60:
            raise LedgerError('storage_integrity')
        now = int(observed)
        if observed < previous:
            # A high-water clamp alone would extend real elapsed leases. Fail
            # all in-flight claims and refuse work until trusted time catches up.
            self._expire(connection, previous, all_running=True)
            raise LedgerError('clock_regression')
        connection.execute("UPDATE metadata SET value=? WHERE key='clock'", (str(observed),))
        return now

    def _project(self, identifier, evidence):
        return public_result(identifier, evidence, max_games=self.limits.max_games,
                             max_bytes=self.limits.max_public_bytes)

    def _expire(self, connection, now, *, all_running=False):
        if all_running:
            rows = connection.execute("SELECT id FROM jobs WHERE state='running'").fetchall()
        else:
            rows = connection.execute("SELECT id FROM jobs WHERE state='running' AND lease_until<=?", (now,)).fetchall()
        for row in rows:
            result = self._project(row['id'], {'status': 'infrastructure_failure', 'reason': 'worker_unavailable'})
            connection.execute("UPDATE jobs SET state='infrastructure_failure', lease_until=NULL, result_json=? WHERE id=?",
                               (_json(result), row['id']))
        return len(rows)

    def _run(self, operation, *, recover=True):
        connection = None
        try:
            connection = self._connect()
            connection.execute('BEGIN IMMEDIATE')
            now = self._now(connection)
            expired = self._expire(connection, now) if recover else 0
            result = operation(connection, now, expired)
            connection.commit()
            return result
        except LedgerError:
            # Expected denials retain rate charges, clock observations and
            # recovery. Such checks all precede source/job payload mutations.
            if connection is not None:
                try:
                    connection.commit()
                except sqlite3.Error:
                    connection.rollback()
                    raise LedgerError('storage_unavailable') from None
            raise
        except sqlite3.Error:
            if connection is not None:
                connection.rollback()
            raise LedgerError('storage_unavailable') from None
        except Exception:
            if connection is not None:
                connection.rollback()
            raise LedgerError('storage_integrity') from None
        finally:
            if connection is not None:
                connection.close()

    def _charge(self, connection, owner, kind, now):
        if kind == 'submit':
            window = self.limits.submit_window_seconds
            owner_cap, global_cap = self.limits.owner_submits_per_window, self.limits.global_submits_per_window
        else:
            window = self.limits.read_window_seconds
            owner_cap, global_cap = self.limits.owner_reads_per_window, self.limits.global_reads_per_window
        connection.execute('DELETE FROM rate_events WHERE kind=? AND observed_at<=?', (kind, now - window))
        total = connection.execute('SELECT count(*) FROM rate_events WHERE kind=?', (kind,)).fetchone()[0]
        own = connection.execute('SELECT count(*) FROM rate_events WHERE kind=? AND owner=?', (kind, owner)).fetchone()[0]
        if total >= global_cap or own >= owner_cap:
            raise LedgerError('rate_limit')
        connection.execute('INSERT INTO rate_events VALUES (?, ?, ?)', (kind, owner, now))

    def _owned(self, connection, owner, identifier):
        if type(identifier) is not str or not _ID.fullmatch(identifier):
            raise LedgerError('not_found')
        row = connection.execute('SELECT * FROM jobs WHERE id=? AND owner=?', (identifier, owner)).fetchone()
        if row is None:
            raise LedgerError('not_found')
        return row

    def _public(self, row):
        if row['state'] in ('queued', 'running'):
            return self._project(row['id'], {'status': 'accepted'})
        result = json.loads(row['result_json'])
        if result.get('submission_id') != row['id'] or result.get('status') != row['state']:
            raise LedgerError('storage_integrity')
        # Reproject stored JSON; unknown fields never become a leak after restart.
        result['validated_complete'] = result['status'] == 'complete'
        safe = self._project(row['id'], result)
        if safe['status'] != row['state']:
            raise LedgerError('storage_integrity')
        return safe

    def _source(self, connection, digest):
        source = connection.execute('SELECT * FROM sources WHERE digest=?', (digest,)).fetchone()
        rows = connection.execute('SELECT * FROM source_files WHERE source_digest=? ORDER BY path', (digest,)).fetchall()
        if source is None or not 1 <= len(rows) <= self.intake.max_files:
            raise LedgerError('storage_integrity')
        files = []
        total = 0
        for row in rows:
            data = row['data']
            if (type(data) is not bytes or len(data) != row['size'] or len(data) > self.intake.max_file_bytes or
                    hashlib.sha256(data).hexdigest() != row['digest']):
                raise LedgerError('storage_integrity')
            total += len(data)
            if total > self.intake.max_source_bytes:
                raise LedgerError('storage_integrity')
            files.append(SourceFile(row['path'], data))
        accepted = AcceptedSubmission(source['package'], tuple(files), digest)
        manifest = _manifest(accepted)
        if (total != source['source_bytes'] or _identity(manifest) != digest or
                _json(manifest) != source['manifest']):
            raise LedgerError('storage_integrity')
        return accepted

    def submit(self, principal, idempotency_key, archive, package):
        owner = _context(principal)
        if (type(idempotency_key) is not str or not _KEY.fullmatch(idempotency_key) or
                type(archive) is not bytes or type(package) is not str):
            raise LedgerError('invalid_request')
        if len(archive) > self.intake.max_archive_bytes or len(package) > self.intake.max_path_bytes:
            raise LedgerError('request_limit')

        def operation(connection, now, expired):
            self._charge(connection, owner, 'submit', now)
            try:
                accepted = accept_source_zip(archive, package, self.intake)
                request = {'schema': 'bcenv.ledger-request.v1', 'accepted_source': accepted.content_sha256}
                rejection = None
            except SubmissionRejected as error:
                accepted, rejection = None, error.code
                request = {'schema': 'bcenv.ledger-request.v1', 'package': package,
                           'archive_sha256': hashlib.sha256(archive).hexdigest(), 'rejection': rejection}
            request_digest = _identity(request)
            existing = connection.execute('SELECT * FROM jobs WHERE owner=? AND idempotency_key=?',
                                          (owner, idempotency_key)).fetchone()
            if existing is not None:
                if existing['request_digest'] != request_digest:
                    raise LedgerError('idempotency_conflict')
                return self._public(existing)
            total = connection.execute('SELECT count(*) FROM jobs').fetchone()[0]
            own = connection.execute('SELECT count(*) FROM jobs WHERE owner=?', (owner,)).fetchone()[0]
            if total >= self.limits.global_records or own >= self.limits.owner_records:
                raise LedgerError('record_limit')
            size = sum(len(item.data) for item in accepted.files) if accepted else 0
            if accepted:
                total_pending = connection.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]
                own_pending = connection.execute("SELECT count(*) FROM jobs WHERE owner=? AND state IN ('queued','running')", (owner,)).fetchone()[0]
                if total_pending >= self.limits.global_pending or own_pending >= self.limits.owner_pending:
                    raise LedgerError('pending_limit')
                own_bytes = connection.execute('SELECT coalesce(sum(source_bytes),0) FROM jobs WHERE owner=?', (owner,)).fetchone()[0]
                retained_bytes = connection.execute('SELECT coalesce(sum(source_bytes),0) FROM jobs').fetchone()[0]
                if (own_bytes + size > self.limits.owner_source_bytes or
                        retained_bytes + size > self.limits.global_source_bytes):
                    raise LedgerError('source_limit')
                # Physical deduplication never discounts the public quota. A
                # discount would disclose cross-owner source presence at cap.
                stored = connection.execute('SELECT digest FROM sources WHERE digest=?', (accepted.content_sha256,)).fetchone()
                if stored:
                    if self._source(connection, accepted.content_sha256) != accepted:
                        raise LedgerError('storage_integrity')
                else:
                    connection.execute('INSERT INTO sources VALUES (?, ?, ?, ?)',
                        (accepted.content_sha256, accepted.package, _json(_manifest(accepted)), size))
                    connection.executemany('INSERT INTO source_files VALUES (?, ?, ?, ?, ?)',
                        [(accepted.content_sha256, item.path, hashlib.sha256(item.data).hexdigest(), len(item.data), item.data)
                         for item in accepted.files])
            identifier = 's_' + uuid.uuid4().hex
            response = self._project(identifier, {'status': 'accepted'} if accepted else {'status': 'rejected', 'reason': rejection})
            connection.execute('''INSERT INTO jobs
                (id,owner,idempotency_key,request_digest,source_digest,source_bytes,state,result_json)
                VALUES (?,?,?,?,?,?,?,?)''',
                (identifier, owner, idempotency_key, request_digest, accepted.content_sha256 if accepted else None,
                 size, 'queued' if accepted else 'rejected', None if accepted else _json(response)))
            return response
        return self._run(operation)

    def result(self, principal, submission_id):
        owner = _context(principal)
        def operation(connection, now, expired):
            self._charge(connection, owner, 'read', now)
            return self._public(self._owned(connection, owner, submission_id))
        return self._run(operation)

    def reserve_job(self, principal, submission_id, worker):
        owner, worker = _context(principal), _context(worker)
        def operation(connection, now, expired):
            row = self._owned(connection, owner, submission_id)
            if row['state'] != 'queued':
                raise LedgerError('job_not_queued')
            total = connection.execute("SELECT count(*) FROM jobs WHERE state='running'").fetchone()[0]
            own = connection.execute("SELECT count(*) FROM jobs WHERE owner=? AND state='running'", (owner,)).fetchone()[0]
            if total >= self.limits.global_running or own >= self.limits.owner_running:
                raise LedgerError('active_limit')
            source = self._source(connection, row['source_digest'])
            generation = int(connection.execute("SELECT value FROM metadata WHERE key='generation'").fetchone()[0]) + 1
            until = now + self.limits.lease_seconds
            connection.execute("UPDATE metadata SET value=? WHERE key='generation'", (str(generation),))
            connection.execute("UPDATE jobs SET state='running',generation=?,worker=?,lease_until=? WHERE id=?",
                               (generation, worker, until, submission_id))
            return JobReservation(submission_id, generation, worker, until, source)
        return self._run(operation)

    def finish(self, principal, submission_id, worker, generation, evidence):
        owner, worker = _context(principal), _context(worker)
        if type(generation) is not int or generation < 1:
            raise LedgerError('invalid_request')
        def operation(connection, now, expired):
            row = self._owned(connection, owner, submission_id)
            if row['generation'] != generation or row['worker'] != worker:
                raise LedgerError('stale_reservation')
            result = self._project(submission_id, evidence)
            if result['status'] not in _TERMINAL:
                raise LedgerError('invalid_terminal_result')
            if row['state'] in _TERMINAL:
                if self._public(row) == result:
                    return result
                raise LedgerError('terminal_conflict')
            if row['state'] != 'running' or row['lease_until'] <= now:
                raise LedgerError('stale_reservation')
            self._source(connection, row['source_digest'])
            connection.execute('UPDATE jobs SET state=?,lease_until=NULL,result_json=? WHERE id=?',
                               (result['status'], _json(result), submission_id))
            return result
        return self._run(operation)

    def recover_expired(self):
        """Trusted operator method: expires claims; never schedules or retries."""
        return self._run(lambda connection, now, expired: expired)

    def replay(self, principal, submission_id):
        owner = _context(principal)
        def operation(connection, now, expired):
            self._charge(connection, owner, 'read', now)
            self._owned(connection, owner, submission_id)
            return {'schema': 'bcenv.scrimmage.v1', 'submission_id': submission_id,
                    'status': 'unavailable', 'reason': 'replay_withheld'}
        return self._run(operation)
