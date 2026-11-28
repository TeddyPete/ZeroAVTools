"""Screenshot of the built-in LCD as a PNG, using only the standard library
(no pygame, so the server stays small in memory)."""
from __future__ import annotations

import glob
import os
import struct
import zlib

PANEL_SIZES = {(320, 170), (170, 320)}


def find_lcd():
    for sysdir in sorted(glob.glob("/sys/class/graphics/fb*")):
        try:
            w, h = (int(v) for v in open(f"{sysdir}/virtual_size").read().strip().split(","))
            bpp = int(open(f"{sysdir}/bits_per_pixel").read().strip())
            stride = int(open(f"{sysdir}/stride").read().strip()) if os.path.exists(f"{sysdir}/stride") else w * bpp // 8
        except (OSError, ValueError):
            continue
        if (w, h) in PANEL_SIZES:
            return "/dev/" + os.path.basename(sysdir), w, h, bpp, stride
    return None


def png(width: int, height: int, rgb_rows: list[bytes]) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + r for r in rgb_rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _lut565():
    r = bytes(((v >> 11) & 31) * 255 // 31 for v in range(65536))
    g = bytes(((v >> 5) & 63) * 255 // 63 for v in range(65536))
    b = bytes((v & 31) * 255 // 31 for v in range(65536))
    return r, g, b


_LUT = None


def lcd_png() -> bytes | None:
    global _LUT
    found = find_lcd()
    if not found:
        return None
    dev, w, h, bpp, stride = found
    try:
        with open(dev, "rb") as f:
            data = f.read(stride * h)
    except OSError:
        return None
    rows = []
    if bpp == 16:
        if _LUT is None:
            _LUT = _lut565()
        lr, lg, lb = _LUT
        for y in range(h):
            px = struct.unpack_from(f"<{w}H", data, y * stride)
            row = bytearray(w * 3)
            row[0::3] = bytes(lr[v] for v in px)
            row[1::3] = bytes(lg[v] for v in px)
            row[2::3] = bytes(lb[v] for v in px)
            rows.append(bytes(row))
    else:
        for y in range(h):
            seg = data[y * stride:y * stride + w * 4]
            row = bytearray(w * 3)
            row[0::3], row[1::3], row[2::3] = seg[2::4], seg[1::4], seg[0::4]
            rows.append(bytes(row))
    if (w, h) == (170, 320):   # portrait buffer: rotate to landscape
        # the apps draw landscape and rotate 90 degrees left before writing,
        # so landscape (X, Y) is buffer row h-1-X, column Y
        rows = [b"".join(rows[h - 1 - x][y * 3:y * 3 + 3] for x in range(h)) for y in range(w)]
        w, h = h, w
    return png(w, h, rows)
