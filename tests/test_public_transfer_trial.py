"""Real SQL/orchestration, inert workers only; no subprocesses or engine games."""
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from services.epoch_authority import ClockSample, _hash
from services.resumable_authority import ResumableAuthority, TRANSFER_PROFILE
from services import resumable_trial as module
from tests import test_resumable_trial as helpers
from tests.test_resumable_trial import Worker, QueuedExecutor


class TransferOrchestrationTests(unittest.TestCase):
    body=helpers.OrchestrationTests.body
    call=helpers.OrchestrationTests.call
    submit=helpers.OrchestrationTests.submit
    complete=helpers.OrchestrationTests.complete
    replay=helpers.OrchestrationTests.replay
    new_paths=helpers.OrchestrationTests.new_paths

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for patch in (mock.patch.object(subprocess,'Popen',side_effect=AssertionError('no subprocess fixture')),
                      mock.patch.object(module,'fixed_inputs',return_value={})):
            patch.start();self.addCleanup(patch.stop)
        config=dict(profile=TRANSFER_PROFILE,command=['/inert/bcenv'],timeout=120,match_timeout=300)
        for key in ('games','validation_games','transfer_games'):
            config[key]=[dict(label=key+str(i),opponent_build='/inert/opponent',map_path='/inert/map',
                             own_slot='A' if i%2==0 else 'B') for i in range(8)]
        config['transfer_schedule_sha256']=_hash(config['transfer_games'])
        config['transfer_selection']={'path':'/inert/proof','sha256':'a'*64}
        paths=self.new_paths('epoch0')
        self.authority=ResumableAuthority.create(self.root/'authority.sqlite',profile=TRANSFER_PROFILE,
            config={'trial':config,'inputs':{}},author='/existing/author',paths=paths,
            clock=lambda:ClockSample('test',time.time(),time.monotonic()))
        def capture(root,batch,result,deadline,games):
            for name in ('source','build','package'):
                (root/name).mkdir(exist_ok=True)
                (root/name/'fixture').write_text('immutable inert bytes')
            return dict(source_sha256=batch['source'],batch_id=batch['id'],files={},build_identity={'synthetic':'build'})
        self.trial=module.ResumableTrial(self.authority,0,worker_factory=Worker,
            executor_factory=QueuedExecutor,capture=capture)
        self.addCleanup(self.trial.close)
        for key in ('author','control','mailbox'):Path(paths[key]).mkdir(parents=True)

    def transfer(self,sid):
        note=self.call(dict(self.body(),operation='note',text='Own transfer hypothesis.',replay_references=[]))
        with mock.patch.object(module,'checked_manifest',return_value=self.authority.batch(sid)['manifest_sha256']):
            return self.call(dict(operation='transfer',submission_id=sid,note_id=note['note_id'],note_sha256=note['note_sha256']))

    def test_immutable_copy_third_schedule_single_executor_no_build(self):
        sid=self.complete();response=self.transfer(sid);tid=response['submission_id']
        self.assertEqual(len(self.trial.workers),3)
        self.assertEqual(len(self.trial.executor.calls),1)
        for worker in self.trial.workers:
            self.assertIs(worker.stop_requested,self.trial.stop_requested)
            self.assertIs(worker.cancellation,self.trial.cancellation)
            self.assertEqual(worker.deadline_monotonic,self.trial.deadline)
        with mock.patch.object(module,'checked_manifest'),mock.patch.object(module,'build_identity',return_value={'synthetic':'build'}):
            self.trial.executor.run_next()
        self.assertEqual(self.authority.batch(tid)['state'],'COMPLETE')
        self.assertEqual(self.trial.workers[2].sources,[])
        self.assertEqual(self.authority.inspect()['counters']['training_games'],16)
        for name in ('source','build','package'):
            self.assertEqual((self.trial.artifacts/sid/name/'fixture').read_bytes(),
                             (self.trial.artifacts/tid/name/'fixture').read_bytes())
        with mock.patch.object(module,'checked_manifest'):
            result=self.call(self.replay(tid))
        self.assertEqual(result['status'],'replay_pending')
        self.assertEqual(len(self.trial.workers[2].replay_calls),1)
        self.assertEqual(self.trial.workers[0].replay_calls,[])

    def test_corrupt_original_denies_before_reservation(self):
        sid=self.complete()
        note=self.call(dict(self.body(),operation='note',text='Own transfer hypothesis.',replay_references=[]))
        before=self.authority.inspect()['counters']
        with mock.patch.object(module,'checked_manifest',side_effect=ValueError('changed')):
            out=self.call(dict(operation='transfer',submission_id=sid,note_id=note['note_id'],note_sha256=note['note_sha256']))
        self.assertEqual(out['status'],'error')
        self.assertEqual(out['reason'],'evidence_unverified')
        after=self.authority.inspect()['counters']
        self.assertEqual(before['versions'],after['versions'])
        self.assertEqual(before['training_games'],after['training_games'])
        self.assertFalse(self.trial.executor.calls)

    def test_copy_identity_change_halts_without_transfer_game(self):
        sid=self.complete();tid=self.transfer(sid)['submission_id']
        with mock.patch.object(module,'checked_manifest'),mock.patch.object(module,'build_identity',return_value={'changed':True}):
            self.trial.executor.run_next()
        self.assertTrue(self.trial.stop_requested.is_set())
        self.assertFalse(any(g['admitted'] for g in self.authority.inspect()['games'] if g['batch']==tid))
        self.assertEqual(self.authority.inspect()['counters']['training_games'],16)

    def test_legacy_unsupported_transfer_never_verifies_artifacts(self):
        self.trial.config['profile']='resumable-v1'
        with mock.patch.object(self.trial,'_verified',side_effect=AssertionError('legacy scan')):
            out=self.call(dict(operation='transfer',submission_id='invalid'))
        self.assertEqual(out['status'],'error')
