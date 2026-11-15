"""Offline-verified snapshots. Standard library only; no inventory is sent anywhere.

Backups use SQLite's backup API, never a raw live database copy. STOP web/MCP
writers before creating a backup: editing can remove old attachments. Source-file
checks detect observed changes, not all races. This is not a DB/files transaction.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
import time
import zipfile

FILE_NAME = re.compile(r"[0-9a-f]{16}\.(?:jpg|png|webp|pdf)\Z")
MAX_FILES = 10000
MAX_TOTAL = 1024 * 1024 * 1024
MAX_MANIFEST = 2 * 1024 * 1024


def _hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as src:
        for chunk in iter(lambda: src.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _references(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as con:
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("database integrity check failed")
        # Check both core tables before accepting a restore candidate.
        con.execute("SELECT item_id, retired_on, reason, method FROM retirements LIMIT 0")
        refs = set()
        for row in con.execute("SELECT photo, sds FROM items"):
            for name in row:
                if name is not None:
                    if not isinstance(name, str) or not FILE_NAME.fullmatch(name):
                        raise ValueError("unsafe attachment reference")
                    refs.add("files/" + name)
    return refs


def _verify_tree(root):
    manifest_path = root / "manifest.json"
    if manifest_path.stat().st_size > MAX_MANIFEST:
        raise ValueError("manifest too large")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or set(manifest) != {"format", "sha256"} or manifest["format"] != 1:
        raise ValueError("unsupported manifest")
    hashes = manifest["sha256"]
    if not isinstance(hashes, dict) or len(hashes) > MAX_FILES:
        raise ValueError("invalid manifest file list")
    expected = {"cabinet.sqlite3"} | _references(root / "cabinet.sqlite3")
    if set(hashes) != expected:
        raise ValueError("manifest does not match database references")
    for name, digest in hashes.items():
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid digest")
        if _hash(root / name) != digest:
            raise ValueError("checksum mismatch: " + name)
    return expected


def create_backup(data_dir, destination, timeout=30):
    """Create a ZIP at a new path. Existing outputs are never overwritten.

    Stop all inventory writers first; file checks are not a cross-system lock.
    1 GiB uncompressed / 10,000-entry limits are deliberate safety limits. The
    filesystem must support hard links for atomic, exclusive archive publication.
    """
    source, target = Path(data_dir), Path(destination)
    database = source / "cabinet.sqlite3"
    if database.is_symlink() or not database.is_file() or (source / "files").is_symlink():
        raise ValueError("expected regular database and non-symlink files directory")
    with tempfile.TemporaryDirectory(prefix=".cabinet-backup-", dir=target.parent) as tmp:
        stage = Path(tmp)
        db_path = stage / "cabinet.sqlite3"
        deadline = time.monotonic() + timeout
        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise TimeoutError("snapshot timed out; retry when writers are idle")
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(db_path)) as dst:
                src.backup(dst, pages=128, progress=progress, sleep=0.05)
        names = {"cabinet.sqlite3"} | _references(db_path)
        if len(names) + 1 > MAX_FILES:
            raise ValueError("too many files")
        (stage / "files").mkdir()
        total = db_path.stat().st_size
        sources = {}
        for name in sorted(names - {"cabinet.sqlite3"}):
            file = source / name
            if file.is_symlink() or not file.is_file():
                raise ValueError("missing or symlink attachment: " + name)
            before = file.stat()
            digest = _hash(file)
            total += before.st_size
            if total > MAX_TOTAL:
                raise ValueError("backup too large")
            shutil.copyfile(file, stage / name)
            if _hash(stage / name) != digest:
                raise ValueError("attachment changed while copying: " + name)
            sources[name] = (before, digest)
        if total > MAX_TOTAL:
            raise ValueError("backup too large")
        if sum((stage / name).stat().st_size for name in names) > MAX_TOTAL:
            raise ValueError("backup grew beyond size limit")
        manifest = {"format": 1, "sha256": {name: _hash(stage / name) for name in sorted(names)}}
        (stage / "manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
        _verify_tree(stage)
        # Current web edits can remove old files. Recheck all references at the
        # end as well as each individual copy. This detects observed races only.
        for name, (before, digest) in sources.items():
            file = source / name
            if file.is_symlink() or not file.is_file():
                raise ValueError("attachment disappeared during backup: " + name)
            after = file.stat()
            if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                    or _hash(file) != digest):
                raise ValueError("attachment changed during backup: " + name)
        archive = stage / "snapshot.zip"
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as out:
            for name in sorted(names | {"manifest.json"}):
                out.write(stage / name, name)
        os.link(archive, target)
    return target


def _unpack(archive, stage):
    with zipfile.ZipFile(archive) as src:
        infos = src.infolist()
        names = [info.filename for info in infos]
        if len(names) > MAX_FILES or len(set(names)) != len(names):
            raise ValueError("too many or duplicate archive entries")
        if sum(info.file_size for info in infos) > MAX_TOTAL:
            raise ValueError("archive too large")
        for info in infos:
            name = info.filename
            if name not in {"manifest.json", "cabinet.sqlite3"} and not (
                name.startswith("files/") and FILE_NAME.fullmatch(name[6:])):
                raise ValueError("unsafe archive path")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode) or info.is_dir():
                raise ValueError("non-regular archive entry")
            if name == "manifest.json" and info.file_size > MAX_MANIFEST:
                raise ValueError("manifest too large")
            dest = stage / name
            dest.parent.mkdir(exist_ok=True)
            with src.open(info) as source, open(dest, "xb") as output:
                shutil.copyfileobj(source, output, 65536)
        expected = _verify_tree(stage) | {"manifest.json"}
        if set(names) != expected:
            raise ValueError("unexpected or missing archive contents")


def verify_backup(archive):
    """Validate paths, bounds, checksums, SQLite integrity and attachment links."""
    with tempfile.TemporaryDirectory(prefix="cabinet-verify-") as tmp:
        _unpack(archive, Path(tmp))
    return True


def restore_backup(archive, destination):
    """Restore only to a new directory, after validating a private staged copy.

    Not a crash-atomic directory publication: RESTORE_INCOMPLETE remains if the
    process dies. Never point a running app at the destination until this returns.
    Existing destinations, including empty directories and symlinks, are refused.
    """
    target = Path(destination)
    with tempfile.TemporaryDirectory(prefix=".cabinet-restore-", dir=target.parent) as tmp:
        stage = Path(tmp)
        _unpack(archive, stage)
        target.mkdir()  # Atomic exclusive reservation, not exists()+rename().
        try:
            (target / "RESTORE_INCOMPLETE").touch(exist_ok=False)
            (target / "files").mkdir()
            for name in _verify_tree(stage) | {"manifest.json"}:
                with open(stage / name, "rb") as src, open(target / name, "xb") as dst:
                    shutil.copyfileobj(src, dst, 65536)
            _verify_tree(target)
            (target / "RESTORE_INCOMPLETE").unlink()
        except Exception:
            shutil.rmtree(target)
            raise
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    for command in ("create", "restore"):
        sub = subs.add_parser(command)
        sub.add_argument("source")
        sub.add_argument("destination")
    subs.add_parser("verify").add_argument("source")
    args = parser.parse_args(argv)
    if args.command == "create":
        create_backup(args.source, args.destination)
    elif args.command == "restore":
        restore_backup(args.source, args.destination)
    else:
        verify_backup(args.source)


if __name__ == "__main__":
    main()
