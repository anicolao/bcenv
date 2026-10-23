"""Retention wait/launcher wiring; inert mocks, no subprocess or game fixtures."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from services import resumable_trial as module
from services import launch_resumable_trial as launch
from services.epoch_authority import AuthorityDenied, AuthorityUnavailable


class GateEvent:
    def __init__(self): self.value=False; self.waits=[]; self.on_wait=lambda: None
    def is_set(self): return self.value
    def set(self): self.value=True
    def wait(self, seconds):
        self.waits.append(seconds)
        self.on_wait()
        return self.value


class RetentionWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for name in ('subprocess.Popen','os.killpg','os.system'):
            p=patch(name,side_effect=AssertionError('no process/signal fixture'))
            p.start();self.addCleanup(p.stop)
        t=self.trial=object.__new__(module.ResumableTrial)
        t.retention_enabled=True;t.epoch=0;t.deadline=1000;t.wall_deadline=2000
        t.artifacts=self.root;t.stop_requested=GateEvent();t.cancellation=Mock()
        t.authority=Mock();t.remaining_seconds=Mock(return_value=50)
        self.state={'run':{'epoch':0,'state':'ACTIVE'},'meta':{'halted':False},'control':{'closed':False},
                    'retention':{'enabled':True,'pending':True,'failed':False,'terminal':False,'sequence':1}}
        t.authority.current_state.side_effect=lambda:self.state
        t.authority.retention_gate.side_effect=lambda epoch:dict(self.state['retention'])
        t.authority.batch.side_effect=lambda sid:{'id':sid,'phase':'training','source':'hash'}
        t.authority.source.return_value=b'unchanged accepted source'
        self.worker=Mock(games=(),cleanup_unverified=False)
        self.worker.run.return_value={'status':'rejected'}
        t.workers=[self.worker];t.capture=Mock(return_value=None);t.storage_checkpoint=Mock()

    def test_pending_accepted_batch_waits_without_launch_or_failure(self):
        def release():
            self.trial.authority.start_batch.assert_not_called()
            self.worker.run.assert_not_called()
            self.trial.authority.finish_batch.assert_not_called()
            self.assertFalse((self.root/'s1').exists())
            self.state['retention']['pending']=False
        self.trial.stop_requested.on_wait=release
        self.trial._batches(['s1'])
        self.assertEqual(self.trial.stop_requested.waits,[.1])
        self.trial.authority.start_batch.assert_called_once()
        self.worker.run.assert_called_once_with(self.root/'s1',b'unchanged accepted source')
        self.trial.authority.finish_batch.assert_called_once()

    def test_completed_first_batch_fences_already_accepted_second(self):
        self.state['retention']['pending']=False
        def finish(*args): self.state['retention']['pending']=True
        self.trial.authority.finish_batch.side_effect=finish
        observed=[]
        def release():
            observed.append(self.worker.run.call_count)
            self.assertFalse((self.root/'s2').exists())
            self.state['retention']['pending']=False
        self.trial.stop_requested.on_wait=release
        self.trial._batches(['s1','s2'])
        self.assertEqual(observed,[1])
        self.assertEqual(self.worker.run.call_count,2)
        self.assertEqual(self.trial.authority.start_batch.call_count,2)

    def test_cancel_pending_never_fabricates_batch_failure(self):
        self.trial.stop_requested.on_wait=self.trial.request_cancel
        self.trial._batches(['s1'])
        self.trial.authority.start_batch.assert_not_called()
        self.trial.authority.finish_batch.assert_not_called()
        self.worker.run.assert_not_called()
        self.trial.cancellation.cancel.assert_called_once()

    def test_deadline_pending_never_starts_or_resets_clock(self):
        self.trial.stop_requested.on_wait=lambda:self.trial.remaining_seconds.configure_mock(return_value=0)
        self.trial._batches(['s1'])
        self.trial.authority.start_batch.assert_not_called()
        self.trial.authority.finish_batch.assert_not_called()
        self.assertEqual((self.trial.deadline,self.trial.wall_deadline),(1000,2000))
        self.assertTrue(self.trial.stop_requested.is_set())

    def test_gate_race_denial_waits_then_starts_once_without_fallback(self):
        self.state['retention']['pending']=False
        self.trial.authority.start_batch.side_effect=[AuthorityDenied('retention_pending'),{}]
        self.trial._batches(['s1'])
        self.assertEqual(self.trial.authority.start_batch.call_count,2)
        self.assertEqual(self.worker.run.call_count,1)
        self.assertEqual(self.trial.stop_requested.waits,[.1])
        self.assertEqual(self.trial.authority.finish_batch.call_count,1)

    def test_stale_epoch_or_failed_gate_stops_without_fallback(self):
        self.state['run']['epoch']=1
        self.trial._batches(['s1'])
        self.trial.authority.start_batch.assert_not_called()
        self.trial.authority.finish_batch.assert_not_called()
        self.assertTrue(self.trial.stop_requested.is_set())

    def test_retention_failure_stops_but_pending_does_not(self):
        self.assertTrue(self.trial.current_epoch_active())
        self.assertFalse(self.trial.stop_requested.is_set())
        self.state['retention']['failed']=True
        self.assertFalse(self.trial.current_epoch_active())
        self.assertTrue(self.trial.stop_requested.is_set())

    def test_gate_io_uncertainty_stops_without_fallback(self):
        self.trial.authority.retention_gate.side_effect=AuthorityUnavailable()
        self.trial._batches(['s1'])
        self.trial.authority.finish_batch.assert_not_called()
        self.worker.run.assert_not_called()
        self.assertTrue(self.trial.stop_requested.is_set())

    def test_legacy_batch_path_never_reads_retention_gate(self):
        self.trial.retention_enabled=False
        self.trial._batches(['s1'])
        self.trial.authority.retention_gate.assert_not_called()
        self.trial.authority.current_state.assert_not_called()
        self.assertEqual(self.worker.run.call_count,1)

    def test_already_admitted_synchronous_replay_finishes_while_pending(self):
        self.trial.authority.replay.return_value={'batch':'s1','game_index':0,
            'query':json.dumps({'round_start':1,'round_end':5,'robot_id':None,'record_offset':0,'record_limit':128})}
        self.trial.authority.batch.side_effect=None
        self.trial.authority.batch.return_value={'id':'s1','phase':'training','manifest':'{}',
            'response':json.dumps({'games':[{'replay_sha256':'a'*64}]})}
        self.worker.replay.return_value={'records':[]}
        self.trial.authority.finish_replay.return_value={'response':{'status':'complete'}}
        with patch.object(module,'checked_manifest'):
            self.assertEqual(self.trial._replay('r1'),{'status':'complete'})
        self.worker.replay.assert_called_once()
        self.trial.authority.retention_gate.assert_not_called()
        self.assertEqual(self.trial.stop_requested.waits,[])

    def test_close_pending_cancels_before_executor_wait_without_retention_wait(self):
        t=self.trial;t.close_lock=threading.Lock();t.cleanup_complete=False;t.close_failure=None
        t.monitor_stop=threading.Event();t.monitor=None;t.monitor_error=None;t.root=self.root
        t.executor=Mock()
        t.executor.shutdown.side_effect=lambda **kw:self.assertTrue(t.stop_requested.is_set())
        with patch.object(module,'publish'):
            t.close()
        t.authority.retention_gate.assert_not_called()
        t.authority.logical_close.assert_called_once()
        t.executor.shutdown.assert_called_once_with(wait=True,cancel_futures=True)
        self.assertEqual(t.stop_requested.waits,[])


class RetentionLauncherTests(unittest.TestCase):
    def test_initialize_optin_binds_exact_computed_inputs_without_start(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);config={'profile':'resumable-v1'}
            inputs={'/public/map':'a'*64};paths={}
            authority=Mock(authority_id='a'*32,config_digest='b'*64)
            authority.inspect.return_value={'run':{'mono_deadline':100,'wall_deadline':200}}
            with patch('subprocess.Popen',side_effect=AssertionError('no subprocess')), \
                    patch.object(launch,'read',side_effect=[config,paths]), \
                    patch.object(launch,'fixed_inputs',return_value=inputs), \
                    patch.object(launch,'fresh_paths'),patch.object(launch,'implementation',return_value={}), \
                    patch.object(launch,'runtime_environment',return_value={}), \
                    patch.object(launch.ResumableAuthority,'create',return_value=authority) as create, \
                    patch.object(launch,'publish'),patch.object(launch,'ResumableTrial') as trial:
                self.assertEqual(launch.main(['--authority',str(root/'db'),'initialize','--config','cfg',
                    '--paths','paths','--author','/same/task','--identity-output','identity','--public-retention']),0)
            bound=create.call_args.kwargs['config']
            self.assertEqual(bound['inputs'],inputs)
            self.assertEqual(bound['trial']['retention'],{'schema':'public-retention-v1',
                'repository_id':1431035719,'fixed_inputs_sha256':hashlib.sha256(module.canonical(inputs)).hexdigest()})
            self.assertNotIn('retention',config)
            trial.assert_not_called()


class RetentionInputTests(unittest.TestCase):
    def test_real_fixed_inputs_bind_and_reject_changed_bytes_or_policy(self):
        with tempfile.TemporaryDirectory() as temp, patch('subprocess.Popen',side_effect=AssertionError('no process')):
            root=Path(temp);command=root/'inert';command.write_bytes(b'never run')
            opponents=[]
            for index in range(2):
                path=root/('opponent'+str(index));path.mkdir();(path/'public.json').write_bytes(b'public')
                opponents.append(str(path))
            config={'profile':'resumable-v1','command':[str(command)],'timeout':120,'match_timeout':300}
            for kind in ('games','validation_games'):
                maps=[]
                for index in range(2):
                    path=root/(kind+str(index));path.write_bytes(b'public-map');maps.append(str(path))
                config[kind]=[{'label':kind+str(i)+str(j)+slot,'opponent_build':opponent,'map_path':path,'own_slot':slot}
                              for i,opponent in enumerate(opponents) for j,path in enumerate(maps) for slot in ('A','B')]
            inputs=module.fixed_inputs(config)
            config['retention']={'schema':'public-retention-v1','repository_id':1431035719,
                                 'fixed_inputs_sha256':hashlib.sha256(module.canonical(inputs)).hexdigest()}
            self.assertEqual(module.fixed_inputs(config),inputs)
            config['retention']['repository_id']=True
            with self.assertRaisesRegex(ValueError,'invalid_retention_config'):module.fixed_inputs(config)
            config['retention']['repository_id']=1431035719
            command.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'retention_fixed_inputs_changed'):module.fixed_inputs(config)


if __name__=='__main__': unittest.main()
