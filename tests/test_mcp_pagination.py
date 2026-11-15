import json
from pathlib import Path
import tempfile
import unittest

from chemical_cabinet import db, mcp


class McpPaginationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'cabinet.sqlite3')
        con = db.connect(self.path)
        self.addCleanup(con.close)
        self.con = con
        con.executemany('INSERT INTO items(name, created_on) VALUES (?,?)',
                        [('synthetic %04d' % i, '2026-01-01') for i in range(501)])
        con.commit()

    def call(self, name='list_items', **args):
        reply = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                            'params': {'name': name, 'arguments': args}}, self.path, False)
        self.assertNotIn('error', reply)
        return reply['result']

    def data(self, result):
        self.assertFalse(result['isError'], result)
        return json.loads(result['content'][0]['text'])

    def test_default_array_preserved_but_truncation_explicit(self):
        result = self.call()
        self.assertEqual(len(self.data(result)), 500)
        self.assertGreater(len(result['content']), 1, 'silent truncation at500')
        self.assertIn('500', result['content'][1]['text'])

    def test_next_page_reaches_previously_hidden_product(self):
        first = self.data(self.call())
        last_result = self.call(offset=500)
        last = self.data(last_result)
        self.assertEqual([r['name'] for r in last], ['synthetic 0500'])
        self.assertEqual(len(last_result['content']), 1)
        self.assertEqual(len({r['id'] for r in first + last}), 501)

    def test_review_filters_before_offset(self):
        self.con.execute("UPDATE items SET photo='x', sds='y', expires_on='9999-12-31' WHERE id<=10")
        self.con.commit()
        rows = self.data(self.call('review', offset=490, limit=10))
        self.assertEqual([r['name'] for r in rows], ['synthetic 0500'])
        self.assertTrue(rows[0]['flags'])

    def test_log_paginates_in_newest_first_order(self):
        self.con.execute("INSERT INTO retirements(item_id,retired_on) SELECT id,'2026-01-01' FROM items ORDER BY id")
        self.con.execute("UPDATE items SET retired_on='2026-01-01'")
        self.con.commit()
        first = self.data(self.call('disposal_log', limit=500))
        last = self.data(self.call('disposal_log', offset=500))
        self.assertEqual(first[0]['item_id'], 501)
        self.assertEqual(last[0]['item_id'], 1)
        self.assertEqual(len({r['item_id'] for r in first + last}), 501)

    def test_invalid_paging_inputs_are_errors(self):
        for args in ({'limit': True}, {'limit': 0}, {'limit': 501}, {'offset': -1},
                     {'offset': False}, {'offset': '1'}, {'offset': 2**64}):
            with self.subTest(args=args):
                result = self.call(**args)
                self.assertTrue(result['isError'], result)

    def test_equal_names_still_have_stable_id_order(self):
        self.con.execute("UPDATE items SET name='same'")
        self.con.commit()
        first = self.data(self.call(limit=500))
        last = self.data(self.call(offset=500))
        self.assertEqual([r['id'] for r in first + last], list(range(1, 502)))

    def test_maximum_offset_review_returns_empty_not_overflow(self):
        self.assertEqual(self.data(self.call('review', offset=2**63 - 1)), [])

    def test_concurrent_rename_can_omit_record_between_pages_is_not_snapshot(self):
        first = self.data(self.call(limit=2))
        self.con.execute("UPDATE items SET name='zz moved' WHERE id=1")
        self.con.commit()
        rest = self.data(self.call(offset=2, limit=500))
        combined_ids = [r['id'] for r in first + rest]
        # Moving an earlier record after the cursor shifts offsets: 3 is missed
        # and 1 reappears. Document this, never advertise a snapshot guarantee.
        self.assertNotIn(3, combined_ids)
        self.assertEqual(combined_ids.count(1), 2)
        result = self.call(limit=2)
        self.assertIn('not a snapshot', result['content'][1]['text'])
