import os
import tempfile
import threading
import unittest
import urllib.request
from datetime import date
from http.server import ThreadingHTTPServer

from chemical_cabinet import app, db

TODAY = date(2026, 6, 1)


class RulesTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")

    def item(self, **kw):
        i = db.add_item(self.con, "Sample cleaner", today=TODAY, **kw)
        return self.con.execute("SELECT * FROM items WHERE id=?", (i,)).fetchone()

    def test_flags(self):
        self.assertIn("expired", db.flags(self.item(expires_on="2026-01-01"), TODAY))
        self.assertIn("expires within 30 days", db.flags(self.item(expires_on="2026-06-20"), TODAY))
        self.assertIn("no expiry date", db.flags(self.item(), TODAY))
        ok = self.item(expires_on="2028-01-01", sds="a" * 16 + ".pdf", photo="b" * 16 + ".jpg")
        self.assertEqual(db.flags(ok, TODAY), [])

    def test_bad_date_and_name(self):
        with self.assertRaises(ValueError):
            db.add_item(self.con, "x", expires_on="01/02/2026")
        with self.assertRaises(ValueError):
            db.add_item(self.con, "  ")

    def test_retire_logs_once(self):
        i = db.add_item(self.con, "Old bleach", today=TODAY)
        db.retire_item(self.con, i, "expired", "hazardous waste collection", today=TODAY)
        self.assertEqual(db.active_items(self.con), [])
        self.assertEqual(len(db.retirement_log(self.con)), 1)
        with self.assertRaises(ValueError):
            db.retire_item(self.con, i)


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler(self.tmp.name))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.srv.server_port

    def tearDown(self):
        self.srv.shutdown()
        self.tmp.cleanup()

    def post(self, path, body, ctype):
        req = urllib.request.Request(self.base + path, data=body, headers={"Content-Type": ctype})
        return urllib.request.urlopen(req)

    def multipart(self, fields, files):
        b = "XBOUNDARYX"
        out = b""
        for k, v in fields.items():
            out += ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (b, k, v)).encode()
        for k, (fn, ct, data) in files.items():
            out += ('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
                    'Content-Type: %s\r\n\r\n' % (b, k, fn, ct)).encode() + data + b"\r\n"
        return out + ("--%s--\r\n" % b).encode(), "multipart/form-data; boundary=" + b

    def test_add_list_retire(self):
        body, ct = self.multipart({"name": "Example <b>cleaner</b>", "expires_on": "2020-01-01"},
                                  {"sds": ("s.pdf", "application/pdf", b"%PDF-1.4 test")})
        self.post("/add", body, ct)
        page = urllib.request.urlopen(self.base + "/").read().decode()
        self.assertIn("Example &lt;b&gt;cleaner&lt;/b&gt;", page)
        self.assertIn("expired", page)
        self.assertIn("Safety data sheet", page)
        self.post("/retire/1", b"reason=old&method=collection", "application/x-www-form-urlencoded")
        log = urllib.request.urlopen(self.base + "/log").read().decode()
        self.assertIn("collection", log)

    def test_rejects_fake_pdf_and_traversal(self):
        body, ct = self.multipart({"name": "x"}, {"sds": ("s.pdf", "application/pdf", b"not a pdf")})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/add", body, ct)
        self.assertEqual(cm.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.base + "/files/../cabinet.sqlite3")
        self.assertEqual(cm.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
