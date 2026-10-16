"""Query adapter validation, artifact statuses and immutable replay identity."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bc23_query', ROOT / 'seasons/2023/adapter.py')
bc23 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bc23)


class ReplayQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.replay = self.root / 'input.bc23'
        self.replay.write_bytes(b'fixture compressed bytes')
        self.engine = self.root / 'engine.jar'
        self.engine.write_bytes(b'fixture engine')
        self.args = SimpleNamespace(replay=str(self.replay), round_start=35, round_end=40,
                                    robot_id=43, limit=3, output=str(self.root / 'out'), timeout=1)

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, state='succeeded', mutation=False, wrong_hash=False):
        def execute(command, output, timeout, stderr):
            self.assertEqual(command[-5:], [str(self.replay), '35', '40', '43', '3'])
            output.write_text(json.dumps({'format': 'bcenv.bc2023.replay-query.v1', 'status': 'complete',
                'truncated': True, 'source_replay': {'sha256': 'wrong' if wrong_hash else bc23.digest(self.replay)}}))
            stderr.write_text('')
            if mutation:
                self.replay.write_bytes(b'changed externally')
            return {'status': state}
        with patch.object(bc23, 'runtime', return_value={'BCENV_JAVA': '/java', 'BCENV_ENGINE_JAR': str(self.engine)}), \
             patch.object(bc23, 'execute', side_effect=execute), \
             patch.dict('os.environ', {'BCENV_REPLAY_CLASSPATH': 'classes:engine.jar'}):
            code = bc23.replay_query(self.args)
        return code, json.loads((self.root / 'out/query-result.json').read_text())

    def test_valid_capped_query_success_with_artifact(self):
        code, result = self.invoke()
        self.assertEqual((code, result['status'], result['truncated']), (0, 'completed', True))
        self.assertEqual(self.replay.read_bytes(), b'fixture compressed bytes')

    def test_timeout_not_completed(self):
        code, result = self.invoke(state='timeout')
        self.assertEqual((code, result['status']), (1, 'timeout'))

    def test_input_mutation_rejected(self):
        code, result = self.invoke(mutation=True)
        self.assertEqual((code, result['status']), (1, 'input_mutated'))

    def test_hash_mismatch_rejected(self):
        code, result = self.invoke(wrong_hash=True)
        self.assertEqual((code, result['status']), (1, 'failed'))

    def test_invalid_parameters_reject_before_output(self):
        cases = [('round_start', -1), ('round_start', 41), ('round_end', 2147483648),
                 ('robot_id', -1), ('robot_id', 2147483648), ('limit', 0), ('limit', 10001)]
        for name, value in cases:
            with self.subTest(name=name, value=value):
                prior = getattr(self.args, name)
                setattr(self.args, name, value)
                with self.assertRaises(ValueError):
                    bc23.replay_query(self.args)
                self.assertFalse((self.root / 'out').exists())
                setattr(self.args, name, prior)

    def test_replay_must_be_regular_file(self):
        self.args.replay = str(self.root)
        with self.assertRaises(ValueError):
            bc23.replay_query(self.args)
        self.assertFalse((self.root / 'out').exists())


if __name__ == '__main__':
    unittest.main()
