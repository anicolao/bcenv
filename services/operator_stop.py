"""Opt-in cooperative operator-file stop; no authentication or process control.

The watcher reads one fixed file and calls only trial.request_cancel(). Main owns
worker cleanup and receipts. A one-second join is a check, not a guarantee against
stalled filesystem/kernel work. Never publish successful cleanup after an error.
"""
import json
import os
from pathlib import Path
import re
import stat
import threading
import time

SCHEMA = 'bcenv.operator-stop.v1'
MAX_BYTES = 256
POLL_SECONDS = 0.1
JOIN_SECONDS = 1.0
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_RUN_ID = re.compile(r'[0-9a-f]{32}\Z')


def _open_directory(path):
    fd = os.open('/', _DIR_FLAGS)
    try:
        for part in path.parts[1:]:
            child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def validate_control_args(directory, run_id):
    """Validate paired optional flags before probes; do not create directories."""
    if directory is None and run_id is None:
        return None
    if directory is None or type(run_id) is not str or _RUN_ID.fullmatch(run_id) is None:
        raise ValueError('invalid_operator_control')
    try:
        path = Path(os.path.abspath(os.fspath(directory)))
        fd = _open_directory(path.parent)
        try:
            try:
                os.stat(path.name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                return path, run_id
            raise ValueError('operator_control_not_fresh')
        finally:
            os.close(fd)
    except (OSError, TypeError):
        raise ValueError('invalid_operator_control') from None


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


class OperatorStop:
    def __init__(self, trial, directory, run_id, forbidden_roots=()):
        selected = validate_control_args(directory, run_id)
        if selected is None:
            raise ValueError('invalid_operator_control')
        self.directory, self.run_id = selected
        for forbidden in forbidden_roots:
            root = Path(os.path.abspath(os.fspath(forbidden)))
            if (root == self.directory or root in self.directory.parents or
                    self.directory in root.parents):
                raise ValueError('operator_control_scope_overlap')
        self.trial = trial
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._started = False
        self._joined = False
        self._observed = False
        self._valid = False
        self._cancel_called = False
        self._cancel_failed = False
        self._error = None
        self._observed_ns = None
        self._cancel_requested_ns = None
        self._cancel_returned_ns = None
        self._joined_ns = None
        self._fd = None
        parent = _open_directory(self.directory.parent)
        failed = False
        try:
            os.mkdir(self.directory.name, mode=0o700, dir_fd=parent)
            self._fd = os.open(self.directory.name, _DIR_FLAGS, dir_fd=parent)
            info = os.fstat(self._fd)
            self._identity = info.st_dev, info.st_ino
        except OSError:
            failed = True
        finally:
            try:
                os.close(parent)
            except OSError:
                failed = True
        if failed:
            if self._fd is not None:
                fd, self._fd = self._fd, None
                try:
                    os.close(fd)
                except OSError:
                    pass  # Bounded best effort; do not mask constructor failure.
            raise ValueError('invalid_operator_control') from None

    def _fail(self, code):
        with self._lock:
            if self._error is None:
                self._error = code
        self._request_cancel()

    def _request_cancel(self):
        with self._lock:
            if self._cancel_called:
                return
            self._cancel_called = True
            self._cancel_requested_ns = time.monotonic_ns()
        try:
            # Deliberately no trial.lock, cancel(), close(), audit, or persistence.
            self.trial.request_cancel()
        except BaseException:
            with self._lock:
                self._cancel_failed = True
                if self._error is None:
                    self._error = 'operator_cancel_failed'
        else:
            with self._lock:
                self._cancel_returned_ns = time.monotonic_ns()

    def _read(self):
        # Verify the fixed directory still names our retained no-follow inode.
        directory = _open_directory(self.directory)
        try:
            info = os.fstat(directory)
            if (info.st_dev, info.st_ino) != self._identity:
                raise OSError('operator directory changed')
        finally:
            os.close(directory)
        try:
            fd = os.open('cancel.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self._fd)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                raise ValueError('invalid operator record')
            data = bytearray()
            while len(data) <= MAX_BYTES:
                chunk = os.read(fd, MAX_BYTES + 1 - len(data))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise ValueError('invalid operator record')
            after = os.fstat(fd)
            if (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
                raise ValueError('operator record changed')
            return bytes(data)
        finally:
            os.close(fd)

    def _observe(self):
        """One bounded poll; internal seam also permits pure deterministic tests."""
        with self._lock:
            if self._observed or self._error is not None:
                return True
        try:
            data = self._read()
        except (OSError, ValueError):
            with self._lock:
                self._observed = True
                self._observed_ns = time.monotonic_ns()
            self._fail('operator_control_read_failed')
            return True
        if data is None:
            return False
        with self._lock:
            self._observed = True
            self._observed_ns = time.monotonic_ns()
        try:
            request = json.loads(data.decode('utf-8'), object_pairs_hook=_unique)
            valid = (type(request) is dict and set(request) == {'schema', 'run_id', 'operation'} and
                     request['schema'] == SCHEMA and request['run_id'] == self.run_id and
                     request['operation'] == 'cancel')
        except (UnicodeError, ValueError, RecursionError):
            valid = False
        if not valid:
            self._fail('operator_control_invalid')
        else:
            with self._lock:
                self._valid = True
            self._request_cancel()
        return True

    def _run(self):
        try:
            while not self._stop.is_set():
                if self._observe():
                    return
                self._stop.wait(POLL_SECONDS)
        except BaseException:
            self._fail('operator_watcher_failed')

    def start(self):
        with self._lock:
            if self._started or self._joined:
                raise ValueError('operator_watcher_already_started')
            self._started = True
        try:
            self._thread = threading.Thread(target=self._run, name='bcenv-operator-stop', daemon=True)
            self._thread.start()
        except BaseException:
            self._fail('operator_watcher_start_failed')
            self.raise_if_failed()
        return self

    def stop_and_join(self):
        with self._lock:
            joined = self._joined
        if joined:
            self.raise_if_failed()
            return
        self._stop.set()
        if self._thread is not None:
            try:
                self._thread.join(JOIN_SECONDS)
                alive = self._thread.is_alive()
            except BaseException:
                self._fail('operator_watcher_join_failed')
                try:
                    inactive = self._thread.is_alive() is False
                except BaseException:
                    inactive = False
                # A conclusively inactive (e.g. never-started) watcher cannot
                # still use this descriptor. This is not a successful join.
                if inactive and self._fd is not None:
                    fd, self._fd = self._fd, None
                    try:
                        os.close(fd)
                    except OSError:
                        self._fail('operator_control_close_failed')
                self.raise_if_failed()
            if alive:
                self._fail('operator_watcher_join_timeout')
                self.raise_if_failed()
        with self._lock:
            self._joined = True
            if self._joined_ns is None:
                self._joined_ns = time.monotonic_ns()
        if self._fd is not None:
            fd, self._fd = self._fd, None
            try:
                os.close(fd)
            except OSError:
                self._fail('operator_control_close_failed')
        self.raise_if_failed()

    def raise_if_failed(self):
        with self._lock:
            error = self._error
        if error is not None:
            raise ValueError(error)

    def snapshot(self):
        with self._lock:
            return {'schema': 'bcenv.operator-stop-status.v1',
                    'scope': 'cooperative operator request only; main owns cleanup',
                    'started': self._started, 'watcher_joined': self._joined,
                    'requested': self._cancel_called, 'failed': self._error is not None, 'joined': self._joined,
                    'observed': self._observed, 'valid_request': self._valid,
                    'cancel_called': self._cancel_called, 'cancel_failed': self._cancel_failed, 'error': self._error,
                    'observed_monotonic_ns': self._observed_ns,
                    'cancel_requested_monotonic_ns': self._cancel_requested_ns,
                    'cancel_returned_monotonic_ns': self._cancel_returned_ns,
                    'joined_monotonic_ns': self._joined_ns}
