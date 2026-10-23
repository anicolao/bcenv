"""Bounded in-process transport tests: temporary files/fakes, never child processes."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import resumable_mailbox as rpc

AID = 'a'*32
RID = 'b'*32


def envelope(body, epoch=0, rid=RID):
    return dict(schema=rpc.SCHEMA, authority_id=AID, epoch=epoch,
                request_id=rid, body_sha256=rpc.digest(body), body=body)


class Trial:
    def __init__(self):
        self.authority = type('Authority', (), {'authority_id': AID})()
        self.epoch = 0
        self.stop_requested = threading.Event()
        self.active = True
        self.calls = []
        self.storage = []
        self.reply = {'status':'complete', 'test_canonical': True}

    def storage_checkpoint(self, stage):
        self.storage.append(stage)

    def remaining_seconds(self):
        return 100

    def current_epoch_active(self):
        return self.active

    def request(self, rid, body):
        self.calls.append((rid,body))
        self.stop_requested.set()
        return self.reply


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mailbox = self.root/'mailbox'
        self.trial = Trial()
        self.guard = mock.patch.object(subprocess, 'Popen', side_effect=AssertionError('no subprocess fixture'))
        self.guard.start(); self.addCleanup(self.guard.stop)

    def run_server(self, files, *, publish_hook=None):
        original = rpc._publish
        def publish(path, data):
            if publish_hook:
                publish_hook(path,data)
            original(path,data)
            if path.name == 'ready.json':
                for name, raw in files.items():
                    (self.mailbox/'requests'/name).write_bytes(raw)
        with mock.patch.object(rpc, '_publish', side_effect=publish):
            return rpc.serve_mailbox(self.trial, self.mailbox)

    def response(self, rid=RID):
        return json.loads((self.mailbox/'responses'/(rid+'.json')).read_text())

    def test_canonical_request_precedes_response_delivery(self):
        body={'operation':'validation_status'}
        def hook(path,data):
            if path.parent.name == 'responses':
                self.assertEqual(self.trial.calls, [(RID,body)])
        result=self.run_server({RID+'.json':rpc.encoded(envelope(body))}, publish_hook=hook)
        self.assertEqual(result['processed'],1)
        self.assertEqual(self.response()['response'],self.trial.reply)
        self.assertTrue(self.response()['canonical'])
        self.assertFalse((self.mailbox/'requests'/(RID+'.json')).exists())

    def test_malformed_json_debits_canonical_denial(self):
        self.run_server({RID+'.json':b'{broken'})
        rid,body=self.trial.calls[0]
        self.assertEqual(rid,RID)
        self.assertEqual(body['operation'],'invalid_request')
        self.assertEqual(body['transport_sha256'],hashlib.sha256((RID+'.json').encode()+b'\0{broken').hexdigest())

    def test_nonuuid_name_uses_deterministic_invalid_id(self):
        raw=b'{}'; name='bad.json'
        self.run_server({name:raw})
        rid=hashlib.sha256(name.encode()+b'\0'+raw).hexdigest()[:32]
        self.assertEqual(self.trial.calls[0][0],rid)
        self.assertTrue(self.response(rid)['canonical'])

    def test_undecodable_filename_charged_by_filesystem_bytes(self):
        name=os.fsdecode(b'\xff.json');raw=b'{}'
        self.run_server({name:raw})
        expected=hashlib.sha256(os.fsencode(name)+b'\0'+raw).hexdigest()[:32]
        self.assertEqual(self.trial.calls[0][0],expected)
        self.assertEqual(self.trial.calls[0][1]['operation'],'invalid_request')

    def test_symlink_request_charged_without_following_target(self):
        target=self.root/'target';target.write_bytes(b'secret not read')
        def hook(path,data):
            if path.name=='ready.json':
                (self.mailbox/'requests'/(RID+'.json')).symlink_to(target)
        self.run_server({},publish_hook=hook)
        self.assertEqual(self.trial.calls[0][1]['operation'],'invalid_request')
        self.assertEqual(target.read_bytes(),b'secret not read')

    def test_unknown_request_io_error_not_fabricated_as_debit(self):
        with mock.patch.object(rpc,'_read',side_effect=PermissionError('unavailable')):
            with self.assertRaises(PermissionError):
                self.run_server({RID+'.json':b'{}'})
        self.assertEqual(self.trial.calls,[])

    def test_foreign_epoch_never_calls_authority(self):
        def hook(path,data):
            if path.parent.name=='responses':self.trial.stop_requested.set()
        result=self.run_server({RID+'.json':rpc.encoded(envelope({'operation':'submit'},epoch=1))},publish_hook=hook)
        self.assertEqual(self.trial.calls,[])
        self.assertEqual(result['transport_rejections'],1)
        self.assertFalse(self.response()['canonical'])
        self.assertNotIn('response',self.response())

    def test_wrong_authority_never_calls_authority(self):
        value=envelope({'operation':'submit'});value['authority_id']='c'*32
        def hook(path,data):
            if path.parent.name=='responses':self.trial.stop_requested.set()
        self.run_server({RID+'.json':rpc.encoded(value)},publish_hook=hook)
        self.assertEqual(self.trial.calls,[])

    def test_staging_is_not_ingress(self):
        with mock.patch.object(self.trial.stop_requested,'wait',side_effect=lambda _:self.trial.stop_requested.set()):
            result=self.run_server({RID+'.tmp':b''})
        self.assertEqual(result['processed'],0)
        self.assertTrue((self.mailbox/'requests'/(RID+'.tmp')).exists())

    def test_epoch_loss_stops_before_dispatch(self):
        self.trial.active=False
        result=self.run_server({RID+'.json':rpc.encoded(envelope({'operation':'submit'}))})
        self.assertEqual(result['processed'],0)
        self.assertTrue((self.mailbox/'requests'/(RID+'.json')).exists())

    def test_epoch_loss_in_request_propagates_without_delivery(self):
        self.trial.request=mock.Mock(side_effect=RuntimeError('stale_epoch'))
        with self.assertRaisesRegex(RuntimeError,'stale_epoch'):
            self.run_server({RID+'.json':rpc.encoded(envelope({'operation':'submit'}))})
        self.trial.request.assert_called_once()
        self.assertFalse((self.mailbox/'responses'/(RID+'.json')).exists())

    def test_delivery_failure_preserves_uncertain_request_no_retry(self):
        def hook(path,data):
            if path.parent.name=='responses':raise OSError('disk failure')
        with self.assertRaisesRegex(OSError,'disk failure'):
            self.run_server({RID+'.json':rpc.encoded(envelope({'operation':'submit'}))},publish_hook=hook)
        self.assertEqual(len(self.trial.calls),1)
        self.assertTrue((self.mailbox/'requests'/(RID+'.json')).exists())

    def test_response_wrapper_overflow_never_retries_operation(self):
        self.trial.reply={'large':'x'*rpc.MAX_RESPONSE}
        with self.assertRaisesRegex(ValueError,'response_limit'):
            self.run_server({RID+'.json':rpc.encoded(envelope({'operation':'result'}))})
        self.assertEqual(len(self.trial.calls),1)

    def test_body_hash_mismatch_is_charged_invalid(self):
        value=envelope({'operation':'submit'});value['body_sha256']='0'*64
        self.run_server({RID+'.json':rpc.encoded(value)})
        self.assertEqual(self.trial.calls[0][1]['operation'],'invalid_request')

    def test_legacy_body_is_not_silently_adopted(self):
        self.run_server({RID+'.json':b'{"operation":"submit"}'})
        self.assertEqual(self.trial.calls[0][1]['operation'],'invalid_request')

    def test_duplicate_json_keys_invalid(self):
        self.run_server({RID+'.json':b'{"operation":"a","operation":"b"}'})
        self.assertEqual(self.trial.calls[0][1]['operation'],'invalid_request')

    def test_filename_id_binding(self):
        self.run_server({RID+'.json':rpc.encoded(envelope({'operation':'submit'},rid='c'*32))})
        self.assertEqual(self.trial.calls[0][1]['operation'],'invalid_request')

    def test_pending_cap_no_dispatch(self):
        with self.assertRaisesRegex(ValueError,'mailbox_queue_limit'):
            self.run_server({('%032x.json'%n):b'{}' for n in range(33)})
        self.assertEqual(self.trial.calls,[])

    def test_foreign_rejections_still_check_storage(self):
        def hook(path,data):
            if path.parent.name=='responses':self.trial.stop_requested.set()
        self.run_server({RID+'.json':rpc.encoded(envelope({},epoch=1))},publish_hook=hook)
        self.assertEqual(self.trial.storage,['mailbox_poll'])
        self.assertEqual(self.trial.calls,[])

    def test_transport_observation_cap_includes_foreign_no_sql_debit(self):
        files={('%032x.json'%n):rpc.encoded(envelope({},epoch=1,rid='%032x'%n)) for n in range(3)}
        with mock.patch.object(rpc,'MAX_OBSERVATIONS',2):
            with self.assertRaisesRegex(ValueError,'transport_observation_limit'):
                self.run_server(files)
        self.assertEqual(self.trial.calls,[])
        self.assertEqual(self.trial.storage,['mailbox_poll']*2)
        self.assertEqual(len(list((self.mailbox/'responses').iterdir())),2)
        self.assertEqual(len(list((self.mailbox/'requests').iterdir())),1)

    def test_storage_failure_precedes_request_and_delivery(self):
        self.trial.storage_checkpoint=mock.Mock(side_effect=ValueError('storage_halt'))
        with self.assertRaisesRegex(ValueError,'storage_halt'):
            self.run_server({RID+'.json':rpc.encoded(envelope({}))})
        self.assertEqual(self.trial.calls,[])
        self.assertEqual(list((self.mailbox/'responses').iterdir()),[])

    def prepare_client(self):
        self.mailbox.mkdir();(self.mailbox/'requests').mkdir();(self.mailbox/'responses').mkdir()

    def test_client_explicit_pins_and_binding(self):
        self.prepare_client();body={'operation':'validation_status'}
        value=rpc._wrapper(AID,0,RID,rpc.digest(body),response={'status':'ok'})
        with mock.patch.object(rpc,'_read',return_value=rpc.encoded(value)):
            self.assertEqual(rpc.mailbox_request(self.mailbox,body,authority_id=AID,epoch=0,request_id=RID),{'status':'ok'})
        submitted=json.loads((self.mailbox/'requests'/(RID+'.json')).read_text())
        self.assertEqual(submitted,envelope(body))

    def test_client_rejects_wrong_response_epoch(self):
        self.prepare_client();body={'operation':'result'}
        value=rpc._wrapper(AID,1,RID,rpc.digest(body),response={'status':'ok'})
        with mock.patch.object(rpc,'_read',return_value=rpc.encoded(value)):
            with self.assertRaisesRegex(ValueError,'response_binding_mismatch'):
                rpc.mailbox_request(self.mailbox,body,authority_id=AID,epoch=0,request_id=RID)

    def test_client_rejects_boolean_response_epoch(self):
        self.prepare_client();body={'operation':'result'}
        value=rpc._wrapper(AID,False,RID,rpc.digest(body),response={'status':'ok'})
        with mock.patch.object(rpc,'_read',return_value=rpc.encoded(value)):
            with self.assertRaisesRegex(ValueError,'response_binding_mismatch'):
                rpc.mailbox_request(self.mailbox,body,authority_id=AID,epoch=0,request_id=RID)

    def test_client_rejects_transport_error_as_noncanonical(self):
        self.prepare_client();body={'operation':'result'}
        value=rpc._wrapper(AID,0,RID,rpc.digest(body),rejection='epoch_identity_mismatch')
        with mock.patch.object(rpc,'_read',return_value=rpc.encoded(value)):
            with self.assertRaises(rpc.TransportRejected):
                rpc.mailbox_request(self.mailbox,body,authority_id=AID,epoch=0,request_id=RID)

    def test_client_timeout_one_request_no_retry(self):
        self.prepare_client()
        with mock.patch.object(rpc.time,'monotonic',side_effect=[0,1]):
            with self.assertRaisesRegex(TimeoutError,'uncertain'):
                rpc.mailbox_request(self.mailbox,{},authority_id=AID,epoch=0,request_id=RID,timeout=.5)
        self.assertEqual([p.name for p in (self.mailbox/'requests').iterdir()],[RID+'.json'])

    def test_client_does_not_overwrite_existing_request_or_consume_old_response(self):
        self.prepare_client();(self.mailbox/'responses'/(RID+'.json')).write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError,'already_present'):
            rpc.mailbox_request(self.mailbox,{},authority_id=AID,epoch=0,request_id=RID)
        self.assertEqual(list((self.mailbox/'requests').iterdir()),[])

    def test_bool_epoch_and_missing_pin_rejected(self):
        with self.assertRaisesRegex(ValueError,'invalid_epoch_identity'):
            rpc.mailbox_request(self.mailbox,{},authority_id=AID,epoch=True)
        with self.assertRaises(TypeError):rpc.mailbox_request(self.mailbox,{})

    def test_symlink_mailbox_rejected(self):
        self.prepare_client();link=self.root/'link';link.symlink_to(self.mailbox,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'symlink'):
            rpc.mailbox_request(link,{},authority_id=AID,epoch=0)


if __name__=='__main__':unittest.main()
