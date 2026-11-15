"""Read-only orphan report: references versus files, with in-flight protection."""
import hashlib
import os
import re
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

import tests.test_cabinet as base
from chemical_cabinet import app, db, orphans

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 40


def tree(path):
    out = {}
    for root, _, names in os.walk(path):
        for n in names:
            p = os.path.join(root, n)
            out[p] = (os.path.getsize(p), os.path.getmtime(p), hashlib.sha256(open(p, "rb").read()).hexdigest())
    return out


class OrphanReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = self.tmp.name
        os.makedirs(os.path.join(self.data, "files"))
        con = db.connect(os.path.join(self.data, "cabinet.sqlite3"))
        self.kept = "a" * 16 + ".png"
        self.kept_sds = "b" * 16 + ".pdf"
        self.old_orphan = "c" * 16 + ".png"
        self.new_file = "d" * 16 + ".png"
        self.missing = "e" * 16 + ".png"
        db.add_item(con, "Demo one", photo=self.kept, sds=self.kept_sds)
        db.add_item(con, "Demo two", photo=self.missing)
        con.close()
        for n in (self.kept, self.kept_sds, self.old_orphan, self.new_file):
            with open(os.path.join(self.data, "files", n), "wb") as fh:
                fh.write(b"x" * 10)
        old = os.path.getmtime(self.data) - 7200
        os.utime(os.path.join(self.data, "files", self.old_orphan), (old, old))
        os.utime(os.path.join(self.data, "files", self.kept), (old, old))

    def tearDown(self):
        self.tmp.cleanup()

    def test_classifies_and_changes_nothing(self):
        before = tree(self.data)
        r = orphans.scan(self.data, 3600)
        self.assertEqual([n for n, _, _ in r["orphans"]], [self.old_orphan])
        self.assertEqual([n for n, _, _ in r["recent"]], [self.new_file])
        self.assertEqual(r["missing"], [self.missing])
        self.assertEqual(r["referenced"], 3)
        self.assertEqual(tree(self.data), before)

    def test_does_not_create_a_database_or_follow_symlinks(self):
        empty = tempfile.mkdtemp()
        os.makedirs(os.path.join(empty, "files"))
        with self.assertRaises(ValueError):
            orphans.scan(empty)
        self.assertFalse(os.path.exists(os.path.join(empty, "cabinet.sqlite3")))
        os.symlink("/etc/hostname", os.path.join(self.data, "files", "f" * 16 + ".png"))
        r = orphans.scan(self.data, 3600)
        self.assertEqual(r["other"], ["f" * 16 + ".png"])

    def test_report_has_no_product_text(self):
        text = orphans.render(orphans.scan(self.data, 3600), 3600)
        self.assertNotIn("Demo", text)
        self.assertIn("Nothing was changed", text)


class OddPathAndUnreadableTest(unittest.TestCase):
    def make(self, root, name, ref, files):
        d = os.path.join(root, name)
        os.makedirs(os.path.join(d, "files"))
        con = db.connect(os.path.join(d, "cabinet.sqlite3"))
        if ref:
            db.add_item(con, "Demo", photo=ref)
        con.close()
        for n in files:
            open(os.path.join(d, "files", n), "wb").write(b"1")
        return d

    def test_special_characters_in_the_folder_name(self):
        ref = "a" * 16 + ".png"
        with tempfile.TemporaryDirectory() as root:
            # A sibling database that a wrongly decoded path would open instead.
            self.make(root, "a", None, [])
            for name in ("%61", "q?x", "h#x", "caf\u00e9 \u00f1", "p%3fq"):
                d = self.make(root, name, ref, [ref])
                r = orphans.scan(d, 0)
                self.assertEqual((r["referenced"], r["orphans"], r["missing"]), (1, [], []), name)

    def test_unreadable_entry_fails_closed(self):
        ref = "a" * 16 + ".png"
        with tempfile.TemporaryDirectory() as root:
            d = self.make(root, "d", ref, [ref])
            import unittest.mock as mock

            class Boom:
                name = ref
                def stat(self, follow_symlinks=True):
                    raise PermissionError(13, "Permission denied")
            with mock.patch.object(orphans.os, "scandir", return_value=[Boom()]):
                with self.assertRaises(ValueError) as cm:
                    orphans.scan(d, 0)
                self.assertEqual(orphans.main(["--data", d]), 2)
            self.assertIn("incomplete", str(cm.exception))


class LateUploadTest(unittest.TestCase):
    def test_upload_committed_after_the_listing_is_not_missing(self):
        import unittest.mock as mock
        late = "a" * 16 + ".png"
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "files"))
            con = db.connect(os.path.join(d, "cabinet.sqlite3"))
            con.close()
            real = os.scandir

            def scandir_then_upload(path):
                entries = list(real(path))  # the listing is taken first (empty)
                open(os.path.join(path, late), "wb").write(b"1")
                c = db.connect(os.path.join(d, "cabinet.sqlite3"))
                db.add_item(c, "Demo", photo=late)
                c.close()
                return entries
            with mock.patch.object(orphans.os, "scandir", side_effect=scandir_then_upload):
                r = orphans.scan(d, 3600)
            self.assertEqual(r["missing"], [])
            self.assertEqual(r["orphans"], [])


class ConcurrentEditOrphanTest(unittest.TestCase):
    setUp = base.ServerTest.setUp
    tearDown = base.ServerTest.tearDown
    req = base.ServerTest.req
    token = base.ServerTest.token
    multipart = base.ServerTest.multipart
    post = base.ServerTest.post

    def test_concurrent_photo_edits_are_reported_not_lost(self):
        tok = self.token()
        body, ct = self.multipart({"name": "A", "csrf": tok}, {"photo": ("p.png", "image/png", PNG)})
        self.assertEqual(self.post("/add", body, ct, tok)[0], 303)

        def edit(i):
            b, c = self.multipart({"name": "A%d" % i, "csrf": tok},
                                  {"photo": ("p.png", "image/png", PNG + bytes([i]) * 5)})
            self.post("/edit/1", b, c, tok)
        for _ in range(8):
            ts = [threading.Thread(target=edit, args=(i,)) for i in range(6)]
            [t.start() for t in ts]
            [t.join() for t in ts]
        r = orphans.scan(self.tmp.name, 0)
        self.assertEqual(r["missing"], [], "a referenced upload must still exist")
        self.assertEqual(r["referenced"], 1)
        self.assertEqual(len(r["orphans"]) + len(r["recent"]), r["on_disk"] - 1)
        young = orphans.scan(self.tmp.name, 3600)
        self.assertEqual(young["orphans"], [], "just-written files are not called orphans")


if __name__ == "__main__":
    unittest.main()
