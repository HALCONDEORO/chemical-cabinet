"""Model input/relationship checks with invented in-memory inventory only."""
import sqlite3
import unittest
from datetime import date, datetime
from chemical_cabinet import db

class ModelValuesTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(':memory:')
        self.addCleanup(self.con.close)

    def test_quantity_and_units_are_descriptive_not_silently_converted(self):
        for quantity in ('1.5 L', '2 × 500 mL', 'about half a bottle', '0', ''):
            item = db.add_item(self.con, 'Example (demo)', quantity='  '+quantity+'  ')
            self.assertEqual(db.get_item(self.con, item)['quantity'], quantity)

    def test_canonical_dates_roundtrip_and_absence(self):
        item = db.add_item(self.con, 'Example (demo)', opened_on=' 2024-02-29 ', expires_on=' ')
        row = db.get_item(self.con, item)
        self.assertEqual(row['opened_on'], '2024-02-29')
        self.assertIsNone(row['expires_on'])

    def test_invalid_dates_do_not_create_rows(self):
        for bad in ('20240229', '2024-W09-4', '2024-2-29', '2023-02-29', '２０２４-０２-２９', False, 0, [], date(2024, 2, 29)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                db.add_item(self.con, 'Example (demo)', expires_on=bad)
        self.assertEqual(db.active_items(self.con), [])

    def test_nontext_fields_fail_before_insert_or_update(self):
        item = db.add_item(self.con, 'Example (demo)', quantity='2 L')
        for field in ('name', 'quantity', 'location', 'notes'):
            for bad in (None, True, 2.5, 'bad\x00value'):
                kwargs = {field: bad}
                with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                    db.add_item(self.con, **dict({'name':'Example (demo)'}, **kwargs))
                with self.assertRaises(ValueError):
                    db.update_item(self.con, item, **dict({'name':'Changed (demo)'}, **kwargs))
        self.assertEqual(db.get_item(self.con, item)['quantity'], '2 L')
        self.assertEqual(len(db.active_items(self.con)), 1)

    def test_ids_do_not_coerce_bool_float_or_numeric_text(self):
        item = db.add_item(self.con, 'Example (demo)')
        for bad in (True, 1.0, '1', 0, -1, None):
            for operation in (lambda: db.get_item(self.con, bad), lambda: db.update_item(self.con, bad, 'Changed (demo)'), lambda: db.retire_item(self.con, bad)):
                with self.subTest(bad=bad), self.assertRaises(ValueError): operation()
        self.assertIsNone(db.get_item(self.con, item)['retired_on'])

    def test_foreign_keys_reject_orphan_retirement_and_parent_delete(self):
        self.assertEqual(self.con.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        with self.assertRaises(sqlite3.IntegrityError), self.con:
            self.con.execute("INSERT INTO retirements(item_id, retired_on) VALUES (999, '2026-01-01')")
        item = db.add_item(self.con, 'Example (demo)')
        db.retire_item(self.con, item)
        with self.assertRaises(sqlite3.IntegrityError), self.con:
            self.con.execute('DELETE FROM items WHERE id=?', (item,))
        self.assertIsNotNone(db.get_item(self.con, item))

    def test_bad_retirement_day_or_text_is_not_partial(self):
        item = db.add_item(self.con, 'Example (demo)')
        for kwargs in ({'today':datetime(2026,1,1)}, {'today':'2026-01-01'}, {'reason':None}, {'method':'bad\x00method'}):
            with self.assertRaises(ValueError): db.retire_item(self.con, item, **kwargs)
        self.assertIsNone(db.get_item(self.con, item)['retired_on'])
        self.assertEqual(db.retirement_log(self.con), [])

    def test_failed_add_trigger_rolls_back_without_poisoning_next_add(self):
        self.con.executescript("CREATE TRIGGER reject_demo BEFORE INSERT ON items WHEN NEW.name='Rejected (demo)' BEGIN SELECT RAISE(ABORT, 'test failure'); END;")
        with self.assertRaises(sqlite3.IntegrityError):
            db.add_item(self.con, 'Rejected (demo)')
        self.assertFalse(self.con.in_transaction)
        db.add_item(self.con, 'Accepted (demo)')
        self.assertEqual([r['name'] for r in db.active_items(self.con)], ['Accepted (demo)'])

    def test_failed_update_trigger_rolls_back_and_next_edit_succeeds(self):
        item = db.add_item(self.con, 'Original (demo)', photo='aaaaaaaaaaaaaaaa.png')
        self.con.executescript("CREATE TRIGGER reject_edit BEFORE UPDATE ON items WHEN NEW.name='Rejected (demo)' BEGIN SELECT RAISE(ABORT, 'test failure'); END;")
        with self.assertRaises(sqlite3.IntegrityError):
            db.update_item(self.con, item, 'Rejected (demo)', photo='bbbbbbbbbbbbbbbb.png')
        self.assertFalse(self.con.in_transaction)
        db.update_item(self.con, item, 'Accepted (demo)')
        self.assertEqual(db.get_item(self.con, item)['photo'], 'aaaaaaaaaaaaaaaa.png')

    def test_bulk_insert_uses_shared_validator_without_partial_commit(self):
        with self.assertRaises(ValueError), self.con:
            db.insert_item(self.con, 'First (demo)', quantity='2 L')
            db.insert_item(self.con, 'Invalid (demo)', expires_on='20260101')
        self.assertEqual(db.active_items(self.con), [])
        with self.con:
            db.insert_item(self.con, 'First (demo)', quantity='2 L')
            db.insert_item(self.con, 'Second (demo)', quantity='3 bottles')
        self.assertEqual(len(db.active_items(self.con)), 2)

    def test_validation_is_pure_and_canonical_for_import_callers(self):
        result = db.validate_item(' Example (demo) ', quantity=' 2 × 500 mL ', opened_on=' 2024-02-29 ')
        self.assertEqual(result['name'], 'Example (demo)')
        self.assertEqual(result['quantity'], '2 × 500 mL')
        self.assertEqual(result['opened_on'], '2024-02-29')
        self.assertFalse(self.con.in_transaction)
        self.assertEqual(db.active_items(self.con), [])

    def test_legacy_database_connect_does_not_destroy_existing_orphans(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'legacy.sqlite3'
            raw = sqlite3.connect(path)
            raw.executescript(db.SCHEMA)
            raw.execute("INSERT INTO retirements(item_id, retired_on) VALUES (999,'2026-01-01')")
            raw.commit()
            raw.close()
            con = db.connect(path)
            try:
                self.assertEqual(con.execute('SELECT count(*) FROM retirements').fetchone()[0], 1)
                self.assertEqual(len(con.execute('PRAGMA foreign_key_check').fetchall()), 1)
                db.add_item(con, 'New (demo)')
                with self.assertRaises(sqlite3.IntegrityError), con:
                    con.execute("INSERT INTO retirements(item_id, retired_on) VALUES (998,'2026-01-01')")
            finally:
                con.close()

    def test_legacy_noncanonical_expiry_remains_visible_without_rewriting(self):
        for legacy in ('20260101', '2026-W01-1', 'not-a-date'):
            item = db.add_item(self.con, 'Legacy (demo)')
            self.con.execute('UPDATE items SET expires_on=? WHERE id=?', (legacy, item))
            self.con.commit()
            row = db.get_item(self.con, item)
            self.assertIn('invalid expiry date', db.flags(row, today=date(2026, 1, 1)))
            self.assertEqual(db.get_item(self.con, item)['expires_on'], legacy)
            self.assertNotIn('no expiry date', db.flags(row, today=date(2026, 1, 1)))

    def test_legacy_sort_key_uses_real_dates_without_rewriting(self):
        rows = [dict(name='Later (demo)', expires_on='2026-02-01'),
                dict(name='Compact (demo)', expires_on='20260101'),
                dict(name='Week (demo)', expires_on='2026-W01-1'),
                dict(name='Unknown (demo)', expires_on='not-a-date'),
                dict(name='Undated (demo)', expires_on=None)]
        self.assertEqual([r['name'] for r in sorted(rows, key=db.expiry_sort_key)],
                         ['Week (demo)', 'Compact (demo)', 'Later (demo)', 'Unknown (demo)', 'Undated (demo)'])
        self.assertEqual(rows[1]['expires_on'], '20260101')
        self.assertEqual(rows[2]['expires_on'], '2026-W01-1')
        for bad in ('20260231', '2026-W99-1', '01/02/2026', True):
            self.assertIsNone(db.read_expiry_date(bad))
