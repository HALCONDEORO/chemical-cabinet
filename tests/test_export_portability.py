"""CSV portability and formula-prefix regression cases, synthetic data only."""
import csv
import io
import unittest

from chemical_cabinet import app, db


class ExportPortabilityTests(unittest.TestCase):
    def test_formula_markers_after_ignorable_prefixes(self):
        for prefix in ("", " ", "  ", "\t", "\r", "\n", "\v", "\f", "\ufeff", " \t\r\n\ufeff"):
            for marker in ("=", "+", "-", "@"):
                value = prefix + marker + "SUM(1,1)"
                with self.subTest(value=repr(value)):
                    self.assertEqual(app.csv_cell(value), "'" + value)

    def test_leading_cell_controls_neutralised_without_formula(self):
        for prefix in ("\t", "\r", "\n"):
            value = prefix + "demo text"
            with self.subTest(value=repr(value)):
                self.assertEqual(app.csv_cell(value), "'" + value)

    def test_safe_values_are_not_rewritten(self):
        for value in ("", "Cleaner (demo)", "  keep spaces", "Café, jabón 🧴 (demo)",
                      "O'Brien", "'already quoted", 'A "quote"', "line one\nline two", "1+1"):
            with self.subTest(value=repr(value)):
                self.assertEqual(app.csv_cell(value), value)
        self.assertEqual(app.csv_cell(None), "")
        self.assertEqual(app.csv_cell(0), "0")

    def test_csv_utf8_quotes_multiline_and_header_contract(self):
        con = db.connect(":memory:")
        self.addCleanup(con.close)
        name = 'Jabón, "Café" 🧴 (demo)'
        location = "Étage 2; A (demo)"
        notes = 'First line\r\nSecond line, with "quotes"\nThird line (demo)'
        item_id = db.add_item(con, name, location=location, quantity="2 × 500 mL (demo)",
                              notes=notes, opened_on="2026-10-01", expires_on="2099-01-01",
                              photo="a" * 16 + ".jpg", sds="b" * 16 + ".pdf")
        payload = app.items_csv(db.active_items(con))
        self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.reader(io.StringIO(payload.decode("utf-8-sig"), newline="")))
        self.assertEqual(rows[0], ["id", "name", "location", "quantity", "opened_on", "expires_on",
                                  "has_photo", "has_safety_data_sheet", "flags", "notes"])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1], [str(item_id), name, location, "2 × 500 mL (demo)", "2026-10-01",
                                  "2099-01-01", "yes", "yes", "", notes])

    def test_csv_neutralises_all_user_text_columns(self):
        # Use a row-shaped object to exercise exporter boundaries without model trimming.
        row = dict(id=1, name=" =SUM(1,1)", location="\n@SUM(1,1)", quantity="\ufeff+1",
                   notes="\t-1", opened_on=None, expires_on=None, photo=None, sds=None,
                   retired_on=None)
        rows = list(csv.reader(io.StringIO(app.items_csv([row]).decode("utf-8-sig"), newline="")))
        for column, key in ((1, "name"), (2, "location"), (3, "quantity"), (9, "notes")):
            self.assertEqual(rows[1][column], "'" + row[key])
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(rows[1]), 10)

    def test_empty_export_still_has_header(self):
        rows = list(csv.reader(io.StringIO(app.items_csv([]).decode("utf-8-sig"), newline="")))
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 10)


if __name__ == "__main__":
    unittest.main()
