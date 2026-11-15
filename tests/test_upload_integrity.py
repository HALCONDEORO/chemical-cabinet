"""Failure-path uploads against the real HTTP handler and a synthetic database."""
import os
import unittest
from unittest.mock import patch

from chemical_cabinet import app, db
import test_cabinet as harness


class UploadIntegrityTest(unittest.TestCase):
    setUp = harness.ServerTest.setUp
    tearDown = harness.ServerTest.tearDown
    req = harness.ServerTest.req
    token = harness.ServerTest.token
    multipart = harness.ServerTest.multipart
    post = harness.ServerTest.post
    add = harness.ServerTest.add

    def uploads(self):
        return {"photo": ("test.png", "image/png", b"\x89PNG\r\n\x1a\nsynthetic"),
                "sds": ("test.pdf", "application/pdf", b"%PDF-1.4 synthetic")}

    def assert_empty(self):
        self.assertEqual(os.listdir(os.path.join(self.tmp.name, "files")), [])
        con = db.connect(os.path.join(self.tmp.name, "cabinet.sqlite3"))
        try:
            self.assertEqual(con.execute("SELECT count(*) FROM items").fetchone()[0], 0)
        finally:
            con.close()

    def test_invalid_name_leaves_no_uploads(self):
        self.assertEqual(self.add({"name": " "}, self.uploads(), self.token())[0], 400)
        self.assert_empty()

    def test_invalid_date_leaves_no_uploads(self):
        self.assertEqual(self.add({"name": "synthetic", "expires_on": "bad"},
                                  self.uploads(), self.token())[0], 400)
        self.assert_empty()

    def test_bad_second_file_leaves_no_first_file(self):
        uploads = self.uploads()
        uploads["sds"] = ("test.pdf", "application/pdf", b"not pdf")
        self.assertEqual(self.add({"name": "synthetic"}, uploads, self.token())[0], 400)
        self.assert_empty()

    def test_database_failure_cleans_both_uploads(self):
        con = db.connect(os.path.join(self.tmp.name, "cabinet.sqlite3"))
        con.execute("CREATE TRIGGER fail_add BEFORE INSERT ON items BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
        con.close()
        # Existing API closes the connection without a response on sqlite errors.
        # Suppress only the server's expected traceback; test real DB failure.
        with patch.object(self.srv, "handle_error"):
            import http.client
            with self.assertRaises(http.client.RemoteDisconnected):
                self.add({"name": "synthetic"}, self.uploads(), self.token())
        self.assert_empty()

    def test_success_keeps_files_referenced_by_product(self):
        self.assertEqual(self.add({"name": "synthetic"}, self.uploads(), self.token())[0], 303)
        con = db.connect(os.path.join(self.tmp.name, "cabinet.sqlite3"))
        try:
            row = con.execute("SELECT photo, sds FROM items").fetchone()
            self.assertEqual(set(os.listdir(os.path.join(self.tmp.name, "files"))), set(row))
        finally:
            con.close()
