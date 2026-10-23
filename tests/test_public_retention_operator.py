"""Operator bridge tests: fake authority and Git; real bounded files only."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tests.test_public_retention_git import FakeGit, OLD
from tools.public_retention_operator import publish_and_ack
from tools.public_retention_git import PublicationUncertain


def sha(b): return hashlib.sha256(b).hexdigest()


class Authority:
    authority_id = 'a_public'
    config_digest = 'a' * 64
    def __init__(self):
        self.descriptor = {'schema': 'bcenv.public-retention-descriptor.v1',
            'authority_id': self.authority_id, 'config_digest': self.config_digest,
            'fixed_inputs_sha256': 'f' * 64, 'epoch': 0, 'checkpoint_id': 'retention_0',
            'sequence': 0, 'reason': 'initial', 'effect_sha256': 'e' * 64,
            'descriptor_sha256': 'd' * 64}
        self.failures = []; self.acks = []
    def retention_descriptor(self): return self.descriptor
    def retention_fail(self, *a, **kw): self.failures.append((a, kw))
    def retention_ack(self, *a, **kw):
        self.acks.append((a,kw)); return {'status': 'retained'}


class OperatorBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.export = self.root/'export'; self.export.mkdir()
        self.checkout = self.root/'checkout'; self.checkout.mkdir()
        self.authority = Authority(); self.git = FakeGit(self.checkout)
        evidence = json.dumps({'schema':'bcenv.public-retention-snapshot.v1',
            'retention':self.authority.descriptor}).encode()
        (self.export/'evidence.json').write_bytes(evidence)
        inv = {'schema':'bcenv.public-retention-inventory.v1','files':{
            'evidence.json':{'sha256':sha(evidence),'bytes':len(evidence)}}}
        raw = json.dumps(inv).encode(); (self.export/'inventory.json').write_bytes(raw)
        self.pin = sha(raw)
        outer = {'schema':inv['schema'],'files':dict(inv['files'])}
        outer['files']['inventory.json']={'sha256':self.pin,'bytes':len(raw)}
        self.prepared={'schema':'bcenv.public-retention-prepared.v1','descriptor':self.authority.descriptor.copy(),
            'inventory':outer,'inventory_sha256':self.pin,'evidence_sha256':sha(evidence)}
        for target in ('subprocess.Popen','os.system','os.kill','os.killpg'):
            guard=patch(target,side_effect=AssertionError('actual execution forbidden'))
            guard.start();self.addCleanup(guard.stop)

    def call(self, **kw):
        args=dict(approved_inventory_sha256=self.pin,checkout=self.checkout,
            expected_old_commit=OLD,readback_dir=self.root/'readback',git_runner=self.git)
        args.update(kw)
        return publish_and_ack(self.authority,0,self.export,self.prepared,**args)

    def test_verified_remote_before_ack(self):
        result=self.call()
        self.assertEqual('retained',result['status']);self.assertEqual(1,len(self.authority.acks))
        args=self.authority.acks[0][1]
        self.assertIs(args['readback_verified'],True)
        self.assertEqual(self.prepared['evidence_sha256'],args['snapshot_binding_sha256'])
        self.assertNotIn(str(self.root),json.dumps(result))
        self.assertFalse(self.authority.failures)

    def test_push_uncertain_no_ack_no_retry(self):
        self.git.push_error=True
        with self.assertRaises(PublicationUncertain):self.call()
        self.assertFalse(self.authority.acks)
        self.assertEqual('publication_unknown',self.authority.failures[-1][1]['reason'])
        self.assertEqual(1,sum(a[0]=='push' for a,_ in self.git.calls))

    def test_readback_corruption_no_ack(self):
        self.git.corrupt_readback=True
        with self.assertRaises(PublicationUncertain):self.call()
        self.assertFalse(self.authority.acks)

    def test_ack_unknown_reports_failure_without_retry(self):
        with patch.object(self.authority,'retention_ack',side_effect=OSError('unknown commit')):
            with self.assertRaises(OSError):self.call()
        self.assertEqual('ack_unknown',self.authority.failures[-1][1]['reason'])
        self.assertEqual(1,sum(a[0]=='push' for a,_ in self.git.calls))

    def test_changed_descriptor_refuses_before_push(self):
        self.authority.descriptor=dict(self.authority.descriptor,sequence=1)
        with self.assertRaises(ValueError):self.call()
        self.assertFalse(self.git.calls)

    def test_unapproved_inventory_refuses_before_push(self):
        with self.assertRaises(ValueError):self.call(approved_inventory_sha256='0'*64)
        self.assertFalse(self.git.calls)


if __name__=='__main__':unittest.main()
