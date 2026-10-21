import base64
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
import zipfile

from services.local_trial import LocalTrial, make_server, AdapterWorker, DevelopmentGame
from services.local_trial_client import source_zip


def archive(path='bot/RobotPlayer.java', text='package bot; public class RobotPlayer {}'):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:
        info=zipfile.ZipInfo(path,(1980,1,1,0,0,0)); info.external_attr=0o100644<<16;info.compress_type=zipfile.ZIP_DEFLATED
        z.writestr(info,text)
    return base64.b64encode(out.getvalue()).decode()


class Worker:
    def run(self,root,accepted):
        (root/'source-evidence.json').write_text(json.dumps([f.path for f in accepted.files]))
        return {'status':'complete','games':[{'index':0,'status':'completed','outcome':'win','replay_sha256':'0'*64}], 'wins':1,'losses':0}
    def replay(self,root,sid,index,query,expected_replay_sha256):
        return {'status':'ok','records':[], 'offset':query.record_offset}


class LocalTrialTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.trial=LocalTrial(Path(self.temp.name)/'run',Worker(),max_submissions=2)
    def tearDown(self):
        self.trial.close();self.temp.cleanup()
    def submit(self,**kwargs):
        return self.trial.dispatch(dict(operation='submit',package='bot',source_zip_base64=archive(),**kwargs))
    def completed(self,sid):
        for _ in range(200):
            value=self.trial.dispatch({'operation':'result','submission_id':sid})
            if value['status']!='accepted':return value
            time.sleep(.005)
        self.fail('worker not completed')
    def test_submit_immutable_evidence_and_bounded_result(self):
        submitted=self.submit();sid=submitted['submission_id'];submitted['status']='forged'
        result=self.completed(sid)
        self.assertEqual(result['status'],'complete');self.assertEqual(result['wins'],1)
        self.assertEqual(json.loads((self.trial.root/sid/'source-evidence.json').read_text()),['bot/RobotPlayer.java'])
        result['games'][0]['outcome']='forged'
        self.assertEqual(self.completed(sid)['games'][0]['outcome'],'win')
        self.assertTrue((self.trial.root/'audit.jsonl').is_file())
    def test_no_client_host_paths_or_extra_fields(self):
        with self.assertRaises(ValueError):self.submit(source='/private/canary')
        with self.assertRaises(ValueError):self.trial.dispatch({'operation':'result','submission_id':'../../canary'})
    def test_reject_traversal_archive(self):
        with self.assertRaises(ValueError):self.trial.dispatch({'operation':'submit','package':'bot','source_zip_base64':archive('../canary.java')})
        self.assertFalse((Path(self.temp.name)/'canary.java').exists())
    def test_reject_invalid_base64_package_and_nonstring(self):
        for data,package in [('not base64','bot'),(archive(),'../../bad'),(22,'bot')]:
            with self.assertRaises(ValueError):self.trial.dispatch({'operation':'submit','package':package,'source_zip_base64':data})
    def test_submission_budget_and_unknown_lookup(self):
        for _ in range(2):self.completed(self.submit()['submission_id'])
        with self.assertRaisesRegex(ValueError,'submission_limit'):self.submit()
        with self.assertRaisesRegex(ValueError,'unknown_submission'):self.completed('s_'+'0'*32)
    def test_replay_validation(self):
        sid=self.submit()['submission_id'];self.completed(sid)
        request={'operation':'replay','submission_id':sid,'game_index':0,'query':{'round_start':1,'round_end':2,'robot_id':None,'record_offset':0,'record_limit':64}}
        self.assertEqual(self.trial.dispatch(request)['status'],'replay')
        for key,value in [('record_limit',129),('record_offset',True),('round_end',1000)]:
            old=request['query'][key];request['query'][key]=value
            with self.assertRaises(ValueError):self.trial.dispatch(request)
            request['query'][key]=old
        request['game_index']=True
        with self.assertRaises(ValueError):self.trial.dispatch(request)
    def test_worker_exception_does_not_leak_host_path(self):
        class Broken(Worker):
            def run(self,*args):raise RuntimeError('/private/evaluator/CANARY')
        self.trial.worker=Broken();sid=self.submit()['submission_id'];result=self.completed(sid)
        self.assertEqual(result['reason'],'worker_failure');self.assertNotIn('CANARY',json.dumps(result))
        self.assertIn('CANARY',(self.trial.root/sid/'failure.txt').read_text())
    def test_fresh_root_required(self):
        with self.assertRaises(FileExistsError):LocalTrial(self.trial.root,Worker())
    def test_client_source_archive(self):
        src=Path(self.temp.name)/'src';(src/'bot').mkdir(parents=True);(src/'bot'/'RobotPlayer.java').write_text('package bot; class RobotPlayer {}')
        data=source_zip(src,'bot')
        self.assertTrue(data.startswith(b'PK'))
        (src/'bad.txt').write_text('bad')
        with self.assertRaises(ValueError):source_zip(src,'bot')
    def test_loopback_http_and_closed_errors(self):
        server=make_server(self.trial);thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            self.assertEqual(server.server_address[0],'127.0.0.1')
            url='http://127.0.0.1:'+str(server.server_port)+'/v1'
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req=urllib.request.Request(url,data=json.dumps({'operation':'result','submission_id':'/private/CANARY'}).encode())
            with self.assertRaises(urllib.error.HTTPError) as caught:opener.open(req)
            data=caught.exception.read();self.assertNotIn(b'CANARY',data)
            self.assertEqual(json.loads(data)['reason'],'unknown_submission')
            req=urllib.request.Request(url,data=json.dumps({'operation':'submit','package':'bot','source_zip_base64':archive()}).encode())
            with opener.open(req) as response:self.assertEqual(json.load(response)['status'],'accepted')
        finally:server.shutdown();thread.join();server.server_close()
    def test_adapter_projects_real_artifact_shape_not_private_fields(self):
        from services.scrimmage import accept_source_zip
        accepted=accept_source_zip(base64.b64decode(archive()),'bot')
        game=DevelopmentGame('public-dev-A','/trusted/opponent','/trusted/map.map23','A')
        class SimulatedAdapter(AdapterWorker):
            def invoke(self,root,name,args,multiplier=1):
                out=Path(args[args.index('--output')+1]);out.mkdir()
                if name=='build':
                    (out/'artifact.json').write_text(json.dumps({'status':'verified'}))
                if name.startswith('game-'):
                    (out/'result.json').write_text(json.dumps({'status':'completed','engine_version':'3.0.13','winner':'A','config':{'secret':'/private/CANARY'}}))
                    (out/'replay.bc23').write_bytes(b'synthetic replay')
                    (out/'replay-summary.json').write_text(json.dumps({'matches':[{'total_rounds':200}]}))
                return True
        worker=SimulatedAdapter(['bcenv'],[game])
        root=Path(self.temp.name)/'adapter';root.mkdir()
        result=worker.run(root,accepted)
        self.assertEqual(result['status'],'complete');self.assertEqual(result['wins'],1)
        self.assertNotIn('CANARY',json.dumps(result));self.assertNotIn('/trusted',json.dumps(result))
        self.assertEqual(result['games'][0]['label'],'public-dev-A')

    def test_adapter_missing_build_artifact_is_not_compile_rejection(self):
        from services.scrimmage import accept_source_zip
        class FailingAdapter(AdapterWorker):
            def invoke(self,*args,**kwargs):return False
        worker=FailingAdapter(['bcenv'],[DevelopmentGame('dev','/trusted/b','/trusted/m.map23','A')])
        root=Path(self.temp.name)/'adapter';root.mkdir()
        with self.assertRaises(FileNotFoundError):worker.run(root,accept_source_zip(base64.b64decode(archive()),'bot'))

    def test_infrastructure_halt_blocks_later_and_queued_submissions(self):
        entered=threading.Event();release=threading.Event();calls=[]
        class Broken(Worker):
            def run(self,*args):
                calls.append(1);entered.set();release.wait(2)
                raise RuntimeError('synthetic infrastructure failure')
        self.trial.worker=Broken()
        first=self.submit()['submission_id'];self.assertTrue(entered.wait(1))
        second=self.submit()['submission_id'];release.set()
        self.assertEqual(self.completed(first)['status'],'infrastructure_failure')
        self.assertEqual(self.completed(second)['reason'],'run_halted')
        self.assertEqual(len(calls),1)
        with self.assertRaisesRegex(ValueError,'run_halted'):self.submit()

    def test_build_timeout_is_infrastructure(self):
        from services.scrimmage import accept_source_zip
        class TimedOut(AdapterWorker):
            def invoke(self,root,name,args,multiplier=1):
                out=root/'build';out.mkdir()
                (out/'artifact.json').write_text(json.dumps({'status':'failed','steps':[{'status':'timeout','exit_code':-9}]}))
                return False
        root=Path(self.temp.name)/'adapter';root.mkdir()
        with self.assertRaisesRegex(ValueError,'build_infrastructure_failure'):
            TimedOut(['bcenv'],[DevelopmentGame('dev','/b','/m.map23','A')]).run(root,accept_source_zip(base64.b64decode(archive()),'bot'))

    def test_worker_stops_schedule_at_first_infrastructure_failure(self):
        from services.scrimmage import accept_source_zip
        calls=[]
        class FailedGame(AdapterWorker):
            def invoke(self,root,name,args,multiplier=1):
                calls.append(name);out=Path(args[args.index('--output')+1]);out.mkdir()
                if name.startswith('game-'):(out/'result.json').write_text(json.dumps({'status':'timeout'}))
                return not name.startswith('game-')
        root=Path(self.temp.name)/'adapter';root.mkdir()
        games=[DevelopmentGame('dev'+str(i),'/b','/m.map23','A') for i in range(2)]
        result=FailedGame(['bcenv'],games).run(root,accept_source_zip(base64.b64decode(archive()),'bot'))
        self.assertEqual(result['status'],'infrastructure_failure');self.assertEqual(len(result['games']),1)
        self.assertNotIn('game-1',calls)

    def test_replay_mismatch_detected_before_invocation(self):
        from services.replay_feedback_v2 import ReplayPageQuery
        class NeverInvoke(AdapterWorker):
            def invoke(self,*args):raise AssertionError('must not invoke on changed replay')
        root=Path(self.temp.name)/'adapter';match=root/'game-0';match.mkdir(parents=True)
        (match/'result.json').write_text(json.dumps({'status':'completed'}));(match/'replay.bc23').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'replay_changed'):
            NeverInvoke(['bcenv'],[DevelopmentGame('dev','/b','/m.map23','A')]).replay(root,'s_'+'0'*32,0,ReplayPageQuery(1,2,None,0,64),'0'*64)

    def test_failed_output_directory_terminates_record(self):
        from unittest.mock import patch
        original=Path.mkdir
        def failing(path,*args,**kwargs):
            if path.name.startswith('s_'):raise OSError('synthetic disk failure')
            return original(path,*args,**kwargs)
        with patch.object(Path,'mkdir',failing):
            sid=self.submit()['submission_id'];result=self.completed(sid)
        self.assertEqual(result['status'],'infrastructure_failure');self.assertTrue(self.trial.halted)

    def test_mailbox_round_trip_and_no_follow(self):
        from services.local_trial import serve_mailbox
        from services.local_trial_client import mailbox_request
        box=Path(self.temp.name)/'mailbox';stop=threading.Event()
        thread=threading.Thread(target=serve_mailbox,args=(self.trial,box),kwargs={'stop':stop});thread.start()
        try:
            for _ in range(100):
                if (box/'ready.json').is_file():break
                time.sleep(.01)
            result=mailbox_request(box,{'operation':'result','submission_id':'s_'+'0'*32},timeout=3)
            self.assertEqual(result['reason'],'unknown_submission')
            submitted=mailbox_request(box,{'operation':'submit','package':'bot','source_zip_base64':archive()},timeout=3)
            self.assertEqual(submitted['status'],'accepted')
            secret=Path(self.temp.name)/'canary';secret.write_text('PRIVATE_CANARY')
            name='a'*32+'.json';(box/'requests'/name).symlink_to(secret)
            for _ in range(100):
                if (box/'responses'/name).exists():break
                time.sleep(.01)
            self.assertNotIn('PRIVATE_CANARY',(box/'responses'/name).read_text())
            self.assertEqual(secret.read_text(),'PRIVATE_CANARY')
        finally:stop.set();thread.join(5)
        self.assertFalse(thread.is_alive())
    def test_mailbox_client_rejects_symlink_directory(self):
        from services.local_trial_client import mailbox_request
        real=Path(self.temp.name)/'real';real.mkdir();alias=Path(self.temp.name)/'alias';alias.symlink_to(real,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'symlinks'):mailbox_request(alias,{},timeout=.1)

    def test_config_rejects_unbounded_or_old_runtime(self):
        game=DevelopmentGame('dev','/trusted/build','/trusted/map.map23','A')
        for kwargs in ({'timeout':121},{'spec_version':'2.0.3'}):
            with self.assertRaises(ValueError):AdapterWorker(['bcenv'],[game],**kwargs)
        with self.assertRaises(ValueError):AdapterWorker(['bcenv'],[game]*9)

if __name__=='__main__':unittest.main()
