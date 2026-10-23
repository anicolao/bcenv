"""Opt-in durable domain authority. SQL owns facts; workers own epoch-private files.

No process/filesystem work occurs in a transaction. Trusted orchestration must
validate manifests before finish/recovery calls; these are not candidate APIs.
"""
import base64
from contextlib import closing
import hashlib
import json
import sqlite3
import uuid

from services.epoch_authority import (EpochAuthority, AuthorityDenied, AuthorityUnavailable,
    PROFILE as CORE_PROFILE, MAX_EPOCHS, _json, _hash, _sha, _id, _paths, _disjoint)
from services.scrimmage import accept_source_zip, SubmissionRejected
from services.replay_feedback_v2 import ReplayPageQuery

PROFILE = 'resumable-v1'
SCHEMA = 'bcenv.resumable.v1'
DOMAIN_VERSION = 1
MAX_PUBLIC = 60000
MAX_RECEIPT_PUBLIC = 64000
# Every distinct source must first consume one of72 notes; each ZIP≤1MiB.
MAX_DOMAIN_SOURCE_BYTES = 72 * 1048576

_TABLES = (
    'CREATE TABLE domain_meta (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, phase TEXT NOT NULL, '
    'halted INTEGER NOT NULL, finalists TEXT NOT NULL, validation_ids TEXT NOT NULL, provisional TEXT, selected TEXT, checkpoint TEXT)',
    'CREATE TABLE domain_sources (fingerprint TEXT PRIMARY KEY, package TEXT NOT NULL, archive BLOB NOT NULL, archive_digest TEXT NOT NULL)',
    'CREATE TABLE domain_notes (id TEXT PRIMARY KEY, source TEXT NOT NULL REFERENCES domain_sources(fingerprint), '
    'digest TEXT NOT NULL, response TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0)',
    'CREATE TABLE domain_ingress (id TEXT PRIMARY KEY, epoch INTEGER NOT NULL, digest TEXT NOT NULL, response TEXT NOT NULL, work TEXT)',
    'CREATE TABLE domain_batches (id TEXT PRIMARY KEY, epoch INTEGER NOT NULL, phase TEXT NOT NULL, original TEXT NOT NULL, '
    'source TEXT NOT NULL REFERENCES domain_sources(fingerprint), state TEXT NOT NULL, response TEXT NOT NULL, manifest TEXT, manifest_sha256 TEXT)',
    'CREATE TABLE domain_games (batch TEXT NOT NULL REFERENCES domain_batches(id), game_index INTEGER NOT NULL, '
    'epoch INTEGER NOT NULL, state TEXT NOT NULL, admitted INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(batch,game_index))',
    'CREATE TABLE domain_replays (id TEXT PRIMARY KEY, epoch INTEGER NOT NULL, fingerprint TEXT NOT NULL, '
    'batch TEXT NOT NULL REFERENCES domain_batches(id), game_index INTEGER NOT NULL, query TEXT NOT NULL, request_id TEXT NOT NULL, '
    'state TEXT NOT NULL, response TEXT)',
    'CREATE TABLE domain_references (id TEXT PRIMARY KEY, response TEXT NOT NULL)',
    'CREATE TABLE domain_control (id INTEGER PRIMARY KEY CHECK(id=1), closed INTEGER NOT NULL, max_storage INTEGER NOT NULL, last_storage INTEGER NOT NULL)',
    'CREATE TABLE domain_recovery (epoch INTEGER PRIMARY KEY, author TEXT NOT NULL, proof TEXT NOT NULL, evidence TEXT NOT NULL)',
)


def public_json(value, *, receipt=False):
    encoded=_json(value)
    if len(encoded.encode()) > (MAX_RECEIPT_PUBLIC if receipt else MAX_PUBLIC):
        raise AuthorityDenied('response_limit')
    return encoded


def manifest_json(value):
    try:
        encoded=json.dumps(value,sort_keys=True,separators=(',', ':'),ensure_ascii=True,allow_nan=False)
        if len(encoded.encode())>1048576:raise ValueError()
        return encoded
    except (ValueError,TypeError,RecursionError):
        raise AuthorityDenied('manifest_limit') from None

def manifest_digest(value):
    return hashlib.sha256(manifest_json(value).encode()).hexdigest()


def _utf8_size(value):
    if type(value) is not str:
        raise AuthorityDenied('invalid_text')
    try:
        return len(value.encode('utf-8'))
    except UnicodeError:
        raise AuthorityDenied('invalid_text') from None


def request_digest(body):
    """Bounded request canonicalization including source bytes; outside SQL."""
    try:
        data = json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        if len(data) > 1500000:
            raise ValueError()
        return hashlib.sha256(data).hexdigest()
    except (TypeError, ValueError, RecursionError):
        raise AuthorityDenied('invalid_request') from None


def prepare(body):
    """Pure intake, deliberately outside admission transactions."""
    if type(body) is not dict:
        return {'error': 'invalid_request'}
    op = body.get('operation')
    try:
        if op in ('note', 'submit'):
            raw = body.get('source_zip_base64')
            if type(raw) is not str or len(raw) > 1400000:
                raise ValueError('invalid_archive')
            archive = base64.b64decode(raw, validate=True)
            source = accept_source_zip(archive, body.get('package'))
            return {'source': source, 'archive': archive, 'archive_digest': hashlib.sha256(archive).hexdigest()}
        if op == 'replay':
            q = body.get('query')
            keys = {'round_start', 'round_end', 'robot_id', 'record_offset', 'record_limit'}
            if type(q) is not dict or set(q) not in (keys, keys | {'mode'}):
                raise ValueError('invalid_query')
            if q.get('mode', 'actions') not in ('actions', 'own_diagnostics'):
                raise ValueError('invalid_query')
            query = ReplayPageQuery(**{k: v for k, v in q.items() if k != 'mode'})
            if query.record_limit > 128:
                raise ValueError('invalid_query')
            return {'query': query}
        return {}
    except (ValueError, TypeError, SubmissionRejected):
        return {'error': 'invalid_query' if op == 'replay' else 'invalid_archive'}


class ResumableAuthority(EpochAuthority):
    # Lifetime cap includes ingress, storage observations and work transitions.
    receipt_limit = 262144

    @staticmethod
    def _validate_config(row):
        raw=row['config']
        if type(raw) is not str or len(raw.encode())>65536 or hashlib.sha256(raw.encode()).hexdigest()!=row['config_digest']:
            raise AuthorityUnavailable('configuration_integrity')
        try:
            if _json(json.loads(raw))!=raw:raise ValueError()
        except (ValueError,TypeError,AuthorityDenied):
            raise AuthorityUnavailable('configuration_integrity') from None

    def _read_identity(self, db):
        super()._read_identity(db)
        self._validate_config(db.execute('SELECT config,config_digest FROM run WHERE id=1').fetchone())

    def _clock_check(self, row):
        self._validate_config(row)
        return super()._clock_check(row)

    def admit(self, *args, **kwargs):
        raise AuthorityDenied('domain_api_required')

    def start(self, *args, **kwargs):
        raise AuthorityDenied('domain_api_required')

    def finalize(self, *args, **kwargs):
        raise AuthorityDenied('domain_api_required')

    @classmethod
    def create(cls, path, *, profile=PROFILE, config, author, paths, clock, duration=21600, limits=None):
        if profile != PROFILE:
            raise AuthorityDenied('invalid_profile')
        obj = super().create(path, profile=CORE_PROFILE,
            config=dict(config, domain_profile=PROFILE), author=author, paths=paths,
            clock=clock, duration=duration, limits=limits)
        try:
            with closing(obj._connect()) as db:
                db.execute('BEGIN IMMEDIATE')
                for statement in _TABLES:
                    db.execute(statement)
                db.execute("INSERT INTO domain_meta VALUES(1,?,'training',0,'[]','{}',NULL,NULL,NULL)", (DOMAIN_VERSION,))
                db.execute('INSERT INTO domain_control VALUES(1,0,0,0)')
                db.commit()
        except BaseException:
            obj._failed = True
            raise AuthorityUnavailable('domain_initialization_unknown') from None
        return obj

    @staticmethod
    def _meta(db):
        row = db.execute('SELECT * FROM domain_meta WHERE id=1').fetchone()
        if row is None or row['version'] != DOMAIN_VERSION:
            raise AuthorityUnavailable('domain_configuration_mismatch')
        return row

    @staticmethod
    def _debit(db, name, count=1):
        row = db.execute('SELECT used,ceiling FROM counters WHERE name=?', (name,)).fetchone()
        if row is None or type(count) is not int or count < 0:
            raise AuthorityUnavailable('domain_integrity')
        if count > row['ceiling'] - row['used']:
            raise AuthorityDenied(name + '_limit')
        db.execute('UPDATE counters SET used=used+? WHERE name=?', (count, name))

    @staticmethod
    def _batch(db, sid):
        if type(sid) is not str:
            raise AuthorityDenied('unknown_submission')
        row = db.execute('SELECT * FROM domain_batches WHERE id=?', (sid,)).fetchone()
        if row is None:
            raise AuthorityDenied('unknown_submission')
        return row

    @staticmethod
    def _source(db, prepared):
        source, archive = prepared['source'], prepared['archive']
        old = db.execute('SELECT * FROM domain_sources WHERE fingerprint=?', (source.content_sha256,)).fetchone()
        if not old:
            size = db.execute('SELECT COALESCE(SUM(length(archive)),0) FROM domain_sources').fetchone()[0]
            if size + len(archive) > MAX_DOMAIN_SOURCE_BYTES:
                raise AuthorityDenied('source_limit')
            db.execute('INSERT INTO domain_sources VALUES (?,?,?,?)',
                       (source.content_sha256, source.package, archive, prepared['archive_digest']))
        elif old['package'] != source.package:
            raise AuthorityUnavailable('source_integrity')
        return source.content_sha256

    def _status(self, db, run):
        meta = self._meta(db)
        counters = {r['name']: r['used'] for r in db.execute('SELECT * FROM counters')}
        observed = self._clock_check(run)
        remaining = max(0., min(run['mono_deadline']-observed.mono, run['wall_deadline']-observed.wall))
        return dict(schema=SCHEMA, status='validation_status', phase=meta['phase'], halted=bool(meta['halted']),
            finalists=json.loads(meta['finalists']), validation_ids=json.loads(meta['validation_ids']),
            provisional_choice=meta['provisional'], final_choice=meta['selected'],
            deadline_monotonic=run['mono_deadline'], deadline_unix=run['wall_deadline'],
            started_monotonic=run['mono_start'], started_unix=run['wall_start'],
            max_run_seconds=run['mono_deadline']-run['mono_start'], remaining_seconds=remaining, profile=PROFILE,
            training_games_attempted=counters['training_games'], validation_games_attempted=counters['validation_games'],
            games_attempted=counters['training_games']+counters['validation_games'],
            usage=counters, epoch=run['epoch'])

    def request(self, epoch, request_id, body, *, attempt_id=None, prepared=None):
        """Commit charged ingress/response; returned work is trusted-only, once.

        Same-ID delivery duplicates charge mailbox once per unique attempt ID but
        never dispatch again. Receipt reconciliation is an explicit charged read.
        """
        _id(request_id)
        digest = request_digest(body)
        # Freeze mutable caller data before it crosses the transaction boundary.
        body = json.loads(json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False))
        normalized = prepare(body)
        if prepared is not None:
            # Only trusted evidence pins may supplement intake; never substitute
            # another request's source archive or query validation.
            if type(prepared) is not dict:
                raise AuthorityDenied('invalid_preparation')
            normalized.update({k: prepared[k] for k in ('verified_manifests','error') if k in prepared})
        prepared = normalized
        tid = attempt_id or 'request_' + request_id
        def action(db, run):
            self._meta(db)
            # An exhausted attempt ceiling cannot be incremented beyond its cap.
            try:
                self._debit(db, 'mailbox')
            except AuthorityDenied as error:
                # Refusal itself is canonical, but never exceed the fixed cap or
                # create another accepted ingress row after capacity is gone.
                return dict(response=dict(schema=SCHEMA,status='error',reason=error.code),work=None,duplicate=False)
            existing = db.execute('SELECT * FROM domain_ingress WHERE id=?', (request_id,)).fetchone()
            if existing:
                response = (json.loads(existing['response']) if existing['digest'] == digest else
                            dict(schema=SCHEMA, status='error', reason='identity_conflict'))
                return dict(response=response, work=None, duplicate=True)
            op = body.get('operation') if type(body) is dict else None
            reading = op not in ('note', 'submit', 'lock_finalists', 'select_finalist', 'checkpoint')
            denial = None
            if reading:
                try:
                    self._debit(db, 'reads')
                except AuthorityDenied as error:
                    denial = error.code
            db.execute('SAVEPOINT payload')
            try:
                if denial:
                    raise AuthorityDenied(denial)
                if prepared.get('error'):
                    raise AuthorityDenied(prepared['error'])
                response, work = self._request_payload(db, run, request_id, body, prepared)
                public_json(response,receipt=op=='receipt'); _json(work)  # Reserve RPC/receipt headroom.
            except AuthorityDenied as error:
                db.execute('ROLLBACK TO payload')
                response, work = dict(schema=SCHEMA, status='error', reason=error.code), None
            db.execute('RELEASE payload')
            db.execute('INSERT INTO domain_ingress VALUES (?,?,?,?,?)',
                (request_id, epoch, digest, _json(response), _json(work) if work else None))
            return dict(response=response, work=work, duplicate=False)
        return self._transition(epoch, tid, 'request', dict(request_id=request_id, digest=digest), action)

    def _request_payload(self, db, run, rid, body, prepared):
        if type(body) is not dict:
            raise AuthorityDenied('invalid_request')
        op = body.get('operation'); meta = self._meta(db)
        if op == 'validation_status' and set(body) == {'operation'}:
            return self._status(db, run), None
        if op == 'receipt' and set(body) == {'operation', 'request_id'}:
            _id(body['request_id'])
            prior = db.execute('SELECT id,epoch,digest,response FROM domain_ingress WHERE id=?', (body['request_id'],)).fetchone()
            if prior is None:
                raise AuthorityDenied('receipt_absent_no_retry')
            return dict(schema=SCHEMA, status='receipt', request_id=prior['id'], origin_epoch=prior['epoch'],
                        request_digest=prior['digest'], response=json.loads(prior['response'])), None
        if op == 'replay_result' and set(body) == {'operation', 'replay_id'}:
            _id(body['replay_id'])
            prior=db.execute('SELECT state,response FROM domain_replays WHERE id=?',(body['replay_id'],)).fetchone()
            if prior is None:raise AuthorityDenied('replay_missing')
            return (json.loads(prior['response']) if prior['response'] else dict(schema=SCHEMA,status='replay_pending',replay_id=body['replay_id'])),None
        if op == 'result' and set(body) == {'operation', 'submission_id'}:
            return json.loads(self._batch(db, body['submission_id'])['response']), None
        if meta['phase']=='selected':
            raise AuthorityDenied('selection_complete')
        if meta['halted'] or db.execute('SELECT closed FROM domain_control WHERE id=1').fetchone()[0]:
            raise AuthorityDenied('run_halted')
        if op == 'checkpoint' and set(body) == {'operation', 'text', 'submission_ids'}:
            text, ids = body['text'], body['submission_ids']
            if (type(text) is not str or not 0 < _utf8_size(text) <= 32768 or type(ids) is not list or
                    len(ids) > 24 or any(type(s) is not str for s in ids) or len(set(ids)) != len(ids)):
                raise AuthorityDenied('invalid_checkpoint')
            for sid in ids:
                self._batch(db, sid)
            response = dict(schema=SCHEMA, status='checkpoint', text=text, submission_ids=ids,
                            checkpoint_sha256=_hash(dict(text=text, submission_ids=ids)))
            db.execute('UPDATE domain_meta SET checkpoint=? WHERE id=1', (_json(response),))
            return response, None
        if op == 'note':
            if meta['phase']!='training':raise AuthorityDenied('training_closed')
            if set(body) != {'operation','package','source_zip_base64','text','replay_references'}:
                raise AuthorityDenied('invalid_note')
            text, refs = body['text'], body['replay_references']
            if type(text) is not str or not text.strip() or _utf8_size(text) > 4096 or type(refs) is not list or len(refs)>8 or any(type(r) is not str for r in refs) or len(set(refs)) != len(refs):
                raise AuthorityDenied('invalid_note')
            references=[]
            for ref in refs:
                row=db.execute('SELECT response FROM domain_references WHERE id=?',(ref,)).fetchone()
                if row is None:raise AuthorityDenied('unknown_replay_reference')
                references.append(json.loads(row[0]))
            self._debit(db,'notes'); source=self._source(db,prepared)
            note=dict(note_id='n_'+uuid.uuid4().hex,source_sha256=source,package=prepared['source'].package,text=text,replay_references=references)
            response=dict(note,schema=SCHEMA,status='noted',note_sha256=_hash(note))
            db.execute('INSERT INTO domain_notes(id,source,digest,response) VALUES(?,?,?,?)',(note['note_id'],source,response['note_sha256'],_json(response)))
            return response,None
        if op == 'submit':
            if set(body) != {'operation','package','source_zip_base64','note_id','note_sha256'}:
                raise AuthorityDenied('invalid_request')
            if meta['phase'] != 'training':raise AuthorityDenied('training_closed')
            if db.execute("SELECT count(*) FROM domain_batches WHERE state IN ('QUEUED','RUNNING')").fetchone()[0]>=2:raise AuthorityDenied('submission_limit')
            source=prepared['source'].content_sha256
            if db.execute("SELECT 1 FROM domain_batches WHERE source=? AND state='UNKNOWN'",(source,)).fetchone():raise AuthorityDenied('unknown_fingerprint')
            if type(body['note_id']) is not str or type(body['note_sha256']) is not str:raise AuthorityDenied('invalid_note')
            note=db.execute('SELECT * FROM domain_notes WHERE id=?',(body['note_id'],)).fetchone()
            if note is None:raise AuthorityDenied('unknown_note')
            if note['used']:raise AuthorityDenied('note_already_used')
            if note['source']!=source or note['digest']!=body['note_sha256']:raise AuthorityDenied('note_mismatch')
            self._debit(db,'versions');self._source(db,prepared)
            sid='s_'+uuid.uuid4().hex
            response=dict(schema=SCHEMA,status='accepted',submission_id=sid,source_sha256=source,phase='training',note_id=note['id'],note_sha256=note['digest'])
            db.execute('UPDATE domain_notes SET used=1 WHERE id=?',(note['id'],))
            db.execute('INSERT INTO domain_batches(id,epoch,phase,original,source,state,response) VALUES(?,?,?,?,?,?,?)',(sid,run['epoch'],'training',sid,source,'QUEUED',_json(response)))
            return response,dict(kind='batch',id=sid)
        if op == 'lock_finalists':
            if set(body)!={'operation','submission_ids','provisional_choice'} or meta['phase']!='training':raise AuthorityDenied('finalists_locked')
            ids=body['submission_ids']
            if type(ids) is not list or len(ids)!=2 or any(type(s) is not str for s in ids) or len(set(ids))!=2 or body['provisional_choice'] not in ids:raise AuthorityDenied('invalid_finalists')
            if db.execute("SELECT 1 FROM domain_batches WHERE state IN ('QUEUED','RUNNING')").fetchone():raise AuthorityDenied('training_pending')
            originals=[self._batch(db,s) for s in ids]
            if any(r['phase']!='training' or r['state']!='COMPLETE' or not r['manifest'] for r in originals):raise AuthorityDenied('invalid_finalists')
            expected={r['id']:r['manifest_sha256'] for r in originals}
            if prepared.get('verified_manifests')!=expected:raise AuthorityDenied('lock_evidence_unverified')
            vids={r['id']:'s_'+uuid.uuid4().hex for r in originals}
            for row in originals:
                response=dict(schema=SCHEMA,status='accepted',phase='validation',submission_id=vids[row['id']],original_submission_id=row['id'],source_sha256=row['source'])
                db.execute('INSERT INTO domain_batches(id,epoch,phase,original,source,state,response) VALUES(?,?,?,?,?,?,?)',(vids[row['id']],run['epoch'],'validation',row['id'],row['source'],'QUEUED',_json(response)))
                db.executemany("INSERT INTO domain_games(batch,game_index,epoch,state) VALUES(?,?,?,'RESERVED')",[(vids[row['id']],index,run['epoch']) for index in range(8)])
            db.execute("UPDATE domain_meta SET phase='locked',finalists=?,validation_ids=?,provisional=? WHERE id=1",(_json(ids),_json(vids),body['provisional_choice']))
            return self._status(db,run),dict(kind='validation',ids=list(vids.values()))
        if op == 'select_finalist':
            if set(body)!={'operation','submission_id'} or meta['phase']!='complete' or meta['selected'] is not None:raise AuthorityDenied('validation_incomplete')
            if body['submission_id'] not in json.loads(meta['finalists']):raise AuthorityDenied('invalid_finalists')
            originals=[self._batch(db,s) for s in json.loads(meta['finalists']) + list(json.loads(meta['validation_ids']).values())]
            if any(r['state']!='COMPLETE' or not r['manifest_sha256'] for r in originals):raise AuthorityDenied('validation_incomplete')
            expected={r['id']:r['manifest_sha256'] for r in originals}
            if prepared.get('verified_manifests')!=expected:raise AuthorityDenied('selection_evidence_unverified')
            db.execute("UPDATE domain_meta SET selected=?,phase='selected' WHERE id=1",(body['submission_id'],))
            return self._status(db,run),None
        if op == 'replay':
            if set(body)!={'operation','submission_id','game_index','query'}:raise AuthorityDenied('invalid_request')
            batch=self._batch(db,body['submission_id']);index=body['game_index'];result=json.loads(batch['response'])
            if batch['state']!='COMPLETE' or type(index) is not int or not 0<=index<8 or not batch['manifest']:raise AuthorityDenied('replay_unavailable')
            game=result['games'][index]
            _sha(game.get('replay_sha256'))
            if game.get('own_slot') not in ('A','B'):raise AuthorityDenied('replay_binding_invalid')
            normalized_query=dict(body['query'],mode=body['query'].get('mode','actions'))
            fingerprint=_hash(dict(config=self.config_digest,source=batch['source'],replay=game['replay_sha256'],own_slot=game['own_slot'],query=normalized_query))
            if db.execute("SELECT 1 FROM domain_replays WHERE fingerprint=? AND state='UNKNOWN'",(fingerprint,)).fetchone():raise AuthorityDenied('unknown_fingerprint')
            self._debit(db,'replays');replay_id='q_'+uuid.uuid4().hex
            db.execute('INSERT INTO domain_replays VALUES(?,?,?,?,?,?,?,?,NULL)',(replay_id,run['epoch'],fingerprint,batch['id'],index,_json(normalized_query),rid,'RUNNING'))
            return dict(schema=SCHEMA,status='replay_pending',replay_id=replay_id),dict(kind='replay',id=replay_id)
        raise AuthorityDenied('invalid_request')

    def start_batch(self, epoch, transition_id, sid):
        def action(db,run):
            if self._meta(db)['halted'] or db.execute('SELECT closed FROM domain_control WHERE id=1').fetchone()[0]:raise AuthorityDenied('run_halted')
            if db.execute("SELECT 1 FROM domain_batches WHERE epoch=? AND state='RUNNING'",(epoch,)).fetchone():raise AuthorityDenied('active_limit')
            changed=db.execute("UPDATE domain_batches SET state='RUNNING' WHERE id=? AND epoch=? AND state='QUEUED'",(sid,epoch)).rowcount
            if changed!=1:raise AuthorityDenied('batch_not_queued')
            row=self._batch(db,sid)
            if row['phase']=='validation':db.execute("UPDATE domain_meta SET phase='validation' WHERE id=1")
            return dict(submission_id=sid,status='RUNNING')
        return self._transition(epoch,transition_id,'start_batch',dict(id=sid),action)

    def before_game(self, epoch, transition_id, sid, index):
        if type(index) is not int or not 0<=index<8:raise AuthorityDenied('invalid_game')
        def action(db,run):
            row=self._batch(db,sid)
            if self._meta(db)['halted'] or db.execute('SELECT closed FROM domain_control WHERE id=1').fetchone()[0] or row['epoch']!=epoch or row['state']!='RUNNING':raise AuthorityDenied('batch_not_running')
            existing=db.execute('SELECT state FROM domain_games WHERE batch=? AND game_index=?',(sid,index)).fetchone()
            if existing and existing['state']!='RESERVED':raise AuthorityDenied('game_already_admitted')
            self._debit(db,'validation_games' if row['phase']=='validation' else 'training_games')
            if existing:
                db.execute("UPDATE domain_games SET state='ADMITTED',admitted=1 WHERE batch=? AND game_index=?",(sid,index))
            else:
                db.execute("INSERT INTO domain_games VALUES(?,?,?,'ADMITTED',1)",(sid,index,epoch))
            return dict(submission_id=sid,game_index=index,status='ADMITTED',physical_launch='not_attested',deadline_monotonic=run['mono_deadline'])
        return self._transition(epoch,transition_id,'game_admit',dict(id=sid,index=index),action)

    def finish_batch(self, epoch, transition_id, sid, result, manifest=None):
        result=json.loads(_json(result))
        manifest_encoded=manifest_json(manifest) if manifest is not None else None
        manifest=json.loads(manifest_encoded) if manifest_encoded is not None else None
        manifest_sha=hashlib.sha256(manifest_encoded.encode()).hexdigest() if manifest_encoded is not None else None
        if result.get('status') not in ('complete','rejected','infrastructure_failure'):raise AuthorityDenied('invalid_result')
        def action(db,run):
            row=self._batch(db,sid)
            if row['epoch']!=epoch or row['state'] not in ('RUNNING','QUEUED'):raise AuthorityDenied('batch_not_running')
            status=result['status']
            if row['state']=='QUEUED' and status!='infrastructure_failure':raise AuthorityDenied('batch_not_running')
            if status=='complete' and (self._meta(db)['halted'] or db.execute('SELECT closed FROM domain_control WHERE id=1').fetchone()[0]):raise AuthorityDenied('run_halted')
            if status=='complete':
                games=result.get('games')
                indices=[r[0] for r in db.execute('SELECT game_index FROM domain_games WHERE batch=? AND admitted=1 ORDER BY game_index',(sid,))]
                if row['state']!='RUNNING' or indices!=list(range(8)) or type(games) is not list or len(games)!=8 or any(g.get('status')!='completed' for g in games) or not manifest:raise AuthorityDenied('invalid_complete_evidence')
                if manifest.get('source_sha256')!=row['source']:raise AuthorityDenied('source_mismatch')
            response=dict(json.loads(row['response']),**result)
            # Worker evidence cannot replace canonical source/ID/note bindings.
            for key,value in json.loads(row['response']).items():
                if key not in ('status',):response[key]=value
            public_json(response)
            state={'complete':'COMPLETE','rejected':'REJECTED','infrastructure_failure':'FAILED'}[status]
            db.execute('UPDATE domain_batches SET state=?,response=?,manifest=?,manifest_sha256=? WHERE id=?',(state,_json(response),manifest_encoded,manifest_sha,sid))
            db.execute("UPDATE domain_games SET state=? WHERE batch=?",('COMMITTED' if status=='complete' else 'UNSCORED',sid))
            if status=='infrastructure_failure':db.execute("UPDATE domain_meta SET halted=1,phase=CASE WHEN selected IS NULL THEN 'halted' ELSE phase END WHERE id=1")
            if row['phase']=='validation':
                vids=list(json.loads(self._meta(db)['validation_ids']).values())
                if status!='complete':
                    for vid in vids:
                        other=self._batch(db,vid)
                        if other['state'] in ('QUEUED','RUNNING'):
                            failed=dict(json.loads(other['response']),status='infrastructure_failure',reason='validation_halted')
                            db.execute("UPDATE domain_batches SET state='FAILED',response=? WHERE id=?",(_json(failed),vid))
                    db.execute("UPDATE domain_meta SET halted=1,phase='halted' WHERE id=1")
                elif all(self._batch(db,v)['state']=='COMPLETE' for v in vids):db.execute("UPDATE domain_meta SET phase='complete' WHERE id=1")
            return dict(response=response)
        return self._transition(epoch,transition_id,'batch_finalize',dict(id=sid,result=result,manifest_sha256=manifest_sha),action)

    def finish_replay(self, epoch, transition_id, replay_id, feedback=None, *, error=None):
        feedback=json.loads(_json(feedback)) if feedback is not None else None
        def action(db,run):
            replay=db.execute('SELECT * FROM domain_replays WHERE id=?',(replay_id,)).fetchone()
            if replay is None or replay['epoch']!=epoch or replay['state']!='RUNNING':raise AuthorityDenied('replay_not_running')
            if error is not None:
                response=dict(schema=SCHEMA,status='error',reason='replay_unavailable')
                db.execute("UPDATE domain_meta SET halted=1,phase=CASE WHEN selected IS NULL THEN 'halted' ELSE phase END WHERE id=1")
                state='FAILED'
            else:
                if self._meta(db)['halted'] or db.execute('SELECT closed FROM domain_control WHERE id=1').fetchone()[0]:raise AuthorityDenied('run_halted')
                batch=self._batch(db,replay['batch']);result=json.loads(batch['response']);index=replay['game_index']
                if type(feedback) is not dict or feedback.get('submission_id')!=batch['id']:raise AuthorityDenied('invalid_feedback')
                reference=dict(reference_id='r_'+uuid.uuid4().hex,submission_id=batch['id'],game_index=index,source_sha256=batch['source'],replay_sha256=result['games'][index]['replay_sha256'],query=json.loads(replay['query']),feedback_sha256=_hash(feedback),phase=batch['phase'],original_submission_id=batch['original'])
                response=dict(schema=SCHEMA,status='replay',submission_id=batch['id'],game_index=index,source_sha256=batch['source'],replay_sha256=reference['replay_sha256'],feedback=feedback,replay_reference=reference,phase=batch['phase'],original_submission_id=batch['original'])
                db.execute('INSERT INTO domain_references VALUES(?,?)',(reference['reference_id'],_json(reference)));state='COMPLETE'
            public_json(response)
            db.execute('UPDATE domain_replays SET state=?,response=? WHERE id=?',(state,_json(response),replay_id))
            # Terminal receipt is separate. Original ingress/admission is immutable;
            # candidate fetches replay_result as a separately charged read.
            return dict(response=response)
        return self._transition(epoch,transition_id,'replay_finalize',dict(id=replay_id,feedback=feedback,error=bool(error)),action)

    def halt(self, epoch, transition_id, reason='infrastructure_failure'):
        def action(db,run):
            db.execute("UPDATE domain_meta SET halted=1,phase=CASE WHEN selected IS NULL THEN 'halted' ELSE phase END WHERE id=1")
            return dict(status='halted',reason=reason)
        return self._transition(epoch,transition_id,'halt',dict(reason=reason),action)

    def _abandon(self, db):
        unknown=0
        for row in db.execute("SELECT * FROM domain_batches WHERE state IN ('QUEUED','RUNNING')").fetchall():
            response=dict(json.loads(row['response']),status='infrastructure_failure',reason='interrupted_unknown',unscored=True)
            db.execute("UPDATE domain_batches SET state='UNKNOWN',response=? WHERE id=?",(_json(response),row['id']));unknown+=1
        db.execute("UPDATE domain_games SET state='UNKNOWN' WHERE state IN ('RESERVED','ADMITTED')")
        for replay in db.execute("SELECT * FROM domain_replays WHERE state='RUNNING'").fetchall():
            response=dict(schema=SCHEMA,status='error',reason='interrupted_unknown',unscored=True)
            db.execute("UPDATE domain_replays SET state='UNKNOWN',response=? WHERE id=?",(_json(response),replay['id']))
        meta=self._meta(db)
        if meta['phase'] in ('locked','validation'):
            db.execute("UPDATE domain_meta SET halted=1,phase='incomplete' WHERE id=1")
        return unknown

    def advance(self, epoch, transition_id, paths):
        paths=_paths(paths)
        def action(db,run):
            if self._meta(db)['phase']=='selected':raise AuthorityDenied('selection_complete')
            if epoch+1>=MAX_EPOCHS:raise AuthorityDenied('epoch_limit')
            old=[p for r in db.execute('SELECT paths FROM epochs') for p in json.loads(r[0]).values()]
            _disjoint(old+list(paths.values()))
            db.execute("UPDATE run SET epoch=epoch+1,state='RECOVERING' WHERE id=1")
            db.execute('INSERT INTO epochs VALUES (?,?)',(epoch+1,_json(paths)))
            db.execute('UPDATE domain_control SET closed=0 WHERE id=1')
            unknown=self._abandon(db)
            return dict(epoch=epoch+1,status='RECOVERING',unknown_batches=unknown)
        return self._transition(epoch,transition_id,'advance',dict(paths=paths),action)

    def reopen(self, epoch, transition_id, author, proof_digest, *, verified=None):
        """Trusted operator only; verified manifest bound to exact committed facts.

        Caller must independently verify files/native task before this transition.
        A digest alone is not accepted as proof of a same-author continuation.
        """
        _sha(proof_digest)
        verified=json.loads(_json(verified))
        def action(db,run):
            if author!=run['author']:raise AuthorityDenied('author_mismatch')
            if self._meta(db)['phase']=='selected':raise AuthorityDenied('selection_complete')
            if self._meta(db)['halted']:raise AuthorityDenied('run_halted')
            if type(verified) is not dict or verified.get('author')!=author or verified.get('epoch')!=epoch or verified.get('same_existing_task') is not True or verified.get('proof_digest')!=proof_digest:raise AuthorityDenied('recovery_proof_missing')
            expected={r['id']:r['manifest_sha256'] for r in db.execute("SELECT id,manifest_sha256 FROM domain_batches WHERE state='COMPLETE'")}
            if verified.get('completed_manifests')!=expected:raise AuthorityDenied('recovery_evidence_mismatch')
            db.execute('INSERT INTO domain_recovery VALUES(?,?,?,?)',(epoch,author,proof_digest,_json(verified)))
            db.execute("UPDATE run SET state='ACTIVE' WHERE id=1")
            return dict(epoch=epoch,status='ACTIVE',proof_digest=proof_digest)
        return self._transition(epoch,transition_id,'reopen',dict(author=author,proof=proof_digest,verified=verified),action,state='RECOVERING')

    def inspect(self):
        """Operator-only immutable snapshot; never used to discover runnable work."""
        try:
            with closing(self._connect(readonly=True)) as db:
                db.execute('BEGIN');self._read_identity(db);self._meta(db)
                return dict(run=dict(db.execute('SELECT * FROM run WHERE id=1').fetchone()),meta=dict(self._meta(db)),
                    control=dict(db.execute('SELECT * FROM domain_control WHERE id=1').fetchone()),
                    counters={r['name']:r['used'] for r in db.execute('SELECT * FROM counters')},
                    epochs=[dict(r) for r in db.execute('SELECT * FROM epochs')],
                    batches=[dict(r) for r in db.execute('SELECT * FROM domain_batches')],
                    games=[dict(r) for r in db.execute('SELECT * FROM domain_games')],
                    replays=[dict(r) for r in db.execute('SELECT * FROM domain_replays')],
                    notes=[dict(r) for r in db.execute('SELECT * FROM domain_notes')],
                    references=[dict(r) for r in db.execute('SELECT * FROM domain_references')])
        except AuthorityDenied:raise
        except BaseException:
            self._failed=True;raise AuthorityUnavailable() from None

    def batch(self,sid):
        try:
            with closing(self._connect(readonly=True)) as db:
                db.execute('BEGIN');self._read_identity(db)
                value=dict(self._batch(db,sid))
            if value['manifest'] is not None and manifest_digest(json.loads(value['manifest']))!=value['manifest_sha256']:
                raise AuthorityUnavailable('manifest_integrity')
            return value
        except AuthorityDenied as error:
            if error.code in ('unknown_submission','source_missing','replay_missing'):
                raise
            self._failed=True;raise AuthorityUnavailable() from None
        except BaseException:
            self._failed=True;raise AuthorityUnavailable() from None

    def source(self,fingerprint):
        try:
            with closing(self._connect(readonly=True)) as db:
                db.execute('BEGIN');self._read_identity(db)
                row=db.execute('SELECT * FROM domain_sources WHERE fingerprint=?',(fingerprint,)).fetchone()
                if row is None:raise AuthorityDenied('source_missing')
                value=dict(row)
            if hashlib.sha256(value['archive']).hexdigest()!=value['archive_digest']:raise AuthorityUnavailable('source_integrity')
            accepted=accept_source_zip(value['archive'],value['package'])
            if accepted.content_sha256!=fingerprint:raise AuthorityUnavailable('source_integrity')
            return accepted
        except AuthorityDenied as error:
            if error.code in ('unknown_submission','source_missing','replay_missing'):
                raise
            self._failed=True;raise AuthorityUnavailable() from None
        except BaseException:
            self._failed=True;raise AuthorityUnavailable() from None

    def replay(self,replay_id):
        try:
            with closing(self._connect(readonly=True)) as db:
                db.execute('BEGIN');self._read_identity(db)
                row=db.execute('SELECT * FROM domain_replays WHERE id=?',(replay_id,)).fetchone()
                if row is None:raise AuthorityDenied('replay_missing')
                value=dict(row)
            _json(json.loads(value['query']))
            if value['response'] is not None:_json(json.loads(value['response']))
            return value
        except AuthorityDenied as error:
            if error.code in ('unknown_submission','source_missing','replay_missing'):
                raise
            self._failed=True;raise AuthorityUnavailable() from None
        except BaseException:
            self._failed=True;raise AuthorityUnavailable() from None

    state = inspect
    recovery_inventory = inspect

    def storage_observation(self, epoch, transition_id, size, error=None, *, recovery=False):
        if type(recovery) is not bool:
            raise AuthorityDenied('invalid_recovery_flag')
        if type(size) is not int or size < 0 or error is not None and type(error) is not str:
            raise AuthorityDenied('invalid_storage_observation')
        def action(db,run):
            db.execute('UPDATE domain_control SET max_storage=MAX(max_storage,?),last_storage=? WHERE id=1',(size,size))
            if error is not None or size > 1073741824:
                db.execute("UPDATE domain_meta SET halted=1,phase=CASE WHEN selected IS NULL THEN 'halted' ELSE phase END WHERE id=1")
            return dict(status='storage_observed',bytes=size,halted=bool(self._meta(db)['halted']),scan_failed=error is not None)
        return self._transition(epoch,transition_id,'storage',dict(size=size,error=error,recovery=recovery),action,state='RECOVERING' if recovery else 'ACTIVE')

    def logical_close(self, epoch, transition_id):
        def action(db,run):
            unknown=self._abandon(db)
            db.execute('UPDATE domain_control SET closed=1 WHERE id=1')
            return dict(status='closed',physical_cleanup='not_attested',unknown_batches=unknown)
        return self._transition(epoch,transition_id,'close',dict(operation='close'),action)

    def register_recovery(self, epoch, transition_id, author, proof, validated_manifest_bindings):
        if type(proof) is not dict or proof.get('author') != author or proof.get('epoch') != epoch:
            raise AuthorityDenied('recovery_proof_missing')
        value=dict(proof,author=author,epoch=epoch,completed_manifests=validated_manifest_bindings)
        return self.reopen(epoch,transition_id,author,proof.get('proof_digest'),verified=value)

    def current_state(self):
        """Read-only watcher observation: no clock, debit, expiry or recovery write."""
        try:
            with closing(self._connect(readonly=True)) as db:
                db.execute('BEGIN');self._read_identity(db);self._meta(db)
                return dict(run=dict(db.execute('SELECT * FROM run WHERE id=1').fetchone()),
                    meta=dict(self._meta(db)),control=dict(db.execute('SELECT * FROM domain_control WHERE id=1').fetchone()))
        except AuthorityDenied:raise
        except BaseException:
            self._failed=True;raise AuthorityUnavailable() from None

    def candidate_history(self):
        """Trusted recovery export of own committed public responses, never tickets."""
        try:
            with closing(self._connect(readonly=True)) as db:
                db.execute('BEGIN');self._read_identity(db);self._meta(db)
                rows=db.execute('SELECT id,epoch,digest,response FROM domain_ingress ORDER BY rowid').fetchall()
                if len(rows)>2200:raise AuthorityUnavailable('history_limit')
                return [dict(id=r['id'],epoch=r['epoch'],digest=r['digest'],response=json.loads(r['response'])) for r in rows]
        except AuthorityDenied:raise
        except BaseException:
            self._failed=True;raise AuthorityUnavailable() from None
