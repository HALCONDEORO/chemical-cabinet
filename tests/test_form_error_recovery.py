"""Form-error recovery: a validation error must hand the user their input back.

On base b456e23 all three tests are RED: POST /add or /edit with a bad date or
an empty name answers 400 with a bare parser message, discarding everything
typed (and any file picked is deleted server-side, so the re-rendered form
must say the file has to be chosen again - it cannot be prefilled).

Expected: 400 with the form re-rendered, submitted values intact, and an
error that names the field. Files are re-picked explicitly by the user.
"""
import unittest

import tests.test_cabinet as base


class FormErrorRecoveryTest(unittest.TestCase):
    setUp = base.ServerTest.setUp
    tearDown = base.ServerTest.tearDown
    req = base.ServerTest.req
    token = base.ServerTest.token
    multipart = base.ServerTest.multipart
    post = base.ServerTest.post

    def host(self):
        return {"Host": "localhost:%d" % self.port}

    def add(self, fields, tok):
        fields = dict(fields, csrf=tok)
        body, ct = self.multipart(fields, {})
        return self.post("/add", body, ct, tok)

    def add_one(self, tok, name="Glass spray"):
        status, _, _ = self.add({"name": name}, tok)
        self.assertEqual(status, 303)

    def test_add_invalid_date_keeps_typed_values(self):
        tok = self.token()
        status, page, _ = self.add(
            {"name": "Descaler gel", "location": "Shelf B", "expires_on": "31/12/2026"}, tok)
        self.assertEqual(status, 400)
        page = page.decode()
        self.assertIn("Descaler gel", page,
                      "the answer must re-render the form with what was typed")
        self.assertIn("Shelf B", page)
        self.assertRegex(page, r"[Ee]xpires",
                         "the error must name the field, not only the parser message")

    def test_edit_invalid_date_keeps_typed_values(self):
        tok = self.token()
        self.add_one(tok)
        fields = dict(name="Glass spray", location="Shelf C", opened_on="2026-13-01", csrf=tok)
        body, ct = self.multipart(fields, {})
        status, page, _ = self.post("/edit/1", body, ct, tok)
        self.assertEqual(status, 400)
        page = page.decode()
        self.assertIn("Shelf C", page,
                      "the answer must re-render the edit form with what was typed")
        self.assertRegex(page, r"[Oo]pened")

    def test_add_without_name_keeps_other_values(self):
        tok = self.token()
        status, page, _ = self.add(
            {"name": "", "location": "Shelf D", "notes": "for the floor machine"}, tok)
        self.assertEqual(status, 400)
        page = page.decode()
        self.assertIn("Shelf D", page)
        self.assertIn("for the floor machine", page)
        self.assertRegex(page, r"[Nn]ame",
                         "the error must say the name is what is missing")


if __name__ == "__main__":
    unittest.main()
