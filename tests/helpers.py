"""Builds tiny valid image files for tests."""
import struct
import zlib


def make_png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\x80\x80\x80" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def make_jpeg(width: int, height: int, orientation: int = 0, big_app_segment: int = 0) -> bytes:
    """A JPEG with a valid frame header (no real image data), optionally with EXIF orientation."""
    out = b"\xff\xd8"
    if big_app_segment:  # e.g. an ICC profile or thumbnail before the frame header
        out += b"\xff\xe2" + struct.pack(">H", big_app_segment + 2) + b"\x00" * big_app_segment
    if orientation:
        tiff = b"MM\x00\x2a\x00\x00\x00\x08" + struct.pack(">H", 1) + struct.pack(">HHIHH", 0x0112, 3, 1, orientation, 0) + b"\x00" * 4
        app1 = b"Exif\x00\x00" + tiff
        out += b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1
    sof = struct.pack(">BHHB", 8, height, width, 1) + b"\x01\x11\x00"
    out += b"\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof
    out += b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00" + b"\x00" * 8 + b"\xff\xd9"
    return out


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def make_heic(width: int, height: int, rotation: int = 0, item_id: int = 1, other_first: bool = True) -> bytes:
    """A HEIC with just enough structure (ftyp, meta with pitm/iprp) to describe its size.

    `rotation` is the irot value (quarter turns anticlockwise). With `other_first` the
    primary image is item 2 and a small thumbnail item 1 comes first, as in real files.
    """
    ispe_big = _box(b"ispe", b"\x00\x00\x00\x00" + struct.pack(">II", width, height))
    ispe_thumb = _box(b"ispe", b"\x00\x00\x00\x00" + struct.pack(">II", 160, 120))
    irot = _box(b"irot", bytes([rotation]))
    ipco = _box(b"ipco", ispe_thumb + ispe_big + irot)           # property indices: 1=thumb, 2=big, 3=rotation
    entries = b""
    primary = item_id
    if other_first:
        entries += struct.pack(">HB", 1, 1) + b"\x01"           # item 1 -> thumb only
        primary = 2
    assoc = bytes([2]) + (bytes([3]) if rotation else b"")
    entries += struct.pack(">HB", primary, 2 if rotation else 1) + assoc
    ipma = _box(b"ipma", b"\x00\x00\x00\x00" + struct.pack(">I", 2 if other_first else 1) + entries)
    iprp = _box(b"iprp", ipco + ipma)
    pitm = _box(b"pitm", b"\x00\x00\x00\x00" + struct.pack(">H", primary))
    meta = _box(b"meta", b"\x00\x00\x00\x00" + pitm + iprp)
    ftyp = _box(b"ftyp", b"heic\x00\x00\x00\x00mif1heic")
    return ftyp + meta + _box(b"mdat", b"\x00" * 32)
