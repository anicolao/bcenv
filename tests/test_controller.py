"""Fixed-tool controller protocol tests, with inert transport/sandbox doubles."""
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.controller import (ControllerLimits, ExecReply, ExecRequest,
    FrozenContext, InvalidCall, ReadRequest, ResultRequest, ScrimmageReply,
    SubmitRequest, TOOL_NAMES, ToolCall, TurnReply, WriteRequest,
    launch_real, run_synthetic, tool_schemas)

ARTIFACT = 'a_' + 'b' * 32
SUBMISSION = 's_' + 'c' * 32


def call(name, arguments, identifier='call1'):
    return ToolCall(identifier, name, json.dumps(arguments))


def executable(argv=None, cwd='scratch', timeout=5):
    return {'argv': argv or ['python3', 'candidate_test.py'], 'cwd': cwd, 'timeout_seconds': timeout}


class SyntheticTransport:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def next_turn(self, request):
        self.requests.append(request)
        return self.replies.pop(0)


class SyntheticSandbox:
    def __init__(self):
        self.files = {'docs/rules.txt': b'synthetic rules'}
        self.requests = []
        self.exec_reply = ExecReply('succeeded', b'synthetic test passed', artifact_id=ARTIFACT)

    def read(self, request):
        self.requests.append(request)
        return self.files[request.path][request.offset:request.offset + request.limit]

    def write(self, request):
        self.requests.append(request)
        self.files[request.path] = request.content

    def exec(self, request):
        self.requests.append(request)
        return self.exec_reply


class SyntheticScrimmage:
    def __init__(self):
        self.requests = []
        self.result_reply = ScrimmageReply(SUBMISSION, {'status': 'complete',
            'validated_complete': True, 'summary': {'games': 1, 'wins': 1, 'draws': 0, 'losses': 0}})
        self.replay_reply = None

    def submit(self, request):
        self.requests.append(request)
        return ScrimmageReply(SUBMISSION, {'status': 'accepted'})

    def result(self, request):
        self.requests.append(request)
        return self.result_reply

    def replay(self, request):
        self.requests.append(request)
        return self.replay_reply


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.context = FrozenContext(b'system\r\nexact bytes\n', b'user task\n  preserve spacing\n')
        self.sandbox = SyntheticSandbox()
        self.scrimmage = SyntheticScrimmage()
        self.clock_time = 10

    def run_calls(self, calls, **kwargs):
        replies = [TurnReply(tuple(calls)), TurnReply(final_text='finished synthetic probe')]
        self.transport = SyntheticTransport(replies)
        return run_synthetic(self.context, self.transport, self.sandbox, self.scrimmage, **kwargs)

    def results(self, report):
        return [item['result'] for item in map(json.loads, report.transcript) if 'result' in item]

    def bootstrap(self):
        return [call('workspace_exec', executable(), 'package'),
                call('scrimmage_submit', {'artifact_id': ARTIFACT, 'package': 'bot'}, 'submit')]

    def test_all_operations_are_mediated_by_typed_adapters(self):
        first = [call('workspace_write', {'path': 'src/bot/RobotPlayer.java', 'content': 'package bot;'}, 'write'),
                 call('workspace_read', {'path': 'src/bot/RobotPlayer.java', 'offset': 0, 'limit': 100}, 'read'),
                 call('workspace_exec', executable(['python3', 'candidate_test.py']), 'exec')]
        second = [call('scrimmage_submit', {'artifact_id': ARTIFACT, 'package': 'bot'}, 'submit'),
                  call('scrimmage_result', {'submission_id': SUBMISSION}, 'result'),
                  call('scrimmage_replay', {'submission_id': SUBMISSION}, 'replay')]
        transport = SyntheticTransport([TurnReply(tuple(first)), TurnReply(tuple(second)), TurnReply(final_text='done')])
        report = run_synthetic(self.context, transport, self.sandbox, self.scrimmage)
        self.assertEqual((report.status, report.turns, report.tool_calls), ('complete', 3, 6))
        self.assertEqual([type(item) for item in self.sandbox.requests], [WriteRequest, ReadRequest, ExecRequest])
        self.assertEqual([type(item) for item in self.scrimmage.requests], [SubmitRequest, ResultRequest, ResultRequest])
        self.assertEqual(self.results(report)[-1]['reason'], 'replay_withheld')
        self.assertEqual(self.sandbox.requests[-1].argv, ('python3', 'candidate_test.py'))

    def test_exact_frozen_initial_context_and_fixed_tools_on_every_turn(self):
        report = self.run_calls([call('workspace_read', {'path': 'docs/rules.txt', 'offset': 0, 'limit': 100})])
        for request in self.transport.requests:
            self.assertIs(request.context, self.context)
            self.assertEqual(request.context.system, b'system\r\nexact bytes\n')
            self.assertEqual(request.context.task, b'user task\n  preserve spacing\n')
            self.assertEqual(request.tool_names, TOOL_NAMES)
            self.assertEqual(request.tool_schemas, tool_schemas())
            self.assertIsInstance(request.transcript, tuple)
        self.assertEqual(report.initial_sha256, self.context.sha256)
        self.assertNotEqual(FrozenContext(b'a', b'bc').sha256, FrozenContext(b'ab', b'c').sha256)
        with self.assertRaises(FrozenInstanceError):
            self.context.task = b'altered'
        with self.assertRaises(ValueError):
            FrozenContext('not bytes', b'task')

    def test_registry_is_immutable_closed_and_provider_neutral(self):
        schemas = tool_schemas()
        self.assertIsInstance(schemas, tuple)
        self.assertEqual(tuple(json.loads(item)['name'] for item in schemas), TOOL_NAMES)
        for schema in map(json.loads, schemas):
            self.assertFalse(schema['parameters']['additionalProperties'])
            self.assertEqual(set(schema['parameters']['required']), set(schema['parameters']['properties']))
            self.assertNotIn('environment', schema['parameters']['properties'])
            self.assertNotIn('mounts', schema['parameters']['properties'])

    def test_unknown_native_network_or_host_tools_never_reach_adapters(self):
        for name in ('exec_command', 'shell', 'browser', 'host.exec', 'network.fetch',
                     'filesystem.read', 'mcp__private__read', 'workspace.read'):
            with self.subTest(name=name):
                report = self.run_calls([call(name, {'command': 'cat /private/heldout'})])
                self.assertEqual(self.results(report)[0]['reason'], 'unknown_tool')
        self.assertEqual(self.sandbox.requests, [])
        self.assertEqual(self.scrimmage.requests, [])

    def test_arbitrary_test_and_shell_argv_are_only_inert_sandbox_inputs(self):
        argv = ['/bin/sh', '-c', 'python3 candidate_test.py && echo never-run-on-host']
        with patch('subprocess.Popen', side_effect=AssertionError('host execution forbidden')):
            report = self.run_calls([call('workspace_exec', executable(argv))])
        self.assertEqual(report.status, 'complete')
        request = self.sandbox.requests[0]
        self.assertEqual(request.argv, tuple(argv))
        self.assertEqual(request.cwd, 'scratch')
        self.assertEqual(request.wall_seconds, 5)
        self.assertEqual(request.memory_bytes, ControllerLimits().exec_memory_bytes)
        self.assertFalse(hasattr(request, 'environment'))
        self.assertFalse(hasattr(request, 'mounts'))

    def test_exec_has_no_model_selected_host_launcher_or_sandbox_configuration(self):
        for key in ('environment', 'env', 'mounts', 'host_launcher', 'sandbox', 'network',
                    'inherit_environment', 'security_flags', 'memory_bytes'):
            arguments = executable()
            arguments[key] = '/private/heldout'
            report = self.run_calls([call('workspace_exec', arguments)])
            self.assertEqual(self.results(report)[0]['reason'], 'invalid_arguments')
        self.assertEqual(self.sandbox.requests, [])

    def test_extra_fields_are_rejected_for_every_tool(self):
        values = [('workspace_read', {'path': 'docs/rules.txt', 'offset': 0, 'limit': 10}),
                  ('workspace_write', {'path': 'src/a.java', 'content': 'x'}),
                  ('workspace_exec', executable()),
                  ('scrimmage_submit', {'artifact_id': ARTIFACT, 'package': 'bot'}),
                  ('scrimmage_result', {'submission_id': SUBMISSION}),
                  ('scrimmage_replay', {'submission_id': SUBMISSION})]
        for name, arguments in values:
            arguments['extra'] = True
            report = self.run_calls([call(name, arguments)])
            self.assertEqual(self.results(report)[0]['reason'], 'invalid_arguments')
        self.assertEqual(self.sandbox.requests + self.scrimmage.requests, [])

    def test_workspace_path_escape_and_protected_writes_are_denied(self):
        for path in ('/etc/passwd', '../secret', 'src/../secret', 'src//A.java',
                     'src/./A.java', 'src\\A.java', 'C:/A.java', 'src/.hidden',
                     'src/CON.java', 'src/trailing.', 'src/ü.java', 'src/A\x00.java'):
            report = self.run_calls([call('workspace_read', {'path': path, 'offset': 0, 'limit': 1})])
            self.assertEqual(self.results(report)[0]['reason'], 'invalid_arguments')
        report = self.run_calls([call('workspace_write', {'path': 'docs/rules.txt', 'content': 'overwrite'})])
        self.assertEqual(self.results(report)[0]['reason'], 'invalid_arguments')
        self.assertEqual(self.sandbox.requests, [])

    def test_exec_cwd_allows_fresh_roots_and_descendants_but_file_tools_require_leaf(self):
        for cwd in ('.', 'src', 'scratch', 'docs', 'artifacts', 'self_play', 'scratch/tests'):
            report = self.run_calls([call('workspace_exec', executable(cwd=cwd))])
            self.assertEqual(self.results(report)[0]['status'], 'succeeded')
            self.assertEqual(self.sandbox.requests[-1].cwd, cwd)
        for path in ('.', 'src', 'scratch'):
            report = self.run_calls([call('workspace_read', {'path': path, 'offset': 0, 'limit': 10})])
            self.assertEqual(self.results(report)[0]['reason'], 'invalid_arguments')

    def test_exec_argument_cwd_and_time_limits(self):
        cases = [executable(cwd='/private'), executable(cwd='../private'), executable(timeout=11),
                 executable(timeout=True), executable(['a' * 4097]), executable(['a'] * 65),
                 executable(['a', 'b\x00c']), executable([7]), executable(['']),
                 {'argv': [], 'cwd': '.', 'timeout_seconds': 1},
                 {'argv': 'echo shell string', 'cwd': '.', 'timeout_seconds': 1}]
        for arguments in cases:
            report = self.run_calls([call('workspace_exec', arguments)])
            self.assertEqual(self.results(report)[0]['reason'], 'invalid_arguments')
        self.assertEqual(self.sandbox.requests, [])

    def test_model_unicode_surrogates_fail_as_arguments_without_adapter_calls(self):
        report = self.run_calls([call('workspace_write', {'path': 'src/A.java', 'content': '\ud800'})])
        self.assertEqual(self.results(report)[0]['reason'], 'invalid_arguments')
        report = self.run_calls([call('workspace_exec', executable(['echo', '\ud800']))])
        self.assertEqual(self.results(report)[0]['reason'], 'invalid_arguments')
        self.assertEqual(self.sandbox.requests, [])

    def test_malformed_duplicate_call_ids_prevent_all_batch_side_effects(self):
        for identifier in ('same', 'bad id'):
            report = self.run_calls([call('workspace_exec', executable(), 'same'),
                                     call('workspace_exec', executable(), identifier)])
            self.assertEqual(report.status, 'invalid_model_reply')
            self.assertEqual(self.sandbox.requests, [])

    def test_read_write_limits_and_strict_integer_types(self):
        for key, value in (('limit', True), ('limit', 8193), ('offset', -1), ('offset', 1.5)):
            arguments = {'path': 'docs/rules.txt', 'offset': 0, 'limit': 10}
            arguments[key] = value
            report = self.run_calls([call('workspace_read', arguments)])
            self.assertEqual(self.results(report)[0]['reason'], 'invalid_arguments')
        limits = ControllerLimits(max_write_bytes=10)
        report = self.run_calls([call('workspace_write', {'path': 'src/A.java', 'content': 'ü' * 6})], limits=limits)
        self.assertEqual(self.results(report)[0]['reason'], 'invalid_arguments')
        self.assertEqual(self.sandbox.requests, [])

    def test_half_megabyte_source_file_can_be_written_with_fixed_limits(self):
        content = 'x' * 524288
        report = self.run_calls([call('workspace_write', {'path': 'src/A.java', 'content': content})])
        self.assertEqual(self.results(report)[0]['bytes_written'], 524288)
        self.assertEqual(self.sandbox.files['src/A.java'], content.encode())

    def test_duplicate_keys_malformed_and_nonobject_json_are_rejected(self):
        cases = ['{"path":"docs/rules.txt","path":"/private","offset":0,"limit":10}',
                 'not json', '[]', 'null', '{"limit":NaN}', '[' * 2000 + ']' * 2000]
        for arguments in cases:
            report = self.run_calls([ToolCall('call1', 'workspace_read', arguments)])
            self.assertEqual(self.results(report)[0]['reason'], 'invalid_arguments')
        self.assertEqual(self.sandbox.requests, [])

    def test_handles_must_be_issued_inside_this_run(self):
        for name, arguments in [('scrimmage_submit', {'artifact_id': ARTIFACT, 'package': 'bot'}),
                                ('scrimmage_result', {'submission_id': SUBMISSION}),
                                ('scrimmage_replay', {'submission_id': SUBMISSION})]:
            report = self.run_calls([call(name, arguments)])
            self.assertEqual(self.results(report)[0]['reason'], 'unknown_handle')
        self.assertEqual(self.scrimmage.requests, [])
        report = self.run_calls([call('workspace_exec', executable())])
        report = self.run_calls([call('scrimmage_submit', {'artifact_id': ARTIFACT, 'package': 'bot'})])
        self.assertEqual(self.results(report)[0]['reason'], 'unknown_handle')

    def test_public_result_projection_removes_internal_metadata(self):
        self.scrimmage.result_reply.evidence.update(opponent='/secret/heldout', logs='/secret/log')
        self.scrimmage.result_reply.evidence['summary']['schedule'] = '/secret/order'
        report = self.run_calls(self.bootstrap() + [call('scrimmage_result', {'submission_id': SUBMISSION}, 'result')])
        self.assertNotIn('/secret', b''.join(report.transcript).decode())
        self.assertEqual(self.results(report)[-1]['status'], 'complete')

    def test_wrong_submission_identity_or_raw_replay_cannot_escape(self):
        self.scrimmage.result_reply = ScrimmageReply('s_' + 'd' * 32, {'status': 'accepted'})
        report = self.run_calls(self.bootstrap() + [call('scrimmage_result', {'submission_id': SUBMISSION}, 'result')])
        self.assertEqual(self.results(report)[-1]['reason'], 'invalid_adapter_response')
        self.scrimmage.replay_reply = b'private opponent replay'
        report = self.run_calls(self.bootstrap() + [call('scrimmage_replay', {'submission_id': SUBMISSION}, 'replay')])
        self.assertEqual(self.results(report)[-1]['reason'], 'invalid_adapter_response')
        self.assertNotIn('private opponent', b''.join(report.transcript).decode())

    def test_adapter_exception_text_is_never_forwarded(self):
        for error in (RuntimeError('/secret/credentials'), InvalidCall('/secret/credentials')):
            with patch.object(self.sandbox, 'read', side_effect=error):
                report = self.run_calls([call('workspace_read', {'path': 'docs/rules.txt', 'offset': 0, 'limit': 10})])
            self.assertNotIn('/secret', b''.join(report.transcript).decode())

    def test_adapter_output_shape_and_read_bound(self):
        with patch.object(self.sandbox, 'read', return_value=b'too many bytes'):
            report = self.run_calls([call('workspace_read', {'path': 'docs/rules.txt', 'offset': 0, 'limit': 1})])
        self.assertEqual(self.results(report)[0]['reason'], 'invalid_adapter_response')
        self.sandbox.exec_reply = {'status': 'succeeded', 'private': '/secret'}
        report = self.run_calls([call('workspace_exec', executable())])
        self.assertEqual(self.results(report)[0]['reason'], 'invalid_adapter_response')

    def test_exec_output_is_truncated_and_encoded_output_is_capped(self):
        self.sandbox.exec_reply = ExecReply('succeeded', b'x' * 20000, b'y' * 20000)
        report = self.run_calls([call('workspace_exec', executable())])
        output = self.results(report)[0]
        self.assertTrue(output['truncated'])
        self.assertLess(len(output['stdout']), 20000)
        self.sandbox.exec_reply = ExecReply('succeeded', b'\x01' * 20000, b'\x01' * 20000)
        report = self.run_calls([call('workspace_exec', executable())])
        self.assertEqual(self.results(report)[0]['reason'], 'tool_output_limit')
        self.assertLessEqual(len(report.transcript[-1]), ControllerLimits().max_tool_output_bytes)

    def test_fallback_error_envelope_cannot_exceed_small_configured_cap(self):
        self.sandbox.exec_reply = ExecReply('succeeded', b'x' * 20000)
        report = self.run_calls([call('workspace_exec', executable(), 'x' * 64)],
                                limits=ControllerLimits(max_tool_output_bytes=128))
        self.assertEqual(report.status, 'output_limit')
        self.assertEqual(self.results(report), [])
        self.assertEqual(len(report.transcript), 1)

    def test_invalid_utf8_final_text_is_a_malformed_model_reply(self):
        report = run_synthetic(self.context, SyntheticTransport([TurnReply(final_text='\ud800')]),
                               self.sandbox, self.scrimmage)
        self.assertEqual(report.status, 'invalid_model_reply')
        self.assertIsNone(report.final_text)

    def test_turn_call_and_total_output_caps(self):
        report = self.run_calls([call('workspace_exec', executable())], limits=ControllerLimits(max_turns=1))
        self.assertEqual(report.status, 'turn_limit')
        report = self.run_calls([call('workspace_exec', executable(), 'one'), call('workspace_exec', executable(), 'two')],
                                limits=ControllerLimits(max_tool_calls=1))
        self.assertEqual(report.status, 'tool_call_limit')
        report = self.run_calls([call('workspace_exec', executable())], limits=ControllerLimits(max_total_output_bytes=1))
        self.assertEqual(report.status, 'output_limit')

    def test_time_cap_is_checked_after_transport_and_adapter(self):
        def now():
            return self.clock_time

        def delayed_transport(request):
            self.clock_time += 31
            return TurnReply((call('workspace_exec', executable()),))

        transport = SyntheticTransport([])
        transport.next_turn = delayed_transport
        report = run_synthetic(self.context, transport, self.sandbox, self.scrimmage, clock=now)
        self.assertEqual(report.status, 'time_limit')
        self.assertEqual(self.sandbox.requests, [])

        def delayed_read(request):
            self.clock_time += 31
            return b'x'

        with patch.object(self.sandbox, 'read', side_effect=delayed_read):
            report = self.run_calls([call('workspace_read', {'path': 'docs/rules.txt', 'offset': 0, 'limit': 1})], clock=now)
        self.assertEqual(report.status, 'time_limit')
        self.assertEqual(len(self.transport.requests), 1)

    def test_invalid_transport_shapes_duplicate_ids_and_oversized_model_output(self):
        replies = [None, {}, TurnReply(), TurnReply(calls=[]),
                   TurnReply(calls=(call('workspace_exec', executable()),), final_text='both'),
                   TurnReply(calls=(object(),)), TurnReply(calls=(ToolCall('bad id', 'workspace_exec', '{}'),)),
                   TurnReply(calls=tuple(call('workspace_exec', executable(), str(n)) for n in range(5))),
                   TurnReply(calls=(call('workspace_exec', executable()), call('workspace_exec', executable())))]
        for reply in replies:
            transport = SyntheticTransport([reply])
            report = run_synthetic(self.context, transport, self.sandbox, self.scrimmage)
            self.assertEqual(report.status, 'invalid_model_reply')
        report = run_synthetic(self.context, SyntheticTransport([TurnReply(final_text='x' * 1000)]),
                               self.sandbox, self.scrimmage, limits=ControllerLimits(max_model_reply_bytes=100))
        self.assertEqual(report.status, 'invalid_model_reply')

    def test_transport_failure_is_bounded_and_context_limits_are_checked(self):
        report = run_synthetic(self.context, SyntheticTransport([]), self.sandbox, self.scrimmage)
        self.assertEqual(report.status, 'transport_failure')
        with self.assertRaisesRegex(ValueError, 'initial_context_limit'):
            self.run_calls([], limits=ControllerLimits(max_initial_bytes=1))
        for value in (True, 0, -1, 1.5):
            with self.assertRaises(ValueError):
                ControllerLimits(max_turns=value)

    def test_real_launch_is_always_unsupported_even_with_claimed_attestations(self):
        for kwargs in ({}, {'enabled': True}, {'boundary_validated': True},
                       {'transport': self.transport if hasattr(self, 'transport') else object(),
                        'attestation': {'tool': True, 'filesystem': True, 'network': True}}):
            with self.assertRaisesRegex(RuntimeError, 'real_launch_unsupported'):
                launch_real(**kwargs)


if __name__ == '__main__':
    unittest.main()
