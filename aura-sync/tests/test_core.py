"""Tests for the changed download logic in the local copy of aura-frame-downloader.
Run from the aura-sync folder:  python3 -m unittest discover -s tests -t .
No network is used; `requests` is replaced by a stand-in (and need not be installed)."""
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "downloader"))
try:
    import requests  # noqa: F401
except ImportError:
    sys.modules["requests"] = types.SimpleNamespace(Session=object, get=None)

from aura import core  # noqa: E402
from aura.exceptions import DownloadError  # noqa: E402


class FakeResponse:
    def __init__(self, body=b"", status=200, headers=None, fail_after=None):
        self.status_code = status
        self.headers = headers if headers is not None else {"Content-Length": str(len(body))}
        self._body = body
        self._fail_after = fail_after
        self.closed = False

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._body), 4):
            if self._fail_after is not None and i >= self._fail_after:
                raise ConnectionError("connection dropped")
            yield self._body[i:i + 4]

    def close(self):
        self.closed = True


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.dest = os.path.join(self.dir, "photo.jpeg")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def files(self):
        return sorted(os.listdir(self.dir))

    def test_complete_download_is_renamed_into_place(self):
        with mock.patch.object(core.requests, "get", return_value=FakeResponse(b"0123456789")):
            core._download("https://x/y", self.dest)
        self.assertEqual(open(self.dest, "rb").read(), b"0123456789")
        self.assertEqual(self.files(), ["photo.jpeg"])

    def test_http_error_leaves_nothing_behind(self):
        for status in (403, 404, 500):
            with mock.patch.object(core.requests, "get", return_value=FakeResponse(b"<html>denied</html>", status)):
                with self.assertRaises(DownloadError):
                    core._download("https://x/y", self.dest)
            self.assertEqual(self.files(), [], f"status {status}")

    def test_truncated_download_leaves_nothing_behind(self):
        resp = FakeResponse(b"0123456789", headers={"Content-Length": "100"})
        with mock.patch.object(core.requests, "get", return_value=resp):
            with self.assertRaises(DownloadError):
                core._download("https://x/y", self.dest)
        self.assertEqual(self.files(), [])
        self.assertTrue(resp.closed)

    def test_connection_dropped_midway_leaves_nothing_behind(self):
        resp = FakeResponse(b"0123456789abcdef", fail_after=8)
        with mock.patch.object(core.requests, "get", return_value=resp):
            with self.assertRaises(ConnectionError):
                core._download("https://x/y", self.dest)
        self.assertEqual(self.files(), [])

    def test_content_encoded_replies_skip_the_length_check(self):
        resp = FakeResponse(b"abcd", headers={"Content-Length": "2", "Content-Encoding": "gzip"})
        with mock.patch.object(core.requests, "get", return_value=resp):
            core._download("https://x/y", self.dest)
        self.assertEqual(self.files(), ["photo.jpeg"])


class FileNameTests(unittest.TestCase):
    def test_safe_part_removes_path_tricks(self):
        for bad in ["../../etc/passwd", "..", "a/b", "a\\b", "x\x00y", "", "...."]:
            out = core._safe_part(bad)
            self.assertNotIn("/", out)
            self.assertNotIn("\\", out)
            self.assertFalse(out.startswith("."))
            self.assertTrue(out)

    def test_safe_extension(self):
        self.assertEqual(core._safe_extension("IMG.JPEG"), ".jpeg")
        self.assertEqual(core._safe_extension("a.mp4"), ".mp4")
        self.assertEqual(core._safe_extension("a./../x"), "")
        self.assertEqual(core._safe_extension("a.toolongext"), "")
        self.assertEqual(core._safe_extension("noext"), "")


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.out = os.path.join(self.root, "frame")
        self.assets = [
            {"id": "AAA", "taken_at": "2020-05-01T10:11:12.000", "user_id": "u1",
             "file_name": "a.jpg", "video_url": "https://v/1", "video_file_name": "a.mov"},
            {"id": "../../evil", "taken_at": "../sneaky", "user_id": "u1", "file_name": "b.png"},
        ]
        patches = [mock.patch.object(core, "create_session", return_value=object()),
                   mock.patch.object(core, "get_frame_assets", side_effect=lambda *a, **k: self.assets),
                   mock.patch.object(core.time, "sleep")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def run_download(self, get, **kwargs):
        with mock.patch.object(core.requests, "get", side_effect=get):
            return core.download_photos_from_aura("e", "p", "frame", self.out, **kwargs)

    def all_files(self):
        found = []
        for base, _dirs, names in os.walk(self.root):
            found += [os.path.relpath(os.path.join(base, n), self.root) for n in names]
        return sorted(found)

    def test_nothing_is_written_outside_the_target_folder(self):
        self.run_download(lambda url, **k: FakeResponse(b"data"))
        for path in self.all_files():
            self.assertTrue(path.startswith("frame" + os.sep), path)

    def test_second_run_skips_files_already_downloaded(self):
        downloaded, skipped, total = self.run_download(lambda url, **k: FakeResponse(b"data"))
        self.assertEqual((downloaded, total), (3, 2))   # 2 stills + 1 video
        again = self.run_download(lambda url, **k: self.fail("downloaded again"))
        self.assertEqual((again[0], again[1]), (0, 3))

    def test_skip_videos_option(self):
        downloaded, _, _ = self.run_download(lambda url, **k: FakeResponse(b"data"), skip_videos=True)
        self.assertEqual(downloaded, 2)
        self.assertFalse(any(p.endswith(".mov") for p in self.all_files()))

    def test_failed_item_leaves_no_file_and_is_retried_next_run(self):
        calls = []

        def flaky(url, **k):
            calls.append(url)
            return FakeResponse(b"<html>blocked</html>", 403) if "imgproxy" in url and len(calls) == 1 else FakeResponse(b"ok")

        first = self.run_download(flaky, skip_videos=True)
        self.assertEqual(first[0], 1)                    # one failed, one worked
        self.assertEqual(len(self.all_files()), 1)       # and no broken file was left
        second = self.run_download(lambda url, **k: FakeResponse(b"ok"), skip_videos=True)
        self.assertEqual(second[0], 1)                   # the failed one is fetched now
        self.assertEqual(len(self.all_files()), 2)

    def test_year_folders(self):
        self.run_download(lambda url, **k: FakeResponse(b"x"), organize_by_year=True, skip_videos=True)
        self.assertTrue(any(p.startswith(os.path.join("frame", "2020")) for p in self.all_files()))


if __name__ == "__main__":
    unittest.main()
