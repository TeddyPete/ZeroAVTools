"""Nomad Packet Capture: the app. Screens on the LCD, input from the keyboard."""
from __future__ import annotations

import os
import signal
import threading
import time

from . import __version__
from . import capture as C
from . import files as F
from . import lcd as L
from . import nomadui as T
from . import netinfo, pcapread, qr, tap, viewer, web
from . import settings as S
from .keys import Keyboard
from .ui import BACK, DOWN, SELECT, UP, Busy, Confirm, Form, Menu, Message, Prompt, Screen, nav_key, wrap

APP_TITLE = "Nomad Packet Capture"
T.set_about(APP_TITLE, __version__)
T.set_popups("nomad-packet-capture")

HELP = (
    "Two modes:\n"
    "Mirror port: plug the Ethernet port into a switch mirror (SPAN) port. On a normal port "
    "you only see broadcast, multicast and this device's own traffic.\n"
    "Inline tap: plug a USB Ethernet adapter in and put the CardputerZero in the cable "
    "between two devices (built-in port to one, adapter to the other). It passes traffic "
    "like a cable (100 Mb/s max, no IP) and records it. With Auto tap on, plugging the "
    "adapter in starts it, even with the app closed. Unplugging the adapter stops it.\n"
    "Captures keep running if you leave the app and stop at their limits. Files are saved "
    "as they record (synced every 5 s), so a power cut loses only the last seconds.\n"
    "Get files off: Share files shows a QR code for a phone or laptop browser (download or "
    "delete there), or plug in a USB stick and copy. Open them in Wireshark.\n"
    "With HDMI connected, a live packet viewer opens on the desktop.\n"
    "TheTechNomad.com"
)


def hms(s: float) -> str:
    s = int(s or 0)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def rate(bps: float) -> str:
    if bps >= 1e6:
        return f"{bps / 1e6:.1f} Mb/s"
    return f"{bps / 1e3:.0f} kb/s"


class App:
    def __init__(self, panel: L.Panel, kb: Keyboard | None):
        self.panel, self.kb = panel, kb
        self.cfg = S.load()
        self.stack: list[Screen] = []
        self.steps = None                 # nomadui.StepsPopup shown over the screen
        self.helpview = None    # Fn+H help view while it is shown
        self.dirty = True
        self.quit = False
        self.state: dict = C.read_state()
        self.share = web.Share()
        self.follower = pcapread.Follower()
        self._follow_file = None
        self._wake = threading.Event()
        self.drives: set = set(F.usb_drives())
        self.usb_offered: set = set()
        self.viewer_for = None          # capture file the viewer was opened for
        self.viewer_at = 0.0

    # ---- stack ----
    def push(self, s: Screen) -> None:
        self.stack.append(s)
        st = getattr(s, "steps", None)       # setup steps the user must do by hand
        if st is not None and not T.steps_hidden(st.key):
            st.top = 0
            self.steps = st
        self.dirty = True

    def show_steps(self, popup) -> None:
        """Show setup steps (nomadui.StepsPopup) over the current screen,
        unless the user hid that popup with D."""
        if popup is not None and not T.steps_hidden(popup.key):
            popup.top = 0
            self.steps = popup
            self.dirty = True

    def pop(self, s: Screen | None = None) -> None:
        if s is None:
            if len(self.stack) > 1:
                self.stack.pop()
        elif s in self.stack:
            self.stack.remove(s)
        self.dirty = True

    def open_about(self) -> None:
        """The About screen (shown like Fn+H help, ESC closes it)."""
        self.helpview = T.AboutView()
        self.dirty = True

    def wake(self) -> None:
        self._wake.set()
        self.dirty = True

    @property
    def capturing(self) -> bool:
        return bool(self.state.get("running"))

    def badge(self) -> str:
        if self.capturing:
            return f"● REC {hms(self.state.get('elapsed', 0))}"
        return "WEB ON" if self.share.on else ""

    def run_task(self, title, label, fn, after=None):
        def done(result, error):
            if error is not None:
                self.push(Message(self, "Problem", str(error), L.BAD))
            elif after:
                after(result)
            elif result:
                self.push(Message(self, title, str(result), L.OK))
        self.push(Busy(self, title, label, fn, done))

    def save_cfg(self) -> None:
        try:
            S.save(self.cfg)
        except OSError as e:
            self.push(Message(self, "Problem", f"Could not save settings: {e}", L.BAD))

    # ---- capture ----
    def start_capture(self) -> None:
        if self.capturing:
            self.push(LiveView(self))
            return
        if self.cfg.get("capmode") == "Inline tap":
            ad = self.cfg.get("tap_adapter") or tap.adapter_in_use() or (tap.adapters() or [""])[0]
            err = tap.why_not(ad)
            if err:
                self.push(Message(self, "Inline tap", err, L.BAD))
                return

            def started_tap(job):
                self.follower.reset()
                self._follow_file = None
                self.state = C.read_state()
                self.push(LiveView(self))
                self.show_steps(mode_steps("Inline tap"))
            self.run_task("Inline tap", "Bridging + starting",
                          lambda: C.start(dict(self.cfg, tap_adapter=ad)), after=started_tap)
            return
        iface = self.cfg["iface"]
        link = netinfo.link(iface)

        def go():
            def started(job):
                self.follower.reset()
                self._follow_file = None
                self.state = C.read_state()
                self.push(LiveView(self))
                self.show_steps(mode_steps("Mirror port"))
            self.run_task("Start capture", "Starting", lambda: C.start(self.cfg), after=started)

        if link["exists"] and not link["up"]:
            self.push(Confirm(self, "No link", f"{iface} has no link (no cable or switch port off). "
                                               "Start anyway? It will capture once the link comes up.",
                              go, yes="OK start", no="ESC cancel"))
        else:
            go()

    def stop_capture(self) -> None:
        def done(st):
            self.state = st
            self.dirty = True
        self.run_task("Stop capture", "Stopping", C.stop, after=done)

    def poll(self) -> None:
        st = C.read_state()
        was = self.capturing
        self.state = st
        if st.get("running"):
            files = st.get("files") or []
            cur = files[-1] if files else None
            if cur != self._follow_file and cur:
                if self._follow_file is None:
                    self.follower.reset()
                self._follow_file = cur
            self.follower.poll(cur)
        elif was:
            self._follow_file = None
        self.dirty = True
        self._check_usb()
        self._check_viewer()

    def _check_usb(self) -> None:
        """Offer to copy captures when a USB stick is plugged in (once per stick)."""
        drives = set(F.usb_drives())
        new = [d for d in drives if d not in self.usb_offered]
        self.drives = drives
        self.usb_offered &= drives            # forget sticks that were removed
        if not new or not self.stack or isinstance(self.stack[-1], (Busy, UsbFound)):
            return
        if self.stack[-1].text_entry:
            return
        mnt = new[0]
        self.usb_offered.add(mnt)
        todo = F.not_on_stick(mnt)
        if todo:
            self.push(UsbFound(self, mnt, todo))

    def _check_viewer(self) -> None:
        """Open the desktop viewer once per capture when HDMI + desktop are up."""
        if not self.cfg.get("viewer") or not self.capturing:
            return
        job = (self.state.get("job") or {}).get("file")
        now = time.monotonic()
        if job == self.viewer_for or now - self.viewer_at < 5:
            return
        self.viewer_at = now
        try:
            if viewer.launch():
                self.viewer_for = job
        except OSError:
            pass

    # ---- main loop ----
    def run(self) -> None:
        self.push(Home(self))
        last_draw = last_poll = 0.0
        while not self.quit:
            keys = self.kb.poll(0.1) if self.kb else (time.sleep(0.1) or [])
            for k in keys:
                if k.name == "exit":     # Esc held: leave the app (key spec)
                    self.quit = True
                    break
                if self.stack:
                    self.dispatch(k)
                self.dirty = True
            if not self.stack:
                break
            now = time.monotonic()
            if now - last_poll > 0.5:
                self.poll()
                last_poll = now
            self.stack[-1].tick()
            if self.dirty or now - last_draw > 1.0:
                self.draw()
                last_draw = now
                self.dirty = False

    def dispatch(self, k) -> None:
        """One key: Fn+H help, number keys 4..8, then the screen itself."""
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
            text = top.help or HELP
            if getattr(top, "steps", None) is not None:
                text = top.steps.title + "\n" + top.steps.help_text() + "\n\n" + text
            self.helpview = T.HelpView(top.title, text, top.keybar())
            return
        if not top.text_entry and top.soft(k):
            return
        top.key(k if top.text_entry else nav_key(k))

    def draw(self) -> None:
        if not self.stack:
            return
        surf, f = self.panel.surf, self.panel.fonts
        if self.helpview is not None:
            self.helpview.render(surf, f)
            self.panel.flush()
            return
        s = self.stack[-1]
        busy = s if isinstance(s, Busy) else None
        if busy is not None and len(self.stack) > 1:
            s = self.stack[-2]      # the Loading popup sits over the screen it came from
        rows, sel = s.rows()
        T.render(surf, s.title, rows, s.hint, sel, s.right(), f, s.keybar())
        if hasattr(s, "overlay"):
            s.overlay(surf)
        if busy is not None:
            T.draw_loading(surf, busy.label, busy.t0, f)
        elif self.steps is not None:
            self.steps.render(surf, f)
        self.panel.flush()


# ---------------------------------------------------------------------------

class Home(Menu):
    def __init__(self, app):
        self.app = app
        c = app.cfg
        items = [
            (lambda: "Stop capture" if app.capturing else "Start capture", self.start_stop,
             lambda: hms(app.state.get("elapsed", 0)) if app.capturing else ready_text(app),
             lambda: L.BAD if app.capturing else L.OK),
            ("Live view", lambda: app.push(LiveView(app)), lambda: "REC" if app.capturing else ""),
            ("Mode", lambda: app.push(ModeMenu(app)),
             lambda: ("Tap " + ("ON" if tap.bridge_up() else "")).strip() if c.get("capmode") == "Inline tap"
             else "Mirror port"),
            ("Filter", lambda: app.push(FilterMenu(app)), lambda: C.PRESET[c["preset"]][2] +
             (" +" if c.get("host") or c.get("port") else "")),
            ("Settings", lambda: open_settings(app), lambda: limits_text(c)),
            ("Saved captures", lambda: app.push(FilesMenu(app)), lambda: str(len(F.listing()))),
            ("Share files (QR)", lambda: app.push(ShareScreen(app)), lambda: "ON" if app.share.on else ""),
            ("Help", lambda: app.push(Message(app, "Help", HELP))),
            ("About", lambda: app.open_about()),
        ]
        super().__init__(app, APP_TITLE, items, "↑↓ move  OK open  hold ESC exit")

    def softkeys(self):
        app = self.app
        return [
            (lambda: ("Stop", L.BAD) if app.capturing else ("Start", L.OK), self.start_stop),
            ("Live", lambda: app.push(LiveView(app))),
            ("Files", lambda: app.push(FilesMenu(app))),
            (lambda: ("Share", L.OK) if app.share.on else "Share", lambda: app.push(ShareScreen(app))),
            ("Filter", lambda: app.push(FilterMenu(app))),
        ]

    @property
    def title(self):
        return "Packet Capture" if self.app.capturing else APP_TITLE

    @title.setter
    def title(self, v):
        pass

    def start_stop(self):
        if self.app.capturing:
            self.app.stop_capture()
        else:
            self.app.start_capture()

    def key(self, k):
        if k.name == "left":
            return
        if k.name == "esc":
            if self.app.capturing:
                self.app.push(LeaveMenu(self.app))
            else:
                self.app.quit = True
            return
        super().key(k)


class LeaveMenu(Menu):
    def __init__(self, app):
        def keep():
            app.quit = True

        def stop_quit():
            C.stop()
            app.quit = True
        super().__init__(app, "Capture running", [
            ("Keep capturing, quit app", keep),
            ("Stop capture and quit", stop_quit, "", L.BAD),
            ("Back to the app", lambda: app.pop(self)),
        ])


def limits_text(c: dict) -> str:
    d = c["duration"] if c["duration"] != "No limit" else ""
    s = ("ring " if c["mode"] == "Ring buffer" else "") + c["size"].replace(" ", "")
    return " ".join(x for x in (d.replace(" ", ""), s) if x)


class FilterMenu(Menu):
    def __init__(self, app):
        self.app = app
        items = []
        for key, label, short, expr, help_ in C.PRESETS:
            items.append((label, (lambda k=key: self.pick(k)),
                          (lambda k=key: "✓" if app.cfg["preset"] == k else ""),
                          (lambda k=key: L.OK if app.cfg["preset"] == k else L.FG)))
        items.append(("Host IP", self.host, lambda: app.cfg.get("host") or "any"))
        items.append(("Port", self.port, lambda: str(app.cfg.get("port") or "any")))
        items.append(("Desktop view filter", self.view, lambda: (app.cfg.get("view_filter") or "none")[:12]))
        super().__init__(app, "Filter", items, "↑↓ move  OK pick  ESC back")
        self.sel = next((i for i, p in enumerate(C.PRESETS) if p[0] == app.cfg["preset"]), 0)

    @property
    def hint(self):
        if self.sel < len(C.PRESETS):
            return C.PRESETS[self.sel][4]
        return "OK edit  ESC back"

    @hint.setter
    def hint(self, v):
        pass

    def pick(self, key):
        app = self.app
        if key == "custom":
            def done(v):
                v = v.strip()
                err = C.check_filter(v)
                if err:
                    app.push(Message(app, "Filter problem", err, L.BAD))
                    return
                app.cfg["custom"] = v
                app.cfg["preset"] = "custom" if v else "all"
                app.save_cfg()
            app.push(Prompt(app, "Custom filter", "Filter", app.cfg.get("custom", ""), done,
                            help_text="tcpdump syntax, e.g. udp port 5004 or host 10.0.0.5"))
            return
        app.cfg["preset"] = key
        app.save_cfg()
        app.pop(self)

    def view(self):
        app = self.app

        def done(v):
            app.cfg["view_filter"] = v.strip()
            app.save_cfg()
        app.push(Prompt(app, "Desktop view filter", "Show", app.cfg.get("view_filter", ""), done,
                        help_text="Words to show in the HDMI viewer, e.g. ptp !follow_up. Doesn't change the capture."))

    def host(self):
        app = self.app

        def done(v):
            v = v.strip()
            if v and not C.valid_ip(v):
                app.push(Message(app, "Host IP", f"'{v}' is not an IP address like 192.168.1.20", L.BAD))
                return
            app.cfg["host"] = v
            app.save_cfg()
        app.push(Prompt(app, "Host IP", "IP", app.cfg.get("host", ""), done,
                        help_text="Only packets to or from this IP. Blank = any."))

    def port(self):
        app = self.app

        def done(v):
            v = v.strip()
            if v and not (v.isdigit() and 0 < int(v) < 65536):
                app.push(Message(app, "Port", "Use a number from 1 to 65535.", L.BAD))
                return
            app.cfg["port"] = v
            app.save_cfg()
        app.push(Prompt(app, "Port", "Port", str(app.cfg.get("port", "")), done,
                        help_text="Only this TCP/UDP port (e.g. 41794 Crestron). Blank = any."))


def open_settings(app):
    c = app.cfg
    fields = [
        ("iface", "Capture port", "choice", netinfo.names()),
        ("tag", "Name tag", "text", "added to file names"),
        ("duration", "Stop after", "choice", C.DURATIONS),
        ("size", "Max size", "choice", C.SIZES),
        ("mode", "When full", "choice", ["Stop at limit", "Ring buffer"]),
        ("snap", "Save", "choice", ["Full packets", "Headers only"]),
        ("promisc", "Promiscuous", "bool"),
        ("host", "Host IP", "ip", "blank = any"),
        ("port", "Port", "number", "blank = any"),
        ("custom", "Custom filter", "text", "tcpdump syntax"),
        ("autotap", "Auto tap on USB", "bool"),
        ("viewer", "HDMI live viewer", "bool"),
    ]

    def validate(v):
        if v.get("host") and not C.valid_ip(v["host"]):
            return f"Host IP '{v['host']}' is not an IP address."
        if v.get("port") and not (str(v["port"]).isdigit() and 0 < int(v["port"]) < 65536):
            return "Port must be 1 to 65535."
        if v.get("custom"):
            err = C.check_filter(v["custom"])
            if err:
                return f"Custom filter: {err}"
        return None

    def save(v):
        c.update(v)
        if c["preset"] == "custom" and not c.get("custom"):
            c["preset"] = "all"
        app.save_cfg()

    form = Form(app, "Capture settings", fields, {k: c.get(k) for k, *_ in fields}, save, validate)
    for f in form.form.fields:
        if f.key in ("custom", "tag"):
            f.maxlen = 200 if f.key == "custom" else 24
    app.push(form)


class LiveView(Screen):
    title = "Live view"
    help = ("Packets, size, rate and drops of the capture, then the top protocols and "
            "senders. OK or key 4 stops a running capture or starts a new one.")

    def softkeys(self):
        app = self.app

        def go():
            if app.capturing:
                app.stop_capture()
            else:
                app.start_capture()
        return [(lambda: ("Stop", L.BAD) if app.capturing else ("Start", L.OK), go),
                ("Files", lambda: app.push(FilesMenu(app))),
                ("Share", lambda: app.push(ShareScreen(app)))]

    def __init__(self, app):
        super().__init__(app)
        self.top = 0

    @property
    def hint(self):
        return "OK stop  ↑↓ scroll  ESC back" if self.app.capturing else \
            "OK new capture  ↑↓ scroll  ESC back"

    def lines(self) -> list:
        a, st = self.app, self.app.state
        job = st.get("job") or {}
        out = []
        if not job:
            return [("No capture yet.", L.DIM), "", ("OK starts one with the", L.DIM),
                    (f"'{C.PRESET[a.cfg['preset']][1]}' filter.", L.DIM)]
        if st.get("running"):
            where = f"tap ⇄ {job['tap']}" if job.get("tap") else job.get("iface")
            out.append((f"● REC {where}", L.BAD, hms(st.get("elapsed", 0))))
        else:
            why = st.get("error") or st.get("reason") or "stopped"
            out.append(("Stopped: " + why, L.BAD if st.get("error") else L.WARN))
        out.append(("Packets", L.FG, f"{st.get('packets', 0):,}"))
        size = F.nice_size(st.get("bytes", 0))
        if job.get("ring"):
            out.append(("Ring", L.FG, f"{size} of {job['max_mb']} MB"))
        else:
            out.append(("Size", L.FG, f"{size} of {job.get('max_mb')} MB"))
        if st.get("running"):
            bps = st.get("bps")
            out.append(("Rate", L.FG, f"{st.get('pps', 0):.0f} pkt/s" + (f"  {rate(bps)}" if bps is not None else "")))
        drops = st.get("drops", 0)
        out.append(("Dropped", L.WARN if drops else L.FG, f"{drops:,}"))
        out.append(("Filter", L.DIM, job.get("preset_name", "")[:18]))
        if job.get("seconds"):
            left = max(0, job["seconds"] - st.get("elapsed", 0))
            out.append(("Time left" if st.get("running") else "Time limit", L.DIM,
                        hms(left if st.get("running") else job["seconds"])))
        fl = a.follower
        if fl.seen:
            tot = sum(fl.protos.values()) or 1
            out.append(("Protocols" + (" (sample)" if fl.skipped else ""), L.WARN))
            for name, n in fl.protos.most_common(8):
                out.append((f"  {name}", L.FG, f"{n * 100 / tot:.0f}%  {n:,}"))
            if fl.talkers:
                out.append(("Top senders", L.WARN))
                for ip, n in fl.talkers.most_common(5):
                    out.append((f"  {ip}", L.FG, f"{n:,}"))
        files = st.get("files") or []
        if files and not st.get("running"):
            out.append(("Saved as", L.WARN))
            for f in files[-3:]:
                for l in wrap(a.panel, os.path.basename(f)):
                    out.append((l, L.DIM))
        if st.get("running") and job.get("filter") and st.get("packets", 0) == 0 and st.get("elapsed", 0) > 5:
            out.append(("No packets match yet.", L.DIM))
        return out

    def rows(self):
        ls = self.lines()
        n = self.nrows
        self.top = max(0, min(self.top, len(ls) - n))
        return ls[self.top:self.top + n], None

    def key(self, k):
        if k.name in UP:
            self.top = max(0, self.top - 1)
        elif k.name in DOWN:
            self.top += 1
        elif k.name == "pagedown":
            self.top += self.nrows
        elif k.name == "pageup":
            self.top = max(0, self.top - self.nrows)
        elif k.name == "enter":
            if self.app.capturing:
                self.app.stop_capture()
            else:
                self.app.pop(self)
                self.app.start_capture()
        elif k.name in BACK:
            self.app.pop(self)

    def tick(self):
        if self.app.capturing:
            self.app.dirty = True


class FilesMenu(Menu):
    def __init__(self, app):
        self.app = app
        super().__init__(app, "Saved captures", [], "↑↓ move  OK open  ESC back")
        self.reload()

    def reload(self):
        app = self.app
        self.saved = F.listing()
        self.items = [(s.label(), (lambda s=s: app.push(FileInfo(app, s, self))), F.nice_size(s.size))
                      for s in self.saved]
        if self.saved:
            for mnt in F.usb_drives():
                self.items.insert(0, (f"Copy all to USB {os.path.basename(mnt)[:10]}",
                                      (lambda mnt=mnt: copy_all(app, mnt)), "", L.OK))
            self.items.append(("Clear all captures", self.delete_all, "", L.BAD))
        else:
            self.items = [("No saved captures yet.", None, "", L.DIM)]
        self.sel = min(self.sel, len(self.items) - 1)
        self.app.dirty = True

    def right(self):
        return f"{C.free_mb() / 1000:.1f} GB free"

    def delete_all(self):
        total = F.nice_size(sum(s.size for s in self.saved))

        def yes():
            for s in self.saved:
                F.delete(s)
            self.reload()
        live = " The capture that is recording now is kept." if self.app.capturing else ""
        self.app.push(Confirm(self.app, "\u26a0 Clear all captures",
                              f"WARNING: this permanently deletes all {len(self.saved)} saved captures "
                              f"({total}) from this device. Copy any you need first.{live}",
                              lambda: self.app.push(Confirm(self.app, "\u26a0 Are you sure?",
                                                            f"Delete {len(self.saved)} captures now? "
                                                            "This can't be undone.", yes,
                                                            yes="OK delete all", no="ESC keep")),
                              yes="OK continue", no="ESC keep"))


class FileInfo(Menu):
    def __init__(self, app, s: F.Saved, parent: FilesMenu):
        self.app, self.s, self.parent = app, s, parent
        m = s.meta
        info = []
        if m.get("started"):
            info.append(("Started", L.DIM, time.strftime("%m-%d %H:%M:%S", time.localtime(m["started"]))))
        if m.get("seconds") is not None:
            info.append(("Length", L.DIM, hms(m["seconds"])))
        info.append(("Size", L.DIM, F.nice_size(s.size)))
        if m.get("packets") is not None:
            info.append(("Packets", L.DIM, f"{m['packets']:,}"))
        if m.get("drops"):
            info.append(("Dropped", L.WARN, f"{m['drops']:,}"))
        if m.get("preset"):
            info.append(("Filter", L.DIM, m["preset"][:18]))
        if m.get("iface"):
            info.append(("Port", L.DIM, m["iface"]))
        if m.get("reason"):
            info.append(("Ended by", L.DIM, m["reason"]))
        items = [("Summary (protocols)", self.summary)]
        for mnt in F.usb_drives():
            items.append((f"Copy to USB {os.path.basename(mnt)[:12]}", (lambda mnt=mnt: self.copy(mnt))))
        items.append(("Delete", self.delete, "", L.BAD))
        items += [(t, None, v, col) for t, col, v in info]
        items.append((s.name[:60], None, "", L.DIM))
        super().__init__(app, s.label(), items, "↑↓ move  OK open  ESC back")

    def summary(self):
        s = self.s

        def show(r):
            if not r.get("ok"):
                self.app.push(Message(self.app, "Summary", "Can't read this file.", L.BAD))
                return
            lines = [f"{r['packets']:,} packets" + ("" if r["complete"] else " (first 50 MB)")]
            if r["first"] and r["last"]:
                lines.append(f"{hms(r['last'] - r['first'])} long")
            tot = sum(n for _, n in r["protos"]) or 1
            lines.append("Protocols:")
            lines += [f"  {n * 100 / tot:3.0f}% {p}" for p, n in r["protos"]]
            if r["talkers"]:
                lines.append("Top senders:")
                lines += [f"  {ip} {n:,}" for ip, n in r["talkers"]]
            self.app.push(Message(self.app, "Summary", "\n".join(lines)))
        self.app.run_task("Summary", "Reading", lambda: pcapread.summarize(s.path), after=show)

    def copy(self, mnt):
        self.app.run_task("Copy to USB", "Copying", lambda: "Copied to " + F.copy_to(self.s, mnt))

    def delete(self):
        def yes():
            F.delete(self.s)
            self.app.pop(self)
            self.parent.reload()
        self.app.push(Confirm(self.app, "Delete", f"Delete {self.s.label()} ({F.nice_size(self.s.size)})?",
                              yes, yes="OK delete", no="ESC keep"))


class ShareScreen(Screen):
    """QR code + address of the download page (Nomad file transfer standard)."""
    title = "Share files"

    def __init__(self, app):
        super().__init__(app)
        if not app.share.on:
            app.share.start()
        self.addrs = netinfo.addresses()
        self.t = time.monotonic()
        self.pick = 0

    @property
    def hint(self):
        more = "  \u2191\u2193 IP" if len(self.addrs) > 1 else ""
        return ("OK turn off" if self.app.share.on else "OK turn on") + more + "  ESC back"

    def url(self):
        if not self.addrs:
            return ""
        # Wi-Fi first: in inline-tap mode the wired ports have no IP anyway
        items = sorted(self.addrs.items(), key=lambda kv: (not kv[0].startswith("wl"), kv[0]))
        ifc, ip = items[self.pick % len(items)]
        return ifc, f"http://{ip}:{self.app.share.port}/"

    def rows(self):
        sh = self.app.share
        if not sh.on:
            return [("Sharing is OFF", L.WARN), "",
                    ("OK puts your captures on a web", L.DIM), ("page for a phone or laptop.", L.DIM)], None
        if not self.addrs:
            return [("No IP address yet.", L.BAD), "", ("Join Wi-Fi or plug in a", L.DIM),
                    ("network cable.", L.DIM)], None
        return [], None

    def overlay(self, surf):
        from . import nomadui as T
        if not self.app.share.on or not self.addrs:
            return
        ifc, url = self.url()
        top = T.TITLE_H + 2
        size = T.H - T.HINT_H - top - 2
        px = qr.draw(surf, url, 4, top, size)
        f = self.app.panel.fonts
        x = px + 12
        y = top + 2
        lines = [("Scan or open:", T.FG, "small")]
        host = url.split("//", 1)[1].rstrip("/")
        ip, port = host.rsplit(":", 1)
        lines += [(ip, T.OK, "row"), (":" + port + f"  {ifc}", T.OK, "row"),
                  ("Download or delete", T.DIM, "small"), ("in the browser.", T.DIM, "small")]
        for text, color, fk in lines:
            img = f[fk].render(text, True, color)
            surf.blit(img, (x, y), (0, 0, T.W - x - 2, img.get_height()))
            y += img.get_height() + 1

    def key(self, k):
        if k.name == "enter":
            if self.app.share.on:
                self.app.share.stop()
            elif not self.app.share.start():
                self.app.push(Message(self.app, "Problem", self.app.share.error, L.BAD))
        elif k.name in UP:
            self.pick -= 1
        elif k.name in DOWN:
            self.pick += 1
        elif k.name in BACK:
            self.app.pop(self)

    def tick(self):
        if time.monotonic() - self.t > 5:
            self.addrs = netinfo.addresses()
            self.t = time.monotonic()


def copy_all(app, mnt):
    def run():
        n, skipped, size = F.copy_all(mnt)
        msg = f"Copied {n} captures ({F.nice_size(size)}) to the USB stick, folder Nomad-Captures."
        if skipped:
            msg += f" {skipped} were already there."
        return msg + " Safe to unplug."
    app.run_task("Copy to USB", "Copying", run)


class UsbFound(Menu):
    """Shown when a USB stick is plugged in and has captures missing."""

    def __init__(self, app, mnt, todo):
        size = F.nice_size(sum(s.size for s in todo))
        self.mnt = mnt

        def go():
            app.pop(self)
            copy_all(app, mnt)
        super().__init__(app, "USB stick found", [
            (f"Copy {len(todo)} captures ({size})", go, "", L.OK),
            ("Not now", lambda: app.pop(self)),
        ], "OK pick  ESC not now")


def ready_text(app) -> str:
    c = app.cfg
    if c.get("capmode") == "Inline tap":
        ads = tap.adapters()
        return f"tap {ads[0]}" if ads else "no USB adapter"
    return f"{c['iface']} {netinfo.link_text(c['iface'])}"


MIRROR_STEPS = [
    "On the switch, set up a mirror (SPAN / port monitor) port: copy the ports or VLAN you want "
    "to watch to one free port. Netgear AV Line: Monitoring > Mirroring.",
    "Plug this device's Ethernet port into that mirror port.",
    "Start capture. No packets? Check the mirror source ports and direction (Tx and Rx)."]
TAP_STEPS = [
    "Plug a USB Ethernet adapter into this device.",
    "Unplug the network cable from the gear you want to watch and plug it into this device's "
    "built-in Ethernet port.",
    "Run a second cable from the USB adapter to that gear.",
    "Traffic now passes through this device (up to 100 Mb/s). PoE does not pass through, "
    "so PoE-powered gear needs its own power."]


def mode_steps(mode: str):
    if mode == "Inline tap":
        return T.StepsPopup("Inline tap setup", TAP_STEPS, key="steps-tap", ok="OK done")
    return T.StepsPopup("Mirror port setup", MIRROR_STEPS, key="steps-mirror", ok="OK done")


class ModeMenu(Menu):
    def __init__(self, app):
        self.app = app
        c = app.cfg

        def pick(m):
            if app.capturing:
                app.push(Message(app, "Mode", "Stop the running capture first.", L.WARN))
                return
            c["capmode"] = m
            app.save_cfg()
            app.pop(self)
            app.show_steps(mode_steps(m))

        def toggle_auto():
            c["autotap"] = not c.get("autotap")
            app.save_cfg()
        items = [
            ("Mirror port", lambda: pick("Mirror port"), lambda: "\u2713" if c.get("capmode") != "Inline tap" else "",
             lambda: L.OK if c.get("capmode") != "Inline tap" else L.FG),
            ("Inline tap (USB adapter)", lambda: pick("Inline tap"),
             lambda: "\u2713" if c.get("capmode") == "Inline tap" else "",
             lambda: L.OK if c.get("capmode") == "Inline tap" else L.FG),
            ("Tap status", lambda: app.push(TapStatus(app))),
            ("Auto tap on plug-in", toggle_auto, lambda: "On" if c.get("autotap") else "Off"),
        ]
        super().__init__(app, "Mode", items, "\u2191\u2193 move  OK pick  ESC back")


class TapStatus(Screen):
    title = "Inline tap"
    steps = mode_steps("Inline tap")

    @property
    def hint(self):
        return ("OK bridge off" if tap.bridge_up() else "OK bridge on") + "  ESC back"

    def rows(self):
        st = tap.status()
        out = []
        if st["up"]:
            out.append((f"Bridge ON ({tap.BRIDGE})", L.OK, "no IP"))
        else:
            out.append(("Bridge off", L.WARN))
        b = st["builtin"]
        for p in [b] + [a for a in st["adapters"]]:
            carrier, speed = st["links"].get(p) or (netinfo.link(p)["up"], netinfo.link(p)["speed"])
            kind = "built-in" if p == b else "USB"
            out.append((f"{p} ({kind})", L.OK if carrier else L.BAD,
                        (f"{speed} Mb/s" if speed else "link") if carrier else "no link"))
        if not st["adapters"]:
            out.append(("Plug in a USB Ethernet", L.DIM))
            out.append(("adapter for inline tap.", L.DIM))
        else:
            out.append(("Max 100 Mb/s through it.", L.DIM))
        return out, None

    def key(self, k):
        if k.name == "enter":
            if tap.bridge_up():
                if self.app.capturing and (self.app.state.get("job") or {}).get("tap"):
                    self.app.push(Message(self.app, "Inline tap", "Stop the capture first.", L.WARN))
                    return
                self.app.run_task("Inline tap", "Removing bridge", lambda: tap.stop() or "Bridge off. "
                                  "The two sides are no longer connected.")
            else:
                ads = tap.adapters()
                ad = self.app.cfg.get("tap_adapter") or (ads[0] if ads else "")
                self.app.run_task("Inline tap", "Bridging", lambda: tap.start(ad) or
                                  f"Bridge on: built-in port \u21c4 {ad}. Traffic passes through. "
                                  "Start capture to record it.")
        elif k.name in BACK:
            self.app.pop(self)


def main_ui() -> int:
    panel = L.Panel()
    kb = Keyboard()
    app = App(panel, kb)

    def on_term(*_):
        app.quit = True
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGHUP, on_term)
    try:
        if not kb.ok:
            panel.draw(APP_TITLE, [("No keyboard found.", L.BAD)], "")
            time.sleep(4)
            return 1
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        app.share.stop()
        msg = "Capture keeps running. Bye!" if C.running() else "Bye!"
        panel.draw(APP_TITLE, [(msg, L.OK)], "")
        kb.close()
    return 0
