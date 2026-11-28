"""Draws the app's screens on the CardputerZero's 320x170 LCD framebuffer.

Same approach as the other TNT apps: render with pygame into a surface and
write RGB565 pixels to the LCD's /dev/fbN. With no LCD (a desktop PC, or
tests) frames can be saved as PNG files instead.
"""
from __future__ import annotations

import glob
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import numpy as np  # noqa: E402
import pygame  # noqa: E402

PANEL_SIZES = {(320, 170), (170, 320)}
W, H = 320, 170

from . import tntui as T  # noqa: E402  (shared TNT look: green title, blue select, red edit)

BG, FG, DIM, OK, WARN, BAD = T.BG, T.FG, T.DIM, T.OK, T.WARN, T.BAD
BAR, SEL_BG, HINT = T.TITLE_BG, T.SEL_BG, T.HINT_FG


def find_lcd_fb():
    override = (os.environ.get("TNT_LCD_FB") or os.environ.get("APPLAUNCH_LINUX_FBDEV_DEVICE")
                or os.environ.get("LV_LINUX_FBDEV_DEVICE"))
    if override:
        override = os.path.realpath(override)
    for sysdir in sorted(glob.glob("/sys/class/graphics/fb*")):
        dev = "/dev/" + os.path.basename(sysdir)
        try:
            w, h = (int(v) for v in open(f"{sysdir}/virtual_size").read().strip().split(","))
            bpp = int(open(f"{sysdir}/bits_per_pixel").read().strip())
        except (OSError, ValueError):
            continue
        if (override and dev == override) or (not override and (w, h) in PANEL_SIZES):
            return dev, w, h, bpp
    return None


class Panel:
    TITLE_H, ROW_H, HINT_H, ROWS = T.TITLE_H, T.ROW_H, T.HINT_H, T.ROWS

    def __init__(self, headless: bool = False):
        pygame.font.init()
        found = None if headless else find_lcd_fb()
        self.dev = None
        if found:
            self.dev, self.fw, self.fh, self.bpp = found
        self.surf = pygame.Surface((W, H))
        self.fonts = T.fonts()
        self.f = self.fonts["row"]
        self._last = None

    def text_width(self, s: str) -> int:
        return self.f.size(s)[0]

    def draw(self, title: str, rows: list, hint: str = "", selected: int | None = None,
             right: str = "") -> None:
        """rows: tntui.RowView, str, (text, color) or (text, color, right_text)."""
        T.render(self.surf, title, rows, hint, selected, right, self.fonts)
        self.flush()

    def save(self, path: str) -> None:
        pygame.image.save(self.surf, path)

    def flush(self) -> None:
        if not self.dev:
            return
        img = self.surf
        if (self.fw, self.fh) == (170, 320):
            img = pygame.transform.rotate(img, 90)
        arr = pygame.surfarray.pixels3d(img).swapaxes(0, 1).astype(np.uint16)
        if self.bpp == 16:
            out = ((arr[..., 0] >> 3) << 11) | ((arr[..., 1] >> 2) << 5) | (arr[..., 2] >> 3)
            data = out.astype("<u2").tobytes()
        else:
            a = arr.astype(np.uint32)
            data = ((a[..., 0] << 16) | (a[..., 1] << 8) | a[..., 2]).astype("<u4").tobytes()
        if data == self._last:
            return
        try:
            with open(self.dev, "wb") as fb:
                fb.write(data)
            self._last = data
        except OSError:
            self.dev = None
