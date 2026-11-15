"""SQLite storage and the review rules. Standard library only."""
import sqlite3
from datetime import date, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  location TEXT NOT NULL DEFAULT '',
  quantity TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  opened_on TEXT,
  expires_on TEXT,
  photo TEXT,
  sds TEXT,
  created_on TEXT NOT NULL,
  retired_on TEXT
);
CREATE TABLE IF NOT EXISTS retirements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER NOT NULL REFERENCES items(id),
  retired_on TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  method TEXT NOT NULL DEFAULT ''
);
"""

SOON_DAYS = 30


def connect(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def parse_date(value):
    """Return a date for YYYY-MM-DD, or None when empty. Raises ValueError otherwise."""
    value = (value or "").strip()
    if not value:
        return None
    return date.fromisoformat(value)


def flags(item, today=None):
    """Review flags for one item row. These are reminders, not a compliance verdict."""
    today = today or date.today()
    out = []
    if item["retired_on"]:
        return out
    exp = parse_date(item["expires_on"])
    if exp is None:
        out.append("no expiry date")
    elif exp < today:
        out.append("expired")
    elif exp <= today + timedelta(days=SOON_DAYS):
        out.append("expires within %d days" % SOON_DAYS)
    if not item["sds"]:
        out.append("no safety data sheet")
    if not item["photo"]:
        out.append("no photo")
    return out


def add_item(con, name, location="", quantity="", notes="", opened_on=None,
             expires_on=None, photo=None, sds=None, today=None):
    name = name.strip()
    if not name:
        raise ValueError("name is required")
    parse_date(opened_on)
    parse_date(expires_on)
    cur = con.execute(
        "INSERT INTO items(name, location, quantity, notes, opened_on, expires_on,"
        " photo, sds, created_on) VALUES (?,?,?,?,?,?,?,?,?)",
        (name, location.strip(), quantity.strip(), notes.strip(),
         (opened_on or "").strip() or None, (expires_on or "").strip() or None,
         photo, sds, (today or date.today()).isoformat()))
    con.commit()
    return cur.lastrowid


def retire_item(con, item_id, reason="", method="", today=None):
    day = (today or date.today()).isoformat()
    row = con.execute("SELECT id, retired_on FROM items WHERE id=?", (item_id,)).fetchone()
    if row is None:
        raise KeyError(item_id)
    if row["retired_on"]:
        raise ValueError("already retired")
    con.execute("UPDATE items SET retired_on=? WHERE id=?", (day, item_id))
    con.execute("INSERT INTO retirements(item_id, retired_on, reason, method) VALUES (?,?,?,?)",
                (item_id, day, reason.strip(), method.strip()))
    con.commit()


def active_items(con):
    return con.execute("SELECT * FROM items WHERE retired_on IS NULL ORDER BY name").fetchall()


def retirement_log(con):
    return con.execute(
        "SELECT r.*, i.name FROM retirements r JOIN items i ON i.id=r.item_id"
        " ORDER BY r.id DESC").fetchall()
