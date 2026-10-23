"""Real launcher probe algorithm with fake Popen/selector/clock; no processes/signals."""
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from services import launch_local_trial as launch


class Selector:
    def __init__(self, *, pending=False, step=None):
        self.pending=pending;self.step=step;self.selections=0
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def register(self,*args):pass
    def get_map(self):return {'fake':True} if self.pending else {}
    def select(self,timeout):
        self.selections+=1
        if self.step:self.step()
        return []


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.output=Path(self.temp.name)/'probe.log'
        self.now=100.
        self.child=mock.Mock(pid=12345,returncode=0)
        self.child.wait.return_value=0
        self.selector=Selector()
        self.patches=[mock.patch.object(launch.subprocess,'Popen',return_value=self.child),
                      mock.patch.object(launch.os,'killpg'),
                      mock.patch.object(launch.selectors,'DefaultSelector',side_effect=lambda:self.selector),
                      mock.patch.object(launch.time,'monotonic',side_effect=lambda:self.now),
                      mock.patch.object(launch,'cancelled',return_value=False),
                      mock.patch.object(launch.os,'read',side_effect=AssertionError('no real descriptor read'))]
        self.popen,self.kill,self.select,self.clock,self.cancel,self.read=[p.start() for p in self.patches]
        for p in self.patches:self.addCleanup(p.stop)

    def test_cancelled_before_popen_does_not_launch_or_signal(self):
        self.cancel.return_value=True
        result=launch.probe(['/inert/bcenv'],self.output,cancel_fd=321)
        self.assertEqual(result['status'],'cancelled');self.assertTrue(result['not_started'])
        self.popen.assert_not_called();self.kill.assert_not_called();self.child.wait.assert_not_called()
        self.assertEqual(self.output.read_bytes(),b'')

    def test_original_deadline_before_popen_does_not_launch(self):
        result=launch.probe(['/inert/bcenv'],self.output,deadline_monotonic=99)
        self.assertEqual(result['status'],'timeout');self.assertTrue(result['not_started'])
        self.popen.assert_not_called();self.kill.assert_not_called()

    def test_absolute_deadline_checked_during_selector_poll(self):
        self.selector=Selector(pending=True,step=lambda:setattr(self,'now',105.))
        result=launch.probe(['/inert/bcenv'],self.output,timeout=30,deadline_monotonic=101,cancel_fd=321)
        self.assertEqual(result['status'],'timeout');self.assertEqual(self.selector.selections,1)
        self.kill.assert_called_once_with(12345,signal.SIGKILL)
        self.assertEqual(self.child.wait.call_args_list,[mock.call()])
        self.child.stdout.close.assert_called_once()

    def test_cancel_observed_during_poll_reaps_owned_fake_child(self):
        self.selector=Selector(pending=True,step=lambda:setattr(self.cancel,'return_value',True))
        result=launch.probe(['/inert/bcenv'],self.output,cancel_fd=321)
        self.assertEqual(result['status'],'cancelled')
        self.kill.assert_called_once_with(12345,signal.SIGKILL)
        self.child.wait.assert_called_once_with();self.child.stdout.close.assert_called_once()

    def test_remaining_absolute_cutoff_clips_post_eof_wait(self):
        result=launch.probe(['/inert/bcenv'],self.output,timeout=30,deadline_monotonic=105)
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(self.child.wait.call_args_list,[mock.call(timeout=5.),mock.call()])

    def test_legacy_default_invocation_retains_original_popen_and_wait(self):
        result=launch.probe(['/inert/bcenv'],self.output)
        self.assertEqual(result['status'],'succeeded')
        self.popen.assert_called_once_with(['/inert/bcenv','--help'],stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT,start_new_session=True)
        self.assertEqual(self.child.wait.call_args_list,[mock.call(timeout=30.),mock.call()])
        self.assertTrue(all(call.args==(None,) for call in self.cancel.call_args_list))

    def test_cancel_after_eof_before_wait(self):
        self.cancel.side_effect=[False,True]
        result=launch.probe(['/inert/bcenv'],self.output,cancel_fd=321)
        self.assertEqual(result['status'],'cancelled')
        self.child.wait.assert_called_once_with()

    def test_eof_wait_timeout_then_cancel_is_observed_before_next_wait(self):
        def wait(timeout=None):
            if timeout is not None:
                self.cancel.return_value=True
                raise subprocess.TimeoutExpired('/inert/bcenv',timeout)
            return 0
        self.child.wait.side_effect=wait
        result=launch.probe(['/inert/bcenv'],self.output,cancel_fd=321)
        self.assertEqual(result['status'],'cancelled')
        self.assertEqual(self.child.wait.call_args_list,[mock.call(timeout=.05),mock.call()])
        self.kill.assert_called_once_with(12345,signal.SIGKILL)
        self.child.stdout.close.assert_called_once()

    def test_selector_error_still_reaps_and_closes(self):
        def fail():raise OSError('selector unavailable')
        self.selector=Selector(pending=True,step=fail)
        with self.assertRaisesRegex(OSError,'selector unavailable'):
            launch.probe(['/inert/bcenv'],self.output,cancel_fd=321)
        self.kill.assert_called_once();self.child.wait.assert_called_once_with()
        self.child.stdout.close.assert_called_once()


if __name__=='__main__':unittest.main()
