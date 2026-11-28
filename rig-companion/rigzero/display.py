"""Small-screen renderer and keyboard access without a desktop browser."""
from __future__ import annotations

import glob
import os
import select
import struct
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

BG, HEADER, PANEL = "#10191e", "#151f25", "#202e35"
INK, DIM, ACCENT, LINE, ALERT = "#f0f8f7", "#a8bfc3", "#65e2d2", "#3a5058", "#ffc875"
SELECT, SELECT_DIM = "#1f63e0", "#d6e4ff"  # bright blue highlight and its secondary text


class Display:
    def __init__(self, headless=False):
        pygame.font.init()
        self.surface = pygame.Surface((320, 170))
        path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        body = pygame.font.Font(path, 14)
        self.fonts = {12: body, 14: body,
                      17: pygame.font.Font(bold, 17), 23: pygame.font.Font(path, 23)}
        self.framebuffer = None
        self.previous = None
        if not headless:
            for folder in sorted(Path("/sys/class/graphics").glob("fb[0-9]*")):
                if (folder / "virtual_size").read_text().strip() == "320,170":
                    if (folder / "bits_per_pixel").read_text().strip() != "16":
                        raise RuntimeError("LCD must use RGB565")
                    self.stride = int((folder / "stride").read_text())
                    self.framebuffer = open("/dev/" + folder.name, "r+b", buffering=0)
                    break
            if self.framebuffer is None:
                raise RuntimeError("CardputerZero LCD framebuffer unavailable")

    def text(self, text, x, y, color=INK, size=14, width=None):
        font = self.fonts[size]
        text = str(text)
        if width is not None:
            original = text
            while text and font.size(text)[0] > width:
                if not original:
                    text = ""
                    break
                original = original[:-1]
                text = original + "…"
        self.surface.blit(font.render(text, True, color), (x, y))

    def rect(self, x, y, w, h, color=PANEL, radius=0):
        pygame.draw.rect(self.surface, color, (x, y, w, h), border_radius=radius)

    def header(self, title, status=""):
        self.surface.fill(BG)
        self.rect(0, 0, 320, 25, HEADER)
        title_width = 230
        if status:
            image = self.fonts[12].render(status, True, ACCENT)
            status_x = 312 - image.get_width()
            title_width = max(0, min(title_width, status_x - 17))
            self.surface.blit(image, (status_x, 5))
        if title_width:
            self.text(title, 9, 3, INK, 17, title_width)
        self.rect(8, 25, 304, 1, LINE)

    def footer(self, active=None, labels=None):
        self.rect(0, 146, 320, 24, HEADER)
        self.rect(0, 146, 320, 1, LINE)
        labels = labels or ["Radio", "APRS", "Inbox", "WX", "Link"]
        for i, (center, label) in enumerate(zip((49, 105, 161, 217, 273), labels)):
            color = INK if i == active else DIM
            if active == i:
                self.rect(center - 26, 147, 52, 23, SELECT)
                self.rect(center - 16, 168, 32, 2, INK)
            image = self.fonts[12].render(label, True, color)
            self.surface.blit(image, (center - image.get_width() // 2, 150))

    def wrap(self, text, x=10, y=43, width=300, height=98, color=INK, offset=0):
        lines = []
        for paragraph in str(text).split("\n"):
            line = ""
            for char in paragraph:
                if self.fonts[14].size(line + char)[0] > width:
                    cut = line.rfind(" ")
                    if cut > 0 and char != " ":
                        lines.append(line[:cut])
                        line = line[cut + 1:]
                    else:
                        lines.append(line)
                        line = ""
                    if char == " " and not line:
                        continue
                line += char
            lines.append(line)
        for row, line in enumerate(lines[offset:offset + height // 17]):
            self.text(line, x, y + row * 17, color)
        return len(lines)

    def flush(self):
        if not self.framebuffer:
            return
        import numpy as np
        pixels = pygame.surfarray.array3d(self.surface).swapaxes(0, 1).astype(np.uint16)
        rgb = ((pixels[:, :, 0] >> 3) << 11) | ((pixels[:, :, 1] >> 2) << 5) | (pixels[:, :, 2] >> 3)
        data = rgb.astype("<u2").tobytes()
        if self.stride != 640:
            data = b"".join(data[i * 640:(i + 1) * 640] + bytes(self.stride - 640) for i in range(170))
        if data != self.previous:
            self.framebuffer.seek(0)
            self.framebuffer.write(data)
            self.previous = data

    def save(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        pygame.image.save(self.surface, str(path))

    def close(self):
        if self.framebuffer:
            self.framebuffer.close()


class Keyboard:
    """Read tca8418c and USB keyboards; preserve the launcher's long Esc handling."""
    event_format = struct.Struct("llHHi")
    names = {1: "esc", 14: "backspace", 15: "tab", 28: "enter", 96: "enter",
             103: "up", 108: "down", 105: "left", 106: "right", 111: "delete"}
    symbols = {55: "~", 69: "`", 70: "_", 71: "-", 72: "+", 73: "=", 74: "[", 75: "]",
               76: "{", 77: "}", 79: ";", 80: ":", 81: "'", 82: '"', 83: "<", 85: ">",
               86: "\\", 89: "|", 90: ",", 91: ".", 92: "/", 93: "?"}

    # CardputerZero key spec: Esc short press = back one level (reported when
    # Esc is released), long press = exit to HOME (reported as "exit").
    ESC_HOLD_S = 1.0

    def __init__(self):
        self.fds = []
        self.esc_down = None
        self.shift = set()
        self.caps = False
        self.characters = {}
        for start, row in ((2, "1234567890"), (16, "qwertyuiop"), (30, "asdfghjkl"), (44, "zxcvbnm")):
            for n, char in enumerate(row):
                self.characters[start + n] = (char, char.upper())
        for code, pair in {2: "1!", 3: "2@", 4: "3#", 5: "4$", 6: "5%", 7: "6^", 8: "7&", 9: "8*",
                           10: "9(", 11: "0)", 12: "-_", 13: "=+", 26: "[{", 27: "]}", 39: ";:",
                           40: "'\"", 41: "`~", 43: "\\|", 51: ",<", 52: ".>", 53: "/?", 57: "  "}.items():
            self.characters[code] = pair
        for path in sorted(glob.glob("/dev/input/event*")):
            root = Path("/sys/class/input") / Path(path).name / "device"
            try:
                bits = int("".join((root / "capabilities/key").read_text().split()), 16)
                if all(bits & (1 << key) for key in (16, 30, 44)):
                    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                    self.fds.append((fd, (root / "name").read_text().startswith("tca8418")))
            except (OSError, ValueError):
                continue

    def poll(self, timeout=.05):
        if self.esc_down is not None:
            timeout = max(0, min(timeout, self.esc_down + self.ESC_HOLD_S - time.monotonic()))
        ready, _, _ = select.select([fd for fd, _ in self.fds], [], [], timeout)
        keys = []
        for fd, internal in list(self.fds):
            if fd not in ready:
                continue
            try:
                data = os.read(fd, self.event_format.size * 64)
            except BlockingIOError:
                continue
            except OSError:
                os.close(fd)
                self.fds.remove((fd, internal))
                self.shift = {entry for entry in self.shift if entry[0] != fd}
                continue
            for offset in range(0, len(data) - self.event_format.size + 1, self.event_format.size):
                _, _, kind, code, value = self.event_format.unpack_from(data, offset)
                if kind != 1:
                    continue
                if code in (42, 54):
                    if value:
                        self.shift.add((fd, code))
                    else:
                        self.shift.discard((fd, code))
                    continue
                if code == 1:
                    if value == 1:
                        self.esc_down = time.monotonic()
                    elif value == 0 and self.esc_down is not None:
                        self.esc_down = None
                        keys.append(("esc", ""))
                    continue
                if code == 58 and value == 1:
                    self.caps = not self.caps
                if not value or code in (389, 383):
                    continue
                if code in self.names:
                    if value != 2 or code not in (1, 28, 96):
                        keys.append((self.names[code], ""))
                elif internal and code in self.symbols:
                    keys.append(("text", self.symbols[code]))
                elif code in self.characters:
                    low, high = self.characters[code]
                    uppercase = bool(self.shift) ^ (self.caps and low.isalpha())
                    keys.append(("text", high if uppercase else low))
        if self.esc_down is not None and time.monotonic() - self.esc_down >= self.ESC_HOLD_S:
            self.esc_down = None
            keys.append(("exit", ""))
        return keys

    def close(self):
        for fd, _ in self.fds:
            os.close(fd)
