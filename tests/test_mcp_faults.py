"""Malformed calls must return an error and leave the stdio session alive."""
import io
import json
from pathlib import Path
import tempfile
import unittest

from chemical_cabinet import db, mcp


class McpFaultTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'cabinet.sqlite3')
        db.connect(self.path).close()

    def assert_session_survives(self, params):
        messages = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': params},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'ping'},
        ]
        out = io.StringIO()
        mcp.serve(self.path, False, io.StringIO('\n'.join(map(json.dumps, messages))), out)
        replies = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(len(replies), 2)
        self.assertTrue('error' in replies[0] or replies[0].get('result', {}).get('isError'))
        self.assertEqual(replies[1], {'jsonrpc': '2.0', 'id': 2, 'result': {}})

    def test_real_process_survives_bad_call_and_continues_valid_request(self):
        import subprocess
        import sys
        messages = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': ['bad']},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
             'params': {'name': 'list_items', 'arguments': {}}},
        ]
        proc = subprocess.run([sys.executable, '-m', 'chemical_cabinet.mcp',
                               '--data', self.tmp.name],
                              input='\n'.join(map(json.dumps, messages)) + '\n',
                              capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        replies = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual(len(replies), 2)
        self.assertEqual(replies[0]['error']['code'], -32602)
        self.assertFalse(replies[1]['result']['isError'])
        self.assertEqual(json.loads(replies[1]['result']['content'][0]['text']), [])

    def test_omitted_arguments_remain_supported(self):
        reply = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                            'params': {'name': 'list_items'}}, self.path, False)
        self.assertFalse(reply['result']['isError'])

    def test_invalid_calls_do_not_create_items_even_with_write_enabled(self):
        for value in ([], False, 0, '', None):
            reply = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                'params': {'name': 'add_item', 'arguments': value}}, self.path, True)
            self.assertEqual(reply['error']['code'], -32602)
        con = db.connect(self.path)
        self.addCleanup(con.close)
        self.assertEqual(con.execute('SELECT count(*) FROM items').fetchone()[0], 0)

    def test_params_array_does_not_terminate_session(self):
        self.assert_session_survives(['bad'])

    def test_params_integer_does_not_terminate_session(self):
        self.assert_session_survives(1)

    def test_unhashable_tool_name_does_not_terminate_session(self):
        self.assert_session_survives({'name': [], 'arguments': {}})

    def test_false_arguments_are_rejected_not_replaced_with_empty_object(self):
        for value in ([], False, 0, ''):
            with self.subTest(value=value):
                self.assert_session_survives({'name': 'list_items', 'arguments': value})
