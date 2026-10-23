"""Operator entrypoint tests with inert probe/trial; no process or native launch."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from services import launch_resumable_trial as launch


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.identity={'test.module':{'file':'/inert/module.py','sha256':'a'*64}}
        self.config={'profile':'resumable-v1','command':['/inert/bcenv']}
        self.paths={k:str(self.root/k) for k in ('service','artifacts','mailbox','control','author')}
        self.snapshot={'run':{'epoch':0,'state':'ACTIVE','config':json.dumps({'config':{
            'trial':self.config,'inputs':{},'implementation':self.identity,'runtime_environment':{}}})},
            'epochs':[{'epoch':0,'paths':json.dumps(self.paths)}]}
        self.authority=mock.Mock(authority_id='a'*32,config_digest='b'*64)
        self.authority.inspect.return_value=self.snapshot
        self.trial=mock.Mock(root=Path(self.paths['service']),deadline=1000)
        self.trial.remaining_seconds.return_value=200
        self.trial.current_epoch_active.return_value=True
        self.trial.stop_requested.is_set.return_value=False
        self.trial.cancellation.read_fd=123
        self.patches=[
            mock.patch.object(subprocess,'Popen',side_effect=AssertionError('no subprocess fixture')),
            mock.patch.object(launch,'load',return_value=self.authority),
            mock.patch.object(launch,'implementation',return_value=self.identity),
            mock.patch.object(launch,'fixed_inputs',return_value={}),
            mock.patch.object(launch,'ResumableTrial',return_value=self.trial),
            mock.patch.object(launch,'probe',return_value={'status':'succeeded','exit_code':0}),
            mock.patch.object(launch,'publish'),
            mock.patch.object(launch,'run_service')]
        self.mocks=[p.start() for p in self.patches]
        for p in self.patches:self.addCleanup(p.stop)
        self.construct,self.probe,self.publish,self.run=self.mocks[4:]
        self.environment=mock.patch.object(launch,'runtime_environment',return_value={})
        self.environment_mock=self.environment.start();self.addCleanup(self.environment.stop)
        self.argv=['--authority',str(self.root/'authority.sqlite'),'serve','--authority-id','a'*32,
                   '--config-digest','b'*64,'--epoch','0']

    def test_actual_entrypoint_one_probe_one_service_finally_close(self):
        self.assertEqual(launch.main(self.argv),0)
        self.construct.assert_called_once_with(self.authority,0)
        self.probe.assert_called_once_with(['/inert/bcenv'],self.trial.root/'adapter-probe.log',
                                           timeout=30,deadline_monotonic=1000,cancel_fd=123)
        self.trial.start_monitor.assert_called_once()
        self.run.assert_called_once_with(self.trial)
        self.trial.close.assert_called_once_with()
        self.publish.assert_called_once()

    def test_changed_pinned_implementation_rejected_before_ownership(self):
        self.mocks[2].return_value={}
        with self.assertRaisesRegex(ValueError,'pinned_implementation_or_inputs_changed'):launch.main(self.argv)
        self.construct.assert_not_called();self.probe.assert_not_called()

    def test_changed_public_input_bytes_rejected_before_ownership(self):
        self.mocks[3].return_value={'changed':'c'*64}
        with self.assertRaisesRegex(ValueError,'pinned_implementation_or_inputs_changed'):launch.main(self.argv)
        self.construct.assert_not_called();self.probe.assert_not_called()

    def test_changed_runtime_environment_rejected_before_ownership(self):
        self.environment_mock.return_value={'changed':'d'*64}
        with self.assertRaisesRegex(ValueError,'pinned_implementation_or_inputs_changed'):launch.main(self.argv)
        self.construct.assert_not_called();self.probe.assert_not_called()

    def test_epoch_without_path_binding_rejected_before_probe(self):
        args=self.argv[:-1]+['1']
        with self.assertRaises((KeyError,ValueError)):launch.main(args)
        self.construct.assert_not_called();self.probe.assert_not_called()

    def test_constructor_epoch_rejection_has_no_owned_trial_to_close(self):
        self.construct.side_effect=ValueError('admission_closed')
        with self.assertRaisesRegex(ValueError,'admission_closed'):launch.main(self.argv)
        self.probe.assert_not_called();self.trial.close.assert_not_called()

    def test_probe_timeout_clipped_to_remaining_original_clock(self):
        self.trial.remaining_seconds.return_value=7
        self.assertEqual(launch.main(self.argv),0)
        self.assertEqual(self.probe.call_args.kwargs['timeout'],7)
        self.assertEqual(self.probe.call_args.kwargs['deadline_monotonic'],1000)

    def test_expired_before_probe_closes_without_probe(self):
        self.trial.remaining_seconds.return_value=0
        with self.assertRaisesRegex(launch.AuthorityDenied,'admission_closed'):launch.main(self.argv)
        self.probe.assert_not_called();self.trial.close.assert_called_once()

    def test_epoch_changed_during_probe_never_opens_mailbox(self):
        self.trial.current_epoch_active.side_effect=[True,False]
        with self.assertRaisesRegex(launch.AuthorityDenied,'admission_closed'):launch.main(self.argv)
        self.probe.assert_called_once();self.run.assert_not_called();self.trial.close.assert_called_once()

    def test_deadline_during_probe_never_opens_mailbox(self):
        self.trial.remaining_seconds.side_effect=[7,0]
        with self.assertRaisesRegex(launch.AuthorityDenied,'admission_closed'):launch.main(self.argv)
        self.run.assert_not_called();self.trial.close.assert_called_once()

    def test_monitor_start_failure_still_closes(self):
        self.trial.start_monitor.side_effect=RuntimeError('observer unavailable')
        with self.assertRaisesRegex(RuntimeError,'observer unavailable'):launch.main(self.argv)
        self.probe.assert_not_called();self.trial.close.assert_called_once()

    def test_failed_probe_halts_and_closes_without_service(self):
        self.probe.return_value={'status':'failed','exit_code':1}
        self.assertEqual(launch.main(self.argv),1)
        self.authority.halt.assert_called_once();self.run.assert_not_called()
        self.trial.close.assert_called_once()

    def test_probe_exception_still_closes(self):
        self.probe.side_effect=OSError('probe unavailable')
        with self.assertRaisesRegex(OSError,'probe unavailable'):launch.main(self.argv)
        self.trial.close.assert_called_once();self.run.assert_not_called()

    def test_loaded_module_read_failure_after_ownership_closes(self):
        self.mocks[2].side_effect=[self.identity,OSError('module read failed')]
        with self.assertRaisesRegex(OSError,'module read failed'):launch.main(self.argv)
        self.trial.close.assert_called_once();self.run.assert_not_called()

    def test_launch_record_publication_failure_still_closes(self):
        self.publish.side_effect=OSError('publication failed')
        with self.assertRaisesRegex(OSError,'publication failed'):launch.main(self.argv)
        self.trial.close.assert_called_once();self.run.assert_not_called()

    def test_service_exception_still_closes(self):
        self.run.side_effect=RuntimeError('service failed')
        with self.assertRaisesRegex(RuntimeError,'service failed'):launch.main(self.argv)
        self.trial.close.assert_called_once()

    def test_close_failure_not_masked_as_normal_return(self):
        self.trial.close.side_effect=RuntimeError('cleanup unverified')
        with self.assertRaisesRegex(RuntimeError,'cleanup unverified'):launch.main(self.argv)


if __name__=='__main__':unittest.main()
