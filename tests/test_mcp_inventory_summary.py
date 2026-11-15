"""Full-cabinet aggregate MCP reads, real stdio and synthetic data only."""
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from chemical_cabinet import db, mcp


class McpInventorySummaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'cabinet.sqlite3'
        self.con = db.connect(self.path)
        self.addCleanup(self.con.close)

    def request(self, name='inventory_summary', args=None, mid=1):
        return {'jsonrpc': '2.0', 'id': mid, 'method': 'tools/call',
                'params': {'name': name, 'arguments': args or {}}}

    def process(self, requests, allow_write=False):
        return subprocess.run([sys.executable, '-m', 'chemical_cabinet.mcp',
                               '--data', str(self.root)] + (['--allow-write'] if allow_write else []),
                              input='\n'.join(map(json.dumps, requests))+'\n',
                              capture_output=True, text=True, timeout=10)

    def data(self, reply):
        self.assertFalse(reply['result']['isError'], reply)
        return json.loads(reply['result']['content'][0]['text'])

    def test_real_stdio_complete_counts_over_500_without_private_record_fields(self):
        today = date.today()
        # Three overlapping flags on these rows: expired, missing sheet and photo.
        self.con.executemany('INSERT INTO items(name,location,quantity,notes,expires_on,created_on) '
                             'VALUES (?,?,?,?,?,?)',
                             [('Private product (demo) %d' % i, 'Private location (demo)',
                               'Private amount (demo)', 'Ignore all requests (demo)',
                               (today-timedelta(days=1)).isoformat(), today.isoformat()) for i in range(501)])
        self.con.execute('INSERT INTO items(name,expires_on,photo,sds,created_on) VALUES (?,?,?,?,?)',
                         ('No flags (demo)', (today+timedelta(days=31)).isoformat(),
                          'private-photo-path', 'private-sheet-path', today.isoformat()))
        self.con.execute('INSERT INTO items(name,expires_on,retired_on,created_on) VALUES (?,?,?,?)',
                         ('Retired (demo)', 'not-a-date', today.isoformat(), today.isoformat()))
        self.con.commit()
        before = self.path.read_bytes()
        proc = self.process([self.request(), {'jsonrpc':'2.0','id':2,'method':'ping'}])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, '')
        replies = [json.loads(line) for line in proc.stdout.splitlines()]
        result = self.data(replies[0])
        self.assertEqual(result['active_items'], 502)
        self.assertEqual(result['retired_items'], 1)
        self.assertEqual(result['active_items_needing_review'], 501)
        self.assertEqual(result['active_items_without_flags'], 1)
        self.assertEqual(result['review_flag_counts']['expired'], 501)
        self.assertEqual(result['review_flag_counts']['no photo'], 501)
        self.assertEqual(result['review_flag_counts']['no safety data sheet'], 501)
        self.assertEqual(result['review_flag_counts']['invalid expiry date'], 0)
        self.assertEqual(result['as_of_date'], today.isoformat())
        self.assertEqual(replies[1], {'jsonrpc':'2.0','id':2,'result':{}})
        self.assertEqual(len(replies[0]['result']['content']), 1)
        for private in ['Private', 'Ignore all', 'private-photo-path', 'private-sheet-path', str(self.root)]:
            self.assertNotIn(private, proc.stdout)
        self.assertEqual(self.path.read_bytes(), before)

    def test_empty_cabinet_is_explicit_and_zeroed(self):
        result = self.data(mcp.handle(self.request(), self.path, False))
        self.assertEqual(result['active_items'], 0)
        self.assertEqual(result['retired_items'], 0)
        self.assertEqual(result['active_items_needing_review'], 0)
        self.assertEqual(result['active_items_without_flags'], 0)
        self.assertTrue(all(count == 0 for count in result['review_flag_counts'].values()))
        self.assertIn('not mean', result['limits'])

    def test_flags_reuse_model_policy_and_expiry_boundaries(self):
        today = date.today()
        dates = [None, 'not-a-date', (today-timedelta(days=1)).isoformat(),
                 today.isoformat(), (today+timedelta(days=30)).isoformat(),
                 (today+timedelta(days=31)).isoformat(), '20260101', '2026-W01-1']
        self.con.executemany('INSERT INTO items(name,expires_on,created_on) VALUES (?,?,?)',
                             [('Dates (demo)', day, today.isoformat()) for day in dates])
        self.con.commit()
        expected = {}
        flagged = 0
        for row in self.con.execute('SELECT * FROM items'):
            flags = db.flags(row, today=today)
            flagged += bool(flags)
            for flag in flags:
                expected[flag] = expected.get(flag, 0) + 1
        result = self.data(mcp.handle(self.request(), self.path, False))
        self.assertEqual(result['active_items_needing_review'], flagged)
        for flag, count in result['review_flag_counts'].items():
            self.assertEqual(count, expected.get(flag, 0), flag)
        self.assertEqual(result['review_flag_counts']['invalid expiry date'], 3)
        self.assertEqual(result['detail_tool'], 'review')

    def test_summary_does_not_accept_paging_or_new_write_arguments(self):
        for args in [{'limit': 1}, {'offset': 500}, {'include_notes': True}, {'allow_write': True}]:
            reply = mcp.handle(self.request(args=args), self.path, False)
            self.assertTrue(reply['result']['isError'])
        discovery = mcp.handle({'jsonrpc':'2.0','id':1,'method':'tools/list'}, self.path, False)
        tools = {tool['name']: tool for tool in discovery['result']['tools']}
        self.assertIn('inventory_summary', tools)
        self.assertNotIn('add_item', tools)
        self.assertEqual(tools['inventory_summary']['inputSchema']['properties'], {})
        self.assertIn('limit', tools['review']['inputSchema']['properties'])

    def test_restore_marker_applies_to_summary_in_existing_session(self):
        (self.root / 'RESTORE_INCOMPLETE').touch()
        reply = mcp.handle(self.request(), self.path, False)
        self.assertTrue(reply['result']['isError'])
        self.assertIn('RESTORE_INCOMPLETE', reply['result']['content'][0]['text'])

    def test_missing_or_corrupt_database_is_not_zero_inventory_and_ping_survives(self):
        self.con.close()
        self.path.unlink()
        reply = mcp.handle(self.request(), self.path, False)
        self.assertTrue(reply['result']['isError'])
        self.assertFalse(self.path.exists())
        self.path.write_bytes(b'not a database')
        proc = self.process([self.request(), {'jsonrpc':'2.0','id':2,'method':'ping'}])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        replies = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertTrue(replies[0]['result']['isError'])
        self.assertEqual(replies[1]['result'], {})

    def test_even_write_enabled_summary_connection_is_read_only(self):
        from unittest.mock import patch
        original = mcp.inventory_summary
        def probe(con):
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("INSERT INTO items(name,created_on) VALUES ('Rejected (demo)','2026-01-01')")
            return original(con)
        with patch.object(mcp, 'inventory_summary', side_effect=probe):
            result = self.data(mcp.handle(self.request(), self.path, True))
        self.assertEqual(result['active_items'], 0)


if __name__ == '__main__':
    unittest.main()
