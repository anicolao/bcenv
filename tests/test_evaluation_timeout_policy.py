"""Synthetic evaluator policy/SQLite-free durability tests; no child execution."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tools import evaluation_timeout_policy as policy


def qualified():
    identity=dict(engine_version='2023.synthetic',engine_sha256='a'*64,map_sha256='b'*64,
        teams={slot:dict(package='synthetic_'+slot,source_sha256='c'*64,binary_sha256='d'*64) for slot in ('A','B')})
    command=['/pinned/bcenv','run','--team-a','/fixed/A','--team-b','/fixed/B','--map','/fixed/map.map23',
        '--output','/owned/assignment0','--timeout','300','--deadline-monotonic','1000',
        '--cleanup-ack','/owned/assignment0-ack.json']
    expected=dict(assignment_id='assignment0',index=0,debit_id='debit0',protocol_sha256='e'*64,
        runtime_identity={'closure_sha256':'f'*64},command=command,input_identity=identity,
        per_match_seconds=300,global_deadline_monotonic=1000)
    result=dict(identity,schema='bcenv.artifact.v1',kind='match',season='2023',status='timeout',winner=None,
        process=dict(command=['/pinned/java','-Xmx512m','-cp','/pinned/engine.jar','battlecode.server.Main','-c=-'],status='timeout',exit_code=-9,child_reaped=True,deadline_monotonic=1000,
            launch_checked_monotonic=100,termination_requested_monotonic=400.1,reaped_monotonic=400.2,
            finished_monotonic=400.3,elapsed_seconds=300.3))
    evidence=dict(assignment_id='assignment0',index=0,debit_id='debit0',runtime_identity=copy.deepcopy(expected['runtime_identity']),
        command=list(command),result_bytes=json.dumps(result).encode(),ack_bytes=json.dumps(policy.ACK).encode(),
        outer_wait_completed=True,outer_returncode=1,outer_wait_monotonic=401,
        ownership_unambiguous=True,artifacts_validated=True,other_infrastructure_failure=False)
    return expected,evidence


class TimeoutPolicyTests(unittest.TestCase):
    def setUp(self):
        self.no_child=mock.patch.object(subprocess,'Popen',side_effect=AssertionError('no child fixtures'))
        self.no_child.start();self.addCleanup(self.no_child.stop)
        self.expected,self.evidence=qualified()
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'censored.json'

    def classify(self):return policy.classify_timeout(self.expected,self.evidence,now_monotonic=402)

    def change_result(self,**values):
        result=json.loads(self.evidence['result_bytes']);result.update(values)
        self.evidence['result_bytes']=json.dumps(result).encode()

    def change_process(self,**values):
        result=json.loads(self.evidence['result_bytes']);result['process'].update(values)
        self.evidence['result_bytes']=json.dumps(result).encode()

    def test_qualified_positive_chain_not_diagnostics_or_exit_code_alone(self):
        result=self.classify()
        self.assertEqual('timeout_censored',result['status']);self.assertFalse(result['scored'])
        self.assertNotIn('eligible_next_untouched',result)
        self.assertEqual(hashlib.sha256(self.evidence['result_bytes']).hexdigest(),result['result_sha256'])
        self.assertEqual(hashlib.sha256(self.evidence['ack_bytes']).hexdigest(),result['ack_sha256'])

    def test_build_package_extraction_and_completed_engine_never_qualify(self):
        for change in ({'kind':'build'},{'kind':'source-package'},{'kind':'replay-query'},
                       {'extraction':{'status':'timeout'}},{'status':'completed','winner':'A'}):
            with self.subTest(change=change):
                self.expected,self.evidence=qualified();self.change_result(**change)
                with self.assertRaises(policy.CensoringRefused):self.classify()
        self.expected,self.evidence=qualified();self.change_process(status='succeeded',exit_code=0)
        with self.assertRaises(policy.CensoringRefused):self.classify()

    def test_positive_inner_wait_required_even_with_minus_nine(self):
        for values in ({'child_reaped':False},{'child_reaped':1},{'not_started':True},{'launch_error':'missing'},
                       {'reaped_monotonic':None},{'exit_code':None},{'exit_code':True}):
            with self.subTest(values=values):
                self.expected,self.evidence=qualified();self.change_process(**values)
                with self.assertRaises(policy.CensoringRefused):self.classify()

    def test_global_expiry_or_clipping_clock_regression_and_changed_deadline_fail(self):
        for values in ({'finished_monotonic':1000},{'termination_requested_monotonic':1000},
                       {'deadline_monotonic':999},{'termination_requested_monotonic':90},
                       {'reaped_monotonic':400.0},{'finished_monotonic':400.0}):
            with self.subTest(values=values):
                self.expected,self.evidence=qualified();self.change_process(**values)
                with self.assertRaises(policy.CensoringRefused):self.classify()
        self.expected,self.evidence=qualified()
        with self.assertRaises(policy.CensoringRefused):policy.classify_timeout(self.expected,self.evidence,now_monotonic=1000)

    def test_missing_outer_wait_ack_or_transport_ambiguity_fails(self):
        for key,value in (('outer_wait_completed',False),('outer_returncode',0),('outer_returncode',True),
                          ('outer_wait_monotonic',399),('ownership_unambiguous',False),
                          ('artifacts_validated',False),('other_infrastructure_failure',True)):
            with self.subTest(key=key):
                self.expected,self.evidence=qualified();self.evidence[key]=value
                with self.assertRaises(policy.CensoringRefused):self.classify()
        self.expected,self.evidence=qualified()
        self.evidence['ack_bytes']=json.dumps(dict(policy.ACK,direct_child_waits_completed=1)).encode()
        with self.assertRaises(policy.CensoringRefused):self.classify()

    def test_identity_and_original_command_are_exact(self):
        for key,value in (('assignment_id','other'),('debit_id','other'),('index',1),('runtime_identity',{'other':'x'})):
            with self.subTest(key=key):
                self.expected,self.evidence=qualified();self.evidence[key]=value
                with self.assertRaises(policy.CensoringRefused):self.classify()
        self.expected,self.evidence=qualified();self.change_result(map_sha256='0'*64)
        with self.assertRaises(policy.CensoringRefused):self.classify()

    def test_no_cancel_fd_cap_override_or_option_abbreviation(self):
        for appended in (['--cancel-fd','8'],['--timeout','600'],['--time','600'],['--cancel-fd=8']):
            self.expected,self.evidence=qualified()
            self.expected['command']+=appended;self.evidence['command']+=appended
            with self.assertRaises(policy.CensoringRefused):self.classify()
        self.expected,self.evidence=qualified();self.expected['per_match_seconds']=301
        with self.assertRaises(policy.CensoringRefused):self.classify()

    def test_numeric_types_and_nonfinite_json_rejected(self):
        for value in (True,float('nan'),float('inf'),'400'):
            self.expected,self.evidence=qualified();self.change_process(reaped_monotonic=value)
            with self.assertRaises(policy.CensoringRefused):self.classify()
        self.expected,self.evidence=qualified();self.change_process(elapsed_seconds=299.999)
        with self.assertRaises(policy.CensoringRefused):self.classify()
        self.expected,self.evidence=qualified();self.evidence['result_bytes']=b'{"status":"timeout","status":"completed"}'
        with self.assertRaises(policy.CensoringRefused):self.classify()

    def test_commit_returns_eligibility_only_after_fsync_readback(self):
        with mock.patch.object(policy.os,'fsync',wraps=policy.os.fsync) as synced:
            receipt=policy.publish_censored(self.path,self.expected,self.evidence,clock=iter([402,403]).__next__)
        self.assertTrue(receipt['eligible_next_untouched']);self.assertEqual(3,synced.call_count)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),receipt['record_sha256'])
        self.assertEqual([self.path],list(self.path.parent.iterdir()))

    def test_invalid_classification_never_writes(self):
        self.evidence['outer_wait_completed']=False
        with self.assertRaises(policy.CensoringRefused):policy.publish_censored(self.path,self.expected,self.evidence,clock=lambda:402)
        self.assertFalse(list(self.path.parent.iterdir()))

    def test_existing_record_never_overwritten_or_reconciled_as_retry(self):
        self.path.write_text('original')
        with self.assertRaises(policy.CensoringCommitUnconfirmed):policy.publish_censored(self.path,self.expected,self.evidence,clock=lambda:402)
        self.assertEqual('original',self.path.read_text())
        self.assertTrue(list(self.path.parent.glob('*.pending')))

    def test_write_or_prepublication_fsync_failure_never_eligible(self):
        with mock.patch.object(policy.os,'fsync',side_effect=OSError('disk unavailable')):
            with self.assertRaises(policy.CensoringCommitUnconfirmed):policy.publish_censored(self.path,self.expected,self.evidence,clock=lambda:402)
        self.assertFalse(self.path.exists());self.assertTrue(list(self.path.parent.glob('*.pending')))

    def test_postpublication_fsync_failure_retains_record_but_no_eligibility(self):
        calls=[None,OSError('directory failure')]
        with mock.patch.object(policy.os,'fsync',side_effect=calls):
            with self.assertRaises(policy.CensoringCommitUnconfirmed):policy.publish_censored(self.path,self.expected,self.evidence,clock=lambda:402)
        self.assertTrue(self.path.exists());self.assertEqual('timeout_censored',json.loads(self.path.read_bytes())['status'])
        self.assertTrue(list(self.path.parent.glob('*.pending')))

    def test_postcommit_global_expiry_retains_censored_record_but_stops(self):
        with self.assertRaises(policy.CensoringCommitUnconfirmed):
            policy.publish_censored(self.path,self.expected,self.evidence,clock=iter([402,1000]).__next__)
        self.assertTrue(self.path.exists())

    def test_postcommit_clock_regression_stops(self):
        with self.assertRaises(policy.CensoringCommitUnconfirmed):
            policy.publish_censored(self.path,self.expected,self.evidence,clock=iter([402,401]).__next__)
        self.assertTrue(self.path.exists())

    def test_shared_preflight_rejects_alias_before_execution(self):
        self.expected['command']+=['--cancel-f','7']
        with self.assertRaises(policy.CensoringRefused):policy.validate_expected(self.expected,now_monotonic=99)

    def test_missing_engine_command_contract_is_not_positive_evidence(self):
        self.change_process(command=[])
        with self.assertRaises(policy.CensoringRefused):self.classify()

    def test_readback_mismatch_after_link_preserves_failure_evidence(self):
        real_link=policy.os.link
        def changed(*args,**kwargs):
            real_link(*args,**kwargs)
            self.path.write_text('changed after publication')
        with mock.patch.object(policy.os,'link',side_effect=changed):
            with self.assertRaises(policy.CensoringCommitUnconfirmed):policy.publish_censored(self.path,self.expected,self.evidence,clock=lambda:402)
        self.assertEqual('changed after publication',self.path.read_text())

    def test_nested_runtime_identity_types_are_exact(self):
        self.expected['runtime_identity']['version']=1
        self.evidence['runtime_identity']['version']=True
        with self.assertRaises(policy.CensoringRefused):self.classify()
