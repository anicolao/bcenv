"""Evaluator-owned, panel-free single-admission glue. No schedule or retry loop.

Only use in a separately preregistered future protocol. Never import into an
author export. The caller owns immutable input verification and ordinary valid
replay scoring; timeout qualification belongs to evaluation_timeout_policy.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time

MAX_RECEIPT = 1024 * 1024
ACK = {'schema': 'bcenv.cooperative-cleanup.v1',
       'direct_child_waits_completed': True,
       'scope': 'normal adapter return only; not descendant containment'}


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate JSON key')
        value[key] = item
    return value


def _option(command, name):
    if command.count(name) != 1:
        raise ValueError('missing or repeated option')
    pos = command.index(name)
    if pos + 1 >= len(command):
        raise ValueError('option lacks value')
    return command[pos + 1]


def _read(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('receipt missing or symlink')
    with path.open('rb') as stream:
        data = stream.read(MAX_RECEIPT + 1)
    if len(data) > MAX_RECEIPT:
        raise ValueError('receipt exceeds bound')
    return data


def run_assignment(expected, invocation_dir, *, validate_expected, validate_inputs, validate_artifacts,
                   publish_censored, run=subprocess.run, clock=time.monotonic):
    """Execute once; a retained admission directory forbids accidental reuse.

    validate_artifacts(expected, output_dir, result_bytes) is trusted observer
    code that rechecks the pinned inputs, actual output contract and partial
    artifact paths. It MUST verify the actual process.command against pinned
    Java executable, heap512, engine classpath/main/config recipe, not only
    map/team metadata. Runtime identity text alone is not executable proof. It must not infer validity from the exit status. Exceptions
    or a non-True return stop. This function never scores a completed replay.
    publish_censored must be the reviewed policy implementation, not a boolean
    classifier. Its durable receipt is the only timeout continuation authority.
    """
    validate_expected(expected, now_monotonic=clock())
    command = list(expected['command'])
    # Accept one canonical run grammar only: argparse accepts equals forms and
    # abbreviations, so checking a few exact tokens is insufficient.
    if command.count('run') != 1:
        raise ValueError('one canonical run subcommand required')
    tail = command[command.index('run') + 1:]
    allowed = {'--team-a', '--team-b', '--map', '--output', '--timeout',
               '--deadline-monotonic', '--cleanup-ack'}
    if len(tail) != 2 * len(allowed):
        raise ValueError('noncanonical run arguments')
    options = tail[::2]
    if set(options) != allowed or len(set(options)) != len(options):
        raise ValueError('unknown, abbreviated, equals-form or duplicate option')
    if any(not isinstance(value, str) or value.startswith('--') for value in tail[1::2]):
        raise ValueError('invalid canonical option value')
    deadline = expected['global_deadline_monotonic']
    now = clock()
    if not math.isfinite(deadline) or not math.isfinite(now) or now >= deadline:
        return {'status': 'stop', 'reason': 'global_deadline'}
    if float(_option(command, '--deadline-monotonic')) != deadline:
        raise ValueError('original global deadline mismatch')
    if float(_option(command, '--timeout')) != expected['per_match_seconds']:
        raise ValueError('per-match cap mismatch')
    if '--cancel-fd' in command:
        raise ValueError('cooperative cancel path is not qualified')
    directory = Path(invocation_dir).absolute()
    output = directory / 'output'
    ack = directory / 'cleanup.json'
    if Path(_option(command, '--output')).absolute() != output:
        raise ValueError('output does not bind fresh invocation')
    if Path(_option(command, '--cleanup-ack')).absolute() != ack:
        raise ValueError('ack does not bind fresh invocation')
    # No exist_ok: any previous admission, even ambiguous/partial, forbids retry.
    if validate_inputs(expected) is not True:
        return {'status': 'stop', 'reason': 'input_validation'}
    directory.mkdir()
    admission = json.dumps(expected, sort_keys=True, allow_nan=False).encode()
    with (directory / 'admission.json').open('xb') as stream:
        stream.write(admission); stream.flush(); os.fsync(stream.fileno())
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    parent_fd = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    try:
        remaining = deadline - clock()
        if remaining <= 0:
            return {'status': 'stop', 'reason': 'global_deadline_after_admission'}
        with (directory / 'adapter.log').open('xb') as log:
            completed = run(command, stdout=log, stderr=subprocess.STDOUT,
                            timeout=remaining, check=False)
        waited = clock()  # only after synchronous run returned its real wait
        result_bytes = _read(output / 'result.json')
        ack_bytes = _read(ack)
        ack_value = json.loads(ack_bytes, object_pairs_hook=_unique_pairs)
        if ack_value != ACK or ack_value.get('direct_child_waits_completed') is not True:
            return {'status': 'stop', 'reason': 'cleanup_ack_invalid'}
        if validate_artifacts(expected, output, result_bytes) is not True:
            return {'status': 'stop', 'reason': 'artifact_validation'}
        result = json.loads(result_bytes)
        if completed.returncode == 0 and result.get('status') == 'completed':
            # Ordinary terminal-replay validation remains mandatory separately.
            return {'status': 'completed_unscored', 'outer_wait_monotonic': waited, 'result_sha256': hashlib.sha256(result_bytes).hexdigest()}
        evidence = {k: expected[k] for k in ('assignment_id', 'index', 'debit_id', 'runtime_identity', 'command')}
        evidence.update(result_bytes=result_bytes, ack_bytes=ack_bytes,
                        outer_wait_completed=True, outer_returncode=completed.returncode,
                        outer_wait_monotonic=waited, ownership_unambiguous=True,
                        artifacts_validated=True, other_infrastructure_failure=False)
        receipt = publish_censored(directory / 'censored.json', expected, evidence, clock=clock)
        fresh = clock()
        if receipt.get('eligible_next_untouched') is not True or not math.isfinite(fresh) or fresh < waited or fresh >= deadline:
            return {'status': 'stop', 'reason': 'censored_without_remaining_authority', 'receipt': receipt}
        return {'status': 'timeout_censored', 'next_original_index': expected['index'] + 1, 'receipt': receipt}
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        # Timeout/transport/persistence ambiguities are never repaired or retried.
        return {'status': 'stop', 'reason': type(error).__name__}
