"""Offline-tested Responses codec for the controller's fixed application tools.

There is deliberately no HTTP sender, SDK, credential lookup, or live launcher.
Both injected callbacks MUST be inert offline fixtures in this version. They are
trusted host code, not model-callable tools or a Python security boundary.

See contracts/responses-transport-v1.md for the public API assumptions, bounded
opaque continuation, pessimistic reservations, and still-closed deployment gate.
"""
from dataclasses import dataclass, replace
import hashlib
import json
import math
import re
import time
from threading import Lock
from typing import Protocol

from services.controller import (ControllerLimits, FrozenContext, TOOL_NAMES,
                                 ToolCall, TurnReply, TurnRequest, tool_schemas)

MODEL = 'gpt-6.1-sol'
REASONING_EFFORT = 'high'
_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_CALL_ID = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
_ERRORS = frozenset((
    'invalid_request', 'context_changed', 'registry_changed', 'transcript_changed',
    'session_complete', 'concurrent_request', 'invalid_clock', 'time_limit', 'request_limit', 'request_bytes_limit',
    'response_bytes_limit', 'reservation_limit', 'input_token_limit',
    'token_count_failure', 'invalid_token_count', 'exchange_failed',
    'invalid_response', 'provider_configuration_mismatch', 'provider_error', 'provider_rate_limited',
    'provider_unavailable', 'provider_incomplete', 'provider_pending',
    'provider_cancelled', 'missing_usage', 'invalid_usage', 'usage_limit',
    'unsupported_output', 'invalid_call', 'unknown_tool', 'multiple_calls',
    'duplicate_call', 'empty_response', 'provider_refusal', 'state_limit',
))


class TransportError(RuntimeError):
    """Only closed error codes, never provider text or callback exceptions."""
    def __init__(self, code):
        super().__init__(code if type(code) is str and code in _ERRORS else 'invalid_response')


@dataclass(frozen=True)
class ResponsesLimits:
    # Illustrative offline admission policy, NOT an authorized spending budget.
    max_requests: int = 20
    max_request_bytes: int = 65536
    max_response_bytes: int = 262144
    max_total_request_bytes: int = 1310720
    max_reserved_response_bytes: int = 5242880
    max_input_tokens: int = 16000
    max_output_tokens: int = 8000
    max_reserved_input_tokens: int = 320000
    max_reserved_output_tokens: int = 160000
    max_output_items: int = 16
    max_text_bytes: int = 16384
    max_reasoning_bytes: int = 65536
    max_state_bytes: int = 131072
    timeout_seconds: int = 30

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in vars(self).values()):
            raise ValueError('invalid_responses_limits')
        if self.max_output_tokens < 16:
            raise ValueError('invalid_responses_limits')


@dataclass(frozen=True)
class OfflineInputCount:
    """Fixture assertion bound to the COMPLETE request bytes; not a tokenizer."""
    request_sha256: str
    input_tokens: int


@dataclass(frozen=True)
class ResponsesRequest:
    body: bytes
    attempt: int
    max_response_bytes: int
    timeout_seconds: int | float


class OfflineTokenCounter(Protocol):
    def count_input(self, body: bytes) -> OfflineInputCount: ...


class OfflineExchange(Protocol):
    def respond(self, request: ResponsesRequest) -> bytes: ...


@dataclass(frozen=True)
class Attempt:
    number: int
    request_sha256: str
    request_bytes: int
    reserved_response_bytes: int
    reserved_input_tokens: int
    reserved_output_tokens: int
    outcome: str = 'not_sent'
    response_bytes: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None


def _encode(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=True, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise TransportError('invalid_response') from None


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_key')
        result[key] = value
    return result


def _constant(unused):
    raise ValueError('non_finite_number')


def _decode(data, cap, code='invalid_response'):
    if type(data) is not bytes or len(data) > cap:
        raise TransportError(code)
    try:
        # Require UTF-8 rather than accepting json.loads' UTF-16/32 autodetection.
        value = json.loads(data.decode('utf-8'), object_pairs_hook=_object,
                           parse_constant=_constant)
        # Also reject escaped lone surrogates anywhere, including discarded fields.
        stack = [(value, 0)]
        while stack:
            item, depth = stack.pop()
            if depth > 32:
                raise ValueError('nesting_limit')
            if type(item) is str:
                item.encode('utf-8')
            elif type(item) is dict:
                stack.extend((x, depth + 1) for pair in item.items() for x in pair)
            elif type(item) is list:
                stack.extend((x, depth + 1) for x in item)
            elif type(item) is float and not math.isfinite(item):
                raise ValueError('non_finite_number')
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise TransportError(code) from None


def _text(value, cap, code='invalid_response', *, nonempty=False):
    if type(value) is not str or (nonempty and not value):
        raise TransportError(code)
    try:
        if len(value.encode('utf-8')) > cap:
            raise TransportError(code)
    except UnicodeError:
        raise TransportError(code) from None
    return value


def _keys(value, required, optional=()):
    if type(value) is not dict or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise TransportError('invalid_response')


def _identifier(value, call=False):
    if type(value) is not str or not (_CALL_ID if call else _ID).fullmatch(value):
        raise TransportError('invalid_call' if call else 'invalid_response')
    return value


def _nonnegative(value):
    if type(value) is not int or not 0 <= value <= 2 ** 63 - 1:
        raise TransportError('invalid_usage')
    return value


class OfflineResponsesTransport:
    """Single-run ModelTransport implementation, with only offline callbacks.

    Successful turns advance exactly once. After a failed attempt a trusted caller
    may explicitly repeat the IDENTICAL TurnRequest; every attempt reserves again.
    The controller itself never retries. There are no refunds, inferred no-charge
    errors, hidden SDK retries, polling, fallbacks, or context truncation.
    """
    def __init__(self, exchange: OfflineExchange, counter: OfflineTokenCounter, *,
                 limits=None, controller_limits=None, clock=time.monotonic):
        self._limits = limits or ResponsesLimits()
        self._controller_limits = controller_limits or ControllerLimits()
        if type(self._limits) is not ResponsesLimits or type(self._controller_limits) is not ControllerLimits:
            raise ValueError('invalid_transport_configuration')
        self._clock = clock
        self._last_clock = None
        self._exchange = exchange
        self._counter = counter
        self._schemas = tool_schemas(self._controller_limits)
        self._tools = tuple(_encode({'type': 'function', 'strict': True,
                                     **json.loads(schema)}) for schema in self._schemas)
        self._context = None
        self._transcript = ()
        self._history = b'[]'
        self._pending_reply = None
        self._pending_call = None
        self._seen_calls = set()
        self._seen_item_ids = set()
        self._turn = 1
        self._finished = False
        self._retry_request = None
        self._attempts = []
        self._lock = Lock()

    @property
    def attempts(self):
        """Trusted-side audit snapshot; no raw response, text, IDs, or secrets."""
        return tuple(self._attempts)

    def _prepare(self, request):
        if self._finished:
            raise TransportError('session_complete')
        if (type(request) is not TurnRequest or type(request.context) is not FrozenContext or
                type(request.turn) is not int or request.turn != self._turn or
                type(request.transcript) is not tuple or
                any(type(item) is not bytes for item in request.transcript)):
            raise TransportError('invalid_request')
        context = request.context
        if (type(context.system) is not bytes or type(context.task) is not bytes or
                len(context.system) + len(context.task) > self._controller_limits.max_initial_bytes):
            raise TransportError('invalid_request')
        try:
            snapshot = FrozenContext(context.system, context.task)
        except (ValueError, UnicodeError):
            raise TransportError('invalid_request') from None
        if context.sha256 != snapshot.sha256 or (self._context is not None and snapshot != self._context):
            raise TransportError('context_changed')
        if (type(request.tool_names) is not tuple or request.tool_names != TOOL_NAMES or
                type(request.tool_schemas) is not tuple or request.tool_schemas != self._schemas):
            raise TransportError('registry_changed')
        if (len(request.transcript) > 2 * self._limits.max_requests or
                sum(len(item) for item in request.transcript) > self._limits.max_state_bytes):
            raise TransportError('state_limit')
        history = json.loads(self._history)
        if self._pending_reply is None:
            if request.transcript:
                raise TransportError('transcript_changed')
        else:
            prefix = self._transcript + (self._pending_reply,)
            if len(request.transcript) != len(prefix) + 1 or request.transcript[:-1] != prefix:
                raise TransportError('transcript_changed')
            result = _decode(request.transcript[-1], self._controller_limits.max_tool_output_bytes,
                             'transcript_changed')
            if (type(result) is not dict or set(result) != {'call_id', 'result'} or
                    result['call_id'] != self._pending_call or type(result['result']) is not dict):
                raise TransportError('transcript_changed')
            history.append({'type': 'function_call_output', 'call_id': self._pending_call,
                            'output': _encode(result['result']).decode('utf-8')})
        if len(_encode(history)) > self._limits.max_state_bytes:
            raise TransportError('state_limit')
        body = _encode({
            'model': MODEL, 'reasoning': {'effort': REASONING_EFFORT},
            'instructions': snapshot.system.decode('utf-8'),
            'input': [{'role': 'user', 'content': snapshot.task.decode('utf-8')}] + history,
            'tools': [json.loads(tool) for tool in self._tools],
            'tool_choice': 'auto', 'parallel_tool_calls': False,
            'max_output_tokens': self._limits.max_output_tokens,
            'store': False, 'stream': False, 'background': False,
            'truncation': 'disabled', 'service_tier': 'default',
        })
        if len(body) > self._limits.max_request_bytes:
            raise TransportError('request_bytes_limit')
        if self._retry_request is not None and self._retry_request != request:
            raise TransportError('transcript_changed')
        return snapshot, history, body

    def _reserve(self, body):
        limits = self._limits
        number = len(self._attempts) + 1
        if number > limits.max_requests:
            raise TransportError('request_limit')
        if sum(item.request_bytes for item in self._attempts) + len(body) > limits.max_total_request_bytes:
            raise TransportError('request_bytes_limit')
        if (number * limits.max_response_bytes > limits.max_reserved_response_bytes or
                number * limits.max_input_tokens > limits.max_reserved_input_tokens or
                number * limits.max_output_tokens > limits.max_reserved_output_tokens):
            raise TransportError('reservation_limit')
        self._attempts.append(Attempt(number, hashlib.sha256(body).hexdigest(), len(body),
            limits.max_response_bytes, limits.max_input_tokens, limits.max_output_tokens))

    def _usage(self, response):
        usage = response.get('usage')
        if usage is None:
            return
        if type(usage) is not dict:
            raise TransportError('invalid_usage')
        required = ('input_tokens', 'output_tokens', 'total_tokens')
        if not set(required) <= usage.keys():
            raise TransportError('invalid_usage')
        inputs, outputs, total = (_nonnegative(usage[key]) for key in required)
        if total != inputs + outputs:
            raise TransportError('invalid_usage')
        details = []
        for field, key, cap in (('input_tokens_details', 'cached_tokens', inputs),
                                ('output_tokens_details', 'reasoning_tokens', outputs)):
            detail = usage.get(field)
            if detail is None:
                details.append(None)
            elif type(detail) is dict and key in detail:
                count = _nonnegative(detail[key])
                if count > cap:
                    raise TransportError('invalid_usage')
                details.append(count)
            else:
                raise TransportError('invalid_usage')
        # Observed usage is a separate report, NEVER returned to the free budget.
        self._attempts[-1] = replace(self._attempts[-1], outcome='reported',
            input_tokens=inputs, output_tokens=outputs, cached_input_tokens=details[0],
            reasoning_output_tokens=details[1])
        if inputs > self._limits.max_input_tokens or outputs > self._limits.max_output_tokens:
            raise TransportError('usage_limit')

    def _response(self, data):
        if type(data) is not bytes:
            raise TransportError('invalid_response')
        self._attempts[-1] = replace(self._attempts[-1], response_bytes=len(data))
        if len(data) > self._limits.max_response_bytes:
            raise TransportError('response_bytes_limit')
        response = _decode(data, self._limits.max_response_bytes)
        if type(response) is not dict:
            raise TransportError('invalid_response')
        self._usage(response)
        # No error message, nested payload, URL, or provider request ID survives.
        if response.get('error') is not None:
            error = response['error']
            code = error.get('code') if type(error) is dict else None
            mapped = {'rate_limit_exceeded': 'provider_rate_limited',
                      'server_error': 'provider_unavailable'}.get(code) if type(code) is str else None
            raise TransportError(mapped or 'provider_error')
        status = response.get('status')
        if status != 'completed':
            if status == 'incomplete':
                raise TransportError('provider_incomplete')
            if status in ('queued', 'in_progress'):
                raise TransportError('provider_pending')
            if status == 'cancelled':
                raise TransportError('provider_cancelled')
            raise TransportError('invalid_response')
        if response.get('incomplete_details') is not None:
            raise TransportError('provider_incomplete')
        if self._attempts[-1].outcome != 'reported':
            raise TransportError('missing_usage')
        if response.get('object') != 'response':
            raise TransportError('invalid_response')
        if response.get('model') != MODEL or response.get('service_tier') != 'default':
            raise TransportError('provider_configuration_mismatch')
        output = response.get('output')
        if type(output) is not list or len(output) > self._limits.max_output_items:
            raise TransportError('invalid_response')
        calls, messages, continuation, ids = [], [], [], set()
        for item in output:
            if type(item) is not dict:
                raise TransportError('invalid_response')
            kind = item.get('type')
            identifier = _identifier(item.get('id'))
            if identifier in ids or identifier in self._seen_item_ids:
                raise TransportError('invalid_response')
            ids.add(identifier)
            if kind == 'function_call':
                _keys(item, ('type', 'id', 'call_id', 'name', 'arguments'), ('status',))
                if item.get('status', 'completed') != 'completed':
                    raise TransportError('invalid_call')
                call_id = _identifier(item['call_id'], call=True)
                if call_id in self._seen_calls:
                    raise TransportError('duplicate_call')
                if type(item['name']) is not str or item['name'] not in TOOL_NAMES:
                    raise TransportError('unknown_tool')
                arguments = _text(item['arguments'], self._controller_limits.max_call_bytes, 'invalid_call')
                if type(_decode(arguments.encode('utf-8'), self._controller_limits.max_call_bytes,
                                'invalid_call')) is not dict:
                    raise TransportError('invalid_call')
                calls.append(ToolCall(call_id, item['name'], arguments))
                if len(calls) > 1:
                    raise TransportError('multiple_calls')
                continuation.append(item)
            elif kind == 'reasoning':
                # Only opaque encrypted continuity. No plaintext reasoning requested
                # or accepted; summaries must be empty, never logged/interpreted.
                _keys(item, ('type', 'id', 'summary', 'encrypted_content'), ('status', 'content'))
                if (item['summary'] != [] or item.get('status') not in (None, 'completed') or
                        item.get('content') not in (None, [])):
                    raise TransportError('unsupported_output')
                _text(item['encrypted_content'], self._limits.max_reasoning_bytes, nonempty=True)
                continuation.append({key: item[key] for key in
                    ('type', 'id', 'summary', 'encrypted_content')})
            elif kind == 'message':
                _keys(item, ('type', 'id', 'status', 'role', 'content'), ('phase',))
                if (item['status'] != 'completed' or item['role'] != 'assistant' or
                        item.get('phase') not in (None, 'commentary', 'final_answer') or
                        type(item['content']) is not list or not item['content'] or
                        len(item['content']) > self._limits.max_output_items):
                    raise TransportError('unsupported_output')
                texts, content = [], []
                for part in item['content']:
                    if type(part) is dict and part.get('type') == 'refusal':
                        raise TransportError('provider_refusal')
                    _keys(part, ('type', 'text', 'annotations'), ('logprobs',))
                    if part['type'] != 'output_text' or part['annotations'] != [] or part.get('logprobs') not in (None, []):
                        raise TransportError('unsupported_output')
                    texts.append(_text(part['text'], self._limits.max_text_bytes))
                    content.append({key: part[key] for key in ('type', 'text', 'annotations')})
                messages.append((_text(''.join(texts), self._limits.max_text_bytes), item.get('phase')))
                continuation.append({**item, 'content': content})
            else:
                raise TransportError('unsupported_output')
        if calls:
            if any(phase == 'final_answer' for _, phase in messages):
                raise TransportError('unsupported_output')
            return TurnReply(tuple(calls)), continuation
        finals = [(index, text) for index, (text, phase) in enumerate(messages) if phase != 'commentary']
        if len(finals) != 1 or not finals[0][1] or finals[0][0] != len(messages) - 1:
            raise TransportError('empty_response')
        return TurnReply(final_text=finals[0][1]), []

    def _time(self):
        try:
            value = self._clock()
            if (type(value) not in (int, float) or not -(2 ** 53) <= value <= 2 ** 53 or
                    not math.isfinite(value) or
                    (self._last_clock is not None and value < self._last_clock)):
                raise ValueError('invalid_clock')
        except Exception:
            raise TransportError('invalid_clock') from None
        self._last_clock = value
        return value

    def next_turn(self, request):
        if not self._lock.acquire(blocking=False):
            raise TransportError('concurrent_request')
        try:
            context, history, body = self._prepare(request)
            self._reserve(body)
            self._context = context
            self._retry_request = request
            start = self._time()
            try:
                count = self._counter.count_input(body)
            except Exception:
                raise TransportError('token_count_failure') from None
            if (type(count) is not OfflineInputCount or
                    count.request_sha256 != self._attempts[-1].request_sha256 or
                    type(count.input_tokens) is not int or count.input_tokens < 0):
                raise TransportError('invalid_token_count')
            if count.input_tokens > self._limits.max_input_tokens:
                raise TransportError('input_token_limit')
            remaining = self._limits.timeout_seconds - (self._time() - start)
            if remaining <= 0:
                raise TransportError('time_limit')
            self._attempts[-1] = replace(self._attempts[-1], outcome='unknown')
            try:
                data = self._exchange.respond(ResponsesRequest(body, len(self._attempts),
                    self._limits.max_response_bytes, remaining))
            except Exception:
                raise TransportError('exchange_failed') from None
            reply, continuation = self._response(data)
            if self._time() - start >= self._limits.timeout_seconds:
                raise TransportError('time_limit')
            new_history = _encode(history + continuation)
            if len(new_history) > self._limits.max_state_bytes:
                raise TransportError('state_limit')
            self._history = new_history
            self._seen_item_ids.update(item['id'] for item in continuation)
            self._transcript = request.transcript
            self._turn += 1
            self._retry_request = None
            if reply.calls:
                self._pending_call = reply.calls[0].call_id
                self._seen_calls.add(self._pending_call)
                self._pending_reply = _encode({'final_text': None, 'calls': [
                    {'call_id': call.call_id, 'name': call.name, 'arguments_json': call.arguments_json}
                    for call in reply.calls]})
            else:
                self._finished = True
            return reply
        finally:
            self._lock.release()
