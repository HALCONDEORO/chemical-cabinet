"""Attachment read hardening against symlink path escape."""
import os
import tempfile
import unittest

from chemical_cabinet import app
import test_cabinet as harness


class AttachmentReadSecurityTest(unittest.TestCase):
    setUp = harness.ServerTest.setUp
    tearDown = harness.ServerTest.tearDown
    req = harness.ServerTest.req

    def test_file_route_refuses_symlinks_and_other_non_regular_files(self):
        files_dir = os.path.join(self.tmp.name, "files")
        protected = os.path.join(self.tmp.name, "not-public.txt")
        with open(protected, "w", encoding="utf-8") as fh:
            fh.write("private")
        for name in ("c" * 16 + ".pdf", "d" * 16 + ".pdf"):
            os.symlink(protected, os.path.join(files_dir, name))
        os.mkdir(os.path.join(files_dir, "e" * 16 + ".pdf"))

        status, body, _ = self.req("GET", "/files/" + "c" * 16 + ".pdf",
                                   headers={"Host": "localhost"})
        self.assertEqual(status, 404)
        self.assertNotIn(b"private", body)
        status, _, _ = self.req("GET", "/files/" + "e" * 16 + ".pdf",
                                headers={"Host": "localhost"})
        self.assertEqual(status, 404)

    def test_files_directory_itself_cannot_be_a_symlink(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as elsewhere:
            os.symlink(elsewhere, os.path.join(data, "files"))
            with self.assertRaisesRegex(ValueError, "must not be a symlink"):
                app.make_handler(data)

    def test_file_route_notices_a_swapped_files_directory(self):
        files_dir = os.path.join(self.tmp.name, "files")
        name = "f" * 16 + ".pdf"
        with tempfile.TemporaryDirectory() as elsewhere:
            with open(os.path.join(elsewhere, name), "wb") as fh:
                fh.write(b"%PDF-1.4 private")
            os.rmdir(files_dir)
            os.symlink(elsewhere, files_dir)
            status, body, _ = self.req("GET", "/files/" + name,
                                       headers={"Host": "localhost"})
        self.assertEqual(status, 404)
        self.assertNotIn(b"private", body)


if __name__ == "__main__":
    unittest.main()
