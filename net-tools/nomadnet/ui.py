"""Screens and drawing for the 320x170 LCD and the HDMI desktop window.

The same screen is drawn twice: small on the LCD (framebuffer) and large in
a desktop window on the Pi's HDMI desktop, which shows more lines and full
widths. Screens build a View (title, lines, hint, cursor) for a number of
rows; the renderer turns it into pixels.
"""
from __future__ import annotations

import glob
import os
import threading
import time
from dataclasses import dataclass, field

import pygame

from . import nomadui as T
from .keys import Key

# colors
WHITE = (235, 235, 235)
DIM = (140, 140, 150)
HEAD = (255, 214, 110)
OK = (110, 230, 120)
WARN = (255, 190, 60)
BAD = (255, 95, 95)
INFO = (130, 200, 255)
BAR = (18, 120, 52)       # green title bar (same as the shared Nomad UI)
SEL = (30, 90, 200)       # blue highlight: move with F/X
EDIT = (190, 28, 28)      # red highlight: typing into this field

MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONO_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"


def font(size: int, bold: bool = False) -> pygame.font.Font:
    path = MONO_BOLD if bold else MONO
    if os.path.exists(path):
        return pygame.font.Font(path, size)
    return pygame.font.Font(None, int(size * 1.35))


@dataclass
class Line:
    text: str
    color: tuple = WHITE
    payload: object = None   # set when the line can be picked with Enter


def as_line(x) -> Line:
    return x if isinstance(x, Line) else Line(str(x))


@dataclass
class View:
    title: str
    lines: list
    hint: str = ""
    cursor: int = -1          # index into lines that is highlighted, -1 none
    hscroll: int = 0
    edit: str | None = None   # an input line shown above the hint (terminal)
    editing: bool = False     # highlight / input line shown red: keys type
    qr: str | None = None     # draw this text as a QR code on the left
    keys: list | None = None  # labels for number keys 4..8 (keybar at the bottom)


# ---------------------------------------------------------------- screens

class Screen:
    title = ""
    hint = ""
    help = ""      # Fn+H text for this screen (the standard key list is added)
    steps = None   # nomadui.StepsPopup: setup steps shown when the screen opens

    def softkeys(self, app) -> list:
        """Actions on number keys 4..8: up to 5 (label, fn(app)) or None.
        Labels are drawn at the bottom of the LCD (official key spec)."""
        return []

    def keybar(self, app) -> list | None:
        sk = self.softkeys(app)
        if not sk or not any(sk):
            return None
        return [(s[0]() if callable(s[0]) else s[0]) if s else None for s in sk]

    def soft(self, k: Key, app) -> bool:
        """Run the action for number key 4..8; True when the key was used."""
        i = T.soft_index(k.char) if not k.name and not k.ctrl else None
        if i is None:
            return False
        sk = self.softkeys(app)
        if i < len(sk) and sk[i] and sk[i][1]:
            sk[i][1](app)
            return True
        return False

    def view(self, rows: int) -> View:
        return View(self.title, [], self.hint)

    def on_key(self, k: Key, app) -> None:
        if k.name == "esc":
            app.pop()

    def on_show(self, app) -> None:
        pass

    def close(self) -> None:
        pass


def window(lines: list, cursor: int, rows: int, top: int) -> tuple[int, int]:
    """Pick the first visible line so the cursor stays on screen."""
    if cursor >= 0:
        if cursor < top:
            top = cursor
        elif cursor >= top + rows:
            top = cursor - rows + 1
    top = max(0, min(top, max(0, len(lines) - rows)))
    return top, cursor


class Menu(Screen):
    """A list of (label, action). Action is a callable(app)."""

    def __init__(self, title: str, items: list, hint: str = "F/X move  OK open  Esc back",
                 softkeys: list | None = None, help: str = ""):
        self.title, self.items, self.hint = title, items, hint
        self.cur = 0
        self._soft = softkeys or []
        if help:
            self.help = help

    def softkeys(self, app) -> list:
        return self._soft

    def labels(self) -> list[str]:
        out = []
        for i, (label, _) in enumerate(self.items):
            label = label() if callable(label) else label
            if self._soft:              # keys 4..8 are actions: no item numbers
                out.append(label)
                continue
            n = f"{i + 1}" if i < 9 else " "
            out.append(f"{n} {label}")
        return out

    def view(self, rows: int) -> View:
        return View(self.title, [Line(t) for t in self.labels()], self.hint, self.cur)

    def on_key(self, k: Key, app) -> None:
        nav = k.nav
        if nav == "esc" or (nav == "left" and len(app.stack) > 1):
            app.pop()                       # Left never quits from the main menu
        elif nav == "up":
            self.cur = (self.cur - 1) % len(self.items)
        elif nav in ("down", "tab"):
            self.cur = (self.cur + 1) % len(self.items)
        elif nav in ("enter", "right"):
            self.items[self.cur][1](app)
        elif not self._soft and k.char.isdigit() and k.char != "0" and int(k.char) <= len(self.items):
            self.cur = int(k.char) - 1
            self.items[self.cur][1](app)


class HelpScreen(Screen):
    """Fn+H: help for the screen underneath, then the standard key list."""
    hint = "F/X scroll  Fn+H/Esc close"

    def __init__(self, under: Screen, app):
        self.title = f"Help: {under.title}" if under.title else "Help"
        self.lines = []
        st = getattr(under, "steps", None)
        if st is not None:
            self.lines += [Line(st.title, HEAD)] + [Line(t) for t in st.help_text().split("\n")] + [Line("")]
        if under.help:
            self.lines += [Line(t) for t in under.help.split("\n")] + [Line("")]
        bar = under.keybar(app) or []
        names = [b[0] if isinstance(b, (tuple, list)) else b for b in bar]
        soft = [f"{T.SOFT_KEYS[i]}  {n}" for i, n in enumerate(names) if n]
        if soft:
            self.lines += [Line("Number keys", HEAD)] + [Line(t) for t in soft] + [Line("")]
        self.lines += [Line(T.KEYS_HELP[0], HEAD)] + [Line(t) for t in T.KEYS_HELP[1:]]
        self.lines += [Line(""), Line("About", HEAD), Line(f"{T.ABOUT_KEY.upper()}  About this app"),
                       Line(f"Developed by {T.DEVELOPER}"), Line(T.WEBSITE)]
        self.top = 0

    def view(self, rows: int) -> View:
        self.top = max(0, min(self.top, len(self.lines) - rows))
        return View(self.title, self.lines[self.top:], self.hint)

    def on_key(self, k: Key, app) -> None:
        if (k.char or "").lower() == T.ABOUT_KEY:
            app.pop()
            app.push(AboutScreen())
        elif k.name in ("help", "esc", "enter", "bksp") or k.nav == "left":
            app.pop()
        elif k.up:
            self.top = max(0, self.top - 1)
        elif k.down:
            self.top += 1
        elif k.name == "pgdn":
            self.top += 5
        elif k.name == "pgup":
            self.top = max(0, self.top - 5)


@dataclass
class Field:
    key: str
    label: str
    value: str = ""
    secret: bool = False
    choices: list = field(default_factory=list)   # cycle with Left/Right


# Fields that only take certain characters (IPs: digits and dots).
_DIGITS = set("0123456789")
ALLOWED = {"ip": _DIGITS | {"."}, "port": _DIGITS, "lport": _DIGITS, "count": _DIGITS,
           "size": _DIGITS, "seconds": _DIGITS, "mb": _DIGITS, "extra": _DIGITS | {",", " "}}


class Form(Screen):
    """Fields on the keyboard, two modes (Nomad standard):
    move: F/X (or arrows) pick a field, OK/Right starts editing it, Esc/Left
          backs out. On a choice field OK/Right/Left cycle the choice.
          OK on [ OK ] runs.
    edit: (highlight turns red) keys type, OK keeps the text and returns to
          move mode, Esc puts the old text back."""

    def __init__(self, title: str, fields: list[Field], submit, note: str = ""):
        self.title, self.fields, self.submit, self.note = title, fields, submit, note
        self.cur = 0
        self.editing = False
        self.before = ""

    @property
    def hint(self) -> str:
        if self.editing:
            return "Type  OK done  Esc cancel"
        f = self.fields[self.cur] if self.cur < len(self.fields) else None
        if f is None:
            return "OK run  F/X move  Esc back"
        if f.choices:
            return "Z/C or OK change  F/X move  Esc"
        return "OK edit  F/X move  Esc back"

    def view(self, rows: int) -> View:
        lines = []
        for i, f in enumerate(self.fields):
            v = "\u2022" * len(f.value) if f.secret else f.value
            if f.choices:
                v = f"< {v} >"
            elif self.editing and i == self.cur:
                v += "_"
            lines.append(Line(f"{f.label}: {v}"))
        if self.note:
            lines.append(Line(self.note, DIM))
        lines.append(Line("[ OK ]", OK))
        cur = self.cur if self.cur < len(self.fields) else len(lines) - 1
        return View(self.title, lines, self.hint, cur, editing=self.editing)

    def values(self) -> dict:
        return {f.key: f.value.strip() if not f.secret else f.value for f in self.fields}

    def on_key(self, k: Key, app) -> None:
        f = self.fields[self.cur] if self.cur < len(self.fields) else None
        if self.editing:
            if k.name == "enter":
                self.editing = False
            elif k.name == "esc":
                f.value = self.before
                self.editing = False
            elif k.name == "bksp":
                f.value = f.value[:-1]
            elif k.name == "del":
                f.value = ""
            elif k.char and (f.key not in ALLOWED or k.char in ALLOWED[f.key]):
                f.value += k.char
            return
        nav = k.nav
        if nav == "esc":
            app.pop()
        elif nav == "up":
            self.cur = max(0, self.cur - 1)
        elif nav in ("down", "tab"):
            self.cur = min(len(self.fields), self.cur + 1)
        elif f is None:
            if nav in ("enter", "right"):
                self.submit(self.values(), app)
            elif nav == "left":
                app.pop()
        elif f.choices:
            if nav in ("left", "right", "enter") or k.char == " ":
                i = f.choices.index(f.value) if f.value in f.choices else 0
                f.value = f.choices[(i + (-1 if nav == "left" else 1)) % len(f.choices)]
        elif nav in ("enter", "right"):
            self.before = f.value
            self.editing = True
        elif nav == "left":
            app.pop()


class AboutScreen(Screen):
    """The standard About screen (nomadui.about_lines) with the website QR code."""
    title = "About"
    hint = "Scan for the website  Esc back"

    def view(self, rows: int) -> View:
        a = T.ABOUT    # short lines: the QR code takes the left half of the LCD
        lines = [Line(a["name"], HEAD), Line(f"v{a['version']}  {a['license']}", DIM),
                 Line("Developed by"), Line(T.DEVELOPER), Line(T.WEBSITE), Line(T.GITHUB)]
        return View(self.title, lines, self.hint, qr=T.WEBSITE_URL)

    def on_key(self, k: Key, app) -> None:
        if k.nav != "right":
            app.pop()


class Message(Screen):
    def __init__(self, title: str, lines: list, hint: str = "Esc back"):
        self.title, self.lines, self.hint = title, [as_line(x) for x in lines], hint
        self.top = 0

    def view(self, rows: int) -> View:
        return View(self.title, self.lines[self.top:], self.hint)

    def on_key(self, k: Key, app) -> None:
        if k.down:
            self.top = min(self.top + 1, max(0, len(self.lines) - 1))
        elif k.up:
            self.top = max(0, self.top - 1)
        elif k.nav != "right":
            app.pop()


# ---------------------------------------------------------------- tasks

class Output:
    """What a background task writes to. Thread safe."""

    def __init__(self):
        self.lines: list[Line] = []
        self.lock = threading.Lock()
        self.stop_flag = threading.Event()
        self.status = ""
        self.version = 0
        self.procs: list = []

    def line(self, text: str = "", color=WHITE, payload=None) -> None:
        with self.lock:
            for part in str(text).split("\n"):
                self.lines.append(Line(part.rstrip("\r"), color, payload))
            self.version += 1

    def head(self, text: str) -> None:
        self.line("-- " + text, HEAD)

    def ok(self, text: str) -> None:
        self.line(text, OK)

    def warn(self, text: str) -> None:
        self.line(text, WARN)

    def bad(self, text: str) -> None:
        self.line(text, BAD)

    def info(self, text: str) -> None:
        self.line(text, INFO)

    def dim(self, text: str) -> None:
        self.line(text, DIM)

    def item(self, text: str, payload, color=WHITE) -> None:
        """A pickable row. A "\n" in text keeps it one row that shows on two
        lines (one cursor step per device)."""
        with self.lock:
            self.lines.append(Line(str(text), color, payload))
            self.version += 1

    def set_status(self, text: str) -> None:
        with self.lock:
            self.status = text
            self.version += 1

    def replace_last(self, text: str, color=WHITE) -> None:
        with self.lock:
            if self.lines:
                self.lines[-1] = Line(text, color, self.lines[-1].payload)
            else:
                self.lines.append(Line(text, color))
            self.version += 1

    @property
    def stopped(self) -> bool:
        return self.stop_flag.is_set()

    def sleep(self, sec: float) -> bool:
        """Sleep; returns False when the user stopped the task."""
        return not self.stop_flag.wait(sec)

    def track(self, proc):
        self.procs.append(proc)
        return proc

    def stop(self) -> None:
        self.stop_flag.set()
        for p in self.procs:
            try:
                p.terminate()
            except Exception:
                pass

    def text(self) -> str:
        with self.lock:
            return "\n".join(l.text for l in self.lines)


class TaskScreen(Screen):
    """Runs fn(out, **kw) in a thread and shows what it writes.
    Lines with a payload can be picked with Enter (on_pick(app, payload))."""

    def __init__(self, title: str, fn, kw: dict | None = None, on_pick=None,
                 report: str | None = None, autostart: bool = True):
        self.title, self.fn, self.kw = title, fn, kw or {}
        self.on_pick, self.report = on_pick, report or title
        self.loading_text = ""      # label on the Loading popup (default: the title)
        self.out = Output()
        self.thread = None
        self.cur = -1        # -1 = follow the end
        self.hscroll = 0
        self.saved_msg = ""
        if autostart:
            self.start()

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.out = Output()
        self.cur = -1
        self.saved_msg = ""

        def run():
            try:
                self.fn(self.out, **self.kw)
            except Exception as e:  # show the error rather than dying
                import traceback
                self.out.bad(f"Error: {e}")
                for l in traceback.format_exc().splitlines()[-4:]:
                    self.out.dim(l)
            self.out.set_status("stopped" if self.out.stopped else "done")

        self.out.set_status("running")
        self.t0 = time.monotonic()
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    @property
    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def lcd_overlay(self):
        """The Loading popup while the task runs and has no results yet
        (only headings), so a slow scan never looks locked up."""
        if not self.running:
            return None
        with self.out.lock:
            real = [l for l in self.out.lines if l.text and not l.text.startswith("-- ")
                    and l.color not in (HEAD, DIM)]
        if real:
            return None
            heads = [l.text[3:] for l in self.out.lines if l.text.startswith("-- ")]
        t0 = getattr(self, "t0", None)
        label = self.loading_text or (heads[-1] if heads else self.title)
        return lambda surf: T.draw_loading(surf, label, t0)

    help = ("Results appear as they come in. F/X scroll, ',' and '/' scroll sideways. "
            "Blue lines can be opened with OK. Saved reports go in Site & reports.")

    def softkeys(self, app) -> list:
        return [(lambda: ("Stop", BAD) if self.running else "Again",
                 lambda app: self.out.stop() if self.running else self.start()),
                ("Save", self.save)]

    def save(self, app) -> None:
        from . import reports
        try:
            path = reports.save(app.cfg, self.report, self.out.text())
            self.saved_msg = "Saved " + os.path.basename(path)
        except OSError as e:
            self.saved_msg = f"Save failed: {e}"

    def view(self, rows: int) -> View:
        with self.out.lock:
            lines = list(self.out.lines)
            status = self.out.status
        cur = self.cur if 0 <= self.cur < len(lines) else len(lines) - 1
        picks = any(l.payload is not None for l in lines)
        state = "running... " if self.running else ""
        hint = self.saved_msg or (state + ("Enter pick  " if picks else "")
                                  + ("Q stop  " if self.running else "R rerun  ") + "S save  Esc back")
        title = self.title if not self.running else f"{self.title} *"
        if self.saved_msg:
            lines = lines + [Line(self.saved_msg, OK if self.saved_msg.startswith("Saved") else BAD)]
            cur = len(lines) - 1 if self.cur < 0 else cur
        return View(title, lines, hint, cur, self.hscroll)

    def on_key(self, k: Key, app) -> None:
        n = len(self.out.lines)
        cur = self.cur if 0 <= self.cur < n else n - 1
        if k.nav in ("esc", "left"):
            self.out.stop()
            app.pop()
        elif k.up:
            self.cur = max(0, cur - 1)
        elif k.down:
            self.cur = -1 if cur + 1 >= n - 1 else cur + 1
        elif k.name == "pgup":
            self.cur = max(0, cur - 8)
        elif k.name == "pgdn":
            self.cur = -1 if cur + 8 >= n - 1 else cur + 8
        elif k.name == "home":
            self.cur = 0
        elif k.name == "end":
            self.cur = -1
        elif k.char == ",":
            self.hscroll = max(0, self.hscroll - 10)
        elif k.char == "/":
            self.hscroll += 10
        elif k.char in ("q", "Q", " "):
            self.out.stop()
        elif k.char in ("r", "R") and not self.running:
            self.start()
        elif k.char in ("s", "S"):
            self.save(app)
        elif k.nav in ("enter", "right") and self.on_pick and 0 <= cur < n:
            p = self.out.lines[cur].payload
            if p is not None:
                self.on_pick(app, p)

    def close(self) -> None:
        self.out.stop()


# ---------------------------------------------------------------- drawing

def find_lcd_fb():
    override = (os.environ.get("NOMAD_LCD_FB") or os.environ.get("APPLAUNCH_LINUX_FBDEV_DEVICE")
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
        if (override and dev == override) or (not override and {w, h} == {320, 170}):
            return dev, w, h, bpp
    return None


class Painter:
    """Draws a View onto a surface of any size with a given font size."""

    def __init__(self, size: tuple[int, int], fsize: int, title_size: int, pad: int,
                 hint_size: int = 0, wrap: bool = False, bold: bool = False):
        self.size = size
        self.wrap = wrap
        self.f = font(fsize, bold)
        self.fb = font(title_size, bold=True)
        self.fh = font(hint_size or max(9, fsize - 1))
        self.lh = self.f.get_linesize()
        self.th = self.fb.get_linesize() + 4
        self.hh = self.fh.get_linesize() + 2
        self.pad = pad
        self.cw = self.f.size("M")[0]

    def rows(self, keys: bool = False) -> int:
        bottom = T.KEYBAR_H if keys and self.size == (T.W, T.H) else self.hh
        return max(1, (self.size[1] - self.th - bottom - 2) // self.lh)

    def cols(self) -> int:
        return max(10, (self.size[0] - 2 * self.pad) // self.cw)

    def draw(self, surf: pygame.Surface, v: View, site: str, top: int, wide: bool) -> None:
        W, H = surf.get_size()
        surf.fill((8, 10, 14))
        surf.fill(BAR, (0, 0, W, self.th))
        surf.blit(self.fb.render(v.title[: max(4, W // self.fb.size("M")[0] - (len(site) + 2 if site else 0))],
                                 True, (255, 255, 255)), (self.pad, 2))
        if site:
            img = self.fh.render(site, True, (255, 230, 200))
            surf.blit(img, (W - img.get_width() - self.pad, (self.th - img.get_height()) // 2))
        bar = bool(v.keys) and not wide and (W, H) == (T.W, T.H)
        hh = T.KEYBAR_H if bar else self.hh
        rows = self.rows(bar) - (1 if v.edit is not None else 0)
        lines = v.lines
        y = self.th + 1
        cols = self.cols()
        xoff = self.pad
        if v.qr:
            from . import qr
            px = qr.draw(surf, v.qr, 4, self.th + 3, H - self.th - hh - 6)
            xoff = px + 10
            cols = max(8, (W - xoff - self.pad) // self.cw)
        wrap = self.wrap and not wide and not v.hscroll

        def chunks(i):
            raw = as_line(lines[i]).text.replace("\t", "    ")
            if "\n" in raw:
                if wide:
                    raw = raw.replace("\n", "  ")
                else:
                    return [c for part in raw.split("\n") for c in one(part)]
            return one(raw)

        def one(text):
            if not wide and v.hscroll:
                text = text[v.hscroll:]
            if not wrap or len(text) <= cols:
                return [text[:cols + 1]]
            import textwrap
            ind = " " * min(len(text) - len(text.lstrip(" ")) + 2, cols // 3)
            return textwrap.wrap(text, cols, subsequent_indent=ind, break_on_hyphens=False) or [""]

        if wrap and 0 <= v.cursor < len(lines):
            # keep the selected line fully on screen when long lines wrap
            top = min(top, v.cursor)
            while top < v.cursor and sum(len(chunks(j)) for j in range(top, v.cursor + 1)) > rows:
                top += 1
        used = 0
        i = top
        while i < len(lines) and used < rows:
            ln = as_line(lines[i])
            color = ln.color
            if ln.payload is not None and color == WHITE:
                color = INFO
            for part in chunks(i)[: rows - used]:
                if i == v.cursor:
                    surf.fill(EDIT if v.editing else SEL, (0, y, W, self.lh))
                surf.blit(self.f.render(part or " ", True, color), (xoff, y))
                y += self.lh
                used += 1
            i += 1
        if v.edit is not None:
            ey = H - hh - self.lh - 1
            surf.fill(EDIT if v.editing else (40, 40, 55), (0, ey, W, self.lh))
            e = "> " + v.edit + ("_" if v.editing else "")
            surf.blit(self.f.render(e[-cols:], True, (255, 255, 255) if v.editing else (170, 170, 180)),
                      (self.pad, ey))
        if bar:
            T.draw_keybar(surf, v.keys, T.fonts())
        else:
            hint = v.hint
            if v.keys:      # desktop window: name the number keys in the hint
                names = [(b[0] if isinstance(b, (tuple, list)) else b) for b in v.keys]
                hint = "   ".join(f"{T.SOFT_KEYS[i]} {n}" for i, n in enumerate(names) if n) + "   |   " + hint
            surf.fill((22, 26, 36), (0, H - self.hh, W, self.hh))
            surf.blit(self.fh.render(hint, True, (170, 200, 255)), (self.pad, H - self.hh + 1))
        # scroll indicator
        if len(lines) > rows:
            bar_h = max(6, (H - self.th - hh) * rows // len(lines))
            span = H - self.th - hh - bar_h
            by = self.th + (span * top // max(1, len(lines) - rows))
            surf.fill((90, 90, 110), (W - 3, by, 3, bar_h))



def live_wayland_socket(rt: str, pref: str = "") -> str:
    """Name of a wayland-N socket in rt that a compositor is listening on."""
    import socket as _s
    try:
        names = [n for n in os.listdir(rt) if n.startswith("wayland-") and not n.endswith(".lock")]
    except OSError:
        return ""
    names.sort(key=lambda n: (n != pref, n))
    for n in names:
        c = _s.socket(_s.AF_UNIX, _s.SOCK_STREAM)
        c.settimeout(0.5)
        try:
            c.connect(os.path.join(rt, n))
            return n
        except OSError:
            pass
        finally:
            c.close()
    return ""

class Renderer:
    def __init__(self, want_window: bool = True):
        self.lcd_dev = None
        found = find_lcd_fb()
        if found:
            self.lcd_dev, self.fw, self.fh, self.bpp = found
        self.lcd = pygame.Surface((320, 170))
        # Big enough to read at arm's length: about 6 rows x 31 columns.
        self.lcd_p = Painter((320, 170), 17, 17, 4, hint_size=12, wrap=True, bold=True)
        self.win = None
        self.win_p = None
        self.last = None
        self.last_t = 0.0
        self.tops = {}
        if want_window:
            self.open_window()
        else:
            self._dummy_display()

    @staticmethod
    def _init_display() -> None:
        """Init SDL video. The desktop may have been restarted onto another
        socket (wayland-1...) leaving a stale wayland-0, and SDL can't retry
        in-process, so pick a socket that accepts a connection first."""
        if os.environ.get("SDL_VIDEODRIVER") == "wayland":
            sock = live_wayland_socket(os.environ.get("XDG_RUNTIME_DIR", ""),
                                       os.environ.get("WAYLAND_DISPLAY", ""))
            if not sock:
                raise pygame.error("no live Wayland desktop")
            os.environ["WAYLAND_DISPLAY"] = sock
        pygame.display.init()

    def open_window(self) -> None:
        try:
            if not pygame.display.get_init():
                self._init_display()
            if os.environ.get("SDL_VIDEODRIVER") in ("dummy", "offscreen"):
                return
            if pygame.display.get_driver() in ("dummy", "offscreen"):
                return
            # Keys come from evdev; keep Wayland text input (IME) out of it.
            pygame.key.stop_text_input()
            sizes = pygame.display.get_desktop_sizes() or [(1280, 720)]
            dw, dh = max(sizes, key=lambda s: s[0] * s[1])
            w, h = min(1280, dw - 40), min(760, dh - 80)
            self.win = pygame.display.set_mode((w, h), pygame.RESIZABLE)
            pygame.key.stop_text_input()
            pygame.display.set_caption("Nomad Net Tools")
            self.resize(w, h)
        except pygame.error as e:
            print(f"no desktop window: {e}", flush=True)
            self.win = None
            self._dummy_display()

    @staticmethod
    def _dummy_display() -> None:
        """LCD-only mode still needs an initialised display for the event
        queue, so fall back to SDL's dummy driver when the desktop is gone."""
        try:
            pygame.display.quit()
            os.environ["SDL_VIDEODRIVER"] = "dummy"
            pygame.display.init()
        except pygame.error as e:
            print(f"display init failed: {e}", flush=True)

    def resize(self, w: int, h: int) -> None:
        fs = 18 if h >= 600 else 15
        self.win_p = Painter((w, h), fs, fs + 6, 10)

    def window_rows(self) -> int:
        return self.win_p.rows() if self.win_p else 0

    def render(self, screen: Screen, site: str, force: bool = False, keys: list | None = None,
               popup=None) -> None:
        """popup: a nomadui.StepsPopup drawn over the LCD screen."""
        rows_l = self.lcd_p.rows(bool(keys))
        v_lcd = screen.view(rows_l)
        v_lcd.keys = keys
        over = getattr(screen, "lcd_overlay", lambda: None)()
        key = (id(screen), v_lcd.title, len(v_lcd.lines), v_lcd.hint, v_lcd.cursor, v_lcd.hscroll,
               v_lcd.edit, tuple((l.text, l.color) if isinstance(l, Line) else l for l in v_lcd.lines[-60:]),
               site, repr(keys), (id(popup), popup.top) if popup is not None else None,
               int(time.monotonic() * 8) if over else None)
        if key == self.last and not force:
            return
        now = time.monotonic()
        if not force and now - self.last_t < 0.08:
            return
        self.last, self.last_t = key, now
        top = self._top(("lcd", id(screen)), v_lcd, rows_l - (1 if v_lcd.edit is not None else 0))
        self.lcd_p.draw(self.lcd, v_lcd, site, top, wide=False)
        if over is not None:
            over(self.lcd)
        if popup is not None:
            popup.render(self.lcd)
        self._flush_lcd()
        if self.win is not None and self.win_p is not None:
            try:
                # Wayland can swap the window surface on resize: fetch it fresh
                surf = pygame.display.get_surface()
                if surf is None:
                    return
                if surf.get_size() != self.win_p.size:
                    self.resize(*surf.get_size())
                rows_w = self.win_p.rows() - (1 if v_lcd.edit is not None else 0)
                topw = self._top(("win", id(screen)), v_lcd, rows_w)
                self.win_p.draw(surf, v_lcd, site, topw, wide=True)
                pygame.display.flip()
            except pygame.error as e:
                print(f"desktop window closed: {e}", flush=True)
                self.win = None

    def _top(self, key, v: View, rows: int) -> int:
        top = self.tops.get(key, 0)
        if v.lines and v.cursor == len(v.lines) - 1:
            top = max(0, len(v.lines) - rows)   # follow the end
        top, _ = window(v.lines, v.cursor, rows, top)
        self.tops[key] = top
        return top

    def _flush_lcd(self) -> None:
        if not self.lcd_dev:
            return
        import numpy as np
        img = self.lcd
        if (self.fw, self.fh) == (170, 320):
            img = pygame.transform.rotate(img, 90)
        arr = pygame.surfarray.pixels3d(img).swapaxes(0, 1).astype(np.uint16)
        if self.bpp == 16:
            out = ((arr[..., 0] >> 3) << 11) | ((arr[..., 1] >> 2) << 5) | (arr[..., 2] >> 3)
            data = out.astype("<u2").tobytes()
        else:
            a = arr.astype(np.uint32)
            data = ((a[..., 0] << 16) | (a[..., 1] << 8) | a[..., 2]).astype("<u4").tobytes()
        try:
            with open(self.lcd_dev, "wb") as fb:
                fb.write(data)
        except OSError as e:
            print(f"LCD write failed ({e})", flush=True)
            self.lcd_dev = None

    def loading(self, text: str):
        """Context manager: the Loading popup over the current LCD picture
        while the main thread does something slow (connect, read a file)."""
        base = self.lcd.copy()

        def present(draw):
            draw(self.lcd)
            self._flush_lcd()
        self.last = None            # redraw normally afterwards
        return T.loading(present, text, background=lambda s: s.blit(base, (0, 0)))

    def save_lcd(self, path: str) -> None:
        pygame.image.save(self.lcd, path)
