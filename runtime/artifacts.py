"""Year-neutral artifact identity and bounded process execution contracts."""
from contextlib import ExitStack
import hashlib
import json
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


def execute(command, output, timeout, stderr=None):
    start = time.monotonic()
    launch_error = None
    with ExitStack() as stack:
        log = stack.enter_context(Path(output).open('wb'))
        errors = stack.enter_context(Path(stderr).open('wb')) if stderr else subprocess.STDOUT
        try:
            proc = subprocess.Popen(command, stdout=log, stderr=errors, start_new_session=True)
        except OSError as error:
            proc = None
            launch_error = str(error)
        try:
            code = proc.wait(timeout=timeout) if proc else None
            status = 'succeeded' if code == 0 else 'failed'
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            code = proc.wait()
            status = 'timeout'
        except BaseException:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise
    result = {'command': command, 'exit_code': code, 'status': status,
            'elapsed_seconds': round(time.monotonic() - start, 3), 'log': Path(output).name,
            'log_sha256': digest(output)}
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
