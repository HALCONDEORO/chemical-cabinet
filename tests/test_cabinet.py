import http.client
import io
import json
import os
import re
import subprocess
import sys
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
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler(self.tmp.name, ["cabinet.test"]))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_port

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        out = (r.status, data, r.getheader("Set-Cookie"))
        c.close()
        return out

    def token(self):
        status, body, cookie = self.req("GET", "/add", headers={"Host": "localhost:%d" % self.port})
        self.assertEqual(status, 200)
        tok = re.search(r"name=csrf value='([0-9a-f]{32})'", body.decode()).group(1)
        self.assertIn(tok, cookie)
        self.assertIn("SameSite=Strict", cookie)
        return tok

    def multipart(self, fields, files):
        b = "XBOUNDARYX"
        out = b""
        for k, v in fields.items():
            out += ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (b, k, v)).encode()
        for k, (fn, ct, data) in files.items():
            out += ('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
                    'Content-Type: %s\r\n\r\n' % (b, k, fn, ct)).encode() + data + b"\r\n"
        return out + ("--%s--\r\n" % b).encode(), "multipart/form-data; boundary=" + b

    def post(self, path, body, ctype, tok, host=None, extra=None, cookie=True):
        h = {"Content-Type": ctype, "Host": host or "localhost:%d" % self.port}
        if cookie:
            h["Cookie"] = "cc_csrf=" + tok
        h.update(extra or {})
        return self.req("POST", path, body, h)

    def add(self, fields, files, tok, **kw):
        fields = dict(fields, csrf=tok)
        body, ct = self.multipart(fields, files)
        return self.post("/add", body, ct, tok, **kw)

    def test_add_list_retire(self):
        tok = self.token()
        status, _, _ = self.add({"name": "Example <b>cleaner</b>", "expires_on": "2020-01-01"},
                                {"sds": ("s.pdf", "application/pdf", b"%PDF-1.4 test")}, tok)
        self.assertEqual(status, 303)
        _, page, _ = self.req("GET", "/", headers={"Host": "localhost"})
        page = page.decode()
        self.assertIn("Example &lt;b&gt;cleaner&lt;/b&gt;", page)
        self.assertIn("expired", page)
        self.assertIn("Safety data sheet", page)
        status, _, _ = self.post("/retire/1", ("csrf=%s&reason=old&method=collection" % tok).encode(),
                                 "application/x-www-form-urlencoded", tok)
        self.assertEqual(status, 303)
        _, log, _ = self.req("GET", "/log", headers={"Host": "127.0.0.1"})
        self.assertIn("collection", log.decode())

    def test_rejects_fake_pdf_and_traversal(self):
        tok = self.token()
        status, _, _ = self.add({"name": "x"}, {"sds": ("s.pdf", "application/pdf", b"not a pdf")}, tok)
        self.assertEqual(status, 400)
        status, _, _ = self.req("GET", "/files/../cabinet.sqlite3", headers={"Host": "localhost"})
        self.assertEqual(status, 404)

    def test_csrf_token_required(self):
        tok = self.token()
        body, ct = self.multipart({"name": "x"}, {})  # no csrf field
        self.assertEqual(self.post("/add", body, ct, tok)[0], 403)
        body, ct = self.multipart({"name": "x", "csrf": "0" * 32}, {})  # field differs from cookie
        self.assertEqual(self.post("/add", body, ct, tok)[0], 403)
        self.assertEqual(self.add({"name": "x"}, {}, "0" * 32)[0], 303)  # a matching pair is what a form sends
        body, ct = self.multipart({"name": "x", "csrf": tok}, {})
        self.assertEqual(self.post("/add", body, ct, tok, cookie=False)[0], 403)  # no cookie
        self.assertEqual(self.post("/retire/1", b"reason=x", "application/x-www-form-urlencoded", tok)[0], 403)

    def test_cross_site_and_rebinding_refused(self):
        tok = self.token()
        self.assertEqual(self.add({"name": "x"}, {}, tok, extra={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.add({"name": "x"}, {}, tok, extra={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.add({"name": "x"}, {}, tok, host="evil.example:%d" % self.port)[0], 403)
        status, _, _ = self.req("GET", "/", headers={"Host": "evil.example"})
        self.assertEqual(status, 403)
        status, _, _ = self.req("GET", "/files/" + "a" * 16 + ".pdf", headers={"Host": "evil.example"})
        self.assertEqual(status, 403)

    def test_valid_origins_and_hosts(self):
        tok = self.token()
        host = "localhost:%d" % self.port
        self.assertEqual(self.add({"name": "a"}, {}, tok, extra={"Origin": "http://" + host})[0], 303)
        self.assertEqual(self.add({"name": "b"}, {}, tok, host="192.168.1.20:8080",
                                  extra={"Origin": "http://192.168.1.20:8080"})[0], 303)
        self.assertEqual(self.add({"name": "c"}, {}, tok, host="cabinet.test",
                                  extra={"Sec-Fetch-Site": "same-origin"})[0], 303)
        self.assertEqual(self.req("GET", "/", headers={"Host": "[::1]:8080"})[0], 200)

    def test_bad_content_length(self):
        tok = self.token()
        for value in ("abc", "-5"):
            status, _, _ = self.post("/add", b"x", "multipart/form-data; boundary=x", tok,
                                     extra={"Content-Length": value})
            self.assertEqual(status, 400)


    def test_csv_export_and_review_sheet(self):
        from chemical_cabinet import db
        con = db.connect(os.path.join(self.tmp.name, "cabinet.sqlite3"))
        db.add_item(con, "=HYPERLINK(\"http://x\")", location="Shelf 1", expires_on="2020-01-01")
        db.add_item(con, "Plain <b>cleaner</b> (demo)", location="Shelf 2")
        con.close()
        h = {"Host": "localhost:%d" % self.port}
        status, body, _ = self.req("GET", "/export.csv", headers=h)
        self.assertEqual(status, 200)
        text = body.decode("utf-8-sig")
        rows = list(__import__("csv").reader(text.splitlines()))
        self.assertEqual(rows[0][1], "name")
        self.assertEqual(rows[1][1][0], "'")          # formula neutralised
        self.assertIn("expired", rows[1][8])
        status, body, _ = self.req("GET", "/review", headers=h)
        self.assertEqual(status, 200)
        self.assertIn("&lt;b&gt;cleaner", body.decode())   # escaped
        self.assertNotIn("<b>cleaner", body.decode())
        self.assertEqual(self.req("GET", "/export.csv", headers={"Host": "evil.example"})[0], 403)


class HostCheckTest(unittest.TestCase):
    def test_host_ok(self):
        for good in ("localhost", "localhost:8080", "127.0.0.1:1", "[::1]:80", "10.0.0.5", "CABINET.test"):
            self.assertTrue(app.host_ok(good, ["cabinet.test"]), good)
        for bad in ("", None, "evil.example", "evil.example:80", "localhost.evil.example"):
            self.assertFalse(app.host_ok(bad, ["cabinet.test"]), bad)


class McpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        con = db.connect(os.path.join(self.tmp.name, "cabinet.sqlite3"))
        db.add_item(con, "Example cleaner (demo)", "Shelf 1", expires_on="2020-01-01")
        con.close()

    def tearDown(self):
        self.tmp.cleanup()

    def run_server(self, lines, *extra):
        p = subprocess.run([sys.executable, "-m", "chemical_cabinet.mcp", "--data", self.tmp.name, *extra],
                           input="\n".join(json.dumps(m) for m in lines) + "\n",
                           capture_output=True, text=True, timeout=20)
        self.assertEqual(p.returncode, 0, p.stderr)
        return [json.loads(x) for x in p.stdout.splitlines()]

    def call(self, name, args, *extra):
        out = self.run_server([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": args}},
        ], *extra)
        self.assertEqual(len(out), 2)  # the notification gets no reply
        self.assertEqual(out[0]["result"]["serverInfo"]["name"], "chemical-cabinet")
        return out[1]["result"]

    def test_read_only_by_default(self):
        out = self.run_server([{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])
        names = {t["name"] for t in out[0]["result"]["tools"]}
        self.assertEqual(names, {"list_items", "review", "disposal_log"})
        res = self.call("add_item", {"name": "x"})
        self.assertTrue(res["isError"])
        res = self.call("list_items", {})
        data = json.loads(res["content"][0]["text"])
        self.assertEqual(len(data), 1)
        self.assertIn("expired", data[0]["flags"])
        self.assertEqual(len(json.loads(self.call("review", {})["content"][0]["text"])), 1)

    def test_write_tools_need_flag_and_validate(self):
        res = self.call("add_item", {"name": "Second (demo)", "expires_on": "2030-01-01"}, "--allow-write")
        self.assertFalse(res["isError"])
        self.assertTrue(self.call("add_item", {"name": "x", "expires_on": "01/02/2030"}, "--allow-write")["isError"])
        self.assertTrue(self.call("add_item", {"name": "x" * 501}, "--allow-write")["isError"])
        self.assertTrue(self.call("add_item", {"name": "x", "photo": "p.jpg"}, "--allow-write")["isError"])
        self.assertTrue(self.call("retire_item", {"item_id": "1"}, "--allow-write")["isError"])
        self.assertTrue(self.call("retire_item", {"item_id": 99}, "--allow-write")["isError"])
        res = self.call("retire_item", {"item_id": 1, "reason": "expired", "method": "collection"}, "--allow-write")
        self.assertFalse(res["isError"])
        log = json.loads(self.call("disposal_log", {})["content"][0]["text"])
        self.assertEqual(log[0]["method"], "collection")

    def test_protocol_errors(self):
        out = self.run_server([{"jsonrpc": "2.0", "id": 5, "method": "nope"}])
        self.assertEqual(out[0]["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
