"""Cooperative logical-storage admission checks; NOT an OS quota or hard cap.

A check sums regular-file logical lengths in the service root and registered
mailboxes, including temporary files and this module's evidence log. It does not
read contents, measure allocated blocks, or include candidate workspace siblings,
shared runtimes, opponents, or maps outside those roots. Hard links count once
per directory entry. Concurrent writes can overshoot arbitrarily between/during
checks; a successful check is not a reservation or an atomic filesystem snapshot.

Checkpoint records have bounded individual size, but the append-only log grows
and is itself charged at the next check. In particular, a successful selection
checkpoint does not prevent the subsequent selection receipt crossing the limit.
Evidence on failure is best effort, not a guarantee of a successful full-disk fsync.
"""
import json
import os
from pathlib import Path
import re
import stat
import threading
import time

DEFAULT_LIMIT_BYTES = 1_073_741_824
LOG_NAME = 'storage-checkpoints.jsonl'
MAX_CHECKPOINT_BYTES = 1024
_STAGE = re.compile(r'[A-Za-z0-9_.:-]{1,80}\Z')
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _absolute(path):
    return Path(os.path.abspath(os.fspath(path)))


def _open_directory(path):
    """Open each component without following symlinks, including ancestors."""
    path = _absolute(path)
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


def _scan_directory(fd):
    total, files = 0, 0
    with os.scandir(fd) as entries:
        for entry in entries:
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
                files += 1
            elif stat.S_ISDIR(info.st_mode):
                child = os.open(entry.name, _DIR_FLAGS, dir_fd=fd)
                try:
                    opened = os.fstat(child)
                    if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                        raise OSError('directory changed during storage scan')
                    size, count = _scan_directory(child)
                    total += size
                    files += count
                finally:
                    os.close(child)
            else:
                raise OSError('unsupported storage entry')
    return total, files


class StorageGuard:
    """Sample logical high-water usage and fail closed on limit/scan errors.

    The root and mailbox directories must already exist. Once an observed limit
    breach or scan/write error occurs, this guard remains failed, even if files
    are later deleted. The coordinator must halt work; do not construct a new
    guard to reset a live run. No ownership/authentication boundary is claimed.
    """
    def __init__(self, root, limit_bytes=DEFAULT_LIMIT_BYTES):
        if type(limit_bytes) is not int or limit_bytes < 1:
            raise ValueError('invalid_storage_limit')
        self.root = _absolute(root)
        self.limit_bytes = limit_bytes
        self.mailboxes = set()
        self.high_water_bytes = 0
        self.sequence = 0
        self.failure = None
        self.lock = threading.RLock()
        try:
            fd = _open_directory(self.root)
            os.close(fd)
        except (OSError, ValueError, RecursionError):
            raise ValueError('storage_scan_failed') from None

    def add_mailbox(self, path):
        """Register the entire trusted mailbox, never a candidate-supplied path."""
        with self.lock:
            try:
                path = _absolute(path)
                fd = _open_directory(path)
                os.close(fd)
                self.mailboxes.add(path)
            except (OSError, ValueError, RecursionError):
                self.failure = 'storage_scan_failed'
                raise ValueError('storage_scan_failed') from None

    def _roots(self):
        # Overlapping directory scopes count once, not once per registration.
        scopes = sorted({self.root, *self.mailboxes}, key=lambda p: (len(p.parts), str(p)))
        roots = []
        for scope in scopes:
            if not any(scope == prior or prior in scope.parents for prior in roots):
                roots.append(scope)
        return roots

    def _append(self, record):
        data = (json.dumps(record, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('ascii')
        if len(data) > MAX_CHECKPOINT_BYTES:
            raise OSError('checkpoint too large')
        directory = _open_directory(self.root)
        try:
            fd = os.open(LOG_NAME, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=directory)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise OSError('checkpoint is not a regular file')
                # A partial write is a failure, not silently completed evidence.
                if os.write(fd, data) != len(data):
                    raise OSError('partial checkpoint write')
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            os.close(directory)

    def check(self, stage):
        with self.lock:
            # Do not put arbitrary caller text or private paths into evidence.
            valid_stage = type(stage) is str and _STAGE.fullmatch(stage) is not None
            stage_label = stage if valid_stage else 'invalid_stage'
            size, files, roots = None, None, self._roots()
            error = self.failure
            if not valid_stage:
                error = 'storage_scan_failed'
            if error is None:
                try:
                    size, files = 0, 0
                    for root in roots:
                        fd = _open_directory(root)
                        try:
                            subtotal, count = _scan_directory(fd)
                        finally:
                            os.close(fd)
                        size += subtotal
                        files += count
                    self.high_water_bytes = max(self.high_water_bytes, size)
                    if size > self.limit_bytes:
                        error = 'storage_limit'
                except (OSError, ValueError, RecursionError):
                    size, files, error = None, None, 'storage_scan_failed'
            self.sequence += 1
            record = {'schema': 'bcenv.storage-checkpoint.v1', 'sequence': self.sequence,
                      'time_ns': time.time_ns(), 'monotonic_ns': time.monotonic_ns(), 'stage': stage_label, 'evidence_attempt': 1,
                      'status': 'ok' if error is None else error,
                      'logical_bytes': size, 'regular_files': files,
                      'high_water_bytes': self.high_water_bytes, 'limit_bytes': self.limit_bytes,
                      'scope_count': len(roots), 'measurement': 'logical_lengths_before_checkpoint_append'}
            try:
                self._append(record)
            except (OSError, ValueError, RecursionError):
                # Evidence may be partial or absent. Attempt one bounded failure
                # record, but never report successful checking after write failure.
                error = 'storage_scan_failed'
                record['status'] = error
                record['evidence_attempt'] = 2
                try:
                    self._append(record)
                except (OSError, ValueError, RecursionError):
                    pass
            if error is not None:
                self.failure = error
                raise ValueError(error)
            return record
