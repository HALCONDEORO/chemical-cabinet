import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from chemical_cabinet import backup, db


class BackupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / 'data'
        (self.data / 'files').mkdir(parents=True)
        self.name = 'a' * 16 + '.pdf'
        (self.data / 'files' / self.name).write_bytes(b'%PDF-synthetic')
        con = db.connect(self.data / 'cabinet.sqlite3')
        self.item = db.add_item(con, 'Synthetic cleaner', sds=self.name)
        db.retire_item(con, self.item, 'old', 'collection')
        con.close()
        self.archive = self.root / 'backup.zip'

    def snapshot(self):
        backup.create_backup(self.data, self.archive)
        return self.archive

    def rewrite(self, change):
        self.snapshot()
        with zipfile.ZipFile(self.archive) as src:
            contents = {i.filename: src.read(i) for i in src.infolist()}
        change(contents)
        with zipfile.ZipFile(self.archive, 'w') as dst:
            for name, payload in contents.items():
                dst.writestr(name, payload)

    def test_roundtrip_includes_retirements_and_only_references(self):
        (self.data / 'files' / ('b' * 16 + '.pdf')).write_bytes(b'unreferenced')
        self.snapshot()
        self.assertTrue(backup.verify_backup(self.archive))
        restored = self.root / 'restored'
        backup.restore_backup(self.archive, restored)
        con = db.connect(restored / 'cabinet.sqlite3')
        self.addCleanup(con.close)
        self.assertEqual(db.retirement_log(con)[0]['method'], 'collection')
        self.assertEqual((restored / 'files' / self.name).read_bytes(), b'%PDF-synthetic')
        self.assertEqual(list((restored / 'files').iterdir()), [restored / 'files' / self.name])
        self.assertFalse((restored / 'RESTORE_INCOMPLETE').exists())

    def test_existing_archive_or_target_never_overwritten(self):
        self.snapshot()
        original = self.archive.read_bytes()
        with self.assertRaises(FileExistsError):
            backup.create_backup(self.data, self.archive)
        self.assertEqual(self.archive.read_bytes(), original)
        target = self.root / 'occupied'
        target.mkdir()
        (target / 'precious').write_bytes(b'keep')
        with self.assertRaises(FileExistsError):
            backup.restore_backup(self.archive, target)
        self.assertEqual((target / 'precious').read_bytes(), b'keep')

    def test_missing_source_does_not_create_database(self):
        (self.data / 'cabinet.sqlite3').unlink()
        with self.assertRaises(ValueError):
            self.snapshot()
        self.assertFalse((self.data / 'cabinet.sqlite3').exists())
        self.assertFalse(self.archive.exists())

    def test_missing_and_symlink_attachment_refused(self):
        file = self.data / 'files' / self.name
        file.unlink()
        with self.assertRaises(ValueError):
            self.snapshot()
        file.symlink_to(self.root / 'outside')
        (self.root / 'outside').write_bytes(b'private')
        with self.assertRaises(ValueError):
            self.snapshot()
        self.assertFalse(self.archive.exists())

    def test_checksum_tampering_refused_before_target_creation(self):
        self.rewrite(lambda files: files.update({'files/' + self.name: b'changed'}))
        with self.assertRaises(ValueError):
            backup.restore_backup(self.archive, self.root / 'restore')
        self.assertFalse((self.root / 'restore').exists())

    def test_archive_path_traversal_refused(self):
        self.rewrite(lambda files: files.update({'../escape': b'x'}))
        with self.assertRaises(ValueError):
            backup.verify_backup(self.archive)
        self.assertFalse((self.root / 'escape').exists())

    def test_duplicate_and_symlink_zip_entries_refused(self):
        self.snapshot()
        with zipfile.ZipFile(self.archive, 'a') as dst:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                dst.writestr('manifest.json', b'{}')
        with self.assertRaises(ValueError):
            backup.verify_backup(self.archive)
        self.archive.unlink()
        self.snapshot()
        with zipfile.ZipFile(self.archive, 'a') as dst:
            info = zipfile.ZipInfo('files/' + 'b' * 16 + '.pdf')
            info.external_attr = 0o120777 << 16
            dst.writestr(info, '/private')
        with self.assertRaises(ValueError):
            backup.verify_backup(self.archive)

    def test_manifest_and_total_bounds(self):
        self.snapshot()
        with patch.object(backup, 'MAX_MANIFEST', 4):
            with self.assertRaises(ValueError):
                backup.verify_backup(self.archive)
        with patch.object(backup, 'MAX_TOTAL', 4):
            with self.assertRaises(ValueError):
                backup.verify_backup(self.archive)

    def test_source_attachment_changed_during_copy_fails_closed(self):
        original_copy = backup.shutil.copyfile
        def changing_source(source, destination):
            result = original_copy(source, destination)
            Path(source).write_bytes(b'concurrent replacement')
            return result
        with patch.object(backup.shutil, 'copyfile', side_effect=changing_source):
            with self.assertRaises(ValueError):
                self.snapshot()
        self.assertFalse(self.archive.exists())

    def test_source_attachment_deleted_after_copy_fails_closed(self):
        original_copy = backup.shutil.copyfile
        def deleting_source(source, destination):
            result = original_copy(source, destination)
            Path(source).unlink()
            return result
        with patch.object(backup.shutil, 'copyfile', side_effect=deleting_source):
            with self.assertRaises(ValueError):
                self.snapshot()
        self.assertFalse(self.archive.exists())

    def test_restore_exception_cleans_reserved_target(self):
        self.snapshot()
        real_copy = backup.shutil.copyfileobj
        def fail_publication(source, output, *args):
            if '/restore/' in str(output.name):
                output.write(b'partial')
                raise OSError('synthetic disk failure')
            return real_copy(source, output, *args)
        with patch.object(backup.shutil, 'copyfileobj', side_effect=fail_publication):
            with self.assertRaises(OSError):
                backup.restore_backup(self.archive, self.root / 'restore')
        self.assertFalse((self.root / 'restore').exists())

    def test_process_interrupt_leaves_incomplete_marker(self):
        self.snapshot()
        real_copy = backup.shutil.copyfileobj
        def interrupt_publication(source, output, *args):
            if '/restore/' in str(output.name):
                output.write(b'partial')
                raise KeyboardInterrupt('synthetic process interruption')
            return real_copy(source, output, *args)
        with patch.object(backup.shutil, 'copyfileobj', side_effect=interrupt_publication):
            with self.assertRaises(KeyboardInterrupt):
                backup.restore_backup(self.archive, self.root / 'restore')
        self.assertTrue((self.root / 'restore' / 'RESTORE_INCOMPLETE').exists())
        with self.assertRaises(FileExistsError):
            backup.restore_backup(self.archive, self.root / 'restore')

    def test_restore_symlink_destination_is_not_followed(self):
        self.snapshot()
        outside = self.root / 'outside'
        outside.mkdir()
        (self.root / 'restore').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(FileExistsError):
            backup.restore_backup(self.archive, self.root / 'restore')
        self.assertEqual(list(outside.iterdir()), [])

    def test_unsupported_hardlink_fails_closed(self):
        with patch.object(backup.os, 'link', side_effect=OSError('unsupported')):
            with self.assertRaises(OSError):
                self.snapshot()
        self.assertFalse(self.archive.exists())
        self.assertEqual(list(self.root.glob('.cabinet-backup-*')), [])

    def test_missing_archive_attachment_refused(self):
        self.rewrite(lambda files: files.pop('files/' + self.name))
        with self.assertRaises((ValueError, FileNotFoundError)):
            backup.restore_backup(self.archive, self.root / 'restore')
        self.assertFalse((self.root / 'restore').exists())

    def test_tampered_database_is_not_accepted_with_new_checksum(self):
        def corrupt(files):
            import hashlib
            files['cabinet.sqlite3'] = b'not SQLite'
            manifest = json.loads(files['manifest.json'])
            manifest['sha256']['cabinet.sqlite3'] = hashlib.sha256(b'not SQLite').hexdigest()
            files['manifest.json'] = json.dumps(manifest).encode()
        self.rewrite(corrupt)
        with self.assertRaises(sqlite3.DatabaseError):
            backup.restore_backup(self.archive, self.root / 'restore')
        self.assertFalse((self.root / 'restore').exists())

    def test_raw_copy_loses_wal_commits_backup_api_keeps_them(self):
        con = db.connect(self.data / 'cabinet.sqlite3')
        self.addCleanup(con.close)
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('PRAGMA wal_autocheckpoint=0')
        db.add_item(con, 'Committed in WAL')
        raw = self.root / 'raw.sqlite3'
        shutil.copyfile(self.data / 'cabinet.sqlite3', raw)
        with sqlite3.connect(raw) as stale:
            self.assertEqual(stale.execute('SELECT count(*) FROM items').fetchone()[0], 1)
        self.snapshot()
        backup.restore_backup(self.archive, self.root / 'restored')
        with sqlite3.connect(self.root / 'restored' / 'cabinet.sqlite3') as restored:
            self.assertEqual(restored.execute('SELECT count(*) FROM items').fetchone()[0], 2)

    def test_raw_delete_journal_copy_can_include_uncommitted_partial_update(self):
        con = db.connect(self.data / 'cabinet.sqlite3')
        self.addCleanup(con.close)
        con.execute('PRAGMA cache_size=5')
        con.execute('CREATE TABLE synthetic(id INTEGER PRIMARY KEY, value TEXT)')
        con.executemany('INSERT INTO synthetic VALUES (?,?)', [(i, 'a' * 1000) for i in range(1000)])
        con.commit()
        con.execute('BEGIN IMMEDIATE')
        con.execute('UPDATE synthetic SET value=?', ('b' * 1000,))
        raw = self.root / 'raw.sqlite3'
        shutil.copyfile(self.data / 'cabinet.sqlite3', raw)
        with sqlite3.connect(raw) as mixed:
            values = dict(mixed.execute('SELECT substr(value,1,1),count(*) FROM synthetic GROUP BY 1'))
            self.assertTrue(0 < values.get('b', 0) < 1000, values)
        started = threading.Event()
        results = []
        def capture():
            started.set()
            try:
                self.snapshot()
            except Exception as exc:
                results.append(exc)
        worker = threading.Thread(target=capture)
        worker.start()
        started.wait(5)
        con.rollback()
        worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [])
        backup.restore_backup(self.archive, self.root / 'restored')
        with sqlite3.connect(self.root / 'restored' / 'cabinet.sqlite3') as restored:
            values = restored.execute('SELECT substr(value,1,1),count(*) FROM synthetic GROUP BY 1').fetchall()
            self.assertEqual(values, [('a', 1000)])


if __name__ == '__main__':
    unittest.main()


class BackupCliTest(unittest.TestCase):
    def run_cli(self, *args):
        import subprocess, sys
        return subprocess.run([sys.executable, "-m", "chemical_cabinet.backup", *args],
                              capture_output=True, text=True)

    def test_refusals_print_one_line_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp, "not-a-backup.zip")
            bad.write_bytes(b"not a zip")
            for args in (("verify", str(bad)), ("verify", str(Path(tmp, "missing.zip"))),
                         ("restore", str(bad), str(Path(tmp, "out"))),
                         ("create", str(Path(tmp, "no-such-data")), str(Path(tmp, "b.zip")))):
                with self.subTest(args=args):
                    r = self.run_cli(*args)
                    self.assertEqual(r.returncode, 1)
                    self.assertTrue(r.stderr.startswith("error: "), r.stderr)
                    self.assertNotIn("Traceback", r.stderr)
            self.assertFalse(Path(tmp, "out").exists())

    def test_restore_over_existing_directory_is_refused_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp, "data")
            (data / "files").mkdir(parents=True)
            db.connect(str(data / "cabinet.sqlite3")).close()
            archive = Path(tmp, "b.zip")
            self.assertEqual(self.run_cli("create", str(data), str(archive)).returncode, 0)
            existing = Path(tmp, "existing")
            existing.mkdir()
            r = self.run_cli("restore", str(archive), str(existing))
            self.assertEqual(r.returncode, 1)
            self.assertNotIn("Traceback", r.stderr)
            self.assertEqual(list(existing.iterdir()), [])
