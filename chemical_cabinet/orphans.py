"""Read-only report of uploaded files that no product refers to.

    python -m chemical_cabinet.orphans --data /absolute/path/to/data

It never deletes, moves or changes anything: the database is opened read-only
and the files folder is only listed. Concurrent edits can leave an upload that
no product refers to (it only uses disk space). A file that was written a
moment ago may still be waiting for its database row, so files newer than
--min-age-minutes (default 60) are reported as "recent", never as orphans.
The scan is a snapshot of a folder that other processes may be changing; it does not defend against someone replacing the folder while it runs. The report gives file names, sizes and ages only: no product names or notes.
"""
import argparse
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

SAFE_NAME = re.compile(r"^[0-9a-f]{16}\.[a-z0-9]{2,5}$")


def _open_ro(db_path):
    """Open the database read-only; the path is percent-encoded, never trusted as a URI."""
    return sqlite3.connect(Path(db_path).as_uri() + "?mode=ro", uri=True)


def _refs(db_path):
    con = _open_ro(db_path)
    try:
        con.execute("PRAGMA query_only = ON")
        out = set()
        for photo, sds in con.execute("SELECT photo, sds FROM items"):
            for name in (photo, sds):
                if name:
                    out.add(name)
        return out
    finally:
        con.close()


def scan(data_dir, min_age_seconds=3600, now=None):
    """Return a dict describing references versus files. Changes nothing."""
    data_dir = os.path.abspath(data_dir)
    db_path = os.path.join(data_dir, "cabinet.sqlite3")
    files_dir = os.path.join(data_dir, "files")
    if not os.path.isfile(db_path) or os.path.islink(db_path):
        raise ValueError("no regular cabinet.sqlite3 in %s" % data_dir)
    if os.path.islink(files_dir) or not os.path.isdir(files_dir):
        raise ValueError("files is missing or not a plain folder in %s" % data_dir)
    now = time.time() if now is None else now
    refs = _refs(db_path)
    on_disk = {}
    other = []
    for entry in os.scandir(files_dir):
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError as exc:
            # Unknown is not "missing" and not "orphan": stop instead of guessing.
            raise ValueError("cannot read %r (%s); the scan is incomplete" % (entry.name, exc.strerror or exc))
        if entry.is_symlink() or not entry.is_file(follow_symlinks=False) or not SAFE_NAME.match(entry.name):
            other.append(entry.name)
        else:
            on_disk[entry.name] = (st.st_size, max(0, now - st.st_mtime))
    # Read the references once more: an edit that committed while we listed
    # the folder must not turn its new file into an orphan.
    refs |= _refs(db_path)
    orphans, recent = [], []
    for name, (size, age) in sorted(on_disk.items()):
        if name in refs:
            continue
        (orphans if age >= min_age_seconds else recent).append((name, size, int(age)))
    # A reference that is not in the listing may belong to an upload that was
    # written and committed after the listing; look at the disk again for it.
    missing = sorted(n for n in refs if n not in on_disk
                     and not os.path.lexists(os.path.join(files_dir, n)))
    return {"referenced": len(refs), "on_disk": len(on_disk), "orphans": orphans,
            "recent": recent, "missing": missing, "other": sorted(other)}


def render(result, min_age_seconds):
    out = ["Referenced by products: %d" % result["referenced"],
           "Upload files on disk: %d" % result["on_disk"],
           "Not referenced and older than %d s: %d (%d bytes)" % (
               min_age_seconds, len(result["orphans"]), sum(s for _, s, _ in result["orphans"])),
           "Not referenced but recent (may be in progress): %d" % len(result["recent"]),
           "Referenced but missing on disk: %d" % len(result["missing"]),
           "Other entries (symlinks, odd names; not touched): %d" % len(result["other"])]
    for title, rows in (("Unreferenced", result["orphans"]), ("Recent", result["recent"])):
        for name, size, age in rows:
            out.append("  %s %s  %d bytes  %d s old" % (title.lower(), name, size, age))
    for name in result["missing"]:
        out.append("  missing %s" % name)
    for name in result["other"]:
        out.append("  other %r" % name)
    out.append("Nothing was changed. Review before removing anything yourself, and keep a backup first.")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="List uploaded files no product refers to (read-only).")
    ap.add_argument("--data", required=True, help="the data folder that holds cabinet.sqlite3 and files/")
    ap.add_argument("--min-age-minutes", type=int, default=60,
                    help="files newer than this are reported as recent, not as orphans")
    args = ap.parse_args(argv)
    if args.min_age_minutes < 0:
        ap.error("--min-age-minutes must be 0 or more")
    secs = args.min_age_minutes * 60
    try:
        print(render(scan(args.data, secs), secs))
    except (ValueError, sqlite3.Error, OSError) as exc:
        print("Cannot scan: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
