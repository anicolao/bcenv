import json
import pathlib
import subprocess
import tempfile
import unittest
from tools.evaluation_assignment import run_assignment, ACK


class IntegrationTests(unittest.TestCase):
    def setup_case(self, tmp):
        directory = pathlib.Path(tmp) / 'opaque-cell'
        expected = dict(assignment_id='opaque-1', index=0, debit_id='debit-1',
                        protocol_sha256='a'*64, runtime_identity='opaque-runtime',
                        input_identity={}, per_match_seconds=300,
                        global_deadline_monotonic=1000.0)
        expected['command'] = ['adapter', 'run', '--team-a', 'opaque-a', '--team-b', 'opaque-b', '--map', 'opaque-map', '--timeout', '300',
            '--deadline-monotonic', '1000.0', '--output', str(directory/'output'),
            '--cleanup-ack', str(directory/'cleanup.json')]
        return directory, expected

    def fake_run(self, directory, *, code=1, status='timeout', ack=True):
        def run(command, **kwargs):
            self.assertEqual(kwargs['timeout'], 990.0)
            output=directory/'output'; output.mkdir()
            (output/'result.json').write_text(json.dumps({'status':status}))
            if ack: (directory/'cleanup.json').write_text(json.dumps(ACK))
            return subprocess.CompletedProcess(command, code)
        return run

    def test_wait_and_durable_policy_precede_next_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory,e=self.setup_case(tmp); seen=[]
            def publish(path, expected, evidence, *, clock):
                self.assertTrue(evidence['outer_wait_completed'])
                self.assertEqual(evidence['outer_returncode'],1)
                self.assertEqual(evidence['outer_wait_monotonic'],10)
                self.assertEqual(expected['global_deadline_monotonic'],1000)
                self.assertEqual(path,directory/'censored.json')
                seen.append(evidence)
                return {'eligible_next_untouched':True}
            result=run_assignment(e,directory,validate_expected=lambda *a,**kw:None,validate_inputs=lambda *a:True,validate_artifacts=lambda *a:True,
                publish_censored=publish,run=self.fake_run(directory),clock=lambda:10)
            self.assertEqual(result['next_original_index'],1);self.assertEqual(len(seen),1)
            with self.assertRaises(FileExistsError):
                run_assignment(e,directory,validate_expected=lambda *a,**kw:None,validate_inputs=lambda *a:True,validate_artifacts=lambda *a:True,
                    publish_censored=publish,run=self.fake_run(directory),clock=lambda:10)
            self.assertEqual(len(seen),1)

    def test_missing_ack_stops_before_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory,e=self.setup_case(tmp)
            result=run_assignment(e,directory,validate_expected=lambda *a,**kw:None,validate_inputs=lambda *a:True,validate_artifacts=lambda *a:True,
                publish_censored=lambda *a,**kw:self.fail('must not publish'),
                run=self.fake_run(directory,ack=False),clock=lambda:10)
            self.assertEqual(result['status'],'stop')

    def test_outer_timeout_never_qualifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory,e=self.setup_case(tmp)
            def fail(*a,**kw):raise subprocess.TimeoutExpired('adapter',990)
            result=run_assignment(e,directory,validate_expected=lambda *a,**kw:None,validate_inputs=lambda *a:True,validate_artifacts=lambda *a:True,
                publish_censored=lambda *a,**kw:self.fail('must not publish'),run=fail,clock=lambda:10)
            self.assertEqual(result,{'status':'stop','reason':'TimeoutExpired'})

    def test_failed_publication_and_validation_stop(self):
        for valid in (True,False):
            with tempfile.TemporaryDirectory() as tmp:
                directory,e=self.setup_case(tmp)
                def publish(*a,**kw):raise OSError('fsync failed')
                result=run_assignment(e,directory,validate_expected=lambda *a,**kw:None,validate_inputs=lambda *a:True,validate_artifacts=lambda *a:valid,
                    publish_censored=publish,run=self.fake_run(directory),clock=lambda:10)
                self.assertEqual(result['status'],'stop')

    def test_global_expiry_after_commit_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory,e=self.setup_case(tmp);time=[10]
            def publish(*a,**kw):time[0]=1000;return {'eligible_next_untouched':True}
            result=run_assignment(e,directory,validate_expected=lambda *a,**kw:None,validate_inputs=lambda *a:True,validate_artifacts=lambda *a:True,
                publish_censored=publish,run=self.fake_run(directory),clock=lambda:time[0])
            self.assertEqual(result['status'],'stop')

    def test_argparse_alias_and_override_forms_rejected_before_launch(self):
        for extra in [['--cancel-fd=3'], ['--timeout=600'],
                      ['--deadline-monotonic=2000'], ['--dead', '2000'],
                      ['--cleanup-ack=other']]:
            with tempfile.TemporaryDirectory() as tmp:
                directory,e=self.setup_case(tmp);e['command'] += extra
                with self.assertRaises(ValueError):
                    run_assignment(e,directory,validate_expected=lambda *a,**kw:None,validate_inputs=lambda *a:True,
                        validate_artifacts=lambda *a:True,
                        publish_censored=lambda *a,**kw:self.fail('must not publish'),
                        run=lambda *a,**kw:self.fail('must not run'),clock=lambda:10)
                self.assertFalse(directory.exists())

    def test_completed_with_corrupt_ack_stops(self):
        for raw in ['{}', 'not-json', json.dumps(dict(ACK,direct_child_waits_completed=False))]:
            with tempfile.TemporaryDirectory() as tmp:
                directory,e=self.setup_case(tmp);base=self.fake_run(directory,code=0,status='completed')
                def run(*a,**kw):
                    result=base(*a,**kw);(directory/'cleanup.json').write_text(raw);return result
                result=run_assignment(e,directory,validate_expected=lambda *a,**kw:None,validate_inputs=lambda *a:True,
                    validate_artifacts=lambda *a:True,
                    publish_censored=lambda *a,**kw:self.fail('must not publish'),
                    run=run,clock=lambda:10)
                self.assertEqual(result['status'],'stop')

    def test_wrong_heap_or_engine_refused_by_required_artifact_gate(self):
        for actual in [['java-pinned', '-Xmx1g', '-cp', 'engine-pinned', 'Main'],
                       ['java-pinned', '-Xmx512m', '-cp', 'wrong-engine', 'Main']]:
            with tempfile.TemporaryDirectory() as tmp:
                directory,e=self.setup_case(tmp);base=self.fake_run(directory)
                def run(*a,**kw):
                    result=base(*a,**kw)
                    (directory/'output/result.json').write_text(json.dumps({'status':'timeout','process':{'command':actual}}))
                    return result
                def validate(expected,output,raw):
                    return json.loads(raw)['process']['command']==['java-pinned','-Xmx512m','-cp','engine-pinned','Main']
                result=run_assignment(e,directory,validate_expected=lambda *a,**kw:None,validate_inputs=lambda *a:True,
                    validate_artifacts=validate,
                    publish_censored=lambda *a,**kw:self.fail('must not publish'),run=run,clock=lambda:10)
                self.assertEqual(result,{'status':'stop','reason':'artifact_validation'})

    def test_completed_is_not_scored(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory,e=self.setup_case(tmp)
            result=run_assignment(e,directory,validate_expected=lambda *a,**kw:None,validate_inputs=lambda *a:True,validate_artifacts=lambda *a:True,
                publish_censored=lambda *a,**kw:self.fail('must not censor'),
                run=self.fake_run(directory,code=0,status='completed'),clock=lambda:10)
            self.assertEqual(result['status'],'completed_unscored');self.assertEqual(result['outer_wait_monotonic'],10)


if __name__=='__main__': unittest.main()
