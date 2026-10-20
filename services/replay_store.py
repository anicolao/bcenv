"""Bounded synthetic replay persistence, sharing the ledger transaction/clock.

All principal, run, worker, policy and source-registration inputs are trusted test
assertions. This extension supplies no authentication, process supervision, game
validation or hard disk/resource isolation. Immutable BLOBs avoid candidate paths.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import re
import uuid

from services.ledger import DurableLedger, JobReservation, LedgerError, _context, _identity, _json
from services.scrimmage import AcceptedSubmission, SourceFile

_HEX = re.compile(r'[0-9a-f]{64}\Z')
_REPLAY = re.compile(r'r_[0-9a-f]{32}\Z')
_VIEW = re.compile(r'v_[0-9a-f]{32}\Z')


@dataclass(frozen=True)
class ReplayPolicy:
    spec_version: str
    producer_identity: str
    projector_identity: str

    def __post_init__(self):
        if (type(self.spec_version) is not str or self.spec_version not in ('2.0.3', '3.0.13') or
                any(type(v) is not str or not _HEX.fullmatch(v)
                    for v in (self.producer_identity, self.projector_identity))):
            raise ValueError('invalid_replay_policy')


@dataclass(frozen=True)
class ReplayLimits:
    source_bytes: int = 8 * 1048576
    sources_per_job: int = 8
    sources_per_run: int = 16
    global_sources: int = 64
    run_source_bytes: int = 16 * 1048576
    global_source_bytes: int = 64 * 1048576
    views_per_job: int = 16
    views_per_run: int = 64
    global_views: int = 256
    attempts_per_run: int = 512
    global_attempts: int = 2048
    active_per_run: int = 1
    global_active: int = 4
    lease_seconds: int = 30
    source_ttl: int = 3600
    view_ttl: int = 1800
    max_records: int = 64
    payload_bytes: int = 12288

    def __post_init__(self):
        if (any(type(v) is not int or not 0 < v < 2**50 for v in vars(self).values()) or
                self.source_bytes > 8 * 1048576 or self.sources_per_job > 8 or
                self.max_records > 64 or not 4096 <= self.payload_bytes <= 12288):
            raise ValueError('invalid_replay_limits')


@dataclass(frozen=True)
class PreparedReplay:
    """Trusted fixture assertion, never a candidate-controlled registration."""
    registration_key: str
    source: bytes
    own_team_id: int
    match_count: int
    max_reported_rounds: int


@dataclass(frozen=True)
class PageReservation:
    submission_id: str
    job_id: str
    replay_id: str
    view_id: str
    generation: int
    lease_until: int
    source: bytes
    source_sha256: str
    own_team_id: int
    round_start: int
    round_end: int
    robot_id: int | None
    record_limit: int
    record_offset: int


_SCHEMA = (
    '''CREATE TABLE replay_job_runs (submission_id TEXT PRIMARY KEY REFERENCES jobs(id),
       owner TEXT NOT NULL, run_id TEXT NOT NULL, job_id TEXT UNIQUE NOT NULL, policy_json TEXT NOT NULL)''',
    '''CREATE TABLE replay_completions (submission_id TEXT PRIMARY KEY REFERENCES replay_job_runs(submission_id),
       batch_digest TEXT NOT NULL, result_digest TEXT NOT NULL, generation INTEGER NOT NULL)''',
    '''CREATE TABLE replay_sources (replay_id TEXT PRIMARY KEY, submission_id TEXT NOT NULL REFERENCES replay_completions(submission_id),
       registration_key TEXT NOT NULL, source BLOB NOT NULL, source_sha256 TEXT NOT NULL,
       size INTEGER NOT NULL CHECK(size=length(source)), accepted_digest TEXT NOT NULL,
       generation INTEGER NOT NULL, result_digest TEXT NOT NULL, own_team_id INTEGER NOT NULL,
       match_count INTEGER NOT NULL, max_reported_rounds INTEGER NOT NULL, expires_at INTEGER NOT NULL,
       UNIQUE(submission_id, registration_key))''',
    '''CREATE TABLE replay_views (view_id TEXT PRIMARY KEY, replay_id TEXT NOT NULL REFERENCES replay_sources(replay_id),
       descriptor TEXT NOT NULL, expires_at INTEGER NOT NULL, UNIQUE(replay_id,descriptor))''',
    '''CREATE TABLE replay_attempts (view_id TEXT NOT NULL REFERENCES replay_views(view_id), record_offset INTEGER NOT NULL,
       generation INTEGER NOT NULL, lease_until INTEGER, state TEXT NOT NULL CHECK(state IN ('running','complete','failed','expired')),
       attempt_count INTEGER NOT NULL, fingerprint TEXT, PRIMARY KEY(view_id,record_offset),
       CHECK((state='running')=(lease_until IS NOT NULL)))''',
)


def _immutable(table):
    return tuple(f"CREATE TRIGGER immutable_{table}_{action} BEFORE {action.upper()} ON {table} "
                 "BEGIN SELECT RAISE(ABORT,'immutable_replay'); END" for action in ('update', 'delete'))


class ReplayStore:
    def __init__(self, ledger, policy, *, limits=None):
        if type(ledger) is not DurableLedger or type(policy) is not ReplayPolicy:
            raise LedgerError('invalid_request')
        self.ledger, self.policy = ledger, policy
        self.limits = limits or ReplayLimits()
        if type(self.limits) is not ReplayLimits or ledger.limits.max_games > 128:
            raise LedgerError('invalid_request')
        policy.__post_init__()
        self.limits.__post_init__()
        self.policy_json = _json(asdict(policy))
        self.configuration = _json({'version': 1, 'policy': asdict(policy), 'limits': asdict(self.limits)})

        def initialize(c, now, expired):
            old = c.execute("SELECT value FROM metadata WHERE key='replay_configuration'").fetchone()
            tables = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'replay_%'")}
            expected = {'replay_job_runs', 'replay_completions', 'replay_sources', 'replay_views', 'replay_attempts'}
            if old is not None:
                if old[0] != self.configuration:
                    raise LedgerError('configuration_mismatch')
                if tables != expected:
                    raise LedgerError('storage_integrity')
                return
            if tables:
                raise LedgerError('incompatible_database')
            for statement in _SCHEMA:
                c.execute(statement)
            for table in expected - {'replay_attempts'}:
                for statement in _immutable(table):
                    c.execute(statement)
            c.execute("INSERT INTO metadata VALUES ('replay_configuration',?)", (self.configuration,))
        ledger._run(initialize)

    def _guard(self, c):
        value = c.execute("SELECT value FROM metadata WHERE key='replay_configuration'").fetchone()
        if value is None or value[0] != self.configuration:
            raise LedgerError('configuration_mismatch')

    def submit(self, principal, run_id, key, archive, package):
        _context(run_id)
        # Binding and idempotency identity are written in the SAME job transaction.
        return self.ledger._submit(principal, key, archive, package,
                                   run_binding=(run_id, self.policy_json), replay_configuration=self.configuration)

    def _owned(self, c, owner, run, submission):
        self._guard(c)
        job = self.ledger._owned(c, owner, submission)
        bound = c.execute('SELECT * FROM replay_job_runs WHERE submission_id=?', (submission,)).fetchone()
        if bound is None:
            # No run owns a legacy job in this extension. Do not disclose its
            # existence to an unrelated run, even under the same principal.
            raise LedgerError('not_found')
        if (bound['owner'], bound['run_id']) != (owner, run):
            raise LedgerError('not_found')
        if bound['policy_json'] != self.policy_json:
            raise LedgerError('configuration_mismatch')
        return job, bound

    def result(self, principal, run_id, submission_id):
        owner, run = _context(principal), _context(run_id)
        def operation(c, now, expired):
            self.ledger._charge(c, owner, 'read', now)
            job, _ = self._owned(c, owner, run, submission_id)
            return self.ledger._public(job)
        return self.ledger._run(operation)

    def preflight(self, principal, run_id, reservation):
        owner, run = _context(principal), _context(run_id)
        self._reservation(reservation)
        def operation(c, now, expired):
            job, bound = self._owned(c, owner, run, reservation.submission_id)
            self._live_job(c, now, job, reservation)
            return bound['job_id']
        return self.ledger._run(operation)

    def _reservation(self, reservation):
        if (type(reservation) is not JobReservation or type(reservation.generation) is not int or
                reservation.generation < 1 or type(reservation.lease_until) is not int or
                type(reservation.source) is not AcceptedSubmission or type(reservation.source.files) is not tuple or
                any(type(item) is not SourceFile or type(item.data) is not bytes or type(item.path) is not str
                    for item in reservation.source.files)):
            raise LedgerError('invalid_request')
        _context(reservation.worker)

    def _live_job(self, c, now, job, reservation):
        if (job['state'] != 'running' or job['lease_until'] <= now or
                job['generation'] != reservation.generation or job['worker'] != reservation.worker):
            raise LedgerError('stale_reservation')
        if self.ledger._source(c, job['source_digest']) != reservation.source:
            raise LedgerError('storage_integrity')

    def completion_state(self, principal, run_id, reservation, evidence, registrations):
        """Trusted preflight or exact prior completion, without calling a provider.

        Expired sources remain immutable and comparable for idempotent completion;
        this does not make them replay-readable again.
        """
        owner, run = _context(principal), _context(run_id)
        self._reservation(reservation)
        if type(registrations) is not tuple or not 1 <= len(registrations) <= self.limits.sources_per_job:
            raise LedgerError('invalid_request')
        keys = set()
        for item in registrations:
            if (type(item) is not tuple or len(item) != 3 or type(item[1]) is not bytes or
                    not 1 <= len(item[1]) <= self.limits.source_bytes or
                    type(item[2]) is not int or item[2] not in (1, 2)):
                raise LedgerError('invalid_evidence')
            _context(item[0])
            if item[0] in keys:
                raise LedgerError('invalid_evidence')
            keys.add(item[0])
        def operation(c, now, expired):
            job, bound = self._owned(c, owner, run, reservation.submission_id)
            if (job['generation'], job['worker']) != (reservation.generation, reservation.worker):
                raise LedgerError('stale_reservation')
            if job['state'] == 'running':
                self._live_job(c, now, job, reservation)
                return bound['job_id'], None
            result = self.ledger._project(job['id'], evidence)
            rows = self._completed_sources(c, job, reservation, result)
            supplied = {key: (data, own) for key, data, own in registrations}
            if (set(supplied) != {row['registration_key'] for row in rows} or
                    any(supplied[row['registration_key']] != (row['source'], row['own_team_id']) for row in rows)):
                raise LedgerError('terminal_conflict')
            return bound['job_id'], result
        return self.ledger._run(operation)

    def _completed_sources(self, c, job, reservation, result):
        marker = c.execute('SELECT * FROM replay_completions WHERE submission_id=?', (job['id'],)).fetchone()
        if (job['state'] != 'complete' or result['status'] != 'complete' or marker is None or
                marker['generation'] != reservation.generation or marker['result_digest'] != _identity(result) or
                self.ledger._public(job) != result):
            raise LedgerError('terminal_conflict')
        if self.ledger._source(c, job['source_digest']) != reservation.source:
            raise LedgerError('storage_integrity')
        rows = c.execute('SELECT * FROM replay_sources WHERE submission_id=? ORDER BY registration_key', (job['id'],)).fetchall()
        if not 1 <= len(rows) <= self.limits.sources_per_job:
            raise LedgerError('storage_integrity')
        manifests = []
        for row in rows:
            if (type(row['source']) is not bytes or not 1 <= row['size'] <= self.limits.source_bytes or
                    len(row['source']) != row['size'] or hashlib.sha256(row['source']).hexdigest() != row['source_sha256'] or
                    row['accepted_digest'] != job['source_digest'] or row['generation'] != reservation.generation or
                    row['result_digest'] != marker['result_digest']):
                raise LedgerError('storage_integrity')
            manifests.append({'key': row['registration_key'], 'sha256': row['source_sha256'],
                'own_team_id': row['own_team_id'], 'match_count': row['match_count'], 'max_reported_rounds': row['max_reported_rounds']})
        if _identity(manifests) != marker['batch_digest']:
            raise LedgerError('storage_integrity')
        return rows

    def complete(self, principal, run_id, reservation, evidence, prepared):
        owner, run = _context(principal), _context(run_id)
        self._reservation(reservation)
        if type(prepared) is not tuple or not 1 <= len(prepared) <= self.limits.sources_per_job:
            raise LedgerError('invalid_request')
        keys, manifests, size = set(), [], 0
        for p in prepared:
            if (type(p) is not PreparedReplay or type(p.source) is not bytes or
                    not 1 <= len(p.source) <= self.limits.source_bytes or
                    type(p.own_team_id) is not int or p.own_team_id not in (1, 2) or
                    type(p.match_count) is not int or not 1 <= p.match_count <= 8 or
                    type(p.max_reported_rounds) is not int or not 0 <= p.max_reported_rounds <= 2147483647):
                raise LedgerError('invalid_evidence')
            _context(p.registration_key)
            if p.registration_key in keys:
                raise LedgerError('invalid_evidence')
            keys.add(p.registration_key)
            size += len(p.source)
            manifests.append({'key': p.registration_key, 'sha256': hashlib.sha256(p.source).hexdigest(),
                              'own_team_id': p.own_team_id, 'match_count': p.match_count,
                              'max_reported_rounds': p.max_reported_rounds})
        batch = _identity(sorted(manifests, key=lambda x: x['key']))
        def operation(c, now, expired):
            job, _ = self._owned(c, owner, run, reservation.submission_id)
            if (job['generation'], job['worker']) != (reservation.generation, reservation.worker):
                raise LedgerError('stale_reservation')
            result = self.ledger._project(job['id'], evidence)
            if result['status'] != 'complete':
                raise LedgerError('invalid_terminal_result')
            result_digest = _identity(result)
            marker = c.execute('SELECT * FROM replay_completions WHERE submission_id=?', (job['id'],)).fetchone()
            if job['state'] != 'running':
                if (marker and marker['generation'] == reservation.generation and
                        marker['batch_digest'] == batch and marker['result_digest'] == result_digest and
                        self.ledger._public(job) == result):
                    self._completed_sources(c, job, reservation, result)
                    return result
                raise LedgerError('terminal_conflict')
            self._live_job(c, now, job, reservation)
            total = c.execute('SELECT count(*),coalesce(sum(size),0) FROM replay_sources').fetchone()
            own = c.execute('''SELECT count(*),coalesce(sum(s.size),0) FROM replay_sources s JOIN replay_job_runs b
                ON b.submission_id=s.submission_id WHERE b.owner=? AND b.run_id=?''', (owner, run)).fetchone()
            if (total[0] + len(prepared) > self.limits.global_sources or own[0] + len(prepared) > self.limits.sources_per_run or
                    total[1] + size > self.limits.global_source_bytes or own[1] + size > self.limits.run_source_bytes):
                raise LedgerError('resource_limit')
            # Every denial precedes these payload writes. One transaction publishes
            # terminal result + marker + all sources, or a storage error rolls back.
            c.execute("UPDATE jobs SET state='complete',lease_until=NULL,result_json=? WHERE id=?", (_json(result), job['id']))
            c.execute('INSERT INTO replay_completions VALUES (?,?,?,?)', (job['id'], batch, result_digest, reservation.generation))
            for p in prepared:
                c.execute('INSERT INTO replay_sources VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                          ('r_' + uuid.uuid4().hex, job['id'], p.registration_key, p.source,
                           hashlib.sha256(p.source).hexdigest(), len(p.source), job['source_digest'], reservation.generation,
                           result_digest, p.own_team_id, p.match_count, p.max_reported_rounds, now + self.limits.source_ttl))
            return result
        return self.ledger._run(operation)

    def _source(self, c, now, owner, run, submission, replay_id):
        job, bound = self._owned(c, owner, run, submission)
        if type(replay_id) is not str or not _REPLAY.fullmatch(replay_id):
            raise LedgerError('not_found')
        row = c.execute('SELECT * FROM replay_sources WHERE replay_id=? AND submission_id=?', (replay_id, submission)).fetchone()
        if row is None:
            raise LedgerError('not_found')
        if row['expires_at'] <= now:
            raise LedgerError('replay_not_available')
        marker = c.execute('SELECT * FROM replay_completions WHERE submission_id=?', (submission,)).fetchone()
        if (job['state'] != 'complete' or marker is None or row['generation'] != job['generation'] or
                marker['generation'] != job['generation'] or row['accepted_digest'] != job['source_digest'] or
                row['result_digest'] != _identity(self.ledger._public(job)) or row['result_digest'] != marker['result_digest'] or
                type(row['source']) is not bytes or not 1 <= row['size'] <= self.limits.source_bytes or
                len(row['source']) != row['size'] or hashlib.sha256(row['source']).hexdigest() != row['source_sha256']):
            raise LedgerError('storage_integrity')
        self.ledger._source(c, job['source_digest'])
        return row, bound

    def manifest(self, principal, run_id, submission, offset, limit):
        owner, run = _context(principal), _context(run_id)
        if type(offset) is not int or not 0 <= offset <= 8 or type(limit) is not int or not 1 <= limit <= 8:
            raise LedgerError('invalid_request')
        def operation(c, now, expired):
            self.ledger._charge(c, owner, 'read', now)
            job, _ = self._owned(c, owner, run, submission)
            if job['state'] in ('queued', 'running'):
                raise LedgerError('not_ready')
            rows = c.execute('SELECT replay_id FROM replay_sources WHERE submission_id=? ORDER BY replay_id', (submission,)).fetchall()
            if not rows:
                raise LedgerError('replay_not_available')
            if offset > len(rows):
                raise LedgerError('invalid_request')
            entries = []
            for row in rows[offset:offset + limit]:
                source, _ = self._source(c, now, owner, run, submission, row['replay_id'])
                entries.append({key: source[key] for key in ('replay_id', 'own_team_id', 'match_count', 'max_reported_rounds', 'expires_at')})
                entries[-1]['spec_version'] = self.policy.spec_version
            end = offset + len(entries)
            return {'schema': 'bcenv.replay-route.v2', 'submission_id': submission, 'status': 'available',
                    'operation': 'manifest', 'replays': entries, 'total_replays': len(rows),
                    'next_manifest_offset': end if end < len(rows) else None}
        return self.ledger._run(operation)

    def reserve_page(self, principal, run_id, submission, *, replay_id=None, view_id=None,
                     round_start=None, round_end=None, robot_id=None, record_limit=None, record_offset=0):
        owner, run = _context(principal), _context(run_id)
        if type(record_offset) is not int or not 0 <= record_offset <= 5000000:
            raise LedgerError('invalid_request')
        if view_id is None:
            if (type(round_start) is not int or type(round_end) is not int or
                    not 0 <= round_start <= round_end <= 2147483647 or round_end - round_start + 1 > 256 or
                    (robot_id is not None and (type(robot_id) is not int or not 0 <= robot_id <= 2147483647)) or
                    type(record_limit) is not int or not 1 <= record_limit <= self.limits.max_records or record_offset != 0):
                raise LedgerError('invalid_request')
        elif (type(view_id) is not str or not _VIEW.fullmatch(view_id) or
              any(v is not None for v in (replay_id, round_start, round_end, robot_id, record_limit))):
            raise LedgerError('invalid_request')
        def operation(c, now, expired):
            self.ledger._charge(c, owner, 'read', now)
            self._owned(c, owner, run, submission)
            new_view = False
            if view_id is None:
                source, bound = self._source(c, now, owner, run, submission, replay_id)
                descriptor = _json({'round_start': round_start, 'round_end': round_end, 'robot_id': robot_id,
                                    'record_limit': record_limit, 'policy': self.policy_json})
                view = c.execute('SELECT * FROM replay_views WHERE replay_id=? AND descriptor=?', (replay_id, descriptor)).fetchone()
                if view is None:
                    counts = c.execute('''SELECT count(*),sum(b.submission_id=?),sum(b.owner=? AND b.run_id=?) FROM replay_views v
                        JOIN replay_sources s ON s.replay_id=v.replay_id JOIN replay_job_runs b ON b.submission_id=s.submission_id''',
                        (submission, owner, run)).fetchone()
                    if counts[0] >= self.limits.global_views or (counts[1] or 0) >= self.limits.views_per_job or (counts[2] or 0) >= self.limits.views_per_run:
                        raise LedgerError('resource_limit')
                    view = {'view_id': 'v_' + uuid.uuid4().hex, 'replay_id': replay_id,
                            'descriptor': descriptor, 'expires_at': min(source['expires_at'], now + self.limits.view_ttl)}
                    new_view = True
            else:
                view = c.execute('SELECT * FROM replay_views WHERE view_id=?', (view_id,)).fetchone()
                if view is None:
                    raise LedgerError('not_found')
                source, bound = self._source(c, now, owner, run, submission, view['replay_id'])
            if view['expires_at'] <= now:
                raise LedgerError('replay_not_available')
            desc = json.loads(view['descriptor'])
            if desc['policy'] != self.policy_json:
                raise LedgerError('configuration_mismatch')
            counts = c.execute('''SELECT coalesce(sum(a.attempt_count),0),coalesce(sum(CASE WHEN b.owner=? AND b.run_id=? THEN a.attempt_count ELSE 0 END),0),
                coalesce(sum(a.state='running'),0),coalesce(sum(a.state='running' AND b.owner=? AND b.run_id=?),0)
                FROM replay_attempts a JOIN replay_views v ON v.view_id=a.view_id JOIN replay_sources s ON s.replay_id=v.replay_id
                JOIN replay_job_runs b ON b.submission_id=s.submission_id''', (owner, run, owner, run)).fetchone()
            if (counts[0] >= self.limits.global_attempts or counts[1] >= self.limits.attempts_per_run or
                    counts[2] >= self.limits.global_active or counts[3] >= self.limits.active_per_run):
                raise LedgerError('resource_limit')
            attempt = c.execute('SELECT * FROM replay_attempts WHERE view_id=? AND record_offset=?', (view['view_id'], record_offset)).fetchone()
            if attempt is not None and attempt['state'] == 'running':
                raise LedgerError('resource_limit')
            generation = 1 if attempt is None else attempt['generation'] + 1
            until = min(view['expires_at'], now + self.limits.lease_seconds)
            if new_view:
                c.execute('INSERT INTO replay_views VALUES (?,?,?,?)', tuple(view[key] for key in ('view_id','replay_id','descriptor','expires_at')))
            c.execute('''INSERT INTO replay_attempts VALUES (?,?,?,?,'running',1,NULL)
                ON CONFLICT(view_id,record_offset) DO UPDATE SET generation=excluded.generation,
                lease_until=excluded.lease_until,state='running',attempt_count=attempt_count+1''',
                (view['view_id'], record_offset, generation, until))
            return PageReservation(submission, bound['job_id'], source['replay_id'], view['view_id'], generation, until,
                source['source'], source['source_sha256'], source['own_team_id'], desc['round_start'], desc['round_end'],
                desc['robot_id'], desc['record_limit'], record_offset)
        return self.ledger._run(operation)

    def finish_page(self, principal, run_id, reservation, payload=None):
        owner, run = _context(principal), _context(run_id)
        if (type(reservation) is not PageReservation or type(reservation.generation) is not int or reservation.generation < 1 or
                type(reservation.record_offset) is not int or not 0 <= reservation.record_offset <= 5000000 or
                type(reservation.source) is not bytes or len(reservation.source) > self.limits.source_bytes or
                type(reservation.own_team_id) is not int or reservation.own_team_id not in (1, 2) or
                (payload is not None and
                (type(payload) is not bytes or len(payload) > self.limits.payload_bytes))):
            raise LedgerError('invalid_request')
        def operation(c, now, expired):
            source, bound = self._source(c, now, owner, run, reservation.submission_id, reservation.replay_id)
            view = c.execute('SELECT * FROM replay_views WHERE view_id=? AND replay_id=?', (reservation.view_id, reservation.replay_id)).fetchone()
            attempt = c.execute('SELECT * FROM replay_attempts WHERE view_id=? AND record_offset=?',
                                (reservation.view_id, reservation.record_offset)).fetchone()
            if (view is None or view['expires_at'] <= now or attempt is None or attempt['state'] != 'running' or
                    attempt['generation'] != reservation.generation or attempt['lease_until'] <= now):
                raise LedgerError('stale_reservation')
            desc = json.loads(view['descriptor'])
            if (bound['job_id'] != reservation.job_id or source['source'] != reservation.source or
                    source['source_sha256'] != reservation.source_sha256 or source['own_team_id'] != reservation.own_team_id or
                    any(desc[key] != getattr(reservation, key) for key in ('round_start','round_end','robot_id','record_limit')) or
                    desc['policy'] != self.policy_json):
                raise LedgerError('storage_integrity')
            fingerprint = hashlib.sha256(payload).hexdigest() if payload is not None else None
            conflict = fingerprint is not None and attempt['fingerprint'] is not None and fingerprint != attempt['fingerprint']
            c.execute('''UPDATE replay_attempts SET state=?,lease_until=NULL,fingerprint=coalesce(fingerprint,?)
                WHERE view_id=? AND record_offset=?''',
                ('failed' if payload is None or conflict else 'complete', None if conflict else fingerprint,
                 reservation.view_id, reservation.record_offset))
            if conflict:
                # Expected denial deliberately commits the failed attempt, retaining
                # the first fingerprint. Never publish the inconsistent bytes.
                raise LedgerError('replay_conflict')
            return payload
        return self.ledger._run(operation)
