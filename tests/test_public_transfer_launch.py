"""Mocked initialization only, no service or process launch."""
import tempfile
from pathlib import Path
import unittest
from unittest import mock
from services import launch_resumable_trial as launch

class ProfileLaunchTests(unittest.TestCase):
    def test_initialize_forwards_exact_profile_in_bound_config(self):
        for profile in ('resumable-v1','public-transfer-v1'):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as root:
                config={'profile':profile};paths={k:str(Path(root)/k) for k in ('service','artifacts','mailbox','control','author')}
                authority=mock.Mock(authority_id='a',config_digest='b')
                authority.inspect.return_value={'run':{'mono_deadline':100,'wall_deadline':200}}
                with mock.patch.object(launch,'read',side_effect=[config,paths]),mock.patch.object(launch,'fresh_paths'), \
                     mock.patch.object(launch,'fixed_inputs',return_value={}),mock.patch.object(launch,'implementation',return_value={}), \
                     mock.patch.object(launch,'runtime_environment',return_value={}),mock.patch.object(launch,'publish'), \
                     mock.patch.object(launch.ResumableAuthority,'create',return_value=authority) as create:
                    self.assertEqual(launch.main(['--authority',str(Path(root)/'db'),'initialize','--config','unused',
                        '--paths','unused','--author','/same/task','--identity-output','unused']),0)
                self.assertEqual(create.call_args.kwargs['profile'],profile)
                self.assertEqual(create.call_args.kwargs['config']['trial'],config)
