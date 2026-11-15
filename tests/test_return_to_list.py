import os
import re
import unittest
from urllib.parse import parse_qs, urlparse

from chemical_cabinet import app, db
import tests.test_cabinet as base


class ReturnToListTest(unittest.TestCase):
    setUp = base.ServerTest.setUp
    tearDown = base.ServerTest.tearDown
    req = base.ServerTest.req
    token = base.ServerTest.token
    multipart = base.ServerTest.multipart
    post = base.ServerTest.post

    def host(self):
        return {"Host": "localhost:%d" % self.port}

    def fill(self, n):
        con = db.connect(os.path.join(self.tmp.name, "cabinet.sqlite3"))
        for i in range(n):
            db.add_item(con, "Demo %03d" % i, "Shelf %d" % (i % 2), "", "", None, "2020-01-01")
        con.commit()
        con.close()

    def edit_link(self, qs):
        html = self.req("GET", "/" + qs, headers=self.host())[1].decode()
        return re.search(r"class=edit href='(/edit/\d+[^']*)'", html).group(1).replace("&amp;", "&")

    def test_edit_link_carries_the_view_and_the_form_returns_to_it(self):
        self.fill(250)
        link = self.edit_link("?q=shelf+0&flagged=1&sort=expiry&page=2")
        self.assertEqual(parse_qs(urlparse(link).query),
                         {"q": ["shelf 0"], "flagged": ["1"], "sort": ["expiry"], "page": ["2"]})
        page = self.req("GET", link, headers=self.host())[1].decode()
        self.assertIn("Back to the list", page)
        self.assertIn("href='/?q=shelf+0&amp;flagged=1&amp;sort=expiry&amp;page=2'", page)
        tok = self.token()
        body, ct = self.multipart({"name": "Renamed", "csrf": tok, "q": "shelf 0", "flagged": "1",
                                   "sort": "expiry", "page": "2"}, {})
        status, _, _ = self.post(link.split("?")[0], body, ct, tok)
        self.assertEqual(status, 303)
        location = self.last_location(link.split("?")[0], tok)
        self.assertEqual(location, "/?q=shelf+0&flagged=1&sort=expiry&page=2")

    def last_location(self, path, tok):
        body, ct = self.multipart({"name": "Again", "csrf": tok, "q": "shelf 0", "flagged": "1",
                                   "sort": "expiry", "page": "2"}, {})
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("POST", path, body=body, headers={"Content-Type": ct, "Host": "localhost:%d" % self.port,
                                                    "Cookie": "cc_csrf=" + tok})
        r = c.getresponse()
        r.read()
        c.close()
        return r.getheader("Location")

    def test_without_a_view_edit_goes_back_to_the_plain_list(self):
        self.fill(3)
        tok = self.token()
        self.assertEqual(self.last_location("/edit/1", tok).split("?")[0], "/")
        link = self.edit_link("")
        self.assertEqual(link, "/edit/1")

    def test_a_forged_view_cannot_redirect_elsewhere(self):
        self.fill(2)
        tok = self.token()
        import http.client
        for value in ("//evil.example", "https://evil.example/", "/\\evil", "\r\nX-Evil: 1", "\u00b2", "\u0662"):
            with self.subTest(value=value):
                body, ct = self.multipart({"name": "x", "csrf": tok, "q": value, "sort": value, "page": value}, {})
                c = http.client.HTTPConnection("127.0.0.1", self.port)
                c.request("POST", "/edit/1", body=body, headers={
                    "Content-Type": ct, "Host": "localhost:%d" % self.port, "Cookie": "cc_csrf=" + tok})
                r = c.getresponse()
                r.read()
                loc = r.getheader("Location")
                c.close()
                self.assertTrue(loc.startswith("/"), loc)
                self.assertFalse(loc.startswith("//"), loc)
                self.assertNotIn("\n", loc)
                self.assertNotIn("evil", urlparse(loc).netloc)

    def test_edit_page_escapes_a_hostile_query(self):
        self.fill(1)
        page = self.req("GET", "/edit/1?q=%27%3E%3Cscript%3Ex%3C%2Fscript%3E", headers=self.host())[1].decode()
        self.assertNotIn("<script>x", page)


if __name__ == "__main__":
    unittest.main()
