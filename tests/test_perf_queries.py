"""Performance-front tests: SQL filtering/paging parity and index use.

Synthetic demo data only. The Python-side reference is app.filter_items over
db.active_items, which query_active_items must match exactly.
"""
import os
import random
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta

from chemical_cabinet import app, db


def synth_items(seed, n):
    """Invented (demo) inventory: mixed expiry states, missing files, accents."""
    rng = random.Random(seed)
    today = date.today()
    expiries = [None, (today - timedelta(days=3)).isoformat(), today.isoformat(),
                (today + timedelta(days=29)).isoformat(),
                (today + timedelta(days=30)).isoformat(),
                (today + timedelta(days=31)).isoformat(),
                (today + timedelta(days=400)).isoformat()]
    words = ["ÁCIDO", "Solvente", "GLASEA", "desengrasante", "LEJÍA", "alcohol"]
    out = []
    for i in range(n):
        out.append({
            "name": "%s %s %04d" % (rng.choice(words), rng.choice(words), i),
            "location": "Shelf %d" % rng.randrange(6),
            "notes": rng.choice(["", "demo note %d" % rng.randrange(20), "corrosive"]),
            "expires_on": rng.choice(expiries),
            "photo": "aabbccddeeff00%02d.jpg" % i if rng.random() < 0.7 else None,
            "sds": "aabbccddeeff11%02d.pdf" % i if rng.random() < 0.7 else None,
            "retired": rng.random() < 0.15,
        })
    return out


class PerfQueryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "cabinet.sqlite3")
        self.con = db.connect(self.path)
        self.items = synth_items(20261115, 300)
        for it in self.items:
            item_id = db.add_item(self.con, it["name"], it["location"], "", it["notes"],
                                  None, it["expires_on"], it["photo"], it["sds"])
            if it["retired"]:
                db.retire_item(self.con, item_id, "used up", "collection")

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def python_reference(self, q, flagged, sort):
        rows = db.active_items(self.con)
        return [it["id"] for it in app.filter_items(rows, q, flagged, sort)]

    def sql_ids(self, q, flagged, sort):
        total, rows = db.query_active_items(self.con, q, flagged, sort, limit=10 ** 9)
        self.assertEqual(total, len(rows))
        return [it["id"] for it in rows]

    def test_sql_matches_python_filter_matrix(self):
        for q in ("", "demo", "ácido glasea", "shelf 1", "corrosive", "zzz-no-match"):
            for flagged in (False, True):
                for sort in ("", "expiry"):
                    with self.subTest(q=q, flagged=flagged, sort=sort):
                        self.assertEqual(self.python_reference(q, flagged, sort),
                                         self.sql_ids(q, flagged, sort))

    def test_pages_cover_python_reference_exactly(self):
        for q, flagged, sort in (("", False, ""), ("shelf 2", True, "expiry")):
            ref = self.python_reference(q, flagged, sort)
            got = []
            offset = 0
            while True:
                total, rows = db.query_active_items(self.con, q, flagged, sort,
                                                    limit=37, offset=offset)
                self.assertEqual(total, len(ref))
                got.extend(it["id"] for it in rows)
                if len(rows) < 37:
                    break
                offset += 37
            self.assertEqual(got, ref)

    def test_flagged_sql_matches_flags_function_row_by_row(self):
        _, flagged_rows = db.query_active_items(self.con, only_flagged=True, limit=10 ** 9)
        flagged_ids = {it["id"] for it in flagged_rows}
        for it in db.active_items(self.con):
            with self.subTest(item=it["id"]):
                self.assertEqual(bool(db.flags(it)), it["id"] in flagged_ids)

    def test_retired_rows_never_returned(self):
        total, rows = db.query_active_items(self.con, limit=10 ** 9)
        retired = sum(1 for it in self.items if it["retired"])
        self.assertEqual(total, len(self.items) - retired)
        self.assertTrue(all(it["retired_on"] is None for it in rows))

    def test_empty_string_expiry_counts_as_flagged(self):
        # A legacy row with '' instead of NULL must behave like parse_date('') -> None.
        self.con.execute(
            "INSERT INTO items(name, location, quantity, notes, opened_on, expires_on,"
            " photo, sds, created_on) VALUES ('Legacy empty date', '', '', '', NULL, '',"
            " 'aabbccddeeff2299.jpg', 'aabbccddeeff2288.pdf', '2026-01-01')")
        self.con.commit()
        total, rows = db.query_active_items(self.con, only_flagged=True, limit=10 ** 9)
        self.assertIn("Legacy empty date", {it["name"] for it in rows})
        item = db.get_item(self.con, rows[0]["id"]) if rows else None
        for it in rows:
            if it["name"] == "Legacy empty date":
                self.assertIn("no expiry date", db.flags(it))

    def test_active_list_query_uses_partial_index(self):
        plan = self.con.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM items WHERE retired_on IS NULL"
            " ORDER BY name").fetchall()
        detail = " ".join(row[3] for row in plan)
        self.assertIn("items_active_name_idx", detail)
        self.assertNotIn("TEMP B-TREE", detail)

    def test_indexes_created_on_pre_index_database(self):
        # A database written by 0.1.x has no indexes; connect() must add them.
        old_path = os.path.join(self.tmp.name, "old.sqlite3")
        raw = sqlite3.connect(old_path)
        raw.executescript("""
            CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL, location TEXT NOT NULL DEFAULT '',
              quantity TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
              opened_on TEXT, expires_on TEXT, photo TEXT, sds TEXT,
              created_on TEXT NOT NULL, retired_on TEXT);
            CREATE TABLE retirements (id INTEGER PRIMARY KEY AUTOINCREMENT,
              item_id INTEGER NOT NULL REFERENCES items(id), retired_on TEXT NOT NULL,
              reason TEXT NOT NULL DEFAULT '', method TEXT NOT NULL DEFAULT '');""")
        raw.execute("INSERT INTO items(name, created_on) VALUES ('Old item', '2026-01-01')")
        raw.commit()
        raw.close()
        con = db.connect(old_path)
        names = {r[1] for r in con.execute("PRAGMA index_list('items')").fetchall()}
        self.assertIn("items_active_name_idx", names)
        names = {r[1] for r in con.execute("PRAGMA index_list('retirements')").fetchall()}
        self.assertIn("retirements_item_id_idx", names)
        total, rows = db.query_active_items(con)
        self.assertEqual((total, [r["name"] for r in rows]), (1, ["Old item"]))
        con.close()


if __name__ == "__main__":
    unittest.main()
