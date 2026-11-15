"""Synthetic SQLite failure and concurrency regressions, no inventory data."""
import concurrent.futures
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from chemical_cabinet import db


class RetirementIntegrityTest(unittest.TestCase):
    def test_failed_log_insert_does_not_leak_retirement_into_next_commit(self):
        con = db.connect(':memory:')
        self.addCleanup(con.close)
        item = db.add_item(con, 'Synthetic cleaner')
        con.execute("CREATE TRIGGER fail_log BEFORE INSERT ON retirements "
                    "BEGIN SELECT RAISE(ABORT, 'synthetic log failure'); END")
        con.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            db.retire_item(con, item, 'expired', 'test only')
        db.add_item(con, 'Unrelated synthetic item')
        row = con.execute('SELECT retired_on FROM items WHERE id=?', (item,)).fetchone()
        self.assertIsNone(row['retired_on'])
        self.assertEqual(db.retirement_log(con), [])

    def test_concurrent_retirement_has_exactly_one_log(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'synthetic.sqlite3'
            con = db.connect(path)
            item = db.add_item(con, 'Synthetic cleaner')
            con.close()
            both_read = threading.Barrier(2)

            class ReadBarrierConnection(sqlite3.Connection):
                def execute(self, sql, parameters=()):
                    cursor = super().execute(sql, parameters)
                    if sql.startswith('SELECT id, retired_on FROM items'):
                        row = cursor.fetchone()
                        both_read.wait(timeout=3)

                        class SavedRow:
                            def fetchone(self):
                                return row
                        return SavedRow()
                    return cursor

            def retire(reason):
                conn = sqlite3.connect(path, factory=ReadBarrierConnection)
                conn.row_factory = sqlite3.Row
                try:
                    db.retire_item(conn, item, reason, 'synthetic')
                    return 'retired'
                except ValueError:
                    return 'already retired'
                finally:
                    conn.close()

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(retire, ('first', 'second')))
            con = db.connect(path)
            self.addCleanup(con.close)
            self.assertCountEqual(outcomes, ['retired', 'already retired'])
            self.assertEqual(len(db.retirement_log(con)), 1)
