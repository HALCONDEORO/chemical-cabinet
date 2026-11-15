"""Synthetic-performance benchmark for the cabinet home list query path.

The data is invented (demo) and the volumes are declared test sizes, not an
estimate of any real user's cabinet. It compares the 0.1.1 request path
(load every active row, filter in Python, slice the page) with
db.query_active_items (filter/sort/page in SQL).

Run: python perf/bench_synthetic.py [N ...]
"""
import os
import random
import sys
import tempfile
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chemical_cabinet import app, db

PAGE = app.PAGE_SIZE


def fill(con, n, seed):
    rng = random.Random(seed)
    today = date.today()
    words = ["Ácido", "Solvente", "Glasea", "Desengrasante", "Lejía", "Alcohol",
             "Barniz", "Amoníaco", "Percloro", "Diluyente"]
    con.execute("BEGIN")
    for i in range(n):
        exp = rng.choice([None, (today + timedelta(days=rng.randrange(-30, 400))).isoformat()])
        con.execute(
            "INSERT INTO items(name, location, quantity, notes, opened_on, expires_on,"
            " photo, sds, created_on, retired_on) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("%s %s %06d" % (rng.choice(words), rng.choice(words), i),
             "Shelf %d" % rng.randrange(8), "", rng.choice(["", "demo note"]), None, exp,
             "aabbccddeeff0011.jpg" if rng.random() < 0.6 else None,
             "aabbccddeeff0022.pdf" if rng.random() < 0.6 else None,
             today.isoformat(), None))
    con.commit()


def timeit(fn, repeats):
    best = float("inf")
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best * 1000.0


def bench(n):
    tmp = tempfile.TemporaryDirectory()
    con = db.connect(os.path.join(tmp.name, "cabinet.sqlite3"))
    fill(con, n, seed=n)
    cases = [
        ("page 1, no filter", "", False, ""),
        ("page 1, search ~10%", "shelf 3", False, ""),
        ("page 1, search no match", "zzz-no-match", False, ""),
        ("page 1, flagged only", "", True, ""),
        ("page 1, expiry sort", "", False, "expiry"),
    ]
    print("synthetic items: %d (invented demo data, declared test size)" % n)
    print("%-24s %12s %12s %8s" % ("case", "0.1.1 path", "SQL query", "speedup"))
    for label, q, flagged, sort in cases:
        def old_path():
            rows = app.filter_items(db.active_items(con), q, flagged, sort)
            return rows[:PAGE]
        def new_path():
            return db.query_active_items(con, q, flagged, sort, limit=PAGE, offset=0)
        # same visible page?
        old_ids = [r["id"] for r in old_path()]
        total, rows = new_path()
        assert old_ids == [r["id"] for r in rows], label
        old_ms = timeit(old_path, repeats=5)
        new_ms = timeit(new_path, repeats=5)
        print("%-24s %9.1f ms %9.1f ms %7.1fx" % (label, old_ms, new_ms, old_ms / new_ms))
    con.close()
    tmp.cleanup()


if __name__ == "__main__":
    for arg in sys.argv[1:] or ["2000", "20000"]:
        bench(int(arg))
        print()
