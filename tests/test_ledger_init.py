"""Journal-mode initialization races only; no job execution or replay retries."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.ledger import DurableLedger, LedgerError


class JournalInitializationTests(unittest.TestCase):
    def test_real_writer_lock_between_schema_commit_and_wal_is_retried(self):
        original_connect = sqlite3.connect
        connections = []

        class ContendedConnection(sqlite3.Connection):
            def __init__(self, database, *args, **kwargs):
                super().__init__(database, *args, **kwargs)
                self.database, self.wal_calls, self.busy_errors = database, 0, 0
                connections.append(self)

            def execute(self, statement, *args):
                if statement != 'PRAGMA journal_mode=WAL':
                    return super().execute(statement, *args)
                self.wal_calls += 1
                if self.wal_calls != 1:
                    return super().execute(statement, *args)
                # Acquire a real competing writer only after the constructor's
                # schema transaction committed. This mode transition returns
                # SQLITE_BUSY immediately even with a connection busy timeout.
                other = original_connect(self.database, isolation_level=None)
                try:
                    other.execute('BEGIN IMMEDIATE')
                    try:
                        return super().execute(statement, *args)
                    except sqlite3.OperationalError as error:
                        if error.sqlite_errorcode == sqlite3.SQLITE_BUSY:
                            self.busy_errors += 1
                        raise
                finally:
                    other.rollback()
                    other.close()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'race.db'
            with patch('services.ledger.sqlite3.connect', side_effect=lambda *a, **kw:
                       original_connect(*a, **kw, factory=ContendedConnection)):
                DurableLedger(path)
            self.assertEqual(connections[0].busy_errors, 1)
            self.assertEqual(connections[0].wal_calls, 2)
            with original_connect(path) as connection:
                self.assertEqual(connection.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
                self.assertEqual(connection.execute('SELECT count(*) FROM jobs').fetchone()[0], 0)

    def test_busy_retry_deadline_is_bounded_without_retrying_transactions(self):
        class BusyConnection:
            def __init__(self):
                self.statements = []
            def execute(self, statement):
                self.statements.append(statement)
                if statement == 'PRAGMA journal_mode=WAL':
                    error = sqlite3.OperationalError('private busy detail')
                    error.sqlite_errorcode = sqlite3.SQLITE_BUSY
                    raise error

        connection = BusyConnection()
        with patch('services.ledger.time.monotonic', side_effect=[0, 0, 1, 1, 5.001]), \
                patch('services.ledger.time.sleep') as sleep:
            with self.assertRaises(sqlite3.OperationalError):
                DurableLedger._enable_wal(connection)
        self.assertEqual(connection.statements, ['PRAGMA busy_timeout=5000',
                         'PRAGMA journal_mode=WAL', 'PRAGMA busy_timeout=4000',
                         'PRAGMA journal_mode=WAL'])
        sleep.assert_called_once_with(0.01)

    def test_nonbusy_storage_errors_and_wrong_mode_are_not_retried(self):
        for code in (sqlite3.SQLITE_IOERR, sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_LOCKED):
            class FailedConnection:
                calls = 0
                def execute(self, statement):
                    if statement == 'PRAGMA journal_mode=WAL':
                        self.calls += 1
                        error = sqlite3.OperationalError('private storage detail')
                        error.sqlite_errorcode = code
                        raise error
            connection = FailedConnection()
            with patch('services.ledger.time.sleep') as sleep:
                with self.assertRaises(sqlite3.OperationalError):
                    DurableLedger._enable_wal(connection)
                sleep.assert_not_called()
            self.assertEqual(connection.calls, 1)

        class WrongMode:
            def execute(self, statement):
                return self
            def fetchone(self):
                return ('delete',)
        with self.assertRaisesRegex(LedgerError, '^storage_unavailable$'):
            DurableLedger._enable_wal(WrongMode())

    def test_repeated_concurrent_initialization_keeps_one_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            for number in range(12):
                path = Path(directory) / str(number)
                with ThreadPoolExecutor(max_workers=8) as pool:
                    configurations = list(pool.map(lambda _: DurableLedger(path).configuration, range(8)))
                self.assertEqual(len(set(configurations)), 1)


if __name__ == '__main__':
    unittest.main()
