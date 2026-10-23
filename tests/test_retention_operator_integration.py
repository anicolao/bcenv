"""Real opt-in SQL gate/export + mocked remote Git, no game or subprocess."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from services.epoch_authority import ClockSample
from services.resumable_authority import ResumableAuthority
from tests.test_public_retention_git import FakeGit, OLD
from tools.public_retention_operator import prepare_checkpoint, publish_and_ack
from tools.public_retention_git import PublicationUncertain


class RetentionOperatorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.clock=ClockSample('test',100,200)
        config={'trial':{'retention':{'schema':'public-retention-v1',
            'fixed_inputs_sha256':'f'*64,'repository_id':1431035719}}}
        self.a=ResumableAuthority.create(self.root/'authority.sqlite', config=config,
            author='/same/native/task', paths={k:str(self.root/k) for k in ('service','artifacts','mailbox','control','author')},
            clock=lambda:self.clock)
        self.checkout=self.root/'checkout';self.checkout.mkdir();self.git=FakeGit(self.checkout)
        for target in ('subprocess.Popen','os.system','os.kill','os.killpg'):
            guard=patch(target,side_effect=AssertionError('actual execution forbidden'))
            guard.start();self.addCleanup(guard.stop)

    def publish(self,prepared):
        return publish_and_ack(self.a,0,self.root/'export',prepared,
            approved_inventory_sha256=prepared['inventory_sha256'],checkout=self.checkout,
            expected_old_commit=OLD,readback_dir=self.root/'readback',git_runner=self.git)

    def test_initial_real_gate_export_readback_ack_preserves_clock_and_reads(self):
        before=self.a.inspect()['run']
        prepared=prepare_checkpoint(self.a,0,self.root/'export',approved_sha256=[])
        self.a.request(0,'status_after_export',{'operation':'validation_status'})
        self.assertTrue(self.a.retention_gate(0)['pending'])
        result=self.publish(prepared)
        self.assertEqual('retained',result['status'])
        self.assertFalse(self.a.retention_gate(0)['pending'])
        after=self.a.inspect()
        self.assertEqual(1,after['counters']['reads'])
        for key in ('wall_deadline','mono_deadline','started_wall','started_mono'):
            if key in before:self.assertEqual(before[key],after['run'][key])

    def test_terminal_after_expiry_archives_gap_without_reopening(self):
        initial = self.a.retention_descriptor()
        self.clock = ClockSample('test', 50000, 50100)
        terminal = self.a.retention_register_terminal(0, terminal_identity='b' * 64,
            lifecycle={'logical_close': 'refused', 'cleanup': 'unknown', 'evidence_sha256': 'c' * 64})
        self.assertNotEqual(initial['checkpoint_id'], terminal['checkpoint_id'])
        prepared = prepare_checkpoint(self.a, 0, self.root/'export', approved_sha256=[])
        evidence = json.loads((self.root/'export/evidence.json').read_bytes())
        history = evidence['retention_history']
        self.assertEqual(history[-1]['effects']['terminal']['lifecycle']['cleanup'], 'unknown')
        self.assertEqual(history[-1]['effects']['unretained_gap']['checkpoint_id'], initial['checkpoint_id'])
        result = self.publish(prepared)
        self.assertEqual('retained', result['status'])
        self.assertTrue(self.a.retention_gate(0)['terminal'])
        self.assertEqual(0, self.a.inspect()['counters']['versions'])

    def test_uncertain_remote_keeps_real_gate_sticky(self):
        prepared=prepare_checkpoint(self.a,0,self.root/'export',approved_sha256=[])
        self.git.push_error=True
        with self.assertRaises(PublicationUncertain):self.publish(prepared)
        gate=self.a.retention_gate(0)
        self.assertTrue(gate['pending']);self.assertTrue(gate['failed'])
        self.assertEqual(1,sum(a[0]=='push' for a,_ in self.git.calls))


if __name__=='__main__':unittest.main()
