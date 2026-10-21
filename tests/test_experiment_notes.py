"""Synthetic prospective-order evidence, never claims hypothesis quality."""
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from services.local_trial import LocalTrial, encoded, closed_dispatch
from test_local_trial import archive, Worker


class ExperimentNotesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.trial=LocalTrial(Path(self.temp.name)/'run',Worker(),require_experiment_notes=True,max_notes=3)
    def tearDown(self):
        self.trial.close();self.temp.cleanup()
    def note_request(self,**kwargs):
        data={'operation':'note','package':'bot','source_zip_base64':archive(),'text':'Prediction before results.','replay_references':[]}
        data.update(kwargs);return data
    def note(self,**kwargs):return self.trial.dispatch(self.note_request(**kwargs))
    def submit_request(self,note,**kwargs):
        data={'operation':'submit','package':'bot','source_zip_base64':archive(),'note_id':note['note_id'],'note_sha256':note['note_sha256']}
        data.update(kwargs);return data
    def complete(self,sid):
        for _ in range(200):
            result=self.trial.dispatch({'operation':'result','submission_id':sid})
            if result['status']!='accepted':return result
            time.sleep(.005)
        self.fail('worker not completed')
    def test_prospective_order_source_binding_and_append_only_chain(self):
        note=self.note();submitted=self.trial.dispatch(self.submit_request(note));result=self.complete(submitted['submission_id'])
        self.assertLess(note['audit_receipt']['sequence'],submitted['submission_receipt']['sequence'])
        self.assertEqual(result['note_id'],note['note_id']);self.assertEqual(result['note_sha256'],note['note_sha256'])
        self.assertEqual(result['source_sha256'],note['source_sha256']);self.assertEqual(result['submission_receipt'],submitted['submission_receipt'])
        submitted['submission_receipt']['sequence'] = -1
        self.assertGreater(self.complete(submitted['submission_id'])['submission_receipt']['sequence'], 0)
        events=[json.loads(x) for x in (self.trial.root/'audit.jsonl').read_text().splitlines()]
        previous='0'*64
        for sequence,event in enumerate(events,1):
            actual=event.pop('event_sha256');self.assertEqual(event['sequence'],sequence);self.assertEqual(event['previous_sha256'],previous)
            self.assertEqual(hashlib.sha256(encoded(event)).hexdigest(),actual);previous=actual
        note['text']='mutated';self.assertNotEqual(self.trial.notes[note['note_id']]['text'],'mutated')
    def test_required_unknown_reused_and_mismatched_notes(self):
        raw={'operation':'submit','package':'bot','source_zip_base64':archive()}
        with self.assertRaisesRegex(ValueError,'note_required'):self.trial.dispatch(raw)
        note=self.note()
        for delta in ({'note_id':'n_'+'0'*32},{'note_sha256':'0'*64},{'source_zip_base64':archive(text='package bot; class RobotPlayer { int changed; }')}):
            with self.assertRaises(ValueError):self.trial.dispatch(self.submit_request(note,**delta))
        sid=self.trial.dispatch(self.submit_request(note))['submission_id'];self.complete(sid)
        with self.assertRaisesRegex(ValueError,'note_already_used'):self.trial.dispatch(self.submit_request(note))
    def test_replay_reference_is_prior_exact_query_and_response(self):
        first=self.note();sid=self.trial.dispatch(self.submit_request(first))['submission_id'];self.complete(sid)
        q={'round_start':1,'round_end':2,'robot_id':None,'record_offset':0,'record_limit':64}
        replay=self.trial.dispatch({'operation':'replay','submission_id':sid,'game_index':0,'query':q})
        ref=replay['replay_reference'];self.assertEqual(ref['query'],q);self.assertEqual(ref['submission_id'],sid)
        self.assertEqual(ref['feedback_sha256'],hashlib.sha256(encoded(replay['feedback'])).hexdigest())
        note=self.note(replay_references=[ref['reference_id']])
        self.assertLess(ref['audit_receipt']['sequence'],note['audit_receipt']['sequence'])
        self.assertEqual(note['replay_references'][0]['replay_sha256'],replay['replay_sha256'])
        ref['query']['round_start']=999
        self.assertEqual(note['replay_references'][0]['query']['round_start'],1)
    def test_unknown_future_duplicate_and_oversize_references(self):
        for refs in (['r_'+'0'*32],['bad']*2,['bad']*9,{'bad':1},[True]):
            with self.assertRaises(ValueError):self.note(replay_references=refs)
    def test_bounded_notes_and_closed_fields(self):
        for delta in ({'text':''},{'text':' '*5},{'text':'é'*2049},{'text':42},{'extra':'field'},{'note_id':'overwrite'}):
            with self.assertRaises(ValueError):self.note(**delta)
        for _ in range(3):self.note()
        with self.assertRaisesRegex(ValueError,'note_limit'):self.note()
    def test_clock_regression_cannot_change_sequence_order(self):
        with patch('services.local_trial.time.time_ns',return_value=100):note=self.note()
        with patch('services.local_trial.time.time_ns',return_value=1):submitted=self.trial.dispatch(self.submit_request(note))
        self.assertGreater(note['audit_receipt']['time_ns'],submitted['submission_receipt']['time_ns'])
        self.assertLess(note['audit_receipt']['sequence'],submitted['submission_receipt']['sequence'])
    def test_note_audit_failure_halts_without_minting_note(self):
        with patch.object(self.trial,'audit',side_effect=OSError('disk')):
            with self.assertRaisesRegex(ValueError,'run_halted'):self.note()
        self.assertTrue(self.trial.halted);self.assertEqual(self.trial.notes,{})
    def test_scheduling_failure_is_terminal_and_preserves_consumed_note(self):
        note=self.note()
        with patch.object(self.trial.executor,'submit',side_effect=RuntimeError('cannot start thread')):
            submitted=self.trial.dispatch(self.submit_request(note))
        self.assertEqual(submitted['reason'],'scheduling_failure');self.assertTrue(self.trial.halted)
        self.assertIn(note['note_id'],self.trial.used_notes)
        result=self.complete(submitted['submission_id'])
        self.assertEqual(result['note_id'],note['note_id']);self.assertEqual(result['submission_receipt'],submitted['submission_receipt'])
        self.assertTrue((self.trial.root/('submission-failure-'+submitted['submission_id']+'.json')).is_file())
    def test_output_limit_terminal_preserves_note_binding(self):
        class Oversized(Worker):
            def run(self,*args):return {'status':'complete','oversized':'x'*70000}
        self.trial.worker=Oversized();note=self.note();submitted=self.trial.dispatch(self.submit_request(note))
        result=self.complete(submitted['submission_id'])
        self.assertEqual(result['reason'],'output_limit');self.assertEqual(result['note_id'],note['note_id'])
        self.assertEqual(result['submission_receipt'],submitted['submission_receipt'])
    def test_persistence_failure_terminal_preserves_note_binding(self):
        original=Path.write_bytes
        def fail_public(path,data):
            if path.name=='public-result.json':raise OSError('disk')
            return original(path,data)
        note=self.note()
        with patch.object(Path,'write_bytes',fail_public):
            submitted=self.trial.dispatch(self.submit_request(note));result=self.complete(submitted['submission_id'])
        self.assertEqual(result['reason'],'persistence_failure');self.assertEqual(result['note_id'],note['note_id'])
        self.assertEqual(result['submission_receipt'],submitted['submission_receipt'])

    def test_closed_wire_errors_and_legacy_optional_profile(self):
        response=json.loads(closed_dispatch(self.trial,encoded({'operation':'submit','package':'bot','source_zip_base64':archive()})))
        self.assertEqual(response['reason'],'note_required')
        legacy=LocalTrial(Path(self.temp.name)/'legacy',Worker())
        try:
            with self.assertRaisesRegex(ValueError,'notes_disabled'):legacy.dispatch(self.note_request())
            self.assertEqual(legacy.dispatch({'operation':'submit','package':'bot','source_zip_base64':archive()})['status'],'accepted')
        finally:legacy.close()

if __name__=='__main__':unittest.main()
