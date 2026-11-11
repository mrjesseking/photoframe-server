"""Finds photos in a folder and keeps an up-to-date catalog of them."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Dict, List, Optional

from .imageinfo import ImageInfoError, read_info

log = logging.getLogger("photoserver")

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
# Folders that NAS systems and operating systems create next to real photos.
IGNORED_DIR_NAMES = {"@eaDir", "#recycle", "$RECYCLE.BIN", "lost+found", "System Volume Information",
                     "@Recycle", ".@__thumb", "#snapshot"}
MAX_WARNINGS = 50


class Entry:
    __slots__ = ("id", "relpath", "size", "mtime_ns", "width", "height", "mime", "error")

    def __init__(self, relpath: str, size: int, mtime_ns: int,
                 width: int = 0, height: int = 0, mime: str = "", error: str = ""):
        self.id = hashlib.sha1(relpath.encode("utf-8", "surrogateescape")).hexdigest()[:16]
        self.relpath = relpath
        self.size = size
        self.mtime_ns = mtime_ns
        self.width = width
        self.height = height
        self.mime = mime
        self.error = error

    @property
    def revision(self) -> str:
        """Changes whenever the file's content (size or modification time) changes."""
        return hashlib.sha1(f"{self.size}:{self.mtime_ns}".encode()).hexdigest()[:10]


class Snapshot:
    """An immutable view of the library at one moment."""

    def __init__(self, entries: List[Entry], warnings: List[str]):
        good = sorted((e for e in entries if not e.error), key=lambda e: e.relpath)
        self.by_id: Dict[str, Entry] = {e.id: e for e in good}
        self.photos = [{"id": e.id, "width": e.width, "height": e.height, "revision": e.revision} for e in good]
        self.warnings = warnings[:MAX_WARNINGS]
        digest = hashlib.sha1()
        for p in self.photos:
            digest.update(f"{p['id']}:{p['revision']}:{p['width']}x{p['height']};".encode())
        self.revision = digest.hexdigest()[:16]


class Library:
    def __init__(self, root: str, data_dir: Optional[str] = None, settle_seconds: float = 10.0):
        self.root = os.path.realpath(root)
        self.settle_seconds = settle_seconds
        self._index_path = os.path.join(data_dir, "index.json") if data_dir else None
        self._cache: Dict[str, Entry] = self._load_index()
        self.snapshot = Snapshot([], [])
        self.last_scan = 0.0
        self.scan_seconds = 0.0

    # -- scanning -----------------------------------------------------------

    def scan(self) -> bool:
        """Re-read the folder. Returns True if the catalog changed."""
        started = time.time()
        found: Dict[str, Entry] = {}
        warnings: List[str] = []
        now_ns = time.time_ns()
        settle_ns = int(self.settle_seconds * 1e9)

        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in IGNORED_DIR_NAMES)
            for name in sorted(filenames):
                if name.startswith(".") or os.path.splitext(name)[1].lower() not in SUPPORTED_EXTENSIONS:
                    continue
                full = os.path.join(dirpath, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                rel = os.path.relpath(full, self.root)
                cached = self._cache.get(rel)
                if cached and cached.size == st.st_size and cached.mtime_ns == st.st_mtime_ns:
                    entry = cached
                elif now_ns - st.st_mtime_ns < settle_ns:
                    continue  # still being copied in; pick it up on a later scan
                else:
                    entry = self._inspect(rel, full, st)
                found[rel] = entry
                if entry.error:
                    warnings.append(f"Skipped {name}: {entry.error}")

        changed_files = set(found) != set(self._cache) or any(found[k] is not self._cache.get(k) for k in found)
        self._cache = found
        snapshot = Snapshot(list(found.values()), warnings)
        changed = snapshot.revision != self.snapshot.revision
        self.snapshot = snapshot
        self.last_scan = time.time()
        self.scan_seconds = self.last_scan - started
        if changed_files:
            self._save_index()
        if changed:
            log.info("Library changed: %d photos (revision %s, scan took %.2fs)",
                     len(snapshot.photos), snapshot.revision, self.scan_seconds)
        return changed

    @staticmethod
    def _inspect(rel: str, full: str, st: os.stat_result) -> Entry:
        try:
            width, height, mime = read_info(full)
            return Entry(rel, st.st_size, st.st_mtime_ns, width, height, mime)
        except ImageInfoError as exc:
            return Entry(rel, st.st_size, st.st_mtime_ns, error=str(exc))

    def full_path(self, entry: Entry) -> str:
        return os.path.join(self.root, entry.relpath)

    # -- persistence (so a restart on a Raspberry Pi doesn't re-read every file) ---

    def _load_index(self) -> Dict[str, Entry]:
        if not self._index_path or not os.path.exists(self._index_path):
            return {}
        try:
            with open(self._index_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if raw.get("version") != 1:
                return {}
            return {rel: Entry(rel, v["s"], v["m"], v["w"], v["h"], v["t"], v["e"])
                    for rel, v in raw["entries"].items()}
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("Ignoring unreadable index file")
            return {}

    def _save_index(self) -> None:
        if not self._index_path:
            return
        data = {"version": 1, "entries": {
            rel: {"s": e.size, "m": e.mtime_ns, "w": e.width, "h": e.height, "t": e.mime, "e": e.error}
            for rel, e in self._cache.items()}}
        tmp = self._index_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self._index_path)
        except OSError as exc:
            log.warning("Could not save index: %s", exc)


class Scanner(threading.Thread):
    """Rescans the library periodically until stopped."""

    def __init__(self, library: Library, interval: float):
        super().__init__(daemon=True, name="scanner")
        self.library = library
        self.interval = max(2.0, interval)
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.library.scan()
            except Exception:  # keep serving the last good catalog whatever happens
                log.exception("Scan failed")
            self._stop_event.wait(self.interval)

    def stop(self) -> None:
        self._stop_event.set()
