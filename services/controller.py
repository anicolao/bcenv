"""Synthetic fixed-tool controller harness, not a real-agent launcher.

The supported-provider transport seam is a typed ``next_turn(TurnRequest)``
interface. This module implements no provider SDK, inference call, credentials,
listener, subprocess, shell, filesystem access or network access. A future
provider adapter must preserve the exact frozen initial UTF-8 bytes, register
only TOOL_NAMES, and return structured calls; prompt instructions alone do not
constrain tools. ``launch_real`` remains permanently closed.

Workspace/scrimmage adapters below are trusted-side integration seams. They must
not be installed from model input. Their synthetic doubles prove dispatch and
schema logic only, not actual tool/filesystem/network isolation. The controller
cannot preempt a blocked Python callback: wall deadlines are checked before and
after every boundary call; real adapters need independently enforced RPC/runtime
limits. Process-local budgets are not a secure execution sandbox.

``workspace_exec`` passes bounded argv and a workspace-relative cwd ONLY to the
trusted SandboxAdapter.exec seam. Shell/test programs are permitted as opaque
sandbox inputs; this module never executes them. Model arguments cannot choose
a host launcher, mounts, inherited environment, network or sandbox configuration.
A packaged source result is an opaque artifact handle; submission accepts that
handle, never a host source path. Real sandbox enforcement is not implemented.
"""
from dataclasses import dataclass, field
import hashlib
import json
import re
import time
from typing import Protocol

from services.scrimmage import public_result

TOOL_NAMES = ('workspace_read', 'workspace_write', 'workspace_exec',
              'scrimmage_submit', 'scrimmage_result', 'scrimmage_replay')
_HANDLE = re.compile(r'a_[0-9a-f]{32}\Z')
_SUBMISSION = re.compile(r's_[0-9a-f]{32}\Z')
_CALL_ID = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
_COMPONENT = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]*\Z')
_PACKAGE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*\Z')
_READ_ROOTS = ('src', 'scratch', 'docs', 'artifacts', 'self_play')
_WRITE_ROOTS = ('src', 'scratch')
_ERRORS = frozenset(('invalid_arguments', 'unknown_tool', 'invalid_adapter_response', 'unknown_handle'))
_RESERVED = frozenset(['con', 'prn', 'aux', 'nul'] +
    [prefix + str(n) for prefix in ('com', 'lpt') for n in range(1, 10)])


@dataclass(frozen=True)
class ControllerLimits:
    max_turns: int = 16
    max_calls_per_turn: int = 4
    max_tool_calls: int = 64
    max_wall_seconds: int = 30
    max_initial_bytes: int = 65536
    max_call_bytes: int = 1048576
    max_model_reply_bytes: int = 4194304
    max_write_bytes: int = 524288
    max_read_bytes: int = 8192
    max_read_offset: int = 1048576
    max_tool_output_bytes: int = 16384
    max_total_output_bytes: int = 131072
    max_argv_items: int = 64
    max_argv_bytes: int = 16384
    max_argument_bytes: int = 4096
    exec_wall_seconds: int = 10
    exec_memory_bytes: int = 268435456

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in vars(self).values()):
            raise ValueError('invalid_controller_limits')
        if self.max_tool_output_bytes < 128:
            raise ValueError('invalid_controller_limits')


@dataclass(frozen=True)
class FrozenContext:
    """Exact caller-supplied UTF-8 bytes; no normalization or hidden appendices."""
    system: bytes
    task: bytes
    sha256: str = field(init=False)

    def __post_init__(self):
        if type(self.system) is not bytes or type(self.task) is not bytes:
            raise ValueError('initial_context_requires_bytes')
        self.system.decode('utf-8')
        self.task.decode('utf-8')
        framed = b'bcenv.controller-context.v1\x00'
        for part in (self.system, self.task):
            framed += len(part).to_bytes(8, 'big') + part
        object.__setattr__(self, 'sha256', hashlib.sha256(framed).hexdigest())


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class TurnReply:
    calls: tuple = ()
    final_text: str | None = None


@dataclass(frozen=True)
class TurnRequest:
    context: FrozenContext
    tool_names: tuple
    tool_schemas: tuple
    transcript: tuple
    turn: int


@dataclass(frozen=True)
class ReadRequest:
    path: str
    offset: int
    limit: int


@dataclass(frozen=True)
class WriteRequest:
    path: str
    content: bytes


@dataclass(frozen=True)
class ExecRequest:
    argv: tuple
    cwd: str
    wall_seconds: int
    memory_bytes: int
    output_bytes: int


@dataclass(frozen=True)
class ExecReply:
    status: str
    stdout: bytes = b''
    stderr: bytes = b''
    artifact_id: str | None = None


@dataclass(frozen=True)
class SubmitRequest:
    artifact_id: str
    package: str


@dataclass(frozen=True)
class ResultRequest:
    submission_id: str


@dataclass(frozen=True)
class ScrimmageReply:
    submission_id: str
    evidence: dict


class ModelTransport(Protocol):
    def next_turn(self, request: TurnRequest) -> TurnReply: ...


class SandboxAdapter(Protocol):
    def read(self, request: ReadRequest) -> bytes: ...
    def write(self, request: WriteRequest) -> None: ...
    def exec(self, request: ExecRequest) -> ExecReply: ...


class ScrimmageAdapter(Protocol):
    def submit(self, request: SubmitRequest) -> ScrimmageReply: ...
    def result(self, request: ResultRequest) -> ScrimmageReply: ...
    def replay(self, request: ResultRequest) -> None: ...


@dataclass(frozen=True)
class RunReport:
    status: str
    initial_sha256: str
    turns: int
    tool_calls: int
    transcript: tuple
    final_text: str | None = None


class InvalidCall(ValueError):
    pass


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidCall('invalid_arguments')
        result[key] = value
    return result


def _constant(unused):
    raise InvalidCall('invalid_arguments')


def _utf8(value):
    try:
        return value.encode('utf-8')
    except UnicodeError:
        raise InvalidCall('invalid_arguments') from None


def _arguments(call, limits):
    if type(call.arguments_json) is not str or len(_utf8(call.arguments_json)) > limits.max_call_bytes:
        raise InvalidCall('invalid_arguments')
    try:
        arguments = json.loads(call.arguments_json, object_pairs_hook=_object, parse_constant=_constant)
    except (ValueError, RecursionError):
        raise InvalidCall('invalid_arguments') from None
    if type(arguments) is not dict:
        raise InvalidCall('invalid_arguments')
    return arguments


def _keys(arguments, expected):
    if set(arguments) != set(expected):
        raise InvalidCall('invalid_arguments')


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise InvalidCall('invalid_arguments')
    return value


def _path(value, writable=False):
    if type(value) is not str or not value.isascii() or len(value) > 240:
        raise InvalidCall('invalid_arguments')
    parts = value.split('/')
    if (len(parts) < 2 or len(parts) > 16 or
            parts[0] not in (_WRITE_ROOTS if writable else _READ_ROOTS) or
            any(part in ('.', '..') or part.endswith('.') or
                part.casefold().split('.')[0] in _RESERVED or
                not _COMPONENT.fullmatch(part) for part in parts)):
        raise InvalidCall('invalid_arguments')
    return value


def _cwd(value):
    # Unlike a file path, a fresh workspace root needs no existing leaf.
    if type(value) is str and (value == '.' or value in _READ_ROOTS):
        return value
    return _path(value)


def _handle(value, pattern):
    if type(value) is not str or not pattern.fullmatch(value):
        raise InvalidCall('invalid_arguments')
    return value


def _text(data, cap):
    if type(data) is not bytes:
        raise InvalidCall('invalid_adapter_response')
    clipped = data[:cap]
    return clipped.decode('utf-8', errors='replace'), len(data) > cap


def _dispatch(call, workspace, scrimmage, limits, artifacts, submissions):
    if type(call.name) is not str or call.name not in TOOL_NAMES:
        raise InvalidCall('unknown_tool')
    arguments = _arguments(call, limits)
    if call.name == 'workspace_read':
        _keys(arguments, ('path', 'offset', 'limit'))
        request = ReadRequest(_path(arguments['path']),
                              _integer(arguments['offset'], 0, limits.max_read_offset),
                              _integer(arguments['limit'], 1, limits.max_read_bytes))
        output = workspace.read(request)
        if type(output) is not bytes or len(output) > request.limit:
            raise InvalidCall('invalid_adapter_response')
        text, _ = _text(output, request.limit)
        return {'status': 'complete', 'text': text}
    if call.name == 'workspace_write':
        _keys(arguments, ('path', 'content'))
        path = _path(arguments['path'], writable=True)
        content = arguments['content']
        if type(content) is not str or len(_utf8(content)) > limits.max_write_bytes:
            raise InvalidCall('invalid_arguments')
        content = _utf8(content)
        if workspace.write(WriteRequest(path, content)) is not None:
            raise InvalidCall('invalid_adapter_response')
        return {'status': 'complete', 'bytes_written': len(content)}
    if call.name == 'workspace_exec':
        _keys(arguments, ('argv', 'cwd', 'timeout_seconds'))
        argv = arguments['argv']
        if (type(argv) is not list or not 1 <= len(argv) <= limits.max_argv_items or
                any(type(arg) is not str or '\x00' in arg or
                    len(_utf8(arg)) > limits.max_argument_bytes for arg in argv) or
                not argv[0] or sum(len(_utf8(arg)) for arg in argv) > limits.max_argv_bytes):
            raise InvalidCall('invalid_arguments')
        cwd = _cwd(arguments['cwd'])
        timeout = _integer(arguments['timeout_seconds'], 1, limits.exec_wall_seconds)
        result = workspace.exec(ExecRequest(tuple(argv), cwd, timeout,
                                           limits.exec_memory_bytes, limits.max_tool_output_bytes))
        if type(result) is not ExecReply or type(result.status) is not str or result.status not in ('succeeded', 'failed', 'timeout'):
            raise InvalidCall('invalid_adapter_response')
        stdout, clipped_out = _text(result.stdout, limits.max_tool_output_bytes // 4)
        stderr, clipped_err = _text(result.stderr, limits.max_tool_output_bytes // 4)
        response = {'status': result.status, 'stdout': stdout, 'stderr': stderr,
                    'truncated': clipped_out or clipped_err}
        if result.artifact_id is not None:
            if result.status != 'succeeded':
                raise InvalidCall('invalid_adapter_response')
            response['artifact_id'] = _handle(result.artifact_id, _HANDLE)
            artifacts.add(result.artifact_id)
        return response
    if call.name == 'scrimmage_submit':
        _keys(arguments, ('artifact_id', 'package'))
        artifact_id = _handle(arguments['artifact_id'], _HANDLE)
        if artifact_id not in artifacts:
            raise InvalidCall('unknown_handle')
        package = arguments['package']
        if type(package) is not str or len(package) > 240 or not _PACKAGE.fullmatch(package):
            raise InvalidCall('invalid_arguments')
        reply = scrimmage.submit(SubmitRequest(artifact_id, package))
    else:
        _keys(arguments, ('submission_id',))
        submission_id = _handle(arguments['submission_id'], _SUBMISSION)
        if submission_id not in submissions:
            raise InvalidCall('unknown_handle')
        request = ResultRequest(submission_id)
        if call.name == 'scrimmage_replay':
            # The facade has no replay publication path; never trust raw adapter
            # data or forward bytes/URLs even if an adapter returns some.
            if scrimmage.replay(request) is not None:
                raise InvalidCall('invalid_adapter_response')
            return {'schema': 'bcenv.scrimmage.v1', 'submission_id': submission_id,
                    'status': 'unavailable', 'reason': 'replay_withheld'}
        reply = scrimmage.result(request)
        if type(reply) is not ScrimmageReply or reply.submission_id != submission_id:
            raise InvalidCall('invalid_adapter_response')
    if type(reply) is not ScrimmageReply:
        raise InvalidCall('invalid_adapter_response')
    result = public_result(reply.submission_id, reply.evidence, max_bytes=limits.max_tool_output_bytes)
    if call.name == 'scrimmage_submit':
        submissions.add(reply.submission_id)
    return result


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')


def tool_schemas(limits=None):
    """Immutable provider-neutral JSON-schema registry for a future API adapter."""
    limits = limits or ControllerLimits()
    text = {'type': 'string'}
    path = {'type': 'string', 'maxLength': 240}
    definitions = (
        ('workspace_read', {'path': path, 'offset': {'type': 'integer', 'minimum': 0, 'maximum': limits.max_read_offset},
                            'limit': {'type': 'integer', 'minimum': 1, 'maximum': limits.max_read_bytes}}),
        ('workspace_write', {'path': path, 'content': text}),
        ('workspace_exec', {'argv': {'type': 'array', 'items': {'type': 'string', 'maxLength': limits.max_argument_bytes},
                                    'minItems': 1, 'maxItems': limits.max_argv_items},
                            'cwd': path, 'timeout_seconds': {'type': 'integer', 'minimum': 1, 'maximum': limits.exec_wall_seconds}}),
        ('scrimmage_submit', {'artifact_id': {'type': 'string', 'pattern': '^a_[0-9a-f]{32}$'}, 'package': path}),
        ('scrimmage_result', {'submission_id': {'type': 'string', 'pattern': '^s_[0-9a-f]{32}$'}}),
        ('scrimmage_replay', {'submission_id': {'type': 'string', 'pattern': '^s_[0-9a-f]{32}$'}}),
    )
    return tuple(_encoded({'name': name, 'parameters': {'type': 'object',
        'properties': fields, 'required': list(fields), 'additionalProperties': False}})
        for name, fields in definitions)


def run_synthetic(context, transport, workspace, scrimmage, *, limits=None, clock=time.monotonic):
    """Run bounded protocol probes using trusted synthetic adapters only.

    The transport sees fixed tool names and typed messages, not controller,
    adapter, filesystem or environment objects. Tool input remains untrusted.
    Returned statuses describe the controller loop, never game/bot strength.
    """
    limits = limits or ControllerLimits()
    if type(context) is not FrozenContext or len(context.system) + len(context.task) > limits.max_initial_bytes:
        raise ValueError('initial_context_limit')
    transcript, calls_seen, used_calls, used_output = [], set(), 0, 0
    artifacts, submissions = set(), set()
    schemas = tool_schemas(limits)
    start = clock()
    turns = 0

    def report(status, text=None):
        return RunReport(status, context.sha256, turns, used_calls, tuple(transcript), text)

    def expired():
        return clock() - start >= limits.max_wall_seconds

    for turn in range(1, limits.max_turns + 1):
        if expired():
            return report('time_limit')
        turns = turn
        try:
            reply = transport.next_turn(TurnRequest(context, TOOL_NAMES, schemas, tuple(transcript), turn))
        except Exception:
            return report('transport_failure')
        if expired():
            return report('time_limit')
        if (type(reply) is not TurnReply or type(reply.calls) is not tuple or
                len(reply.calls) > limits.max_calls_per_turn or
                (reply.final_text is not None and (type(reply.final_text) is not str or reply.calls))):
            return report('invalid_model_reply')
        if (reply.final_text is not None and len(reply.final_text) > limits.max_model_reply_bytes or
                any(type(call) is not ToolCall or type(call.call_id) is not str or
                    type(call.name) is not str or type(call.arguments_json) is not str or
                    len(call.call_id) > 64 or len(call.name) > 64 or
                    len(call.arguments_json) > limits.max_call_bytes for call in reply.calls)):
            return report('invalid_model_reply')
        try:
            if reply.final_text is not None:
                reply.final_text.encode('utf-8')
            reply_bytes = _encoded({'final_text': reply.final_text, 'calls': [
                {'call_id': call.call_id, 'name': call.name, 'arguments_json': call.arguments_json}
                for call in reply.calls if type(call) is ToolCall]})
        except (ValueError, TypeError, UnicodeError):
            return report('invalid_model_reply')
        if len(reply_bytes) > limits.max_model_reply_bytes or any(type(call) is not ToolCall for call in reply.calls):
            return report('invalid_model_reply')
        if reply.final_text is not None:
            return report('complete', reply.final_text)
        if not reply.calls:
            return report('invalid_model_reply')
        # Validate call identifiers for the whole turn before any adapter side
        # effect. A malformed/duplicate ID later in a batch cannot partly run it.
        turn_ids = [call.call_id for call in reply.calls]
        if (len(set(turn_ids)) != len(turn_ids) or
                any(not _CALL_ID.fullmatch(identifier) or identifier in calls_seen for identifier in turn_ids)):
            return report('invalid_model_reply')
        transcript.append(reply_bytes)
        for call in reply.calls:
            if used_calls >= limits.max_tool_calls:
                return report('tool_call_limit')
            if expired():
                return report('time_limit')
            if type(call.call_id) is not str or not _CALL_ID.fullmatch(call.call_id) or call.call_id in calls_seen:
                return report('invalid_model_reply')
            calls_seen.add(call.call_id)
            used_calls += 1
            try:
                output = _dispatch(call, workspace, scrimmage, limits, artifacts, submissions)
            except InvalidCall as error:
                output = {'status': 'rejected', 'reason': str(error) if str(error) in _ERRORS else 'invalid_adapter_response'}
            except Exception:
                output = {'status': 'infrastructure_failure', 'reason': 'adapter_failure'}
            if expired():
                return report('time_limit')
            encoded = _encoded({'call_id': call.call_id, 'result': output})
            if len(encoded) > limits.max_tool_output_bytes:
                encoded = _encoded({'call_id': call.call_id,
                                    'result': {'status': 'rejected', 'reason': 'tool_output_limit'}})
            if (len(encoded) > limits.max_tool_output_bytes or
                    used_output + len(encoded) > limits.max_total_output_bytes):
                return report('output_limit')
            used_output += len(encoded)
            transcript.append(encoded)
    return report('turn_limit')


def launch_real(*args, **kwargs):
    """Unsupported until an independently tested boundary/transport is built."""
    raise RuntimeError('real_launch_unsupported')
