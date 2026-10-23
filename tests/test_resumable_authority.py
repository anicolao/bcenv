"""Pure SQLite/domain tests: no process, game, or native task execution."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from services.epoch_authority import AuthorityDenied, ClockSample, _hash
from services.resumable_authority import ResumableAuthority


def archive(text='package own; public class RobotPlayer {}'):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as z:z.writestr('own/RobotPlayer.java',text)
    return base64.b64encode(output.getvalue()).decode()

def paths(n):return {k:'/epoch'+str(n)+'/'+k for k in ('service','artifacts','mailbox','control','author')}

class DomainTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=[100,200]
        self.clock=lambda:ClockSample('boot',self.now[0],self.now[1])
        self.a=ResumableAuthority.create(Path(self.tmp.name)/'db',config={},author='/same/task',paths=paths(0),clock=self.clock)
        self.i=0
    def req(self,body,epoch=0,prepared=None,rid=None):
        self.i+=1
        return self.a.request(epoch,rid or 'r'+str(self.i),body,attempt_id='a'+str(self.i),prepared=prepared)
    def submit(self,text='package own; public class RobotPlayer {}'):
        body={'package':'own','source_zip_base64':archive(text)}
        note=self.req(dict(body,operation='note',text='own prediction',replay_references=[]))['response']
        result=self.req(dict(body,operation='submit',note_id=note['note_id'],note_sha256=note['note_sha256']))
        return result['response']['submission_id'],result
    def finish(self,sid,epoch=0):
        self.a.start_batch(epoch,'start'+sid,sid)
        for i in range(8):self.a.before_game(epoch,'g'+sid+str(i),sid,i)
        result={'status':'complete','games':[{'status':'completed','replay_sha256':'a'*64,'own_slot':'A'} for _ in range(8)],'wins':0,'losses':8}
        manifest={'source_sha256':self.a.batch(sid)['source'],'files':{'replay':'a'*64}}
        return self.a.finish_batch(epoch,'f'+sid,sid,result,manifest)
    def test_note_submit_atomic_and_duplicate_delivery(self):
        sid,response=self.submit();self.assertEqual('batch',response['work']['kind'])
        self.assertEqual(1,self.a.inspect()['counters']['versions'])
        before=self.a.inspect()['counters']['mailbox']
        request={'operation':'validation_status'}
        first=self.req(request,rid='stable')
        second=self.req(request,rid='stable')
        self.assertEqual(first['response'],second['response']);self.assertIsNone(second['work'])
        self.assertEqual(before+2,self.a.inspect()['counters']['mailbox'])
    def test_denial_retains_read_and_mailbox_not_payload(self):
        out=self.req({'operation':'bogus'})
        self.assertEqual('error',out['response']['status'])
        self.assertEqual(1,self.a.inspect()['counters']['reads'])
        self.assertEqual(1,self.a.inspect()['counters']['mailbox'])
    def test_parent_running_child_games_do_not_hit_active_limit(self):
        sid,_=self.submit();self.finish(sid)
        self.assertEqual('COMPLETE',self.a.batch(sid)['state'])
        self.assertEqual(8,self.a.inspect()['counters']['training_games'])
    def test_queued_and_running_unknown_no_refund(self):
        a,_=self.submit();b,_=self.submit('package own; public class RobotPlayer { int x; }')
        self.a.start_batch(0,'start',a);self.a.before_game(0,'game',a,0)
        self.a.advance(0,'advance',paths(1))
        self.assertEqual({'UNKNOWN'},{r['state'] for r in self.a.inspect()['batches']})
        self.assertEqual(2,self.a.inspect()['counters']['versions'])
        with self.assertRaises(AuthorityDenied):self.a.before_game(0,'late',a,1)
        proof={'author':'/same/task','epoch':1,'same_existing_task':True,'proof_digest':'a'*64,'completed_manifests':{}}
        self.a.reopen(1,'open','/same/task','a'*64,verified=proof)
        body={'package':'own','source_zip_base64':archive()}
        note=self.req(dict(body,operation='note',text='again',replay_references=[]),epoch=1)['response']
        denied=self.req(dict(body,operation='submit',note_id=note['note_id'],note_sha256=note['note_sha256']),epoch=1)
        self.assertEqual('unknown_fingerprint',denied['response']['reason'])
    def test_replay_overlaps_background_batch_and_commits_reference(self):
        sid,_=self.submit();self.finish(sid)
        pending,_=self.submit('package own; public class RobotPlayer { int y; }')
        self.a.start_batch(0,'pending',pending)
        q={'round_start':1,'round_end':5,'robot_id':None,'record_offset':0,'record_limit':128}
        req=self.req(dict(operation='replay',submission_id=sid,game_index=0,query=q),rid='query')
        replay=req['work']['id']
        result=self.a.finish_replay(0,'replaydone',replay,{'submission_id':sid,'job_id':'j_'+sid[2:]})
        self.assertEqual('replay',result['response']['status'])
        receipt=self.req({'operation':'receipt','request_id':'query'})
        self.assertEqual(req['response'],receipt['response']['response'])
        terminal=self.req({'operation':'replay_result','replay_id':replay})
        self.assertEqual(result['response'],terminal['response'])
    def test_lock_and_selection_complete(self):
        a,_=self.submit();self.finish(a)
        b,_=self.submit('package own; public class RobotPlayer { int y; }');self.finish(b)
        pins={sid:_hash(json.loads(self.a.batch(sid)['manifest'])) for sid in (a,b)}
        lock=self.req(dict(operation='lock_finalists',submission_ids=[a,b],provisional_choice=a),prepared={'verified_manifests':pins})
        self.assertEqual('validation',lock['work']['kind'])
        for vid in lock['work']['ids']:self.finish(vid)
        allpins=dict(pins,**{vid:self.a.batch(vid)['manifest_sha256'] for vid in lock['work']['ids']})
        selected=self.req(dict(operation='select_finalist',submission_id=a),prepared={'verified_manifests':allpins})
        self.assertEqual('selected',selected['response']['phase'])
        self.assertEqual(16,self.a.inspect()['counters']['validation_games'])
    def test_interrupted_validation_stays_incomplete(self):
        a,_=self.submit();self.finish(a)
        b,_=self.submit('package own; public class RobotPlayer { int y; }');self.finish(b)
        pins={sid:_hash(json.loads(self.a.batch(sid)['manifest'])) for sid in (a,b)}
        self.req(dict(operation='lock_finalists',submission_ids=[a,b],provisional_choice=a),prepared={'verified_manifests':pins})
        self.a.advance(0,'advance',paths(1))
        self.assertEqual('incomplete',self.a.inspect()['meta']['phase'])
        proof={'author':'/same/task','epoch':1,'same_existing_task':True,'proof_digest':'a'*64,'completed_manifests':pins}
        with self.assertRaises(AuthorityDenied):self.a.reopen(1,'open','/same/task','a'*64,verified=proof)
    def test_stale_halt_close_and_storage_never_change_new_epoch(self):
        self.a.advance(0,'advance',paths(1))
        for call in (lambda:self.a.halt(0,'late'),lambda:self.a.logical_close(0,'close'),lambda:self.a.storage_observation(0,'storage',2**31)):
            with self.assertRaises(AuthorityDenied):call()
        self.assertEqual(0,self.a.inspect()['meta']['halted'])
        self.assertEqual(0,self.a.inspect()['control']['max_storage'])
    def test_recovery_proof_required_no_deadline_reset(self):
        old=self.a.inspect()['run']['mono_deadline'];self.a.advance(0,'advance',paths(1))
        with self.assertRaises(AuthorityDenied):self.a.reopen(1,'open','/same/task','a'*64)
        self.assertEqual(old,self.a.inspect()['run']['mono_deadline'])
    def test_known_failure_sticky(self):
        sid,_=self.submit();self.a.finish_batch(0,'failed',sid,{'status':'infrastructure_failure'})
        self.a.advance(0,'advance',paths(1))
        proof={'author':'/same/task','epoch':1,'same_existing_task':True,'proof_digest':'a'*64,'completed_manifests':{}}
        with self.assertRaises(AuthorityDenied):self.a.reopen(1,'open','/same/task','a'*64,verified=proof)

    def test_invalid_types_are_charged_denials_not_poison(self):
        for body in ({'operation':'receipt','request_id':{}},
                     {'operation':'checkpoint','text':'\ud800','submission_ids':[]},
                     {'operation':'result','submission_id':[]},
                     {'operation':'submit','package':'own','source_zip_base64':archive(),'note_id':{},'note_sha256':'x'}):
            self.assertEqual('error',self.req(body)['response']['status'])
        self.assertEqual('validation_status',self.req({'operation':'validation_status'})['response']['status'])
    def test_payload_error_rolls_back_consumption(self):
        sid,_=self.submit()
        body={'operation':'lock_finalists','submission_ids':[sid,'missing'],'provisional_choice':sid}
        before=self.a.inspect()
        self.assertEqual('error',self.req(body)['response']['status'])
        after=self.a.inspect()
        self.assertEqual(before['meta'],after['meta'])
        self.assertEqual(before['batches'],after['batches'])
        self.assertEqual(before['counters']['mailbox']+1,after['counters']['mailbox'])
    def test_unexpected_transition_exception_rolls_back_everything(self):
        from unittest.mock import patch
        from services.epoch_authority import AuthorityUnavailable
        def bad(db,*args):
            db.execute("UPDATE counters SET used=used+1 WHERE name='notes'")
            raise RuntimeError('injected')
        with patch.object(self.a,'_request_payload',bad):
            with self.assertRaises(AuthorityUnavailable):self.req({'operation':'validation_status'})
        self.assertEqual(0,self.a.inspect()['counters']['mailbox'])
        self.assertEqual(0,self.a.inspect()['counters']['notes'])
    def test_uncertain_commit_keeps_original_receipt_without_execution_retry(self):
        from unittest.mock import patch
        from services.epoch_authority import CommitUnknown
        def uncertain(db):
            db.commit();raise OSError('lost commit acknowledgement')
        with patch.object(self.a,'_commit',uncertain):
            with self.assertRaises(CommitUnknown):self.req({'operation':'validation_status'},rid='stable')
        retained=self.a.receipt('a1')
        self.assertEqual('validation_status',retained['response']['status'])
        self.assertEqual(1,self.a.inspect()['counters']['mailbox'])
    def test_close_marks_all_outstanding_unknown(self):
        sid,_=self.submit()
        self.a.logical_close(0,'close')
        self.assertEqual('UNKNOWN',self.a.batch(sid)['state'])
        self.assertEqual(1,self.a.inspect()['control']['closed'])
        self.a.advance(0,'advance',paths(1))
        self.assertEqual(0,self.a.inspect()['control']['closed'])
    def test_recovery_storage_is_explicit_and_sticky(self):
        self.a.advance(0,'advance',paths(1))
        with self.assertRaises(AuthorityDenied):self.a.storage_observation(1,'bad',10)
        self.a.storage_observation(1,'scan',1073741825,recovery=True)
        self.assertEqual(1073741825,self.a.inspect()['control']['max_storage'])
        self.assertEqual(1,self.a.inspect()['meta']['halted'])
    def test_clock_and_epoch_denials_have_no_counter_side_effects(self):
        self.now[0]=99
        with self.assertRaises(AuthorityDenied):self.req({'operation':'validation_status'})
        self.assertEqual(0,self.a.inspect()['counters']['mailbox'])
    def test_sameid_different_payload_is_denied_and_no_work(self):
        self.req({'operation':'validation_status'},rid='same')
        result=self.req({'operation':'bogus'},rid='same')
        self.assertEqual('identity_conflict',result['response']['reason'])
        self.assertIsNone(result['work'])
        self.assertEqual(2,self.a.inspect()['counters']['mailbox'])
    def test_checkpoint_bound_and_sources_are_only_committed_ids(self):
        bad=self.req({'operation':'checkpoint','text':'plan','submission_ids':['missing']})
        self.assertEqual('error',bad['response']['status'])
        sid,_=self.submit()
        good=self.req({'operation':'checkpoint','text':'known source; own questions','submission_ids':[sid]})
        self.assertEqual('checkpoint',good['response']['status'])
        self.assertEqual(good['response'],json.loads(self.a.inspect()['meta']['checkpoint']))
    def test_second_batch_cannot_start_but_replay_is_not_a_batch(self):
        a,_=self.submit();b,_=self.submit('package own; public class RobotPlayer { int z; }')
        self.a.start_batch(0,'start',a)
        with self.assertRaises(AuthorityDenied):self.a.start_batch(0,'start2',b)
        self.a.before_game(0,'game',a,0)
        with self.assertRaises(AuthorityDenied):self.a.before_game(0,'gameagain',a,0)
        self.assertEqual(1,self.a.inspect()['counters']['training_games'])

    def test_large_internal_manifest_does_not_expand_public_response(self):
        from services.resumable_authority import manifest_digest
        sid,_=self.submit();self.a.start_batch(0,'start',sid)
        for i in range(8):self.a.before_game(0,'g'+str(i),sid,i)
        names=['build/classes/'+('long_name_'*8)+str(i)+'.class' for i in range(449)]
        manifest={'source_sha256':self.a.batch(sid)['source'],'files':{n:'a'*64 for n in names},'sizes':{n:1 for n in names}}
        self.assertGreater(len(json.dumps(manifest)),65536)
        result={'status':'complete','games':[{'status':'completed','replay_sha256':'a'*64,'own_slot':'A'} for _ in range(8)]}
        finished=self.a.finish_batch(0,'finish',sid,result,manifest)
        self.assertLess(len(json.dumps(finished)),65536)
        self.assertEqual(manifest_digest(manifest),self.a.batch(sid)['manifest_sha256'])
        with self.assertRaises(AuthorityDenied):self.a.finish_batch(0,'oversized',sid,result,{'extra':'x'*1048577})
    def test_core_seams_cannot_bypass_domain(self):
        for call in (self.a.admit,self.a.start,self.a.finalize):
            with self.assertRaises(AuthorityDenied):call()
    def test_interrupted_replay_preserves_original_ingress_and_exposes_unknown_terminal(self):
        sid,_=self.submit();self.finish(sid)
        q={'round_start':1,'round_end':5,'robot_id':None,'record_offset':0,'record_limit':128}
        admitted=self.req(dict(operation='replay',submission_id=sid,game_index=0,query=q),rid='query')
        self.a.advance(0,'advance',paths(1))
        history={r['id']:r['response'] for r in self.a.candidate_history()}
        self.assertEqual(admitted['response'],history['query'])
        self.assertEqual('UNKNOWN',self.a.replay(admitted['work']['id'])['state'])

    def test_unknown_replay_effect_is_source_artifact_not_request_or_batch_id(self):
        a,_=self.submit();self.finish(a)
        b,_=self.submit();self.finish(b)
        q={'round_start':1,'round_end':5,'robot_id':None,'record_offset':0,'record_limit':128}
        pending=self.req(dict(operation='replay',submission_id=a,game_index=0,query=q))
        self.a.advance(0,'advance',paths(1))
        pins={sid:self.a.batch(sid)['manifest_sha256'] for sid in (a,b)}
        proof={'author':'/same/task','epoch':1,'same_existing_task':True,'proof_digest':'a'*64,'completed_manifests':pins}
        self.a.reopen(1,'open','/same/task','a'*64,verified=proof)
        duplicate=self.req(dict(operation='replay',submission_id=b,game_index=0,query=dict(q,mode='actions')),epoch=1)
        self.assertEqual('unknown_fingerprint',duplicate['response']['reason'])
        distinct=self.req(dict(operation='replay',submission_id=b,game_index=0,query=dict(q,mode='own_diagnostics')),epoch=1)
        self.assertEqual('replay_pending',distinct['response']['status'])
    def test_readonly_integrity_failure_poisons_writer(self):
        from services.epoch_authority import AuthorityUnavailable
        sid,_=self.submit()
        with self.a._connect() as db:db.execute('UPDATE domain_sources SET archive=?',(b'corrupt',))
        with self.assertRaises(AuthorityUnavailable):self.a.source(self.a.batch(sid)['source'])
        with self.assertRaises(AuthorityUnavailable):self.req({'operation':'validation_status'})

    def test_exact_version_and_note_caps_no_partial_note_use(self):
        for i in range(24):
            sid,_=self.submit()
            self.a.start_batch(0,'start'+str(i),sid)
            self.a.finish_batch(0,'reject'+str(i),sid,{'status':'rejected','reason':'compile_rejected'})
        body={'package':'own','source_zip_base64':archive()}
        note=self.req(dict(body,operation='note',text='at cap',replay_references=[]))['response']
        denied=self.req(dict(body,operation='submit',note_id=note['note_id'],note_sha256=note['note_sha256']))
        self.assertEqual('versions_limit',denied['response']['reason'])
        self.assertEqual(24,self.a.inspect()['counters']['versions'])
        self.assertEqual(0,next(r['used'] for r in self.a.inspect()['notes'] if r['id']==note['note_id']))
        for i in range(47):self.req(dict(body,operation='note',text='bounded own note',replay_references=[]))
        last=self.req(dict(body,operation='note',text='over cap',replay_references=[]))
        self.assertEqual('notes_limit',last['response']['reason'])
        self.assertEqual(72,self.a.inspect()['counters']['notes'])
    def test_replay_read_and_mailbox_limits_are_not_refunded_or_exceeded(self):
        sid,_=self.submit();self.finish(sid)
        with self.a._connect() as db:
            db.execute("UPDATE counters SET used=449 WHERE name='replays'")
            db.execute("UPDATE counters SET used=1998 WHERE name='reads'")
        q={'round_start':1,'round_end':5,'robot_id':None,'record_offset':0,'record_limit':128}
        self.assertEqual('replay_pending',self.req(dict(operation='replay',submission_id=sid,game_index=0,query=q))['response']['status'])
        self.assertEqual('replays_limit',self.req(dict(operation='replay',submission_id=sid,game_index=0,query=q))['response']['reason'])
        self.assertEqual('reads_limit',self.req({'operation':'validation_status'})['response']['reason'])
        self.assertEqual(450,self.a.inspect()['counters']['replays'])
        self.assertEqual(2000,self.a.inspect()['counters']['reads'])
        with self.a._connect() as db:db.execute("UPDATE counters SET used=2200 WHERE name='mailbox'")
        self.assertEqual('mailbox_limit',self.req({'operation':'validation_status'})['response']['reason'])
        self.assertEqual(2200,self.a.inspect()['counters']['mailbox'])
    def test_last_game_admission_cap_counts_admission_not_launch(self):
        sid,_=self.submit();self.a.start_batch(0,'start',sid)
        with self.a._connect() as db:db.execute("UPDATE counters SET used=191 WHERE name='training_games'")
        admitted=self.a.before_game(0,'last',sid,0)
        self.assertEqual('not_attested',admitted['physical_launch'])
        with self.assertRaises(AuthorityDenied):self.a.before_game(0,'over',sid,1)
        self.assertEqual(192,self.a.inspect()['counters']['training_games'])
        self.assertEqual(1,len(self.a.inspect()['games']))

    def test_public_response_headroom_and_canonical_wrapper(self):
        from services.resumable_authority import public_json
        original={'x':'x'*59992}
        self.assertEqual(60000,len(public_json(original).encode()))
        with self.assertRaises(AuthorityDenied):public_json({'x':'x'*59993})
        wrapper={'schema':'bcenv.resumable.v1','status':'receipt','request_id':'a'*128,'origin_epoch':0,'request_digest':'b'*64,'response':original}
        wrapped=public_json(wrapper,receipt=True)
        envelope={'schema':'bcenv.resumable-mailbox.v1','authority_id':'c'*32,'epoch':0,'request_id':'d'*32,'request_digest':'e'*64,'response':json.loads(wrapped)}
        self.assertLess(len(json.dumps(envelope).encode()),65536)
        with self.assertRaises(AuthorityDenied):public_json({'x':'x'*64000},receipt=True)
    def test_config_corruption_fails_before_clock_or_read_mutation(self):
        from services.epoch_authority import AuthorityUnavailable
        with self.a._connect() as db:db.execute("UPDATE run SET config='{}'")
        with self.assertRaises(AuthorityUnavailable):self.a.current_state()
        with self.assertRaises(AuthorityUnavailable):self.req({'operation':'validation_status'})
    def test_transition_itself_validates_config_even_without_readidentity_call(self):
        from services.epoch_authority import AuthorityUnavailable
        with self.a._connect() as db:db.execute("UPDATE run SET config='{}'")
        with self.assertRaises(AuthorityUnavailable):self.req({'operation':'validation_status'})
    def test_selection_requires_validation_and_original_manifest_pins(self):
        a,_=self.submit();self.finish(a)
        b,_=self.submit('package own; public class RobotPlayer { int x; }');self.finish(b)
        pins={sid:self.a.batch(sid)['manifest_sha256'] for sid in (a,b)}
        locked=self.req(dict(operation='lock_finalists',submission_ids=[a,b],provisional_choice=a),prepared={'verified_manifests':pins})
        for sid in locked['work']['ids']:self.finish(sid)
        rejected=self.req(dict(operation='select_finalist',submission_id=a),prepared={'verified_manifests':pins})
        self.assertEqual('selection_evidence_unverified',rejected['response']['reason'])
        self.assertIsNone(self.a.inspect()['meta']['selected'])

    def test_status_remaining_uses_current_original_clock_not_previous_receipt(self):
        self.now[0]+=30;self.now[1]+=30
        result=self.req({'operation':'validation_status'})['response']
        self.assertEqual(21570,result['remaining_seconds'])
        self.assertEqual(21800,result['deadline_monotonic'])
        self.assertEqual('resumable-v1',result['profile'])

    def test_locked_notes_denied_and_selected_only_immutable_reads(self):
        a,_=self.submit();self.finish(a)
        b,_=self.submit('package own; public class RobotPlayer { int x; }');self.finish(b)
        pins={sid:self.a.batch(sid)['manifest_sha256'] for sid in (a,b)}
        locked=self.req(dict(operation='lock_finalists',submission_ids=[a,b],provisional_choice=a),prepared={'verified_manifests':pins})
        note=dict(operation='note',package='own',source_zip_base64=archive(),text='not a new experiment',replay_references=[])
        self.assertEqual('training_closed',self.req(note)['response']['reason'])
        checkpoint={'operation':'checkpoint','text':'own locked work record','submission_ids':[a,b]}
        self.assertEqual('checkpoint',self.req(checkpoint)['response']['status'])
        for sid in locked['work']['ids']:self.finish(sid)
        pins.update({sid:self.a.batch(sid)['manifest_sha256'] for sid in locked['work']['ids']})
        self.req(dict(operation='select_finalist',submission_id=a),prepared={'verified_manifests':pins})
        q={'round_start':1,'round_end':5,'robot_id':None,'record_offset':0,'record_limit':128}
        for body in (note,checkpoint,dict(operation='replay',submission_id=a,game_index=0,query=q)):
            self.assertEqual('selection_complete',self.req(body)['response']['reason'])
        self.assertEqual('complete',self.req({'operation':'result','submission_id':a})['response']['status'])
        self.assertEqual('selected',self.req({'operation':'validation_status'})['response']['phase'])
        with self.assertRaises(AuthorityDenied):self.a.advance(0,'forbidden',paths(1))
        self.assertEqual(a,self.a.inspect()['meta']['selected'])

if __name__=='__main__':unittest.main()
