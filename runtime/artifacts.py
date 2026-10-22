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
from runtime.cancellation import cancelled

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


def execute(command, output, timeout, stderr=None, *, deadline_monotonic=None, cancel_fd=None,
            cooperative_child=False, on_interrupt=None, ownership=None):
    """Bound one process group; an optional same-host deadline spans many calls.

    Callers supervising this adapter must allow cleanup grace beyond the deadline
    so this process can kill and reap its separately-sessioned child first.
    """
    if deadline_monotonic is not None and (type(deadline_monotonic) not in (int, float)
            or not math.isfinite(deadline_monotonic)):
        raise ValueError('deadline_monotonic must be a finite absolute monotonic time')
    ownership = {} if ownership is None else ownership
    def waited(proc, timeout=None):
        code = proc.wait(timeout=timeout) if timeout is not None else proc.wait()
        ownership["direct_child_wait_completed"] = True
        return code

    start = time.monotonic()
    launch_error = None
    termination_requested = reaped = None
    with ExitStack() as stack:
        log = stack.enter_context(Path(output).open('wb'))
        errors = stack.enter_context(Path(stderr).open('wb')) if stderr else subprocess.STDOUT
        launch_checked = time.monotonic() if deadline_monotonic is not None else None
        was_cancelled = cancelled(cancel_fd)
        not_started = was_cancelled or (launch_checked is not None and launch_checked >= deadline_monotonic)
        cutoff = min(start + timeout, deadline_monotonic) if deadline_monotonic is not None else start + timeout
        proc = None
        try:
            try:
                proc = None if not_started else subprocess.Popen(command, stdout=log, stderr=errors, start_new_session=True,
                    **({"pass_fds": (cancel_fd,)} if cooperative_child and cancel_fd is not None else {}))
            except OSError as error:
                launch_error = str(error)
            ownership["not_started"] = proc is None
            if proc is None:
                code = None
                status = 'cancelled' if was_cancelled else ('timeout' if not_started else 'failed')
            elif cancel_fd is None:
                wait_timeout = timeout if deadline_monotonic is None else min(timeout, max(0., deadline_monotonic - time.monotonic()))
                code = waited(proc, timeout=wait_timeout)
                status = 'succeeded' if code == 0 else 'failed'
            else:
                while True:
                    if cancelled(cancel_fd):
                        if not was_cancelled:
                            was_cancelled = True
                            if cooperative_child:
                                # Let the trusted adapter reap its own detached children.
                                cutoff = min(cutoff, time.monotonic() + 5.)
                        if not cooperative_child:
                            status = 'cancelled'
                            break
                    remaining = cutoff - time.monotonic()
                    if remaining <= 0:
                        status = 'timeout'
                        break
                    try:
                        code = waited(proc, timeout=min(.1, remaining))
                        status = 'succeeded' if code == 0 else 'failed'
                        break
                    except subprocess.TimeoutExpired:
                        continue
                if status in ('timeout', 'cancelled'):
                    termination_requested = time.monotonic()
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    code = waited(proc)
        except subprocess.TimeoutExpired:
            termination_requested = time.monotonic()
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            code = waited(proc)
            status = 'timeout'
        except BaseException:
            if cooperative_child and on_interrupt is not None:
                on_interrupt()
            if proc is not None and not ownership.get("direct_child_wait_completed"):
                if cooperative_child and on_interrupt is not None:
                    try:
                        waited(proc, timeout=5.)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        waited(proc)
                else:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    waited(proc)
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
