"""Read the display-oriented pixel size of a photo from its header.

Only the file's metadata is read; the image is never decoded, so this is fast
and light enough for a Raspberry Pi. Supports JPEG, PNG and HEIC/HEIF.
The size returned is the size as displayed: a JPEG stored sideways with an
EXIF orientation of 5-8, or a HEIC with a 90/270 degree rotation property,
is reported with width and height swapped.
"""
from __future__ import annotations

import struct
from typing import BinaryIO, Dict, List, Optional, Tuple


class ImageInfoError(Exception):
    """The file is not a readable photo of a supported type."""


def read_info(path: str) -> Tuple[int, int, str]:
    """Return (width, height, mime type) as displayed."""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
            f.seek(0)
            if head[:2] == b"\xff\xd8":
                w, h = _jpeg(f)
                return w, h, "image/jpeg"
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                w, h = _png(head, f)
                return w, h, "image/png"
            if head[4:8] == b"ftyp":
                w, h = _heif(f)
                return w, h, "image/heic"
    except (OSError, struct.error, IndexError, ValueError) as exc:
        raise ImageInfoError(f"unreadable ({exc.__class__.__name__})") from exc
    raise ImageInfoError("not a JPEG, PNG or HEIC file")


# --- JPEG -------------------------------------------------------------------

_SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _jpeg(f: BinaryIO) -> Tuple[int, int]:
    f.seek(2)
    orientation = 1
    for _ in range(512):  # segments; far more than any real file has before the frame header
        byte = f.read(1)
        while byte and byte != b"\xff":
            byte = f.read(1)
        marker = f.read(1)
        while marker == b"\xff":  # fill bytes
            marker = f.read(1)
        if not marker:
            break
        m = marker[0]
        if m in (0x01, 0xD8) or 0xD0 <= m <= 0xD7 or m == 0x00:
            continue
        if m in (0xD9, 0xDA):  # end of image / start of scan: no frame header found
            break
        length = struct.unpack(">H", f.read(2))[0]
        if length < 2:
            break
        if m == 0xE1 and length >= 8:  # APP1: maybe EXIF
            data = f.read(length - 2)
            if data[:6] == b"Exif\x00\x00":
                orientation = _exif_orientation(data[6:]) or orientation
            continue
        if m in _SOF_MARKERS:
            _precision, height, width = struct.unpack(">BHH", f.read(5))
            if width <= 0 or height <= 0:
                break
            return (height, width) if 5 <= orientation <= 8 else (width, height)
        f.seek(length - 2, 1)
    raise ValueError("no JPEG frame header")


def _exif_orientation(tiff: bytes) -> Optional[int]:
    if len(tiff) < 8:
        return None
    endian = "<" if tiff[:2] == b"II" else ">" if tiff[:2] == b"MM" else None
    if endian is None:
        return None
    offset = struct.unpack(endian + "I", tiff[4:8])[0]
    if offset + 2 > len(tiff):
        return None
    count = struct.unpack(endian + "H", tiff[offset:offset + 2])[0]
    for i in range(min(count, 64)):
        entry = tiff[offset + 2 + i * 12: offset + 14 + i * 12]
        if len(entry) < 12:
            return None
        tag, kind = struct.unpack(endian + "HH", entry[:4])
        if tag == 0x0112 and kind == 3:
            value = struct.unpack(endian + "H", entry[8:10])[0]
            return value if 1 <= value <= 8 else None
    return None


# --- PNG --------------------------------------------------------------------

def _png(head: bytes, f: BinaryIO) -> Tuple[int, int]:
    if len(head) < 16:
        raise ValueError("short PNG")
    f.seek(16)
    width, height = struct.unpack(">II", f.read(8))
    if width <= 0 or height <= 0:
        raise ValueError("bad PNG size")
    return width, height


# --- HEIC / HEIF (ISO base media file format) ---------------------------------

_MAX_META = 32 * 1024 * 1024


def _boxes(data: bytes, start: int = 0, end: Optional[int] = None):
    """Yield (type, payload_start, payload_end) for each box in data[start:end]."""
    end = len(data) if end is None else end
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack(">I4s", data[pos:pos + 8])
        header = 8
        if size == 1:
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            return
        yield kind, pos + header, pos + size
        pos += size


def _heif(f: BinaryIO) -> Tuple[int, int]:
    f.seek(0)
    while True:
        header = f.read(8)
        if len(header) < 8:
            break
        size, kind = struct.unpack(">I4s", header)
        header_len = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            header_len = 16
        if kind == b"meta":
            payload = size - header_len if size else 0
            if not 0 < payload <= _MAX_META:
                break
            return _heif_meta(f.read(payload))
        if size < header_len:
            break
        f.seek(size - header_len, 1)
    raise ValueError("no HEIF meta box")


def _heif_meta(meta: bytes) -> Tuple[int, int]:
    base = 4  # meta is a "full box": 4 bytes of version and flags precede its children
    primary: Optional[int] = None
    properties: List[Tuple[bytes, bytes]] = []
    associations: Dict[int, List[int]] = {}

    for kind, start, end in _boxes(meta, base):
        if kind == b"pitm":
            version = meta[start]
            primary = struct.unpack(">H" if version == 0 else ">I",
                                    meta[start + 4:start + (6 if version == 0 else 8)])[0]
        elif kind == b"iprp":
            for sub, s2, e2 in _boxes(meta, start, end):
                if sub == b"ipco":
                    properties = [(k, meta[s:e]) for k, s, e in _boxes(meta, s2, e2)]
                elif sub == b"ipma":
                    associations = _parse_ipma(meta[s2:e2])

    item = primary if primary in associations else next(iter(associations), None)
    if item is None:
        raise ValueError("no image item")
    width = height = None
    rotation = 0
    for index in associations[item]:
        if not 1 <= index <= len(properties):
            continue
        kind, body = properties[index - 1]
        if kind == b"ispe" and len(body) >= 12:
            width, height = struct.unpack(">II", body[4:12])
        elif kind == b"irot" and body:
            rotation = body[0] & 3
    if not width or not height:
        raise ValueError("no image size property")
    return (height, width) if rotation in (1, 3) else (width, height)


def _parse_ipma(body: bytes) -> Dict[int, List[int]]:
    version = body[0]
    flags = struct.unpack(">I", b"\x00" + body[1:4])[0]
    count = struct.unpack(">I", body[4:8])[0]
    pos = 8
    result: Dict[int, List[int]] = {}
    for _ in range(count):
        if version < 1:
            item = struct.unpack(">H", body[pos:pos + 2])[0]
            pos += 2
        else:
            item = struct.unpack(">I", body[pos:pos + 4])[0]
            pos += 4
        n = body[pos]
        pos += 1
        indices = []
        for _ in range(n):
            if flags & 1:
                value = struct.unpack(">H", body[pos:pos + 2])[0]
                pos += 2
                indices.append(value & 0x7FFF)
            else:
                indices.append(body[pos] & 0x7F)
                pos += 1
        result[item] = indices
    return result
