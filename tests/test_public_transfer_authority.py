"""Public transfer authority: pure SQLite and synthetic evidence, no workers/games."""
import json
from pathlib import Path
import tempfile
import unittest

from services.epoch_authority import AuthorityDenied, ClockSample, _hash
from services.resumable_authority import ResumableAuthority, TRANSFER_PROFILE
from tests import test_resumable_authority as helpers
from tests.test_resumable_authority import archive, paths


class TransferTests(unittest.TestCase):
    req = helpers.DomainTests.req
    submit = helpers.DomainTests.submit

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=[100,200];self.clock=lambda:ClockSample('boot',*self.now)
        self.games=[{'cell':i} for i in range(8)]
        self.config=dict(profile=TRANSFER_PROFILE,transfer_games=self.games,transfer_schedule_sha256=_hash(self.games))
        self.a=ResumableAuthority.create(Path(self.tmp.name)/'db',profile=TRANSFER_PROFILE,config={'trial':self.config,'inputs':{},'implementation':{},'runtime_environment':{}},
            author='/same/task',paths=paths(0),clock=self.clock)
        self.i=0

    def finish(self,sid,epoch=0):
        self.a.start_batch(epoch,'start'+sid,sid)
        for i in range(8):self.a.before_game(epoch,'g'+sid+str(i),sid,i)
        result=dict(status='complete',games=[dict(status='completed',replay_sha256='a'*64,own_slot='A') for _ in range(8)])
        manifest=dict(source_sha256=self.a.batch(sid)['source'],build_identity={'synthetic':'build'},files={'replay':'a'*64})
        return self.a.finish_batch(epoch,'f'+sid,sid,result,manifest)

    def original(self,text='package own; public class RobotPlayer {}'):
        sid,_=self.submit(text);self.finish(sid);return sid

    def transfer_request(self,sid,epoch=0):
        source=self.a.source(self.a.batch(sid)['source'])
        # A generic fresh note binds the exact accepted own source.
        import base64,io,zipfile
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:
            for file in source.files:z.writestr(file.path,file.data)
        note=self.req(dict(operation='note',package=source.package,source_zip_base64=base64.b64encode(data.getvalue()).decode(),
            text='prospective public comparison',replay_references=[]),epoch=epoch)['response']
        return dict(operation='transfer',submission_id=sid,note_id=note['note_id'],note_sha256=note['note_sha256'])

    def transfer(self,sid,epoch=0):
        return self.req(self.transfer_request(sid,epoch),epoch=epoch,
            prepared={'verified_manifests':{sid:self.a.batch(sid)['manifest_sha256']}})

    def test_atomic_reservation_and_no_second_game_debit(self):
        original=self.original();out=self.transfer(original);sid=out['response']['submission_id']
        state=self.a.inspect();self.assertEqual(2,state['counters']['versions']);self.assertEqual(16,state['counters']['training_games'])
        self.assertEqual(0,sum(g['admitted'] for g in state['games'] if g['batch']==sid))
        before=self.req({'operation':'validation_status'})['response']
        self.assertEqual(8,before['training_games_attempted']);self.assertEqual(8,before['games_attempted'])
        self.assertEqual(16,before['usage']['training_games']);self.assertEqual(16,before['training_games_debited'])
        self.assertEqual('not_attested',before['physical_launches'])
        self.finish(sid);self.assertEqual(16,self.a.inspect()['counters']['training_games'])
        response=self.a.batch(sid);self.assertEqual('transfer',response['phase']);self.assertEqual(original,response['original'])
        status=self.req({'operation':'validation_status'})['response']
        self.assertEqual(16,status['training_games_attempted']);self.assertEqual(16,status['games_attempted'])
        self.assertEqual((1,8,8),(status['transfer_slots_used'],status['transfer_games_reserved'],status['transfer_games_admitted']))

    def test_same_effect_queued_complete_and_alias_are_unique(self):
        original=self.original();first=self.transfer(original)
        self.assertEqual('transfer_effect_used',self.transfer(original)['response']['reason'])
        self.finish(first['response']['submission_id'])
        alias=self.original()
        before=self.a.inspect()['counters']
        self.assertEqual('transfer_effect_used',self.transfer(alias)['response']['reason'])
        after=self.a.inspect()['counters'];self.assertEqual(before['versions'],after['versions']);self.assertEqual(before['training_games'],after['training_games'])

    def test_two_slots_distinct_sources_only(self):
        for n in range(2):
            sid=self.original('package own; public class RobotPlayer { int x'+str(n)+'; }')
            transfer=self.transfer(sid);self.finish(transfer['response']['submission_id'])
        third=self.original('package own; public class RobotPlayer { int third; }')
        self.assertEqual('transfer_limit',self.transfer(third)['response']['reason'])

    def test_unknown_retains_full_reservation_and_effect_after_recovery(self):
        original=self.original();transfer=self.transfer(original)['response']['submission_id']
        self.a.advance(0,'advance',paths(1))
        pins={original:self.a.batch(original)['manifest_sha256']}
        self.a.reopen(1,'reopen','/same/task','a'*64,verified=dict(author='/same/task',epoch=1,same_existing_task=True,
            proof_digest='a'*64,completed_manifests=pins))
        self.assertEqual('UNKNOWN',self.a.batch(transfer)['state'])
        self.assertEqual(16,self.a.inspect()['counters']['training_games'])
        self.assertEqual('transfer_effect_used',self.transfer(original,epoch=1)['response']['reason'])

    def test_transfer_not_original_or_finalist(self):
        a=self.original();tid=self.transfer(a)['response']['submission_id'];self.finish(tid)
        self.assertEqual('invalid_transfer_original',self.transfer(tid)['response']['reason'])
        b=self.original('package own; public class RobotPlayer { int b; }')
        pins={x:self.a.batch(x)['manifest_sha256'] for x in (tid,b)}
        out=self.req(dict(operation='lock_finalists',submission_ids=[tid,b],provisional_choice=b),prepared={'verified_manifests':pins})
        self.assertEqual('invalid_finalists',out['response']['reason'])

    def test_version_game_denial_rolls_back_slot_and_note_consumption(self):
        sid=self.original()
        with self.a._connect() as db:db.execute("UPDATE counters SET used=190 WHERE name='training_games'")
        before=self.a.inspect()['counters']['versions']
        self.assertEqual('training_games_limit',self.transfer(sid)['response']['reason'])
        self.assertEqual(before,self.a.inspect()['counters']['versions'])
        with self.a._connect() as db:
            self.assertEqual(0,db.execute('SELECT count(*) FROM domain_transfers').fetchone()[0])
            self.assertEqual(0,db.execute('SELECT used FROM domain_notes ORDER BY rowid DESC LIMIT 1').fetchone()[0])

    def test_replay_reference_preserves_transfer_pins(self):
        original=self.original();sid=self.transfer(original)['response']['submission_id'];self.finish(sid)
        pending=self.req(dict(operation='replay',submission_id=sid,game_index=0,
            query=dict(round_start=1,round_end=5,robot_id=None,record_offset=0,record_limit=128)))
        result=self.a.finish_replay(0,'replaydone',pending['work']['id'],dict(submission_id=sid))['response']
        for item in (result,result['replay_reference']):
            self.assertEqual(original,item['original_submission_id']);self.assertEqual('own',item['package'])
            self.assertEqual(self.config['transfer_schedule_sha256'],item['transfer_schedule_sha256'])
            self.assertEqual(_hash({'synthetic':'build'}),item['build_identity_sha256'])

    def test_old_profile_rejects_operation_without_schema_change(self):
        old=ResumableAuthority.create(Path(self.tmp.name)/'old',config={},author='/same/task',paths=paths(0),clock=self.clock)
        result=old.request(0,'oldrequest',dict(operation='transfer',submission_id='x',note_id='x',note_sha256='a'*64))
        self.assertEqual('invalid_request',result['response']['reason'])
        with old._connect() as db:self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='domain_transfers'").fetchone())

    def test_configuration_rejects_incorrect_schedule_hash(self):
        with self.assertRaises(AuthorityDenied):
            ResumableAuthority.create(Path(self.tmp.name)/'bad',profile=TRANSFER_PROFILE,
                config={'trial':dict(self.config,transfer_schedule_sha256='a'*64),'inputs':{}},author='/same/task',paths=paths(0),clock=self.clock)

    def test_concurrent_same_effect_spends_one_slot_and_one_note(self):
        import concurrent.futures,threading
        sid=self.original();bodies=[self.transfer_request(sid) for _ in range(2)]
        barrier=threading.Barrier(2)
        pins={'verified_manifests':{sid:self.a.batch(sid)['manifest_sha256']}}
        def request(i):
            barrier.wait()
            return self.a.request(0,'concurrent'+str(i),bodies[i],attempt_id='attempt'+str(i),prepared=pins)['response']
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(request,range(2)))
        self.assertEqual(1,sum(r['status']=='accepted' for r in responses))
        self.assertEqual(1,sum(r.get('reason')=='transfer_effect_used' for r in responses))
        self.assertEqual(2,self.a.inspect()['counters']['versions'])
        self.assertEqual(16,self.a.inspect()['counters']['training_games'])
        with self.a._connect() as db:
            self.assertEqual(1,db.execute('SELECT count(*) FROM domain_transfers').fetchone()[0])
            self.assertEqual(2,db.execute('SELECT sum(used) FROM domain_notes').fetchone()[0])

    def test_unverified_original_and_consumed_note_do_not_reserve(self):
        sid=self.original();body=self.transfer_request(sid)
        denied=self.req(body,prepared={'verified_manifests':{sid:'b'*64}})
        self.assertEqual('transfer_evidence_unverified',denied['response']['reason'])
        accepted=self.req(body,prepared={'verified_manifests':{sid:self.a.batch(sid)['manifest_sha256']}})
        self.assertEqual('accepted',accepted['response']['status'])
        self.assertEqual(2,self.a.inspect()['counters']['versions'])

    def test_build_mismatch_cannot_commit_transfer(self):
        original=self.original();sid=self.transfer(original)['response']['submission_id']
        self.a.start_batch(0,'start',sid)
        for i in range(8):self.a.before_game(0,'admit'+str(i),sid,i)
        with self.assertRaises(AuthorityDenied) as error:
            self.a.finish_batch(0,'wrongbuild',sid,dict(status='complete',games=[{'status':'completed'}]*8),
                dict(source_sha256=self.a.batch(sid)['source'],build_identity={'synthetic':'different'}))
        self.assertEqual('transfer_build_mismatch',error.exception.code)
        self.assertEqual('RUNNING',self.a.batch(sid)['state'])
        self.assertEqual(16,self.a.inspect()['counters']['training_games'])

    def test_pending_quota_and_single_batch_worker_are_shared(self):
        sid=self.original();first=self.transfer(sid)['response']['submission_id']
        pending,_=self.submit('package own; public class RobotPlayer { int pending; }')
        self.a.start_batch(0,'first',first)
        with self.assertRaises(AuthorityDenied) as error:self.a.start_batch(0,'second',pending)
        self.assertEqual('active_limit',error.exception.code)
        self.assertEqual('transfer_effect_used',self.transfer(sid)['response']['reason'])

    def test_locked_profile_rejects_transfer_without_spending(self):
        a=self.original();b=self.original('package own; public class RobotPlayer { int second; }')
        body=self.transfer_request(a)
        pins={sid:self.a.batch(sid)['manifest_sha256'] for sid in (a,b)}
        self.req(dict(operation='lock_finalists',submission_ids=[a,b],provisional_choice=a),prepared={'verified_manifests':pins})
        out=self.req(body,prepared={'verified_manifests':{a:pins[a]}})
        self.assertEqual('training_closed',out['response']['reason'])
        self.assertEqual(2,self.a.inspect()['counters']['versions'])

    def test_unknown_replay_keeps_transfer_binding(self):
        original=self.original();sid=self.transfer(original)['response']['submission_id'];self.finish(sid)
        pending=self.req(dict(operation='replay',submission_id=sid,game_index=0,
            query=dict(round_start=1,round_end=5,robot_id=None,record_offset=0,record_limit=128)))
        self.assertEqual(original,pending['response']['original_submission_id'])
        self.a.advance(0,'advance',paths(1))
        terminal=json.loads(self.a.replay(pending['work']['id'])['response'])
        self.assertEqual(original,terminal['original_submission_id'])
        self.assertEqual(self.config['transfer_schedule_sha256'],terminal['transfer_schedule_sha256'])

    def test_new_profile_requires_bound_config_and_retains_bound_identity(self):
        with self.assertRaises(AuthorityDenied):
            ResumableAuthority.create(Path(self.tmp.name)/'flat',profile=TRANSFER_PROFILE,config=self.config,
                author='/same/task',paths=paths(0),clock=self.clock)
        bound=json.loads(self.a.inspect()['run']['config'])['config']
        self.assertEqual(self.config,bound['trial'])
        self.assertEqual(TRANSFER_PROFILE,bound['domain_profile'])
        self.assertEqual({'inputs','implementation','runtime_environment','trial','domain_profile'},set(bound))
        self.assertEqual(TRANSFER_PROFILE,self.req({'operation':'validation_status'})['response']['profile'])
