import os
import shutil
import subprocess
import tempfile
import unittest

from photoserver.imageinfo import ImageInfoError, read_info
from tests.helpers import make_heic, make_jpeg, make_png


class ImageInfoTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, name, data):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_png(self):
        self.assertEqual(read_info(self.write("a.png", make_png(7, 5))), (7, 5, "image/png"))

    def test_jpeg_plain(self):
        self.assertEqual(read_info(self.write("a.jpg", make_jpeg(400, 300))), (400, 300, "image/jpeg"))

    def test_jpeg_orientation_swaps_for_5_to_8_only(self):
        for orientation, expected in [(1, (400, 300)), (2, (400, 300)), (3, (400, 300)), (4, (400, 300)),
                                      (5, (300, 400)), (6, (300, 400)), (7, (300, 400)), (8, (300, 400))]:
            path = self.write("o.jpg", make_jpeg(400, 300, orientation=orientation))
            self.assertEqual(read_info(path)[:2], expected, f"orientation {orientation}")

    def test_jpeg_with_large_segment_before_frame_header(self):
        path = self.write("big.jpg", make_jpeg(640, 480, orientation=6, big_app_segment=60000))
        self.assertEqual(read_info(path)[:2], (480, 640))

    def test_heic_size_and_rotation(self):
        self.assertEqual(read_info(self.write("a.heic", make_heic(4032, 3024)))[:2], (4032, 3024))
        self.assertEqual(read_info(self.write("b.heic", make_heic(4032, 3024, rotation=1)))[:2], (3024, 4032))
        self.assertEqual(read_info(self.write("c.heic", make_heic(4032, 3024, rotation=2)))[:2], (4032, 3024))
        self.assertEqual(read_info(self.write("d.heic", make_heic(4032, 3024, rotation=3)))[:2], (3024, 4032))

    def test_heic_uses_primary_item_not_the_thumbnail(self):
        path = self.write("t.heic", make_heic(3000, 2000, other_first=True))
        self.assertEqual(read_info(path)[:2], (3000, 2000))

    @unittest.skipUnless(shutil.which("sips"), "needs macOS sips to make a real HEIC")
    def test_real_heic_made_by_sips(self):
        src = self.write("src.png", make_png(64, 48))
        out = os.path.join(self.dir, "real.heic")
        subprocess.run(["sips", "-s", "format", "heic", src, "--out", out], capture_output=True, check=True)
        self.assertEqual(read_info(out)[:2], (64, 48))

    def test_garbage_and_truncated_files_raise_cleanly(self):
        cases = {"empty.jpg": b"", "text.jpg": b"hello world, not a photo",
                 "trunc.jpg": make_jpeg(10, 10)[:6], "trunc.png": make_png(10, 10)[:12],
                 "trunc.heic": make_heic(10, 10)[:30], "zero.png": make_png(0, 0)}
        for name, data in cases.items():
            with self.assertRaises(ImageInfoError, msg=name):
                read_info(self.write(name, data))

    def test_missing_file_raises(self):
        with self.assertRaises(ImageInfoError):
            read_info(os.path.join(self.dir, "nope.jpg"))


if __name__ == "__main__":
    unittest.main()
