"""Real policy plus synthetic subprocess, no engine/game execution."""
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
from tools import evaluation_timeout_policy as policy
from tools.evaluation_assignment import run_assignment
spec=importlib.util.spec_from_file_location('synthetic_policy_fixtures',REPO/'tests/test_evaluation_timeout_policy.py')
fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)

class RealPolicyIntegration(unittest.TestCase):
    def test_real_durable_publication_after_synthetic_actual_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=pathlib.Path(tmp)/'opaque-cell';expected,evidence=fixtures.qualified()
            cmd=expected['command'];cmd[cmd.index('--output')+1]=str(directory/'output');cmd[cmd.index('--cleanup-ack')+1]=str(directory/'cleanup.json')
            ticks=[100.0]
            def run(command,**kwargs):
                self.assertEqual(kwargs['timeout'],900)
                (directory/'output').mkdir();(directory/'output/result.json').write_bytes(evidence['result_bytes']);(directory/'cleanup.json').write_bytes(evidence['ack_bytes']);ticks[0]=401
                return subprocess.CompletedProcess(command,1)
            def validate(expected,output,raw):
                return json.loads(raw)['process']['command']==['/pinned/java','-Xmx512m','-cp','/pinned/engine.jar','battlecode.server.Main','-c=-']
            got=run_assignment(expected,directory,validate_expected=policy.validate_expected,
                validate_inputs=lambda e:True,validate_artifacts=validate,
                publish_censored=policy.publish_censored,run=run,clock=lambda:ticks[0])
            self.assertEqual(got['status'],'timeout_censored');self.assertEqual(got['next_original_index'],1)
            raw=(directory/'censored.json').read_bytes();self.assertEqual(hashlib.sha256(raw).hexdigest(),got['receipt']['record_sha256'])
            self.assertFalse(json.loads(raw)['scored']);self.assertFalse(json.loads(raw)['retry_allowed'])

if __name__=='__main__':unittest.main()
