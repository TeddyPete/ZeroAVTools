"""Nomad SDR screens and keys (320x170 LCD + CardputerZero keyboard).

Screens: Radio (main page), Menu, Band plan, Memories & presets, APRS,
Fox hunt, Recordings (+ web page QR), Settings, About.
Keys follow the CardputerZero key spec and the Nomad standard (nomadui.py).
"""
from __future__ import annotations

import os
import signal
import subprocess
import time

import pygame

from . import __version__
from . import aprs as P
from . import audio as A
from . import bandplan as B
from . import dsp
from . import files as Fs
from . import lcd as L
from . import nomadui as T
from . import qr
from . import settings as S
from .keys import Keyboard
from .radio import STEPS, Radio

APP_TITLE = "Nomad SDR"
W, H = T.W, T.H
BIG_FONT = None


def font(size: int, bold: bool = True):
    path = next((p for p in T.FONT_PATHS if os.path.exists(p) and (bold or "Bold" not in p)), None)
    return pygame.font.Font(path, size) if path else pygame.font.Font(None, int(size * 1.35))


def fmt_mhz(mhz: float) -> str:
    return f"{mhz:.4f}".rstrip("0").rstrip(".") if mhz < 1000 else f"{mhz:.3f}"


def fmt_step(hz: int) -> str:
    return f"{hz / 1e6:g}M" if hz >= 1e6 else (f"{hz / 1e3:g}k" if hz >= 1000 else f"{hz}")


def fmt_bw(hz: float) -> str:
    return f"{hz / 1000:g}k"


def grid_to_latlon(g: str):
    g = (g or "").strip().upper()
    if len(g) < 4 or not (g[0].isalpha() and g[1].isalpha() and g[2:4].isdigit()):
        return None
    lon = (ord(g[0]) - 65) * 20 - 180 + int(g[2]) * 2
    lat = (ord(g[1]) - 65) * 10 - 90 + int(g[3])
    if len(g) >= 6 and g[4:6].isalpha():
        lon += (ord(g[4]) - 65) * (2 / 24) + 1 / 24
        lat += (ord(g[5]) - 65) * (1 / 24) + 1 / 48
    else:
        lon += 1
        lat += 0.5
    return lat, lon


BEARINGS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]

HELP_RADIO = ("Up/Down (F/X) tune by the step. Left/Right (Z/C) change the step. "
              "OK opens the menu. 4 mode, 5 type a frequency, 6 record, 7 band plan, 8 menu. "
              "R listens on a repeater's input, M saves a memory, S squelch, G gain, "
              "- and = volume, Space mutes. The line under the meter shows the band plan "
              "and whether your license class may transmit there. Set your class in Settings.")


# ---------------------------------------------------------------------------- screens

class Screen:
    title = APP_TITLE
    help = ""
    text_entry = False

    def __init__(self, app):
        self.app = app

    def keybar(self) -> list:
        return []

    def soft(self, k) -> bool:
        """Number keys 4..8. Return True when used."""
        return False

    def key(self, k) -> None:
        if k.name in ("esc", "left"):
            self.app.pop()

    def tick(self) -> None:
        pass

    def render(self, surf, f) -> None:
        T.render(surf, self.title, [], "", None, "", f, self.keybar())

    def on_close(self) -> None:
        pass


class ListScreen(Screen):
    """Menu list. items: [(label, value_text, action, color)]."""

    def __init__(self, app, title, items_fn, hint=T.HINT_MENU):
        super().__init__(app)
        self.title = title
        self.items_fn = items_fn
        self.sel = 0
        self.top = 0
        self.hint = hint

    def items(self):
        return self.items_fn()

    def key(self, k) -> None:
        it = self.items()
        n = len(it)
        if k.name == "up" and n:
            self.sel = (self.sel - 1) % n
        elif k.name == "down" and n:
            self.sel = (self.sel + 1) % n
        elif k.name in ("enter", "right") and n:
            act = it[min(self.sel, n - 1)][2]
            if act:
                act()
        elif k.name in ("esc", "left"):
            self.app.pop()

    def render(self, surf, f) -> None:
        it = self.items()
        rows_n = T.ROWS_KEYS if any(self.keybar()) else T.ROWS
        self.sel = max(0, min(self.sel, len(it) - 1)) if it else 0
        if self.sel < self.top:
            self.top = self.sel
        if self.sel >= self.top + rows_n:
            self.top = self.sel - rows_n + 1
        rows = [(lab, col or T.FG, val) for lab, val, _, col in it[self.top:self.top + rows_n]]
        if not rows:
            rows = [("(nothing here yet)", T.DIM)]
        T.render(surf, self.title, rows, self.hint, (self.sel - self.top) if it else None, "", f, self.keybar())


class FormScreen(Screen):
    """Settings form on nomadui.EditList."""

    def __init__(self, app, title, fields, values, on_change):
        super().__init__(app)
        self.title = title
        self.form = T.EditList(fields, values)
        self.on_change = on_change

    @property
    def text_entry(self):
        return self.form.editing

    def key(self, k) -> None:
        ev = self.form.handle(k)
        if ev == "back":
            self.app.pop()
        elif ev and (ev.startswith("changed:") or ev.startswith("action:")):
            self.on_change(ev.split(":", 1)[1], self.form.values)

    def render(self, surf, f) -> None:
        T.render(surf, self.title, self.form.view(T.ROWS), self.form.hint(), None if self.form.editing else
                 self.form.sel - self.form.top, "", f)


class FreqEntry(Screen):
    """Type a frequency in MHz (Enter tunes, Esc cancels)."""
    title = "Frequency"
    text_entry = True
    help = "Type the frequency in MHz, like 146.52 or 162.55, then OK. Esc cancels."

    def __init__(self, app, on_ok, start=""):
        super().__init__(app)
        self.buf = start
        self.on_ok = on_ok
        self.err = ""

    def key(self, k) -> None:
        if k.name == "enter":
            try:
                mhz = float(self.buf)
            except ValueError:
                self.err = "Type a number in MHz"
                return
            if mhz > 1766:
                self.err = "The dongle tunes up to 1766 MHz"
                return
            if mhz <= 0.5:
                self.err = "Too low"
                return
            self.app.pop()
            self.on_ok(mhz)
        elif k.name == "esc":
            self.app.pop()
        elif k.name == "backspace":
            self.buf = self.buf[:-1]
        elif k.char and (k.char.isdigit() or k.char == ".") and len(self.buf) < 12:
            self.buf += k.char
            self.err = ""

    def render(self, surf, f) -> None:
        rows = [T.RowView("MHz", self.buf, "editing", T.FG, len(self.buf)),
                ("", T.FG), (self.err or "Examples: 146.52  162.55  7.074", T.BAD if self.err else T.DIM)]
        T.render(surf, self.title, rows, "OK tune   ESC cancel", None, "", f)


class Radio_(Screen):
    """Main page: frequency, meter, band plan, spectrum."""
    help = HELP_RADIO

    def __init__(self, app):
        super().__init__(app)
        self.f_big = font(30)
        self.f_mid = font(17)
        self.f_sm = font(13)

    def keybar(self):
        a = self.app
        rec = ("Stop", T.BAD) if a.radio.rec else "Rec"
        return [a.cfg["mode"], "Freq", rec, "Band", "Menu"]

    def soft(self, k) -> bool:
        i = T.soft_index(k.char)
        a = self.app
        if i == 0:
            a.cycle_mode()
        elif i == 1:
            a.push(FreqEntry(a, a.tune))
        elif i == 2:
            a.toggle_rec()
        elif i == 3:
            a.open_bandplan()
        elif i == 4:
            a.open_menu()
        else:
            return False
        return True

    def key(self, k) -> None:
        a = self.app
        step = int(a.cfg.get("step", 12500))
        n = k.name
        if n == "up":
            a.tune(round((a.radio.freq * 1e6 + step) / step) * step / 1e6, keep_mode=True)
        elif n == "down":
            a.tune(round((a.radio.freq * 1e6 - step) / step) * step / 1e6, keep_mode=True)
        elif n in ("right", "left"):
            i = STEPS.index(step) if step in STEPS else 7
            i = min(len(STEPS) - 1, i + 1) if n == "right" else max(0, i - 1)
            a.set(step=STEPS[i])
            a.toast(f"Step {fmt_step(STEPS[i])}Hz")
        elif n == "enter":
            a.open_menu()
        elif n == "esc":
            a.quit = True
        elif k.char in ("r", "R"):
            a.reverse()
        elif k.char in ("m", "M"):
            a.save_memory()
        elif k.char in ("s", "S"):
            a.cycle_squelch()
        elif k.char in ("g", "G"):
            a.cycle_gain()
        elif k.char in ("-", "_"):
            a.volume(-0.1)
        elif k.char in ("=", "+"):
            a.volume(+0.1)
        elif k.char == " ":
            a.radio.mute = not a.radio.mute
            a.toast("Muted" if a.radio.mute else "Sound on")
        elif k.char in ("b", "B"):
            a.toggle_bias()

    def tick(self) -> None:
        r, a = self.app.radio, self.app
        if r.status == "No dongle" and not getattr(self, "_told", False):
            self._told = True
            a.show_steps(NO_DONGLE_STEPS)
        elif r.status != "No dongle":
            self._told = False
        self._tick()

    def _tick(self) -> None:
        self.app.dirty = True

    def render(self, surf, f) -> None:
        a = self.app
        r = a.radio
        rx = r.rx
        cfg = a.cfg
        surf.fill(T.BG)
        surf.fill(T.TITLE_BG, (0, 0, W, T.TITLE_H))
        title = T.title_image(APP_TITLE, f=f)
        surf.blit(title, (6, (T.TITLE_H - title.get_height()) // 2))
        tags = []
        if r.rec:
            tags.append(("REC", (255, 90, 90)))
        if cfg.get("bias_tee"):
            tags.append(("BIAS", T.WARN))
        x = 6 + title.get_width() + 8
        for t, c in tags:
            img = f["small"].render(t, True, c)
            surf.blit(img, (x, 6))
            x += img.get_width() + 8
        # frequency
        mhz = r.freq
        whole = f"{mhz:.6f}"
        main, sub = whole[:-3], whole[-3:]
        img = self.f_big.render(main, True, T.FG)
        surf.blit(img, (6, 28))
        img2 = self.f_mid.render(sub, True, T.DIM)
        surf.blit(img2, (8 + img.get_width(), 40))
        # mode / filter box
        mx = 228
        surf.fill(T.SEL_BG, (mx, 30, W - mx - 4, 18))
        mt = f["small"].render(f"{cfg['mode']} {fmt_bw(r.filter_for(cfg['mode']))}", True, (255, 255, 255))
        surf.blit(mt, (mx + (W - mx - 4 - mt.get_width()) // 2, 31))
        gain = "AGC" if cfg.get("gain") is None else f"{cfg['gain']:g}dB"
        st = f["tiny"].render(f"{fmt_step(int(cfg.get('step', 12500)))}  {gain}", True, T.DIM)
        surf.blit(st, (mx, 50))
        # meter
        y = 64
        snr = rx.level - rx.noise if rx.noise > -119 else 0.0
        frac = max(0.0, min(1.0, (snr + 3) / 50.0))
        surf.fill((30, 34, 46), (6, y + 2, 190, 10))
        col = T.OK if rx.sq_open else T.DIM
        surf.fill(col, (6, y + 2, int(190 * frac), 10))
        pk = max(0.0, min(1.0, (rx.peak - rx.noise + 3) / 50.0)) if rx.noise > -119 else 0
        surf.fill(T.WARN, (6 + int(190 * pk), y, 2, 14))
        if cfg.get("squelch") is not None and rx.noise > -119:
            sx = 6 + int(190 * max(0.0, min(1.0, (cfg["squelch"] - rx.noise + 3) / 50.0)))
            surf.fill((255, 255, 255), (sx, y, 1, 14))
        sq = "SQ off" if cfg.get("squelch") is None else ("open" if rx.sq_open else "closed")
        mt = f["tiny"].render(f"{snr:+.0f} dB  {sq}", True, T.FG)
        surf.blit(mt, (202, y))
        # band plan line(s)
        b, seg = B.find(mhz)
        line = B.label(mhz) or "Not a ham band"
        surf.blit(self._fit(line, W - 12, f), (6, 80))
        info, icol = "", T.DIM
        can = B.can_tx(mhz, cfg.get("license", "None"))
        if seg:
            if can is None:
                info, icol = B.who_text(seg.who), T.HINT_FG
            else:
                info, icol = ("You can transmit here", T.OK) if can else ("Receive only for your class", T.WARN)
        elif b and can is not None:
            info, icol = "Receive only (band gap)", T.WARN
        off = B.repeater_offset(mhz)
        if off is not None:
            info = (info + "  " if info else "") + f"Rptr in {fmt_mhz(mhz + off)} (R)"
        if r.direct:
            info = "HF direct sampling (V3)  " + info
        elif mhz < 24 and cfg.get("hf") == "off":
            info, icol = "HF is off: Settings > HF input", T.WARN
        if r.error:
            info, icol = r.error, T.BAD
        if info:
            surf.blit(self._fit(info, W - 12, f, small=True, color=icol), (6, 97))
        self.draw_spectrum(surf, 113, 138, f)
        if r.mute:
            img = f["small"].render("MUTED", True, T.WARN)
            surf.blit(img, (W - img.get_width() - 6, 80))
        T.draw_keybar(surf, self.keybar(), f)

    def _fit(self, text, width, f, small=False, color=None):
        fnt = f["small"] if small else f["row"]
        if fnt.size(text)[0] > width:
            fnt = f["small"] if not small else f["tiny"]
        img = fnt.render(text, True, color or T.FG)
        if img.get_width() > width:
            img = pygame.transform.smoothscale(img, (width, img.get_height()))
        return img

    def draw_spectrum(self, surf, y0, y1, f) -> None:
        rx = self.app.radio.rx
        h = y1 - y0
        surf.fill((10, 14, 24), (0, y0, W, h))
        spec = rx.spec
        lo = rx.noise_bin - 6 if rx.noise_bin > -159 else float(spec.min())
        span = 50.0
        import numpy as np
        v = np.clip((spec - lo) / span, 0, 1)
        hz_px = dsp.RATE * dsp.SPEC_BINS / dsp.N / dsp.SPEC_W
        cx = W // 2 + int((rx.offset + rx.shift) / hz_px)
        bw = self.app.radio.filter_for(self.app.cfg["mode"])
        half = max(1, int(bw / hz_px / 2))
        if rx.mode in ("USB", "CW"):
            surf.fill((24, 44, 90), (cx, y0, max(1, int(bw / hz_px)), h))
        elif rx.mode == "LSB":
            surf.fill((24, 44, 90), (cx - int(bw / hz_px), y0, max(1, int(bw / hz_px)), h))
        else:
            surf.fill((24, 44, 90), (cx - half, y0, 2 * half, h))
        for x in range(0, W):
            hh = int(v[x] * (h - 1))
            if hh:
                surf.fill((90, 200, 255), (x, y1 - hh, 1, hh))
        surf.fill((255, 200, 90), (cx + (int(dsp.CW_PITCH / hz_px) if rx.mode == "CW" else 0), y0, 1, h))


NO_DONGLE_STEPS = T.StepsPopup("Plug in the RTL-SDR", [
    "Plug the RTL-SDR dongle into this device's USB port (straight in or with a short cable).",
    "Screw an antenna onto its SMA jack (2 m / 70 cm for APRS and FM, a long wire for HF).",
    "Wait a few seconds: the radio starts by itself once the dongle shows up."],
    key="", ok="OK")

FOX_STEPS = T.StepsPopup("Fox hunt setup", [
    "Use a directional antenna (a small Yagi or tape-measure beam) on the RTL-SDR.",
    "Tune to the hidden transmitter's frequency with Up/Down or 8 Freq.",
    "Hold the antenna up, turn slowly and walk toward the strongest reading.",
    "Close in, lower the gain with 4 (or add an attenuator) so the dongle doesn't overload."],
    key="steps-fox", ok="OK start")

APRS_STEPS = T.StepsPopup("APRS setup", [
    "Use a 2 m antenna (vertical, outside or high up is best) on the RTL-SDR.",
    "For distances, plug in the GPS or set your grid in Settings.",
    "To see packets on the map, install Rig Companion: packets go to its APRS list too."],
    key="steps-aprs", ok="OK start")


class Fox(Screen):
    title = "Fox hunt"
    steps = FOX_STEPS
    help = ("Find a hidden transmitter. The big number is the signal above the noise (dB). "
            "Turn slowly with a directional antenna and walk toward the peak. Gain is fixed "
            "here so the number means something; lower it with 4 as you get close (strong "
            "signals overload the dongle). 6 turns the tone on/off: its pitch rises with the "
            "signal. 7 clears the peak. Up/Down tune.")

    def __init__(self, app):
        super().__init__(app)
        self.hist = []
        self.f_huge = font(44)
        self.f_mid = font(17)
        c = app.cfg
        self.saved = {"gain": c.get("gain"), "squelch": c.get("squelch"), "agc": c.get("agc", True)}
        g = c.get("gain")
        if g is None:
            g = 28.0
        app.set(gain=g, squelch=None, agc=False)
        app.radio.fox_tone = bool(c.get("fox_tone", True))
        self.best = -999.0

    def on_close(self) -> None:
        self.app.radio.fox_tone = False
        self.app.set(**self.saved)

    def keybar(self):
        return ["Gain-", "Gain+", ("Tone" if self.app.radio.fox_tone else "Tone"), "Peak 0", "Freq"]

    def soft(self, k) -> bool:
        i = T.soft_index(k.char)
        a = self.app
        if i in (0, 1):
            gs = a.radio.gains or [0, 10, 20, 30, 40, 49.6]
            g = a.cfg.get("gain") or 28.0
            j = min(range(len(gs)), key=lambda q: abs(gs[q] - g))
            j = max(0, j - 2) if i == 0 else min(len(gs) - 1, j + 2)
            a.set(gain=gs[j])
            self.best = -999.0
            a.toast(f"Gain {gs[j]:g} dB")
        elif i == 2:
            a.radio.fox_tone = not a.radio.fox_tone
            a.cfg["fox_tone"] = a.radio.fox_tone
            a.save()
        elif i == 3:
            self.best = -999.0
            a.radio.rx.peak = -120.0
        elif i == 4:
            a.push(FreqEntry(a, a.tune))
        else:
            return False
        return True

    def key(self, k) -> None:
        a = self.app
        step = int(a.cfg.get("step", 12500))
        if k.name == "up":
            a.tune((a.radio.freq * 1e6 + step) / 1e6, keep_mode=True)
        elif k.name == "down":
            a.tune((a.radio.freq * 1e6 - step) / 1e6, keep_mode=True)
        else:
            super().key(k)

    def tick(self) -> None:
        rx = self.app.radio.rx
        if rx.noise > -119:
            v = rx.level - rx.noise
            self.hist.append(v)
            self.hist = self.hist[-150:]
            self.best = max(self.best, v)
        self.app.dirty = True

    def render(self, surf, f) -> None:
        a = self.app
        rx = a.radio.rx
        surf.fill(T.BG)
        T.render(surf, self.title, [], "", None, "", f, self.keybar())
        top = T.TITLE_H
        v = self.hist[-1] if self.hist else 0.0
        trend = 0.0
        if len(self.hist) > 12:
            trend = sum(self.hist[-4:]) / 4 - sum(self.hist[-12:-8]) / 4
        img = self.f_huge.render(f"{v:+.0f}", True, T.FG)
        surf.blit(img, (8, top + 2))
        surf.blit(f["small"].render("dB", True, T.DIM), (12 + img.get_width(), top + 26))
        arrow = "rising" if trend > 1.5 else ("falling" if trend < -1.5 else "steady")
        acol = T.OK if trend > 1.5 else (T.WARN if trend < -1.5 else T.DIM)
        surf.blit(f["row"].render(arrow, True, acol), (150, top + 4))
        surf.blit(f["small"].render(f"peak {self.best:+.0f} dB" if self.best > -900 else "peak -", True, T.WARN),
                  (150, top + 24))
        g = a.cfg.get("gain")
        surf.blit(f["small"].render(f"{fmt_mhz(a.radio.freq)} MHz  {a.cfg['mode']}  gain {g:g}" if g is not None
                                    else f"{fmt_mhz(a.radio.freq)} MHz", True, T.HINT_FG), (8, top + 50))
        # bar
        y = top + 70
        frac = max(0.0, min(1.0, (v + 3) / 50))
        surf.fill((30, 34, 46), (8, y, W - 16, 12))
        surf.fill(T.OK, (8, y, int((W - 16) * frac), 12))
        if self.best > -900:
            px = 8 + int((W - 16) * max(0.0, min(1.0, (self.best + 3) / 50)))
            surf.fill(T.WARN, (px, y - 2, 2, 16))
        if rx.level > -3:
            surf.blit(f["small"].render("OVERLOAD: lower the gain (4)", True, T.BAD), (8, y + 14))
        # history
        hy0, hy1 = top + 88, H - T.KEYBAR_H - 2
        if len(self.hist) > 1 and hy1 > hy0:
            hh = hy1 - hy0
            pts = []
            n = len(self.hist)
            for i, val in enumerate(self.hist):
                x = 8 + int((W - 16) * i / 149)
                yv = hy1 - int(hh * max(0.0, min(1.0, (val + 3) / 50)))
                pts.append((x, yv))
            if n > 1:
                pygame.draw.lines(surf, (90, 200, 255), False, pts, 1)


class Aprs_(Screen):
    title = "APRS receive"
    steps = APRS_STEPS
    help = ("Decodes APRS on 144.390 MHz (North America) with the SDR. Every packet also "
            "goes to Rig Companion's APRS list, inbox, weather page and map (when Rig "
            "Companion is installed). Distances use the GPS or the grid in Settings. "
            "4 hear the channel, 5 clear the list, 7 switch frequency (144.390 / ISS 145.825 / "
            "Europe 144.800). OK opens a packet.")
    FREQS = [(144.390, "N. America"), (145.825, "ISS"), (144.800, "Europe")]

    def __init__(self, app):
        super().__init__(app)
        c = app.cfg
        self.saved = {k: c.get(k) for k in ("freq", "mode", "squelch", "agc")}
        self.fi = 0
        self.dec = P.Aprs(to_rig=bool(c.get("aprs_to_rig", True)))
        self.sel = 0
        self.top = 0
        self.detail = None
        ok = self.dec.start()
        app.radio.listeners.append(self.dec.feed)
        app.radio.mute = not c.get("aprs_audio", False)
        app.set(freq=self.FREQS[0][0], mode="NFM", squelch=None)
        cf = dict(c.get("filter", {}))
        self.saved_filter = cf.get("NFM")
        cf["NFM"] = 16000
        app.set(filter=cf)
        if not ok:
            app.toast(self.dec.error, 5)
        self.loc = app.location()

    def on_close(self) -> None:
        a = self.app
        if self.dec.feed in a.radio.listeners:
            a.radio.listeners.remove(self.dec.feed)
        self.dec.close()
        a.radio.mute = False
        cf = dict(a.cfg.get("filter", {}))
        if self.saved_filter:
            cf["NFM"] = self.saved_filter
        else:
            cf.pop("NFM", None)
        a.set(filter=cf, **self.saved)

    def keybar(self):
        return [("Mute" if not self.app.radio.mute else "Hear"), "Clear", None, "Freq", None]

    def soft(self, k) -> bool:
        i = T.soft_index(k.char)
        a = self.app
        if i == 0:
            a.radio.mute = not a.radio.mute
            a.cfg["aprs_audio"] = not a.radio.mute
            a.save()
        elif i == 1:
            with self.dec.lock:
                self.dec.packets.clear()
        elif i == 3:
            self.fi = (self.fi + 1) % len(self.FREQS)
            a.set(freq=self.FREQS[self.fi][0])
            a.toast(f"{self.FREQS[self.fi][0]:.3f} {self.FREQS[self.fi][1]}")
        elif i in (2, 4):
            return True
        else:
            return False
        return True

    def key(self, k) -> None:
        if self.detail is not None:
            if k.name in ("esc", "left", "enter"):
                self.detail = None
            return
        with self.dec.lock:
            pk = list(self.dec.packets)
        n = len(pk)
        if k.name == "up" and n:
            self.sel = max(0, self.sel - 1)
        elif k.name == "down" and n:
            self.sel = min(n - 1, self.sel + 1)
        elif k.name in ("enter", "right") and n:
            self.detail = pk[min(self.sel, n - 1)]
        elif k.name in ("esc", "left"):
            self.app.pop()

    def tick(self) -> None:
        self.app.dirty = True

    def where(self, p) -> str:
        if not p.get("pos"):
            return ""
        if not self.loc:
            return "position"
        d, brg = P.distance_km(self.loc, p["pos"])
        return f"{d:.0f} km {BEARINGS[int((brg + 22.5) // 45) % 8]}" if d >= 10 else f"{d:.1f} km {BEARINGS[int((brg + 22.5) // 45) % 8]}"

    def render(self, surf, f) -> None:
        a = self.app
        rx = a.radio.rx
        with self.dec.lock:
            pk = list(self.dec.packets)
            count = self.dec.count
        rig = self.dec.rig.ok if self.dec.rig else False
        if self.detail is not None:
            p = self.detail
            lines = [(p["src"] + "  " + p["kind"], T.OK), (time.strftime("%H:%M:%S", time.localtime(p["t"])) +
                                                           "  " + self.where(p), T.DIM)]
            lines += [(l, T.FG) for l in T.wrap_text(f["small"], p["raw"], W - 14)]
            surf.fill(T.BG)
            T.render(surf, p["src"], [], "ESC back", None, "", f)
            y = T.TITLE_H + 3
            for t, c in lines[:8]:
                surf.blit(f["small"].render(t, True, c), (7, y))
                y += 16
            return
        rows = []
        n_rows = T.ROWS_KEYS - 1
        self.sel = min(self.sel, max(0, len(pk) - 1))
        if self.sel < self.top:
            self.top = self.sel
        if self.sel >= self.top + n_rows:
            self.top = self.sel - n_rows + 1
        snr = rx.level - rx.noise if rx.noise > -119 else 0
        head = f"{a.radio.freq:.3f}  {count} packets"
        rows.append((head, T.HINT_FG, ("to Rig " if rig else "") + f"{snr:+.0f}dB"))
        for p in pk[self.top:self.top + n_rows]:
            rows.append((p["src"], T.FG, self.where(p) or p["kind"]))
        if not pk:
            rows.append(("Listening for packets...", T.DIM))
        T.render(surf, self.title, rows, "", (self.sel - self.top + 1) if pk else None, "", f, self.keybar())


class Recordings(ListScreen):
    help = ("Recordings are WAV files (32 kHz). OK or 4 plays one, 5 deletes it (press twice), "
            "6 shows a QR code for a web page to download them on a phone or laptop, 7 copies "
            "them all to a USB stick (folder Nomad-SDR). Settings > Record can record only "
            "while the squelch is open.")

    def __init__(self, app):
        super().__init__(app, "Recordings", self._items, "OK play  ESC back")
        self.play = None
        self.armed = None

    def _items(self):
        out = []
        for r in Fs.listing():
            out.append((Fs.label(r), "REC" if r["live"] else f"{r['seconds']:.0f}s",
                        (lambda r=r: self.toggle_play(r)), T.WARN if r["live"] else None))
        return out

    def keybar(self):
        return ["Stop" if self.play and self.play.poll() is None else "Play", "Delete", "QR page", "USB", None]

    def current(self):
        lst = Fs.listing()
        return lst[self.sel] if 0 <= self.sel < len(lst) else None

    def toggle_play(self, r) -> None:
        if self.play and self.play.poll() is None:
            self.stop()
            return
        if r and not r["live"]:
            self.app.radio.mute = True
            self.play = subprocess.Popen(["aplay", "-q", "-D", self.app.radio.out.device or "default", r["path"]],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self) -> None:
        if self.play:
            try:
                self.play.terminate()
            except OSError:
                pass
            self.play = None
        self.app.radio.mute = False

    def on_close(self) -> None:
        self.stop()

    def tick(self) -> None:
        if self.play and self.play.poll() is not None:
            self.stop()
            self.app.dirty = True

    def soft(self, k) -> bool:
        i = T.soft_index(k.char)
        a = self.app
        r = self.current()
        if i == 0:
            self.toggle_play(r)
        elif i == 1:
            if not r:
                return True
            if r["live"]:
                a.toast("Stop the recording first")
            elif self.armed == r["path"]:
                Fs.delete(r)
                self.armed = None
                a.toast("Deleted")
            else:
                self.armed = r["path"]
                a.toast("Press 5 again to delete", 3)
        elif i == 2:
            a.push(SharePage(a))
        elif i == 3:
            drives = Fs.usb_drives()
            if not drives:
                a.toast("Plug in a USB stick", 3)
            else:
                try:
                    c, s = Fs.copy_all(drives[0])
                    a.toast(f"Copied {c}, {s} already there", 4)
                except (OSError, RuntimeError) as e:
                    a.toast(str(e), 5)
        elif i == 4:
            return True
        else:
            return False
        return True


class SharePage(Screen):
    title = "Download page"
    help = "Scan the code with a phone on the same network to play or download recordings. ESC closes."

    def __init__(self, app):
        super().__init__(app)
        if not app.share.start():
            app.toast(app.share.error or "Web page failed", 4)

    def render(self, surf, f) -> None:
        a = self.app
        ip = Fs.my_ip()
        url = f"http://{ip}:{a.share.port}/" if ip else ""
        surf.fill(T.BG)
        T.render(surf, self.title, [], "ESC back", None, "", f)
        if not url:
            surf.blit(f["row"].render("No network connection", True, T.WARN), (8, 40))
            return
        side = qr.draw(surf, url, W - 126, T.TITLE_H + 2, 122)
        y = T.TITLE_H + 8
        for t, c in (("Scan to open", T.OK), (f"{ip}", T.FG), (f":{a.share.port}", T.FG),
                     (f"{len(Fs.listing())} recordings", T.DIM), ("Same Wi-Fi or LAN", T.DIM)):
            surf.blit(f["small"].render(t, True, c), (8, y))
            y += 19
        _ = side


class Info(Screen):
    """Full-screen help or About (nomadui views)."""

    def __init__(self, app, view):
        super().__init__(app)
        self.view = view

    def key(self, k) -> None:
        if self.view.handle(k) == "close":
            self.app.pop()

    def render(self, surf, f) -> None:
        self.view.render(surf, f)


# ---------------------------------------------------------------------------- app

class App:
    def __init__(self, panel, kb, radio=None):
        self.panel = panel
        self.kb = kb
        self.cfg = S.load()
        self.radio = radio or Radio(self.cfg)
        self.radio.cfg = self.cfg
        self.stack: list[Screen] = []
        self.helpview = None
        self.steps = None                 # nomadui.StepsPopup shown over the screen
        self.quit = False
        self.dirty = True
        self.msg = ""
        self.msg_until = 0.0
        self.share = Fs.Share()
        self._gps = None
        T.set_about(APP_TITLE, __version__)
        T.set_popups("nomad-sdr")

    # -- helpers
    def push(self, s: Screen) -> None:
        self.stack.append(s)
        self.dirty = True
        self.show_steps(getattr(s, "steps", None))

    def show_steps(self, popup) -> None:
        """Setup steps the user does by hand, over the current screen (not
        once the user hid that popup with D)."""
        if popup is not None and not T.steps_hidden(popup.key):
            popup.top = 0
            self.steps = popup
            self.dirty = True

    def pop(self) -> None:
        if self.stack:
            s = self.stack.pop()
            s.on_close()
        if not self.stack:
            self.quit = True
        self.dirty = True

    def toast(self, text: str, secs: float = 2.0) -> None:
        self.msg, self.msg_until = text, time.monotonic() + secs
        self.dirty = True

    def save(self) -> None:
        try:
            S.save(self.cfg)
        except OSError:
            pass

    def set(self, **kw) -> None:
        self.radio.set(**kw)
        self.save()

    def location(self):
        try:
            from . import nomadgps
            if self._gps is None:
                self._gps = nomadgps.shared()
            loc = self._gps.location()
            if loc:
                return loc[0], loc[1]
        except Exception:   # noqa: BLE001  GPS is optional
            pass
        return grid_to_latlon(self.cfg.get("grid", ""))

    def tune(self, mhz: float, keep_mode: bool = False) -> None:
        mhz = max(0.5, min(1766.0, mhz))
        kw = {"freq": round(mhz, 6)}
        if not keep_mode:
            kw["mode"] = B.default_mode(mhz)
            st = 12500 if mhz >= 30 else 1000
            if 88 <= mhz < 108:
                st = 100000
            elif 118 <= mhz < 137:
                st = 25000
            kw["step"] = st
        self.set(**kw)

    def cycle_mode(self) -> None:
        m = dsp.MODES
        i = (m.index(self.cfg["mode"]) + 1) % len(m) if self.cfg["mode"] in m else 0
        self.set(mode=m[i])
        self.toast(f"Mode {m[i]}")

    def cycle_squelch(self) -> None:
        rx = self.radio.rx
        cur = self.cfg.get("squelch")
        base = round(rx.noise + 6) if rx.noise > -119 else -60
        opts = [None, base, base + 6, base + 12]
        if cur is None:
            nxt = base
        else:
            higher = [o for o in opts[1:] if o > cur + 0.5]
            nxt = higher[0] if higher and cur < base + 12 else None
        self.set(squelch=nxt)
        self.toast("Squelch off" if nxt is None else f"Squelch {nxt - (rx.noise if rx.noise > -119 else 0):+.0f} dB over noise")

    def cycle_gain(self) -> None:
        gs = self.radio.gains or [0.0, 10.0, 20.0, 30.0, 40.0, 49.6]
        picks = [None] + [g for g in gs if g in (0.0, 9.7, 19.7, 29.7, 38.6, 49.6)]
        if len(picks) < 3:
            picks = [None] + gs[::5]
        cur = self.cfg.get("gain")
        i = picks.index(cur) if cur in picks else 0
        nxt = picks[(i + 1) % len(picks)]
        self.set(gain=nxt)
        self.toast("Gain auto" if nxt is None else f"Gain {nxt:g} dB")

    def volume(self, d: float) -> None:
        v = round(max(0.0, min(1.5, float(self.cfg.get("volume", 0.6)) + d)), 2)
        self.set(volume=v)
        self.toast(f"Volume {int(v * 100)}%")

    def toggle_bias(self) -> None:
        on = not self.cfg.get("bias_tee")
        self.set(bias_tee=on)
        self.toast("Bias-tee ON: 4.5 V on the antenna jack" if on else "Bias-tee off", 4)

    def toggle_rec(self) -> None:
        if self.radio.rec:
            p = self.radio.stop_rec()
            self.toast(f"Saved {os.path.basename(p)[:30]}", 3)
        else:
            try:
                self.radio.start_rec()
                self.toast("Recording")
            except OSError as e:
                self.toast(f"Can't record: {e}", 4)

    def reverse(self) -> None:
        off = B.repeater_offset(self.radio.freq)
        if off is None:
            self.toast("Not a repeater output")
            return
        self.tune(self.radio.freq + off, keep_mode=True)
        self.toast(f"Repeater input {fmt_mhz(self.radio.freq)}")

    def save_memory(self) -> None:
        mems = list(self.cfg.get("memories", []))
        f = round(self.radio.freq, 6)
        if any(abs(m["freq"] - f) < 1e-6 for m in mems):
            self.toast("Already saved")
            return
        name = (B.label(f) or "Memory").split("  ")[-1][:22]
        mems.insert(0, {"name": f"{fmt_mhz(f)} {name}", "freq": f, "mode": self.cfg["mode"]})
        self.set(memories=mems[:100])
        self.toast("Saved to Memories")

    # -- screens
    def open_menu(self) -> None:
        def items():
            c = self.cfg
            g = c.get("gain")
            sq = c.get("squelch")
            return [
                ("Mode", c["mode"], self.cycle_mode, None),
                ("Filter", fmt_bw(self.radio.filter_for(c["mode"])), self.cycle_filter, None),
                ("Gain", "Auto" if g is None else f"{g:g} dB", self.cycle_gain, None),
                ("Squelch", "Off" if sq is None else f"{sq:.0f} dBFS", self.cycle_squelch, None),
                ("Volume", f"{int(float(c.get('volume', 0.6)) * 100)}%", lambda: self.volume(
                    0.1 if float(c.get("volume", 0.6)) < 1.45 else -1.5), None),
                ("Band plan", "", self.open_bandplan, None),
                ("Memories & presets", "", self.open_memories, None),
                ("APRS receive", "", lambda: self.push(Aprs_(self)), None),
                ("Fox hunt", "", lambda: self.push(Fox(self)), None),
                ("Recordings", str(len(Fs.listing())), lambda: self.push(Recordings(self)), None),
                ("Settings", "", self.open_settings, None),
                ("About", "", lambda: self.push(Info(self, T.AboutView())), None),
            ]
        s = ListScreen(self, "Menu", items, "↑↓ move  OK change/open  ESC back")
        s.help = "Mode, filter, gain, squelch and volume change with OK. The rest open a screen."
        self.push(s)

    def cycle_filter(self) -> None:
        m = self.cfg["mode"]
        ch = dsp.FILTERS[m]
        cur = self.radio.filter_for(m)
        nxt = ch[(ch.index(cur) + 1) % len(ch)] if cur in ch else ch[0]
        flt = dict(self.cfg.get("filter", {}))
        flt[m] = nxt
        self.set(filter=flt)

    def open_bandplan(self) -> None:
        def bands():
            out = []
            lic = self.cfg.get("license", "None")
            for b in B.BANDS:
                tag = "HF" if b.hf else ""
                if lic != "None":
                    ok = any(B.SHORT[lic] in s.who for s in b.segs)
                    tag = (tag + " " if tag else "") + ("TX" if ok else "RX")
                out.append((f"{b.name}  {b.lo:g}-{b.hi:g}", tag, (lambda b=b: self.open_band(b)),
                            T.DIM if b.hf and self.cfg.get("hf") == "off" else None))
            return out
        s = ListScreen(self, "Band plan", bands)
        s.help = ("US amateur bands. TX/RX shows whether your class (Settings) may transmit "
                  "somewhere in the band. HF bands need the V3's direct sampling (Settings > HF input) "
                  "or a V4 / upconverter. OK opens a band.")
        b, _ = B.find(self.radio.freq)
        if b:
            s.sel = B.BANDS.index(b)
        self.push(s)

    def open_band(self, band) -> None:
        def segs():
            lic = self.cfg.get("license", "None")
            out = []
            for sg in band.segs:
                if lic != "None":
                    val, col = ("TX" if B.SHORT[lic] in sg.who else "RX"), None
                else:
                    val, col = "".join(sg.who), None
                out.append((f"{sg.lo:g} {sg.use}", val, (lambda sg=sg: self._tune_seg(sg)), col))
            return out
        s = ListScreen(self, band.name + ("  " + band.note if band.note else ""), segs, "OK tune  ESC back")
        s.help = ("Each line starts at its lowest frequency. T G E = Technician, General, Extra may "
                  "transmit; with a class set it says TX or RX. OK tunes to the calling or usual "
                  "frequency with the right mode.")
        self.push(s)

    def _tune_seg(self, sg) -> None:
        self.tune(sg.call or sg.lo)
        self.set(mode=sg.mode)
        while len(self.stack) > 1:
            self.pop()

    def open_memories(self) -> None:
        def items():
            out = []
            for m in self.cfg.get("memories", []):
                out.append((m["name"], m["mode"], (lambda m=m: self._tune_mem(m)), T.OK))
            for name, mhz, mode in B.PRESETS:
                out.append((name, mode, (lambda mhz=mhz, mode=mode: self._tune_mem({"freq": mhz, "mode": mode})), None))
            return out
        s = ListScreen(self, "Memories & presets", items, "OK tune  ESC back")
        s.help = ("Your memories (green) come first, then presets. M on the Radio screen saves the "
                  "current frequency. 5 deletes the selected memory (press twice).")
        armed = {"i": None}

        def soft(k):
            i = T.soft_index(k.char)
            if i == 1:
                mems = list(self.cfg.get("memories", []))
                if s.sel < len(mems):
                    if armed["i"] == s.sel:
                        mems.pop(s.sel)
                        self.set(memories=mems)
                        armed["i"] = None
                        self.toast("Memory deleted")
                    else:
                        armed["i"] = s.sel
                        self.toast("Press 5 again to delete", 3)
                return True
            return i is not None
        s.soft = soft
        s.keybar = lambda: [None, "Delete" if s.sel < len(self.cfg.get("memories", [])) else None, None, None, None]
        self.push(s)

    def _tune_mem(self, m) -> None:
        self.tune(m["freq"])
        self.set(mode=m["mode"])
        while len(self.stack) > 1:
            self.pop()

    def open_settings(self) -> None:
        c = self.cfg
        hf_names = {"direct": "Direct sampling (V3)", "upconverter": "Upconverter", "off": "Off"}
        rev = {v: k for k, v in hf_names.items()}
        F = T.Field
        fields = [
            F("bias_tee", "Bias-tee (antenna power)", "bool"),
            F("hf", "HF input", "choice", list(hf_names.values())),
            F("upconverter_mhz", "Upconverter MHz", "number", visible=lambda v: v["hf"] == "Upconverter"),
            F("ppm", "PPM correction", "text", maxlen=4),
            F("license", "My license class", "choice", B.CLASSES),
            F("grid", "My grid (no GPS)", "text", maxlen=6),
            F("rec_squelch", "Record", "bool", ("Squelch open", "Always")),
            F("agc", "AGC (AM/SSB/CW)", "bool"),
            F("aprs_to_rig", "APRS to Rig Companion", "bool"),
        ]
        vals = {"bias_tee": bool(c.get("bias_tee")), "hf": hf_names.get(c.get("hf"), "Off"),
                "upconverter_mhz": f"{float(c.get('upconverter_mhz', 125)):g}", "ppm": str(c.get("ppm", 0)),
                "license": c.get("license", "None"), "grid": c.get("grid", ""),
                "rec_squelch": bool(c.get("rec_squelch")), "agc": bool(c.get("agc", True)),
                "aprs_to_rig": bool(c.get("aprs_to_rig", True))}

        def changed(key, v):
            if key == "bias_tee":
                self.set(bias_tee=v[key])
                self.toast("Bias-tee ON: only with an LNA or active antenna" if v[key] else "Bias-tee off", 4)
            elif key == "hf":
                self.set(hf=rev[v[key]])
            elif key == "upconverter_mhz":
                try:
                    self.set(upconverter_mhz=float(v[key]))
                except ValueError:
                    pass
            elif key == "ppm":
                try:
                    self.set(ppm=max(-200, min(200, int(v[key]))))
                except ValueError:
                    self.toast("PPM: a whole number like -2")
            elif key == "grid":
                g = v[key].strip().upper()
                self.set(grid=g)
                if g and not grid_to_latlon(g):
                    self.toast("Grid like EN34 or EN34fx")
            else:
                self.set(**{key: v[key]})
        s = FormScreen(self, "Settings", fields, vals, changed)
        s.help = ("Bias-tee puts 4.5 V on the antenna jack for an LNA or active antenna; never use it "
                  "with a DC-shorted antenna. HF input: the V3 hears 0.5-28.8 MHz by direct sampling "
                  "(Q branch); choose Upconverter for a Ham It Up style converter. PPM fixes the "
                  "dongle's frequency error. Your license class marks where you may transmit.")
        self.push(s)

    # -- loop
    def run(self) -> None:
        A.recover()
        self.t_start = time.monotonic()
        self.radio.start()
        self.push(Radio_(self))
        last = 0.0
        while not self.quit:
            keys = self.kb.poll(0.08) if self.kb else (time.sleep(0.08) or [])
            for k in keys:
                if k.name == "exit":
                    self.quit = True
                    break
                self.dispatch(k)
                self.dirty = True
                if self.quit:
                    break
            if self.stack:
                self.stack[-1].tick()
            now = time.monotonic()
            if (self.dirty and now - last > 0.12) or now - last > 1.0:
                self.draw()
                last = now
                self.dirty = False
        self.close()

    def dispatch(self, k) -> None:
        if not self.stack:
            return
        top = self.stack[-1]
        if self.helpview is not None:
            if self.helpview.handle(k) == "close":
                self.helpview = None
            return
        if self.steps is not None:
            if self.steps.handle(k) == "close":
                self.steps = None
            return
        if k.name == "help":
            text = top.help or HELP_RADIO
            st = getattr(top, "steps", None)
            if st is not None:
                text = st.title + "\n" + st.help_text() + "\n\n" + text
            self.helpview = T.HelpView(top.title, text, top.keybar())
            return
        if not top.text_entry and top.soft(k):
            return
        if not top.text_entry:
            k.name = T.nav_name(k.name, k.char, k.ctrl)
        top.key(k)

    def draw(self) -> None:
        surf, f = self.panel.surf, self.panel.fonts
        if self.helpview is not None:
            self.helpview.render(surf, f)
        elif self.stack:
            self.stack[-1].render(surf, f)
            if self.radio.status == "Starting" and not self.radio.error:
                # opening the dongle takes a moment: never look locked up
                T.draw_loading(surf, "Starting the RTL-SDR", self.t_start, f)
                self.dirty = True
            elif self.steps is not None:
                self.steps.render(surf, f)
        if self.msg and time.monotonic() < self.msg_until:
            img = f["small"].render(self.msg, True, (255, 255, 255))
            w = min(W - 8, img.get_width() + 12)
            y = H - T.KEYBAR_H - 24
            surf.fill((50, 56, 80), ((W - w) // 2, y, w, 21))
            surf.blit(img, ((W - w) // 2 + 6, y + 2), (0, 0, w - 12, 20))
        self.panel.flush()

    def close(self) -> None:
        while self.stack:
            self.stack.pop().on_close()
        self.share.stop()
        self.radio.stop()
        self.save()


def main_ui(seconds: float = 0) -> int:
    panel = L.Panel()
    kb = Keyboard()
    app = App(panel, kb)

    def on_term(*_):
        app.quit = True
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGHUP, on_term)
    if seconds:
        signal.signal(signal.SIGALRM, on_term)
        signal.alarm(int(seconds))
    try:
        app.run()
    except KeyboardInterrupt:
        app.close()
    finally:
        panel.draw(APP_TITLE, [("Bye!", T.OK)], "")
        kb.close()
    return 0
