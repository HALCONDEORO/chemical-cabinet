"""Structural keyboard checks on the rendered pages.

This reads the HTML. It does not drive a real browser, so it cannot prove the
tab order on screen; it catches the usual structural mistakes.
"""
from html.parser import HTMLParser
import unittest

import tests.test_cabinet as base


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.focusable = []      # (tag, attrs) in document order
        self.labels_for = set()
        self.label_depth = 0
        self.imgs = []
        self.h1 = 0
        self.lang = None
        self.text_stack = []
        self.names = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.lang = a.get("lang")
        if tag == "h1":
            self.h1 += 1
        if tag == "img":
            self.imgs.append(a)
        if tag == "label":
            if a.get("for"):
                self.labels_for.add(a["for"])
            self.label_depth += 1
        hidden = tag == "input" and a.get("type") in ("hidden",)
        if not hidden and (tag in ("a", "button", "input", "textarea", "select", "summary")
                           and not (tag == "a" and "href" not in a)):
            self.focusable.append((tag, a, self.label_depth > 0))
            self.text_stack.append(len(self.focusable) - 1)
            self.names[len(self.focusable) - 1] = ""
        elif "tabindex" in a:
            self.focusable.append((tag, a, False))
            self.names[len(self.focusable) - 1] = ""

    def handle_endtag(self, tag):
        if tag == "label":
            self.label_depth -= 1
        if tag in ("a", "button", "summary") and self.text_stack:
            self.text_stack.pop()

    def handle_data(self, data):
        for i in self.text_stack:
            self.names[i] += data.strip()


class KeyboardStructureTest(unittest.TestCase):
    # Reuse the server helpers without re-running the inherited server tests.
    setUp = base.ServerTest.setUp
    tearDown = base.ServerTest.tearDown
    req = base.ServerTest.req
    token = base.ServerTest.token
    multipart = base.ServerTest.multipart
    post = base.ServerTest.post
    add = base.ServerTest.add

    def pages(self):
        tok = self.token()
        self.add({"name": "Demo cleaner", "expires_on": "2020-01-01"}, {}, tok)
        h = {"Host": "localhost:%d" % self.port}
        for path in ("/", "/add", "/edit/1", "/log", "/review"):
            status, body, _ = self.req("GET", path, headers=h)
            self.assertEqual(status, 200, path)
            page = Page()
            page.feed(body.decode())
            yield path, page

    def test_structure(self):
        for path, page in self.pages():
            with self.subTest(path=path):
                self.assertTrue(page.lang, "html lang")
                self.assertEqual(page.h1, 1, "exactly one h1")
                self.assertTrue(page.focusable)
                first_tag, first_attrs, _ = page.focusable[0]
                self.assertEqual(first_tag, "a")
                self.assertIn("skip", first_attrs.get("class", ""), "skip link comes first")
                for tag, attrs, _ in page.focusable:
                    tabindex = attrs.get("tabindex")
                    self.assertFalse(tabindex and tabindex.isdigit() and int(tabindex) > 0,
                                     "no positive tabindex")
                for img in page.imgs:
                    self.assertIn("alt", img)

    def test_every_control_has_a_name(self):
        for path, page in self.pages():
            for idx, (tag, attrs, in_label) in enumerate(page.focusable):
                with self.subTest(path=path, control=tag, attrs=attrs):
                    if tag in ("a", "button", "summary"):
                        self.assertTrue(page.names[idx] or attrs.get("aria-label"))
                    elif tag == "input" and attrs.get("type") in ("submit", "button"):
                        self.assertTrue(attrs.get("value"))
                    else:
                        self.assertTrue(in_label or attrs.get("id") in page.labels_for
                                        or attrs.get("aria-label"))


if __name__ == "__main__":
    unittest.main()


class CheckerSelfTest(unittest.TestCase):
    def test_checker_sees_problems(self):
        page = Page()
        page.feed("<html><body><a href='/x' tabindex='3'></a><input id='q'><img src='a.png'></body></html>")
        self.assertEqual(page.names[0], "")
        self.assertNotIn("q", page.labels_for)
        self.assertNotIn("alt", page.imgs[0])
        self.assertEqual(page.focusable[0][1]["tabindex"], "3")
