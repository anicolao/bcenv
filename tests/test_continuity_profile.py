"""Synthetic capacity/profile coverage: no Java, real matches, or candidate material."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from services import local_trial, launch_local_trial
from services.trial_profiles import resolve_profile, trial_options, CONTINUITY_VALUES, LEGACY_DEFAULTS
from test_public_finalist import FakeAdapter, archive, games


class ProfileResolutionTests(unittest.TestCase):
    def test_default_profile_is_legacy_and_immutable(self):
        profile = resolve_profile()
        self.assertEqual(profile['profile'], 'legacy-v1')
        for key, value in LEGACY_DEFAULTS.items(): self.assertEqual(profile[key], value)
        self.assertEqual(profile['total_game_budget'], 80)
        with self.assertRaises(TypeError): profile['max_submissions'] = 24

    def test_exact_continuity_defaults_and_derived_bounds(self):
        p = resolve_profile('continuity-v1')
        for key, value in CONTINUITY_VALUES.items(): self.assertEqual(p[key], value)
        self.assertEqual((p['training_game_budget'], p['validation_game_budget'], p['total_game_budget']), (192, 16, 208))
        self.assertEqual((p['max_pending'], p['worker_count'], p['games_per_submission']), (2, 1, 8))
        self.assertEqual(dict(p), dict(resolve_profile('continuity-v1', **CONTINUITY_VALUES)))

    def test_unknown_and_wrong_type_profiles_rejected(self):
        for value in (None, True, 1, '', 'continuity', 'continuity-v2', [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError): resolve_profile(value)

    def test_every_continuity_capacity_mismatch_or_wrong_type_rejected(self):
        for key, expected in CONTINUITY_VALUES.items():
            values = (None, 0, 1, 'true') if type(expected) is bool else (None, True, False, 0, expected - 1, expected + 1, float(expected), str(expected))
            if type(expected) is bool: values += (False,)
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    resolve_profile('continuity-v1', **{key: value})
        with self.assertRaisesRegex(ValueError, 'invalid_profile_field'):
            resolve_profile('continuity-v1', resume=True)

    def test_config_requires_validation_and_exact_fixed_shape(self):
        with self.assertRaises(ValueError): trial_options({'profile': 'continuity-v1'})
        for key, expected in [('max_pending', 2), ('worker_count', 1), ('games_per_submission', 8),
                              ('training_game_budget', 192), ('validation_game_budget', 16), ('total_game_budget', 208)]:
            config = {'profile': 'continuity-v1', 'validation_games': [], key: expected}
            self.assertEqual(trial_options(config)['profile'], 'continuity-v1')
            for value in (True, float(expected), expected + 1):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    trial_options(dict(config, **{key: value}))
        with self.assertRaises(ValueError):
            trial_options({'profile': 'continuity-v1', 'validation_games': [], 'resume': True})


class ContinuityProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.base = Path(self.temp.name)
        self.trials, self.gates = [], []
        self.addCleanup(self.cleanup)
        self.opponents = [self.base / ('opponent-' + str(i)) for i in range(2)]
        self.maps = [self.base / ('map-' + str(i)) for i in range(4)]
        for path in self.opponents:
            path.mkdir(); (path / 'pin').write_text('synthetic fixture')
        for path in self.maps: path.write_text('synthetic map')
        self.training, self.validation = self.schedule(self.maps[:2]), self.schedule(self.maps[2:])
        self.worker = FakeAdapter([sys.executable], [local_trial.DevelopmentGame(**g) for g in self.training])
        self.trial = local_trial.LocalTrial(self.base / 'trial', self.worker,
            profile='continuity-v1', validation_games=self.validation)
        self.trials.append(self.trial)
        self.trial.finalist.worker.run_games = lambda root: games(self.trial.finalist.worker, root)

    def cleanup(self):
        for event in self.gates: event.set()
        for trial in self.trials: trial.close()
        self.temp.cleanup()

    def schedule(self, maps):
        return [dict(label='cell-' + str(i*4+j*2+k), opponent_build=str(o), map_path=str(m), own_slot=slot)
                for i,o in enumerate(self.opponents) for j,m in enumerate(maps) for k,slot in enumerate(('A','B'))]

    def note(self):
        return self.trial.dispatch({'operation': 'note', 'package': 'bot', 'source_zip_base64': archive(),
                                   'text': 'Synthetic prospective test record.', 'replay_references': []})

    def submit(self):
        note = self.note()
        return self.trial.dispatch({'operation': 'submit', 'package': 'bot', 'source_zip_base64': archive(),
            'note_id': note['note_id'], 'note_sha256': note['note_sha256']})['submission_id']

    def flush(self): self.trial.executor.submit(lambda: None).result(timeout=30)

    def test_constructor_defaults_and_six_hour_clock(self):
        t = self.trial
        self.assertEqual((t.max_submissions,t.max_notes,t.max_reads,t.max_replay_queries), (24,72,2000,450))
        self.assertTrue(t.require_experiment_notes)
        self.assertEqual(t.finalist.max_run_seconds, 21600)
        self.assertEqual(t.finalist.deadline_monotonic - t.finalist.started_monotonic, 21600)
        self.assertEqual(t.executor._max_workers, 1)
        state = t.finalist.state()
        self.assertEqual(state['game_budget'], 208)
        with self.assertRaises(ValueError):
            local_trial.LocalTrial(self.base / 'no-validation', self.worker, profile='continuity-v1')

    def test_actual_twenty_four_submissions_and_192_plus_16_attempts(self):
        ids = []
        for _ in range(24):
            ids.append(self.submit()); self.flush()
            self.assertEqual(self.trial.records[ids[-1]]['status'], 'complete')
        self.assertEqual(len(set(ids)), 24)
        self.assertEqual(self.trial.finalist.state()['training_games_attempted'], 192)
        with self.assertRaisesRegex(ValueError, 'submission_limit'): self.submit()
        with self.assertRaisesRegex(ValueError, 'game_limit'):
            self.trial.finalist.before_game('s_unaccepted-extra', 0)
        response = self.trial.dispatch({'operation': 'lock_finalists', 'submission_ids': ids[:2], 'provisional_choice': ids[0]})
        self.flush()
        state = self.trial.finalist.state()
        self.assertEqual(state['phase'], 'complete')
        self.assertEqual((state['training_games_attempted'],state['validation_games_attempted'],state['games_attempted']), (192,16,208))
        self.assertEqual(len(response['validation_ids']), 2)
        with self.assertRaisesRegex(ValueError, 'game_limit'):
            self.trial.finalist.before_game(response['validation_ids'][ids[0]], 0)
        with self.assertRaises(ValueError): self.submit()
        selected = self.trial.dispatch({'operation': 'select_finalist', 'submission_id': ids[0]})
        self.assertEqual(selected['final_choice'], ids[0])
        self.assertEqual(self.trial.finalist.state()['games_attempted'], 208)

    def test_notes_are_required_and_exact_72_note_boundary(self):
        with self.assertRaisesRegex(ValueError, 'note_required'):
            self.trial.dispatch({'operation': 'submit','package':'bot','source_zip_base64':archive()})
        for _ in range(72): self.note()
        self.assertEqual(len(self.trial.notes),72)
        with self.assertRaisesRegex(ValueError, 'note_limit'): self.note()

    def test_read_boundary_2000(self):
        self.trial.reads = 1999
        self.trial.dispatch({'operation': 'validation_status'})
        self.assertEqual(self.trial.reads, 2000)
        with self.assertRaisesRegex(ValueError, 'read_limit'):
            self.trial.dispatch({'operation': 'validation_status'})

    def test_replay_modes_share_450_boundary(self):
        sid = self.submit(); self.flush(); self.trial.replays = 449
        request = {'operation': 'replay','submission_id':sid,'game_index':0,
                   'query': {'round_start':1,'round_end':2,'robot_id':None,'record_offset':0,'record_limit':64}}
        with patch.object(self.worker,'replay',return_value={'records':[]}):
            self.assertEqual(self.trial.dispatch(request)['status'], 'replay')
            request['query']['mode'] = 'own_diagnostics'
            with self.assertRaisesRegex(ValueError, 'replay_limit'): self.trial.dispatch(request)
        self.assertEqual(self.trial.replays,451)

    def test_pending_remains_two_and_single_worker(self):
        entered, release = threading.Event(), threading.Event(); self.gates.append(release)
        self.trial.executor.submit(lambda: (entered.set(), release.wait(30)))
        self.assertTrue(entered.wait(5))
        self.submit(); self.submit()
        with self.assertRaisesRegex(ValueError, 'submission_limit'): self.submit()
        release.set(); self.flush()
        self.assertEqual(self.trial.finalist.state()['games_attempted'],16)

    def test_fresh_root_refuses_restart_without_touching_checkpoint(self):
        before = (self.trial.root/'phase.json').read_bytes()
        with self.assertRaises(FileExistsError):
            local_trial.LocalTrial(self.trial.root,self.worker,profile='continuity-v1',validation_games=self.validation)
        self.assertEqual((self.trial.root/'phase.json').read_bytes(),before)
        self.assertEqual(self.trial.finalist.state()['games_attempted'],0)

    def test_actual_mailbox_ceiling_2200_requests(self):
        mailbox = self.base/'mailbox'
        sequence = [0]
        unlink = Path.unlink
        def write_request(number):
            # Mix malformed filenames/JSON, ordinary invalid requests, and repeats.
            name = ('invalid-%s.json' % number if number % 4 == 0 else
                    'f' * 32 + '.json' if number % 4 == 3 else '%032x.json' % number)
            (mailbox/'requests'/name).write_text('{' if number % 4 == 1 else '{}')
        def seed(*args, **kwargs):
            write_request(0)
        def unlink_then_enqueue(path, *args, **kwargs):
            result = unlink(path, *args, **kwargs)
            if path.parent == mailbox/'requests':
                sequence[0] += 1
                write_request(sequence[0])
            return result
        # Storage metering has separate tests; exercise actual mailbox files/counts here.
        with patch.object(self.trial,'storage_checkpoint',return_value=None), patch('builtins.print', side_effect=seed), patch.object(Path,'unlink',new=unlink_then_enqueue):
            local_trial.serve_mailbox(self.trial, mailbox)
        self.assertEqual(json.loads((mailbox/'stopped.json').read_text())['processed'],2200)
        self.assertEqual(json.loads((mailbox/'stopped.json').read_text())['dispatch_attempts'],551)
        self.assertEqual(json.loads((mailbox/'stopped.json').read_text())['usage']['mailbox_attempts'],2200)
        self.assertEqual(len(list((mailbox/'responses').glob('*.json'))),1101)
        self.assertEqual(len(list((mailbox/'requests').glob('*.json'))),1)

    def test_resolved_profile_participates_in_parity(self):
        base={'command':[sys.executable],'games':self.training,'validation_games':self.validation,'max_submissions':8}
        legacy=launch_local_trial.launch_identity(base)
        extended=dict(base,profile='continuity-v1'); extended.pop('max_submissions')
        continuity=launch_local_trial.launch_identity(extended)
        self.assertNotEqual(legacy,continuity)
        self.assertEqual(continuity,launch_local_trial.launch_identity(dict(extended,**CONTINUITY_VALUES)))
        for key in CONTINUITY_VALUES:
            wrong=dict(extended,**{key:False if key=='require_experiment_notes' else CONTINUITY_VALUES[key]+1})
            with self.subTest(key=key),self.assertRaises(ValueError):launch_local_trial.launch_identity(wrong)

    def test_constructor_rejects_capacity_override_before_root_creation(self):
        for i, (key, value) in enumerate(CONTINUITY_VALUES.items()):
            wrong = False if type(value) is bool else value + 1
            root = self.base / ('bad-' + str(i))
            with self.subTest(key=key), self.assertRaises(ValueError):
                local_trial.LocalTrial(root,self.worker,profile='continuity-v1',validation_games=self.validation,**{key:wrong})
            self.assertFalse(root.exists())

    def test_legacy_constructor_defaults_remain_unchanged(self):
        trial = local_trial.LocalTrial(self.base/'legacy',self.worker)
        self.trials.append(trial)
        self.assertEqual((trial.max_submissions,trial.max_notes,trial.max_reads,trial.max_replay_queries),(12,24,2000,150))
        self.assertFalse(trial.require_experiment_notes)
        self.assertIsNone(trial.finalist)

    def test_extended_deadline_still_halts_without_new_game(self):
        with patch('services.public_finalist.time.monotonic',return_value=self.trial.finalist.deadline_monotonic):
            with self.assertRaisesRegex(ValueError,'run_halted'): self.note()
            with self.assertRaisesRegex(ValueError,'run_halted'):
                self.trial.finalist.before_game('s_never_started',0)
        self.assertEqual(self.trial.finalist.state()['games_attempted'],0)

    def test_both_configuration_entrypoints_forward_all_resolved_limits(self):
        config={'profile':'continuity-v1','command':[sys.executable],'games':self.training,'validation_games':self.validation}
        path=self.base/'config.json';path.write_text(json.dumps(config))
        expected=trial_options(config)
        for shared in (False,True):
            args=['--config',str(path),'--root',str(self.base/('root-'+str(shared))),
                  '--mailbox',str(self.base/('mailbox-'+str(shared)))]
            with self.subTest(shared=shared),patch.object(local_trial,'LocalTrial') as ctor,patch.object(local_trial,'serve_mailbox') as serve:
                if shared:
                    with patch.object(launch_local_trial,'probe',return_value={'status':'succeeded'}):
                        self.assertEqual(launch_local_trial.main(args+['--evidence',str(self.base/'evidence')]),0)
                else:
                    with patch.object(sys,'argv',['local_trial']+args):local_trial.main()
                self.assertEqual(ctor.call_args.kwargs,expected)
                serve.assert_called_once()

    def test_profile_cannot_be_supplied_through_candidate_request(self):
        note=self.note()
        request={'operation':'submit','package':'bot','source_zip_base64':archive(),
                 'note_id':note['note_id'],'note_sha256':note['note_sha256'],'profile':'legacy-v1'}
        with self.assertRaises(ValueError): self.trial.dispatch(request)
        self.assertEqual(len(self.trial.records),0)
