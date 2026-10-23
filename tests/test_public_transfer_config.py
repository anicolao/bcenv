"""Real fixed_inputs configuration validation; all inputs inert temporary bytes."""
import copy
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from services import public_transfer_maps as maps
from services import resumable_trial as trial


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.command=self.root/'bcenv';self.command.write_bytes(b'inert command, never run')
        self.opponents=[]
        for i in range(2):
            path=self.root/('opponent'+str(i));path.mkdir();(path/'artifact.json').write_bytes(b'inert public build metadata')
            self.opponents.append(str(path))
        self.mapsets=[]
        for group in range(3):
            entries=[]
            for index in range(2):
                p=self.root/('map%d%d.map23'%(group,index));p.write_bytes(b'inert map bytes')
                entries.append(str(p))
            self.mapsets.append(entries)
        self.proof=self.root/'proof.json';self.proof.write_bytes(b'inert proof bytes')
        self.config={'profile':'public-transfer-v1','command':[str(self.command)],'timeout':120,'match_timeout':300}
        for key,paths in zip(('games','validation_games','transfer_games'),self.mapsets):
            self.config[key]=self.schedule(paths)
        self.refresh_hash()
        self.config['transfer_selection']={'path':str(self.proof),'sha256':hashlib.sha256(self.proof.read_bytes()).hexdigest()}
        p=mock.patch.object(maps,'validate_selection',return_value=tuple(self.mapsets[2]));self.validate=p.start();self.addCleanup(p.stop)
        p=mock.patch.object(subprocess,'Popen',side_effect=AssertionError('no process fixture'));p.start();self.addCleanup(p.stop)

    def schedule(self,paths):
        return [dict(label='o%d-m%d-%s'%(i,j,side),opponent_build=opponent,map_path=path,own_slot=side)
                for i,opponent in enumerate(self.opponents) for j,path in enumerate(paths) for side in ('A','B')]

    def refresh_hash(self):
        self.config['transfer_schedule_sha256']=hashlib.sha256(trial.canonical(self.config['transfer_games'])).hexdigest()

    def test_new_profile_validates_full_proof_but_binds_only_its_hash(self):
        value=trial.fixed_inputs(self.config)
        self.validate.assert_called_once_with(str(self.proof),self.config['transfer_selection']['sha256'],
               {'training':sorted(self.mapsets[0]),'validation':sorted(self.mapsets[1])})
        expected=[self.command,self.proof,*[Path(p) for group in self.mapsets for p in group],
                  *[Path(p)/'artifact.json' for p in self.opponents]]
        self.assertEqual(value,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in expected})

    def test_legacy_resumable_profile_unchanged_no_proof_invocation(self):
        config={k:v for k,v in self.config.items() if not k.startswith('transfer_')};config['profile']='resumable-v1'
        value=trial.fixed_inputs(config)
        self.validate.assert_not_called();self.assertNotIn(str(self.proof),value)
        self.assertTrue(all(p not in value for p in self.mapsets[2]))

    def test_legacy_rejects_transfer_keys(self):
        self.config['profile']='resumable-v1'
        with self.assertRaisesRegex(ValueError,'invalid_resumable_config'):trial.fixed_inputs(self.config)

    def test_missing_or_extra_keys_rejected(self):
        for key in ('transfer_games','transfer_selection','transfer_schedule_sha256'):
            config=copy.deepcopy(self.config);del config[key]
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'invalid_resumable_config'):trial.fixed_inputs(config)
        config=copy.deepcopy(self.config);config['private_filter']=[]
        with self.assertRaisesRegex(ValueError,'invalid_resumable_config'):trial.fixed_inputs(config)

    def test_exact_schedule_digest_required(self):
        self.config['transfer_games'][0]['label']='changed'
        with self.assertRaisesRegex(ValueError,'invalid_transfer_schedule'):trial.fixed_inputs(self.config)

    def test_transfer_must_not_overlap_training_or_validation(self):
        for group in (0,1):
            self.config['transfer_games']=self.schedule(self.mapsets[group]);self.refresh_hash()
            with self.subTest(group=group),self.assertRaisesRegex(ValueError,'invalid_transfer_schedule'):trial.fixed_inputs(self.config)

    def test_transfer_proof_selected_map_set_must_match_schedule(self):
        self.validate.return_value=tuple(self.mapsets[0])
        with self.assertRaisesRegex(ValueError,'transfer_selection_schedule_mismatch'):trial.fixed_inputs(self.config)

    def test_invalid_selection_shape_or_path_rejected(self):
        for selection in ({},{'path':'relative','sha256':'a'*64},{'path':str(self.proof),'sha256':'a'*64,'extra':True}):
            self.config['transfer_selection']=selection
            with self.subTest(selection=selection),self.assertRaisesRegex(ValueError,'invalid_transfer_selection'):trial.fixed_inputs(self.config)

    def test_proof_error_propagates_no_fallback(self):
        self.validate.side_effect=ValueError('official_corpus_mismatch')
        with self.assertRaisesRegex(ValueError,'official_corpus_mismatch'):trial.fixed_inputs(self.config)

    def test_changed_opponent_set_rejected(self):
        new=self.root/'other';new.mkdir();(new/'artifact.json').write_bytes(b'inert')
        for game in self.config['transfer_games']:
            if game['opponent_build']==self.opponents[0]:game['opponent_build']=str(new)
        self.refresh_hash()
        with self.assertRaisesRegex(ValueError,'invalid_transfer_schedule'):trial.fixed_inputs(self.config)

    def test_duplicate_or_missing_cells_rejected(self):
        for games in (self.config['transfer_games'][:-1],self.config['transfer_games'][:-1]+[self.config['transfer_games'][0]]):
            self.config['transfer_games']=games;self.refresh_hash()
            with self.assertRaisesRegex(ValueError,'invalid_schedule'):trial.fixed_inputs(self.config)

    def test_new_profile_all_schedules_require_exact_ab_cartesian_cells(self):
        for key in ('games','validation_games','transfer_games'):
            config=copy.deepcopy(self.config)
            for game in config[key]:game['own_slot']={'A':'C','B':'D'}[game['own_slot']]
            config['transfer_schedule_sha256']=hashlib.sha256(trial.canonical(config['transfer_games'])).hexdigest()
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'invalid_game|invalid_schedule'):trial.fixed_inputs(config)


if __name__=='__main__':unittest.main()
