"""Composed model/performance parity on invented legacy and Unicode rows."""
import unittest
from datetime import date
from chemical_cabinet import db

class ModelQueryParityTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(':memory:')
        self.addCleanup(self.con.close)
        self.today = date(2026, 1, 1)
        for n, expiry in enumerate((None, '', ' ', '20260101', '2026-W01-1', 'not-a-date',
                                    '2026-01-15', '2027-02-01', '20260231', '2026-W99-1')):
            for label in ('ÁCIDO (demo)', 'ácido (demo)', 'Zulu (demo)'):
                i = db.add_item(self.con, label, location=('München (demo)' if n % 2 else 'münchen (demo)'), notes='Δοκιμή (demo)',
                                photo='aaaaaaaaaaaaaaaa.png', sds='bbbbbbbbbbbbbbbb.pdf')
                self.con.execute('UPDATE items SET expires_on=?,created_on=? WHERE id=?', (expiry, '2026-01-%02d' % (n+1), i))
                self.con.commit()

    def reference(self, q, flagged, sort, location=""):
        rows = db.active_items(self.con)
        q = q.strip().lower()[:100]
        rows = [r for r in rows if q in (r['name']+' '+r['location']+' '+r['notes']).lower()]
        if location:
            rows = [r for r in rows if r['location'] == location[:200]]
        if flagged:
            rows = [r for r in rows if db.flags(r, self.today)]
        if sort == 'expiry':
            rows.sort(key=db.expiry_sort_key)
        elif sort == 'location':
            rows.sort(key=lambda r: (r['location'].lower(), r['name'].lower()))
        elif sort == 'newest':
            rows.sort(key=lambda r: (r['created_on'], r['id']), reverse=True)
        return rows

    def test_legacy_unicode_flags_and_date_sort_parity_all_pages(self):
        for q in ('', 'ácido', 'MÜNCHEN', 'δοκιμή', 'no match'):
            for flagged in (False, True):
                for sort in ('', 'expiry'):
                    with self.subTest(q=q, flagged=flagged, sort=sort):
                        expected = self.reference(q, flagged, sort)
                        total, actual = db.query_active_items(self.con, q, flagged, sort, self.today)
                        self.assertEqual(total, len(expected))
                        self.assertEqual([r['id'] for r in actual], [r['id'] for r in expected])
                        paged = []
                        for offset in range(0, total, 3):
                            count, rows = db.query_active_items(self.con, q, flagged, sort, self.today, 3, offset)
                            self.assertEqual(count, total)
                            paged.extend(r['id'] for r in rows)
                        self.assertEqual(paged, [r['id'] for r in expected])

    def test_new_location_newest_and_exact_loc_all_pages(self):
        for q in ('', 'ácido', 'MÜNCHEN', 'δοκιμή', 'no match'):
            for flagged in (False, True):
                for sort in ('', 'expiry', 'location', 'newest'):
                    for location in ('', 'München (demo)', 'münchen (demo)', 'MÜNCHEN (demo)', 'missing'):
                        with self.subTest(q=q, flagged=flagged, sort=sort, location=location):
                            expected = self.reference(q, flagged, sort, location)
                            total, rows = db.query_active_items(self.con, q, flagged, sort, self.today, location=location)
                            self.assertEqual(total, len(expected))
                            self.assertEqual([r['id'] for r in rows], [r['id'] for r in expected])
                            paged = []
                            for offset in range(0, total, 3):
                                count, page = db.query_active_items(self.con, q, flagged, sort, self.today, 3, offset, location=location)
                                self.assertEqual(count, total)
                                paged.extend(r['id'] for r in page)
                            self.assertEqual(paged, [r['id'] for r in expected])

    def test_invalid_legacy_included_even_when_later_lexicographically(self):
        total, rows = db.query_active_items(self.con, only_flagged=True, today=self.today)
        self.assertTrue(any(r['expires_on'] == 'not-a-date' for r in rows))
        self.assertFalse(any(r['expires_on'] == '2027-02-01' for r in rows))
        self.assertTrue(all(db.flags(r, self.today) for r in rows))

    def test_no_limit_offset_is_respected_and_bad_paging_rejected(self):
        _, all_rows = db.query_active_items(self.con)
        _, tail = db.query_active_items(self.con, offset=2)
        self.assertEqual([r['id'] for r in tail], [r['id'] for r in all_rows[2:]])
        for kwargs in ({'limit':True}, {'limit':0}, {'limit':-1}, {'offset':True}, {'offset':-1}):
            with self.assertRaises(ValueError): db.query_active_items(self.con, **kwargs)
