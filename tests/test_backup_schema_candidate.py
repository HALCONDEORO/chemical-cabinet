"""Structurally intact SQLite is not necessarily an application backup."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

from chemical_cabinet import backup, db


class BackupSchemaCandidateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / 'data'
        (self.data / 'files').mkdir(parents=True)
        self.database = self.data / 'cabinet.sqlite3'
        self.archive = self.root / 'snapshot.zip'

    def make_invalid(self, kind):
        con = db.connect(self.database)
        with con:
            if kind == 'items':
                con.executescript('DROP TABLE items; CREATE TABLE items(photo TEXT, sds TEXT);')
            elif kind == 'retirements':
                con.executescript('DROP TABLE retirements; CREATE TABLE retirements('
                                  'item_id INTEGER, retired_on TEXT, reason TEXT, method TEXT);')
            elif kind == 'view':
                con.executescript('ALTER TABLE items RENAME TO hidden_items; '
                                  'CREATE VIEW items AS SELECT * FROM hidden_items;')
        self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        con.close()

    def test_create_refuses_incompatible_core_schema(self):
        for kind in ('items', 'retirements', 'view'):
            with self.subTest(kind=kind):
                self.database.unlink(missing_ok=True)
                self.archive.unlink(missing_ok=True)
                self.make_invalid(kind)
                with self.assertRaises((ValueError, sqlite3.DatabaseError)):
                    backup.create_backup(self.data, self.archive)
                self.assertFalse(self.archive.exists())
                self.database.unlink()

    def test_verify_and_restore_refuse_checksum_valid_incompatible_database(self):
        for kind in ('items', 'retirements', 'view'):
            with self.subTest(kind=kind):
                self.database.unlink(missing_ok=True)
                self.archive.unlink(missing_ok=True)
                self.make_invalid(kind)
                payload = self.database.read_bytes()
                manifest = {'format': 1, 'sha256': {
                    'cabinet.sqlite3': hashlib.sha256(payload).hexdigest()}}
                with zipfile.ZipFile(self.archive, 'w') as out:
                    out.writestr('cabinet.sqlite3', payload)
                    out.writestr('manifest.json', json.dumps(manifest))
                with self.assertRaises((ValueError, sqlite3.DatabaseError)):
                    backup.verify_backup(self.archive)
                target = self.root / 'restored'
                with self.assertRaises((ValueError, sqlite3.DatabaseError)):
                    backup.restore_backup(self.archive, target)
                self.assertFalse(target.exists())
                self.database.unlink()

    def test_additive_columns_and_extra_tables_remain_compatible(self):
        con = db.connect(self.database)
        con.executescript('ALTER TABLE items ADD COLUMN extra TEXT; '
                          'CREATE TABLE synthetic_extra(value TEXT);')
        con.close()
        backup.create_backup(self.data, self.archive)
        self.assertTrue(backup.verify_backup(self.archive))
        backup.restore_backup(self.archive, self.root / 'restored')
        con = db.connect(self.root / 'restored' / 'cabinet.sqlite3')
        try:
            self.assertEqual(db.active_items(con), [])
            self.assertEqual(con.execute('SELECT extra FROM items').fetchall(), [])
        finally:
            con.close()


if __name__ == '__main__':
    unittest.main()
