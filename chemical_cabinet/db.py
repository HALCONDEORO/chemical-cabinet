"""SQLite storage and the review rules. Standard library only."""
import re
import sqlite3
import unicodedata
from datetime import date, timedelta


def fold(text):
    """Lowercase without diacritic marks, so a search typed without accents
    still finds them: 'lejia' finds 'Lejía', 'munchen' finds 'München'."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower())
                   if not unicodedata.combining(c))

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
CREATE INDEX IF NOT EXISTS items_active_name_idx ON items(name) WHERE retired_on IS NULL;
CREATE INDEX IF NOT EXISTS retirements_item_id_idx ON retirements(item_id);
"""

SOON_DAYS = 30


def connect(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.create_function("py_lower", 1, lambda value: value.lower(), deterministic=True)
    con.create_function("py_fold", 1, lambda value: fold(value), deterministic=True)
    con.create_function("expiry_day", 1, _expiry_day, deterministic=True)
    con.create_function("expiry_group", 1, _expiry_group, deterministic=True)
    con.create_function("expiry_invalid", 1, _expiry_invalid, deterministic=True)
    con.executescript(SCHEMA)
    return con


def _text(value, field):
    """Keep descriptive quantities/units lossless; reject non-text and NUL."""
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError("%s must be text without NUL characters" % field)
    return value.strip()


def _item_id(value):
    if type(value) is not int or value < 1:
        raise ValueError("item_id must be a positive integer")
    return value


def parse_date(value):
    """Return a date for YYYY-MM-DD, or None when empty. Raises ValueError otherwise."""
    if value is None:
        return None
    value = _text(value, "date")
    if not value:
        return None
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("date must use YYYY-MM-DD")
    return date.fromisoformat(value)


def _date_text(value):
    parsed = parse_date(value)
    return parsed.isoformat() if parsed is not None else None


def _day(value):
    if value is None:
        return date.today().isoformat()
    if type(value) is not date:
        raise ValueError("today must be a date")
    return value.isoformat()


def read_expiry_date(value):
    """Read known legacy ISO forms without accepting them for new writes.

    Older Python versions may have stored basic YYYYMMDD or ISO week dates.
    Decode only these exact ISO representations, never locale-based dates.
    Unknown/malformed legacy values return None and remain visible for review.
    """
    try:
        return parse_date(value)
    except ValueError:
        if not isinstance(value, str):
            return None
        value = value.strip()
        try:
            if re.fullmatch(r"[0-9]{8}", value):
                return date(int(value[:4]), int(value[4:6]), int(value[6:]))
            week = re.fullmatch(r"([0-9]{4})-W([0-9]{2})-([1-7])", value)
            if week:
                return date.fromisocalendar(*(int(part) for part in week.groups()))
        except ValueError:
            pass
        return None


def _expiry_day(value):
    parsed = read_expiry_date(value)
    return parsed.isoformat() if parsed is not None else None


def _expiry_group(value):
    return 0 if read_expiry_date(value) is not None else (2 if not value else 1)


def _expiry_invalid(value):
    try:
        parse_date(value)
    except ValueError:
        return 1
    return 0


def expiry_sort_key(item):
    """Dated items first (including known legacy ISO), malformed then undated."""
    value = item["expires_on"]
    parsed = read_expiry_date(value)
    group = _expiry_group(value)
    return group, parsed or date.max, item["name"].lower()


def flags(item, today=None):
    """Review flags for one item row. These are reminders, not a compliance verdict."""
    today = today or date.today()
    out = []
    if item["retired_on"]:
        return out
    try:
        exp = parse_date(item["expires_on"])
    except ValueError:
        # Old cabinets may contain formats accepted by older Python versions.
        # Keep them visible for an explicit edit; never guess or rewrite dates.
        out.append("invalid expiry date")
        exp = read_expiry_date(item["expires_on"])
    if exp is None and "invalid expiry date" not in out:
        out.append("no expiry date")
    elif exp is not None and exp < today:
        out.append("expired")
    elif exp is not None and exp <= today + timedelta(days=SOON_DAYS):
        out.append("expires within %d days" % SOON_DAYS)
    if not item["sds"]:
        out.append("no safety data sheet")
    if not item["photo"]:
        out.append("no photo")
    return out


def validate_item(name, location="", quantity="", notes="", opened_on=None,
                  expires_on=None, photo=None, sds=None):
    """Return normalized item fields without any database writes or commits."""
    name = _text(name, "name")
    if not name:
        raise ValueError("name is required")
    return dict(name=name, location=_text(location, "location"),
                quantity=_text(quantity, "quantity"), notes=_text(notes, "notes"),
                opened_on=_date_text(opened_on), expires_on=_date_text(expires_on),
                photo=photo, sds=sds)


def insert_item(con, name, location="", quantity="", notes="", opened_on=None,
                expires_on=None, photo=None, sds=None, today=None):
    """Validate and insert one item WITHOUT commit. Caller owns the transaction.

    For a multi-row import use ``with con:`` around every insert_item call.
    Ordinary single-item callers should use add_item, which commits on success.
    """
    values = validate_item(name, location, quantity, notes, opened_on,
                           expires_on, photo, sds)
    values["created_on"] = _day(today)
    cur = con.execute(
        "INSERT INTO items(name, location, quantity, notes, opened_on, expires_on,"
        " photo, sds, created_on) VALUES (:name,:location,:quantity,:notes,:opened_on,"
        " :expires_on,:photo,:sds,:created_on)", values)
    return cur.lastrowid


def add_item(con, name, location="", quantity="", notes="", opened_on=None,
             expires_on=None, photo=None, sds=None, today=None):
    with con:
        return insert_item(con, name, location, quantity, notes, opened_on,
                           expires_on, photo, sds, today)


def update_item(con, item_id, name, location="", quantity="", notes="", opened_on=None,
                expires_on=None, photo=None, sds=None):
    """Edit an active product. A new photo or sheet replaces the old one only if given."""
    values = validate_item(name, location, quantity, notes, opened_on,
                           expires_on, photo, sds)
    item_id = _item_id(item_id)
    row = con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if row is None:
        raise KeyError(item_id)
    if row["retired_on"]:
        raise ValueError("retired items cannot be edited")
    # Preserve an omitted attachment from the current DB row, not the earlier
    # SELECT snapshot. Retirement may also have committed since that snapshot.
    with con:
        changed = con.execute(
            "UPDATE items SET name=?, location=?, quantity=?, notes=?, opened_on=?, expires_on=?,"
            " photo=COALESCE(NULLIF(?, ''), photo), sds=COALESCE(NULLIF(?, ''), sds)"
            " WHERE id=? AND retired_on IS NULL",
            (values["name"], values["location"], values["quantity"], values["notes"],
             values["opened_on"], values["expires_on"], values["photo"], values["sds"], item_id))
        if changed.rowcount != 1:
            raise ValueError("retired items cannot be edited")


def get_item(con, item_id):
    return con.execute("SELECT * FROM items WHERE id=?", (_item_id(item_id),)).fetchone()


def retire_item(con, item_id, reason="", method="", today=None):
    item_id = _item_id(item_id)
    day = _day(today)
    reason = _text(reason, "reason")
    method = _text(method, "method")
    row = con.execute("SELECT id, retired_on FROM items WHERE id=?", (item_id,)).fetchone()
    if row is None:
        raise KeyError(item_id)
    if row["retired_on"]:
        raise ValueError("already retired")
    # Atomic replacement of the active state, not a grant from the earlier read.
    # Another connection may have retired the item while this call was waiting.
    with con:
        changed = con.execute(
            "UPDATE items SET retired_on=? WHERE id=? AND retired_on IS NULL",
            (day, item_id))
        if changed.rowcount != 1:
            raise ValueError("already retired")
        con.execute("INSERT INTO retirements(item_id, retired_on, reason, method) VALUES (?,?,?,?)",
                    (item_id, day, reason, method))


def active_items(con):
    return con.execute("SELECT * FROM items WHERE retired_on IS NULL ORDER BY name").fetchall()


def _flagged_where(today):
    """Same review predicate as flags(), including invalid legacy dates."""
    soon = (today + timedelta(days=SOON_DAYS)).isoformat()
    return ("(expires_on IS NULL OR trim(expires_on)='' OR expiry_invalid(expires_on)=1"
            " OR expiry_day(expires_on)<=? OR sds IS NULL OR sds=''"
            " OR photo IS NULL OR photo='')", (soon,))


def query_active_items(con, q="", only_flagged=False, sort="", today=None,
                       limit=None, offset=0, location=""):
    """Return (count, rows) with Unicode search, review and stable date paging.

    Uses the same read-only legacy date decoder as expiry_sort_key and flags.
    The caller must use db.connect (SQL helper functions are connection local).
    No mutation of legacy values, quantity interpretation or full-table fetch.
    """
    today = date.fromisoformat(_day(today))
    q = fold(_text(q, "query"))[:100]
    if not isinstance(location, str) or "\x00" in location:
        raise ValueError("location filter must be text without NUL characters")
    location = location[:200]
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("limit must be a positive integer")
    where = ["retired_on IS NULL"]
    params = []
    if q:
        where.append("instr(py_fold(name || ' ' || location || ' ' || notes), ?) > 0")
        params.append(q)
    if location:
        where.append("location = ?")
        params.append(location)
    if only_flagged:
        cond, extra = _flagged_where(today)
        where.append(cond)
        params.extend(extra)
    where_sql = " AND ".join(where)
    total = con.execute("SELECT COUNT(*) FROM items WHERE " + where_sql, params).fetchone()[0]
    if sort == "expiry":
        order = "expiry_group(expires_on), expiry_day(expires_on), py_lower(name), name, id"
    elif sort == "location":
        order = "py_lower(location), py_lower(name), name, id"
    elif sort == "newest":
        order = "created_on DESC, id DESC"
    else:
        order = "name, id"
    sql = "SELECT * FROM items WHERE " + where_sql + " ORDER BY " + order
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params = params + [limit, offset]
    elif offset:
        sql += " LIMIT -1 OFFSET ?"
        params = params + [offset]
    return total, con.execute(sql, params).fetchall()


def retirement_log(con):
    return con.execute(
        "SELECT r.*, i.name FROM retirements r JOIN items i ON i.id=r.item_id"
        " ORDER BY r.id DESC").fetchall()
