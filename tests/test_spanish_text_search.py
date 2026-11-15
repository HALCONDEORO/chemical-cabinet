"""Regression: the searcher must find Spanish text typed without accents.

Synthetic demo data only. Pins the desired behavior (folding search) and
documents declared limits: quantity is not part of the search corpus (the
field label says name/location/notes), multi-word queries stay a literal
substring, and the default name order keeps the legacy byte order (pinned by
the parity suites).
"""
import os
import unittest
from datetime import date

from chemical_cabinet import app, db
import tests.test_cabinet as base

TODAY = date(2026, 6, 1)
DEMO_ROWS = [
    ("Lejía (demo)", "Almacén", "5 L", "No mezclar con amoníaco (demo)", "2027-01-01"),
    ("Amoniaco (demo)", "Baño", "750 ml", "", "2027-06-01"),
    ("Ácido desincrustante (demo)", "Almacén", "1 L", "", "2028-01-01"),
]


class FoldUnitTest(unittest.TestCase):
    def test_fold_strips_marks_and_case(self):
        self.assertEqual(db.fold("Lejía"), "lejia")
        self.assertEqual(db.fold("MÜNCHEN"), "munchen")
        self.assertEqual(db.fold("Baño"), "bano")
        self.assertEqual(db.fold("Δοκιμή"), "δοκιμη")
        self.assertEqual(db.fold("shelf 1"), "shelf 1")


class SpanishSearchModelTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")
        self.addCleanup(self.con.close)
        for name, loc, qty, notes, exp in DEMO_ROWS:
            db.add_item(self.con, name, loc, qty, notes, None, exp, today=TODAY)

    def names(self, q, **kw):
        total, rows = db.query_active_items(self.con, q, today=TODAY, **kw)
        return total, [r["name"] for r in rows]

    def test_query_without_accents_finds_accented_name(self):
        self.assertEqual(self.names("lejia"), (1, ["Lejía (demo)"]))
        self.assertEqual(self.names("acido"), (1, ["Ácido desincrustante (demo)"]))

    def test_query_with_accents_still_matches(self):
        self.assertEqual(self.names("lejía"), (1, ["Lejía (demo)"]))
        self.assertEqual(self.names("LEJÍA"), (1, ["Lejía (demo)"]))

    def test_query_without_accents_finds_accented_location(self):
        total, names = self.names("almacen")
        self.assertEqual(total, 2)
        self.assertEqual(self.names("bano"), (1, ["Amoniaco (demo)"]))

    def test_query_without_accents_finds_accented_notes(self):
        self.assertEqual(self.names("amoniaco"), (2, ["Amoniaco (demo)", "Lejía (demo)"]))  # legacy byte order

    def test_location_filter_stays_exact(self):
        total, rows = db.query_active_items(self.con, "", today=TODAY, location="Almacén")
        self.assertEqual(total, 2)
        total, rows = db.query_active_items(self.con, "", today=TODAY, location="Almacen")
        self.assertEqual(total, 0)

    def test_python_fallback_filter_matches_sql(self):
        rows = db.active_items(self.con)
        py = [r["id"] for r in app.filter_items(rows, "lejia", False, "")]
        total, sql_rows = db.query_active_items(self.con, "lejia", today=TODAY)
        self.assertEqual([r["id"] for r in sql_rows], py)

    def test_declared_limit_quantity_not_in_corpus(self):
        self.assertEqual(self.names("750"), (0, []))

    def test_declared_limit_multiword_stays_literal(self):
        self.assertEqual(self.names("lejia amoniaco"), (0, []))


class SpanishSearchHttpTest(unittest.TestCase):
    setUp = base.ServerTest.setUp
    tearDown = base.ServerTest.tearDown
    req = base.ServerTest.req

    def get(self, qs=""):
        status, body, _ = self.req("GET", "/" + qs, headers={"Host": "localhost:%d" % self.port})
        self.assertEqual(status, 200)
        return body.decode()

    def test_search_without_accents_lists_item_and_count(self):
        con = db.connect(os.path.join(self.tmp.name, "cabinet.sqlite3"))
        db.add_item(con, "Lejía (demo)", "Almacén", "5 L", "", None, "2027-01-01", today=TODAY)
        con.commit()
        con.close()
        page = self.get("?q=lejia")
        self.assertIn("Lejía (demo)", page)
        self.assertIn("1 of", page)
        self.assertNotIn("Lejía", self.get("?q=zzz-no-match"))


if __name__ == "__main__":
    unittest.main()
