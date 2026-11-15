"""SQLite-sized item identifiers must not terminate an MCP write session."""
import io
import json
import sqlite3
from pathlib import Path
import tempfile
import unittest

from chemical_cabinet import db, mcp


class McpIdentifierBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'cabinet.sqlite3'
        con = db.connect(self.path)
        self.item_id = db.add_item(con, 'Identifier boundary product (demo)')
        con.close()

    def test_out_of_range_ids_return_errors_and_keep_session_alive(self):
        requests = []
        values = [2**63, 10**100, -(2**63)-1, 0, -1, True, 1.0, '1']
        for mid, value in enumerate(values, 1):
            requests.append({'jsonrpc': '2.0', 'id': mid, 'method': 'tools/call',
                             'params': {'name': 'retire_item', 'arguments': {'item_id': value}}})
        requests.append({'jsonrpc': '2.0', 'id': 99, 'method': 'ping'})
        before = self.path.read_bytes()
        output = io.StringIO()
        mcp.serve(self.path, True, io.StringIO('\n'.join(map(json.dumps, requests))), output)
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(replies), len(requests))
        for reply in replies[:-1]:
            self.assertTrue(reply['result']['isError'], reply)
            self.assertIn('item_id', reply['result']['content'][0]['text'])
        self.assertEqual(replies[-1], {'jsonrpc': '2.0', 'id': 99, 'result': {}})
        self.assertEqual(self.path.read_bytes(), before)

    def test_maximum_sqlite_identifier_is_accepted_and_can_be_retired(self):
        with sqlite3.connect(self.path) as con:
            con.execute('UPDATE items SET id=? WHERE id=?', (2**63-1, self.item_id))
        reply = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                            'params': {'name': 'retire_item', 'arguments': {'item_id': 2**63-1}}},
                           self.path, True)
        self.assertFalse(reply['result']['isError'])
        self.assertEqual(json.loads(reply['result']['content'][0]['text']), {'retired': 2**63-1})

    def test_discovery_advertises_the_same_sqlite_upper_bound(self):
        reply = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, self.path, True)
        tool = next(tool for tool in reply['result']['tools'] if tool['name'] == 'retire_item')
        self.assertEqual(tool['inputSchema']['properties']['item_id'].get('maximum'), 2**63-1)


if __name__ == '__main__':
    unittest.main()
