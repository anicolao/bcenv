"""Trusted match-timeout configuration tests; no builds, games or subprocesses."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from services import local_trial
from services import launch_local_trial as launch


class MatchTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = local_trial.DevelopmentGame('public', '/public/build', '/public/map', 'A')
        self.config = {'command': [sys.executable], 'games': [vars(self.game)], 'timeout': 90}

    def worker(self, **kwargs):
        return local_trial.AdapterWorker([sys.executable], [self.game], **kwargs)

    def invoke(self, worker, arguments, multiplier=1, name='operation'):
        before = list(arguments)
        def completed(command, output, timeout, **kwargs):
            # A mocked adapter must model both outer wait and inner cleanup ack.
            if 'ownership' in kwargs:
                kwargs['ownership']['direct_child_wait_completed'] = True
                ack = Path(command[command.index('--cleanup-ack') + 1])
                ack.write_text(json.dumps({'schema': 'bcenv.cooperative-cleanup.v1',
                    'direct_child_waits_completed': True,
                    'scope': 'normal adapter return only; not descendant containment'}))
            return {'exit_code': 0}
        with patch('services.local_trial.execute', side_effect=completed) as execute:
            self.assertTrue(worker.invoke(self.root, name, arguments, multiplier))
        self.assertEqual(arguments, before, 'caller arguments must not be mutated')
        return execute.call_args.args

    def test_missing_match_timeout_preserves_legacy_defaults(self):
        self.assertEqual(self.worker().match_timeout, 90)
        for timeout in (1, 90, 120):
            worker = self.worker(timeout=timeout)
            self.assertEqual(worker.match_timeout, timeout)
            command, log, outer = self.invoke(worker, ['run'], 2)
            self.assertEqual(command[-2:], ['--timeout', str(timeout)])
            self.assertEqual(outer, timeout * 2 + 15)
            self.assertNotIn('--deadline-monotonic', command)

    def test_match_timeout_valid_boundaries(self):
        for value in (1, 120, 121, 300):
            with self.subTest(value=value):
                self.assertEqual(self.worker(match_timeout=value).match_timeout, value)

    def test_explicit_invalid_match_timeout_never_defaults(self):
        for value in (None, True, False, 0, -1, 301, 90.0, '90', [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.worker(match_timeout=value)

    def test_nonmatch_timeout_remains_bounded_at_120(self):
        for timeout in (None, True, False, 0, 121, 300, 90.0, '90'):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.worker(timeout=timeout, match_timeout=300)

    def test_only_run_uses_match_timeout_and_615_outer_bound(self):
        worker = self.worker(timeout=90, match_timeout=300)
        command, log, outer = self.invoke(worker, ['run', '--output', 'synthetic'], 2)
        self.assertEqual(command[-2:], ['--timeout', '300'])
        self.assertEqual(outer, 615)
        for arguments in (['build'], ['package'], ['replay', 'page'], ['replay', 'diagnostics'], ['--help'], ['replay', 'run']):
            with self.subTest(arguments=arguments):
                command, log, outer = self.invoke(worker, arguments, 2, name='run')
                self.assertEqual(command[-2:], ['--timeout', '90'])
                self.assertEqual(outer, 195)

    def test_match_timeout_does_not_change_success_failure_semantics(self):
        with patch('services.local_trial.execute', return_value={'exit_code': 2}):
            self.assertFalse(self.worker(match_timeout=300).invoke(self.root, 'run', ['run'], 2))

    def test_absolute_deadline_still_propagates_with_cleanup_grace(self):
        worker = self.worker(timeout=90, match_timeout=300)
        worker.remaining_seconds = lambda: 2.5
        worker.deadline_monotonic = 12345.25
        for operation, expected_timeout in (('run', '300'), ('build', '90')):
            command, log, outer = self.invoke(worker, [operation], 2)
            self.assertEqual(outer, 7.5)
            self.assertEqual(command[-2:], ['--timeout', expected_timeout])
            index = command.index('--deadline-monotonic')
            self.assertEqual(command[index + 1], '12345.25')

    def test_expired_deadline_refuses_any_subprocess(self):
        worker = self.worker(match_timeout=300)
        worker.deadline_monotonic = 12345.25
        for remaining in (0, -1):
            worker.remaining_seconds = lambda: remaining
            for arguments in (['run'], ['build'], ['replay', 'diagnostics']):
                with self.subTest(remaining=remaining, arguments=arguments), patch('services.local_trial.execute') as execute:
                    with self.assertRaisesRegex(ValueError, 'run_halted'):
                        worker.invoke(self.root, 'operation', arguments, 2)
                    execute.assert_not_called()

    def test_finalist_worker_inherits_resolved_match_timeout(self):
        opponents = [self.root / 'opponent-a', self.root / 'opponent-b']
        maps = [self.root / ('map-' + str(i)) for i in range(4)]
        for opponent in opponents:
            opponent.mkdir(); (opponent / 'pin').write_text('synthetic public fixture')
        for path in maps:
            path.write_text('synthetic map fixture')
        def schedule(selected):
            return [dict(label='cell-' + str(i*4+j*2+k), opponent_build=str(opponent), map_path=str(path), own_slot=slot)
                    for i, opponent in enumerate(opponents) for j, path in enumerate(selected)
                    for k, slot in enumerate(('A', 'B'))]
        for value in (90, 300):
            worker = local_trial.AdapterWorker([sys.executable],
                [local_trial.DevelopmentGame(**g) for g in schedule(maps[:2])], timeout=90, match_timeout=value)
            trial = local_trial.LocalTrial(self.root / ('trial-' + str(value)), worker,
                max_submissions=8, validation_games=schedule(maps[2:]))
            self.addCleanup(trial.close)
            self.assertEqual(trial.worker.match_timeout, value)
            self.assertEqual(trial.finalist.worker.match_timeout, value)
            self.assertEqual(trial.finalist.worker.timeout, 90)
            self.assertEqual(trial.worker.deadline_monotonic, trial.finalist.worker.deadline_monotonic)
            for phase_worker in (trial.worker, trial.finalist.worker):
                phase_worker.remaining_seconds = lambda: 5
                command, log, outer = self.invoke(phase_worker, ['run'], 2)
                self.assertEqual(command[-2:], ['--timeout', str(value)])
                self.assertEqual(outer, 10)

    def test_launch_parity_binds_resolved_match_timeout(self):
        default = launch.launch_identity(self.config)
        explicit_default = launch.launch_identity(dict(self.config, match_timeout=90))
        self.assertEqual(default, explicit_default)
        self.assertEqual(default['match_timeout'], 90)
        extended = launch.launch_identity(dict(self.config, match_timeout=300))
        self.assertEqual(extended['match_timeout'], 300)
        self.assertNotEqual(default, extended)
        self.assertEqual({k: v for k, v in default.items() if k != 'match_timeout'},
                         {k: v for k, v in extended.items() if k != 'match_timeout'})
        legacy_without_timeout = dict(self.config); legacy_without_timeout.pop('timeout')
        self.assertEqual(default, launch.launch_identity(legacy_without_timeout))

    def entry(self, config, shared, tag):
        path = self.root / (tag + '.json'); path.write_text(json.dumps(config))
        args = ['--config', str(path), '--root', str(self.root / (tag + '-root')),
                '--mailbox', str(self.root / (tag + '-mailbox'))]
        with patch.object(local_trial, 'LocalTrial') as trial, patch.object(local_trial, 'serve_mailbox') as serve:
            if shared:
                with patch.object(launch, 'probe', return_value={'status': 'succeeded'}):
                    self.assertEqual(launch.main(args + ['--evidence', str(self.root / (tag + '-evidence'))]), 0)
            else:
                with patch.object(sys, 'argv', ['local_trial'] + args):
                    local_trial.main()
            serve.assert_called_once()
            return trial.call_args.args[1]

    def test_both_config_entrypoints_forward_match_timeout(self):
        for shared in (False, True):
            for i, config in enumerate((self.config, dict(self.config, match_timeout=300))):
                with self.subTest(shared=shared, config=config):
                    worker = self.entry(config, shared, 'valid-' + str(shared) + '-' + str(i))
                    self.assertEqual(worker.match_timeout, 90 if i == 0 else 300)
                    self.assertEqual(worker.timeout, 90)

    def test_both_config_entrypoints_reject_invalid_explicit_values_before_readiness(self):
        for shared in (False, True):
            for i, value in enumerate((None, True, 0, 301, 90.0, '90')):
                tag = 'invalid-' + str(shared) + '-' + str(i)
                with self.subTest(shared=shared, value=value), self.assertRaises(ValueError):
                    self.entry(dict(self.config, match_timeout=value), shared, tag)
                self.assertFalse((self.root / (tag + '-root')).exists())
                self.assertFalse((self.root / (tag + '-mailbox')).exists())
