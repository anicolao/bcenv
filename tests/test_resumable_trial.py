"""Real orchestration plus real temporary SQLite; adapter/executor are inert fakes.

No JVM, subprocess, signal, native task or runtime fault-injection fixture.
"""
import base64
import hashlib
import io
import json
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import uuid
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from services.epoch_authority import AuthorityDenied, AuthorityUnavailable, ClockSample, CommitUnknown
from services.resumable_authority import ResumableAuthority
from services import resumable_trial as module
from runtime.cancellation import cancelled


class QueuedExecutor:
    def __init__(self, **kw):
        self.calls=[];self.closed=False
    def submit(self,fn,*args):
        self.calls.append((fn,args))
        return object()
    def run_next(self):
        fn,args=self.calls.pop(0)
        return fn(*args)
    def shutdown(self,**kw):
        self.closed=True


class Worker:
    def __init__(self,command,games,timeout,**kw):
        self.games=games;self.sources=[];self.behavior=None;self.replay_calls=[]
    def run(self,root,source):
        self.sources.append(source)
        if self.behavior:return self.behavior(root,source)
        return self.run_games(root)
    def run_games(self,root):
        games=[]
        for i,g in enumerate(self.games):
            self.before_game(root.name,i)
            games.append(dict(index=i,label=g.label,own_slot=g.own_slot,status='completed',
                              replay_sha256='a'*64,outcome='win'))
        return dict(status='complete',games=games,wins=8,losses=0)
    def replay(self,root,sid,index,query,replay_hash,**kw):
        self.replay_calls.append((sid,index,query,kw))
        return dict(submission_id=sid,job_id='j_'+sid[2:],records=[])


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.popen=mock.patch.object(subprocess,'Popen',side_effect=AssertionError('no subprocess fixture'))
        self.popen.start();self.addCleanup(self.popen.stop)
        self.input_patch=mock.patch.object(module,'fixed_inputs',return_value={})
        self.input_patch.start();self.addCleanup(self.input_patch.stop)
        self.config={'profile':'resumable-v1','command':['/inert/bcenv'],
                     'timeout':120,'match_timeout':300}
        for k in ('games','validation_games'):
            self.config[k]=[dict(label=k+str(i),opponent_build='/inert/opponent',map_path='/inert/map',
                                 own_slot='A' if i%2==0 else 'B') for i in range(8)]
        self.paths=self.new_paths('epoch0')
        self.authority=ResumableAuthority.create(self.root/'authority.sqlite',
            config={'trial':self.config,'inputs':{}},author='/root/existing_author',paths=self.paths,
            clock=lambda:ClockSample('test-domain',time.time(),time.monotonic()))
        self.captured=[]
        def capture(root,batch,result,deadline,games):
            self.captured.append((batch['id'],result['status'],deadline))
            if result['status']!='complete':return None
            return dict(source_sha256=batch['source'],batch_id=batch['id'],files={},build_identity={})
        self.trial=module.ResumableTrial(self.authority,0,worker_factory=Worker,
                                         executor_factory=QueuedExecutor,capture=capture)
        for key in ('author','control','mailbox'):Path(self.paths[key]).mkdir(parents=True)
        self.expected_close_unavailable=False
        self.expected_cleanup_unverified=False
        self.addCleanup(self.cleanup_trial)

    def cleanup_trial(self):
        try:self.trial.close()
        except AuthorityUnavailable:
            if not self.expected_close_unavailable:raise
            self.assertFalse((self.trial.root/'cleanup.json').exists())
        except ValueError as error:
            if not self.expected_cleanup_unverified or str(error)!='cooperative_cleanup_unverified':raise
            self.assertFalse((self.trial.root/'cleanup.json').exists())
            self.trial.cancellation.close()  # Release this inert fixture's descriptors, not a success receipt.

    def new_paths(self,name):
        return {key:str(self.root/name/key) for key in ('service','artifacts','mailbox','control','author')}

    def body(self,variant='one'):
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as archive:
            archive.writestr('bot/RobotPlayer.java','package bot; public class RobotPlayer {} // '+variant)
        return {'package':'bot','source_zip_base64':base64.b64encode(data.getvalue()).decode()}

    def call(self,body,rid=None):
        return self.trial.request(rid or uuid.uuid4().hex,body)

    def submit(self,variant='one'):
        body=self.body(variant)
        note=self.call(dict(body,operation='note',text='Own prospective test note.',replay_references=[]))
        self.assertEqual(note['status'],'noted')
        request=dict(body,operation='submit',note_id=note['note_id'],note_sha256=note['note_sha256'])
        rid=uuid.uuid4().hex
        response=self.call(request,rid)
        self.assertEqual(response['status'],'accepted')
        return response['submission_id'],rid,request

    def complete(self,variant='one'):
        sid,_,_=self.submit(variant)
        self.trial.executor.run_next()
        self.assertEqual(self.authority.batch(sid)['state'],'COMPLETE')
        return sid

    def replay(self,sid):
        return dict(operation='replay',submission_id=sid,game_index=0,
                    query=dict(round_start=1,round_end=5,robot_id=None,record_offset=0,record_limit=128))

    def test_admission_schedules_exactly_once_duplicate_charges_no_new_work(self):
        sid,rid,body=self.submit()
        prior=self.authority.inspect()['counters']
        reply=self.call(body,rid)
        after=self.authority.inspect()['counters']
        self.assertEqual(reply['submission_id'],sid)
        self.assertEqual(len(self.trial.executor.calls),1)
        self.assertEqual(after['versions'],prior['versions'])
        self.assertEqual(after['mailbox'],prior['mailbox']+1)

    def test_worker_receives_durable_original_source_and_nested_games_admit(self):
        sid,_,body=self.submit()
        body['source_zip_base64']='mutated after committed admission'
        self.trial.executor.run_next()
        worker=self.trial.workers[0]
        self.assertEqual(len(worker.sources),1)
        source=worker.sources[0]
        self.assertIn(b'// one',source.files[0].data)
        self.assertEqual(source.content_sha256,self.authority.batch(sid)['source'])
        self.assertEqual(self.authority.batch(sid)['state'],'COMPLETE')
        self.assertEqual(self.authority.inspect()['counters']['training_games'],8)
        self.assertEqual(self.captured[0][2],self.authority.inspect()['run']['mono_deadline'])

    def test_nested_duplicate_game_denied_without_second_debit(self):
        sid,_,_=self.submit()
        self.authority.start_batch(0,'manual_start',sid)
        self.trial.before_game(sid,0)
        with self.assertRaises(AuthorityDenied):self.trial.before_game(sid,0)
        self.assertEqual(self.authority.inspect()['counters']['training_games'],1)

    def test_queued_unknown_is_never_discovered_or_run_after_advance(self):
        sid,_,_=self.submit()
        self.authority.advance(0,'advance',self.new_paths('epoch1'))
        self.trial.executor.run_next()
        self.assertEqual(self.authority.batch(sid)['state'],'UNKNOWN')
        self.assertEqual(self.trial.workers[0].sources,[])
        self.assertTrue(self.trial.stop_requested.is_set())

    def test_replay_can_finish_while_background_batch_is_running(self):
        first=self.complete('first')
        second,_,_=self.submit('second')
        entered=threading.Event();release=threading.Event();errors=[]
        worker=self.trial.workers[0]
        def blocked(root,source):
            entered.set()
            if not release.wait(30):raise AssertionError('test cleanup release missing')
            return worker.run_games(root)
        worker.behavior=blocked
        def run():
            try:self.trial.executor.run_next()
            except BaseException as error:errors.append(error)
        thread=threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(entered.wait(10))
            self.assertEqual(self.authority.batch(second)['state'],'RUNNING')
            with mock.patch.object(module,'checked_manifest',return_value='a'*64):
                response=self.call(self.replay(first))
            self.assertEqual(response['status'],'replay_pending')
            before=self.authority.inspect()['counters']['reads']
            terminal=self.call(dict(operation='replay_result',replay_id=response['replay_id']))
            self.assertEqual(terminal['status'],'replay')
            self.assertEqual(self.authority.inspect()['counters']['reads'],before+1)
            self.assertEqual(self.authority.batch(second)['state'],'RUNNING')
            self.assertEqual(len(worker.replay_calls),1)
        finally:
            release.set();thread.join(30)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])

    def test_epoch_change_while_worker_blocked_rejects_late_finish_and_cancels_token(self):
        sid,_,_=self.submit()
        entered=threading.Event();release=threading.Event();errors=[]
        def blocked(root,source):
            entered.set()
            if not release.wait(30):raise AssertionError('test cleanup release missing')
            return {'status':'rejected','reason':'compile_rejected'}
        self.trial.workers[0].behavior=blocked
        def run():
            try:self.trial.executor.run_next()
            except BaseException as error:errors.append(error)
        thread=threading.Thread(target=run);thread.start()
        try:
            self.assertTrue(entered.wait(10))
            self.authority.advance(0,'advance',self.new_paths('epoch1'))
            self.assertFalse(self.trial.current_epoch_active())
            self.assertTrue(cancelled(self.trial.cancellation.read_fd))
        finally:
            release.set();thread.join(30)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        self.assertEqual(self.authority.batch(sid)['state'],'UNKNOWN')
        self.assertEqual(self.authority.inspect()['counters']['versions'],1)

    def test_batch_commit_unknown_never_fallback_finalizes(self):
        self.submit()
        self.trial.workers[0].behavior=lambda root,source:{'status':'rejected','reason':'compile_rejected'}
        with mock.patch.object(self.authority,'finish_batch',side_effect=CommitUnknown()) as finish:
            try:self.trial.executor.run_next()
            except AuthorityUnavailable:pass
            self.assertEqual(finish.call_count,1,'ambiguous COMMIT must not trigger a fallback finalize')
        self.assertTrue(self.trial.stop_requested.is_set())

    def test_replay_commit_unknown_never_fallback_finalizes(self):
        sid=self.complete()
        with mock.patch.object(module,'checked_manifest',return_value='a'*64):
            with mock.patch.object(self.authority,'finish_replay',side_effect=CommitUnknown()) as finish:
                with self.assertRaises(AuthorityUnavailable):self.call(self.replay(sid))
                self.assertEqual(finish.call_count,1,'ambiguous COMMIT must not trigger a fallback replay finalize')
        self.assertTrue(self.trial.stop_requested.is_set())

    def test_monitor_cancels_synchronous_replay_without_waiting_for_request(self):
        sid=self.complete()
        entered=threading.Event();release=threading.Event();errors=[]
        worker=self.trial.workers[0]
        def blocked(*args,**kw):
            entered.set()
            if not release.wait(30):raise AssertionError('test cleanup release missing')
            return dict(submission_id=sid,records=[])
        worker.replay=blocked
        def run():
            try:self.call(self.replay(sid))
            except BaseException as error:errors.append(error)
        self.trial.start_monitor()
        with mock.patch.object(module,'checked_manifest',return_value='a'*64):
            thread=threading.Thread(target=run);thread.start()
            try:
                self.assertTrue(entered.wait(10))
                self.authority.advance(0,'advance',self.new_paths('epoch1'))
                # No manual current_epoch_active call: the independent observer
                # must signal cancellation while request() is blocked in replay.
                self.assertTrue(self.trial.stop_requested.wait(10))
                # Admission fencing intentionally precedes pipe EOF. Observe the
                # descriptor separately rather than assuming Event implies EOF.
                fd=self.trial.cancellation.read_fd
                self.assertEqual(select.select([fd],[],[],10)[0],[fd])
                self.assertTrue(cancelled(fd))
            finally:
                release.set();thread.join(30)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors),1);self.assertIsInstance(errors[0],AuthorityDenied)
        state=self.authority.inspect()
        self.assertEqual(state['replays'][0]['state'],'UNKNOWN')
        self.assertEqual(state['references'],[])
        self.assertEqual(state['counters']['replays'],1)

    def proof(self,epoch=1):
        return dict(author='/root/existing_author',canonical_task='/root/existing_author',epoch=epoch,
                    same_existing_task=True,continuation_method='followup_task',
                    followup_succeeded=True,retained_context_confirmed=True,
                    proof_digest=hashlib.sha256(b'trusted unit-test assertion only').hexdigest())

    def test_minimal_recovery_excludes_uncommitted_workspace_and_preserves_deadline(self):
        sid,_,_=self.submit()
        checkpoint=self.call(dict(operation='checkpoint',text='Own committed text',submission_ids=[sid]))
        oldauthor=Path(self.paths['author'])
        for name in ('src','tests','work'):
            (oldauthor/name).mkdir();(oldauthor/name/'lost.txt').write_text('uncommitted bytes')
        original=self.authority.inspect()['run']
        paths=self.new_paths('epoch1')
        self.authority.advance(0,'advance',paths)
        response=module.recover_committed(self.authority,1,'recover',self.proof())
        self.assertEqual(response['status'],'ACTIVE')
        now=self.authority.inspect()
        self.assertEqual(now['run']['mono_deadline'],original['mono_deadline'])
        self.assertEqual(now['run']['wall_deadline'],original['wall_deadline'])
        author=Path(paths['author'])
        self.assertEqual(list((author/'src').iterdir()),[])
        self.assertEqual(list((author/'tests').iterdir()),[])
        self.assertFalse(any(author.rglob('lost.txt')))
        self.assertEqual(len(list((author/'recovered/sources').rglob('*.java'))),1)
        self.assertEqual(len(list((author/'recovered/notes').glob('*.json'))),1)
        self.assertEqual(json.loads((author/'recovered/checkpoint.json').read_text()),checkpoint)
        self.assertEqual(now['batches'][0]['state'],'UNKNOWN')
        self.assertEqual(now['counters']['versions'],1)
        self.assertEqual(self.trial.workers[0].sources,[])

    def test_recovery_wrong_existing_author_stays_closed_and_writes_nothing(self):
        paths=self.new_paths('epoch1');self.authority.advance(0,'advance',paths)
        proof=self.proof();proof['canonical_task']='/root/replacement_author'
        with self.assertRaisesRegex(AuthorityDenied,'recovery_proof_missing'):
            module.recover_committed(self.authority,1,'recover',proof)
        self.assertEqual(self.authority.inspect()['run']['state'],'RECOVERING')
        self.assertFalse(Path(paths['author']).exists())

    def test_changed_durable_source_blocks_recovery_without_substitution(self):
        sid,_,_=self.submit();paths=self.new_paths('epoch1')
        self.authority.advance(0,'advance',paths)
        db=self.authority._connect()
        try:db.execute("UPDATE domain_sources SET archive=?",(b'corrupt archive',))
        finally:db.close()
        self.expected_close_unavailable=True  # Source corruption permanently closes this writer.
        with self.assertRaises(AuthorityUnavailable):
            module.recover_committed(self.authority,1,'recover',self.proof())
        self.assertEqual(self.authority.inspect()['run']['state'],'RECOVERING')
        self.assertEqual(self.authority.batch(sid)['state'],'UNKNOWN')
        self.assertEqual(self.trial.workers[0].sources,[])

    def test_449_file_internal_manifest_above_public_response_cap_recovers(self):
        sid,_,_=self.submit()
        self.authority.start_batch(0,'manual_start',sid)
        root=self.trial.artifacts/sid;root.mkdir()
        (root/'source/bot').mkdir(parents=True)
        (root/'source/bot/RobotPlayer.java').write_text('package bot; public class RobotPlayer {}')
        (root/'build/classes').mkdir(parents=True)
        for i in range(430):
            (root/'build/classes'/('C%03d_'%i+'x'*105+'.class')).write_bytes(b'inert fake bytes')
        receipt=dict(status='succeeded',exit_code=0,child_reaped=True,
                     deadline_monotonic=self.trial.deadline,reaped_monotonic=time.monotonic())
        (root/'build/artifact.json').write_text(json.dumps({'steps':[receipt]}))
        (root/'package').mkdir();(root/'package/fixture.zip').write_bytes(b'inert package')
        map_path=self.root/'fixture.map';map_path.write_bytes(b'inert map')
        map_hash=hashlib.sha256(map_path.read_bytes()).hexdigest()
        games=[];entries=[];binding={'engine_sha256':'e'*64}
        for i in range(8):
            self.trial.before_game(sid,i)
            g=module.DevelopmentGame('game'+str(i),'/inert/opponent',str(map_path),'A')
            games.append(g);out=root/('game-'+str(i));out.mkdir()
            (out/'replay.bc23').write_bytes(b'inert replay')
            replay_hash=hashlib.sha256((out/'replay.bc23').read_bytes()).hexdigest()
            (out/'result.json').write_text(json.dumps(dict(process=receipt,extraction=receipt,
                         engine_sha256='e'*64,map_sha256=map_hash)))
            entries.append(dict(index=i,label=g.label,own_slot='A',status='completed',
                                replay_sha256=replay_hash,outcome='win'))
        result=dict(status='complete',games=entries,wins=8,losses=0)
        batch=self.authority.batch(sid)
        with mock.patch.object(module,'build_identity',return_value=binding):
            manifest=module.capture_batch(root,batch,result,self.trial.deadline,games)
            self.assertEqual(len(manifest['files']),449)
            self.assertGreater(len(module.canonical(manifest)),65536)
            self.assertLess(len(module.canonical(manifest)),module.MAX_MANIFEST)
            self.authority.finish_batch(0,'complete_large',sid,result,manifest)
            self.authority.advance(0,'advance',self.new_paths('epoch1'))
            response=module.recover_committed(self.authority,1,'recover',self.proof())
            self.assertEqual(response['status'],'ACTIVE')
            copied=Path(self.new_paths('epoch1')['artifacts'])/sid
            self.assertEqual(module.checked_manifest(copied,manifest),module.checked_manifest(root,manifest))

    def test_cleanup_unverified_never_publishes_success(self):
        self.expected_cleanup_unverified=True
        self.trial.workers[0].cleanup_unverified=True
        try:
            with self.assertRaisesRegex(ValueError,'cleanup_unverified'):self.trial.close()
            self.assertFalse((self.trial.root/'cleanup.json').exists())
            self.assertTrue(self.trial.executor.closed)
        finally:self.trial.workers[0].cleanup_unverified=False

    def test_logical_close_commits_unknown_before_executor_join(self):
        sid,_,_=self.submit()
        observed=[]
        def shutdown(**kwargs):
            state=self.authority.inspect()
            observed.append(state['control']['closed'])
            self.assertEqual(self.authority.batch(sid)['state'],'UNKNOWN')
            self.assertTrue(cancelled(self.trial.cancellation.read_fd))
            self.trial.executor.run_next()  # A delayed queued callback cannot revive work.
            self.assertEqual(self.trial.workers[0].sources,[])
            self.assertEqual(self.authority.batch(sid)['state'],'UNKNOWN')
        self.trial.executor.shutdown=shutdown
        self.trial.close()
        self.assertEqual(observed,[1])
        self.assertTrue((self.trial.root/'cleanup.json').exists())

    def test_old_epoch_close_does_not_close_replacement(self):
        self.authority.advance(0,'advance',self.new_paths('epoch1'))
        self.trial.close()
        state=self.authority.inspect()
        self.assertEqual(state['run']['epoch'],1)
        self.assertEqual(state['run']['state'],'RECOVERING')
        self.assertFalse(state['control']['closed'])


if __name__=='__main__':unittest.main()
