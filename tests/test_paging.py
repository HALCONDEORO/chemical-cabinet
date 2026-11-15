import os
import re
import unittest

from chemical_cabinet import app, db
import tests.test_cabinet as base


class PagingTest(unittest.TestCase):
    setUp = base.ServerTest.setUp
    tearDown = base.ServerTest.tearDown
    req = base.ServerTest.req

    def fill(self, n):
        con = db.connect(os.path.join(self.tmp.name, "cabinet.sqlite3"))
        for i in range(n):
            db.add_item(con, "Demo %03d" % i, "Shelf %d" % (i % 3), "", "", None, "2020-01-01")
        con.commit()
        con.close()

    def get(self, qs=""):
        status, body, _ = self.req("GET", "/" + qs, headers={"Host": "localhost:%d" % self.port})
        self.assertEqual(status, 200)
        return body.decode()

    def names(self, html):
        return re.findall(r"Demo \d{3}", html)

    def test_small_lists_have_no_pager(self):
        self.fill(app.PAGE_SIZE)
        html = self.get()
        self.assertEqual(len(self.names(html)), app.PAGE_SIZE)
        self.assertNotIn("Pages of products", html)
        self.assertNotIn("Showing", html)

    def test_pages_cover_every_product_once_in_order(self):
        self.fill(2 * app.PAGE_SIZE + 7)
        seen = []
        for n in (1, 2, 3):
            html = self.get("" if n == 1 else "?page=%d" % n)
            seen += self.names(html)
            self.assertIn("Page %d of 3" % n, html)
        self.assertEqual(seen, ["Demo %03d" % i for i in range(2 * app.PAGE_SIZE + 7)])
        self.assertIn("Showing %d-%d of %d products." % (2 * app.PAGE_SIZE + 1, 2 * app.PAGE_SIZE + 7, 2 * app.PAGE_SIZE + 7), html)
        self.assertNotIn(">Next<", html)
        self.assertNotIn(">Previous<", self.get())

    def test_links_keep_search_filter_and_sort(self):
        self.fill(400)
        html = self.get("?q=shelf+1&flagged=1&sort=expiry")
        self.assertIn("of 133 products", html)
        self.assertIn("href='/?q=shelf+1&amp;flagged=1&amp;sort=expiry&amp;page=2'", html)
        second = self.get("?q=shelf+1&flagged=1&sort=expiry&page=2")
        self.assertIn("href='/?q=shelf+1&amp;flagged=1&amp;sort=expiry'", second)  # back to page 1

    def test_odd_page_values_fall_back_safely(self):
        self.fill(2 * app.PAGE_SIZE + 20)
        for value in ("0", "-3", "abc", "9" * 30, "999", "", "1.5"):
            with self.subTest(page=value):
                html = self.get("?page=" + value)
                self.assertTrue(self.names(html))
                self.assertRegex(html, r"Page [13] of 3")


if __name__ == "__main__":
    unittest.main()
