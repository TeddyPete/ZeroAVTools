"""TNT UI standard for CardputerZero apps (one file, copy it into any TNT app).

What every TNT app does on the 320x170 screen:

  * Title bar is green, the highlighted row is bright blue with white text, a
    field being edited is red. Rows that open something end in a ">", On/Off
    switches are a green / grey pill. Number keys 4..8 get a label bar at the bottom.
  * Menus:   Up/Down move (plain F / X work too, no Fn needed).
             OK (Enter) or Right selects.  Left or ESC goes back one level;
             ESC on the main page quits.  (Plain Z / C = Left / Right.)
  * Fields:  OK or Right on a text field starts editing it right there and the
             row turns red.  While editing every key types (F/X/Z/C are just
             letters), Fn+Left/Right move the cursor, DEL deletes, OK saves and
             goes back to the menu, ESC cancels the edit.
             On On/Off and choice fields OK or Right just flips the value.

The only dependency is pygame, and only for render(). Key handling works
with any key object that has .name and .char (and optionally .ctrl), where
.name is "up", "down", "left", "right", "enter", "esc", "backspace",
"delete", "home", "end", "tab", "pageup", "pagedown" or the typed character.

Use it like this:

    form = EditList([Field("ssid", "Name"), Field("pw", "Password", "secret"),
                     Field("save", "Save", "action")], {"ssid": "", "pw": ""})
    ev = form.handle(key)        # "back", "action:save", "changed:ssid" or None
    tntui.render(surface, fonts, "Hotspot", form.view(6), form.hint())
"""
from __future__ import annotations

from dataclasses import dataclass, field as _field
from typing import Any, Callable

# ---- colors (RGB) -----------------------------------------------------------
# No published CardputerZero/APPLaunch color standard exists, so this is the
# TNT scheme Ted picked: green title, blue highlight, red while editing.
BG = (8, 10, 16)
FG = (235, 235, 235)
DIM = (145, 155, 170)
TITLE_BG = (18, 120, 52)      # green title bar
TITLE_FG = (255, 255, 255)
TITLE_RIGHT = (210, 255, 215)
SEL_BG = (31, 99, 224)        # bright blue highlight (as Rig Companion); text on it is white
SEL_FG = (255, 255, 255)
CHEVRON = (120, 130, 150)     # ">" at the right end of rows that open something
PILL_ON = (22, 150, 72)       # On/Off switches: green pill / grey pill, white text
PILL_OFF = (78, 86, 104)
EDIT_BG = (190, 28, 28)       # red while editing
EDIT_FIELD = (90, 8, 8)       # darker red box behind the text being typed
OK = (110, 220, 120)
WARN = (255, 200, 90)
BAD = (255, 110, 100)
HINT_BG = (24, 28, 40)
HINT_FG = (150, 200, 255)

# ---- layout (320x170) -----------------------------------------------------------
W, H = 320, 170
TITLE_H, ROW_H, HINT_H = 26, 20, 19
ROWS = (H - TITLE_H - HINT_H - 2) // ROW_H     # 6 rows of 17 px bold text
FONT_PATHS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
FONT_SIZES = {"title": 19, "row": 17, "small": 14, "tiny": 12}

# ---- keys -----------------------------------------------------------------------
UP, DOWN = {"up"}, {"down"}
SELECT = {"enter", "right"}
BACK = {"esc", "left"}
# Plain letters printed under the arrows on the CardputerZero keycaps.
NAV_LETTERS = {"f": "up", "x": "down", "z": "left", "c": "right"}

HINT_MENU = "↑↓ move  OK/→ open  ←/ESC back"
HINT_FORM = "↑↓ move  OK/→ edit  ESC back"
HINT_EDIT = "OK save  ESC cancel"

# ---- cyber theme (Nomad AI Bridge) ----------------------------------------------
# Near-black with faint scanlines, monospace, neon cyan lines, magenta accent.
CYBER = False
CYAN = (0, 234, 255)
CYAN_D = (0, 92, 110)
MAG = (255, 46, 160)
LIME = (120, 255, 150)


def use_cyber() -> None:
    """Switch every screen to the cyber look. Call once at start-up, before fonts()."""
    global CYBER, BG, FG, DIM, TITLE_BG, TITLE_FG, TITLE_RIGHT, SEL_BG, EDIT_BG, EDIT_FIELD
    global OK, BAD, HINT_BG, HINT_FG, CHEVRON, KEYBAR_BG, SOFT_BG, SOFT_FG, SOFT_LINE, FONT_PATHS, FONT_SIZES
    CYBER = True
    BG, FG, DIM = (4, 6, 12), (226, 246, 255), (110, 150, 170)
    TITLE_BG, TITLE_FG, TITLE_RIGHT = (4, 6, 12), CYAN, LIME
    SEL_BG = (0, 140, 170)
    EDIT_BG, EDIT_FIELD = (175, 18, 70), (80, 6, 34)
    OK, BAD = LIME, (255, 70, 110)
    HINT_BG, HINT_FG = (6, 14, 20), CYAN
    CHEVRON = CYAN_D
    KEYBAR_BG, SOFT_BG, SOFT_FG, SOFT_LINE = (4, 6, 12), (6, 22, 30), CYAN, CYAN
    FONT_PATHS = ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    FONT_SIZES = {"title": 16, "row": 16, "small": 13, "tiny": 12}
    _fonts.clear()


def glow_line(surf, x1: int, y: int, x2: int, color) -> None:
    surf.fill(tuple(c // 3 for c in color), (x1, y - 1, x2 - x1, 3))
    surf.fill(color, (x1, y, x2 - x1, 1))


def corners(surf, rect, color, n: int = 6) -> None:
    """Bracket corners around rect (cyber frames)."""
    x, y, w, h = rect
    for cx, cy, dx, dy in ((x, y, 1, 1), (x + w - 1, y, -1, 1), (x, y + h - 1, 1, -1), (x + w - 1, y + h - 1, -1, -1)):
        surf.fill(color, (min(cx, cx + dx * (n - 1)), cy, n, 1))
        surf.fill(color, (cx, min(cy, cy + dy * (n - 1)), 1, n))


def code_box(surf, text: str, font, cy: int) -> None:
    """The big one-time code, centred at height cy."""
    import pygame
    img = font.render(text, True, CYAN if CYBER else (255, 255, 255))
    x = (W - img.get_width()) // 2
    y = cy - img.get_height() // 2
    r = pygame.Rect(x - 12, y - 4, img.get_width() + 24, img.get_height() + 8)
    if CYBER:
        r = pygame.Rect(10, r.y, W - 20, r.h)
        surf.fill((0, 30, 40), r)
        corners(surf, r, CYAN, 10)
        glow = font.render(text, True, CYAN_D)
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            surf.blit(glow, (x + dx, y + dy))
    else:
        pygame.draw.rect(surf, SEL_BG, r, border_radius=6)
    surf.blit(img, (x, y))


# ---- number keys 4..8: label bar at the bottom (official CardputerZero key spec,
# cardputer.cc/#/documents/cp0-keys), same look as the other Nomad apps ----------
SOFT_KEYS = "45678"
SOFT_X = (49, 105, 161, 217, 273)   # recommended icon centres
SOFT_SIZE = 22
SOFT_W = 52
KEYBAR_H = 30                        # bottom strip y 140..170
ROWS_KEYS = (H - TITLE_H - KEYBAR_H - 2) // ROW_H   # 5 rows above a keybar
KEYBAR_BG = (14, 17, 26)
SOFT_BG = (36, 48, 76)
SOFT_FG = (235, 240, 255)
SOFT_LINE = (150, 200, 255)


def soft_index(char: str) -> int | None:
    """0..4 for number keys 4..8, else None."""
    return SOFT_KEYS.index(char) if char and len(char) == 1 and char in SOFT_KEYS else None


def nav_name(name: str, char: str = "", ctrl: bool = False) -> str:
    """Key name for menus: plain F/X/Z/C become up/down/left/right."""
    if char and not ctrl and char.lower() in NAV_LETTERS:
        return NAV_LETTERS[char.lower()]
    return name


def _k(key) -> tuple[str, str, bool]:
    return key.name, getattr(key, "char", "") or "", bool(getattr(key, "ctrl", False))


# ---- editable list ------------------------------------------------------------------

@dataclass
class Field:
    """One row. kind: text | ip | mask | secret | password | number | bool | choice | header | action.
    password is like secret but stays hidden while typing too."""
    key: str
    label: str
    kind: str = "text"
    choices: list | tuple = ()          # choice: values; bool: (on_name, off_name)
    help: str = ""                      # shown in the hint bar while editing
    visible: Callable[[dict], bool] | None = None
    maxlen: int = 64
    color: Any = None                   # label color override

    @property
    def editable(self) -> bool:
        return self.kind in ("text", "ip", "mask", "secret", "password", "number")

    @property
    def selectable(self) -> bool:
        return self.kind != "header"


ALLOWED = {"ip": set("0123456789."), "mask": set("0123456789./"), "number": set("0123456789")}


@dataclass
class RowView:
    label: str
    value: str = ""
    state: str = "normal"       # normal | selected | editing | header
    color: Any = None
    cursor: int | None = None   # cursor position in value while editing
    chevron: bool = False       # draw ">" at the right: this row opens something
    pill: bool | None = None    # On/Off switch: True green "On", False grey "Off"
    icon: Any = None            # (text, color): small badge at the left


@dataclass
class EditList:
    fields: list
    values: dict
    sel: int = 0
    top: int = 0
    editing: bool = False
    buf: str = ""
    pos: int = 0
    orig: dict = _field(default_factory=dict)

    def __post_init__(self):
        self.values = dict(self.values)
        self.orig = dict(self.values)
        items = self.shown()
        self.sel = next((i for i, f in enumerate(items) if f.selectable), 0)

    # -- state --
    def shown(self) -> list:
        return [f for f in self.fields if f.visible is None or f.visible(self.values)]

    @property
    def current(self) -> Field | None:
        items = self.shown()
        if not items:
            return None
        self.sel = max(0, min(self.sel, len(items) - 1))
        return items[self.sel]

    @property
    def changed(self) -> bool:
        return self.values != self.orig

    def select(self, key: str) -> None:
        for i, f in enumerate(self.shown()):
            if f.key == key:
                self.sel = i

    def start_edit(self) -> None:
        f = self.current
        if f and f.editable:
            self.editing = True
            self.buf = str(self.values.get(f.key, ""))
            self.pos = len(self.buf)

    def _move(self, d: int) -> None:
        items = self.shown()
        for _ in range(len(items)):
            self.sel = (self.sel + d) % len(items)
            if items[self.sel].selectable:
                return

    # -- keys --
    def handle(self, key) -> str | None:
        """Feed one key. Returns "back", "action:<key>", "changed:<key>",
        "edit:<key>", "cancel:<key>" or None."""
        name, char, ctrl = _k(key)
        f = self.current
        if f is None:
            return "back" if name in BACK else None
        if self.editing:
            return self._edit_key(f, name, char, ctrl)
        name = nav_name(name, char, ctrl)
        if name in UP:
            self._move(-1)
        elif name in DOWN:
            self._move(1)
        elif name == "pageup":
            for _ in range(ROWS):
                self._move(-1)
        elif name == "pagedown":
            for _ in range(ROWS):
                self._move(1)
        elif name in SELECT or (char == " " and f.kind in ("bool", "choice")):
            if f.kind == "action":
                return f"action:{f.key}"
            if f.kind == "bool":
                self.values[f.key] = not self.values.get(f.key)
                return f"changed:{f.key}"
            if f.kind == "choice":
                ch = [str(c) for c in f.choices]
                cur = str(self.values.get(f.key))
                self.values[f.key] = ch[(ch.index(cur) + 1) % len(ch)] if cur in ch else ch[0]
                return f"changed:{f.key}"
            if f.editable:
                self.start_edit()
                return f"edit:{f.key}"
        elif name in BACK:
            return "back"
        return None

    def _edit_key(self, f: Field, name: str, char: str, ctrl: bool) -> str | None:
        if name == "enter":
            v = self.buf if f.kind in ("secret", "password") else self.buf.strip()
            self.editing = False
            self.values[f.key] = v
            return f"changed:{f.key}"
        if name == "esc":
            self.editing = False
            return f"cancel:{f.key}"
        if name == "left":
            self.pos = max(0, self.pos - 1)
        elif name == "right":
            self.pos = min(len(self.buf), self.pos + 1)
        elif name == "home":
            self.pos = 0
        elif name == "end":
            self.pos = len(self.buf)
        elif name == "backspace":
            if self.pos:
                self.buf = self.buf[:self.pos - 1] + self.buf[self.pos:]
                self.pos -= 1
        elif name == "delete":
            self.buf = self.buf[:self.pos] + self.buf[self.pos + 1:]
        elif ctrl and char.lower() == "u":
            self.buf, self.pos = "", 0
        elif char and not ctrl and len(self.buf) < f.maxlen:
            if f.kind in ALLOWED and char not in ALLOWED[f.kind]:
                return None
            self.buf = self.buf[:self.pos] + char + self.buf[self.pos:]
            self.pos += 1
        return None

    # -- drawing --
    def fmt(self, f: Field) -> str:
        v = self.values.get(f.key)
        if f.kind == "bool":
            names = tuple(f.choices) if f.choices else ("On", "Off")
            return names[0] if v else names[1]
        if f.kind in ("header", "action"):
            return ""
        if f.kind in ("secret", "password"):
            return "•" * len(str(v or "")) if v else "(none)"
        return str(v if v is not None else "")

    def view(self, n: int = ROWS) -> list[RowView]:
        items = self.shown()
        self.current  # clamps sel
        if self.sel < self.top:
            self.top = self.sel
        if self.sel >= self.top + n:
            self.top = self.sel - n + 1
        out = []
        for i, f in enumerate(items[self.top:self.top + n], start=self.top):
            if f.kind == "header":
                out.append(RowView(f.label, state="header", color=f.color or WARN))
                continue
            label = f.label
            color = f.color or FG
            if i == self.sel and self.editing:
                shown = "•" * len(self.buf) if f.kind == "password" else self.buf
                out.append(RowView(label, shown, "editing", color, self.pos))
            else:
                st = "selected" if i == self.sel else "normal"
                if f.kind == "bool" and not f.choices:
                    out.append(RowView(label, "", st, color, pill=bool(self.values.get(f.key))))
                else:
                    out.append(RowView(label, self.fmt(f), st, color, chevron=f.kind == "action"))
        return out

    def hint(self) -> str:
        if self.editing:
            f = self.current
            extra = f"  {f.help}" if f and f.help and len(f.help) <= 20 else ""
            return HINT_EDIT + extra
        f = self.current
        if f and f.kind in ("bool", "choice"):
            return "↑↓ move  OK/→ change  ESC back"
        if f and f.kind == "action":
            return HINT_MENU
        return HINT_FORM


# ---- rendering (pygame) ---------------------------------------------------------

_fonts: dict = {}


def fonts() -> dict:
    """{"title", "row", "small"} pygame fonts, DejaVu Sans Bold when present."""
    if not _fonts:
        import os
        import pygame
        pygame.font.init()
        path = next((p for p in FONT_PATHS if os.path.exists(p)), None)
        for name, size in FONT_SIZES.items():
            _fonts[name] = pygame.font.Font(path, size) if path else pygame.font.Font(None, int(size * 1.35))
    return _fonts


def as_view(row, selected: bool) -> RowView:
    """Accept RowView, "text", (text, color) or (text, color, right_text)."""
    if isinstance(row, RowView):
        return row
    if isinstance(row, str):
        row = (row, FG)
    return RowView(row[0], row[2] if len(row) > 2 else "", "selected" if selected else "normal", row[1])


REMOTE_ON = (255, 150, 30)     # orange dot: TNT MCP Link is on (Claude can reach the device)
REMOTE_ROOT = (255, 60, 60)    # red dot: ... and Admin (sudo) is allowed


def remote_state() -> str:
    """"", "on" or "root": read from the flag file the TNT MCP Link server keeps."""
    import os
    run = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    try:
        state = ""
        for name in ("tnt-remote", "nomad-ai-bridge", "nomad-mcp"):   # TNT MCP Link, Nomad AI Bridge (was Nomad MCP)
            try:
                with open(os.path.join(run, name, "active")) as fh:
                    state = "root" if "root" in fh.read() else (state or "on")
            except OSError:
                pass
        return state
    except OSError:
        return ""


def render(surf, title: str, rows: list, hint: str = "", selected: int | None = None,
           right: str = "", f: dict | None = None, keys: list | None = None) -> None:
    """Draw a whole TNT screen: green title, up to ROWS rows, hint bar.
    While TNT MCP Link is on, every TNT app shows an orange dot (red with sudo)
    at the right end of the title bar."""
    import pygame
    f = f or fonts()
    surf.fill(BG)
    if CYBER:
        for sy in range(0, H, 3):                  # faint scanlines
            surf.fill((8, 12, 20), (0, sy, W, 1))
        surf.blit(f["title"].render("\u258c" + title.upper(), True, TITLE_FG), (3, 3), (0, 0, W - 10, TITLE_H))
        glow_line(surf, 0, TITLE_H - 1, W, CYAN)
        surf.fill(MAG, (0, TITLE_H - 2, 40, 3))
    else:
        surf.fill(TITLE_BG, (0, 0, W, TITLE_H))
        surf.blit(f["title"].render(title, True, TITLE_FG), (6, 2), (0, 0, W - 12, TITLE_H))
    rs = remote_state()
    dot = 16 if rs else 0
    if right:
        rc = TITLE_RIGHT
        if CYBER:
            right = "● " + right
            rc = MAG if right.endswith("ROOT") else DIM if right.endswith("OFF") else LIME
        img = f["small"].render(right, True, rc)
        if CYBER:
            surf.fill(BG, (W - img.get_width() - 12 - dot, 0, img.get_width() + 12 + dot, TITLE_H - 2))
            surf.blit(img, (W - img.get_width() - 6 - dot, 6))
            img = None
        if img is not None:
            surf.fill(TITLE_BG, (W - img.get_width() - 12 - dot, 0, img.get_width() + 12 + dot, TITLE_H))
            surf.blit(img, (W - img.get_width() - 6 - dot, 5))
    if rs:
        surf.fill(TITLE_BG, (W - dot - 4, 0, dot + 4, TITLE_H - (2 if CYBER else 0)))
        pygame.draw.circle(surf, REMOTE_ROOT if rs == "root" else REMOTE_ON, (W - 10, TITLE_H // 2), 6)
    y = TITLE_H + 1
    n = ROWS_KEYS if keys and any(keys) else ROWS
    for i, row in enumerate(rows[:n]):
        draw_row(surf, f["row"], y, as_view(row, selected == i), f)
        y += ROW_H
    if keys and any(keys):
        draw_keybar(surf, keys, f)
    elif hint:
        surf.fill(HINT_BG, (0, H - HINT_H, W, HINT_H))
        if CYBER:
            glow_line(surf, 0, H - HINT_H, W, CYAN_D)
        surf.blit(f["small"].render(hint, True, HINT_FG), (6, H - HINT_H + 1), (0, 0, W - 12, HINT_H))


def draw_row(surf, font, y: int, r: RowView, f: dict | None = None) -> None:
    import pygame
    if r.state == "editing":
        surf.fill(EDIT_BG, (0, y, W, ROW_H))
        lw = font.size(r.label)[0]
        fx = min(lw + 14, W - 150)
        surf.blit(font.render(r.label, True, TITLE_FG), (6, y + 1), (0, 0, fx - 10, ROW_H))
        fw = W - fx - 4
        surf.fill(EDIT_FIELD, (fx - 3, y + 1, fw + 1, ROW_H - 2))
        # scroll so the cursor stays in view
        start = 0
        while start < r.cursor and font.size(r.value[start:r.cursor])[0] > fw - 8:
            start += 1
        text = r.value[start:]
        surf.blit(font.render(text, True, (255, 255, 255)), (fx, y + 1), (0, 0, fw - 2, ROW_H))
        cx = fx + font.size(r.value[start:r.cursor])[0]
        surf.fill((255, 255, 255), (min(cx, W - 6), y + 3, 2, ROW_H - 6))
        return
    f = f or fonts()
    sel = r.state == "selected"
    color = r.color or FG
    if sel:
        surf.fill(SEL_BG, (0, y, W, ROW_H))
        if CYBER:
            surf.fill(BAD if color == BAD else MAG, (0, y, 3, ROW_H))
        elif color == BAD:                     # a risky action keeps a red edge when highlighted
            surf.fill((235, 60, 60), (0, y, 4, ROW_H))
    x = 6
    if r.icon:
        text, bg = r.icon
        box = pygame.Rect(x, y + 2, 16, ROW_H - 4)
        if CYBER:
            ic = SEL_FG if sel else bg
            pygame.draw.rect(surf, ic, box, 1)
            img = f["tiny"].render(text, True, ic)
        else:
            pygame.draw.rect(surf, bg, box, border_radius=4)
            if sel:
                pygame.draw.rect(surf, SEL_FG, box, 1, border_radius=4)
            img = f["tiny"].render(text, True, (255, 255, 255))
        surf.blit(img, (box.centerx - img.get_width() // 2, box.centery - img.get_height() // 2))
        x += 22
    right = W - 6
    if r.chevron:
        cx, cy = W - 10, y + ROW_H // 2
        pygame.draw.lines(surf, SEL_FG if sel else CHEVRON, False,
                          [(cx - 3, cy - 5), (cx + 2, cy), (cx - 3, cy + 5)], 2)
        right = W - 20
    if r.pill is not None and CYBER:
        img = f["small"].render("[ON]" if r.pill else "[OFF]", True, SEL_FG if sel else (LIME if r.pill else DIM))
        surf.blit(img, (right - img.get_width(), y + 2))
        right -= img.get_width() + 6
    elif r.pill is not None:
        img = f["small"].render("On" if r.pill else "Off", True, (255, 255, 255))
        pw = 40
        box = pygame.Rect(right - pw, y + 2, pw, ROW_H - 4)
        pygame.draw.rect(surf, PILL_ON if r.pill else PILL_OFF, box, border_radius=8)
        if sel:
            pygame.draw.rect(surf, SEL_FG, box, 1, border_radius=8)
        surf.blit(img, (box.centerx - img.get_width() // 2, box.centery - img.get_height() // 2))
        right = box.x - 6
    vw = 0
    if r.value:
        img = font.render(r.value, True, SEL_FG if sel else (CYAN if CYBER else DIM))
        lw = font.size(r.label)[0]
        vw = min(img.get_width(), max((right - x) // 2, right - x - lw - 12))
        surf.blit(img, (right - vw, y + 1), (0, 0, vw, ROW_H))
    room = right - x - (vw + 10 if vw else 0)
    surf.blit(font.render(r.label, True, SEL_FG if sel else color), (x, y + 1), (0, 0, room, ROW_H))


def _fit(f: dict, text: str, width: int):
    font = f["small"]
    if font.size(text)[0] > width:
        font = f.get("tiny") or font
    return font


def draw_keybar(surf, keys: list, f: dict | None = None) -> None:
    """Labels for number keys 4..8 along the bottom, each with a short line pointing
    down to its key. keys: up to 5 labels, None/"" leaves a key out. A label can be
    ("text", color) to tint it (e.g. red for a risky action)."""
    import pygame
    f = f or fonts()
    top = H - KEYBAR_H
    surf.fill(KEYBAR_BG, (0, top, W, KEYBAR_H))
    if CYBER:
        glow_line(surf, 0, top, W, CYAN_D)
    else:
        surf.fill((40, 46, 62), (0, top, W, 1))
    by = top + 2
    for i, cx in enumerate(SOFT_X):
        lab = keys[i] if i < len(keys) else None
        if not lab:
            continue
        color = SOFT_FG
        if isinstance(lab, (tuple, list)):
            lab, color = lab[0], lab[1]
        r = pygame.Rect(cx - SOFT_W // 2, by, SOFT_W, SOFT_SIZE)
        if CYBER:
            r = r.inflate(-2, -2)
            surf.fill(SOFT_BG, r)
            corners(surf, r, color, 5)
        else:
            pygame.draw.rect(surf, SOFT_BG, r, border_radius=4)
        font = _fit(f, lab, SOFT_W - 4)
        img = font.render(lab, True, color)
        if img.get_width() > SOFT_W - 4:
            img = pygame.transform.smoothscale(img, (SOFT_W - 4, img.get_height()))
        surf.blit(img, (cx - img.get_width() // 2, by + (SOFT_SIZE - img.get_height()) // 2))
        surf.fill(color if CYBER else SOFT_LINE, (cx, by + SOFT_SIZE + 1, 1, H - (by + SOFT_SIZE + 1)))


# ---- helpers the popups below need (the other Nomad apps have them in nomadui) ----
HELP = "help"


def wrap_text(font, text, width: int, indent: str = "") -> list:
    out = []
    for para in (text if isinstance(text, list) else str(text).split("\n")):
        words, cur = str(para).split(" "), ""
        for w in words:
            trial = f"{cur} {w}" if cur else w
            if cur and font.size(trial)[0] > width:
                out.append(cur)
                cur = indent + w
            else:
                cur = trial
        out.append(cur)
    return out


# ---- Setup-steps popup and Loading popup (every app) -----------------------------
# Standard (Ted, 2026-11-28):
#  * A mode that needs the user to do something the app can't do for them
#    (plug a cable in, set a menu on the radio or TV, turn a feature on)
#    shows a StepsPopup with those steps on the mode's first screen.
#    OK / ESC close it, D closes it and hides it for good (saved in
#    ~/.config/<app>/popups.json). Fn+H on that screen shows the steps again.
#  * Anything slow inside an app shows the Loading popup, so the app never
#    looks locked up: draw_loading() for apps that redraw by themselves, or
#    `with loading(present, "Reading EDID"):` / run_loading() around a slow
#    call on the main thread (a small thread keeps the spinner turning).

POPUP_APP = ""                  # config folder name, set with set_popups()
POPUP_BG = (4, 6, 10)           # dimmed background behind a popup
POPUP_BOX = (22, 27, 40)
POPUP_EDGE = (150, 200, 255)


def set_popups(app_dir: str) -> None:
    """The app's ~/.config folder name (store rule), e.g. "nomad-sdr"; the
    "don't show again" choices are kept there."""
    global POPUP_APP
    POPUP_APP = app_dir


def _popup_file() -> str:
    import os
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, POPUP_APP or "nomad-app", "popups.json")


def _hidden_popups() -> set:
    import json
    try:
        with open(_popup_file()) as fh:
            return set(json.load(fh).get("hidden", []))
    except (OSError, ValueError, AttributeError):
        return set()


def steps_hidden(key: str) -> bool:
    """True when the user pressed D on this popup before."""
    return bool(key) and key in _hidden_popups()


def hide_steps(key: str, hidden: bool = True) -> None:
    import json
    import os
    keys = _hidden_popups()
    (keys.add if hidden else keys.discard)(key)
    path = _popup_file()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w") as fh:
            json.dump({"hidden": sorted(keys)}, fh)
        os.replace(path + ".tmp", path)
    except OSError:
        pass                    # read-only home: the popup just shows again


def reset_steps() -> None:
    """Show every setup popup again (for a Settings item)."""
    import os
    try:
        os.remove(_popup_file())
    except OSError:
        pass


def _dim(surf) -> None:
    import pygame
    shade = pygame.Surface(surf.get_size())
    shade.fill(POPUP_BG)
    shade.set_alpha(170)
    surf.blit(shade, (0, 0))


def _box(surf, rect, edge=None) -> None:
    import pygame
    pygame.draw.rect(surf, POPUP_BOX, rect, border_radius=6)
    pygame.draw.rect(surf, edge or POPUP_EDGE, rect, 2, border_radius=6)


class StepsPopup:
    """Steps the user has to do by hand before a mode works, shown over the
    mode's first screen. steps: list of strings (numbered for you); intro and
    outro: optional text before / after them. key: name for "don't show
    again" ("" = always show, no D key)."""
    LINE_H = 16

    def __init__(self, title: str, steps: list, intro: str = "", outro: str = "", key: str = "",
                 ok: str = "OK start"):
        self.title, self.steps, self.intro, self.outro = title, list(steps), intro, outro
        self.key, self.ok = key, ok
        self.top = 0
        self._lines = None

    @classmethod
    def maybe(cls, title: str, steps: list, key: str, **kw):
        """A popup, or None when the user hid this one with D."""
        return None if steps_hidden(key) else cls(title, steps, key=key, **kw)

    def help_text(self) -> str:
        """The same steps as plain text, for the screen's Fn+H help."""
        out = [self.intro] if self.intro else []
        out += [f"{i + 1}. {s}" for i, s in enumerate(self.steps)]
        if self.outro:
            out.append(self.outro)
        return "\n".join(out)

    def lines(self, f: dict) -> list:
        if self._lines is None:
            font, width = f["small"], W - 30
            out = []
            if self.intro:
                out += [(l, FG) for l in wrap_text(font, self.intro, width)]
            for i, s in enumerate(self.steps):
                out += [(l, FG) for l in wrap_text(font, f"{i + 1}. {s}", width, indent="    ")]
            if self.outro:
                out += [(l, DIM) for l in wrap_text(font, self.outro, width)]
            self._lines = out
        return self._lines

    def rows(self) -> int:
        return (H - 8 - TITLE_H - HINT_H - 6) // self.LINE_H

    def handle(self, key) -> str | None:
        """"close" when the popup should go away (OK, ESC, Left; D also hides
        it for good)."""
        name, char, ctrl = _k(key)
        if self.key and (char or "").lower() == "d" and not ctrl:
            hide_steps(self.key)
            return "close"
        name = nav_name(name, char, ctrl)
        n = len(self._lines or [])
        if name in ("up", "pageup"):
            self.top = max(0, self.top - (1 if name == "up" else self.rows()))
        elif name in ("down", "pagedown"):
            self.top = max(0, min(n - self.rows(), self.top + (1 if name == "down" else self.rows())))
        elif name in ("enter", "esc", "left", "right", HELP, "backspace"):
            return "close"
        return None

    def render(self, surf, f: dict | None = None) -> None:
        """Draw over whatever is on surf now (the mode's first screen)."""
        f = f or fonts()
        _dim(surf)
        x0, y0, w, h = 4, 4, W - 8, H - 8
        _box(surf, (x0, y0, w, h), WARN)
        surf.fill(TITLE_BG, (x0 + 2, y0 + 2, w - 4, TITLE_H - 4))
        img = f["row"].render(self.title, True, TITLE_FG)
        surf.blit(img, (x0 + 8, y0 + 2 + max(0, (TITLE_H - 4 - img.get_height()) // 2)), (0, 0, w - 16, TITLE_H))
        ls = self.lines(f)
        y = y0 + TITLE_H + 1
        for text, color in ls[self.top:self.top + self.rows()]:
            surf.blit(f["small"].render(text, True, color), (x0 + 9, y), (0, 0, w - 18, self.LINE_H))
            y += self.LINE_H
        if len(ls) > self.rows():
            span = h - TITLE_H - HINT_H - 4
            bh = max(8, span * self.rows() // len(ls))
            by = y0 + TITLE_H + (span - bh) * self.top // max(1, len(ls) - self.rows())
            surf.fill(POPUP_EDGE, (x0 + w - 6, by, 3, bh))
        hy = y0 + h - HINT_H - 1
        surf.fill(HINT_BG, (x0 + 2, hy, w - 4, HINT_H - 1))
        parts = [self.ok] + (["↑↓ more"] if len(ls) > self.rows() else []) + \
                (["D don't show again"] if self.key else [])
        hint = "  ".join(parts)
        himg = f["small"].render(hint, True, HINT_FG)
        if himg.get_width() > w - 12 and f.get("tiny"):
            himg = f["tiny"].render(hint, True, HINT_FG)
        surf.blit(himg, (x0 + 6, hy + 1), (0, 0, w - 12, HINT_H))


def draw_loading(surf, text: str = "Loading", t0: float | None = None, f: dict | None = None,
                 detail: str = "", dim: bool = True) -> None:
    """The Loading popup: a turning spinner, what is loading, and the seconds
    once it takes a while. Call it every frame (about 8 a second) on top of
    the screen it belongs to. t0: time.monotonic() when the job started."""
    import math
    import time
    import pygame
    f = f or fonts()
    now = time.monotonic()
    if dim:
        _dim(surf)
    w, h = 260, 70
    x0, y0 = (W - w) // 2, (H - h) // 2
    _box(surf, (x0, y0, w, h))
    cx, cy, r = x0 + 30, y0 + h // 2, 13
    step = int(now * 10) % 10
    for i in range(10):
        a = 2 * math.pi * i / 10 - math.pi / 2
        age = (step - i) % 10
        shade = max(50, 255 - age * 22)
        col = (shade * POPUP_EDGE[0] // 255, shade * POPUP_EDGE[1] // 255, shade * POPUP_EDGE[2] // 255)
        pygame.draw.circle(surf, col, (int(cx + r * math.cos(a)), int(cy + r * math.sin(a))), 3 if age else 4)
    label = (text or "Loading").rstrip(". …") + "…"
    tx, tw = x0 + 54, w - 62
    img = f["row"].render(label, True, FG)
    if img.get_width() > tw:
        img = f["small"].render(label, True, FG)
    sub = detail
    if t0 is not None and now - t0 >= 2:
        secs = f"{int(now - t0)} s"
        sub = f"{detail}  {secs}" if detail else f"Please wait  {secs}"
    elif not sub:
        sub = "Please wait"
    simg = f["small"].render(sub, True, DIM)
    top = y0 + (h - img.get_height() - simg.get_height() - 2) // 2
    surf.blit(img, (tx, top), (0, 0, tw, img.get_height()))
    surf.blit(simg, (tx, top + img.get_height() + 2), (0, 0, tw, simg.get_height()))


class loading:
    """Keep the Loading popup turning while the main thread does something
    slow:

        with nomadui.loading(present, "Reading EDID"):
            info = edid.read()

    present(draw) must put a frame on the LCD: it is called with a function
    draw(surf) that paints the popup on a 320x170 surface. It runs in a
    helper thread, so present must be safe to call from one (an app whose
    LCD has a worker thread, or one that just writes the framebuffer)."""

    def __init__(self, present, text: str = "Loading", detail: str = "", delay: float = 0.0,
                 background=None):
        self.present, self.text, self.detail, self.delay = present, text, detail, delay
        self.background = background     # optional fn(surf) drawing the screen under it
        self._stop = None
        self._th = None

    def _draw(self, surf) -> None:
        if self.background is not None:
            self.background(surf)
            draw_loading(surf, self.text, self.t0, detail=self.detail)
        else:
            surf.fill(BG)
            draw_loading(surf, self.text, self.t0, detail=self.detail, dim=False)

    def __enter__(self):
        import threading
        import time
        self.t0 = time.monotonic()
        self._stop = threading.Event()

        def spin():
            if self.delay and self._stop.wait(self.delay):
                return
            while not self._stop.is_set():
                try:
                    self.present(self._draw)
                except Exception:
                    return          # never let the popup break the app
                self._stop.wait(0.12)
        self._th = threading.Thread(target=spin, daemon=True)
        self._th.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._th is not None:
            self._th.join(1.0)
        return False


def run_loading(present, text: str, fn, *args, **kw):
    """fn(*args, **kw) with the Loading popup showing; returns its result."""
    with loading(present, text):
        return fn(*args, **kw)
