"""HTTP API. Three read-only endpoints; photos are never re-encoded."""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .library import Library, Scanner

log = logging.getLogger("photoserver")

PHOTO_PATH = re.compile(r"^/api/v1/photos/([0-9a-f]{16})$")
CHUNK = 256 * 1024


class PhotoServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: Tuple[str, int], library: Library, token: str = ""):
        super().__init__(address, Handler)
        self.library = library
        self.token = token


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"PhotoFrameServer/{__version__}"
    server: PhotoServer

    def log_message(self, fmt: str, *args) -> None:  # quieter, goes through logging
        log.debug("%s - %s", self.address_string(), fmt % args)

    # -- helpers ------------------------------------------------------------

    def _send_json(self, status: int, payload: dict, etag: Optional[str] = None, head: bool = False) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _authorized(self, query: dict) -> bool:
        token = self.server.token
        if not token:
            return True
        header = self.headers.get("Authorization", "")
        supplied = header[7:] if header.startswith("Bearer ") else (query.get("token") or [""])[0]
        return hmac.compare_digest(supplied.encode(), token.encode())

    # -- routes -------------------------------------------------------------

    def do_HEAD(self) -> None:
        self._route(head=True)

    def do_GET(self) -> None:
        self._route(head=False)

    def _route(self, head: bool) -> None:
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        path = parts.path
        try:
            if path == "/api/v1/health":
                snap = self.server.library.snapshot
                self._send_json(200, {"ok": True, "version": __version__, "photos": len(snap.photos),
                                      "revision": snap.revision, "authRequired": bool(self.server.token)}, head=head)
            elif path == "/api/v1/catalog":
                if not self._authorized(query):
                    return self._send_json(401, {"error": "unauthorized"}, head=head)
                self._catalog(head)
            else:
                match = PHOTO_PATH.match(path)
                if not match:
                    return self._send_json(404, {"error": "not found"}, head=head)
                if not self._authorized(query):
                    return self._send_json(401, {"error": "unauthorized"}, head=head)
                self._photo(match.group(1), head)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client went away mid-transfer

    def _catalog(self, head: bool) -> None:
        snap = self.server.library.snapshot
        etag = f'"{snap.revision}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send_json(200, {"revision": snap.revision, "photos": snap.photos, "warnings": snap.warnings},
                        etag=etag, head=head)

    def _photo(self, photo_id: str, head: bool) -> None:
        library = self.server.library
        entry = library.snapshot.by_id.get(photo_id)
        if entry is None:
            return self._send_json(404, {"error": "unknown photo"}, head=head)
        etag = f'"{entry.revision}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            f = open(library.full_path(entry), "rb")
        except OSError:
            return self._send_json(404, {"error": "file unavailable"}, head=head)
        with f:
            size = os.fstat(f.fileno()).st_size
            start, end, partial = 0, size - 1, False
            rng = self.headers.get("Range")
            if rng:
                match = re.match(r"^bytes=(\d*)-(\d*)$", rng.strip())
                if match and (match.group(1) or match.group(2)):
                    if match.group(1):
                        start = int(match.group(1))
                        end = min(int(match.group(2)), size - 1) if match.group(2) else size - 1
                    else:  # suffix range: the last N bytes
                        start = max(0, size - int(match.group(2)))
                    if start > end or start >= size:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    partial = True
            length = end - start + 1
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", entry.mime or "application/octet-stream")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("ETag", etag)
            # The client asks for ?v=<revision>, so a given URL never changes: cache it hard.
            self.send_header("Cache-Control", "private, max-age=31536000, immutable")
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if head:
                return
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(CHUNK, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def run() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(message)s")
    photo_dir = os.environ.get("PHOTO_DIR", "/photos")
    data_dir = os.environ.get("DATA_DIR", "/data")
    port = int(os.environ.get("PORT", "8080"))
    interval = float(os.environ.get("SCAN_INTERVAL", "30"))
    settle = float(os.environ.get("SETTLE_SECONDS", "10"))
    token = os.environ.get("TOKEN", "")
    token_file = os.environ.get("TOKEN_FILE", "")
    if not token and token_file and os.path.exists(token_file):
        with open(token_file, "r", encoding="utf-8") as f:
            token = f.read().strip()

    if not os.path.isdir(photo_dir):
        raise SystemExit(f"PHOTO_DIR {photo_dir!r} is not a folder. Mount your photos there.")
    if data_dir and not (os.path.isdir(data_dir) and os.access(data_dir, os.W_OK)):
        log.warning("DATA_DIR %s is not writable; the photo index will not be kept between restarts", data_dir)
        data_dir = ""

    library = Library(photo_dir, data_dir or None, settle)
    library.scan()
    scanner = Scanner(library, interval)
    scanner.start()

    server = PhotoServer(("0.0.0.0", port), library, token)
    log.info("Photo Frame server %s: %d photos from %s on port %d (%s)", __version__,
             len(library.snapshot.photos), photo_dir, port, "token required" if token else "NO TOKEN SET")

    def stop(*_args) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever()
    finally:
        scanner.stop()
        server.server_close()
