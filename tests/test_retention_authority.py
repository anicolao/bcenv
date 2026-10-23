"""Pure real SQLite gate tests; no processes, games, Git or archive publication."""
import base64
from contextlib import closing
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

from services.epoch_authority import AuthorityDenied, AuthorityUnavailable, ClockSample, CommitUnknown
from services.resumable_authority import ResumableAuthority
from services.public_retention_authority import retention_descriptor_from_db, retention_history_from_db


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        patch=mock.patch.object(subprocess,'Popen',side_effect=AssertionError('no subprocess'));patch.start();self.addCleanup(patch.stop)
        self.now=100.;self.i=0
        self.config={'trial':{'retention':dict(schema='public-retention-v1',fixed_inputs_sha256='f'*64,repository_id=1431035719)}}
        self.a=self.create('a',self.config)
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:z.writestr('own/RobotPlayer.java','package own; public class RobotPlayer {}')
        self.source=dict(package='own',source_zip_base64=base64.b64encode(stream.getvalue()).decode())

    def create(self,name,config):
        return ResumableAuthority.create(self.root/(name+'.sqlite'),config=config,author='same-author',
            paths={k:str(self.root/name/k) for k in ('service','artifacts','mailbox','control','author')},
            clock=lambda:ClockSample('domain',self.now,self.now),duration=1000)

    def request(self,**body):
        self.i+=1;return self.a.request(0,'request'+str(self.i),body)['response']

    def ack(self,d=None,**overrides):
        d=d or self.a.retention_pending()
        args=dict(descriptor_sha256=d['descriptor_sha256'],inventory_sha256='a'*64,snapshot_binding_sha256='b'*64,
            repository_id=1431035719,commit_sha='c'*40,readback_sha256='d'*64,readback_verified=True)
        args.update(overrides)
        return self.a.retention_ack(d['epoch'],d['checkpoint_id'],**args)

    def batch(self):
        n=self.request(operation='note',text='own note',replay_references=[],**self.source)
        r=self.request(operation='submit',note_id=n['note_id'],note_sha256=n['note_sha256'],**self.source)
        self.assertEqual('accepted',r['status']);return r['submission_id']

    def terminal(self,**overrides):
        args=dict(terminal_identity='e'*64,lifecycle=dict(logical_close='refused',cleanup='unknown',evidence_sha256='9'*64))
        args.update(overrides);return self.a.retention_register_terminal(0,**args)

    def test_default_no_tables_no_status_change(self):
        legacy=self.create('legacy',{})
        self.assertIsNone(legacy.retention_pending());self.assertFalse(legacy.retention_gate(0)['enabled'])
        self.assertNotIn('retention',legacy.current_state())
        with closing(legacy._connect(readonly=True)) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='domain_retention'").fetchone())

    def test_initial_mutation_gate_charged_reads_stable_ack(self):
        d=self.a.retention_pending();self.assertEqual('initial',d['reason'])
        for operation in ('note','checkpoint','replay','lock_finalists','select_finalist','submit'):
            r=self.request(operation=operation)
            self.assertIn(r['status'],('error',))
        before=self.a.retention_pending()
        status=self.request(operation='validation_status')
        self.assertTrue(status['retention']['pending']);self.assertGreater(status['usage']['reads'],0)
        self.assertEqual(before,self.a.retention_pending())
        first=self.ack(d);self.assertEqual(first,self.ack(d));self.assertIsNone(self.a.retention_pending())
        with self.assertRaises(AuthorityDenied):self.ack(d,inventory_sha256='1'*64)

    def test_valid_note_pending_no_effect_debit_or_source(self):
        before=self.a.inspect()['counters']
        reply=self.request(operation='note',text='own',replay_references=[],**self.source)
        self.assertEqual('retention_pending',reply['reason'])
        after=self.a.inspect()['counters']
        self.assertEqual(before['notes'],after['notes']);self.assertEqual(before['mailbox']+1,after['mailbox'])
        with closing(self.a._connect(readonly=True)) as db:self.assertEqual(0,db.execute('SELECT count(*) FROM domain_sources').fetchone()[0])

    def test_already_running_replay_finishes_without_invalidating_gate(self):
        from tests.test_resumable_authority import DomainTests
        self.ack();one=self.batch();DomainTests.finish(self,one);self.ack()
        pending=self.request(operation='replay',submission_id=one,game_index=0,
            query=dict(round_start=0,round_end=1,robot_id=None,record_offset=0,record_limit=8))
        self.assertEqual('replay_pending',pending['status'])
        two=self.batch();self.a.start_batch(0,'starttwo',two)
        self.a.finish_batch(0,'finishtwo',two,dict(status='rejected',reason='compile_rejected'))
        d=self.a.retention_pending()
        self.a.finish_replay(0,'finishquery',pending['replay_id'],dict(submission_id=one,records=[]))
        self.assertEqual(d,self.a.retention_pending());self.ack(d)

    def test_atomic_finish_gate_keeps_queued_batch(self):
        self.ack();one=self.batch();two=self.batch()
        self.a.start_batch(0,'start',one)
        self.a.finish_batch(0,'finish',one,dict(status='rejected',reason='compile_rejected'))
        d=self.a.retention_pending();self.assertEqual('batch',d['reason'])
        with self.assertRaisesRegex(AuthorityDenied,'retention_pending'):self.a.start_batch(0,'next',two)
        self.assertEqual('QUEUED',self.a.batch(two)['state'])
        with closing(self.a._connect(readonly=True)) as db:
            run=db.execute('SELECT * FROM run').fetchone();self.assertEqual(d,retention_descriptor_from_db(db,run))
        self.ack(d);self.a.start_batch(0,'next',two)
        self.assertEqual('RUNNING',self.a.batch(two)['state'])

    def test_failure_sticky_and_retained_unknown_fail(self):
        d=self.a.retention_pending();self.ack(d)
        self.a.retention_fail(0,d['checkpoint_id'],descriptor_sha256=d['descriptor_sha256'],reason='ack_unknown')
        self.assertTrue(self.a.retention_gate(0)['failed'])
        with self.assertRaisesRegex(AuthorityDenied,'retention_failed'):self.a.start_batch(0,'s','none')
        with closing(self.a._connect(readonly=True)) as db:
            row=db.execute('SELECT state,ack FROM domain_retention_checkpoints').fetchone()
            self.assertEqual('RETAINED',row['state']);self.assertIsNotNone(row['ack'])

    def test_terminal_supersedes_expired_without_reset(self):
        old=self.a.retention_pending();before=self.a.current_state()['run'];self.now=1200
        with self.assertRaisesRegex(AuthorityDenied,'deadline_expired'):self.a.logical_close(0,'close')
        self.a.retention_fail(0,old['checkpoint_id'],descriptor_sha256=old['descriptor_sha256'],reason='publication_unknown')
        terminal=self.terminal();self.assertEqual('terminal',terminal['reason'])
        with self.assertRaisesRegex(AuthorityDenied,'retention_superseded'):self.ack(old)
        self.ack(terminal);self.assertEqual(terminal,self.terminal())
        gate=self.a.retention_gate(0);self.assertTrue(gate['terminal']);self.assertTrue(gate['failed']);self.assertFalse(gate['pending'])
        self.assertEqual(before,self.a.current_state()['run'])
        with closing(self.a._connect(readonly=True)) as db:
            effects=json.loads(db.execute("SELECT effects FROM domain_retention_checkpoints WHERE reason='terminal'").fetchone()[0])
            self.assertEqual(old['checkpoint_id'],effects['unretained_gap']['checkpoint_id']);self.assertTrue(effects['sticky_failure'])
            self.assertTrue(effects['observed_state']['expired']);self.assertFalse(effects['observed_state']['logical_closed'])

    def test_ack_types_binding_epoch_and_expiry(self):
        d=self.a.retention_pending()
        for override in ({'readback_verified':1},{'repository_id':True},{'commit_sha':'x'*40},{'descriptor_sha256':'0'*64}):
            with self.assertRaises(AuthorityDenied):self.ack(d,**override)
        with self.assertRaisesRegex(AuthorityDenied,'stale_epoch'):self.a.retention_gate(1)
        self.now=1200
        with self.assertRaisesRegex(AuthorityDenied,'deadline_expired'):self.ack(d)
        self.assertTrue(self.a.retention_gate(0)['pending'])

    def test_pending_advance_reopen_and_terminal_mutations_refused(self):
        paths={k:str(self.root/'new'/k) for k in ('service','artifacts','mailbox','control','author')}
        with self.assertRaisesRegex(AuthorityDenied,'retention_pending'):self.a.advance(0,'advance',paths)
        self.terminal();self.assertEqual('retention_terminal',self.request(operation='checkpoint',text='x',submission_ids=[])['reason'])
        with self.assertRaisesRegex(AuthorityDenied,'retention_terminal'):self.a.logical_close(0,'close')

    def test_commit_ambiguity_poisoned_no_success(self):
        def uncertain(db):db.commit();raise OSError('lost return')
        with mock.patch.object(self.a,'_commit',side_effect=uncertain):
            with self.assertRaises(CommitUnknown):self.ack()
        with self.assertRaisesRegex(AuthorityUnavailable,'writer_closed'):self.terminal()

    def test_max28_and_no_terminal_revision(self):
        self.ack()
        # Exercise the finite private SQL set allocator without game fixtures.
        for i in range(26):
            self.a._retention_write(0,lambda db,run:self.a._retention_new(db,run,'batch'));self.ack()
        d=self.terminal();self.assertEqual(27,d['sequence']);self.ack(d)
        with self.assertRaisesRegex(AuthorityDenied,'terminal_identity_conflict'):self.terminal(terminal_identity='a'*64)
        with self.assertRaisesRegex(AuthorityDenied,'retention_terminal'):
            self.a._retention_write(0,lambda db,run:self.a._retention_new(db,run,'batch'))

    def test_expired_terminal_includes_unabandoned_queued_batch(self):
        self.ack();sid=self.batch();self.now=1200;d=self.terminal()
        self.assertIn(self.a.authority_id,d['checkpoint_id'])
        with closing(self.a._connect(readonly=True)) as db:
            effects=json.loads(db.execute("SELECT effects FROM domain_retention_checkpoints WHERE reason='terminal'").fetchone()[0])
            self.assertEqual([(sid,'QUEUED')],[(r['id'],r['state']) for r in effects['batches']])

    def test_typed_history_preserves_terminal_gap_and_ack(self):
        first=self.a.retention_pending();self.ack();sid=self.batch();self.a.start_batch(0,'start',sid)
        self.a.finish_batch(0,'finish',sid,dict(status='rejected',reason='compile_rejected'))
        pending=self.a.retention_pending()
        self.a.retention_fail(0,pending['checkpoint_id'],descriptor_sha256=pending['descriptor_sha256'],reason='publication_unknown')
        self.terminal()
        with closing(self.a._connect(readonly=True)) as db:
            rows=retention_history_from_db(db,db.execute('SELECT * FROM run').fetchone())
        self.assertEqual(first,rows[0]['ack']['descriptor'])
        self.assertEqual('SUPERSEDED',rows[1]['state'])
        self.assertEqual('publication_unknown',rows[2]['effects']['unretained_gap']['failure'])
        self.assertEqual('unknown',rows[2]['effects']['terminal']['lifecycle']['cleanup'])

    def test_history_rejects_corrupt_or_extra_effect_fields_even_rehashed(self):
        from services.epoch_authority import _hash,_json
        with closing(self.a._connect()) as db:
            row=db.execute('SELECT * FROM domain_retention_checkpoints').fetchone()
            effects=json.loads(row['effects']);effects['command']='/private/path'
            d=json.loads(row['descriptor']);d['effect_sha256']=_hash(effects)
            d['descriptor_sha256']=_hash({k:v for k,v in d.items() if k!='descriptor_sha256'})
            db.execute('UPDATE domain_retention_checkpoints SET effects=?,descriptor=?',(_json(effects),_json(d)))
            with self.assertRaises(AuthorityUnavailable):retention_history_from_db(db,db.execute('SELECT * FROM run').fetchone())
            db.execute('UPDATE domain_retention_checkpoints SET effects=?',(row['effects'],))
            with self.assertRaises(AuthorityUnavailable):retention_history_from_db(db,db.execute('SELECT * FROM run').fetchone())

    def test_known_failure_halt_not_cleared_by_ack(self):
        self.ack();sid=self.batch();self.a.start_batch(0,'start',sid)
        self.a.finish_batch(0,'failed',sid,dict(status='infrastructure_failure',reason='known_failure'))
        self.ack();self.assertTrue(self.a.current_state()['meta']['halted'])


if __name__=='__main__':unittest.main()
