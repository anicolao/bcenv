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
        return code, json.loads((Path(self.args.output) / 'query-result.json').read_text())

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



class ReplayPageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.replay = self.root / 'input.bc23'
        self.replay.write_bytes(b'fixture compressed bytes')
        self.engine = self.root / 'engine.jar'
        self.engine.write_bytes(b'fixture engine')
        self.args = SimpleNamespace(replay=str(self.replay), round_start=35, round_end=40,
            robot_id=43, record_offset=2, limit=3, output=str(self.root / 'out'), timeout=1)

    def invoke(self, change=None, state='succeeded', mutation=False):
        def execute(command, output, timeout, stderr):
            self.assertEqual(command[-7:], ['--page', str(self.replay), '35', '40', '43', '3', '2'])
            value = {'format': 'bcenv.bc2023.replay-query.v2', 'status': 'complete',
                'source_replay': {'sha256': bc23.digest(self.replay)},
                'source_stream_complete': True, 'extracted_fields_valid': True,
                'all_schema_fields_validated': False, 'page_records_complete': True,
                'matching_record_count': 26, 'returned_record_count': 3,
                'page_end_offset': 5, 'next_record_offset': 5,
                'truncated': True, 'selected_records_complete': False, 'records': [{}, {}, {}],
                'query': {'round_start': 35, 'round_end': 40, 'robot_id': 43,
                          'record_offset': 2, 'record_limit': 3}}
            if change:
                change(value)
            output.write_text(json.dumps(value))
            stderr.write_text('')
            if mutation:
                self.replay.write_bytes(b'changed externally')
            return {'status': state}
        with patch.object(bc23, 'runtime', return_value={'BCENV_JAVA': '/java', 'BCENV_ENGINE_JAR': str(self.engine)}), \
             patch.object(bc23, 'execute', side_effect=execute), \
             patch.dict('os.environ', {'BCENV_REPLAY_CLASSPATH': 'classes:engine.jar'}):
            code = bc23.replay_page(self.args)
        return code, json.loads((Path(self.args.output) / 'query-result.json').read_text())

    def test_explicit_page_command_and_receipt(self):
        code, result = self.invoke()
        self.assertEqual((code, result['status'], result['kind']), (0, 'completed', 'replay-page'))
        self.assertTrue(result['truncated'])
        self.assertEqual(self.replay.read_bytes(), b'fixture compressed bytes')

    def test_page_rejects_wrong_format_or_position(self):
        cases = [lambda q: q.update(format='bcenv.bc2023.replay-query.v1'),
                 lambda q: q['query'].update(record_offset=0),
                 lambda q: q['query'].update(record_limit=4),
                 lambda q: q.update(next_record_offset=None),
                 lambda q: q.update(page_end_offset=True),
                 lambda q: q.update(source_stream_complete=False),
                 lambda q: q.update(page_records_complete=False),
                 lambda q: q.update(records=[])]
        for index, change in enumerate(cases):
            self.args.output = str(self.root / ('out' + str(index)))
            code, result = self.invoke(change=change)
            self.assertEqual((code, result['status']), (1, 'failed'))

    def test_page_parameters_reject_before_allocating_output(self):
        for key, value in (('record_offset', -1), ('record_offset', 5000001),
                           ('record_offset', True), ('round_end', 291),
                           ('round_start', False), ('limit', True), ('robot_id', False)):
            with self.subTest(key=key, value=value):
                prior = getattr(self.args, key)
                setattr(self.args, key, value)
                with self.assertRaises(ValueError):
                    bc23.replay_page(self.args)
                self.assertFalse((self.root / 'out').exists())
                setattr(self.args, key, prior)

    def test_page_timeout_and_mutation_not_completed(self):
        code, result = self.invoke(state='timeout')
        self.assertEqual((code, result['status']), (1, 'timeout'))
        self.args.output = str(self.root / 'mutated')
        code, result = self.invoke(mutation=True)
        self.assertEqual((code, result['status']), (1, 'input_mutated'))

    def test_page_cli_requires_explicit_offset(self):
        with patch('sys.argv', ['adapter.py', 'replay', 'page', '--replay', str(self.replay),
                               '--round-start', '35', '--round-end', '40', '--output', str(self.root / 'out')]):
            with self.assertRaises(SystemExit) as caught:
                bc23.main()
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse((self.root / 'out').exists())


if __name__ == '__main__':
    unittest.main()
