"""Synthetic public-phase tests: no JVM, real games, or competitor material."""
import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from runtime.artifacts import digest, identity, inventory
from services.local_trial import AdapterWorker, DevelopmentGame, LocalTrial, encoded
from services.local_trial_client import source_zip


def archive():
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('bot/RobotPlayer.java', 'package bot; public class RobotPlayer {}')
    return base64.b64encode(out.getvalue()).decode()


def verified_files(root, accepted):
    for name in ('source', 'build/source', 'build/classes', 'package'):
        (root / name).mkdir(parents=True)
    for f in accepted.files:
        p = root / 'source' / f.path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(f.data)
    shutil.copytree(root / 'source', root / 'build/source', dirs_exist_ok=True)
    p = root / 'build/classes/bot/RobotPlayer.class'
    p.parent.mkdir(parents=True)
    p.write_bytes(b'synthetic class bytes, never executed')
    sources, classes = inventory(root / 'source'), inventory(root / 'build/classes')
    artifact = {'status': 'verified', 'package': accepted.package,
                'source_files': sources, 'source_sha256': identity(sources),
                'binary_files': classes, 'binary_sha256': identity(classes),
                'engine_sha256': 'a' * 64}
    (root / 'build/artifact.json').write_text(json.dumps(artifact))
    z = root / 'package/source.zip'
    z.write_bytes(source_zip(root / 'source', accepted.package))
    package = {'source_sha256': artifact['source_sha256'], 'binary_sha256': artifact['binary_sha256'],
               'verified_engine_sha256': artifact['engine_sha256'], 'zip_sha256': digest(z)}
    (root / 'package/package.json').write_text(json.dumps(package))


def games(worker, root):
    result = []
    for i, game in enumerate(worker.games):
        worker.before_game(root.name, i)
        result.append({'index': i, 'label': game.label, 'status': 'completed',
                       'outcome': 'win', 'replay_sha256': '0' * 64})
    return {'status': 'complete', 'games': result, 'wins': len(result), 'losses': 0}


class FakeAdapter(AdapterWorker):
    def run(self, root, accepted):
        verified_files(root, accepted)
        return games(self, root)


class PublicFinalistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.gates = []
        self.trials = []
        self.addCleanup(self.cleanup)
        self.opponents = [self.base / ('opponent-' + str(i)) for i in range(2)]
        for p in self.opponents:
            p.mkdir()
            (p / 'pin').write_text('public synthetic fixture')
        self.maps = [self.base / ('map-' + str(i)) for i in range(4)]
        for p in self.maps:
            p.write_text('synthetic map data')
        self.training = self.schedule(self.maps[:2])
        self.validation = self.schedule(self.maps[2:])
        self.worker = FakeAdapter([sys.executable], [DevelopmentGame(**g) for g in self.training])
        self.trial = self.new_trial('run')
        self.validation_runs = []
        self.trial.finalist.worker.run_games = self.fake_validation

    def cleanup(self):
        for event in self.gates:
            event.set()
        for trial in self.trials:
            trial.close()
        self.temp.cleanup()

    def schedule(self, maps):
        return [{'label': 'game-' + str(i * 4 + j * 2 + k), 'opponent_build': str(o),
                 'map_path': str(m), 'own_slot': slot}
                for i, o in enumerate(self.opponents) for j, m in enumerate(maps)
                for k, slot in enumerate(('A', 'B'))]

    def new_trial(self, name, **kwargs):
        trial = LocalTrial(self.base / name, self.worker, max_submissions=8,
                           validation_games=self.validation, **kwargs)
        self.trials.append(trial)
        return trial

    def fake_validation(self, root):
        self.validation_runs.append(root.name)
        return games(self.trial.finalist.worker, root)

    def flush(self):
        self.trial.executor.submit(lambda: None).result(timeout=30)

    def submit(self):
        return self.trial.dispatch({'operation': 'submit', 'package': 'bot', 'source_zip_base64': archive()})['submission_id']

    def two(self):
        ids = []
        for _ in range(2):
            ids.append(self.submit())
            self.flush()
        return ids

    def lock(self, ids):
        return self.trial.dispatch({'operation': 'lock_finalists', 'submission_ids': ids,
                                    'provisional_choice': ids[0]})

    def gate_executor(self):
        entered, release = threading.Event(), threading.Event()
        self.gates.append(release)
        def wait():
            entered.set()
            if not release.wait(30):
                raise RuntimeError('test gate timed out')
        self.trial.executor.submit(wait)
        self.assertTrue(entered.wait(10))
        return release

    def test_success_same_source_distinct_ids_lock_and_select(self):
        ids = self.two()
        self.assertEqual(self.trial.records[ids[0]]['source_sha256'], self.trial.records[ids[1]]['source_sha256'])
        release = self.gate_executor()
        response = self.lock(ids)
        self.assertEqual(response['phase'], 'locked')
        self.assertEqual(set(response['validation_ids']), set(ids))
        self.assertEqual(self.validation_runs, [])
        for vid in response['validation_ids'].values():
            self.assertEqual(self.trial.records[vid]['status'], 'accepted')
        release.set(); self.flush()
        state = self.trial.dispatch({'operation': 'validation_status'})
        self.assertEqual(state['phase'], 'complete')
        self.assertEqual(state['validation_games_attempted'], 16)
        self.assertEqual(state['games_attempted'], 32)
        for original, vid in response['validation_ids'].items():
            result = self.trial.dispatch({'operation': 'result', 'submission_id': vid})
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(result['source_sha256'], self.trial.records[original]['source_sha256'])
            self.assertEqual(result['binary_sha256'], self.trial.finalist.identities[original]['binary_sha256'])
            self.assertEqual([g['label'] for g in result['games']], [g.label for g in self.trial.finalist.worker.games])
        selected = self.trial.dispatch({'operation': 'select_finalist', 'submission_id': ids[1]})
        self.assertEqual(selected['final_choice'], ids[1])
        with self.assertRaisesRegex(ValueError, 'selection_complete'):
            self.trial.dispatch({'operation': 'select_finalist', 'submission_id': ids[0]})
        self.assertEqual(json.loads((self.trial.root / 'phase.json').read_text())['final_choice'], ids[1])

    def test_invalid_duplicate_unknown_rejected_and_extra_field(self):
        ids = self.two()
        for pair in ([ids[0], ids[0]], [ids[0], 's_' + '0' * 32], [ids[0]], [ids[0], 7]):
            with self.assertRaisesRegex(ValueError, 'invalid_finalists'):
                self.lock(pair)
        self.trial.records[ids[1]]['status'] = 'rejected'
        with self.assertRaisesRegex(ValueError, 'invalid_finalists'):
            self.lock(ids)
        self.trial.records[ids[1]]['status'] = 'complete'
        with self.assertRaisesRegex(ValueError, 'invalid_request'):
            self.trial.dispatch({'operation': 'lock_finalists', 'submission_ids': ids,
                                'provisional_choice': ids[0], 'map_path': '/untrusted'})
        self.assertEqual(self.trial.finalist.phase, 'training')

    def test_pending_training_rejects_lock(self):
        ids = self.two()
        release = self.gate_executor()
        self.submit()
        with self.assertRaisesRegex(ValueError, 'training_pending'):
            self.lock(ids)
        release.set(); self.flush()

    def test_lock_closes_submit_renomination_and_early_selection(self):
        ids = self.two(); release = self.gate_executor(); self.lock(ids)
        with self.assertRaisesRegex(ValueError, 'training_closed'):
            self.submit()
        with self.assertRaisesRegex(ValueError, 'finalists_locked'):
            self.lock(ids)
        with self.assertRaisesRegex(ValueError, 'validation_incomplete'):
            self.trial.dispatch({'operation': 'select_finalist', 'submission_id': ids[0]})
        release.set(); self.flush()
        with self.assertRaisesRegex(ValueError, 'invalid_finalists'):
            self.trial.dispatch({'operation': 'select_finalist', 'submission_id': 's_' + 'f' * 32})

    def test_eighty_attempt_ceiling_and_no_repeated_cell(self):
        ids = []
        for _ in range(8):
            ids.append(self.submit()); self.flush()
        with self.assertRaisesRegex(ValueError, 'submission_limit'):
            self.submit()
        response = self.lock(ids[:2]); self.flush()
        state = self.trial.finalist.state()
        self.assertEqual((state['training_games_attempted'], state['validation_games_attempted'], state['games_attempted']), (64, 16, 80))
        with self.assertRaisesRegex(ValueError, 'game_limit'):
            self.trial.finalist.before_game(response['validation_ids'][ids[0]], 0)
        self.assertEqual(self.trial.finalist.state()['games_attempted'], 80)

    def test_stop_first_validation_failure_does_not_start_second(self):
        ids = self.two()
        def fail(root):
            self.validation_runs.append(root.name)
            self.trial.finalist.worker.before_game(root.name, 0)
            return {'status': 'infrastructure_failure', 'games': []}
        self.trial.finalist.worker.run_games = fail
        response = self.lock(ids); self.flush()
        self.assertEqual(len(self.validation_runs), 1)
        self.assertTrue(self.trial.halted)
        self.assertEqual(self.trial.finalist.phase, 'halted')
        self.assertEqual(self.trial.finalist.state()['validation_games_attempted'], 1)
        self.assertEqual(self.trial.records[response['validation_ids'][ids[1]]]['status'], 'not_run')

    def test_changed_source_or_classes_rejects_nomination(self):
        ids = self.two()
        source = self.trial.root / ids[0] / 'source/bot/RobotPlayer.java'
        original = source.read_bytes(); source.write_bytes(original + b' ')
        with self.assertRaisesRegex(ValueError, 'run_halted'):
            self.lock(ids)
        self.assertEqual(self.validation_runs, [])
        source.write_bytes(original)
        self.trial.halted = False
        (self.trial.root / ids[0] / 'build/classes/bot/RobotPlayer.class').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'run_halted'):
            self.lock(ids)
        self.assertEqual(self.validation_runs, [])

    def test_source_mutation_after_lock_halts_before_games(self):
        ids = self.two(); release = self.gate_executor(); response = self.lock(ids)
        (self.trial.root / ids[0] / 'source/bot/RobotPlayer.java').write_text('changed')
        release.set(); self.flush()
        self.assertTrue(self.trial.halted)
        self.assertEqual(self.validation_runs, [])
        self.assertEqual(self.trial.records[response['validation_ids'][ids[1]]]['status'], 'not_run')

    def test_changed_validation_map_halts_before_game(self):
        ids = self.two(); release = self.gate_executor(); self.lock(ids)
        self.maps[2].write_text('changed map')
        release.set(); self.flush()
        self.assertTrue(self.trial.halted)
        self.assertEqual(self.trial.finalist.state()['validation_games_attempted'], 0)

    def test_scheduling_failure_is_terminal(self):
        ids = self.two()
        with patch.object(self.trial.executor, 'submit', side_effect=RuntimeError('scheduling failed')):
            with self.assertRaisesRegex(ValueError, 'run_halted'):
                self.lock(ids)
        self.assertTrue(self.trial.halted)
        self.assertEqual(self.trial.finalist.phase, 'halted')
        with self.assertRaisesRegex(ValueError, 'run_halted'):
            self.lock(ids)
        self.assertEqual(self.validation_runs, [])

    def test_phase_persistence_failure_halts_before_scheduling(self):
        ids = self.two()
        with patch('services.public_finalist.os.replace', side_effect=OSError('disk failed')):
            with self.assertRaisesRegex(ValueError, 'run_halted'):
                self.lock(ids)
        self.assertTrue(self.trial.halted)
        self.assertEqual(self.validation_runs, [])

    def test_existing_root_never_recovers_or_replays(self):
        ids = self.two(); self.lock(ids); self.flush()
        with self.assertRaises(FileExistsError):
            LocalTrial(self.trial.root, self.worker, max_submissions=8, validation_games=self.validation)
        self.assertEqual(len(self.validation_runs), 2)

    def test_concurrent_nomination_only_one_lock(self):
        ids = self.two(); release = self.gate_executor(); barrier = threading.Barrier(3); outcomes = []
        def nominate():
            barrier.wait()
            try:
                self.lock(ids); outcomes.append('locked')
            except ValueError as e:
                outcomes.append(str(e))
        threads = [threading.Thread(target=nominate) for _ in range(2)]
        for thread in threads: thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(5); self.assertFalse(thread.is_alive())
        self.assertCountEqual(outcomes, ['locked', 'finalists_locked'])
        release.set(); self.flush()
        self.assertEqual(len(self.validation_runs), 2)

    def test_validation_replay_routes_to_validation_worker(self):
        ids = self.two(); response = self.lock(ids); self.flush()
        vid = response['validation_ids'][ids[0]]
        request = {'operation': 'replay', 'submission_id': vid, 'game_index': 0,
                   'query': {'round_start': 1, 'round_end': 2, 'robot_id': None,
                             'record_offset': 0, 'record_limit': 64}}
        with patch.object(self.trial.finalist.worker, 'replay', return_value={'records': [], 'marker': 'validation'}) as replay, patch.object(self.worker, 'replay') as training:
            result = self.trial.dispatch(request)
            self.assertEqual(result['feedback']['marker'], 'validation')
            self.assertEqual(replay.call_args.args[1], vid)
            training.assert_not_called()

    def test_receipt_chain_and_phase_order(self):
        ids = self.two(); self.lock(ids); self.flush()
        self.trial.dispatch({'operation': 'select_finalist', 'submission_id': ids[0]})
        events = [json.loads(x) for x in (self.trial.root / 'audit.jsonl').read_text().splitlines()]
        previous = '0' * 64
        for i, e in enumerate(events, 1):
            self.assertEqual(e['sequence'], i); self.assertEqual(e['previous_sha256'], previous)
            previous = e.pop('event_sha256')
            self.assertEqual(hashlib.sha256(encoded(e)).hexdigest(), previous)
        operations = [e['operation'] for e in events]
        self.assertLess(operations.index('lock_finalists'), operations.index('validation_start'))
        self.assertLess(operations.index('validation_start'), operations.index('validation_complete'))
        self.assertEqual(operations[-2:], ['validation_finished', 'select_finalist'])

    def test_validation_status_read_quota(self):
        self.trial.max_reads = 1
        self.assertEqual(self.trial.dispatch({'operation': 'validation_status'})['phase'], 'training')
        with self.assertRaisesRegex(ValueError, 'read_limit'):
            self.trial.dispatch({'operation': 'validation_status'})

    def test_validation_replay_quota(self):
        ids = self.two(); response = self.lock(ids); self.flush()
        self.trial.max_replay_queries = 0
        with self.assertRaisesRegex(ValueError, 'replay_limit'):
            self.trial.dispatch({'operation': 'replay',
                'submission_id': response['validation_ids'][ids[0]], 'game_index': 0,
                'query': {'round_start': 1, 'round_end': 2, 'robot_id': None,
                          'record_offset': 0, 'record_limit': 64}})

    def test_malformed_complete_validation_result_is_failure(self):
        ids = self.two()
        def malformed(root):
            result = self.fake_validation(root)
            result['games'][0]['status'] = 'failed'
            return result
        self.trial.finalist.worker.run_games = malformed
        response = self.lock(ids); self.flush()
        self.assertTrue(self.trial.halted)
        self.assertEqual(len(self.validation_runs), 1)
        self.assertEqual(self.trial.records[response['validation_ids'][ids[1]]]['status'], 'not_run')

    def test_success_without_charged_attempts_is_rejected(self):
        ids = self.two()
        def uncharged(root):
            self.validation_runs.append(root.name)
            return {'status': 'complete', 'wins': 8, 'losses': 0,
                    'games': [{'index': i, 'status': 'completed', 'outcome': 'win',
                               'replay_sha256': '0' * 64} for i in range(8)]}
        self.trial.finalist.worker.run_games = uncharged
        self.lock(ids); self.flush()
        self.assertTrue(self.trial.halted)
        self.assertEqual(len(self.validation_runs), 1)

    def test_changed_public_opponent_halts_nomination(self):
        ids = self.two()
        (self.opponents[0] / 'pin').write_text('changed opponent')
        with self.assertRaisesRegex(ValueError, 'run_halted'):
            self.lock(ids)
        self.assertEqual(self.validation_runs, [])

    def test_lock_receipt_failure_never_schedules(self):
        ids = self.two()
        with patch.object(self.trial, 'audit', side_effect=OSError('audit disk failure')):
            with self.assertRaisesRegex(ValueError, 'run_halted'):
                self.lock(ids)
        self.assertTrue(self.trial.halted)
        self.assertEqual(self.validation_runs, [])

    def test_validation_start_persistence_failure_is_explicit_terminal(self):
        ids = self.two(); release = self.gate_executor(); self.lock(ids)
        with patch('services.public_finalist.os.replace', side_effect=OSError('phase evidence unavailable')):
            release.set(); self.flush()
        self.assertTrue(self.trial.halted)
        self.assertEqual(self.trial.finalist.phase, 'halted')
        self.assertEqual(self.validation_runs, [])
        self.assertTrue(all(self.trial.records[vid]['status'] != 'accepted'
                            for vid in self.trial.finalist.validation_ids.values()))

    def test_deadline_bounds_and_clock_metadata(self):
        for i, value in enumerate((0, -1, 2701, True, 1.5)):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'invalid_deadline_config'):
                self.new_trial('invalid-clock-' + str(i), max_run_seconds=value)
        state = self.trial.finalist.state()
        self.assertEqual(state['max_run_seconds'], 2700)
        self.assertEqual(state['deadline_monotonic'] - state['started_monotonic'], 2700)
        self.assertEqual(state['deadline_unix'] - state['started_unix'], 2700)
        self.assertGreater(state['remaining_seconds'], 0)
        self.assertLessEqual(state['remaining_seconds'], 2700)

    def test_expired_clock_denies_mutations_but_allows_status(self):
        ids = self.two()
        with patch('services.public_finalist.time.monotonic', return_value=self.trial.finalist.deadline_monotonic):
            for request in ({'operation': 'submit'}, {'operation': 'note'},
                            {'operation': 'lock_finalists', 'submission_ids': ids, 'provisional_choice': ids[0]},
                            {'operation': 'select_finalist', 'submission_id': ids[0]}):
                with self.subTest(operation=request['operation']), self.assertRaisesRegex(ValueError, 'run_halted'):
                    self.trial.dispatch(request)
            status = self.trial.dispatch({'operation': 'validation_status'})
            self.assertEqual(status['phase'], 'halted')
            self.assertEqual(status['remaining_seconds'], 0)
        self.assertEqual(self.validation_runs, [])

    def test_expired_before_game_has_no_attempt(self):
        before = len(self.trial.finalist.attempts)
        with patch('services.public_finalist.time.monotonic', return_value=self.trial.finalist.deadline_monotonic + 1):
            with self.assertRaisesRegex(ValueError, 'run_halted'):
                self.trial.finalist.before_game('s_never_executed', 0)
        self.assertEqual(len(self.trial.finalist.attempts), before)

    def test_expiry_after_lock_never_starts_validation_game(self):
        ids = self.two(); release = self.gate_executor(); self.lock(ids)
        with patch('services.public_finalist.time.monotonic', return_value=self.trial.finalist.deadline_monotonic):
            release.set(); self.flush()
        self.assertTrue(self.trial.halted)
        self.assertEqual(self.validation_runs, [])
        self.assertEqual(self.trial.finalist.phase, 'halted')

    def test_worker_timeout_is_capped_by_remaining_service_time(self):
        self.worker.remaining_seconds = lambda: 3.25
        def completed_execute(command, *args, **kwargs):
            # Synthetic ownership/ack contract only: this mock creates no child.
            kwargs['ownership']['direct_child_wait_completed'] = True
            Path(command[command.index('--cleanup-ack') + 1]).write_text(json.dumps({
                'schema': 'bcenv.cooperative-cleanup.v1',
                'direct_child_waits_completed': True,
                'scope': 'normal adapter return only; not descendant containment'}))
            return {'exit_code': 0}
        with patch('services.local_trial.execute', side_effect=completed_execute) as execute:
            self.assertTrue(self.worker.invoke(self.base, 'synthetic', ['--help'], 2))
        self.assertEqual(execute.call_args.args[2], 8.25)
        command = execute.call_args.args[0]
        self.assertEqual(command[command.index('--deadline-monotonic') + 1], str(self.worker.deadline_monotonic))
        self.worker.remaining_seconds = lambda: 0
        with patch('services.local_trial.execute') as execute:
            with self.assertRaisesRegex(ValueError, 'run_halted'):
                self.worker.invoke(self.base, 'synthetic', ['--help'])
            execute.assert_not_called()

    def test_selection_rechecks_clock_after_identity_work(self):
        ids = self.two()
        self.lock(ids)
        self.flush()
        original = self.trial.finalist.verify_finalists
        def expire():
            original()
            self.trial.finalist.deadline_monotonic = 0
        with patch.object(self.trial.finalist, 'verify_finalists', side_effect=expire):
            with self.assertRaisesRegex(ValueError, 'run_halted'):
                self.trial.dispatch({'operation': 'select_finalist', 'submission_id': ids[0]})
        self.assertIsNone(self.trial.finalist.final_choice)

    def test_lock_rechecks_clock_after_identity_work(self):
        ids = self.two()
        original = self.trial.finalist._schedule_identity
        def expire():
            result = original()
            self.trial.finalist.deadline_monotonic = 0
            return result
        with patch.object(self.trial.finalist, '_schedule_identity', side_effect=expire):
            with self.assertRaisesRegex(ValueError, 'run_halted'):
                self.lock(ids)
        self.assertFalse(self.trial.finalist.finalists)
