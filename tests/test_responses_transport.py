"""Responses protocol/admission tests. All counts and exchanges are synthetic."""
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.controller import (ControllerLimits, FrozenContext, TOOL_NAMES,
    TurnRequest, launch_real, run_synthetic, tool_schemas)
from services.responses_transport import (MODEL, REASONING_EFFORT, OfflineInputCount,
    OfflineResponsesTransport, ResponsesLimits, TransportError)
from test_controller import SyntheticSandbox, SyntheticScrimmage


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')


def function(name='workspace_read', arguments=None, call_id='call_1', item_id='fc_1'):
    if arguments is None:
        arguments = {'path': 'docs/rules.txt', 'offset': 0, 'limit': 100}
    return {'type': 'function_call', 'id': item_id, 'status': 'completed',
            'call_id': call_id, 'name': name,
            'arguments': arguments if type(arguments) is str else json.dumps(arguments)}


def message(text='done', phase='final_answer', item_id='msg_1'):
    return {'type': 'message', 'id': item_id, 'status': 'completed', 'role': 'assistant',
            'phase': phase, 'content': [{'type': 'output_text', 'text': text, 'annotations': []}]}


def reasoning():
    return {'type': 'reasoning', 'id': 'rs_1', 'summary': [],
            'encrypted_content': 'opaque-fixture-only-do-not-decrypt', 'status': None, 'content': None}


def response(output=None, **changes):
    result = {'object': 'response', 'status': 'completed', 'error': None,
              'model': MODEL, 'service_tier': 'default',
              'incomplete_details': None, 'output': [message(item_id='msg_final')] if output is None else output,
              'usage': {'input_tokens': 100, 'output_tokens': 16, 'total_tokens': 116,
                        'input_tokens_details': {'cached_tokens': 50, 'cache_write_tokens': 0},
                        'output_tokens_details': {'reasoning_tokens': 10}}}
    result.update(changes)
    return encoded(result)


class FixtureCounter:
    def __init__(self, tokens=100):
        self.tokens = tokens
        self.bodies = []

    def count_input(self, body):
        self.bodies.append(body)
        return OfflineInputCount(hashlib.sha256(body).hexdigest(), self.tokens)


class FixtureExchange:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def respond(self, request):
        self.requests.append(request)
        reply = self.responses.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class ResponsesTests(unittest.TestCase):
    def setUp(self):
        self.context = FrozenContext(b'exact system\r\n  text\n', 'task: café\n'.encode('utf-8'))
        self.request = TurnRequest(self.context, TOOL_NAMES, tool_schemas(), (), 1)

    def transport(self, responses, **kwargs):
        self.exchange = FixtureExchange(responses)
        self.counter = kwargs.pop('counter', FixtureCounter())
        return OfflineResponsesTransport(self.exchange, self.counter, **kwargs)

    def reject_response(self, payload, code, **kwargs):
        transport = self.transport([payload], **kwargs)
        with self.assertRaisesRegex(TransportError, '^' + code + '$'):
            transport.next_turn(self.request)
        self.assertEqual(len(self.exchange.requests), 1)
        return transport

    def next_request(self, reply, result=None, previous=None):
        previous = previous or self.request
        call = reply.calls[0]
        return replace(previous, turn=previous.turn + 1, transcript=previous.transcript + (
            encoded({'final_text': None, 'calls': [{'call_id': call.call_id,
                     'name': call.name, 'arguments_json': call.arguments_json}]}),
            encoded({'call_id': call.call_id, 'result': result or {'status': 'complete', 'text': 'rules'}})))

    def test_six_strict_tools_pinned_model_and_exact_context(self):
        transport = self.transport([response()])
        self.assertEqual(transport.next_turn(self.request).final_text, 'done')
        sent = self.exchange.requests[0]
        body = json.loads(sent.body)
        self.assertEqual(body['model'], MODEL)
        self.assertEqual(body['reasoning'], {'effort': REASONING_EFFORT})
        self.assertEqual(body['instructions'].encode('utf-8'), self.context.system)
        self.assertEqual(body['input'], [{'role': 'user', 'content': self.context.task.decode('utf-8')}])
        self.assertEqual(tuple(item['name'] for item in body['tools']), TOOL_NAMES)
        for schema, tool in zip(tool_schemas(), body['tools']):
            self.assertEqual(tool, {'type': 'function', 'strict': True, **json.loads(schema)})
        self.assertIs(body['parallel_tool_calls'], False)
        self.assertEqual(body['tool_choice'], 'auto')
        for key in ('store', 'stream', 'background'):
            self.assertIs(body[key], False)
        self.assertEqual(body['service_tier'], 'default')
        self.assertEqual(body['truncation'], 'disabled')
        self.assertNotIn('previous_response_id', body)
        self.assertNotIn('endpoint', body)
        self.assertEqual(self.counter.bodies, [sent.body])
        with self.assertRaises(FrozenInstanceError):
            sent.body = b'changed'
        with self.assertRaisesRegex(TransportError, 'session_complete'):
            transport.next_turn(self.request)
        self.assertEqual(len(self.exchange.requests), 1)

    def test_controller_end_to_end_and_stateless_opaque_continuation(self):
        output = [reasoning(), message('checking', 'commentary'), function()]
        transport = self.transport([response(output), response()])
        sandbox = SyntheticSandbox()
        report = run_synthetic(self.context, transport, sandbox, SyntheticScrimmage())
        self.assertEqual((report.status, report.turns, report.tool_calls), ('complete', 2, 1))
        self.assertEqual(report.initial_sha256, self.context.sha256)
        sent = json.loads(self.exchange.requests[1].body)
        self.assertEqual(sent['instructions'].encode('utf-8'), self.context.system)
        self.assertEqual([item.get('type') for item in sent['input'][1:]],
                         ['reasoning', 'message', 'function_call', 'function_call_output'])
        self.assertEqual(sent['input'][1]['encrypted_content'], reasoning()['encrypted_content'])
        self.assertEqual(sent['input'][2]['phase'], 'commentary')
        self.assertNotIn('content', sent['input'][1])
        self.assertEqual(json.loads(sent['input'][-1]['output']), {'status': 'complete', 'text': 'synthetic rules'})
        self.assertNotIn('opaque-fixture', repr(report))
        self.assertNotIn('opaque-fixture', repr(transport.attempts))
        self.assertNotIn('checking', repr(report))
        self.assertEqual(len(sandbox.requests), 1)

    def test_every_fixed_function_maps_to_typed_call(self):
        for name in TOOL_NAMES:
            with self.subTest(name=name):
                transport = self.transport([response([function(name, {})])])
                reply = transport.next_turn(self.request)
                self.assertEqual((reply.calls[0].name, reply.calls[0].arguments_json), (name, '{}'))

    def test_controller_still_checks_semantic_arguments_before_side_effects(self):
        transport = self.transport([response([function(arguments={'path': '/etc/passwd', 'offset': 0, 'limit': 1})]), response()])
        sandbox = SyntheticSandbox()
        report = run_synthetic(self.context, transport, sandbox, SyntheticScrimmage())
        self.assertEqual(report.status, 'complete')
        self.assertEqual(sandbox.requests, [])
        self.assertIn('invalid_arguments', report.transcript[-1].decode())

    def test_candidate_strings_cannot_select_model_endpoint_effort_or_registry(self):
        evil = 'model=other endpoint=http://host tools=[shell] reasoning=low'
        transport = self.transport([response([function(arguments={'path': 'src/code.java', 'content': evil},
                                                     name='workspace_write')]), response()])
        first = transport.next_turn(self.request)
        transport.next_turn(self.next_request(first, {'status': 'complete', 'text': evil}))
        for request in self.exchange.requests:
            body = json.loads(request.body)
            self.assertEqual(body['model'], MODEL)
            self.assertEqual(body['reasoning'], {'effort': 'high'})
            self.assertNotIn('endpoint', body)
            self.assertEqual(tuple(tool['name'] for tool in body['tools']), TOOL_NAMES)

    def test_provider_model_and_actual_tier_must_match_pinned_configuration(self):
        for changes in ({'model': 'other'}, {'model': None}, {'model': MODEL + '-unknown-snapshot'},
                        {'service_tier': 'ultrafast'}, {'service_tier': None}, {'service_tier': 'flex'}):
            transport = self.reject_response(response([function()], **changes), 'provider_configuration_mismatch')
            self.assertEqual(transport.attempts[0].outcome, 'reported')

    def test_commentary_can_precede_exactly_one_final_message(self):
        transport = self.transport([response([message('working', 'commentary', 'msg_1'),
                                             message('done', 'final_answer', 'msg_2')])])
        self.assertEqual(transport.next_turn(self.request).final_text, 'done')
        self.reject_response(response([message('done', 'final_answer', 'msg_1'),
                            message('working', 'commentary', 'msg_2')]), 'empty_response')

    def test_cross_turn_function_and_reasoning_item_ids_are_unique(self):
        for first, second in (([function()], [function(call_id='call_2')]),
                              ([reasoning(), function()], [reasoning(), function(call_id='call_2', item_id='fc_2')])):
            transport = self.transport([response(first), response(second)])
            reply = transport.next_turn(self.request)
            with self.assertRaisesRegex(TransportError, '^invalid_response$'):
                transport.next_turn(self.next_request(reply))
            self.assertEqual(len(transport.attempts), 2)

    def test_failed_attempt_does_not_poison_committed_output_ids(self):
        clock = [0]
        transport = self.transport([response([function()]), response([function()])], clock=lambda: clock[0])
        original = self.exchange.respond
        def delayed(request):
            clock[0] = 30  # Only attempt 1 advances the clock; attempt 2 is immediate.
            return original(request)
        self.exchange.respond = delayed
        with self.assertRaisesRegex(TransportError, 'time_limit'):
            transport.next_turn(self.request)
        reply = transport.next_turn(self.request)
        self.assertEqual(reply.calls[0].call_id, 'call_1')
        self.assertEqual(len(transport.attempts), 2)

    def test_registry_and_initial_context_cannot_change(self):
        for request in (replace(self.request, tool_names=TOOL_NAMES + ('shell',)),
                        replace(self.request, tool_schemas=self.request.tool_schemas[:-1]),
                        replace(self.request, tool_schemas=self.request.tool_schemas + (b'{}',))):
            transport = self.transport([])
            with self.assertRaisesRegex(TransportError, 'registry_changed'):
                transport.next_turn(request)
            self.assertEqual(transport.attempts, ())
        transport = self.transport([response([function()])])
        next_request = self.next_request(transport.next_turn(self.request))
        with self.assertRaisesRegex(TransportError, 'context_changed'):
            transport.next_turn(replace(next_request, context=FrozenContext(b'other', self.context.task)))
        self.assertEqual(len(self.exchange.requests), 1)

    def test_custom_controller_schema_limits_must_match(self):
        limits = ControllerLimits(max_read_bytes=2)
        transport = self.transport([response()], controller_limits=limits)
        with self.assertRaisesRegex(TransportError, 'registry_changed'):
            transport.next_turn(self.request)
        request = replace(self.request, tool_schemas=tool_schemas(limits))
        transport.next_turn(request)
        self.assertEqual(json.loads(self.exchange.requests[0].body)['tools'][0]['parameters']['properties']['limit']['maximum'], 2)

    def test_transcript_prefix_call_and_result_are_not_replaceable(self):
        transport = self.transport([response([function()]), response([function(call_id='call_2', item_id='fc_2')])])
        first = transport.next_turn(self.request)
        good = self.next_request(first)
        invalid = [replace(good, turn=True), replace(good, turn=8), replace(good, transcript=()),
                   replace(good, transcript=good.transcript[::-1]),
                   replace(good, transcript=(good.transcript[0], encoded({'call_id': 'other', 'result': {}}))),
                   replace(good, transcript=(good.transcript[0], encoded({'call_id': 'call_1', 'result': {}, 'extra': 1})))]
        for request in invalid:
            with self.assertRaises(TransportError):
                transport.next_turn(request)
        second = transport.next_turn(good)
        third = self.next_request(second, previous=good)
        with self.assertRaisesRegex(TransportError, 'transcript_changed'):
            transport.next_turn(replace(third, transcript=(b'{}',) + third.transcript[1:]))
        self.assertEqual(len(self.exchange.requests), 2)

    def test_unknown_multiple_duplicate_and_malformed_calls_fail_closed(self):
        for name in ('shell', 'web_search', 'mcp', 'tool_search', 'host.exec'):
            self.reject_response(response([function(name)]), 'unknown_tool')
        self.reject_response(response([function(), function(call_id='call_2', item_id='fc_2')]), 'multiple_calls')
        self.reject_response(response([function(), function(item_id='fc_2')]), 'multiple_calls')
        for arguments in ('{', '[]', 'null', '{"a":1,"a":2}', '{"x":NaN}', '{"x":1e999}', '{"a":"\\ud800"}'):
            self.reject_response(response([function(arguments=arguments)]), 'invalid_call')
        for call_id in ('', 'x' * 65, '../escape', 1):
            self.reject_response(response([function(call_id=call_id)]), 'invalid_call')
        transport = self.transport([response([function()]), response([function(item_id='fc_2')])])
        first = transport.next_turn(self.request)
        with self.assertRaisesRegex(TransportError, 'duplicate_call'):
            transport.next_turn(self.next_request(first))

    def test_no_partial_batch_dispatch(self):
        for bad in (function('shell', item_id='fc_2'), function(call_id='call_2', item_id='fc_2')):
            transport = self.transport([response([function(), bad])])
            sandbox = SyntheticSandbox()
            report = run_synthetic(self.context, transport, sandbox, SyntheticScrimmage())
            self.assertEqual(report.status, 'transport_failure')
            self.assertEqual(sandbox.requests, [])

    def test_status_truncation_and_exhaustion_never_dispatch_partial_output(self):
        for status, code in (('incomplete', 'provider_incomplete'), ('queued', 'provider_pending'),
                             ('in_progress', 'provider_pending'), ('cancelled', 'provider_cancelled'),
                             ('failed', 'invalid_response'), ('new_status', 'invalid_response')):
            transport = self.reject_response(response([function()], status=status), code)
            self.assertEqual(transport.attempts[-1].outcome, 'reported')
        self.reject_response(response([function()], incomplete_details={'reason': 'max_output_tokens'}), 'provider_incomplete')
        self.reject_response(response([]), 'empty_response')
        self.reject_response(response([reasoning()]), 'empty_response')
        self.reject_response(response([message('', 'final_answer')]), 'empty_response')
        self.reject_response(response([message('still working', 'commentary')]), 'empty_response')
        for kind in ('web_search_call', 'local_shell_call', 'mcp_call', 'compaction'):
            self.reject_response(response([{'id': 'item_1', 'type': kind}]), 'unsupported_output')

    def test_output_shapes_fields_phases_and_plaintext_reasoning_are_closed(self):
        item = function(); item['status'] = 'incomplete'
        self.reject_response(response([item]), 'invalid_call')
        item = function(); item['endpoint'] = 'https://evil.invalid'
        self.reject_response(response([item]), 'invalid_response')
        item = reasoning(); item['summary'] = [{'type': 'summary_text', 'text': 'never expose'}]
        self.reject_response(response([item, function()]), 'unsupported_output')
        item = reasoning(); item['content'] = [{'type': 'reasoning_text', 'text': 'never expose'}]
        self.reject_response(response([item, function()]), 'unsupported_output')
        item = reasoning(); del item['encrypted_content']
        self.reject_response(response([item, function()]), 'invalid_response')
        self.reject_response(response([message('end', 'final_answer'), function()]), 'unsupported_output')
        self.reject_response(response([message('bad', 'unknown')]), 'unsupported_output')
        item = message(); item['role'] = 'system'
        self.reject_response(response([item]), 'unsupported_output')
        item = message(); item['content'] = [{'type': 'refusal', 'refusal': 'private reason'}]
        self.reject_response(response([item]), 'provider_refusal')
        item = message(); item['content'][0]['annotations'] = [{'url': 'private'}]
        self.reject_response(response([item]), 'unsupported_output')
        self.reject_response(response([message(), message(item_id='msg_2')]), 'empty_response')

    def test_response_parser_and_error_payloads_are_sanitized_and_bounded(self):
        for data in (b'{', b'[]', b'{"status":1,"status":2}', b'{"x":NaN}',
                     b'{"x":"\\ud800"}', b'{"x":1e999}', b'"' + b'\\ud800' + b'"',
                     b'[' * 100 + b']' * 100, '{"x":1}'.encode('utf-16'), 'not bytes'):
            self.reject_response(data, 'invalid_response')
        marker = 'PRIVATE_SECRET_DO_NOT_LEAK'
        for code, expected in (('rate_limit_exceeded', 'provider_rate_limited'),
                               ('server_error', 'provider_unavailable'), (marker, 'provider_error')):
            transport = self.reject_response(encoded({'error': {'code': code, 'message': marker,
                'nested': {'secret': marker, 'url': 'https://private.invalid'}}}), expected)
            self.assertNotIn(marker, repr(transport.attempts))
            self.assertEqual(transport.attempts[0].outcome, 'unknown')
        self.reject_response(encoded({'error': {'message': marker * 30000}}), 'response_bytes_limit')
        payload = response(extra_private={'secret': marker}, request_id=marker)
        transport = self.transport([payload])
        reply = transport.next_turn(self.request)
        self.assertNotIn(marker, repr(reply) + repr(transport.attempts))

    def test_per_request_and_cumulative_byte_limits(self):
        transport = self.transport([response()], limits=ResponsesLimits(max_request_bytes=1))
        with self.assertRaisesRegex(TransportError, 'request_bytes_limit'):
            transport.next_turn(self.request)
        self.assertEqual(transport.attempts, ())
        self.assertEqual(self.exchange.requests, [])
        self.reject_response(response(), 'response_bytes_limit', limits=ResponsesLimits(max_response_bytes=10))
        self.reject_response(response([message('é' * 10)]), 'invalid_response', limits=ResponsesLimits(max_text_bytes=19))
        self.reject_response(response([reasoning(), function()]), 'invalid_response', limits=ResponsesLimits(max_reasoning_bytes=1))
        self.reject_response(response([function()]), 'state_limit', limits=ResponsesLimits(max_state_bytes=10))
        self.reject_response(response([reasoning(), function()]), 'invalid_response', limits=ResponsesLimits(max_output_items=1))
        transport = self.transport([RuntimeError('unknown')], limits=ResponsesLimits(max_total_request_bytes=1))
        with self.assertRaisesRegex(TransportError, 'request_bytes_limit'):
            transport.next_turn(self.request)
        self.assertEqual(self.exchange.requests, [])

    def test_token_count_fixture_must_bind_all_request_bytes(self):
        class BadCounter:
            def count_input(self, body):
                return OfflineInputCount('0' * 64, 1)
        transport = self.transport([], counter=BadCounter())
        with self.assertRaisesRegex(TransportError, 'invalid_token_count'):
            transport.next_turn(self.request)
        self.assertEqual(transport.attempts[0].outcome, 'not_sent')
        self.assertEqual(self.exchange.requests, [])
        for tokens, code in ((True, 'invalid_token_count'), (-1, 'invalid_token_count'),
                             (16001, 'input_token_limit')):
            transport = self.transport([], counter=FixtureCounter(tokens))
            with self.assertRaisesRegex(TransportError, code):
                transport.next_turn(self.request)
            self.assertEqual(self.exchange.requests, [])
            self.assertEqual(len(transport.attempts), 1)

    def test_unknown_outcomes_and_explicit_retries_reserve_full_ceiling(self):
        transport = self.transport([TimeoutError('credential=secret'), response()])
        with self.assertRaisesRegex(TransportError, '^exchange_failed$'):
            transport.next_turn(self.request)
        self.assertEqual(len(self.exchange.requests), 1)  # No automatic retry.
        first = transport.attempts[0]
        self.assertEqual((first.outcome, first.input_tokens, first.output_tokens), ('unknown', None, None))
        self.assertEqual((first.reserved_input_tokens, first.reserved_output_tokens), (16000, 8000))
        transport.next_turn(self.request)  # Trusted explicit retry; no candidate API for this.
        self.assertEqual(len(transport.attempts), 2)
        self.assertEqual(self.exchange.requests[0].body, self.exchange.requests[1].body)
        self.assertEqual(sum(item.reserved_input_tokens for item in transport.attempts), 32000)
        self.assertEqual(sum(item.reserved_output_tokens for item in transport.attempts), 16000)
        self.assertEqual(transport.attempts[1].input_tokens, 100)
        self.assertEqual(transport.attempts[1].output_tokens, 16)
        self.assertEqual(transport.attempts[1].cached_input_tokens, 50)
        self.assertEqual(transport.attempts[1].reasoning_output_tokens, 10)
        with self.assertRaises(FrozenInstanceError):
            transport.attempts[0].outcome = 'refunded'

    def test_retry_cannot_change_payload_even_after_unknown_outcome(self):
        transport = self.transport([TimeoutError('unknown')])
        with self.assertRaises(TransportError):
            transport.next_turn(self.request)
        with self.assertRaisesRegex(TransportError, 'context_changed'):
            transport.next_turn(replace(self.request, context=FrozenContext(b'changed', b'task')))
        self.assertEqual(len(self.exchange.requests), 1)

    def test_request_and_token_reservations_exhaust_on_errors_without_refunds(self):
        for limits, code in ((ResponsesLimits(max_requests=1), 'request_limit'),
                             (ResponsesLimits(max_reserved_input_tokens=16000), 'reservation_limit'),
                             (ResponsesLimits(max_reserved_output_tokens=8000), 'reservation_limit'),
                             (ResponsesLimits(max_reserved_response_bytes=262144), 'reservation_limit')):
            transport = self.transport([encoded({'error': {'code': 'rate_limit_exceeded'}})], limits=limits)
            with self.assertRaisesRegex(TransportError, 'provider_rate_limited'):
                transport.next_turn(self.request)
            with self.assertRaisesRegex(TransportError, code):
                transport.next_turn(self.request)
            self.assertEqual(len(transport.attempts), 1)
            self.assertEqual(len(self.exchange.requests), 1)

    def test_counter_failures_count_toward_attempt_budget_without_exchange(self):
        class RaisingCounter:
            def count_input(self, body):
                raise RuntimeError('raw_secret')
        transport = self.transport([], counter=RaisingCounter(), limits=ResponsesLimits(max_requests=1))
        with self.assertRaisesRegex(TransportError, '^token_count_failure$'):
            transport.next_turn(self.request)
        with self.assertRaisesRegex(TransportError, '^request_limit$'):
            transport.next_turn(self.request)
        self.assertEqual(self.exchange.requests, [])
        self.assertEqual(transport.attempts[0].outcome, 'not_sent')
        self.assertEqual(transport.attempts[0].reserved_output_tokens, 8000)

    def test_usage_must_be_complete_bounded_and_internally_consistent(self):
        self.reject_response(response(usage=None), 'missing_usage')
        for usage in ('private', {}, {'input_tokens': True, 'output_tokens': 0, 'total_tokens': 1},
                      {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 3},
                      {'input_tokens': -1, 'output_tokens': 0, 'total_tokens': -1},
                      {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2,
                       'input_tokens_details': {'cached_tokens': 2}}):
            transport = self.reject_response(response(usage=usage), 'invalid_usage')
            self.assertEqual(transport.attempts[0].outcome, 'unknown')
        for usage in ({'input_tokens': 16001, 'output_tokens': 0, 'total_tokens': 16001},
                      {'input_tokens': 1, 'output_tokens': 8001, 'total_tokens': 8002}):
            transport = self.reject_response(response(usage=usage), 'usage_limit')
            self.assertEqual(transport.attempts[0].outcome, 'reported')
            self.assertEqual(transport.attempts[0].reserved_input_tokens, 16000)

    def test_soft_deadlines_and_reentrant_callbacks_cannot_dispatch(self):
        time = [0]
        class SlowCounter(FixtureCounter):
            def count_input(self, body):
                time[0] = 30
                return super().count_input(body)
        transport = self.transport([], counter=SlowCounter(), clock=lambda: time[0])
        with self.assertRaisesRegex(TransportError, 'time_limit'):
            transport.next_turn(self.request)
        self.assertEqual(self.exchange.requests, [])
        self.assertEqual(transport.attempts[0].outcome, 'not_sent')
        time[0] = 0
        transport = self.transport([response()], clock=lambda: time[0])
        original = self.exchange.respond
        def slow(request):
            time[0] = 30
            return original(request)
        self.exchange.respond = slow
        with self.assertRaisesRegex(TransportError, 'time_limit'):
            transport.next_turn(self.request)
        self.assertEqual(transport.attempts[0].outcome, 'reported')
        transport = self.transport([response()])
        original = self.exchange.respond
        def nested(request):
            with self.assertRaisesRegex(TransportError, 'concurrent_request'):
                transport.next_turn(self.request)
            return original(request)
        self.exchange.respond = nested
        self.assertEqual(transport.next_turn(self.request).final_text, 'done')

    def test_exchange_receives_only_remaining_attempt_time(self):
        clock = [0]
        class CountingDelay(FixtureCounter):
            def count_input(self, body):
                clock[0] += 7
                return super().count_input(body)
        transport = self.transport([response()], counter=CountingDelay(), clock=lambda: clock[0])
        transport.next_turn(self.request)
        self.assertEqual(self.exchange.requests[0].timeout_seconds, 23)

    def test_invalid_and_regressing_clocks_fail_with_closed_errors(self):
        for value in (float('nan'), float('inf'), float('-inf'), True, False, 'secret', None, 2 ** 10000):
            with self.subTest(value_type=type(value).__name__):
                transport = self.transport([], clock=lambda: value)
                with self.assertRaisesRegex(TransportError, '^invalid_clock$'):
                    transport.next_turn(self.request)
                self.assertEqual(self.exchange.requests, [])
                self.assertEqual(transport.attempts[0].outcome, 'not_sent')
        for times in ([100, 0], [100, 101, 0]):
            samples = iter(times)
            transport = self.transport([response()], clock=lambda: next(samples))
            with self.assertRaisesRegex(TransportError, '^invalid_clock$'):
                transport.next_turn(self.request)
        def broken_clock():
            raise RuntimeError('secret')
        transport = self.transport([], clock=broken_clock)
        with self.assertRaisesRegex(TransportError, '^invalid_clock$'):
            transport.next_turn(self.request)

    def test_no_network_credential_subprocess_or_real_launch(self):
        transport = self.transport([response()])
        with patch('socket.socket', side_effect=AssertionError('no network')), \
             patch('subprocess.Popen', side_effect=AssertionError('no subprocess')), \
             patch('os.getenv', side_effect=AssertionError('no credentials')):
            self.assertEqual(transport.next_turn(self.request).final_text, 'done')
            with self.assertRaisesRegex(RuntimeError, '^real_launch_unsupported$'):
                launch_real(transport=transport, enabled=True)

    def test_configuration_errors_and_closed_error_constructor(self):
        for kwargs in ({'max_requests': True}, {'max_request_bytes': 0}, {'max_output_tokens': 15}):
            with self.assertRaises(ValueError):
                ResponsesLimits(**kwargs)
        self.assertEqual(str(TransportError('provider raw secret')), 'invalid_response')


if __name__ == '__main__':
    unittest.main()
