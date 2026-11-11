import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from photoserver.library import Library
from photoserver.server import PhotoServer
from tests.helpers import make_heic, make_jpeg, make_png


class ServerTests(unittest.TestCase):
    TOKEN = "s3cret"

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.data = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "holiday"))
        self.put("holiday/a.jpg", make_jpeg(400, 300))
        self.put("b.png", make_png(30, 50))
        self.put("c.heic", make_heic(4032, 3024, rotation=1))
        self.start()

    def start(self, token=TOKEN, settle=0.0):
        self.library = Library(self.root, self.data, settle_seconds=settle)
        self.library.scan()
        self.server = PhotoServer(("127.0.0.1", 0), self.library, token)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.data, ignore_errors=True)

    def put(self, rel, data, age=60):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        t = time.time() - age
        os.utime(path, (t, t))

    def get(self, path, token="s3cret", headers=None, method="GET"):
        req = urllib.request.Request(self.base + path, method=method, headers=headers or {})
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def catalog(self):
        status, _, body = self.get("/api/v1/catalog")
        self.assertEqual(status, 200)
        return json.loads(body)

    # -- catalog ------------------------------------------------------------

    def test_catalog_lists_photos_with_display_sizes(self):
        photos = {(p["width"], p["height"]) for p in self.catalog()["photos"]}
        self.assertEqual(photos, {(400, 300), (30, 50), (3024, 4032)})   # the HEIC is rotated

    def test_catalog_never_exposes_paths(self):
        text = json.dumps(self.catalog())
        self.assertNotIn("holiday", text)
        self.assertNotIn(".jpg", text)

    def test_catalog_etag_gives_304_until_something_changes(self):
        _, headers, _ = self.get("/api/v1/catalog")
        etag = headers["ETag"]
        self.assertEqual(self.get("/api/v1/catalog", headers={"If-None-Match": etag})[0], 304)
        self.put("new.png", make_png(9, 9))
        self.assertTrue(self.library.scan())
        status, headers2, _ = self.get("/api/v1/catalog", headers={"If-None-Match": etag})
        self.assertEqual(status, 200)
        self.assertNotEqual(headers2["ETag"], etag)

    # -- photos -------------------------------------------------------------

    def photo_url(self, w, h):
        p = next(p for p in self.catalog()["photos"] if (p["width"], p["height"]) == (w, h))
        return f"/api/v1/photos/{p['id']}?v={p['revision']}"

    def test_photo_bytes_are_the_original_file(self):
        status, headers, body = self.get(self.photo_url(400, 300))
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/jpeg")
        with open(os.path.join(self.root, "holiday", "a.jpg"), "rb") as f:
            self.assertEqual(body, f.read())
        self.assertIn("immutable", headers["Cache-Control"])

    def test_photo_range_requests(self):
        url = self.photo_url(400, 300)
        _, _, full = self.get(url)
        status, headers, part = self.get(url, headers={"Range": "bytes=10-19"})
        self.assertEqual((status, part), (206, full[10:20]))
        self.assertEqual(headers["Content-Range"], f"bytes 10-19/{len(full)}")
        self.assertEqual(self.get(url, headers={"Range": "bytes=-5"})[2], full[-5:])
        self.assertEqual(self.get(url, headers={"Range": f"bytes={len(full) + 10}-"})[0], 416)

    def test_photo_conditional_get_and_head(self):
        url = self.photo_url(30, 50)
        _, headers, _ = self.get(url)
        self.assertEqual(self.get(url, headers={"If-None-Match": headers["ETag"]})[0], 304)
        status, h, body = self.get(url, method="HEAD")
        self.assertEqual((status, body), (200, b""))
        self.assertEqual(int(h["Content-Length"]), len(self.get(url)[2]))

    def test_unknown_ids_and_path_tricks_are_404(self):
        for path in ["/api/v1/photos/0000000000000000", "/api/v1/photos/../../etc/passwd",
                     "/api/v1/photos/%2e%2e%2f%2e%2e%2fetc%2fpasswd", "/api/v1/photos/abc",
                     "/etc/passwd", "/api/v1/photos/", "/"]:
            self.assertEqual(self.get(path)[0], 404, path)

    # -- auth ---------------------------------------------------------------

    def test_token_required_except_health(self):
        self.assertEqual(self.get("/api/v1/catalog", token=None)[0], 401)
        self.assertEqual(self.get("/api/v1/catalog", token="wrong")[0], 401)
        self.assertEqual(self.get("/api/v1/photos/0000000000000000", token=None)[0], 401)
        status, _, body = self.get("/api/v1/health", token=None)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["authRequired"])

    def test_token_in_query_string_also_works(self):
        self.assertEqual(self.get("/api/v1/catalog?token=s3cret", token=None)[0], 200)

    def test_no_token_configured_means_open(self):
        self.server.shutdown(); self.server.server_close()
        self.start(token="")
        self.assertEqual(self.get("/api/v1/catalog", token=None)[0], 200)

    # -- library behaviour --------------------------------------------------

    def test_new_and_removed_photos_appear_after_a_scan(self):
        before = len(self.catalog()["photos"])
        self.put("dump/many1.jpg", make_jpeg(100, 100))
        self.put("dump/sub/many2.png", make_png(50, 40))
        self.assertTrue(self.library.scan())
        self.assertEqual(len(self.catalog()["photos"]), before + 2)
        os.remove(os.path.join(self.root, "b.png"))
        self.assertTrue(self.library.scan())
        self.assertEqual(len(self.catalog()["photos"]), before + 1)
        self.assertFalse(self.library.scan())

    def test_junk_folders_and_files_are_ignored(self):
        for rel in ["@eaDir/a.jpg/SYNOPHOTO_THUMB_M.jpg", "#recycle/old.jpg", ".hidden/x.jpg",
                    "holiday/._a.jpg", "holiday/.DS_Store", "notes.txt", "movie.mov"]:
            self.put(rel, make_jpeg(10, 10))
        self.library.scan()
        self.assertEqual(len(self.catalog()["photos"]), 3)

    def test_unfinished_copies_wait_until_settled(self):
        self.server.shutdown(); self.server.server_close()
        self.start(settle=5.0)
        self.put("copying.jpg", make_jpeg(10, 10), age=0)       # just written: still being copied
        self.library.scan()
        self.assertEqual(len(self.catalog()["photos"]), 3)
        self.put("copying.jpg", make_jpeg(10, 10), age=30)      # settled
        self.library.scan()
        self.assertEqual(len(self.catalog()["photos"]), 4)

    def test_corrupt_files_are_skipped_with_a_warning_and_do_not_break_the_catalog(self):
        self.put("bad.jpg", b"this is not a jpeg")
        self.library.scan()
        cat = self.catalog()
        self.assertEqual(len(cat["photos"]), 3)
        self.assertTrue(any("bad.jpg" in w for w in cat["warnings"]))

    def test_replaced_file_gets_a_new_revision(self):
        old = {p["id"]: p["revision"] for p in self.catalog()["photos"]}
        self.put("b.png", make_png(60, 80))
        self.library.scan()
        new = {p["id"]: p for p in self.catalog()["photos"]}
        changed = [i for i in old if new[i]["revision"] != old[i]]
        self.assertEqual(len(changed), 1)
        self.assertEqual((new[changed[0]]["width"], new[changed[0]]["height"]), (60, 80))

    def test_index_is_saved_and_reused_after_restart(self):
        self.assertTrue(os.path.exists(os.path.join(self.data, "index.json")))
        again = Library(self.root, self.data, settle_seconds=0)
        self.assertEqual(len(again._cache), 3)                  # loaded without reading any image
        again.scan()
        self.assertEqual(again.snapshot.revision, self.library.snapshot.revision)

    def test_health_is_cheap_and_correct(self):
        status, _, body = self.get("/api/v1/health", token=None)
        data = json.loads(body)
        self.assertEqual((status, data["ok"], data["photos"]), (200, True, 3))


if __name__ == "__main__":
    unittest.main()
