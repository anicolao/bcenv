"""Opt-in public-evidence gate. Trusted assertions, not Git authentication.

No publication, filesystem work, or waits in these bounded SQL transactions.
Evidence-only terminal/failure writes never change admission clocks or budgets.
"""
from contextlib import closing
import json
import math
import re

from services.epoch_authority import (AuthorityDenied, AuthorityUnavailable,
    CommitUnknown, _json, _hash, _sha, _sample, _id)

SCHEMA = 'bcenv.public-retention-descriptor.v1'
REPOSITORY_ID = 1431035719
MAX_CHECKPOINTS = 28
FAILURES = frozenset(('publication_failed','publication_unknown','readback_failed','ack_unknown','export_failed'))


def retention_config(run):
    config=json.loads(run['config'])['config']
    trial=config.get('trial',{})
    return trial.get('retention') if type(trial) is dict else None


def validate_retention_config(config):
    trial=config.get('trial',{}) if type(config) is dict else {}
    if type(trial) is not dict or 'retention' not in trial:return None
    value=trial['retention']
    if (type(value) is not dict or set(value)!={'schema','fixed_inputs_sha256','repository_id'} or
        value['schema']!='public-retention-v1' or type(value['repository_id']) is not int or
        value['repository_id']!=REPOSITORY_ID):raise AuthorityDenied('invalid_retention_configuration')
    _sha(value['fixed_inputs_sha256'])
    return value


def retention_descriptor_from_db(db,run):
    """Use inside caller's consistent SQL read transaction/backup."""
    if retention_config(run) is None:return None
    control=db.execute('SELECT * FROM domain_retention WHERE id=1').fetchone()
    if control is None:raise AuthorityUnavailable('retention_state_missing')
    if control['pending_id'] is None:return None
    history=retention_history_from_db(db,run)
    matches=[r['descriptor'] for r in history if r['descriptor']['checkpoint_id']==control['pending_id']]
    if len(matches)!=1:raise AuthorityUnavailable('retention_state_missing')
    return matches[0]


def retention_history_from_db(db,run):
    """Typed public audit history from the same consistent backup, never raw SQL."""
    if retention_config(run) is None:return []
    def require(ok):
        if not ok:raise AuthorityUnavailable('retention_history_corrupt')
    def exact(value,keys):require(type(value) is dict and set(value)==set(keys));return value
    def digest(value):_sha(value);return value
    def ident(value):_id(value);return value
    def flag(value):require(type(value) is bool);return value
    def number(value):require(type(value) in (int,float) and math.isfinite(value) and value>=0);return value
    def nullable_digest(value):return None if value is None else digest(value)
    rows=db.execute('SELECT * FROM domain_retention_checkpoints ORDER BY sequence').fetchall()
    require(len(rows)<=MAX_CHECKPOINTS);out=[]
    descriptor_keys=('schema','authority_id','config_digest','fixed_inputs_sha256','epoch','checkpoint_id','sequence','reason','effect_sha256','descriptor_sha256')
    for index,row in enumerate(rows):
        d=exact(json.loads(row['descriptor']),descriptor_keys)
        require(d['schema']==SCHEMA and d['authority_id']==run['authority_id'] and d['config_digest']==run['config_digest'] and
                d['fixed_inputs_sha256']==retention_config(run)['fixed_inputs_sha256'] and type(d['epoch']) is int and 0<=d['epoch']<=run['epoch'] and
                type(d['sequence']) is int and d['sequence']==index==row['sequence'] and d['checkpoint_id']==row['id'] and
                d['epoch']==row['epoch'] and d['reason']==row['reason'] and d['reason'] in ('initial','batch','terminal'))
        ident(d['checkpoint_id']);digest(d['effect_sha256']);digest(d['descriptor_sha256'])
        require(_hash({k:v for k,v in d.items() if k!='descriptor_sha256'})==d['descriptor_sha256'])
        raw=json.loads(row['effects']);require(_hash(raw)==d['effect_sha256'])
        terminal=d['reason']=='terminal'
        keys={'batches','selected','phase','halted'}
        if terminal:keys|={'terminal','observed_state','sticky_failure','superseded_checkpoint'}
        if terminal and raw.get('superseded_checkpoint') is not None:keys.add('unretained_gap')
        exact(raw,keys);require(type(raw['batches']) is list and len(raw['batches'])<=26)
        batches=[]
        for b in raw['batches']:
            exact(b,('id','state','source_sha256','manifest_sha256','response_sha256'))
            require(b['state'] in ('QUEUED','RUNNING','COMPLETE','REJECTED','FAILED','UNKNOWN'))
            if not terminal:require(b['state'] not in ('QUEUED','RUNNING'))
            batches.append(dict(id=ident(b['id']),state=b['state'],source_sha256=digest(b['source_sha256']),
                manifest_sha256=nullable_digest(b['manifest_sha256']),response_sha256=digest(b['response_sha256'])))
        require(len({b['id'] for b in batches})==len(batches))
        require(raw['phase'] in ('training','locked','validation','complete','selected','halted','incomplete'))
        effects=dict(batches=batches,selected=None if raw['selected'] is None else ident(raw['selected']),phase=raw['phase'],halted=flag(raw['halted']))
        if terminal:
            t=exact(raw['terminal'],('identity','lifecycle'));l=exact(t['lifecycle'],('logical_close','cleanup','evidence_sha256'))
            require(l['logical_close'] in ('committed','refused','unknown') and l['cleanup'] in ('confirmed','unknown'))
            require(t['identity']==row['terminal_identity'])
            effects['terminal']=dict(identity=digest(t['identity']),lifecycle=dict(logical_close=l['logical_close'],cleanup=l['cleanup'],evidence_sha256=digest(l['evidence_sha256'])))
            o=exact(raw['observed_state'],('logical_closed','run_state','expired','observed_wall','observed_monotonic'))
            require(o['run_state'] in ('ACTIVE','RECOVERING'))
            effects['observed_state']=dict(logical_closed=flag(o['logical_closed']),run_state=o['run_state'],expired=flag(o['expired']),observed_wall=number(o['observed_wall']),observed_monotonic=number(o['observed_monotonic']))
            effects['sticky_failure']=flag(raw['sticky_failure'])
            effects['superseded_checkpoint']=None if raw['superseded_checkpoint'] is None else ident(raw['superseded_checkpoint'])
            if 'unretained_gap' in raw:
                gap=exact(raw['unretained_gap'],('checkpoint_id','state','failure'))
                require(gap['state'] in ('PENDING','FAILED') and gap['checkpoint_id']==raw['superseded_checkpoint'] and (gap['failure'] is None or gap['failure'] in FAILURES))
                effects['unretained_gap']=dict(checkpoint_id=ident(gap['checkpoint_id']),state=gap['state'],failure=gap['failure'])
        ack=None
        if row['ack'] is not None:
            a=exact(json.loads(row['ack']),('status','descriptor','receipt'));require(a['status']=='retained' and a['descriptor']==d)
            r=exact(a['receipt'],('descriptor_sha256','inventory_sha256','snapshot_binding_sha256','repository_id','commit_sha','readback_sha256','readback_verified'))
            for k in ('descriptor_sha256','inventory_sha256','snapshot_binding_sha256','readback_sha256'):digest(r[k])
            require(r['descriptor_sha256']==d['descriptor_sha256'] and type(r['repository_id']) is int and r['repository_id']==REPOSITORY_ID and
                r['readback_verified'] is True and type(r['commit_sha']) is str and re.fullmatch('[0-9a-f]{40}',r['commit_sha']) is not None)
            ack=dict(status='retained',descriptor=dict(d),receipt={k:r[k] for k in ('descriptor_sha256','inventory_sha256','snapshot_binding_sha256','repository_id','commit_sha','readback_sha256','readback_verified')})
        require(row['state'] in ('PENDING','FAILED','RETAINED','SUPERSEDED') and (row['failure'] is None or row['failure'] in FAILURES))
        require((row['state']=='RETAINED')==(ack is not None))
        out.append(dict(descriptor=dict(d),effects=effects,state=row['state'],ack=ack,failure=row['failure']))
    return out


class PublicRetentionAuthority:
    def _retention_initialize(self,db,run):
        if retention_config(run) is None:return
        db.execute('CREATE TABLE domain_retention (id INTEGER PRIMARY KEY CHECK(id=1), sequence INTEGER NOT NULL, pending_id TEXT, failed INTEGER NOT NULL, terminal_id TEXT)')
        db.execute('CREATE TABLE domain_retention_checkpoints (id TEXT PRIMARY KEY, sequence INTEGER NOT NULL UNIQUE, epoch INTEGER NOT NULL, reason TEXT NOT NULL, descriptor TEXT NOT NULL, effects TEXT NOT NULL, state TEXT NOT NULL, ack TEXT, failure TEXT, terminal_identity TEXT)')
        db.execute('INSERT INTO domain_retention VALUES(1,-1,NULL,0,NULL)')
        self._retention_new(db,run,'initial')

    def _retention_state(self,db,run):
        if retention_config(run) is None:return dict(enabled=False,pending=False,failed=False,terminal=False,sequence=-1)
        row=db.execute('SELECT * FROM domain_retention WHERE id=1').fetchone()
        if row is None:raise AuthorityUnavailable('retention_state_missing')
        return dict(enabled=True,pending=row['pending_id'] is not None,failed=bool(row['failed']),terminal=row['terminal_id'] is not None,sequence=row['sequence'])

    def _retention_guard(self,db,run):
        value=self._retention_state(db,run)
        for key in ('terminal','failed','pending'):
            if value[key]:raise AuthorityDenied('retention_'+key)

    def _retention_new(self,db,run,reason,terminal=None):
        if retention_config(run) is None:return None
        control=db.execute('SELECT * FROM domain_retention WHERE id=1').fetchone()
        if control['terminal_id']:raise AuthorityDenied('retention_terminal')
        if control['pending_id'] and reason!='terminal':raise AuthorityDenied('retention_pending')
        sequence=control['sequence']+1
        if sequence>=MAX_CHECKPOINTS:raise AuthorityDenied('retention_limit')
        closed=[dict(id=r['id'],state=r['state'],source_sha256=r['source'],manifest_sha256=r['manifest_sha256'],response_sha256=_hash(json.loads(r['response'])))
                for r in db.execute("SELECT * FROM domain_batches "+('' if reason=='terminal' else "WHERE state NOT IN ('QUEUED','RUNNING') ")+"ORDER BY id")]
        meta=db.execute('SELECT selected,phase,halted FROM domain_meta WHERE id=1').fetchone()
        effects=dict(batches=closed,selected=meta['selected'],phase=meta['phase'],halted=bool(meta['halted']))
        if terminal is not None:
            observed=_sample(self.clock())
            if observed.domain!=run['domain']:raise AuthorityDenied('clock_domain_mismatch')
            if observed.wall<run['last_wall'] or observed.mono<run['last_mono']:raise AuthorityDenied('clock_regression')
            closed=bool(db.execute('SELECT closed FROM domain_control WHERE id=1').fetchone()[0])
            if terminal['lifecycle']['logical_close']=='committed' and not closed:raise AuthorityDenied('logical_close_not_committed')
            effects['observed_state']=dict(logical_closed=closed,run_state=run['state'],expired=observed.wall>=run['wall_deadline'] or observed.mono>=run['mono_deadline'],observed_wall=observed.wall,observed_monotonic=observed.mono)
            effects['terminal']=terminal
            effects['sticky_failure']=bool(control['failed'])
            effects['superseded_checkpoint']=control['pending_id']
            if control['pending_id']:
                old=db.execute('SELECT failure,state FROM domain_retention_checkpoints WHERE id=?',(control['pending_id'],)).fetchone()
                effects['unretained_gap']=dict(checkpoint_id=control['pending_id'],state=old['state'],failure=old['failure'])
                db.execute("UPDATE domain_retention_checkpoints SET state='SUPERSEDED' WHERE id=?",(control['pending_id'],))
        checkpoint_id='retention_'+run['authority_id']+'_'+str(sequence)
        descriptor=dict(schema=SCHEMA,authority_id=run['authority_id'],config_digest=run['config_digest'],
            fixed_inputs_sha256=retention_config(run)['fixed_inputs_sha256'],epoch=run['epoch'],
            checkpoint_id=checkpoint_id,sequence=sequence,reason=reason,effect_sha256=_hash(effects))
        descriptor['descriptor_sha256']=_hash(descriptor)
        db.execute('INSERT INTO domain_retention_checkpoints VALUES(?,?,?,?,?,?,?,NULL,NULL,?)',
            (checkpoint_id,sequence,run['epoch'],reason,_json(descriptor),_json(effects),'PENDING',terminal['identity'] if terminal else None))
        db.execute('UPDATE domain_retention SET sequence=?,pending_id=?,terminal_id=? WHERE id=1',
            (sequence,checkpoint_id,checkpoint_id if terminal else None))
        return descriptor

    def retention_gate(self,epoch):
        try:
            with closing(self._connect(readonly=True)) as db:
                db.execute('BEGIN');self._read_identity(db);run=db.execute('SELECT * FROM run WHERE id=1').fetchone()
                # Base identity checker returns the canonical run row.
                if type(epoch) is not int or run['epoch']!=epoch:raise AuthorityDenied('stale_epoch')
                return self._retention_state(db,run)
        except AuthorityDenied:raise
        except BaseException:
            self._failed=True;raise AuthorityUnavailable() from None

    def retention_descriptor(self):
        try:
            with closing(self._connect(readonly=True)) as db:
                db.execute('BEGIN');self._read_identity(db);run=db.execute('SELECT * FROM run WHERE id=1').fetchone()
                return retention_descriptor_from_db(db,run)
        except AuthorityDenied:raise
        except BaseException:
            self._failed=True;raise AuthorityUnavailable() from None

    retention_pending=retention_descriptor

    def _retention_write(self,epoch,action):
        if self._failed:raise AuthorityUnavailable('writer_closed')
        if type(epoch) is not int or epoch<0:raise AuthorityDenied('invalid_epoch')
        db=None;committing=False
        try:
            db=self._connect();db.execute('BEGIN IMMEDIATE');self._read_identity(db);run=db.execute('SELECT * FROM run WHERE id=1').fetchone()
            if run['epoch']!=epoch:raise AuthorityDenied('stale_epoch')
            if retention_config(run) is None:raise AuthorityDenied('retention_disabled')
            result=action(db,run)
            encoded=_json(result);committing=True;self._commit(db)
            return json.loads(encoded)
        except AuthorityDenied:
            if committing:self._failed=True;raise CommitUnknown() from None
            if db is not None:
                try:db.rollback()
                except BaseException:
                    self._failed=True;raise AuthorityUnavailable() from None
            raise
        except BaseException:
            self._failed=True
            if db is not None:
                try:db.rollback()
                except BaseException:pass
            if committing:raise CommitUnknown() from None
            raise AuthorityUnavailable() from None
        finally:
            if db is not None:
                try:db.close()
                except BaseException:
                    self._failed=True;raise CommitUnknown() from None

    def _retention_row(self,db,epoch,checkpoint_id,descriptor_sha256):
        _sha(descriptor_sha256)
        if type(checkpoint_id) is not str:raise AuthorityDenied('retention_identity')
        row=db.execute('SELECT * FROM domain_retention_checkpoints WHERE id=?',(checkpoint_id,)).fetchone()
        if row is None or row['epoch']!=epoch:raise AuthorityDenied('retention_identity')
        if row['state']=='SUPERSEDED':raise AuthorityDenied('retention_superseded')
        descriptor=json.loads(row['descriptor'])
        if descriptor['descriptor_sha256']!=descriptor_sha256:raise AuthorityDenied('retention_identity')
        if _hash({k:v for k,v in descriptor.items() if k!='descriptor_sha256'})!=descriptor_sha256:raise AuthorityUnavailable('retention_descriptor_corrupt')
        return row,descriptor

    def retention_ack(self,epoch,checkpoint_id,*,descriptor_sha256,inventory_sha256,snapshot_binding_sha256,repository_id,commit_sha,readback_sha256,readback_verified):
        for digest in (descriptor_sha256,inventory_sha256,snapshot_binding_sha256,readback_sha256):_sha(digest)
        if (type(repository_id) is not int or repository_id!=REPOSITORY_ID or readback_verified is not True or
            type(commit_sha) is not str or re.fullmatch('[0-9a-f]{40}',commit_sha) is None):raise AuthorityDenied('invalid_retention_ack')
        receipt=dict(descriptor_sha256=descriptor_sha256,inventory_sha256=inventory_sha256,snapshot_binding_sha256=snapshot_binding_sha256,
            repository_id=repository_id,commit_sha=commit_sha,readback_sha256=readback_sha256,readback_verified=True)
        def action(db,run):
            row,descriptor=self._retention_row(db,epoch,checkpoint_id,descriptor_sha256)
            control=db.execute('SELECT * FROM domain_retention WHERE id=1').fetchone()
            if row['sequence']!=control['sequence']:raise AuthorityDenied('retention_identity')
            first_clock=self._clock_check(run) if row['reason']!='terminal' else None
            answer=dict(status='retained',descriptor=descriptor,receipt=receipt)
            if row['ack'] is not None:
                if json.loads(row['ack'])!=answer:raise AuthorityDenied('retention_ack_conflict')
                return answer
            control=db.execute('SELECT * FROM domain_retention WHERE id=1').fetchone()
            if control['pending_id']!=checkpoint_id:raise AuthorityDenied('retention_identity')
            if control['failed'] and row['reason']!='terminal':raise AuthorityDenied('retention_failed')
            db.execute("UPDATE domain_retention_checkpoints SET state='RETAINED',ack=? WHERE id=?",(_json(answer),checkpoint_id))
            db.execute('UPDATE domain_retention SET pending_id=NULL WHERE id=1')
            if first_clock is not None:
                now=self._clock_check(dict(run,last_wall=first_clock.wall,last_mono=first_clock.mono))
                db.execute('UPDATE run SET last_wall=?,last_mono=? WHERE id=1',(now.wall,now.mono))
            return answer
        return self._retention_write(epoch,action)

    def retention_fail(self,epoch,checkpoint_id,*,descriptor_sha256,reason):
        if type(reason) is not str or reason not in FAILURES:raise AuthorityDenied('invalid_retention_failure')
        def action(db,run):
            row,descriptor=self._retention_row(db,epoch,checkpoint_id,descriptor_sha256)
            control=db.execute('SELECT * FROM domain_retention WHERE id=1').fetchone()
            if row['sequence']!=control['sequence']:raise AuthorityDenied('retention_identity')
            if row['failure'] is not None and row['failure']!=reason:raise AuthorityDenied('retention_failure_conflict')
            db.execute('UPDATE domain_retention SET failed=1 WHERE id=1')
            db.execute("UPDATE domain_retention_checkpoints SET failure=?,state=CASE WHEN state='RETAINED' THEN state ELSE 'FAILED' END WHERE id=?",(reason,checkpoint_id))
            return dict(status='retention_failed',checkpoint_id=checkpoint_id,reason=reason)
        return self._retention_write(epoch,action)

    def retention_register_terminal(self,epoch,*,terminal_identity,lifecycle):
        _sha(terminal_identity)
        if (type(lifecycle) is not dict or set(lifecycle)!={'logical_close','cleanup','evidence_sha256'} or
            lifecycle['logical_close'] not in ('committed','refused','unknown') or lifecycle['cleanup'] not in ('confirmed','unknown')):raise AuthorityDenied('invalid_terminal_evidence')
        _sha(lifecycle['evidence_sha256']);terminal=dict(identity=terminal_identity,lifecycle=json.loads(_json(lifecycle)))
        def action(db,run):
            control=db.execute('SELECT * FROM domain_retention WHERE id=1').fetchone()
            if control['terminal_id']:
                row=db.execute('SELECT * FROM domain_retention_checkpoints WHERE id=?',(control['terminal_id'],)).fetchone()
                if json.loads(row['effects'])['terminal']!=terminal:raise AuthorityDenied('terminal_identity_conflict')
                return json.loads(row['descriptor'])
            return self._retention_new(db,run,'terminal',terminal)
        return self._retention_write(epoch,action)
