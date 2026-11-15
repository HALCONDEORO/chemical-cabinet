"""Bounded additive CSV import. No attachments, updates or inferred dates.

Uses the standard-library CSV parser and one SQLite transaction, not add_item
(which commits each row). HTTP callers must retain their Host/Origin/CSRF and
RESTORE_INCOMPLETE guards. A successful repeated import adds repeated products.
"""
import argparse
import csv
import io
from pathlib import Path
import sqlite3
import sys

from . import db

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 1000
LIMITS = {"name": 200, "location": 500, "quantity": 200, "notes": 10000,
          "opened_on": 10, "expires_on": 10}
FIELDS = tuple(LIMITS)


class CsvImportError(ValueError):
    """A safe-to-display input error, without echoing inventory cell contents."""


def parse_csv(raw):
    """Return validated dictionaries from UTF-8 CSV bytes without any writes.

    Header names are case-insensitive with surrounding whitespace ignored.
    All unknown/duplicate headers are refused rather than silently losing data.
    Line numbers refer to the first physical line of a record, including quoted
    multiline records. Empty records are skipped; an empty import is refused.
    """
    if not isinstance(raw, bytes):
        raise TypeError("CSV input must be bytes")
    if len(raw) > MAX_BYTES:
        raise CsvImportError("CSV exceeds the 2 MiB limit")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CsvImportError("CSV must be UTF-8; save it as CSV UTF-8 and try again") from None
    if any(ord(c) < 32 and c not in "\t\r\n" for c in text) or "\x7f" in text:
        raise CsvImportError("CSV contains unsupported control characters")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader, None)
        if not header:
            raise CsvImportError("CSV is empty; the first row must contain a name header")
        header = [h.strip().lower() for h in header]
        if len(set(header)) != len(header):
            raise CsvImportError("CSV has duplicate column headers")
        if any(h not in FIELDS for h in header):
            raise CsvImportError("Unsupported column header; use comma-separated name, location, quantity, notes, opened_on, expires_on only")
        if "name" not in header:
            raise CsvImportError("CSV requires a name column")
        rows = []
        while True:
            line = reader.line_num + 1
            cells = next(reader, None)
            if cells is None:
                break
            if not cells or all(not c.strip() for c in cells):
                continue
            if len(cells) != len(header):
                raise CsvImportError("Line %d: expected %d columns, found %d" % (line, len(header), len(cells)))
            row = {field: "" for field in FIELDS}
            row.update(zip(header, (cell.strip() for cell in cells)))
            if not row["name"]:
                raise CsvImportError("Line %d: name is required" % line)
            for field, limit in LIMITS.items():
                if len(row[field]) > limit:
                    raise CsvImportError("Line %d: %s exceeds %d characters" % (line, field, limit))
            for field in ("opened_on", "expires_on"):
                try:
                    db.parse_date(row[field])
                except ValueError:
                    raise CsvImportError("Line %d: %s must be a real YYYY-MM-DD date or empty" % (line, field)) from None
            try:
                row = db.validate_item(**row)
            except ValueError:
                raise CsvImportError("Line %d: invalid product fields" % line) from None
            rows.append(row)
            if len(rows) > MAX_ROWS:
                raise CsvImportError("CSV exceeds the 1000-product limit")
    except csv.Error:
        raise CsvImportError("CSV syntax error near line %d; check quoting and commas" % reader.line_num) from None
    if not rows:
        raise CsvImportError("CSV contains no products")
    return rows


def import_csv(con, raw, today=None, *, data_dir=None):
    """Validate all bytes, then add all products or none; return new IDs.

    Requires an idle connection to an existing cabinet schema. Never commits a
    caller's pending transaction. Dates are copied, quantities stay text. Does
    not interpret formulas, restore attachment paths, match names or update IDs.
    data_dir enables the restore-marker recheck after acquiring the write lock.
    CLI callers always provide it; other callers must guard their data directory.
    This does not lock out restores or marker changes after the recheck.
    """
    rows = parse_csv(raw)
    if con.in_transaction:
        raise ValueError("CSV import requires a connection without a pending transaction")
    con.execute("PRAGMA foreign_keys = ON")
    if con.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise ValueError("CSV import requires SQLite foreign-key enforcement")
    ids = []
    with con:
        con.execute("BEGIN IMMEDIATE")
        if data_dir is not None:
            _check_restore_marker(Path(data_dir))
        for row in rows:
            ids.append(db.insert_item(con, today=today, **row))
    return ids


def _check_restore_marker(data_dir):
    marker = data_dir / "RESTORE_INCOMPLETE"
    if marker.exists() or marker.is_symlink():
        raise CsvImportError("RESTORE_INCOMPLETE marker present; restore into a new directory before importing")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate CSV; add products only with --apply. Repeating --apply adds duplicates.")
    parser.add_argument("csv_file", type=Path)
    parser.add_argument("--data", type=Path, default=Path("data"), help="existing cabinet data folder (used only with --apply)")
    parser.add_argument("--apply", action="store_true", help="add all validated products to the existing cabinet")
    args = parser.parse_args(argv)
    try:
        with args.csv_file.open("rb") as src:
            raw = src.read(MAX_BYTES + 1)
        rows = parse_csv(raw)
        if not args.apply:
            print("Valid CSV: %d products. No changes made. Use --apply to add them; repeated imports add duplicates." % len(rows))
            return 0
        _check_restore_marker(args.data)
        database = args.data / "cabinet.sqlite3"
        if database.is_symlink() or not database.is_file():
            raise CsvImportError("Expected an existing regular cabinet.sqlite3 database; start the cabinet first")
        con = sqlite3.connect(database.resolve().as_uri() + "?mode=rw", uri=True)
        try:
            ids = import_csv(con, raw, data_dir=args.data)
        finally:
            con.close()
        print("Added %d products. Photos and safety data sheets must be attached separately." % len(ids))
        return 0
    except (ValueError, OSError, sqlite3.Error) as exc:
        # Do not echo filenames, SQL trigger messages or cell values from failed input.
        detail = str(exc) if isinstance(exc, CsvImportError) else "Could not read CSV or write the existing cabinet; no partial CSV import was committed"
        print("Import refused: " + detail, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
