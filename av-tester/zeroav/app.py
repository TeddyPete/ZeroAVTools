"""Nomad AV Tester: HDMI test patterns and tones, run from the keyboard,
with status on the built-in LCD.

Run on the device (from the console, not a desktop session):
    python3 -m zeroav.app
Render every pattern to PNG without a display (for checking patterns):
    python3 -m zeroav.app --render-dir out --size 1920x1080
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time

from . import __version__
from . import nomadui as _nomadui

_nomadui.set_about("Nomad AV Tester", __version__)
_nomadui.set_popups("nomad-av-tester")

# Navigation follows the Nomad standard (CardputerZero arrows are on the Fn
# layer: Fn+F up, Fn+X down, Fn+Z left, Fn+C right; OK is Enter):
#   Up/Down move, Right or OK select, Left back one level,
#   Esc back one level and quit from the main menu.
MENU_HELP = "↑↓ move  OK open  hold ESC exit"
KEYS_HELP = "Up/Dn pattern  OK info  < back  T tone"
PICK_HELP = "↑↓ move  OK/→ choose  ←/ESC back"
SYNC_HELP = "OK start  0 zero cal  S save  < back"
STATUS_HELP = "Up/Dn scroll  < back"
ROOM_HELP = "↑↓ move  OK/→ edit  ESC back"
EDIT_HELP = "OK save  ESC cancel"
METER_HELP = "W A/Z  T tone  +/- cal  S save  < back"
EDID_HELP = "Up/Dn scroll  > page  S save  < back"
# The CardputerZero's Sym layer sends keypad/Japanese placeholder keys for the
# symbols printed on the keycaps (Sym+B '.' arrives as KEY_HIRAGANA). SDL turns
# those evdev codes into these scancodes; map them back to the printed symbol
# for text fields. The Fn and Sym keys themselves (KEY_DVD, KEY_CD) type nothing.
SYM_SCANCODES = {85: "~", 83: "`", 71: "_", 95: "-", 96: "+", 97: "=", 86: "[", 92: "]",
                 93: "{", 94: "}", 89: ";", 90: ":", 91: "'", 98: '"', 99: "<", 148: ">",
                 100: "\\", 135: "|", 146: ",", 147: ".", 138: "/", 136: "?"}
PLAIN_ARROWS = {}     # filled in run() once pygame is imported: f x z c -> up down left right
MENU = [("patterns", "Test patterns"), ("modes", "Video mode"), ("audio", "Audio output"),
        ("sync", "Lip-sync test"), ("meter", "Audio meter / RTA"), ("edid", "Display EDID"),
        ("link", "HDMI link status"), ("room", "Room report"), ("about", "About")]
# Number keys 4..8 under the screen (official CardputerZero key spec): labels
# drawn at the bottom of the LCD, and what each key does on each screen as
# (pygame key, typed char) fed through the normal key handling below.
SOFT = {"menu": ["Pattern", "Lip-sync", "Meter", "EDID", "Tone"],
        "patterns": ["Prev", "Next", "Group", "Info", "Tone"],
        "sync": ["Start", "Zero", "Save"],
        "meter": ["A/Z", "Tone", "Reset", "Save"],
        "edid": ["Page", "Save"]}
HELP_TEXT = {
    "menu": "Nomad AV Tester: test patterns, tones and checks for displays and audio over HDMI. "
            "Up/Down picks a tool, OK opens it. TheTechNomad.com",
    "patterns": "Test patterns on the HDMI output. Up/Down or 4/5 step through them, 6 jumps to "
                "the next group, OK or 7 shows frame rate and dropped frames, 8 cycles the tone. "
                "+ / - change the tone level, M changes the video mode.",
    "sync": "Lip-sync: the screen flashes white with a click. Aim the camera at the display and "
            "put the mic near the speakers. + means audio is late: add video delay or remove "
            "audio delay in the DSP, then test again. 5 makes the current result the zero point.",
    "meter": "Audio meter and 1/3-octave RTA from the mic. 4 switches A / Z weighting, 5 cycles the "
             "test tone, 6 resets max and Leq, 7 saves a report. + / - calibrate SPL by 1 dB.",
    "edid": "What the connected display says it can do (EDID). Up/Down scroll, 4 or OK shows the "
            "next page on HDMI, 5 saves a report with the raw bytes.",
    "link": "HDMI link: mode, measured frame rate, dropped frames and hot-plug history.",
    "room": "Room report: type a room name and notes, then Save report. It collects the EDID, "
            "the latest lip-sync and audio results.",
    "modes": "Pick the HDMI video mode. OK switches to it.",
    "audio": "Pick a test signal for the HDMI and headphone audio. OK plays it.",
}
# Setup steps the user does by hand, shown as a popup when the mode opens
# (nomadui.StepsPopup; D hides it for good, Fn+H shows them again).
SYNC_STEPS = ["Set the TV or projector to the picture mode you will use (Game mode changes delay).",
              "Point this device's camera at the screen, close enough that it sees the white flash.",
              "Put this device's mic near the speakers that play the HDMI sound.",
              "Keep the room quiet, then press OK to start."]
METER_STEPS = ["Hold this device where the listener sits, mic toward the speakers, or plug a "
               "measurement source into the 3.5 mm input.",
               "For real SPL: play a calibrator or check a reference meter once, then match it with "
               "+ / - (saved for next time).",
               "Press 5 to play a test tone or pink noise through HDMI."]
HELP_TEXT["sync"] += "\n\nBefore you start:\n" + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(SYNC_STEPS))
HELP_TEXT["meter"] += "\n\nBefore you start:\n" + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(METER_STEPS))


class StepsOnLcd:
    """A nomadui.StepsPopup on the LCD over the mode's title, shown and
    closed like the Fn+H help (lcd.show_help)."""

    def __init__(self, title: str, popup):
        self.title, self.popup = title, popup

    def handle(self, key):
        return self.popup.handle(key)

    def render(self, surf, f=None) -> None:
        _nomadui.render(surf, self.title, [], "")
        self.popup.render(surf)


def steps_popup(title: str, steps: list, key: str):
    """StepsOnLcd for this mode, or None when the user hid it with D."""
    p = _nomadui.StepsPopup.maybe("Before you start", steps, key, ok="OK done")
    return StepsOnLcd(title, p) if p else None


AUDIO_MODES = [(None, "Off"), ("tone", "1 kHz tone, both"), ("lr", "1 kHz L then R"),
               ("left", "1 kHz left only"), ("right", "1 kHz right only"), ("pink", "Pink noise"),
               ("sweep", "Log sweep 20 Hz-20 kHz"), ("polarity", "Polarity pulse")]
REPORT_DIR = os.path.expanduser("~/.local/share/nomad-av-tester/reports")


class _EscHold:
    """CardputerZero key spec: Esc short press = back one level, long press =
    exit to HOME. A short Esc is acted on when it is released; holding it for
    ESC_HOLD_S turns into a quit (the launcher would kill the app after 3 s)."""
    ESC_HOLD_S = 1.0

    def __init__(self, pg):
        self.pg, self.down = pg, None

    def filter(self, events):
        pg = self.pg
        out = []
        for ev in events:
            if ev.type == pg.KEYDOWN and ev.key == pg.K_ESCAPE:
                self.down = time.monotonic()
                continue
            if ev.type == pg.KEYUP and ev.key == pg.K_ESCAPE:
                if self.down is not None:
                    out.append(pg.event.Event(pg.KEYDOWN, key=pg.K_ESCAPE, unicode="\x1b",
                                              mod=0, scancode=getattr(ev, "scancode", 0)))
                self.down = None
                continue
            out.append(ev)
        if self.down is not None and time.monotonic() - self.down >= self.ESC_HOLD_S:
            self.down = None
            out.append(pg.event.Event(pg.QUIT, esc_hold=True))
        return out


class _HelpKey:
    """A pygame key as the name/char key object nomadui.HelpView expects."""

    def __init__(self, pygame, k, ch):
        names = {pygame.K_UP: "up", pygame.K_DOWN: "down", pygame.K_LEFT: "left", pygame.K_RIGHT: "right",
                 pygame.K_ESCAPE: "esc", pygame.K_RETURN: "enter", pygame.K_KP_ENTER: "enter",
                 pygame.K_PAGEUP: "pageup", pygame.K_PAGEDOWN: "pagedown", pygame.K_HELP: "help",
                 pygame.K_F1: "help", pygame.K_BACKSPACE: "backspace"}
        self.name = names.get(k, ch)
        self.char = "" if k in names else ch
        self.ctrl = False


def parse_size(s: str) -> tuple[int, int]:
    w, h = s.lower().split("x")
    return int(w), int(h)


def render_all(out_dir: str, size: tuple[int, int]) -> None:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame
    from .patterns import all_patterns, draw_info_overlay
    pygame.init()
    os.makedirs(out_dir, exist_ok=True)
    surf = pygame.Surface(size)
    for i, p in enumerate(all_patterns()):
        p.draw(surf, 30)
        if i == 0:
            draw_info_overlay(surf, [f"{size[0]}x{size[1]}", "sample overlay"])
        fn = os.path.join(out_dir, f"{i:02d}_{p.name.replace(' ', '_').replace('/', '-')}.png")
        pygame.image.save(surf, fn)
        print(fn)
    # LCD status screens, 320x170, used as store screenshots
    from .lcd import LcdPanel
    lcd = LcdPanel(headless=True)
    pats = all_patterns()
    for n, (i, tone) in enumerate([(0, "off"), (5, "tone"), (18, "lr")]):
        p = pats[i]
        lcd.show(p.name, status_lines(size, 60, 60.0, i, len(pats), p.category, tone, -20.0), KEYS_HELP)
        fn = os.path.join(out_dir, f"lcd_{n}.png")
        lcd.save(fn)
        print(fn)


def status_lines(mode, refresh, fps, idx, total, category, tone, dbfs, dropped=0) -> list[str]:
    label = mode.label if hasattr(mode, "label") else f"{mode[0]}x{mode[1]} @ {refresh or '?'} Hz"
    return [
        f"{label}  ({fps:.1f} fps)  drop {dropped}",
        f"Pattern {idx + 1}/{total}  [{category}]",
        f"Tone: {tone}  {dbfs:.0f} dBFS",
    ]


def pick_hdmi_display(pygame) -> int:
    """Index of the largest display that isn't the 320x170 LCD."""
    sizes = pygame.display.get_desktop_sizes()
    best, area = 0, -1
    for i, (w, h) in enumerate(sizes):
        if {w, h} == {320, 170}:
            continue
        if w * h > area:
            best, area = i, w * h
    return best


def save_edid_report(info, modes) -> str:
    """Write the decoded EDID (and the raw bytes) to the reports folder."""
    from . import edid
    os.makedirs(REPORT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = (info.name or info.vendor_id or "display").replace(" ", "_").replace("/", "-") if info else "none"
    base = os.path.join(REPORT_DIR, f"edid-{stamp}-{name}")
    with open(base + ".txt", "w") as f:
        f.write(f"Nomad AV Tester EDID report  {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(edid.text_report(info))
        if modes:
            f.write("\n== Modes this device can send ==\n")
            f.writelines(f"  {m.label}\n" for m in modes)
    if info is not None:
        with open(base + ".bin", "wb") as f:
            f.write(info.raw)
    return base + ".txt"


def link_lines(edid_info, mode, size, fps, dropped, plug_log, plug_state) -> list[str]:
    """HDMI link status, plain English."""
    from . import edid as _e
    conn, st = _e.hdmi_status()
    lines = [f"HDMI {conn or '?'}: {st}"]
    if edid_info:
        lines.append(f"Display: {edid_info.vendor or edid_info.vendor_id} {edid_info.name}".strip())
    lab = mode.label if getattr(mode, "size", None) == size else f"{size[0]}x{size[1]}"
    lines.append(f"Sending {lab}, {fps:.2f} fps, dropped {dropped}")
    lines.append("HDCP: not available (Pi HDMI has no HDCP)")
    lines.append(f"Plug events: {len(plug_log)}")
    lines += plug_log[-8:][::-1]
    return lines


def save_room_report(room, edid_info, mode, fps, dropped, plug_log, last) -> str:
    """One text file per room: EDID, link, latest lip-sync and audio results, notes."""
    from . import edid
    os.makedirs(REPORT_DIR, exist_ok=True)
    name = room["name"].strip() or "room"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:40]
    path = os.path.join(REPORT_DIR, f"room-{safe}-{time.strftime('%Y%m%d-%H%M%S')}.txt")
    out = [f"Nomad AV Tester room report: {name}", time.strftime("%Y-%m-%d %H:%M:%S"), "",
           "Notes: " + (room["notes"].strip() or "-"), "", "== Link =="]
    out += ["  " + l for l in link_lines(edid_info, mode, mode.size, fps, dropped, plug_log, "")]
    out += ["", last.get("sync") or "== Lip-sync ==\n  not measured in this session", "",
            last.get("audio") or "== Audio ==\n  not measured in this session", "",
            edid.text_report(edid_info)]
    with open(path, "w") as f:
        f.write("\n".join(out))
    return path


def pick_start_mode(modes, info, want_size=None) -> int:
    """Index of the mode to start in: the size asked for, else the
    display's native mode, else the first (largest) one."""
    if not modes:
        return 0
    if want_size:
        hits = [i for i, m in enumerate(modes) if m.size == want_size]
        if hits:
            return min(hits, key=lambda i: abs(modes[i].hz - 60))
    if info is not None and info.preferred:
        p = info.preferred
        hits = [i for i, m in enumerate(modes) if m.size == (p.w, p.h) and m.interlaced == p.interlaced]
        if hits:
            return min(hits, key=lambda i: abs(modes[i].hz - p.hz))
    progressive = [i for i, m in enumerate(modes) if not m.interlaced and m.hz <= 61]
    return progressive[0] if progressive else 0


def no_hdmi(lcd, detail: str) -> None:
    """Explain on the LCD why HDMI output could not start, then exit."""
    print(f"HDMI output unavailable: {detail}", file=sys.stderr, flush=True)
    lcd.show("No HDMI output", ["Connect an HDMI display", "and open the app again.", detail[:40]],
             "Closing in 8 s")
    time.sleep(8)


def selftest(out, modes, mode, patterns, edid_info) -> None:
    """Stress the HDMI output without a keyboard: flip through every pattern
    fast (like holding Space), the info overlay, EDID page, meter-style page,
    lip-sync flashes and every mode, logging GPU memory (CMA) as it goes."""
    import pygame
    from . import edid
    from .kms import cma_free_kb
    low = [10 ** 9]

    def note(what):
        free = cma_free_kb()[0]
        low[0] = min(low[0], free)
        print(f"selftest {what}: CMA free {free // 1024} MB", file=sys.stderr, flush=True)

    note("start")
    t0, n = time.perf_counter(), 0
    for rnd in range(3):
        for i, p in enumerate(patterns):
            for f in range(3):
                out.frame(p, n, ["selftest", p.name, f"round {rnd + 1}"] if i % 2 else None)
                n += 1
        note(f"patterns round {rnd + 1}")
    print(f"selftest fast pattern changes: {n} frames in {time.perf_counter() - t0:.1f} s",
          file=sys.stderr, flush=True)
    for p in patterns:
        if p.animated:
            t1 = time.perf_counter()
            for f in range(120):
                out.frame(p, f, None)
            print(f"selftest {p.name}: {120 / (time.perf_counter() - t1):.1f} fps", file=sys.stderr, flush=True)
    secs = edid.sections(edid_info) if edid_info else [("No display", ["none"])]
    for pg in range(3):
        out.text_page("Display EDID", secs, pg)
    note("EDID page")
    surf = pygame.Surface((960, 540))
    for k in range(20):
        surf.fill((k * 10 % 255, 40, 80))
        out.surface_page(surf, ("selftest", k))
    note("meter page")
    for k in range(40):
        out.solid(k % 6 < 3)
    note("lip-sync flashes")
    t1 = time.perf_counter()
    for f in range(120):
        out.frame(patterns[0], f, None)
    print(f"selftest static pattern: {120 / (time.perf_counter() - t1):.2f} fps at {mode.label}",
          file=sys.stderr, flush=True)
    for m in modes:
        try:
            out.open(m)
            out.frame(patterns[0], 0, None)
            t1 = time.perf_counter()
            for f in range(30):
                out.frame(patterns[1], f, None)
            note(f"mode {m.label} {30 / (time.perf_counter() - t1):.1f} fps")
        except Exception as e:
            print(f"selftest mode {m.label} FAILED: {e}", file=sys.stderr, flush=True)
    out.open(mode)
    note("end")
    print(f"selftest done. Lowest CMA free: {low[0] // 1024} MB", file=sys.stderr, flush=True)


def log_cma(what: str) -> None:
    from .kms import cma_free_kb
    free, total = cma_free_kb()
    # Linux lends idle CMA to its page cache and moves it back on demand, so
    # a low number here during a run is normal; GPU allocation failures show
    # up in the kernel log as "Failed to allocate from GEM DMA helper".
    print(f"CMA free {free // 1024} of {total // 1024} MB ({what}; includes memory lent to cache)",
          file=sys.stderr, flush=True)


def run(args) -> None:
    os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
    log_cma("start")
    # Preferred HDMI path: plain KMS framebuffers, no GPU (see kms.py).
    # SDL then only reads the keyboard ("evdev" driver). NOMAD_OUTPUT=gl
    # forces the older GPU path.
    kms = None
    if os.environ["SDL_VIDEODRIVER"] == "kmsdrm" and os.environ.get("NOMAD_OUTPUT", "kms") == "kms":
        try:
            from .kms import KmsOutput
            kms = KmsOutput()
            os.environ["SDL_VIDEODRIVER"] = "evdev"
        except Exception as e:
            print(f"direct KMS output unavailable ({e}); using the GPU path", file=sys.stderr, flush=True)
            kms = None
    import pygame
    from .audio import TonePlayer
    from .lcd import LcdPanel
    from .patterns import all_patterns

    pygame.init()
    lcd = LcdPanel()
    # Opening HDMI, reading the EDID and building the patterns takes a few
    # seconds: keep the Loading popup turning on the LCD meanwhile.
    boot = _nomadui.loading(lcd.draw_async, "Starting HDMI output")
    boot.__enter__()
    # The launcher stops apps with SIGTERM; turn that into a clean quit.
    signal.signal(signal.SIGTERM, lambda *_: pygame.event.post(pygame.event.Event(pygame.QUIT)))
    try:
        pygame.display.init()
        pygame.mouse.set_visible(False)
    except pygame.error as e:
        boot.__exit__(None, None, None)
        no_hdmi(lcd, f"Display error: {e}")
        return
    from . import edid, sdlmodes
    if kms is not None:
        # a hidden 1x1 window gives keyboard events a focus target
        from pygame._sdl2.video import Window as _W
        _input_window = _W("Nomad AV Tester input", size=(1, 1))
    disp = 0 if kms is not None else pick_hdmi_display(pygame)
    driver = "direct KMS" if kms is not None else pygame.display.get_driver()
    try:
        rates = pygame.display.get_desktop_refresh_rates()
        desk_hz = rates[disp] if rates else 0
    except AttributeError:
        desk_hz = 0
    edid_info = edid.read()
    windowed = driver in ("wayland", "x11")
    if kms is not None:
        modes = kms.modes()
    elif windowed:
        # The Pi desktop owns HDMI: fill its screen; it sets the mode.
        w, h = pygame.display.get_desktop_sizes()[disp]
        modes = [sdlmodes.Mode(w, h, float(desk_hz))]
    else:
        # Every mode with its refresh rate; fall back to plain sizes.
        modes = sdlmodes.list_modes(disp) or [
            sdlmodes.Mode(w, h, float(desk_hz))
            for w, h in sorted(set(pygame.display.list_modes(display=disp) or []), reverse=True)]
    if not modes:
        modes = [sdlmodes.Mode(1920, 1080, 60.0)]
    want = parse_size(args.size) if args.size and not windowed else None
    mode_idx = pick_start_mode(modes, edid_info, want)
    if args.mode:
        hits = [i for i, m in enumerate(modes) if m.label == args.mode]
        if hits:
            mode_idx = hits[0]
        else:
            print(f"mode {args.mode} not offered; have: {', '.join(m.label for m in modes)}",
                  file=sys.stderr, flush=True)
    mode = modes[mode_idx]
    print(f"video driver {driver}, display {disp}, {len(modes)} modes, start {mode.label}",
          file=sys.stderr, flush=True)

    from .output import HdmiOutput
    try:
        if kms is not None:
            kms.open(mode)
            out = kms
        else:
            out = HdmiOutput(mode, fullscreen_desktop=windowed, display=disp)
    except Exception as e:
        boot.__exit__(None, None, None)
        no_hdmi(lcd, str(e))
        return
    size = out.size
    print(f"output: {getattr(out, 'kind', 'GPU' if out.accelerated else 'software renderer')}",
          file=sys.stderr, flush=True)
    log_cma(f"output open at {mode.label}")
    cma_t = time.monotonic()
    patterns = all_patterns()
    boot.__exit__(None, None, None)
    if args.selftest:
        selftest(out, modes, mode, patterns, edid_info)
        out.close()
        return
    tones = TonePlayer(args.audio_device)
    idx, frame, show_info = 0, 0, False
    edid_view, edid_scroll, edid_page, edid_msg = False, 0, 0, ""
    sync, sync_msg, sync_lcd_t = None, "", 0.0
    meter, meter_msg, meter_t, meter_n = None, "", 0.0, 0
    status_view, status_scroll = False, 0
    room = None                     # dict while the room report form is open
    help_view = None                # nomadui.HelpView while Fn+H help is shown
    pygame.key.set_repeat()   # no auto-repeat: a held Esc or OK must act once
    PLAIN_ARROWS.update({"f": pygame.K_UP, "x": pygame.K_DOWN, "z": pygame.K_LEFT, "c": pygame.K_RIGHT})
    screen, menu_sel = "menu", 0    # "menu", "patterns", or a picker: "modes" / "audio"
    pick_sel = 0
    last = {"sync": "", "audio": ""}  # latest results, for room reports
    plug_log: list[str] = []
    plug_state = edid.hdmi_status()[1]
    plug_t = time.monotonic()
    banner, banner_until = "", 0.0
    meter_surf = pygame.Surface((960, 540))
    meter_font = pygame.font.Font(None, 34)
    meter_small = pygame.font.Font(None, 24)
    clock = pygame.time.Clock()
    dropped, worst_ms, worst_win, nominal = 0, 0.0, 0.0, 0.0
    last_present = time.perf_counter()
    skip_gap = True
    fps_measured, t0, n0 = 0.0, time.monotonic(), 0
    refresh = mode.hz or desk_hz
    dirty = True

    def edid_sections():
        secs = edid.sections(edid_info) if edid_info else [
            ("No display", ["No EDID found. Is an HDMI display connected?"])]
        can = [m.label for m in modes]
        return secs + [(f"This device can send ({len(can)})", can)]

    esc = _EscHold(pygame)
    while True:
        for ev in esc.filter(pygame.event.get()):
            if ev.type in (pygame.QUIT, pygame.WINDOWCLOSE):
                print("quit: Esc held" if getattr(ev, "esc_hold", False) else
                      "quit: stop request from the launcher (SIGTERM) or window close",
                      file=sys.stderr, flush=True)
                tones.stop()
                out.close()
                return
            if ev.type != pygame.KEYDOWN:
                continue
            print(f"key {pygame.key.name(ev.key)!r} {ev.unicode!r}", file=sys.stderr, flush=True)
            k, ch = ev.key, (ev.unicode or "").lower()
            typing = room is not None and room["edit"] is not None
            if not typing and ch in PLAIN_ARROWS and len(ch) == 1:
                k, ch = PLAIN_ARROWS[ch], ""     # plain F/X/Z/C work as arrows too
            where = ("edid" if edid_view else "sync" if sync is not None else "room" if room is not None
                     else "link" if status_view else "meter" if meter is not None else screen)
            if help_view is not None:            # Fn+H help is open: scroll or close it
                if help_view.handle(_HelpKey(pygame, k, ch)) == "close":
                    help_view = None
                    lcd.hide_help()
                    sync_lcd_t = 0.0
                else:
                    lcd.show_help(help_view)
                dirty = True
                skip_gap = True
                continue
            if k in (pygame.K_HELP, pygame.K_F1):  # Fn+H: show this screen's help
                from . import nomadui
                titles = {"menu": "Nomad AV Tester", "patterns": "Test patterns", "sync": "Lip-sync",
                          "meter": "Audio meter", "edid": "Display EDID", "link": "HDMI link",
                          "room": "Room report", "modes": "Video mode", "audio": "Audio output"}
                help_view = nomadui.HelpView(titles.get(where, ""), HELP_TEXT.get(where, ""), SOFT.get(where))
                lcd.show_help(help_view)
                skip_gap = True
                continue
            if not typing and len(ch) == 1 and ch in "45678" and where in SOFT:
                i = "45678".index(ch)
                if i >= len(SOFT[where]):
                    continue                     # no action on this key here
                if where == "menu" and i == 0:
                    screen = "patterns"
                    dirty = True
                    skip_gap = True
                    continue
                k, ch = {"menu": [None, (None, "l"), (None, "a"), (None, "e"), (None, "t")],
                         "patterns": [(pygame.K_UP, ""), (pygame.K_DOWN, ""), (None, "g"), (None, "i"),
                                      (None, "t")],
                         "sync": [(pygame.K_SPACE, ""), (None, "0"), (None, "s")],
                         "meter": [(None, "w"), (None, "t"), (None, "r"), (None, "s")],
                         "edid": [(pygame.K_RETURN, ""), (None, "s")]}[where][i]
            up, down = k == pygame.K_UP, k == pygame.K_DOWN
            back = k in (pygame.K_ESCAPE, pygame.K_LEFT)
            select = k in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_RIGHT)
            if edid_view:
                if back or ch in ("e", "b"):
                    edid_view = False
                elif ch == "q":
                    tones.stop()
                    out.close()
                    return
                elif down or ch in (".", "n"):
                    edid_scroll += 1
                elif up or ch in (",", ";", "p"):
                    edid_scroll = max(0, edid_scroll - 1)
                elif select or ch in ("/", " "):
                    edid_page += 1      # next HDMI page (wraps)
                elif ch == "s":
                    try:
                        path = save_edid_report(edid_info, modes)
                        edid_msg = "Saved " + os.path.basename(path)
                        print(f"EDID report saved to {path}", file=sys.stderr, flush=True)
                    except OSError as e:
                        edid_msg = f"Save failed: {e}"
                dirty = True
                skip_gap = True
                continue
            if sync is not None:
                if sync.state == "done" and sync.result():
                    last["sync"] = sync.report()
                if back or ch in ("l", "b"):
                    sync.stop()
                    sync = None
                elif ch == "q":
                    sync.stop()
                    tones.stop()
                    out.close()
                    return
                elif (select or k == pygame.K_SPACE) and sync.state in ("idle", "done", "error"):
                    from .lipsync import LipSyncTest
                    sync.stop()
                    sync = LipSyncTest(tones.device, args.mic_device)
                    sync.start()
                    sync_msg = ""
                elif ch == "0" and sync.state == "done":
                    cal = sync.calibrate_here()
                    sync_msg = (f"Calibrated: {cal:+.0f} ms is now zero" if cal is not None
                                else "Need 3+ good flashes to calibrate")
                elif ch == "s" and sync.state == "done":
                    try:
                        os.makedirs(REPORT_DIR, exist_ok=True)
                        path = os.path.join(REPORT_DIR, f"lipsync-{time.strftime('%Y%m%d-%H%M%S')}.txt")
                        with open(path, "w") as f:
                            f.write(sync.report())
                        sync_msg = "Saved " + os.path.basename(path)
                    except OSError as e:
                        sync_msg = f"Save failed: {e}"
                dirty = True
                skip_gap = True
                continue
            if room is not None:
                # Form: move with F/X (or arrows), OK or Right edits a field;
                # while editing, keys type, OK keeps the text, Esc undoes it.
                if typing:
                    f = room["edit"]
                    if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        room["edit"] = None
                    elif k == pygame.K_ESCAPE:
                        room[f], room["edit"] = room["orig"], None
                    elif k == pygame.K_BACKSPACE:
                        room[f] = room[f][:-1]
                    elif getattr(ev, "scancode", None) in SYM_SCANCODES:
                        room[f] += SYM_SCANCODES[ev.scancode]
                    elif ev.unicode and ev.unicode.isprintable():
                        room[f] += ev.unicode
                elif back:
                    room = None
                elif up:
                    room["sel"] = (room["sel"] - 1) % 3
                elif down:
                    room["sel"] = (room["sel"] + 1) % 3
                elif select:
                    if room["sel"] < 2:
                        f = ("name", "notes")[room["sel"]]
                        room["edit"], room["orig"] = f, room[f]
                    else:
                        try:
                            path = save_room_report(room, edid_info, mode, fps_measured, dropped,
                                                    plug_log, last)
                            banner, banner_until = "Saved " + os.path.basename(path), time.monotonic() + 4
                        except OSError as e:
                            banner, banner_until = f"Save failed: {e}", time.monotonic() + 4
                        room = None
                dirty = True
                skip_gap = True
                continue
            if status_view:
                if back or ch in ("h", "b"):
                    status_view = False
                elif down or ch in (".", "n", " "):
                    status_scroll += 1
                elif up or ch in (",", ";", "p"):
                    status_scroll = max(0, status_scroll - 1)
                dirty = True
                skip_gap = True
                continue
            if meter is not None:
                if back or ch in ("a", "b"):
                    last["audio"] = meter.report()
                    meter.stop()
                    meter = None
                elif ch == "q":
                    meter.stop()
                    tones.stop()
                    out.close()
                    return
                elif ch == "w":
                    meter.weighting = "A" if meter.weighting == "Z" else "Z"
                    meter.reset()
                elif ch == "r":
                    meter.reset()
                    meter_msg = "Max and Leq reset"
                elif ch in ("+", "="):
                    meter.adjust_cal(+1.0)
                    meter_msg = f"SPL cal {meter.cal:+.0f} dB (saved)"
                elif ch in ("-", "_"):
                    meter.adjust_cal(-1.0)
                    meter_msg = f"SPL cal {meter.cal:+.0f} dB (saved)"
                elif ch == "t":
                    tones.cycle()
                    meter_msg = f"Output: {tones.mode or 'off'}"
                elif ch == "s":
                    try:
                        os.makedirs(REPORT_DIR, exist_ok=True)
                        path = os.path.join(REPORT_DIR, f"audio-{time.strftime('%Y%m%d-%H%M%S')}.txt")
                        with open(path, "w") as f:
                            f.write(meter.report())
                        meter_msg = "Saved " + os.path.basename(path)
                    except OSError as e:
                        meter_msg = f"Save failed: {e}"
                dirty = True
                skip_gap = True
                continue
            if screen in ("modes", "audio"):
                n = len(modes) if screen == "modes" else len(AUDIO_MODES)
                if back:
                    screen = "menu"
                elif up:
                    pick_sel = (pick_sel - 1) % n
                elif down:
                    pick_sel = (pick_sel + 1) % n
                elif select:
                    if screen == "audio":
                        am = AUDIO_MODES[pick_sel][0]
                        tones.stop() if am is None else tones.play(am)
                    elif pick_sel != mode_idx:
                        ch, k = "m", None      # handled by the mode switch below
                if ch != "m":
                    dirty = True
                    skip_gap = True
                    continue
            if screen == "menu" and (select or up or down):
                if up:
                    menu_sel = (menu_sel - 1) % len(MENU)
                elif down:
                    menu_sel = (menu_sel + 1) % len(MENU)
                else:
                    item = MENU[menu_sel][0]
                    if item == "patterns":
                        screen = "patterns"
                    elif item == "modes":
                        screen, pick_sel = "modes", mode_idx
                    elif item == "audio":
                        screen = "audio"
                        pick_sel = next((i for i, (m, _) in enumerate(AUDIO_MODES) if m == tones.mode), 0)
                    elif item == "about":            # About: shown like Fn+H help, ESC closes it
                        from . import nomadui
                        help_view = nomadui.AboutView()
                        lcd.show_help(help_view)
                        skip_gap = True
                        continue
                    else:
                        ch = {"sync": "l", "meter": "a", "edid": "e", "link": "h", "room": "n"}[item]
                        k = None
                if k is not None:
                    dirty = True
                    skip_gap = True
                    continue
            if ch == "h":
                status_view, status_scroll = True, 0
                dirty = True
                skip_gap = True
                continue
            if ch == "n":
                room = {"sel": 0, "edit": None, "orig": "", "name": "", "notes": ""}
                dirty = True
                skip_gap = True
                continue
            if ch == "a":
                from .meter import Meter
                with _nomadui.loading(lcd.draw_async, "Opening the mic"):
                    meter, meter_msg = Meter(args.mic_device), ""
                    meter.start()
                help_view = steps_popup("Audio meter", METER_STEPS, "steps-meter")
                if help_view is not None:
                    lcd.show_help(help_view)
                skip_gap = True
                continue
            if ch == "l":
                from .lipsync import LipSyncTest
                tones.stop()
                sync, sync_msg = LipSyncTest(tones.device, args.mic_device), ""
                help_view = steps_popup("Lip-sync test", SYNC_STEPS, "steps-sync")
                if help_view is not None:
                    lcd.show_help(help_view)
                dirty = True
                skip_gap = True
                continue
            if ch == "e":
                edid_info = edid.read()  # re-read: the display may have changed
                edid_view, edid_scroll, edid_page, edid_msg = True, 0, 0, ""
                dirty = True
                skip_gap = True
                continue
            if (k == pygame.K_ESCAPE and screen == "menu") or ch == "q":
                print(f"quit: key {pygame.key.name(k) if k else ch}", file=sys.stderr, flush=True)
                tones.stop()
                out.close()
                return
            if back:                       # patterns -> main menu
                screen = "menu"
            elif ch == "m" and k is None:  # chosen in the video mode list
                mode_idx = pick_sel - 1
            if screen == "patterns" and (down or k in (pygame.K_RIGHT, pygame.K_SPACE) or ch in (".", "/")):
                idx = (idx + 1) % len(patterns)
            elif screen == "patterns" and (up or ch in (",", ";", "p")):
                idx = (idx - 1) % len(patterns)
            elif screen == "patterns" and k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                show_info = not show_info
            elif screen == "patterns" and ch == "g":     # key 6: first pattern of the next group
                cats = list(dict.fromkeys(p.category for p in patterns))
                nxt = cats[(cats.index(patterns[idx].category) + 1) % len(cats)]
                idx = next(i for i, p in enumerate(patterns) if p.category == nxt)
            elif ch.isdigit() and ch != "0":
                cats = list(dict.fromkeys(p.category for p in patterns))
                c = int(ch) - 1
                if c < len(cats):
                    idx = next(i for i, p in enumerate(patterns) if p.category == cats[c])
            elif ch == "i":
                show_info = not show_info
            elif ch == "t":
                tones.cycle()
            elif ch in ("+", "="):
                tones.dbfs = min(0.0, tones.dbfs + 2)
                if tones.mode:
                    tones.play(tones.mode)
            elif ch == "-":
                tones.dbfs = max(-60.0, tones.dbfs - 2)
                if tones.mode:
                    tones.play(tones.mode)
            elif ch == "m" and len(modes) > 1:
                mode_idx = (mode_idx + 1) % len(modes)
                mode = modes[mode_idx]
                with _nomadui.loading(lcd.draw_async, f"Switching to {mode.label}"):
                    out.open(mode)
                size = out.size
                refresh = mode.hz or desk_hz
                print(f"mode {mode.label} ({'set' if out.mode_set else 'size only'})",
                      file=sys.stderr, flush=True)
                log_cma(f"mode {mode.label}")
                dropped, nominal = 0, 0.0
            dirty = True
            skip_gap = True  # a pattern or mode change rebuilds the texture

        nowm = time.monotonic()
        if nowm - cma_t >= 30:
            cma_t = nowm
            log_cma("every 30 s")
        if nowm - plug_t >= 1.0:
            plug_t = nowm
            st = edid.hdmi_status()[1]
            if st != plug_state:
                plug_state = st
                plug_log.append(f"{time.strftime('%H:%M:%S')} {st}")
                print(f"HDMI {st}", file=sys.stderr, flush=True)
                if st == "connected":
                    edid_info = edid.read()
                    who = (edid_info.name or edid_info.vendor) if edid_info else ""
                    banner = f"HDMI connected {who}".strip()
                else:
                    banner = "HDMI unplugged"
                banner_until, dirty = nowm + 5, True
        if banner and nowm > banner_until:
            banner, dirty = "", True

        if room is not None or status_view:
            if dirty:
                if room is not None:
                    items = ["Room: " + room["name"], "Notes: " + room["notes"], "Save report"]
                    lcd.show_list_async("Room report", items, room["sel"],
                                        EDIT_HELP if room["edit"] else ROOM_HELP, editing=bool(room["edit"]))
                else:
                    ll = lcd.wrap(link_lines(edid_info, mode, size, fps_measured, dropped, plug_log, plug_state))
                    status_scroll = min(status_scroll, max(0, len(ll) - lcd.ROWS_SMALL))
                    lcd.show_async("HDMI link", ll[status_scroll:status_scroll + lcd.ROWS_SMALL],
                                   STATUS_HELP, small=True)
                dirty = False

        if meter is not None:
            now = time.monotonic()
            if now - meter_t >= 0.1:
                meter_t = now
                meter.update()
                from .meter import draw_rta
                ms, mf, mm = meter_surf, meter_font, meter_small
                ms.fill((8, 12, 24))
                ms.blit(mf.render(f"Audio input ({meter.weighting}-weighted)   output: {tones.mode or 'off'}",
                                  True, (255, 255, 255)), (20, 12))
                for i, line in enumerate(meter.lines()):
                    ms.blit(mm.render(line, True, (230, 230, 230)), (20, 52 + i * 24))
                if meter.mic.error:
                    ms.blit(mm.render(meter.mic.error[:80], True, (255, 120, 120)), (20, 124))
                draw_rta(ms, (20, 150, 920, 370), meter.bands, meter.hold, font=mm)
                meter_n += 1
                out.surface_page(ms, ('meter', meter_n))
                bands, hold = meter.bands.copy(), meter.hold.copy()
                lines = meter.lines()[:2]
                if meter_msg:
                    lines = lines[:1] + [meter_msg]
                msg, wt = meter_msg, meter.weighting

                def draw(s, bands=bands, hold=hold, lines=lines, msg=msg, wt=wt):
                    s.fill((0, 0, 0))
                    s.fill((20, 60, 120), (0, 0, 320, 22))
                    s.blit(lcd.font.render(f"Audio in  {wt}   {tones.mode or ''}", True, (255, 255, 255)), (6, 3))
                    for i, ln in enumerate(lines):
                        s.blit(lcd.font_small.render(ln, True, (230, 230, 230)), (6, 25 + i * 15))
                    draw_rta(s, (2, 58, 316, 80), bands, hold, font=None)
                    from . import nomadui
                    nomadui.draw_keybar(s, SOFT["meter"])

                lcd.draw_async(draw)
            else:
                out.surface_page(meter_surf, ("meter", meter_n))
            frame += 1
            continue

        if sync is not None:
            white = sync.frame_is_white() if sync.state in ("warmup", "running") else False
            out.solid(white)
            if sync.state in ("warmup", "running"):
                sync.presented(white)
            now = time.monotonic()
            if dirty or now - sync_lcd_t > 0.3:
                lines = sync.lcd_lines()
                if sync_msg:
                    lines = lines[:4] + [sync_msg]
                lcd.show_async("Lip-sync test", lines, SYNC_HELP, small=True, keys=SOFT["sync"])
                sync_lcd_t, dirty = now, False
            frame += 1
            continue

        if edid_view:
            out.text_page("Display EDID", edid_sections(), edid_page)
            edid_page %= max(1, out.pages)
            if dirty:
                lines = lcd.wrap(edid.summary_lines(edid_info))
                rows = lcd.ROWS_SMALL
                edid_scroll = min(edid_scroll, max(0, len(lines) - rows))
                title = (edid_info.name or edid_info.vendor or "Display") if edid_info else "No display"
                rows = lcd.ROWS_SMALL_KEYS - (1 if edid_msg else 0)
                edid_scroll = min(edid_scroll, max(0, len(lines) - rows))
                pos = f"{edid_scroll + 1}-{min(len(lines), edid_scroll + rows)}/{len(lines)}"
                lcd.show_async(f"EDID {pos} {title}", lines[edid_scroll:edid_scroll + rows]
                               + ([edid_msg] if edid_msg else []), EDID_HELP, small=True, keys=SOFT["edid"])
                dirty = False
            clock.tick(30)
            frame += 1
            continue

        pat = patterns[idx]
        overlay = None
        if show_info:
            overlay = [f"{mode.label if size == mode.size else f'{size[0]}x{size[1]}'}"
                       f"  measured {fps_measured:.2f} fps",
                       f"dropped frames: {dropped}   worst frame {worst_ms:.1f} ms",
                       pat.name]
        # Every loop presents one frame, synced to vblank: the GPU redraws the
        # cached texture, so this runs at the HDMI refresh rate.
        out.frame(pat, frame, overlay)
        frame += 1
        # Dropped-frame check: a gap between presents longer than 1.5 frame
        # periods means at least one vblank was missed.
        tp = time.perf_counter()
        gap = tp - last_present
        last_present = tp
        if skip_gap:
            skip_gap = False
        else:
            worst_win = max(worst_win, gap)
            if nominal and gap > 1.5 * nominal:
                dropped += round(gap / nominal) - 1
        clock.tick(125)  # safety cap only, in case vsync isn't honoured

        now = time.monotonic()
        if now - t0 >= 1.0:
            fps_measured = (frame - n0) / (now - t0)
            t0, n0 = now, frame
            worst_ms, worst_win = worst_win * 1000, 0.0
            if int(now) % 5 == 0:
                print(f"{mode.label}: {fps_measured:.2f} fps, dropped {dropped}, worst {worst_ms:.1f} ms",
                      file=sys.stderr, flush=True)
            # Learn the frame period from the display (or from what we see).
            if not nominal and fps_measured > 20:
                nominal = 1.0 / (refresh if refresh else round(fps_measured))
            dirty = True
        if dirty and room is None and not status_view and screen != "patterns":
            if screen == "menu":
                vals = {"patterns": pat.name, "modes": mode.label, "audio": tones.mode or "off"}
                items = [label + (f": {vals[key]}" if key in vals else "") for key, label in MENU]
                sel, title, hint = menu_sel, banner or "Nomad AV Tester", MENU_HELP
            else:
                names = ([m.label for m in modes] if screen == "modes"
                         else [label for _, label in AUDIO_MODES])
                cur = mode_idx if screen == "modes" else next(
                    (i for i, (m, _) in enumerate(AUDIO_MODES) if m == tones.mode), 0)
                items = [n + ("  *" if i == cur else "") for i, n in enumerate(names)]
                sel, hint = pick_sel, PICK_HELP
                title = "Video mode" if screen == "modes" else f"Audio out {tones.dbfs:.0f} dBFS"
            lcd.show_list_async(title, items, sel, hint, keys=SOFT.get(screen))
            dirty = False
        if dirty and room is None and not status_view:
            tone = tones.mode or "off"
            lcd.show_async(banner or pat.name, status_lines(mode if size == mode.size else size, refresh, fps_measured, idx, len(patterns),
                                                  pat.category, tone, tones.dbfs, dropped), KEYS_HELP,
                           keys=SOFT["patterns"])
            dirty = False


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--size", help="HDMI resolution, e.g. 1920x1080 (default: display's preferred)")
    ap.add_argument("--mode", help="start in this exact mode, e.g. 1920x1080p50 or 1920x1080p59.94")
    ap.add_argument("--audio-device", default=os.environ.get("ZEROAV_AUDIO"),
                    help="ALSA device for tones (default: pipewire if present, else default)")
    ap.add_argument("--mic-device", default=os.environ.get("NOMAD_MIC", "default"),
                    help="ALSA capture device for the lip-sync test (default: default, i.e. PipeWire)")
    ap.add_argument("--selftest", action="store_true",
                    help="stress the HDMI output (patterns, pages, flashes, every mode), log GPU memory, exit")
    ap.add_argument("--render-dir", help="render all patterns to PNG files here and exit")
    args = ap.parse_args(argv)
    if args.render_dir:
        render_all(args.render_dir, parse_size(args.size or "1920x1080"))
        return
    # Leave a trace of every exit: Python errors and hard crashes (segfaults
    # in SDL or the GPU driver) both go to the app log via stderr.
    import faulthandler
    import traceback
    faulthandler.enable(file=sys.stderr, all_threads=True)
    print(f"--- start {time.strftime('%Y-%m-%d %H:%M:%S')} args {argv}", file=sys.stderr, flush=True)
    try:
        run(args)
        print(f"--- normal exit {time.strftime('%H:%M:%S')}", file=sys.stderr, flush=True)
    except Exception:
        traceback.print_exc()
        print(f"--- CRASH {time.strftime('%H:%M:%S')}", file=sys.stderr, flush=True)
        try:
            from .lcd import LcdPanel
            import pygame
            pygame.init()
            msg = traceback.format_exc().strip().splitlines()[-1]
            LcdPanel().show("App error", ["Sorry, the app hit an error:", msg[:40], msg[40:80],
                                          "Log: ~/.local/share/", "nomad-av-tester/app.log"], "Closing in 8 s")
            time.sleep(8)
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main(sys.argv[1:])
