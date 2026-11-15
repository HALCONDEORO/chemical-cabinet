"""CSV importer regressions. Invented inventory only."""
import csv
from datetime import date
import io
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from chemical_cabinet import csv_import as ci, db


class CsvValidationTest(unittest.TestCase):
    def test_bom_unicode_crlf_quotes_multiline_and_whitespace(self):
        rows = ci.parse_csv(('\ufeff Name ,NOTES,quantity,location,expires_on\r\n'
                             '"Café (demo)","line 1, quoted\r\nline 2",2 L, Étage ,2028-02-29\r\n').encode())
        self.assertEqual(rows[0]["name"], "Café (demo)")
        self.assertEqual(rows[0]["notes"], "line 1, quoted\r\nline 2")
        self.assertEqual(rows[0]["location"], "Étage")
        self.assertIsNone(rows[0]["opened_on"])

    def test_every_header_subset_and_order(self):
        self.assertEqual(ci.parse_csv(b"quantity,name\n3 bottles,Demo\n")[0]["quantity"], "3 bottles")
        self.assertEqual(ci.parse_csv(b"name\nDemo\n")[0]["name"], "Demo")

    def test_empty_or_invalid_headers(self):
        for raw in (b"", b"\xef\xbb\xbf", b"\n", b"notes\nDemo\n", b"name,Name\na,b\n",
                    b"name,\na,b\n", b"name,photo\na,../x\n", b"name;quantity\na;2\n",
                    b"id,name\n1,a\n", b"name,retired_on\na,2026-01-01\n"):
            with self.subTest(raw=raw), self.assertRaises(ci.CsvImportError):
                ci.parse_csv(raw)

    def test_blank_records_are_skipped_but_not_empty_import(self):
        self.assertEqual(len(ci.parse_csv(b"name,notes\n\n,\n  , \nDemo,ok\n")), 1)
        for raw in (b"name\n", b"name,notes\n,\n\n"):
            with self.assertRaisesRegex(ci.CsvImportError, "no products"):
                ci.parse_csv(raw)

    def test_bad_utf8_and_controls(self):
        for raw in (b"name\n\xff\n", b"name\nDemo\x00\n", b"name\nDemo\x1b\n", b"name\nDemo\x7f\n"):
            with self.subTest(raw=raw), self.assertRaises(ci.CsvImportError):
                ci.parse_csv(raw)
        self.assertIn("\t", ci.parse_csv(b"name,notes\nDemo,a\tb\n")[0]["notes"])

    def test_column_counts_and_name(self):
        for raw in (b"name,notes\nDemo\n", b"name\nDemo,extra\n", b"name,notes\n,secret\n"):
            with self.subTest(raw=raw), self.assertRaisesRegex(ci.CsvImportError, "Line 2"):
                ci.parse_csv(raw)

    def test_line_number_after_multiline_record(self):
        with self.assertRaisesRegex(ci.CsvImportError, "Line 4"):
            ci.parse_csv(b'name,notes\nDemo,"first\nsecond"\n,missing\n')

    def test_strict_dates(self):
        for field in ("opened_on", "expires_on"):
            for value in ("2026-02-29", "20260201", "2026-W01-1", "01/02/2026", "0000-01-01", "2026-2-01"):
                with self.subTest(field=field, value=value), self.assertRaises(ci.CsvImportError):
                    ci.parse_csv(("name,%s\nDemo,%s\n" % (field, value)).encode())
            self.assertIsNone(ci.parse_csv(("name,%s\nDemo,\n" % field).encode())[0][field])

    def test_syntax_errors(self):
        for raw in (b'name\n"unclosed', b'name,notes\n"Demo"x,notes\n'):
            with self.assertRaisesRegex(ci.CsvImportError, "syntax error"):
                ci.parse_csv(raw)

    def test_limits(self):
        with self.assertRaisesRegex(ci.CsvImportError, "2 MiB"):
            ci.parse_csv(b"x" * (ci.MAX_BYTES + 1))
        with self.assertRaisesRegex(ci.CsvImportError, "1000"):
            ci.parse_csv(b"name\n" + b"Demo\n" * 1001)
        self.assertEqual(len(ci.parse_csv(b"name\n" + b"Demo\n" * 1000)), 1000)
        for field, limit in ci.LIMITS.items():
            if field.endswith("_on"):
                continue
            header = ["name"] if field == "name" else ["name", field]
            values = ["x" * (limit + 1)] if field == "name" else ["Demo", "x" * (limit + 1)]
            with self.subTest(field=field), self.assertRaisesRegex(ci.CsvImportError, "exceeds"):
                ci.parse_csv((",".join(header) + "\n" + ",".join(values)).encode())

    def test_formula_text_is_preserved_not_executed_or_unescaped(self):
        for text in ("=1+1", "+x", "-1", "@x", "'=1+1", "Robert'); DROP TABLE items;--"):
            buf = io.StringIO()
            csv.writer(buf).writerows([["name"], [text]])
            self.assertEqual(ci.parse_csv(buf.getvalue().encode())[0]["name"], text)


class CsvTransactionTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")
        self.addCleanup(self.con.close)
        self.old = db.add_item(self.con, "Existing (demo)", photo="a" * 16 + ".jpg")

    def test_additive_import_preserves_existing_and_attachments(self):
        ids = ci.import_csv(self.con, b"name,quantity,expires_on\nDemo,2 L,2028-01-01\nDemo,1 bottle,\n", date(2026, 11, 15))
        self.assertEqual(len(set(ids)), 2)
        self.assertNotIn(self.old, ids)
        self.assertEqual(db.get_item(self.con, self.old)["photo"], "a" * 16 + ".jpg")
        item = db.get_item(self.con, ids[0])
        self.assertEqual(item["created_on"], "2026-11-15")
        self.assertEqual(item["quantity"], "2 L")
        self.assertIsNone(item["photo"])
        self.assertIsNone(item["sds"])
        self.assertIsNone(item["retired_on"])
        self.assertEqual(db.retirement_log(self.con), [])

    def test_invalid_later_row_never_writes_first_row(self):
        with self.assertRaises(ci.CsvImportError):
            ci.import_csv(self.con, b"name,expires_on\nGood,2028-01-01\nBad,not-a-date\n")
        self.assertEqual(len(db.active_items(self.con)), 1)
        self.assertFalse(self.con.in_transaction)

    def test_database_failure_rolls_back_every_row(self):
        self.con.execute("CREATE TRIGGER fail_import BEFORE INSERT ON items WHEN NEW.name='Fail' BEGIN SELECT RAISE(ABORT, 'failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            ci.import_csv(self.con, b"name\nGood\nFail\n")
        self.assertEqual(len(db.active_items(self.con)), 1)
        self.assertFalse(self.con.in_transaction)

    def test_shared_model_validation_and_insert_helpers_are_used(self):
        with patch.object(db, "validate_item", wraps=db.validate_item) as validator:
            ci.parse_csv(b"name\nDemo\n")
            self.assertEqual(validator.call_count, 1)
        with patch.object(db, "insert_item", wraps=db.insert_item) as insert:
            ci.import_csv(self.con, b"name\nFirst\nSecond\n")
            self.assertEqual(insert.call_count, 2)
        with patch.object(db, "validate_item", side_effect=ValueError("private model error")):
            with self.assertRaisesRegex(ci.CsvImportError, "Line 2: invalid product fields"):
                ci.parse_csv(b"name\nDemo\n")

    def test_foreign_keys_enabled_on_plain_sqlite_connection(self):
        con = sqlite3.connect(":memory:")
        self.addCleanup(con.close)
        con.executescript(db.SCHEMA + "CREATE TABLE csv_guard (item_id INTEGER REFERENCES items(id));"
                         "CREATE TRIGGER csv_fk AFTER INSERT ON items BEGIN "
                         "INSERT INTO csv_guard VALUES (999999); END;")
        self.assertEqual(con.execute("PRAGMA foreign_keys").fetchone()[0], 0)
        with self.assertRaises(sqlite3.IntegrityError):
            ci.import_csv(con, b"name\nDemo\n")
        self.assertEqual(con.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM items").fetchone()[0], 0)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM csv_guard").fetchone()[0], 0)

    def test_restore_marker_appearing_while_begin_waits_refuses_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "cabinet.sqlite3"
            blocker = db.connect(path)
            self.addCleanup(blocker.close)
            blocker.execute("BEGIN IMMEDIATE")
            attempting = threading.Event()
            finished = threading.Event()
            errors = []
            def worker():
                con = sqlite3.connect(path, timeout=5)
                try:
                    con.set_trace_callback(lambda sql: attempting.set() if sql == "BEGIN IMMEDIATE" else None)
                    ci.import_csv(con, b"name\nDemo\n", data_dir=root)
                except Exception as exc:
                    errors.append(exc)
                finally:
                    con.close()
                    finished.set()
            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            try:
                self.assertTrue(attempting.wait(2), "worker did not attempt BEGIN")
                self.assertFalse(finished.is_set(), "worker bypassed held write lock")
                (root / "RESTORE_INCOMPLETE").touch()
            finally:
                blocker.rollback()
                thread.join(6)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], ci.CsvImportError)
            self.assertIn("RESTORE_INCOMPLETE", str(errors[0]))
            self.assertEqual(blocker.execute("SELECT COUNT(*) FROM items").fetchone()[0], 0)

    def test_pending_transaction_is_not_committed_or_discarded(self):
        self.con.execute("UPDATE items SET notes='pending' WHERE id=?", (self.old,))
        with self.assertRaisesRegex(ValueError, "pending transaction"):
            ci.import_csv(self.con, b"name\nDemo\n")
        self.assertTrue(self.con.in_transaction)
        self.con.rollback()
        self.assertEqual(db.get_item(self.con, self.old)["notes"], "")


class CsvCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "demo.csv"
        self.source.write_bytes(b"name,notes\nDemo,Unicode cafe\n")
        self.data = self.root / "data"

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "chemical_cabinet.csv_import", str(self.source), "--data", str(self.data), *args], capture_output=True, text=True)

    def test_dry_run_does_not_create_data_folder(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No changes made", result.stdout)
        self.assertFalse(self.data.exists())

    def test_apply_existing_only_and_restore_marker(self):
        self.assertEqual(self.run_cli("--apply").returncode, 1)
        self.assertFalse(self.data.exists())
        self.data.mkdir()
        path = self.data / "cabinet.sqlite3"
        db.connect(path).close()
        marker = self.data / "RESTORE_INCOMPLETE"
        marker.touch()
        result = self.run_cli("--apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("RESTORE_INCOMPLETE", result.stderr)
        marker.unlink()
        marker.symlink_to(self.root / "nonexistent")
        self.assertEqual(self.run_cli("--apply").returncode, 1)
        marker.unlink()
        result = self.run_cli("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        con = db.connect(path)
        self.addCleanup(con.close)
        self.assertEqual(len(db.active_items(con)), 1)

    def test_error_does_not_echo_private_cell_or_filename(self):
        self.source.write_bytes(b"name,expires_on\nPrivateDemo,PRIVATE-DATE\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Line 2", result.stderr)
        self.assertNotIn("PrivateDemo", result.stderr)
        self.assertNotIn("PRIVATE-DATE", result.stderr)
        self.assertNotIn(str(self.source), result.stderr)


if __name__ == "__main__":
    unittest.main()
