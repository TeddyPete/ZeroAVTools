"""Tiny QR code maker (byte mode, error correction M, versions 1-6), so the
screen can show a scannable link with no extra packages.

    m = qr.matrix("http://192.168.1.10:8080/")   # list of rows of bools
"""
from __future__ import annotations

# version: (total codewords, ec codewords per block, number of blocks), level M
_M = {1: (26, 10, 1), 2: (44, 16, 1), 3: (70, 26, 1), 4: (100, 18, 2), 5: (134, 24, 2), 6: (172, 16, 4)}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34]}

# GF(256) with polynomial 0x11D
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a: int, b: int) -> int:
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _rs_remainder(data: list[int], n: int) -> list[int]:
    gen = [1]
    for i in range(n):          # gen *= (x - a^i)
        nxt = [0] * (len(gen) + 1)
        for j, c in enumerate(gen):
            nxt[j] ^= c
            nxt[j + 1] ^= _mul(c, _EXP[i])
        gen = nxt
    rem = [0] * n
    for b in data:
        f = b ^ rem[0]
        rem = rem[1:] + [0]
        for j in range(n):
            rem[j] ^= _mul(gen[j + 1], f)
    return rem


def _codewords(text: bytes, ver: int) -> list[int]:
    total, ec, blocks = _M[ver]
    data_len = total - ec * blocks
    bits = [0, 1, 0, 0] + [int(b) for b in format(len(text), "08b")]
    for byte in text:
        bits += [int(b) for b in format(byte, "08b")]
    cap = data_len * 8
    bits += [0] * min(4, cap - len(bits))
    bits += [0] * (-len(bits) % 8)
    data = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(data) < data_len:
        data.append(pad)
        pad ^= 0xEC ^ 0x11
    size = data_len // blocks           # all blocks equal for these versions
    chunks = [data[i * size:(i + 1) * size] for i in range(blocks)]
    ecs = [_rs_remainder(c, ec) for c in chunks]
    out = []
    for i in range(size):
        out += [c[i] for c in chunks]
    for i in range(ec):
        out += [e[i] for e in ecs]
    return out


def _mask(m: int, x: int, y: int) -> bool:
    return [
        (x + y) % 2 == 0, y % 2 == 0, x % 3 == 0, (x + y) % 3 == 0,
        (x // 3 + y // 2) % 2 == 0, x * y % 2 + x * y % 3 == 0,
        (x * y % 2 + x * y % 3) % 2 == 0, ((x + y) % 2 + x * y % 3) % 2 == 0,
    ][m]


def _build(ver: int, cw: list[int], mask: int) -> list[list[bool]]:
    n = ver * 4 + 17
    mod = [[False] * n for _ in range(n)]
    fn = [[False] * n for _ in range(n)]

    def put(x, y, v):
        mod[y][x] = v
        fn[y][x] = True

    for i in range(n):                      # timing
        put(6, i, i % 2 == 0)
        put(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):   # finders + separators
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < n and 0 <= y < n:
                    d = max(abs(dx), abs(dy))
                    put(x, y, d not in (2, 4))
    al = _ALIGN[ver]
    for i, ay in enumerate(al):
        for j, ax in enumerate(al):
            if (i == 0 and j == 0) or (i == 0 and j == len(al) - 1) or (i == len(al) - 1 and j == 0):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    put(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
    # format info (level M = 0b00)
    data = mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    fbits = ((data << 10) | rem) ^ 0x5412
    bit = lambda i: (fbits >> i) & 1 == 1  # noqa: E731
    for i in range(6):
        put(8, i, bit(i))
    put(8, 7, bit(6))
    put(8, 8, bit(7))
    put(7, 8, bit(8))
    for i in range(9, 15):
        put(14 - i, 8, bit(i))
    for i in range(8):
        put(n - 1 - i, 8, bit(i))
    for i in range(8, 15):
        put(8, n - 15 + i, bit(i))
    put(8, n - 8, True)
    # data, zigzag from the bottom right
    i = 0
    right = n - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(n):
            for j in range(2):
                x = right - j
                up = ((right + 1) & 2) == 0
                y = n - 1 - vert if up else vert
                if not fn[y][x]:
                    v = i < len(cw) * 8 and (cw[i >> 3] >> (7 - (i & 7))) & 1 == 1
                    i += 1
                    mod[y][x] = v != _mask(mask, x, y)
        right -= 2
    return mod


def _penalty(m: list[list[bool]]) -> int:
    n, p = len(m), 0
    for grid in (m, [list(c) for c in zip(*m)]):
        for row in grid:
            run, prev = 0, None
            for v in row:
                if v == prev:
                    run += 1
                else:
                    if run >= 5:
                        p += run - 2
                    run, prev = 1, v
            if run >= 5:
                p += run - 2
            s = "".join("1" if v else "0" for v in row)
            p += 40 * (s.count("10111010000") + s.count("00001011101"))
    for y in range(n - 1):
        for x in range(n - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                p += 3
    dark = sum(v for row in m for v in row)
    p += abs(dark * 20 - n * n * 10) // (n * n) * 10
    return p


def matrix(text: str) -> list[list[bool]]:
    data = text.encode()
    for ver in sorted(_M):
        total, ec, blocks = _M[ver]
        if len(data) + 2 <= total - ec * blocks:
            break
    else:
        raise ValueError("text too long for this QR maker")
    cw = _codewords(data, ver)
    return min((_build(ver, cw, k) for k in range(8)), key=_penalty)


def draw(surf, text: str, x: int, y: int, size: int, fg=(0, 0, 0), bg=(255, 255, 255)) -> int:
    """Draw a QR code (with a 2-module quiet zone) into a pygame surface, fitting
    in a size x size square at (x, y). Returns the pixel size used."""
    m = matrix(text)
    n = len(m) + 4
    scale = max(1, size // n)
    px = scale * n
    surf.fill(bg, (x, y, px, px))
    for r, row in enumerate(m):
        for c, v in enumerate(row):
            if v:
                surf.fill(fg, (x + (c + 2) * scale, y + (r + 2) * scale, scale, scale))
    return px
