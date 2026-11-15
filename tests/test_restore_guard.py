import os
import tempfile
import unittest

from chemical_cabinet import app


class RestoreGuardTest(unittest.TestCase):
    def test_incomplete_restore_is_refused_before_touching_the_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = os.path.join(tmp, "data")
            os.mkdir(data)
            open(os.path.join(data, "RESTORE_INCOMPLETE"), "w").close()
            with self.assertRaises(ValueError):
                app.make_handler(data)
            self.assertEqual(os.listdir(data), ["RESTORE_INCOMPLETE"])

    def test_marker_as_dangling_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = os.path.join(tmp, "data")
            os.mkdir(data)
            os.symlink(os.path.join(tmp, "nowhere"), os.path.join(data, "RESTORE_INCOMPLETE"))
            with self.assertRaises(ValueError):
                app.make_handler(data)
            self.assertFalse(os.path.exists(os.path.join(data, "files")))

    def test_normal_directory_still_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            app.make_handler(os.path.join(tmp, "data"))


if __name__ == "__main__":
    unittest.main()
