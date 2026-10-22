import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from services import launch_local_trial as launch


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = {'command': [sys.executable, '-c', 'print("help")'],
                       'games': [{'label': 'generic', 'opponent_build': '/public/build',
                                  'map_path': '/public/map', 'own_slot': 'A'}],
                       'require_experiment_notes': True}

    def args(self, tag):
        config = self.root / (tag + '.json')
        config.write_text(json.dumps(self.config))
        return ['--config', str(config), '--root', str(self.root / (tag + '-service')),
                '--mailbox', str(self.root / (tag + '-mailbox')),
                '--evidence', str(self.root / tag)]

    def test_success_check_never_starts_service(self):
        self.assertEqual(launch.main(self.args('a') + ['--check-only']), 0)
        self.assertFalse((self.root / 'a-service').exists())
        self.assertFalse((self.root / 'a-mailbox').exists())

    def test_failed_probe_rejects_before_readiness(self):
        self.config['command'] = [sys.executable, '-c', 'raise SystemExit(1)']
        self.assertEqual(launch.main(self.args('a')), 1)
        self.assertFalse((self.root / 'a-service').exists())
        self.assertFalse((self.root / 'a-mailbox').exists())
        self.assertEqual(json.loads((self.root / 'a/launch.json').read_text())['status'], 'probe_failed')

    def test_same_context_and_config_snapshot(self):
        with patch.object(launch.local_trial, 'LocalTrial') as trial, patch.object(launch.local_trial, 'serve_mailbox') as serve:
            self.assertEqual(launch.main(self.args('a')), 0)
            worker = trial.call_args.args[1]
            self.assertEqual(worker.command, self.config['command'])
            self.assertEqual(serve.call_args.args[0], trial.return_value)

    def test_parity_matches_with_quota_change(self):
        self.assertEqual(launch.main(self.args('a') + ['--check-only']), 0)
        self.config['max_submissions'] = 8
        self.assertEqual(launch.main(self.args('b') + ['--check-only', '--expect-parity', str(self.root / 'a/launch.json')]), 0)

    def test_command_change_fails_parity_before_probe(self):
        self.assertEqual(launch.main(self.args('a') + ['--check-only']), 0)
        self.config['command'][-1] = 'print("different")'
        with patch.object(launch, 'probe') as probe:
            self.assertEqual(launch.main(self.args('b') + ['--expect-parity', str(self.root / 'a/launch.json')]), 1)
            probe.assert_not_called()
        self.assertFalse((self.root / 'b-mailbox').exists())

    def test_relative_executable_rejected(self):
        self.config['command'][0] = 'python3'
        with self.assertRaises(ValueError):
            launch.main(self.args('a'))
        self.assertFalse((self.root / 'a-mailbox').exists())

    def test_probe_output_cap(self):
        result = launch.probe([sys.executable, '-c', 'print("x" * 100000)'], self.root / 'log')
        self.assertEqual(result['status'], 'output_limit')
        self.assertEqual((self.root / 'log').stat().st_size, launch.MAX_LOG)

    def test_probe_timeout(self):
        result = launch.probe([sys.executable, '-c', 'import time; time.sleep(5)'], self.root / 'log', timeout=.1)
        self.assertEqual(result['status'], 'timeout')

    def test_probe_launch_failure(self):
        result = launch.probe(['/nonexistent/bcenv'], self.root / 'log')
        self.assertEqual(result['status'], 'failed')
        self.assertIn('launch_error', result)

    def test_deadline_duration_change_fails_parity(self):
        self.assertEqual(launch.main(self.args('a') + ['--check-only']), 0)
        self.config['max_run_seconds'] = 1200
        with patch.object(launch, 'probe') as probe:
            self.assertEqual(launch.main(self.args('b') + ['--expect-parity', str(self.root / 'a/launch.json')]), 1)
            probe.assert_not_called()

    def test_public_validation_schedule_change_fails_parity(self):
        self.assertEqual(launch.main(self.args('a') + ['--check-only']), 0)
        self.config['validation_games'] = self.config['games']
        with patch.object(launch, 'probe') as probe:
            self.assertEqual(launch.main(self.args('b') + ['--expect-parity', str(self.root / 'a/launch.json')]), 1)
            probe.assert_not_called()
