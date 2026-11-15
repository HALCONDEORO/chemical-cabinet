"""Executable documentation examples, using only a temporary synthetic cabinet."""
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import unquote

from chemical_cabinet import db

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


class DocumentationExamples(unittest.TestCase):
    def records(self):
        return json.loads((DOCS / "examples/demo-records.json").read_text(encoding="utf-8"))

    def test_user_guide_records_are_valid_and_explicitly_synthetic(self):
        guide = (DOCS / "USER-GUIDE.md").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            con = db.connect(str(Path(tmp) / "cabinet.sqlite3"))
            try:
                for record in self.records():
                    self.assertIn("(demo)", record["name"])
                    self.assertIn("(demo)", record["notes"])
                    for field in ("name", "location", "quantity", "notes"):
                        self.assertIn(record[field], guide)
                    item_id = db.add_item(con, **record)
                    saved = db.get_item(con, item_id)
                    for key, value in record.items():
                        self.assertEqual(saved[key], value)
            finally:
                con.close()

    def test_mcp_documented_example_matches_real_stdio_output(self):
        document = (DOCS / "AI-AGENTS.md").read_text(encoding="utf-8")
        blocks = re.findall(r"```(?:json)?\n(.*?)```", document, flags=re.S)
        inputs = next(block for block in blocks if '"method":"initialize"' in block)
        initialization = next(json.loads(block) for block in blocks
                              if block.lstrip().startswith('{"jsonrpc": "2.0"'))
        inventory = next(json.loads(block) for block in blocks if block.lstrip().startswith("["))
        with tempfile.TemporaryDirectory() as tmp:
            con = db.connect(str(Path(tmp) / "cabinet.sqlite3"))
            try:
                db.add_item(con, **self.records()[1])
            finally:
                con.close()
            result = subprocess.run(
                [sys.executable, "-m", "chemical_cabinet.mcp", "--data", tmp],
                input=inputs, text=True, capture_output=True, cwd=ROOT, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            replies = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(len(replies), 2, "notification must not add a reply")
            self.assertEqual(replies[0], initialization)
            self.assertIs(replies[1]["result"]["isError"], False)
            self.assertEqual(json.loads(replies[1]["result"]["content"][0]["text"]), inventory)

    def test_new_guides_relative_links_resolve(self):
        for filename in ("USER-GUIDE.md", "TROUBLESHOOTING.md", "AI-AGENTS.md"):
            path = DOCS / filename
            text = path.read_text(encoding="utf-8")
            for destination in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
                if "://" in destination or destination.startswith("#"):
                    continue
                target = destination.split("#", 1)[0]
                self.assertTrue((path.parent / unquote(target)).is_file(),
                                "%s has a missing link: %s" % (filename, destination))


if __name__ == "__main__":
    unittest.main()
