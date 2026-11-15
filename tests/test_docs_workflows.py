"""Check newly documented workflows against a temporary synthetic cabinet."""
from datetime import date
import http.client
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

from chemical_cabinet import app, db, mcp

ROOT = Path(__file__).resolve().parents[1]


class DocumentationWorkflows(unittest.TestCase):
    def test_print_link_scopes_and_csv_scope_match_guides(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = app.make_handler(tmp)
            con = db.connect(str(Path(tmp) / "cabinet.sqlite3"))
            try:
                db.add_item(con, "Alpha (demo)", location="Shelf A (demo)")
                db.add_item(con, "Beta (demo)", location="Shelf B (demo)")
            finally:
                con.close()
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def get(path):
                client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                try:
                    client.request("GET", path)
                    response = client.getresponse()
                    self.assertEqual(response.status, 200)
                    return response.read().decode("utf-8-sig")
                finally:
                    client.close()
            try:
                cabinet = get("/?q=Alpha")
                self.assertIn("href='/review'>Print sheet", cabinet)
                self.assertIn("href='/review?q=Alpha'>Print this view", cabinet)
                self.assertNotIn("Print this view", get("/?sort=expiry"))
                filtered = get("/review?q=Alpha&page=2")
                self.assertIn("Alpha (demo)", filtered)
                self.assertNotIn("Beta (demo)", filtered)
                for path in ("/review", "/export.csv?q=Alpha"):
                    full = get(path)
                    self.assertIn("Alpha (demo)", full)
                    self.assertIn("Beta (demo)", full)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_ai_read_workflows_use_existing_tools_and_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = db.connect(str(Path(tmp) / "cabinet.sqlite3"))
            try:
                item_id = db.add_item(con, "Example cleaner B (demo)", location="Shelf B (demo)",
                                      quantity="1 bottle")
                data = mcp.call_tool(con, "review", {}, False)
                self.assertEqual(data[0]["id"], item_id)
                self.assertEqual(data[0]["quantity"], "1 bottle")
                self.assertEqual(data[0]["flags"],
                                 ["no expiry date", "no safety data sheet", "no photo"])
                self.assertEqual(mcp.call_tool(con, "list_items", {}, False), data)
                db.retire_item(con, item_id, reason="Invented reason (demo)",
                               method="Invented log entry (demo)", today=date(2026, 1, 1))
                entry = mcp.call_tool(con, "disposal_log", {}, False)[0]
                self.assertEqual(set(entry), {"item_id", "name", "retired_on", "reason", "method"})
                self.assertEqual(entry["item_id"], item_id)
                self.assertEqual(mcp.call_tool(con, "review", {}, False), [])
            finally:
                con.close()

    def test_onboarding_links_resolve_and_uses_shared_demo_record(self):
        docs = ROOT / "docs"
        examples = json.loads((docs / "examples/demo-records.json").read_text())
        first = (docs / "FIRST-STEPS.md").read_text()
        for key in ("name", "location", "quantity", "notes"):
            self.assertIn(examples[1][key], first)
        for filename in ("FIRST-STEPS.md", "AI-WORKFLOWS.md"):
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", (docs / filename).read_text()):
                if "://" not in target and not target.startswith("#"):
                    self.assertTrue((docs / target.split("#", 1)[0]).is_file(), target)


if __name__ == "__main__":
    unittest.main()
