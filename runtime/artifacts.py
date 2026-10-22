"""Year-neutral artifact identity and bounded process execution contracts."""
from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time

VERSION = "bcenv.artifact.v1"

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inventory(root):
    root = Path(root)
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Symlinks are not allowed: ' + str(path))
        if path.is_file():
            result[path.relative_to(root).as_posix()] = digest(path)
    return result


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def execute(command, output, timeout, stderr=None, *, deadline_monotonic=None):
    """Bound one process group; an optional same-host deadline spans many calls.

    Callers supervising this adapter must allow cleanup grace beyond the deadline
    so this process can kill and reap its separately-sessioned child first.
    """
    if deadline_monotonic is not None and (type(deadline_monotonic) not in (int, float)
            or not math.isfinite(deadline_monotonic)):
        raise ValueError('deadline_monotonic must be a finite absolute monotonic time')
    start = time.monotonic()
    launch_error = None
    termination_requested = reaped = None
    with ExitStack() as stack:
        log = stack.enter_context(Path(output).open('wb'))
        errors = stack.enter_context(Path(stderr).open('wb')) if stderr else subprocess.STDOUT
        launch_checked = time.monotonic() if deadline_monotonic is not None else None
        not_started = launch_checked is not None and launch_checked >= deadline_monotonic
        try:
            proc = None if not_started else subprocess.Popen(command, stdout=log, stderr=errors, start_new_session=True)
        except OSError as error:
            proc = None
            launch_error = str(error)
        try:
            wait_timeout = timeout if deadline_monotonic is None else min(timeout, max(0., deadline_monotonic - time.monotonic()))
            code = proc.wait(timeout=wait_timeout) if proc else None
            status = 'timeout' if not_started else ('succeeded' if code == 0 else 'failed')
        except subprocess.TimeoutExpired:
            if deadline_monotonic is not None:
                termination_requested = time.monotonic()
            os.killpg(proc.pid, signal.SIGKILL)
            code = proc.wait()
            status = 'timeout'
        except BaseException:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise
        if deadline_monotonic is not None and proc is not None:
            reaped = time.monotonic()
    finished = time.monotonic()
    result = {'command': command, 'exit_code': code, 'status': status,
            'elapsed_seconds': round(finished - start, 3), 'log': Path(output).name,
            'log_sha256': digest(output)}
    if deadline_monotonic is not None:
        # These are application-side observations, not exact kernel spawn/kill
        # instants. reaped is sampled only after wait() has returned.
        result.update(deadline_monotonic=deadline_monotonic,
                      launch_checked_monotonic=launch_checked,
                      finished_monotonic=finished, child_reaped=reaped is not None)
        if reaped is not None:
            result['reaped_monotonic'] = reaped
        if termination_requested is not None:
            result['termination_requested_monotonic'] = termination_requested
    if not_started:
        result['not_started'] = True
    if launch_error:
        result['launch_error'] = launch_error
    if stderr:
        result['stderr_log'] = Path(stderr).name
        result['stderr_sha256'] = digest(stderr)
    return result


def new_output(path):
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=False)
    return path
