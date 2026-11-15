"""Cross-component regressions. Only invented inventory in temporary folders."""
import csv
from contextlib import closing
import http.client
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

from chemical_cabinet import app, backup


class IntegrationJourneyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / "inventory"
        self.start(self.data)

    def start(self, data):
        if hasattr(self, "cookie"):
            del self.cookie
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler(str(data)))
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.port = self.srv.server_port
        _, _, headers = self.request("GET", "/add")
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.token = self.cookie.split("=", 1)[1]

    def stop(self):
        if self.srv:
            self.srv.shutdown()
            self.srv.server_close()
            self.thread.join(timeout=2)
            self.srv = None

    def request(self, method, path, payload=None, content_type=None):
        headers = {"Host": "localhost"}
        if hasattr(self, "cookie"):
            headers["Cookie"] = self.cookie
        if content_type:
            headers["Content-Type"] = content_type
        with closing(http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)) as client:
            client.request(method, path, payload, headers)
            response = client.getresponse()
            return response.status, response.read(), dict(response.getheaders())

    def save(self, path, fields, uploads=None):
        boundary = "integration-journey-fixture"
        chunks = []
        for name, value in dict(fields, csrf=self.token).items():
            chunks.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" %
                           (boundary, name, value)).encode())
        for name, (kind, data) in (uploads or {}).items():
            chunks.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"fixture\"\r\nContent-Type: %s\r\n\r\n" %
                           (boundary, name, kind)).encode() + data + b"\r\n")
        chunks.append(("--%s--\r\n" % boundary).encode())
        return self.request("POST", path, b"".join(chunks), "multipart/form-data; boundary=" + boundary)

    def read_tool(self, name, data=None):
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": name, "arguments": {}}}
        process = subprocess.run([sys.executable, "-m", "chemical_cabinet.mcp", "--data", str(data or self.data)],
                                 input=json.dumps(message) + "\n", text=True, capture_output=True, timeout=5)
        self.assertEqual(process.returncode, 0, process.stderr)
        reply = json.loads(process.stdout)
        self.assertFalse(reply["result"]["isError"], reply)
        return json.loads(reply["result"]["content"][0]["text"])

    def test_journey_http_files_edit_retire_backup_restore_stdio(self):
        photo = b"\x89PNG\r\n\x1a\nfictional image fixture"
        sheet = b"%PDF-1.4 fictional sheet fixture"
        fields = {"name": "Cleaner <demo> (demo)", "location": "Shelf A (demo)",
                  "quantity": "2 bottles", "notes": "Fictional <script>not executable</script>",
                  "opened_on": "2026-01-01", "expires_on": "2099-01-01"}
        status, _, _ = self.save("/add", fields, {"photo": ("image/png", photo), "sds": ("application/pdf", sheet)})
        self.assertEqual(status, 303)
        first = self.read_tool("list_items")[0]
        self.assertTrue(first["has_photo"] and first["has_safety_data_sheet"])
        self.assertEqual(first["flags"], [])
        page = self.request("GET", "/")[1].decode()
        self.assertIn("Cleaner &lt;demo&gt;", page)
        self.assertNotIn("<script>not executable", page)
        links = re.findall(r"/files/([0-9a-f]{16}\.(?:png|pdf))", page)
        self.assertEqual(len(links), 2)
        expected = {"png": photo, "pdf": sheet}
        for name in links:
            self.assertEqual(self.request("GET", "/files/" + name)[1], expected[name.rsplit(".", 1)[1]])
        replacement = b"%PDF-1.4 replacement fictional sheet"
        fields.update(name="Updated cleaner (demo)", location="Shelf B (demo)")
        self.assertEqual(self.save("/edit/1", fields, {"sds": ("application/pdf", replacement)})[0], 303)
        self.assertEqual(self.read_tool("list_items")[0]["location"], "Shelf B (demo)")
        current = list((self.data / "files").iterdir())
        self.assertEqual(len(current), 2)
        self.assertEqual({p.read_bytes() for p in current}, {photo, replacement})
        exported = list(csv.DictReader(io.StringIO(self.request("GET", "/export.csv")[1].decode("utf-8-sig"))))
        self.assertEqual(exported[0]["name"], fields["name"])
        body = urlencode({"csrf": self.token, "reason": "Fictional retirement", "method": "Demo record only"}).encode()
        self.assertEqual(self.request("POST", "/retire/1", body, "application/x-www-form-urlencoded")[0], 303)
        self.assertEqual(self.read_tool("list_items"), [])
        self.assertEqual(self.request("GET", "/edit/1")[0], 404)
        self.assertEqual(self.read_tool("disposal_log")[0]["name"], fields["name"])
        self.assertEqual(len(list(csv.DictReader(io.StringIO(self.request("GET", "/export.csv")[1].decode("utf-8-sig"))))), 0)
        self.stop()  # The backup contract requires stopping all writers first.
        archive = self.root / "snapshot.zip"
        backup.create_backup(self.data, archive)
        self.assertTrue(backup.verify_backup(archive))
        restored = self.root / "restored"
        backup.restore_backup(archive, restored)
        self.assertEqual(self.read_tool("disposal_log", restored)[0]["method"], "Demo record only")
        self.start(restored)
        self.assertIn(b"Updated cleaner (demo)", self.request("GET", "/log")[1])
        self.assertEqual({p.read_bytes() for p in (restored / "files").iterdir()}, {photo, replacement})
        for p in (restored / "files").iterdir():
            self.assertEqual(self.request("GET", "/files/" + p.name)[1], p.read_bytes())

    def test_journey_rejected_edit_keeps_original_files_and_record(self):
        original = b"%PDF-1.4 original fictional sheet"
        self.assertEqual(self.save("/add", {"name": "Original cleaner (demo)", "expires_on": "2099-01-01"},
                                   {"sds": ("application/pdf", original)})[0], 303)
        before = self.read_tool("list_items")
        files_before = {p.name: p.read_bytes() for p in (self.data / "files").iterdir()}
        self.assertEqual(self.save("/edit/1", {"name": "Rejected change (demo)", "expires_on": "20260101"},
                                   {"sds": ("application/pdf", b"%PDF-1.4 rejected replacement")})[0], 400)
        self.assertEqual(self.read_tool("list_items"), before)
        self.assertEqual({p.name: p.read_bytes() for p in (self.data / "files").iterdir()}, files_before)

    def test_journey_upload_name_collision_never_overwrites_existing_sheet(self):
        original = b"%PDF-1.4 original fictional sheet"
        self.assertEqual(self.save("/add", {"name": "Existing cleaner (demo)"},
                                   {"sds": ("application/pdf", original)})[0], 303)
        existing = next((self.data / "files").iterdir())
        original_rows = self.read_tool("list_items")
        original_token_hex = app.secrets.token_hex
        def collision(size):
            return existing.stem if size == 8 else original_token_hex(size)
        with patch.object(app.secrets, "token_hex", side_effect=collision), patch.object(self.srv, "handle_error"):
            try:
                self.save("/add", {"name": " "}, {"sds": ("application/pdf", b"%PDF-1.4 rejected replacement")})
            except http.client.RemoteDisconnected:
                pass  # Failing closed on a collision is permitted.
        self.assertEqual(self.read_tool("list_items"), original_rows)
        self.assertTrue(existing.is_file(), "collision deleted another product's attachment")
        self.assertEqual(existing.read_bytes(), original)
        self.assertEqual(self.request("GET", "/files/" + existing.name)[1], original)

    def test_journey_failed_upload_write_removes_partial_file(self):
        real_open = open
        class BrokenWrite:
            def __init__(self, handle):
                self.handle = handle
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.handle.close()
            def write(self, data):
                self.handle.write(data[:8])
                self.handle.flush()
                raise OSError("synthetic disk failure (demo)")
        def interrupted_open(path, mode="r", *args, **kwargs):
            handle = real_open(path, mode, *args, **kwargs)
            return BrokenWrite(handle) if mode in {"wb", "xb"} and Path(path).parent == self.data / "files" else handle
        with patch.object(app, "open", interrupted_open, create=True), patch.object(self.srv, "handle_error"):
            # Existing handler closes connections on I/O failure. This test is
            # about persistent state, not imposing a new HTTP error contract.
            with self.assertRaises(http.client.RemoteDisconnected):
                self.save("/add", {"name": "Interrupted upload (demo)"},
                          {"photo": ("image/png", b"\x89PNG\r\n\x1a\nfictional photo")})
        self.assertEqual(self.read_tool("list_items"), [])
        self.assertEqual(list((self.data / "files").iterdir()), [])

    def test_journey_compact_expiry_refused_at_http_boundary(self):
        # date.fromisoformat accepts this on modern Python, but dates are stored
        # as text and sorted lexically. Mixed representations corrupt ordering.
        status, _, _ = self.save("/add", {"name": "Compact expiry (demo)", "expires_on": "20260101"})
        self.assertEqual(status, 400)
        self.assertEqual(self.read_tool("list_items"), [])

    def test_journey_week_date_refused_at_mcp_boundary(self):
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "add_item", "arguments": {"name": "Week expiry (demo)", "expires_on": "2026-W01-1"}}}
        process = subprocess.run([sys.executable, "-m", "chemical_cabinet.mcp", "--data", str(self.data), "--allow-write"],
                                 input=json.dumps(message) + "\n", text=True, capture_output=True, timeout=5)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(json.loads(process.stdout)["result"]["isError"])
        self.assertEqual(self.read_tool("list_items"), [])


if __name__ == "__main__":
    unittest.main()
