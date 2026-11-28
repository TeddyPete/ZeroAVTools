"""Keyboard input read straight from Linux evdev (no extra packages).

The CardputerZero's keyboard (tca8418) is a normal Linux input device. We
read key events from every keyboard-like /dev/input/event* device and turn
them into Key(name, char) with a US layout, Shift and Caps Lock.
"""
from __future__ import annotations

import glob
import os
import select
import struct
from dataclasses import dataclass

EV_KEY = 1
_FMT = "llHHi"  # struct input_event: timeval, type, code, value
_SZ = struct.calcsize(_FMT)

_CHARS: dict[int, tuple[str, str]] = {}
for _row, _start in (("1234567890", 2), ("qwertyuiop", 16), ("asdfghjkl", 30), ("zxcvbnm", 44)):
    for _i, _c in enumerate(_row):
        _CHARS[_start + _i] = (_c, _c.upper())
for _code, _pair in {2: "1!", 3: "2@", 4: "3#", 5: "4$", 6: "5%", 7: "6^", 8: "7&", 9: "8*",
                     10: "9(", 11: "0)", 12: "-_", 13: "=+", 26: "[{", 27: "]}", 39: ";:",
                     40: "'\"", 41: "`~", 43: "\\|", 51: ",<", 52: ".>", 53: "/?", 57: "  "}.items():
    _CHARS[_code] = (_pair[0], _pair[1])

# CardputerZero internal keyboard (tca8418c). Its driver switches layers
# itself: Fn (sends KEY_DVD) gives real arrows, F-keys, Delete, Home/End/PgUp/
# PgDn; Sym (sends KEY_CD) switches to a layer whose codes are keypad/Japanese
# keys standing in for the symbols printed on the keycaps. Map those to the
# printed symbol (device tree linux,keymap1, by key position).
SYM_LAYER = {55: "~", 69: "`", 70: "_", 71: "-", 72: "+", 73: "=", 74: "[", 75: "]", 76: "{", 77: "}",
             79: ";", 80: ":", 81: "'", 82: '"', 83: "<", 85: ">", 86: "\\", 89: "|",
             90: ",", 91: ".", 92: "/", 93: "?"}
FN_KEY, SYM_KEY = 389, 383  # KEY_DVD, KEY_CD: layer keys, never text

NAMED = {1: "esc", 14: "backspace", 15: "tab", 28: "enter", 96: "enter", 103: "up", 108: "down",
         105: "left", 106: "right", 111: "delete", 102: "home", 107: "end", 104: "pageup",
         109: "pagedown"}
SHIFT, CTRL, ALT, CAPS = {42, 54}, {29, 97}, {56, 100}, 58


@dataclass
class Key:
    name: str          # "enter", "up", "a", "A", "1" ...
    char: str = ""     # printable character, "" for control keys
    ctrl: bool = False


def _is_keyboard(event_path: str) -> bool:
    """True if the device reports letter keys (KEY_A..KEY_Z)."""
    n = os.path.basename(event_path)
    try:
        words = open(f"/sys/class/input/{n}/device/capabilities/key").read().split()
    except OSError:
        return False
    bits = 0
    for w in words:
        bits = (bits << 64) | int(w, 16)
    return all(bits >> code & 1 for code in (16, 30, 44))  # q, a, z


def _name(event_path: str) -> str:
    try:
        return open(f"/sys/class/input/{os.path.basename(event_path)}/device/name").read().strip()
    except OSError:
        return ""


class Keyboard:
    def __init__(self, devices: list[str] | None = None):
        paths = devices or [p for p in sorted(glob.glob("/dev/input/event*")) if _is_keyboard(p)]
        self.fds = []
        for p in paths:
            try:
                self.fds.append(os.open(p, os.O_RDONLY | os.O_NONBLOCK))
            except OSError:
                pass
        self.shift = self.ctrl = self.alt = False
        self.internal = any(_name(p).startswith("tca8418") for p in paths) or devices is not None
        self.caps = False

    @property
    def ok(self) -> bool:
        return bool(self.fds)

    def poll(self, timeout: float) -> list[Key]:
        if not self.fds:
            select.select([], [], [], timeout)
            return []
        r, _, _ = select.select(self.fds, [], [], timeout)
        keys: list[Key] = []
        for fd in r:
            try:
                data = os.read(fd, _SZ * 64)
            except BlockingIOError:
                continue
            except OSError:
                continue
            for off in range(0, len(data) - _SZ + 1, _SZ):
                _, _, etype, code, value = struct.unpack_from(_FMT, data, off)
                if etype == EV_KEY:
                    k = self._feed(code, value)
                    if k:
                        keys.append(k)
        return keys

    LONG_ESC = 0.8     # seconds: holding ESC this long exits the app (CardputerZero key spec)

    def _feed(self, code: int, value: int) -> Key | None:
        down = value != 0
        if code in NAMED and NAMED[code] == "esc":
            # short ESC = back; hold ESC = exit to HOME ("esc_long", sent once)
            import time
            now = time.monotonic()
            if value == 1:
                self._esc_t, self._esc_fired = now, False
            elif not getattr(self, "_esc_fired", True) and now - getattr(self, "_esc_t", now) >= self.LONG_ESC:
                self._esc_fired = True
                return Key("esc_long", ctrl=self.ctrl)
            elif value == 0:
                self._esc_fired = True
        if code in SHIFT:
            self.shift = down
            return None
        if code in CTRL:
            self.ctrl = down
            return None
        if code in ALT:
            self.alt = down
            return None
        if code == CAPS:
            if value == 1:
                self.caps = not self.caps
            return None
        if not down:
            return None
        if code in NAMED:
            # Holding ESC exits to the launcher's HOME; its auto-repeat must not
            # back out of several screens. OK/Enter doesn't repeat either.
            if value == 2 and NAMED[code] in ("esc", "enter"):
                return None
            return Key(NAMED[code], ctrl=self.ctrl)
        if code in (FN_KEY, SYM_KEY):
            return None
        if code in SYM_LAYER and self.internal:
            ch = SYM_LAYER[code]
            return Key(ch, ch, ctrl=self.ctrl)
        if code in _CHARS:
            lo, hi = _CHARS[code]
            up = self.shift
            if lo.isalpha() and self.caps:
                up = not up
            ch = hi if up else lo
            return Key(ch, ch, ctrl=self.ctrl)
        return None

    def close(self) -> None:
        for fd in self.fds:
            try:
                os.close(fd)
            except OSError:
                pass
        self.fds = []
