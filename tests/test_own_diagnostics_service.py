"""Own-diagnostic routing/receipts using synthetic artifacts only; no games."""
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from services.local_trial import AdapterWorker, DevelopmentGame, LocalTrial, MAX_RESPONSE, closed_dispatch, encoded
from services.replay_feedback_v2 import ReplayPageQuery
from runtime.artifacts import digest
from test_local_trial import archive, Worker
from test_own_diagnostics import fixture, SUBMISSION


class RoutedWorker(Worker):
    def __init__(self): self.calls = []
    def replay(self, root, sid, index, query, expected_replay_sha256, **kwargs):
        self.calls.append((query, expected_replay_sha256, kwargs))
        return {'records': [], 'mode': kwargs.get('mode', 'actions')}


class OwnDiagnosticServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.worker = RoutedWorker()
        self.trial = LocalTrial(Path(self.temp.name)/'run', self.worker, require_experiment_notes=True, max_replay_queries=2)
        note = self.note()
        accepted = self.trial.dispatch({'operation':'submit','package':'bot','source_zip_base64':archive(),
                                       'note_id':note['note_id'],'note_sha256':note['note_sha256']})
        self.sid = accepted['submission_id']
        for _ in range(1000):
            if self.trial.dispatch({'operation':'result','submission_id':self.sid})['status'] != 'accepted': break
            time.sleep(.005)
        else: self.fail('synthetic worker did not complete')
    def tearDown(self):
        self.trial.close(); self.temp.cleanup()
    def note(self, refs=None):
        return self.trial.dispatch({'operation':'note','package':'bot','source_zip_base64':archive(),
            'text':'Generic test observation.','replay_references':[] if refs is None else refs})
    def request(self, mode='own_diagnostics'):
        query = {'round_start':1,'round_end':2,'robot_id':None,'record_offset':0,'record_limit':64}
        if mode is not None: query['mode'] = mode
        return {'operation':'replay','submission_id':self.sid,'game_index':0,'query':query}
    def test_default_old_projection_and_new_explicit_route(self):
        old = self.trial.dispatch(self.request(None)); new = self.trial.dispatch(self.request())
        self.assertEqual(old['feedback']['mode'], 'actions'); self.assertEqual(self.worker.calls[0][2], {})
        self.assertEqual(new['feedback']['mode'], 'own_diagnostics')
        self.assertEqual(self.worker.calls[1][2], {'mode':'own_diagnostics'})
        self.assertEqual(self.worker.calls[1][1], '0'*64)
    def test_explicit_actions_keeps_old_worker_contract(self):
        self.trial.dispatch(self.request('actions')); self.assertEqual(self.worker.calls[0][2], {})
    def test_query_modes_share_existing_quota(self):
        self.trial.dispatch(self.request(None)); self.trial.dispatch(self.request())
        with self.assertRaisesRegex(ValueError,'replay_limit'): self.trial.dispatch(self.request())
        self.assertEqual(len(self.worker.calls),2); self.assertEqual(self.trial.replays,3)
    def test_mode_reference_note_and_audit_binding(self):
        request = self.request(); response = self.trial.dispatch(request); ref = response['replay_reference']
        self.assertEqual(ref['query'],request['query'])
        self.assertEqual(ref['feedback_sha256'],hashlib.sha256(encoded(response['feedback'])).hexdigest())
        self.assertEqual(ref['replay_sha256'],'0'*64)
        following = self.note([ref['reference_id']])
        self.assertEqual(following['replay_references'][0]['query']['mode'],'own_diagnostics')
        events = [json.loads(line) for line in (self.trial.root/'audit.jsonl').read_text().splitlines()]
        event = next(e for e in events if e['sequence'] == ref['audit_receipt']['sequence'])
        self.assertEqual(event['response']['query']['mode'],'own_diagnostics')
        actual = event.pop('event_sha256'); self.assertEqual(actual,hashlib.sha256(encoded(event)).hexdigest())
        event['response']['query']['mode']='actions'
        self.assertNotEqual(actual,hashlib.sha256(encoded(event)).hexdigest())
        ref['query']['mode']='forged'
        self.assertEqual(following['replay_references'][0]['query']['mode'],'own_diagnostics')
    def test_candidate_cannot_select_team_path_or_unknown_mode(self):
        for field,value in (('own_team_id',2),('replay','/private/CANARY'),('path','/private/CANARY'),
                            ('mode','all_diagnostics'),('mode',True),('mode',None)):
            request=self.request(); request['query'][field]=value
            output=json.loads(closed_dispatch(self.trial,encoded(request)))
            self.assertEqual(output['status'],'error'); self.assertEqual(output['reason'],'invalid_query')
            self.assertNotIn('CANARY',json.dumps(output))
        self.assertEqual(self.worker.calls,[]); self.assertEqual(self.trial.replays,0)
    def test_whole_response_envelope_cap(self):
        with patch.object(self.worker,'replay',return_value={'records':['CANARY'*MAX_RESPONSE]}):
            data=closed_dispatch(self.trial,encoded(self.request()))
        self.assertLessEqual(len(data),MAX_RESPONSE); self.assertEqual(json.loads(data)['status'],'error')
        self.assertNotIn(b'CANARY',data)


class OwnDiagnosticAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        match=self.root/'game-0'; match.mkdir()
        (match/'result.json').write_text('{"status":"completed"}')
        (match/'replay.bc23').write_bytes(b'public synthetic replay placeholder')
        self.sha=digest(match/'replay.bc23'); self.query=ReplayPageQuery(0,2,None,0,128)
    def tearDown(self): self.temp.cleanup()
    def worker(self,slot):
        return AdapterWorker(['fixed-bcenv'],[DevelopmentGame('public-dev','/trusted/b','/trusted/m',slot)])
    def fake_execute(self,source,commands):
        def execute(command,log,timeout):
            commands.append((command,timeout)); out=Path(command[command.index('--output')+1]); out.mkdir()
            (out/'replay-query.json').write_bytes(encoded(source)); return {'exit_code':0}
        return execute
    def test_trusted_slot_routing_and_32k_feedback_limit(self):
        for slot,own in (('A',1),('B',2)):
            worker=self.worker(slot); source=fixture(128,limit=128,text='\x00'*64)
            source['query']['own_team_id']=own; commands=[]
            with patch('services.local_trial.execute',side_effect=self.fake_execute(source,commands)):
                response=worker.replay(self.root,SUBMISSION,0,self.query,self.sha,mode='own_diagnostics')
            command=commands[0][0]
            self.assertEqual(command[:3],['fixed-bcenv','replay','diagnostics'])
            self.assertEqual(command[command.index('--own-team-id')+1],str(own))
            self.assertEqual(response['own_team_id'],own); self.assertLessEqual(len(encoded(response)),32768)
            self.assertLess(len(response['records']),128)
            self.assertEqual(response['coverage']['next_record_offset'],len(response['records']))
            self.assertNotIn('OPPONENT_PRIVATE_CANARY',json.dumps(response))
    def test_default_adapter_page_route_unchanged(self):
        worker=self.worker('A'); commands=[]
        with patch('services.local_trial.execute',side_effect=self.fake_execute({},commands)), \
             patch('services.local_trial.project_replay_feedback_page',return_value=b'{"legacy":true}') as project:
            response=worker.replay(self.root,SUBMISSION,0,self.query,self.sha)
        self.assertEqual(response,{'legacy':True}); self.assertEqual(commands[0][0][:3],['fixed-bcenv','replay','page'])
        self.assertNotIn('--own-team-id',commands[0][0])
        self.assertEqual(project.call_args.kwargs['limits'].max_output_encoded_bytes,32768)
    def test_absolute_deadline_reaches_diagnostic_adapter(self):
        worker=self.worker('A'); worker.remaining_seconds=lambda:2.5; worker.deadline_monotonic=1234.5; commands=[]
        with patch('services.local_trial.execute',side_effect=self.fake_execute(fixture(limit=128),commands)):
            worker.replay(self.root,SUBMISSION,0,self.query,self.sha,mode='own_diagnostics')
        command,timeout=commands[0]
        self.assertEqual(command[command.index('--deadline-monotonic')+1],'1234.5'); self.assertEqual(timeout,7.5)
        worker.remaining_seconds=lambda:0
        with patch('services.local_trial.execute') as execute:
            with self.assertRaisesRegex(ValueError,'run_halted'):
                worker.replay(self.root,SUBMISSION,0,self.query,self.sha,mode='own_diagnostics')
            execute.assert_not_called()
    def test_invalid_mode_and_changed_replay_never_invoke(self):
        worker=self.worker('A')
        with patch('services.local_trial.execute') as execute:
            with self.assertRaisesRegex(ValueError,'invalid_query'):
                worker.replay(self.root,SUBMISSION,0,self.query,self.sha,mode='all')
            with self.assertRaisesRegex(ValueError,'replay_changed'):
                worker.replay(self.root,SUBMISSION,0,self.query,'0'*64,mode='own_diagnostics')
            execute.assert_not_called()


class OwnDiagnosticCommandTests(unittest.TestCase):
    def setUp(self):
        import importlib.util
        from types import SimpleNamespace
        spec = importlib.util.spec_from_file_location('own_test_adapter',
            Path(__file__).resolve().parents[1] / 'seasons/2023/adapter.py')
        self.adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.adapter)
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.replay = self.root / 'public.bc23'; self.replay.write_bytes(b'synthetic public input')
        self.engine = self.root / 'engine.jar'; self.engine.write_bytes(b'not executed')
        self.args = SimpleNamespace(round_start=0, round_end=2, robot_id=None, limit=128,
            record_offset=0, own_team_id=2, replay=str(self.replay), output=str(self.root / 'output'),
            timeout=90, deadline_monotonic=1234.5)
    def tearDown(self): self.temp.cleanup()
    def receipt(self):
        source = fixture(limit=128); source['query']['own_team_id'] = 2
        source.update(all_schema_fields_validated=False, page_records_complete=True,
                      truncated=False, selected_records_complete=True)
        source['source_replay'] = {'sha256':digest(self.replay)}
        return source
    def run_command(self, source, mutate=False):
        calls = []
        def execute(command, path, timeout, **kwargs):
            calls.append((command, timeout, kwargs)); Path(path).write_bytes(encoded(source))
            if mutate: self.replay.write_bytes(b'mutated')
            return {'status':'succeeded','exit_code':0}
        with patch.object(self.adapter, 'runtime', return_value={'BCENV_JAVA':'pinned-java','BCENV_ENGINE_JAR':str(self.engine)}), \
             patch.dict('os.environ', {'BCENV_REPLAY_CLASSPATH':'pinned-replay-classes'}), \
             patch.object(self.adapter,'execute',side_effect=execute):
            status = self.adapter.replay_diagnostics(self.args)
        return status, calls
    def test_exact_java_argv_and_deadline(self):
        status, calls = self.run_command(self.receipt())
        self.assertEqual(status,0)
        command, timeout, options = calls[0]
        self.assertEqual(command,['pinned-java','-Xmx512m','-cp','pinned-replay-classes',
            'OwnDiagnostics',str(self.replay),'0','2','-1','128','0','2'])
        self.assertEqual(timeout,90); self.assertEqual(options['deadline_monotonic'],1234.5)
    def test_wrong_team_receipt_rejected(self):
        source = self.receipt(); source['query']['own_team_id'] = 1
        status, _ = self.run_command(source); self.assertEqual(status,1)
        self.assertEqual(json.loads((Path(self.args.output)/'query-result.json').read_text())['status'],'failed')
    def test_replay_mutation_rejected(self):
        status, _ = self.run_command(self.receipt(), mutate=True); self.assertEqual(status,1)
        self.assertEqual(json.loads((Path(self.args.output)/'query-result.json').read_text())['status'],'input_mutated')
    def test_cli_requires_own_team_and_offset(self):
        import io
        base = ['bcenv','replay','diagnostics','--replay',str(self.replay),'--round-start','0',
                '--round-end','2','--output',self.args.output]
        for extra in ([], ['--own-team-id','1'], ['--record-offset','0']):
            with patch('sys.argv',base+extra), patch('sys.stderr',io.StringIO()), \
                 patch.object(self.adapter,'replay_diagnostics') as run:
                with self.assertRaises(SystemExit) as error: self.adapter.main()
                self.assertEqual(error.exception.code,2); run.assert_not_called()


if __name__ == '__main__': unittest.main()
