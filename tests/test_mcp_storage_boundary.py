"""MCP storage-boundary checks using real SQLite files and stdio processes."""
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from chemical_cabinet import db, mcp


class McpStorageBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'cabinet.sqlite3'
        con = db.connect(self.path)
        self.item_id = db.add_item(con, 'Storage boundary product (demo)')
        con.close()

    def request(self, name='list_items', arguments=None, mid=1):
        return {'jsonrpc': '2.0', 'id': mid, 'method': 'tools/call',
                'params': {'name': name, 'arguments': arguments or {}}}

    def assert_tool_error(self, reply):
        self.assertTrue(reply['result']['isError'], reply)
        self.assertNotIn('Storage boundary product', json.dumps(reply))

    def test_marked_startup_refuses_read_and_write_without_database_changes(self):
        before = self.path.read_bytes()
        (self.root / 'RESTORE_INCOMPLETE').touch()
        for allow in ([], ['--allow-write']):
            with self.subTest(allow=allow):
                proc = subprocess.run(
                    [sys.executable, '-m', 'chemical_cabinet.mcp', '--data', str(self.root)] + allow,
                    input=json.dumps(self.request('add_item', {'name': 'Rejected (demo)'})) + '\n',
                    capture_output=True, text=True, timeout=10)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(proc.stdout, '')
                self.assertIn('RESTORE_INCOMPLETE', proc.stderr)
                self.assertEqual(self.path.read_bytes(), before)

    def test_marker_without_database_is_reported_before_missing_database(self):
        self.path.unlink()
        (self.root / 'RESTORE_INCOMPLETE').touch()
        with self.assertRaisesRegex(SystemExit, 'RESTORE_INCOMPLETE'):
            mcp.main(['--data', str(self.root)])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['RESTORE_INCOMPLETE'])

    def test_dangling_marker_and_directory_marker_are_both_refused(self):
        marker = self.root / 'RESTORE_INCOMPLETE'
        marker.symlink_to(self.root / 'does-not-exist')
        self.assert_tool_error(mcp.handle(self.request(), self.path, False))
        marker.unlink()
        marker.mkdir()
        self.assert_tool_error(mcp.handle(self.request(), self.path, False))

    def test_new_marker_blocks_every_inventory_tool_in_existing_session(self):
        self.assertFalse(mcp.handle(self.request(), self.path, False)['result']['isError'])
        before = self.path.read_bytes()
        (self.root / 'RESTORE_INCOMPLETE').touch()
        for name, args in [('list_items', {}), ('review', {}), ('disposal_log', {}),
                           ('add_item', {'name': 'Rejected (demo)'}),
                           ('retire_item', {'item_id': self.item_id})]:
            with self.subTest(tool=name):
                reply = mcp.handle(self.request(name, args), self.path, True)
                self.assert_tool_error(reply)
                self.assertIn('RESTORE_INCOMPLETE', reply['result']['content'][0]['text'])
        self.assertEqual(self.path.read_bytes(), before)

    def test_read_connection_cannot_write_even_when_write_tools_are_enabled(self):
        from unittest.mock import patch
        original = mcp.call_tool
        seen = []
        def attempt_write(con, name, args, allow_write, page_info):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'readonly'):
                con.execute("INSERT INTO items(name, created_on) VALUES ('Rejected (demo)', '2026-01-01')")
            seen.append(name)
            return original(con, name, args, allow_write, page_info)
        with patch.object(mcp, 'call_tool', side_effect=attempt_write):
            result = mcp.handle(self.request(), self.path, True)
        self.assertEqual(seen, ['list_items'])
        self.assertFalse(result['result']['isError'])

    def test_missing_database_is_not_recreated_by_either_mode(self):
        self.path.unlink()
        for name, args, allow in [('list_items', {}, False),
                                   ('add_item', {'name': 'Rejected (demo)'}, True)]:
            with self.subTest(tool=name):
                self.assert_tool_error(mcp.handle(self.request(name, args), self.path, allow))
                self.assertFalse(self.path.exists())

    def test_incomplete_schema_is_not_silently_bootstrapped(self):
        self.path.unlink()
        with sqlite3.connect(self.path) as con:
            con.execute('CREATE TABLE unrelated (value TEXT)')
        before = self.path.read_bytes()
        for allow in (False, True):
            self.assert_tool_error(mcp.handle(self.request(), self.path, allow))
        self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_database_returns_error_and_session_continues(self):
        self.path.write_bytes(b'not a SQLite database')
        requests = [self.request(), {'jsonrpc': '2.0', 'id': 2, 'method': 'ping'}]
        output = io.StringIO()
        mcp.serve(self.path, False, io.StringIO('\n'.join(map(json.dumps, requests))), output)
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assert_tool_error(replies[0])
        self.assertEqual(replies[1], {'jsonrpc': '2.0', 'id': 2, 'result': {}})

    def test_write_mode_still_adds_and_retires_existing_database(self):
        added = mcp.handle(self.request('add_item', {'name': 'Added product (demo)'}), self.path, True)
        self.assertFalse(added['result']['isError'])
        item_id = json.loads(added['result']['content'][0]['text'])['id']
        retired = mcp.handle(self.request('retire_item', {'item_id': item_id}), self.path, True)
        self.assertFalse(retired['result']['isError'])
        with sqlite3.connect(self.path) as con:
            self.assertIsNotNone(con.execute('SELECT retired_on FROM items WHERE id=?', (item_id,)).fetchone()[0])

    def test_database_uri_special_characters_are_not_interpreted_as_options(self):
        folder = self.root / 'cabinet ? # % (demo)'
        folder.mkdir()
        path = folder / 'cabinet.sqlite3'
        db.connect(path).close()
        result = mcp.handle(self.request(), path, False)
        self.assertFalse(result['result']['isError'])
        self.assertEqual(json.loads(result['result']['content'][0]['text']), [])


if __name__ == '__main__':
    unittest.main()
