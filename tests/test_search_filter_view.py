"""Search, filter, sort and everyday-list navigation behaviour.

Owns the location filter, the sort select (expiry/location/newest), the match
count and the filtered print view. Existing suites (test_paging,
test_return_to_list) pin the pre-0.1.2 behaviour and stay untouched.
"""
import os
import re
import unittest
from urllib.parse import parse_qs, quote, urlparse

from chemical_cabinet import app, db
import tests.test_cabinet as base


class FilterViewTest(unittest.TestCase):
    setUp = base.ServerTest.setUp
    tearDown = base.ServerTest.tearDown
    req = base.ServerTest.req
    token = base.ServerTest.token
    multipart = base.ServerTest.multipart
    post = base.ServerTest.post

    def host(self):
        return {"Host": "localhost:%d" % self.port}

    def fill(self, rows):
        con = db.connect(os.path.join(self.tmp.name, "cabinet.sqlite3"))
        for row in rows:
            db.add_item(con, *row[:2], **row[2])
        con.commit()
        con.close()

    def stock(self):
        self.fill([
            ("Bleach", "Store room", {"expires_on": "2020-01-01"}),
            ("Glass cleaner", "Shelf A", {"expires_on": "2030-01-01", "sds": "a" * 16 + ".pdf",
                                          "photo": "b" * 16 + ".jpg"}),
            ("Descaler", "Shelf A", {"expires_on": "2031-06-01", "sds": "c" * 16 + ".pdf",
                                     "photo": "d" * 16 + ".jpg"}),
        ])

    def get(self, qs=""):
        status, body, _ = self.req("GET", "/" + qs, headers=self.host())
        self.assertEqual(status, 200)
        return body.decode()

    def test_location_filter_matches_exactly_and_offers_known_locations(self):
        self.stock()
        html = self.get("?loc=Shelf+A")
        self.assertIn("2 of 3 products match.", html)
        self.assertIn("Glass cleaner", html)
        self.assertIn("Descaler", html)
        self.assertNotIn("Bleach", html)
        form = self.get()
        for label in ("Every location", "Shelf A", "Store room"):
            self.assertIn(label, form)

    def test_unknown_location_gets_the_clear_message(self):
        self.stock()
        html = self.get("?loc=Nowhere")
        self.assertIn("No products match.", html)
        self.assertIn("Clear the filters.", html)

    def test_location_option_is_escaped_and_round_trips(self):
        hostile = 'Shelf <script>alert(1)</script> & "quoted"'
        self.fill([("Cleaner", hostile, {"expires_on": "2030-01-01", "sds": "a" * 16 + ".pdf",
                                         "photo": "b" * 16 + ".jpg"})])
        html = self.get()
        self.assertNotIn("<script>alert", html)
        self.assertIn("Shelf &lt;script&gt;", html)
        filtered = self.get("?loc=" + quote(hostile))
        self.assertIn("1 of 1 products match.", filtered)
        self.assertIn("Cleaner", filtered)

    def test_no_location_select_when_nothing_has_a_location(self):
        self.fill([("Cleaner", "", {"expires_on": "2030-01-01", "sds": "a" * 16 + ".pdf",
                                    "photo": "b" * 16 + ".jpg"})])
        self.assertNotIn("name=loc", self.get())

    def test_sort_select_offers_the_views_and_keeps_expiry(self):
        self.stock()
        html = self.get()
        self.assertIn("<select name=sort>", html)
        for label in ("Name A-Z", "Soonest expiry first", "Grouped by location", "Newest first"):
            self.assertIn(label, html)
        expired_first = self.get("?sort=expiry")
        self.assertLess(expired_first.index("Bleach"), expired_first.index("Glass cleaner"))
        self.assertIn("value='expiry' selected", expired_first)

    def test_sort_by_location_groups_and_sort_by_newest_inverts(self):
        self.fill([
            ("Alpha", "Room B", {"expires_on": "2030-01-01", "sds": "a" * 16 + ".pdf",
                                 "photo": "b" * 16 + ".jpg", "today": __import__("datetime").date(2026, 5, 1)}),
            ("Beta", "Room A", {"expires_on": "2030-01-01", "sds": "c" * 16 + ".pdf",
                                "photo": "d" * 16 + ".jpg", "today": __import__("datetime").date(2026, 6, 1)}),
        ])
        by_location = self.get("?sort=location")
        self.assertLess(by_location.index("Beta"), by_location.index("Alpha"))
        newest = self.get("?sort=newest")
        self.assertLess(newest.index("Beta"), newest.index("Alpha"))
        self.assertIn("value='newest' selected", newest)

    def test_bogus_sort_falls_back_to_name_order(self):
        self.fill([("Zulu", "", {"expires_on": "2020-01-01"}),
                   ("Alpha", "", {"expires_on": "2020-01-01"})])
        html = self.get("?sort=bogus")
        self.assertLess(html.index("Alpha"), html.index("Zulu"))

    def test_page_links_and_edit_return_keep_the_location(self):
        rows = [("Demo %03d" % i, "Shelf %d" % (i % 2),
                 {"expires_on": "2030-01-01", "sds": "a" * 16 + ".pdf", "photo": "b" * 16 + ".jpg"})
                for i in range(250)]
        self.fill(rows)
        html = self.get("?loc=Shelf+1")
        self.assertIn("125 of 250 products match.", html)
        self.assertIn("href='/?loc=Shelf+1&amp;page=2'", html)
        link = re.search(r"class=edit href='(/edit/\d+[^']*)'", html).group(1).replace("&amp;", "&")
        self.assertEqual(parse_qs(urlparse(link).query), {"loc": ["Shelf 1"]})
        page = self.req("GET", link, headers=self.host())[1].decode()
        self.assertIn("value='loc' value='Shelf 1'".replace("value='loc'", "name='loc'"), page)
        self.assertIn("href='/?loc=Shelf+1'", page)
        tok = self.token()
        body, ct = self.multipart({"name": "Renamed", "csrf": tok, "loc": "Shelf 1"}, {})
        status, _, _ = self.post(link.split("?")[0], body, ct, tok)
        self.assertEqual(status, 303)

    def test_full_view_round_trip_through_an_edit(self):
        self.stock()
        html = self.get("?q=glass&flagged=0&sort=newest&loc=Shelf+A")
        link = re.search(r"class=edit href='(/edit/\d+[^']*)'", html).group(1).replace("&amp;", "&")
        self.assertEqual(parse_qs(urlparse(link).query),
                         {"q": ["glass"], "sort": ["newest"], "loc": ["Shelf A"]})
        tok = self.token()
        body, ct = self.multipart({"name": "Glass cleaner", "csrf": tok, "q": "glass",
                                   "sort": "newest", "loc": "Shelf A"}, {})
        self.post(link.split("?")[0], body, ct, tok)
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        body, ct = self.multipart({"name": "Glass cleaner", "csrf": tok, "q": "glass",
                                   "sort": "newest", "loc": "Shelf A"}, {})
        c.request("POST", link.split("?")[0], body=body,
                  headers={"Content-Type": ct, "Host": "localhost:%d" % self.port,
                           "Cookie": "cc_csrf=" + tok})
        location = c.getresponse().getheader("Location")
        c.close()
        self.assertEqual(location, "/?q=glass&sort=newest&loc=Shelf+A")

    def test_filtered_list_offers_a_matching_print_view(self):
        self.stock()
        html = self.get("?loc=Shelf+A&sort=expiry")
        self.assertIn("href='/review?sort=expiry&amp;loc=Shelf+A'", html)
        review = self.req("GET", "/review?sort=expiry&loc=Shelf+A", headers=self.host())[1].decode()
        self.assertIn('Filtered: location &quot;Shelf A&quot;; soonest expiry first.', review)
        self.assertIn("Glass cleaner", review)
        self.assertIn("Descaler", review)
        self.assertNotIn("Bleach", review)

    def test_plain_print_view_is_unchanged(self):
        self.stock()
        review = self.req("GET", "/review", headers=self.host())[1].decode()
        self.assertNotIn("Filtered:", review)
        self.assertIn("Bleach", review)

    def test_match_count_only_appears_with_active_filters(self):
        self.stock()
        self.assertNotIn("products match.", self.get())
        self.assertIn("3 of 3 products match.", self.get("?q=e"))


if __name__ == "__main__":
    unittest.main()
