"""Real SQLite foreign-key enforcement through MCP read/write openers."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from chemical_cabinet import db, mcp


class McpForeignKeyBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'cabinet.sqlite3'
        con = db.connect(self.path)
        db.add_item(con, 'Composition product (demo)')
        con.close()

    def request(self, name='list_items', arguments=None):
        return {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                'params': {'name': name, 'arguments': arguments or {}}}

    def test_foreign_keys_enabled_on_read_and_write_connections(self):
        original = mcp.call_tool
        modes = []
        def inspect(con, name, args, allow_write, page_info):
            self.assertEqual(con.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            modes.append(name)
            if name == 'add_item':
                # Real database constraint, not a mocked SQLite return value.
                with self.assertRaises(sqlite3.IntegrityError):
                    con.execute("INSERT INTO retirements(item_id, retired_on) VALUES (?, ?)",
                                (999999, '2026-01-01'))
            return original(con, name, args, allow_write, page_info)
        before = self.path.read_bytes()
        with patch.object(mcp, 'call_tool', side_effect=inspect):
            self.assertFalse(mcp.handle(self.request(), self.path, False)['result']['isError'])
            self.assertEqual(self.path.read_bytes(), before)
            self.assertFalse(mcp.handle(self.request('add_item', {'name': 'FK product (demo)'}),
                                       self.path, True)['result']['isError'])
        self.assertEqual(modes, ['list_items', 'add_item'])
        with sqlite3.connect(self.path) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM retirements').fetchone()[0], 0)
            self.assertEqual(con.execute('SELECT count(*) FROM items').fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
