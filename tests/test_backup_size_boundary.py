"""Creation and verification must use the same uncompressed-size budget."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from chemical_cabinet import backup, db


class BackupSizeBoundaryTest(unittest.TestCase):
    def test_manifest_bytes_count_toward_archive_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / 'data'
            (data / 'files').mkdir(parents=True)
            db.connect(data / 'cabinet.sqlite3').close()
            archive = root / 'snapshot.zip'
            database_size = (data / 'cabinet.sqlite3').stat().st_size
            with patch.object(backup, 'MAX_TOTAL', database_size):
                with self.assertRaisesRegex(ValueError, 'including manifest'):
                    backup.create_backup(data, archive)
            self.assertFalse(archive.exists())
            self.assertEqual(list(root.glob('.cabinet-backup-*')), [])

    def test_exact_total_limit_is_accepted_and_verifiable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / 'data'
            (data / 'files').mkdir(parents=True)
            db.connect(data / 'cabinet.sqlite3').close()
            archive = root / 'snapshot.zip'
            # Empty database has a fixed-length SHA-256 manifest, regardless of
            # snapshot page content. Use its actual serialized size, not a magic
            # threshold or the compressed ZIP size.
            manifest = {'format': 1, 'sha256': {'cabinet.sqlite3': '0' * 64}}
            limit = (data / 'cabinet.sqlite3').stat().st_size + len(
                json.dumps(manifest, sort_keys=True).encode('utf-8'))
            with patch.object(backup, 'MAX_TOTAL', limit):
                backup.create_backup(data, archive)
                self.assertTrue(backup.verify_backup(archive))
                backup.restore_backup(archive, root / 'restored')
            with zipfile.ZipFile(archive) as src:
                self.assertEqual(sum(info.file_size for info in src.infolist()), limit)


if __name__ == '__main__':
    unittest.main()
