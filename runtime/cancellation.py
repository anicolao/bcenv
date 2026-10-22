"""Voluntary same-host cancellation, not supervisor-death containment."""
import os
import select
import threading


def cancelled(fd):
    if fd is None:
        return False
    # The sole writer publishes cancellation by closing, never by writing data.
    return bool(select.select([fd], [], [], 0)[0])


class Cancellation:
    def __init__(self):
        self.read_fd, self.write_fd = os.pipe()
        self.lock = threading.RLock()
        self.error = None

    def cancel(self):
        with self.lock:
            if self.error is not None:
                raise self.error
            if self.write_fd is not None:
                fd, self.write_fd = self.write_fd, None
                try:
                    os.close(fd)
                except OSError as error:
                    self.error = error
                    raise

    def close(self):
        self.cancel()
        with self.lock:
            if self.read_fd is not None:
                os.close(self.read_fd)
                self.read_fd = None
