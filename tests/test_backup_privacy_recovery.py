"""Focused regressions for owner-only recovery and interrupted-source refusal."""
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from chemical_cabinet import backup, db


class BackupPrivacyRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / 'data'
        (self.data / 'files').mkdir(parents=True)
        self.name = 'c' * 16 + '.pdf'
        (self.data / 'files' / self.name).write_bytes(b'synthetic attachment')
        con = db.connect(self.data / 'cabinet.sqlite3')
        db.add_item(con, 'Synthetic product', sds=self.name)
        con.close()
        self.archive = self.root / 'snapshot.zip'

    def test_incomplete_source_refused_even_if_contents_are_valid(self):
        (self.data / 'RESTORE_INCOMPLETE').touch()
        with self.assertRaisesRegex(ValueError, 'source restore is incomplete'):
            backup.create_backup(self.data, self.archive)
        self.assertFalse(self.archive.exists())
        self.assertEqual(list(self.root.glob('.cabinet-backup-*')), [])

    def test_dangling_incomplete_marker_is_also_refused(self):
        (self.data / 'RESTORE_INCOMPLETE').symlink_to(self.root / 'missing')
        with self.assertRaisesRegex(ValueError, 'source restore is incomplete'):
            backup.create_backup(self.data, self.archive)
        self.assertFalse(self.archive.exists())

    def test_marker_appearing_before_publication_refuses_snapshot(self):
        original = backup._verify_tree
        def interrupted(root):
            result = original(root)
            (self.data / 'RESTORE_INCOMPLETE').touch()
            return result
        with patch.object(backup, '_verify_tree', side_effect=interrupted):
            with self.assertRaisesRegex(ValueError, 'source restore is incomplete'):
                backup.create_backup(self.data, self.archive)
        self.assertFalse(self.archive.exists())
        self.assertEqual(list(self.root.glob('.cabinet-backup-*')), [])

    @unittest.skipUnless(os.name == 'posix', 'POSIX permission bits required')
    def test_private_outputs_even_with_permissive_umask(self):
        previous = os.umask(0)
        try:
            backup.create_backup(self.data, self.archive)
            target = self.root / 'restored'
            backup.restore_backup(self.archive, target)
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE(self.archive.stat().st_mode), 0o600)
        for directory in (target, target / 'files'):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        for name in ('cabinet.sqlite3', 'manifest.json', 'files/' + self.name):
            self.assertEqual(stat.S_IMODE((target / name).stat().st_mode), 0o600)
        with zipfile.ZipFile(self.archive) as archive:
            for info in archive.infolist():
                self.assertEqual(stat.S_IMODE(info.external_attr >> 16), 0o600)
        self.assertEqual((target / 'files' / self.name).read_bytes(), b'synthetic attachment')

    @unittest.skipUnless(os.name == 'posix', 'POSIX permission bits required')
    def test_interruption_marker_is_private_and_remains(self):
        backup.create_backup(self.data, self.archive)
        original = backup.shutil.copyfileobj
        target = self.root / 'restored'
        def interrupt(source, destination, *args):
            if str(target) in str(destination.name):
                raise KeyboardInterrupt('synthetic interruption')
            return original(source, destination, *args)
        previous = os.umask(0)
        try:
            with patch.object(backup.shutil, 'copyfileobj', side_effect=interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    backup.restore_backup(self.archive, target)
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
        marker = target / 'RESTORE_INCOMPLETE'
        self.assertTrue(marker.exists())
        self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)


if __name__ == '__main__':
    unittest.main()
