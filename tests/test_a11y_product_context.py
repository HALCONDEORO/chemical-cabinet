"""Dependency-free regressions for product context and persistent field labels."""
from html.parser import HTMLParser
import unittest

from chemical_cabinet import app, db


class Elements(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.elements = []
        self.ids = {}
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.elements.append((tag, attrs))
        if "id" in attrs:
            if attrs["id"] in self.ids:
                raise AssertionError("duplicate id: " + attrs["id"])
            self.ids[attrs["id"]] = (tag, attrs)


class ProductContextTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")
        for name in ("Blue cleaner (demo)", "Floor cleaner (demo)"):
            db.add_item(self.con, name, sds="a" * 16 + ".pdf")
        self.items = db.active_items(self.con)
        self.source = app.render_list(self.items, "a" * 32)
        self.html = Elements(self.source)

    def tearDown(self):
        self.con.close()

    def test_every_article_is_named_by_its_own_heading(self):
        articles = [a for tag, a in self.html.elements if tag == "article"]
        self.assertEqual(len(articles), 2)
        for attrs, item in zip(articles, self.items):
            heading = "product-%d" % item["id"]
            self.assertEqual(attrs["aria-labelledby"], heading)
            self.assertEqual(self.html.ids[heading][0], "h2")
            # A single text occurrence avoids unnecessary repetition for speech
            # and preserves consumers that extract product names from the page.
            self.assertEqual(self.source.count(item["name"]), 1)

    def test_actions_and_retire_forms_reference_the_right_product(self):
        for tag, attrs in self.html.elements:
            for target in attrs.get("aria-labelledby", "").split():
                self.assertIn(target, self.html.ids)
        for item in self.items:
            n = item["id"]
            for prefix in ("edit", "sds", "retire-summary", "retire-button"):
                attrs = self.html.ids["%s-%d" % (prefix, n)][1]
                self.assertEqual(attrs["aria-labelledby"], "%s-%d product-%d" % (prefix, n, n))
            form = next(attrs for tag, attrs in self.html.elements
                        if tag == "form" and attrs.get("action") == "/retire/%d" % n)
            self.assertEqual(form["aria-labelledby"], "retire-summary-%d product-%d" % (n, n))

    def test_retire_fields_have_visible_labels_and_remain_optional(self):
        labels = {a["for"] for tag, a in self.html.elements if tag == "label"}
        fields = [a for tag, a in self.html.elements
                  if tag == "input" and a.get("name") in ("reason", "method")]
        self.assertEqual(len(fields), 4)
        for attrs in fields:
            self.assertIn(attrs["id"], labels)
            self.assertNotIn("required", attrs)
        self.assertEqual(self.source.count("Reason for retiring (optional)</label>"), 2)
        self.assertEqual(self.source.count("How it was disposed of (optional)</label>"), 2)

    def test_names_cannot_inject_elements_or_attributes(self):
        db.add_item(self.con, "Demo '</h2><script>alert(1)</script>")
        source = app.render_list(db.active_items(self.con))
        parsed = Elements(source)
        self.assertNotIn("script", [tag for tag, _ in parsed.elements])
        self.assertIn("&lt;script&gt;", source)

    def test_skip_target_accepts_programmatic_not_sequential_focus(self):
        parsed = Elements(app.page("Cabinet", self.source).decode())
        tag, attrs = parsed.ids["main"]
        self.assertEqual(tag, "main")
        self.assertEqual(attrs["tabindex"], "-1")
        self.assertEqual(attrs["aria-label"], "Main content")
        self.assertNotIn("tabindex=0", self.source)


if __name__ == "__main__":
    unittest.main()
