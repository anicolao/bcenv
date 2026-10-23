"""Opt-in internal SQL authority seam; NOT a runnable/resumable trial profile.

No LocalTrial, provider, filesystem capture, native task, or process integration.
Policy inputs/proofs are trusted assertions; see docs/epoch-authority.md.
"""
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import uuid

PROFILE = 'resumable-authority-v1'
APP_ID = 0x42434550
VERSION = 1
LIMITS = dict(versions=24, training_games=192, validation_games=16,
              notes=72, replays=450, reads=2000, mailbox=2200)
MAX_JSON = 65536
MAX_SOURCE = 1048576
MAX_SOURCES = 64 * MAX_SOURCE
MAX_RECEIPTS = 8192
MAX_EPOCHS = 64
PATH_KEYS = frozenset(('service', 'artifacts', 'mailbox', 'control', 'author'))
_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_AUTHORITY = re.compile(r'[0-9a-f]{32}\Z')


class AuthorityDenied(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class AuthorityUnavailable(RuntimeError):
    def __init__(self, code='authority_unavailable'):
        self.code = code
        super().__init__(code)


class CommitUnknown(AuthorityUnavailable):
    def __init__(self):
        super().__init__('commit_unknown')


@dataclass(frozen=True)
class ClockSample:
    domain: str
    wall: float
    mono: float


def _json(value):
    try:
        data = json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=True, allow_nan=False)
        if len(data.encode()) > MAX_JSON:
            raise ValueError()
        return data
    except (ValueError, TypeError, RecursionError):
        raise AuthorityDenied('invalid_payload') from None


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _id(value):
    if type(value) is not str or not _ID.fullmatch(value):
        raise AuthorityDenied('invalid_id')
    return value


def _sha(value):
    if type(value) is not str or not _SHA.fullmatch(value):
        raise AuthorityDenied('invalid_digest')
    return value


def _paths(value):
    if type(value) is not dict or set(value) != PATH_KEYS:
        raise AuthorityDenied('invalid_paths')
    result = {}
    for key, path in value.items():
        if (type(path) is not str or len(path) > 4096 or not path.startswith('/') or path.startswith('//') or
                '\x00' in path or '..' in path.split('/') or str(PurePosixPath(path)) != path):
            raise AuthorityDenied('invalid_paths')
        result[key] = path
    _disjoint(list(result.values()))
    return result


def _disjoint(paths):
    for i, a in enumerate(paths):
        for b in paths[i + 1:]:
            if a == b or a.startswith(b.rstrip('/') + '/') or b.startswith(a.rstrip('/') + '/'):
                raise AuthorityDenied('overlapping_paths')


def _sample(value):
    if (not isinstance(value, ClockSample) or type(value.domain) is not str or
            not 0 < len(value.domain) <= 256 or any(type(x) not in (int, float) or
            not math.isfinite(x) or x < 0 for x in (value.wall, value.mono))):
        raise AuthorityDenied('invalid_clock')
    return value


SCHEMA = (
    'CREATE TABLE run (id INTEGER PRIMARY KEY CHECK(id=1), authority_id TEXT NOT NULL UNIQUE, config_digest TEXT NOT NULL, '
    'config TEXT NOT NULL, author TEXT NOT NULL, epoch INTEGER NOT NULL, state TEXT NOT NULL, '
    'domain TEXT NOT NULL, wall_start REAL NOT NULL, mono_start REAL NOT NULL, '
    'wall_deadline REAL NOT NULL, mono_deadline REAL NOT NULL, last_wall REAL NOT NULL, last_mono REAL NOT NULL)',
    'CREATE TABLE counters (name TEXT PRIMARY KEY, used INTEGER NOT NULL, ceiling INTEGER NOT NULL)',
    'CREATE TABLE epochs (epoch INTEGER PRIMARY KEY, paths TEXT NOT NULL)',
    'CREATE TABLE sources (digest TEXT PRIMARY KEY, body BLOB NOT NULL)',
    'CREATE TABLE jobs (id TEXT PRIMARY KEY, epoch INTEGER NOT NULL REFERENCES epochs(epoch), '
    'kind TEXT NOT NULL, request_digest TEXT NOT NULL, fingerprint TEXT NOT NULL, '
    'source_digest TEXT NOT NULL REFERENCES sources(digest), source_fingerprint TEXT NOT NULL, '
    'request TEXT NOT NULL, state TEXT NOT NULL, outcome_id TEXT UNIQUE, outcome TEXT)',
    'CREATE TABLE receipts (id TEXT PRIMARY KEY, epoch INTEGER NOT NULL REFERENCES epochs(epoch), '
    'operation TEXT NOT NULL, digest TEXT NOT NULL, response TEXT NOT NULL)',
    'CREATE TABLE events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, receipt_id TEXT UNIQUE NOT NULL REFERENCES receipts(id))',
)


class EpochAuthority:
    """Explicit opt-in, short transactions, no transaction/execution retry loop."""
    def __init__(self, path, *, authority_id, config_digest, clock):
        self.path = Path(path).absolute()
        if type(authority_id) is not str or not _AUTHORITY.fullmatch(authority_id):
            raise AuthorityDenied('invalid_authority_id')
        self.authority_id = authority_id
        self.config_digest = _sha(config_digest)
        self.clock = clock
        self._failed = False
        try:
            with closing(self._connect(readonly=True)) as db:
                self._read_identity(db)
        except sqlite3.Error:
            raise AuthorityUnavailable() from None

    @classmethod
    def create(cls, path, *, profile, config, author, paths, clock, duration=21600, limits=None):
        if profile != PROFILE or type(config) is not dict:
            raise AuthorityDenied('invalid_profile')
        if type(author) is not str or not 0 < len(author) <= 256:
            raise AuthorityDenied('invalid_author')
        if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 21600:
            raise AuthorityDenied('invalid_duration')
        if limits is not None and type(limits) is not dict:
            raise AuthorityDenied('invalid_limits')
        limits = dict(LIMITS if limits is None else limits)
        if set(limits) != set(LIMITS) or any(type(v) is not int or not 0 <= v <= LIMITS[k] for k, v in limits.items()):
            raise AuthorityDenied('invalid_limits')
        paths = _paths(paths)
        sample = _sample(clock())
        canonical = _json(dict(profile=profile, config=config, limits=limits, duration=duration))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        authority_id = uuid.uuid4().hex
        path = Path(path).absolute()
        # Exclusive creation: never adopt/reset a failed partial initialization.
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except OSError:
            raise AuthorityUnavailable('initialization_unavailable') from None
        db = None
        primary_error = False
        try:
            db = sqlite3.connect(str(path), timeout=1, isolation_level=None)
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            for sql in SCHEMA:
                db.execute(sql)
            db.execute(f'PRAGMA application_id={APP_ID}')
            db.execute(f'PRAGMA user_version={VERSION}')
            db.execute('INSERT INTO run VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (authority_id, digest, canonical, author, 0, 'ACTIVE', sample.domain, sample.wall,
                        sample.mono, sample.wall + duration, sample.mono + duration,
                        sample.wall, sample.mono))
            db.executemany('INSERT INTO counters VALUES (?,0,?)', limits.items())
            db.execute('INSERT INTO epochs VALUES (0,?)', (_json(paths),))
            db.commit()
        except BaseException:
            primary_error = True
            if db is not None:
                try:
                    db.rollback()
                except BaseException:
                    pass
            raise AuthorityUnavailable('initialization_unknown') from None
        finally:
            if db is not None:
                try:
                    db.close()
                except BaseException:
                    if not primary_error:
                        raise AuthorityUnavailable('initialization_unknown') from None
        return cls(path, authority_id=authority_id, config_digest=digest, clock=clock)

    def _connect(self, readonly=False):
        db = None
        try:
            db = sqlite3.connect(self.path.as_uri() + ('?mode=ro' if readonly else '?mode=rw'),
                                 uri=True, timeout=1, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA synchronous=FULL')
            if (db.execute('PRAGMA application_id').fetchone()[0] != APP_ID or
                    db.execute('PRAGMA user_version').fetchone()[0] != VERSION):
                db.close()
                raise AuthorityUnavailable('incompatible_database')
            return db
        except BaseException:
            if db is not None:
                try:
                    db.close()
                except BaseException:
                    pass
            raise AuthorityUnavailable() from None

    def _read_identity(self, db):
        row = db.execute('SELECT authority_id,config_digest FROM run WHERE id=1').fetchone()
        if row is None or row['authority_id'] != self.authority_id:
            raise AuthorityDenied('authority_mismatch')
        if row['config_digest'] != self.config_digest:
            raise AuthorityDenied('configuration_mismatch')

    def _clock_check(self, row):
        now = _sample(self.clock())
        if now.domain != row['domain']:
            raise AuthorityDenied('clock_domain_mismatch')
        if now.wall < row['last_wall'] or now.mono < row['last_mono']:
            raise AuthorityDenied('clock_regression')
        if now.wall >= row['wall_deadline'] or now.mono >= row['mono_deadline']:
            raise AuthorityDenied('deadline_expired')
        return now

    def _commit(self, db):
        """Private fault-injection seam for pure SQLite tests, not an execution retry."""
        db.commit()

    def _transition(self, epoch, tid, operation, identity, action, state='ACTIVE'):
        if self._failed:
            raise AuthorityUnavailable('writer_closed')
        if type(epoch) is not int or epoch < 0:
            raise AuthorityDenied('invalid_epoch')
        _id(tid)
        digest = _hash(identity)  # Bounded serialization before acquiring writer lock.
        db = None
        committing = False
        primary_error = False
        try:
            db = self._connect()
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM run WHERE id=1').fetchone()
            if row is None or row['authority_id'] != self.authority_id:
                raise AuthorityDenied('authority_mismatch')
            if row['config_digest'] != self.config_digest:
                raise AuthorityDenied('configuration_mismatch')
            if row['epoch'] != epoch:
                raise AuthorityDenied('stale_epoch')
            if row['state'] != state:
                raise AuthorityDenied('admission_closed')
            first_clock = self._clock_check(row)  # After bounded SQL lock acquisition.
            prior = db.execute('SELECT digest FROM receipts WHERE id=?', (tid,)).fetchone()
            if prior:
                raise AuthorityDenied('duplicate_request' if prior[0] == digest else 'identity_conflict')
            if db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0] >= MAX_RECEIPTS:
                raise AuthorityDenied('receipt_limit')
            response = action(db, row)
            observed = dict(row, last_wall=first_clock.wall, last_mono=first_clock.mono)
            now = self._clock_check(observed)  # Late cutoff before authorization/commit.
            response = dict(response, authority_id=self.authority_id, transition_id=tid, origin_epoch=epoch,
                            operation=operation, request_digest=digest,
                            authorized_wall=now.wall, authorized_monotonic=now.mono)
            encoded = _json(response)
            db.execute('UPDATE run SET last_wall=?,last_mono=? WHERE id=1', (now.wall, now.mono))
            db.execute('INSERT INTO receipts VALUES (?,?,?,?,?)', (tid, epoch, operation, digest, encoded))
            db.execute('INSERT INTO events(receipt_id) VALUES (?)', (tid,))
            committing = True
            self._commit(db)
            return json.loads(encoded)
        except AuthorityDenied:
            primary_error = True
            if committing:
                self._failed = True
                raise CommitUnknown() from None
            if db is not None:
                try:
                    db.rollback()
                except BaseException:
                    self._failed = True
                    raise AuthorityUnavailable() from None
            raise
        except BaseException:
            primary_error = True
            self._failed = True
            if db is not None:
                try:
                    db.rollback()
                except BaseException:
                    pass
            if committing:
                raise CommitUnknown() from None
            raise AuthorityUnavailable() from None
        finally:
            if db is not None:
                try:
                    db.close()
                except BaseException:
                    self._failed = True
                    if not primary_error:
                        raise CommitUnknown() from None

    def admit(self, epoch, request_id, kind, payload, source, source_fingerprint, debits, *, effect=None):
        if kind not in ('submit', 'replay', 'game', 'copy') or type(payload) is not dict:
            raise AuthorityDenied('invalid_request')
        if type(source) is not bytes or not 0 < len(source) <= MAX_SOURCE:
            raise AuthorityDenied('invalid_source')
        _sha(source_fingerprint)
        if type(debits) is not dict or any(k not in LIMITS or type(v) is not int or v < 0 for k, v in debits.items()):
            raise AuthorityDenied('invalid_debits')
        if kind != 'submit' and type(effect) is not dict:
            raise AuthorityDenied('invalid_effect')
        payload = json.loads(_json(payload))
        effect = json.loads(_json(effect))
        debits = dict(debits)
        blob_digest = hashlib.sha256(source).hexdigest()
        fingerprint = _hash(dict(config=self.config_digest, kind=kind,
                                 source=source_fingerprint, effect=None if kind == 'submit' else effect))
        identity = dict(operation='admit', kind=kind, payload=payload, source=blob_digest,
                        source_fingerprint=source_fingerprint, effect=effect, debits=debits)
        request = _json(identity)
        request_digest = hashlib.sha256(request.encode()).hexdigest()
        def action(db, row):
            if db.execute("SELECT 1 FROM jobs WHERE fingerprint=? AND state='UNKNOWN'", (fingerprint,)).fetchone():
                raise AuthorityDenied('unknown_fingerprint')
            if db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] >= 2200:
                raise AuthorityDenied('job_limit')
            for name, amount in debits.items():
                count = db.execute('SELECT used,ceiling FROM counters WHERE name=?', (name,)).fetchone()
                if amount > count['ceiling'] - count['used']:
                    raise AuthorityDenied('quota_exhausted')
            existing = db.execute('SELECT body FROM sources WHERE digest=?', (blob_digest,)).fetchone()
            if existing and existing[0] != source:
                raise AuthorityDenied('source_conflict')
            if not existing:
                size = db.execute('SELECT COALESCE(SUM(length(body)),0) FROM sources').fetchone()[0]
                if size + len(source) > MAX_SOURCES:
                    raise AuthorityDenied('source_limit')
                db.execute('INSERT INTO sources VALUES (?,?)', (blob_digest, source))
            for name, amount in debits.items():
                db.execute('UPDATE counters SET used=used+? WHERE name=?', (amount, name))
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,NULL,NULL)',
                       (request_id, epoch, kind, request_digest, fingerprint, blob_digest,
                        source_fingerprint, request, 'QUEUED'))
            return dict(job_id=request_id, status='QUEUED', fingerprint=fingerprint,
                        source_digest=blob_digest)
        return self._transition(epoch, request_id, 'admit', identity, action)

    def start(self, epoch, transition_id, job_id):
        _id(job_id)
        def action(db, row):
            if db.execute("SELECT 1 FROM jobs WHERE epoch=? AND state='RUNNING'", (epoch,)).fetchone():
                raise AuthorityDenied('active_limit')
            changed = db.execute("UPDATE jobs SET state='RUNNING' WHERE id=? AND epoch=? AND state='QUEUED'", (job_id, epoch)).rowcount
            if changed != 1:
                raise AuthorityDenied('job_not_queued')
            return dict(job_id=job_id, status='RUNNING')
        return self._transition(epoch, transition_id, 'start', dict(operation='start', job_id=job_id), action)

    def finalize(self, epoch, transition_id, job_id, outcome):
        _id(job_id)
        if type(outcome) is not dict:
            raise AuthorityDenied('invalid_outcome')
        encoded = _json(outcome)
        outcome = json.loads(encoded)
        def action(db, row):
            changed = db.execute("UPDATE jobs SET state='COMPLETE',outcome_id=?,outcome=? WHERE id=? AND epoch=? AND state='RUNNING'",
                                 (transition_id, encoded, job_id, epoch)).rowcount
            if changed != 1:
                raise AuthorityDenied('job_not_running')
            return dict(job_id=job_id, status='COMPLETE', outcome_id=transition_id, outcome=outcome)
        return self._transition(epoch, transition_id, 'finalize', dict(operation='finalize', job_id=job_id, outcome=outcome), action)

    def advance(self, epoch, transition_id, paths):
        paths = _paths(paths)
        def action(db, row):
            if epoch + 1 >= MAX_EPOCHS:
                raise AuthorityDenied('epoch_limit')
            old = [p for r in db.execute('SELECT paths FROM epochs') for p in json.loads(r[0]).values()]
            _disjoint(old + list(paths.values()))
            changed = db.execute("UPDATE run SET epoch=epoch+1,state='RECOVERING' WHERE id=1 AND epoch=? AND state='ACTIVE'", (epoch,)).rowcount
            if changed != 1:
                raise AuthorityDenied('stale_epoch')
            unknown = db.execute("UPDATE jobs SET state='UNKNOWN' WHERE epoch<=? AND state IN ('QUEUED','RUNNING')", (epoch,)).rowcount
            db.execute('INSERT INTO epochs VALUES (?,?)', (epoch + 1, _json(paths)))
            return dict(epoch=epoch + 1, status='RECOVERING', unknown_jobs=unknown)
        return self._transition(epoch, transition_id, 'advance', dict(operation='advance', paths=paths), action)

    def reopen(self, epoch, transition_id, author, proof_digest):
        _sha(proof_digest)
        def action(db, row):
            if author != row['author']:
                raise AuthorityDenied('author_mismatch')
            db.execute("UPDATE run SET state='ACTIVE' WHERE id=1 AND epoch=? AND state='RECOVERING'", (epoch,))
            return dict(epoch=epoch, status='ACTIVE', proof_digest=proof_digest)
        return self._transition(epoch, transition_id, 'reopen', dict(operation='reopen', author=author, proof=proof_digest), action, state='RECOVERING')

    def receipt(self, transition_id):
        """Internal operator-only, execution-free reconciliation, even after failure.

        Absence does NOT authorize retry. No candidate retrieval/debit API exists.
        """
        _id(transition_id)
        try:
            with closing(self._connect(readonly=True)) as db:
                db.execute('BEGIN')
                self._read_identity(db)
                row = db.execute('SELECT response FROM receipts WHERE id=?', (transition_id,)).fetchone()
                return None if row is None else json.loads(row[0])
        except AuthorityDenied:
            raise
        except BaseException:
            self._failed = True
            raise AuthorityUnavailable() from None

    def snapshot(self):
        """Operator inspection only: never discovers work to run from files/rows."""
        try:
            with closing(self._connect(readonly=True)) as db:
                db.execute('BEGIN')
                self._read_identity(db)
                return dict(run=dict(db.execute('SELECT * FROM run WHERE id=1').fetchone()),
                            counters={r['name']: r['used'] for r in db.execute('SELECT * FROM counters')},
                            jobs=[dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY id')])
        except AuthorityDenied:
            raise
        except BaseException:
            self._failed = True
            raise AuthorityUnavailable() from None
