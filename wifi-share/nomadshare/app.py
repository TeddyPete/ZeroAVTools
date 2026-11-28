"""Nomad WiFi Share: the app. Screens on the LCD, input from the keyboard."""
from __future__ import annotations

import os
import signal
import threading
import time

from . import __version__
from . import lcd as L
from . import nomadui as T
from . import net, portal
from . import settings as S
from .keys import Key, Keyboard
from .ui import BACK, SELECT, Busy, Confirm, Form, Menu, Message, Screen, Prompt, nav_key, wrap

APP_TITLE = "Nomad WiFi Share"
T.set_about(APP_TITLE, __version__)
T.set_popups("nomad-wifi-share")
HELP_MAIN = ("Nomad WiFi Share shares internet between Wi-Fi and the Ethernet port. "
             "Settings are changed right here on the keyboard. TheTechNomad.com")


class App:
    def __init__(self, panel: L.Panel, kb: Keyboard | None):
        self.panel, self.kb = panel, kb
        self.cfg = S.load()
        self.stack: list[Screen] = []
        self.steps = None                 # nomadui.StepsPopup shown over the screen
        self.helpview = None    # Fn+H help view while it is shown
        self.dirty = True
        self.quit = False
        self.status: dict = {}
        self.inet: tuple[str, str] = ("checking", "")
        self.inet_at = 0.0
        self._wake = threading.Event()
        self._poll_now = threading.Event()
        self.portal_session: portal.Session | None = None

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

    def refresh(self) -> None:
        """Ask the status thread for fresh data now (and a new internet check)."""
        self.inet_at = 0
        self._poll_now.set()

    def mode_badge(self) -> str:
        if self.status.get("lost"):
            return "STOPPED"
        m = self.status.get("mode")
        return {"wifi2eth": "SHARING", "hotspot": "HOTSPOT"}.get(m, "")

    def run_task(self, title, label, fn, ok_title=None, after=None):
        def done(result, error):
            self.refresh()
            if error is not None:
                self.push(Message(self, "Problem", str(error), L.BAD))
            elif after:
                after(result)
            elif result:
                self.push(Message(self, ok_title or title, str(result), L.OK))
        self.push(Busy(self, title, label, fn, done))

    # ---- background status ----
    def _status_loop(self) -> None:
        while not self.quit:
            try:
                self.status = net.status()
                if time.monotonic() - self.inet_at > 20 and not self.status.get("mode") == "hotspot" or self.inet_at == 0:
                    self.inet = portal.check(timeout=4)
                    self.inet_at = time.monotonic()
                elif self.status.get("mode") == "hotspot" and time.monotonic() - self.inet_at > 20:
                    self.inet = portal.check(timeout=4)
                    self.inet_at = time.monotonic()
            except Exception as e:  # keep polling whatever happens
                self.status.setdefault("error", str(e))
            self.dirty = True
            self._poll_now.wait(4)
            self._poll_now.clear()

    # ---- main loop ----
    def run(self) -> None:
        threading.Thread(target=self._status_loop, daemon=True).start()
        self.push(Home(self))
        if not self.cfg.get("setup_done"):
            from .extras import Wizard
            self.push(Wizard(self))
        last_draw = 0.0
        while not self.quit:
            keys = self.kb.poll(0.1) if self.kb else (time.sleep(0.1) or [])
            for k in keys:
                if k.name == "exit":     # Esc held: leave the app (key spec)
                    self.quit = True
                    break
                if self.stack:
                    self.dispatch(k)
                self.dirty = True
            if self.stack:
                self.stack[-1].tick()
            now = time.monotonic()
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
            text = top.help or HELP_MAIN
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
        paint = getattr(s, "paint", None)
        if paint:
            paint(surf, f)
        if busy is not None:
            T.draw_loading(surf, busy.label, busy.t0, f)
        elif self.steps is not None:
            self.steps.render(surf, f)
        self.panel.flush()


# ---------------------------------------------------------------------------

def inet_row(app) -> tuple[str, tuple]:
    state, detail = app.inet
    if state == "online":
        return "Internet: online", L.OK
    if state == "portal":
        return "Internet: login page (portal)", L.WARN
    if state == "checking":
        return "Internet: checking...", L.DIM
    return "Internet: offline", L.BAD


class Home(Menu):
    def __init__(self, app):
        self.app = app
        from . import extras as X
        items = [
            ("Status", lambda: app.push(Status(app)), lambda: inet_row(app)[0].split(": ")[1]),
            ("Dashboard", lambda: app.push(X.Dashboard(app)), ""),
            ("Share Wi-Fi to Ethernet", self.mode1, lambda: "ON" if app.status.get("mode") == "wifi2eth" else "",
             lambda: L.OK if app.status.get("mode") == "wifi2eth" else L.FG),
            ("Hotspot from Ethernet", self.mode2, lambda: "ON" if app.status.get("mode") == "hotspot" else "",
             lambda: L.OK if app.status.get("mode") == "hotspot" else L.FG),
            ("Join Wi-Fi", lambda: open_wifi_list(app), lambda: (app.status.get("wifi_link") or {}).get("ssid", "")[:14]
             if app.status.get("mode") != "hotspot" else ""),
            ("Guest Wi-Fi login", lambda: start_portal(app), lambda: "needed" if app.inet[0] == "portal" else ""),
            ("Devices", lambda: app.push(X.Devices(app)), lambda: str(len(app.status.get("clients", []))) if app.status.get("mode") else ""),
            ("Switch port (LLDP)", lambda: app.push(X.SwitchPort(app)), lambda: f"VLAN {app.status['vlan']}" if app.status.get("vlan") else ""),
            ("VPN (WireGuard)", lambda: app.push(X.Vpn(app)), lambda: "ON" if app.status.get("vpn") == "on" else "",
             lambda: L.OK if app.status.get("vpn") == "on" else L.FG),
            ("Settings", lambda: app.push(X.SettingsMenu(app)), ""),
            ("Stop sharing", self.stop, ""),
            ("About", lambda: app.open_about(), ""),
        ]
        super().__init__(app, APP_TITLE, items, "\u2191\u2193 move  OK open  hold ESC exit")

    help = ("Share Wi-Fi to Ethernet gives a switch (and the gear on it) this device's Wi-Fi "
            "internet. Hotspot from Ethernet makes a Wi-Fi hotspot from a wired network (its own "
            "network, or bridged). Guest Wi-Fi login opens a hotel or venue login page right here. "
            "Devices: rename, block, fixed IPs, speed limits. Dashboard: live speed and ping. "
            "Settings: VLAN tag, port forwards, local names, the setup wizard.")

    def softkeys(self):
        app = self.app
        on = app.status.get("mode")
        return [
            (lambda: ("Share", L.OK) if on == "wifi2eth" else "Share", self.mode1),
            (lambda: ("Hotspot", L.OK) if on == "hotspot" else "Hotspot", self.mode2),
            ("Join", lambda: open_wifi_list(app)),
            ("Status", lambda: app.push(Status(app))),
            (("Stop", L.BAD), self.stop) if on else None,
        ]

    def mode1(self):
        app, c = self.app, self.app.cfg
        if app.status.get("mode") == "wifi2eth":
            return self.stop()
        p = S.mask_to_prefix(c["share_mask"])
        tag = f" VLAN {net.vlan_id(c)}." if net.vlan_id(c) else ""
        dhcp = "DHCP server ON: gear gets addresses automatically." if c["share_dhcp"] else \
            f"DHCP OFF: set gear gateway to {c['share_ip']} and DNS to 1.1.1.1."
        warn = ""
        if app.status.get("mode") == "hotspot":
            warn = "The hotspot will stop. "
        app.push(Confirm(app, "Wi-Fi -> Ethernet",
                         f"{warn}Share {(app.status.get('wifi_link') or {}).get('ssid') or 'Wi-Fi'} to the Ethernet port. "
                         f"Pi on Ethernet: {c['share_ip']}/{p}.{tag} {dhcp}",
                         lambda: app.run_task("Wi-Fi -> Ethernet", "Starting", lambda: net.start_wifi_to_eth(app.cfg),
                                              ok_title="Sharing on"),
                         yes="OK start", no="ESC back"))
        app.show_steps(T.StepsPopup("Before you share", [
            "Join a Wi-Fi network with internet first (Join Wi-Fi).",
            "Plug this device's Ethernet port into the switch (or straight into one device).",
            "Gear on that switch must get its address by DHCP, or use the gateway and DNS shown "
            "next if DHCP is off.",
            "Unplug any other router or DHCP server from that switch so there is only one."],
            key="steps-wifi2eth", ok="OK done"))

    def mode2(self):
        app, c = self.app, self.app.cfg
        if app.status.get("mode") == "hotspot":
            return self.stop()
        up = "Ethernet uplink: DHCP" if c["uplink_dhcp"] else f"Ethernet uplink: {c['uplink_ip']}"
        if c.get("hs_bridge"):
            up = "Bridged to Ethernet: devices get addresses from the wired network"
        warn = " Wi-Fi client connection drops while the hotspot runs." if app.status.get("wifi_state") == "connected" else ""
        app.push(Confirm(app, "Hotspot",
                         f"Hotspot {c['hs_ssid']}  password {c['hs_password']}  ch {c['hs_channel']}. {up}.{warn}",
                         lambda: app.run_task("Hotspot", "Starting hotspot", lambda: net.start_hotspot(app.cfg),
                                              ok_title="Hotspot on"),
                         yes="OK start", no="ESC back"))
        app.show_steps(T.StepsPopup("Before the hotspot", [
            "Plug this device's Ethernet port into a wired network with internet (a router or "
            "a live wall port).",
            "If that network has no DHCP, set the Ethernet uplink address under Settings first.",
            "Connect phones and laptops to the hotspot name and password shown next."],
            key="steps-hotspot", ok="OK done"))

    def stop(self):
        app = self.app
        app.run_task("Stop sharing", "Restoring network", lambda: "\n".join(net.restore(log=lambda m: None)) or "Normal network restored.",
                     ok_title="Stopped")

    def key(self, k):
        if k.name == "esc":
            if self.app.status.get("mode"):
                self.app.push(Confirm(self.app, "Quit?", "Sharing stops and the normal network comes back.",
                                      self._quit, yes="OK quit", no="ESC stay"))
            else:
                self._quit()
        elif k.name == "left":
            pass  # already on the main page
        else:
            super().key(k)

    def _quit(self):
        self.app.quit = True


class Status(Screen):
    title = "Status"
    hint = "OK clients  R recheck  \u2190 back"
    help = "What is running now, the internet check, addresses and how many clients are connected."

    def softkeys(self):
        from .extras import Devices, Dashboard
        return [("Recheck", self.app.refresh), ("Devices", lambda: self.app.push(Devices(self.app))),
                ("Graph", lambda: self.app.push(Dashboard(self.app)))]

    def __init__(self, app):
        super().__init__(app)
        self.top = 0

    def all_rows(self):
        st, app = self.app.status, self.app
        mode = st.get("mode")
        mname = {"wifi2eth": "Wi-Fi > Ethernet", "hotspot": "Hotspot (bridge)" if st.get("bridge") else "Hotspot"}.get(mode, "Off")
        rows = [("Mode", L.OK if mode else L.FG, mname)]
        if st.get("lost"):
            rows.append(("Network stopped it: use Stop", L.BAD))
        text, color = inet_row(app)
        rows.append(("Internet", color, text.split(": ", 1)[1].replace(" (portal)", "")))
        if st.get("wifi"):
            if mode == "hotspot":
                rows.append(("Hotspot", L.FG, app.cfg["hs_ssid"]))
                rows.append(("Password", L.WARN, app.cfg["hs_password"]))
                rows.append(("Hotspot IP", L.FG, ip_short(st.get("wifi_ip")).split("/")[0]))
            else:
                link = st.get("wifi_link") or {}
                if link:
                    rows.append(("Wi-Fi", L.FG, f"{link.get('ssid', '')[:14]} {link.get('signal', 0)}%"))
                    rows.append(("Wi-Fi IP", L.FG, ip_short(st.get("wifi_ip")).split("/")[0]))
                else:
                    rows.append(("Wi-Fi", L.DIM, st.get("wifi_state", "?")))
        if st.get("eth"):
            if not st.get("eth_carrier"):
                rows.append(("Ethernet", L.WARN, "no cable"))
            else:
                ips = st.get("lan_ip") if st.get("vlan") else st.get("eth_ip")
                rows.append(("Ethernet", L.FG, ip_short(ips).split("/")[0] or st.get("eth_state", "")))
        if st.get("vlan"):
            rows.append(("VLAN tag", L.FG, st["vlan"]))
        if st.get("bridge_ip"):
            rows.append(("Bridge IP", L.FG, ip_short(st.get("bridge_ip")).split("/")[0]))
        if st.get("vpn"):
            rows.append(("VPN", L.OK if st["vpn"] == "on" else L.WARN, st["vpn"]))
        if mode:
            rows.append(("Clients", L.FG, str(len(st.get("clients", [])))))
        else:
            r = st.get("route")
            rows.append(("Gateway", L.DIM, f"{r[0]}" if r else "none"))
        return rows

    def rows(self):
        rows = self.all_rows()
        self.top = min(self.top, max(0, len(rows) - self.nrows))
        return rows[self.top:self.top + self.nrows], None

    def key(self, k):
        n = len(self.all_rows())
        if k.char in ("r", "R"):
            self.app.refresh()
        elif k.name == "up":
            self.top = max(0, self.top - 1)
        elif k.name == "down":
            self.top = min(max(0, n - self.nrows), self.top + 1)
        elif k.name in SELECT:
            from .extras import Devices
            self.app.push(Devices(self.app))
        else:
            super().key(k)


def ip_short(ips) -> str:
    return ips[0] if ips else ""


class Clients(Screen):
    title = "Clients"
    hint = "\u2191\u2193 scroll  \u2190/ESC back"

    def __init__(self, app):
        super().__init__(app)
        self.top = 0

    def rows(self):
        cl = self.app.status.get("clients", [])
        if not self.app.status.get("mode"):
            return [("Not sharing right now.", L.DIM)], None
        if not cl:
            return [("No devices seen yet.", L.DIM), ("Leases and ARP refresh every few s.", L.DIM)], None
        rows = []
        for c in cl:
            name = c.get("name") or c["mac"]
            rows.append((name[:20], L.FG, c.get("ip") or "(no IP)"))
        return rows[self.top:self.top + self.nrows], None

    def key(self, k):
        n = len(self.app.status.get("clients", []))
        if k.name == "up":
            self.top = max(0, self.top - 1)
        elif k.name == "down":
            self.top = min(max(0, n - self.nrows), self.top + 1)
        else:
            super().key(k)


# ---- Wi-Fi join --------------------------------------------------------------

def open_wifi_list(app):
    w = app.status.get("wifi")
    if app.status.get("mode") == "hotspot":
        app.push(Confirm(app, "Join Wi-Fi", "The hotspot uses the Wi-Fi radio. Stop the hotspot and scan?",
                         lambda: app.run_task("Join Wi-Fi", "Stopping hotspot", lambda: net.stop_sharing(keep_saved=True),
                                              after=lambda _: open_wifi_list(app))))
        return
    app.run_task("Join Wi-Fi", "Scanning", lambda: net.scan(w or net.wifi_dev() or "wlan0"),
                 after=lambda nets: app.push(WifiList(app, nets)))


class WifiList(Menu):
    def __init__(self, app, nets):
        self.nets = nets
        items = []
        for n in nets:
            right = f"{n['signal']}%" + (" lock" if n["security"] else " open")
            label = ("* " if n["active"] else "") + n["ssid"][:22] + (" (saved)" if n["saved"] and not n["active"] else "")
            items.append((label, lambda n=n: self.pick(n), right, L.OK if n["active"] else L.FG))
        items.append(("Other network (hidden)...", self.hidden, ""))
        super().__init__(app, "Join Wi-Fi", items, "OK join  R rescan  D forget  \u2190 back")

    def pick(self, n):
        app = self.app
        if n["active"]:
            app.push(Message(app, n["ssid"], "Already connected. Use Guest Wi-Fi login if a portal page appears."))
            return
        if n["security"] and not n["saved"]:
            if "802.1X" in n["security"] or "EAP" in n["security"]:
                app.push(Message(app, n["ssid"], "Enterprise (802.1X) Wi-Fi isn't supported yet.", L.WARN))
                return
            app.push(Prompt(app, n["ssid"][:24], f"Password for {n['ssid'][:22]}", "",
                               lambda pw: do_join(app, n["ssid"], pw), secret=True,
                               help_text="Type the password, then OK to join."))
        else:
            do_join(app, n["ssid"], "")

    def hidden(self):
        app = self.app
        app.push(Prompt(app, "Hidden network", "Network name (SSID)", "",
                           lambda ssid: ssid and app.push(Prompt(
                               app, ssid[:24], "Password (empty if open)", "",
                               lambda pw: do_join(app, ssid, pw, hidden=True), secret=True))))

    def key(self, k):
        if k.char in ("r", "R"):
            self.app.pop(self)
            open_wifi_list(self.app)
        elif k.char in ("d", "D") and self.sel < len(self.nets):
            n = self.nets[self.sel]
            if n["saved"] or n["active"]:
                self.app.push(Confirm(self.app, "Forget network", f"Forget saved network {n['ssid']}?",
                                      lambda: self.app.run_task("Forget", "Removing", lambda: net.forget_wifi(n["ssid"]))))
        else:
            super().key(k)


def do_join(app, ssid, pw, hidden=False):
    def after(msg):
        # Leave the scan list, then check for a guest login page.
        for s in [s for s in app.stack if isinstance(s, WifiList)]:
            app.pop(s)
        app.run_task("Join Wi-Fi", "Checking internet", lambda: portal.check(timeout=6),
                     after=lambda res: _after_check(app, msg, res))
    app.run_task("Join Wi-Fi", f"Joining {ssid[:18]}", lambda: net.join_wifi(ssid, pw, hidden), after=after)


def _after_check(app, msg, res):
    app.inet, app.inet_at = res, time.monotonic()
    state, url = res
    if state == "portal":
        app.push(Confirm(app, "Guest login needed", f"{msg}. This network shows a login page. Open it now?",
                         lambda: start_portal(app), yes="OK open", no="ESC later"))
    elif state == "online":
        app.push(Message(app, "Connected", f"{msg}. Internet is working.", L.OK))
    else:
        app.push(Message(app, "Connected", f"{msg}, but no internet yet ({url}).", L.WARN))


# ---- Captive portal ------------------------------------------------------------

def start_portal(app):
    if app.status.get("mode") == "hotspot":
        app.push(Message(app, "Guest login", "The Wi-Fi radio is running the hotspot. Stop it and join the guest Wi-Fi first.", L.WARN))
        return

    def job():
        state, url = portal.check(timeout=6)
        if state != "portal":
            return state, url, None
        app.portal_session = portal.Session()
        return state, url, app.portal_session.fetch(url)

    def after(res):
        state, url, page = res
        app.inet, app.inet_at = (state, url), time.monotonic()
        if state == "online":
            app.push(Message(app, "Guest login", "Internet already works. No login page found.", L.OK))
        elif state == "offline":
            app.push(Message(app, "Guest login", f"No internet and no login page ({url}). Check the Wi-Fi connection.", L.BAD))
        else:
            app.push(PortalPage(app, page))
    app.run_task("Guest login", "Looking for login page", job, after=after)


class PortalPage(Menu):
    def __init__(self, app, page: portal.Page):
        self.page = page
        self.form = page.best_form()
        items = []
        if self.form:
            for f in self.form.fields:
                if f.kind == "checkbox" and f.required:
                    f.checked = True
            for f in self.form.fields:
                if f.kind == "checkbox":
                    items.append((lambda f=f: self._cb_label(f), lambda f=f: self.toggle(f)))
                elif f.kind in ("text", "email", "tel", "number", "password"):
                    items.append(((f.label or f.name or f.kind)[:24], lambda f=f: self.edit(f),
                                  lambda f=f: ("*" * len(f.value) if f.kind == "password" else f.value)[:14] or "(empty)"))
                elif f.kind == "select" and f.options:
                    items.append(((f.label or f.name)[:20], lambda f=f: self.cycle(f),
                                  lambda f=f: next((t for v, t in f.options if v == f.value), f.value)[:14]))
                elif f.kind == "radio":
                    items.append((lambda f=f: self._radio_label(f), lambda f=f: self.radio(f)))
            subs = self.form.submits or [None]
            for b in subs:
                label = (b.label or b.value or "Submit") if b else "Submit"
                items.append((f"> {label[:28]}", lambda b=b: self.submit(b), "", L.OK))
        for text, url in page.accept_links():
            items.append((f"> {text[:28]}", lambda url=url: self.follow(url), "link", L.OK))
        items.append(("Open on HDMI browser", self.hdmi, "", L.HINT))
        items.append(("Page text", self.show_text, "", L.DIM))
        title = (page.title or "Guest login")[:24]
        super().__init__(app, title, items, "OK select  H HDMI  \u2190 back")
        if page.needs_browser:
            self.sel = len(items) - 2

    @staticmethod
    def _cb_label(f):
        return ("[x] " if f.checked else "[ ] ") + (f.label or f.name or "checkbox")[:30]

    @staticmethod
    def _radio_label(f):
        return ("(o) " if f.checked else "( ) ") + (f.label or f.value)[:30]

    def toggle(self, f):
        f.checked = not f.checked

    def radio(self, f):
        for g in self.form.fields:
            if g.kind == "radio" and g.name == f.name:
                g.checked = g is f

    def cycle(self, f):
        vals = [v for v, _ in f.options]
        f.value = vals[(vals.index(f.value) + 1) % len(vals)] if f.value in vals else vals[0]

    def edit(self, f):
        def done(v):
            f.value = v
        self.app.push(Prompt(self.app, self.title, f.label or f.name, f.value, done,
                                secret=True if f.kind == "password" else None,
                                help_text="As asked by the guest Wi-Fi page."))

    def submit(self, b):
        missing = [f for f in self.form.fields if f.required and f.kind in ("text", "email", "tel", "number", "password") and not f.value]
        if missing:
            self.app.push(Message(self.app, "Needed", f"Fill in: {missing[0].label or missing[0].name}", L.WARN))
            return
        sess = self.app.portal_session
        self._go(lambda: sess.submit(self.form, b))

    def follow(self, url):
        sess = self.app.portal_session
        self._go(lambda: sess.fetch(url))

    def _go(self, fetch):
        app = self.app

        def job():
            try:
                page = fetch()
            except Exception as e:  # the portal may cut the connection once we're online
                page = None
                err = str(e)
            else:
                err = ""
            time.sleep(1.5)
            return page, err, portal.check(timeout=6)

        def after(res):
            page, err, inet = res
            app.inet, app.inet_at = inet, time.monotonic()
            if inet[0] == "online":
                app.pop(self)
                app.push(Message(app, "Guest login", "You're online. Sharing can start now.", L.OK))
            elif page is not None:
                app.pop(self)
                app.push(PortalPage(app, page))
            else:
                app.push(Message(app, "Guest login", f"The page didn't load ({err[:80]}). Try Open on HDMI browser.", L.WARN))
        app.run_task("Guest login", "Sending", job, after=after)

    def hdmi(self):
        url = self.page.url
        self.app.run_task("Guest login", "Opening browser on HDMI", lambda: portal.open_on_hdmi(url),
                          ok_title="HDMI browser")

    def show_text(self):
        p = self.page
        self.app.push(Message(self.app, self.title, f"{p.url}\n{p.text or '(no text)'}"))

    def key(self, k):
        if k.char in ("h", "H"):
            self.hdmi()
        else:
            super().key(k)


# ---- Settings forms --------------------------------------------------------------

def edit_ethernet(app):
    fields = [
        ("", "Share side (Wi-Fi -> Ethernet)", "header"),
        ("share_ip", "Pi address", "ip"),
        ("share_mask", "Subnet mask", "mask", "Like 255.255.255.0 or 24"),
        ("share_dhcp", "DHCP server", "bool", ("On", "Off (static)")),
        ("share_vlan", "VLAN tag", "number", "0 = untagged"),
        ("lldp", "Switch info (LLDP)", "bool", ("Listen", "Off")),
        ("", "Uplink (for the hotspot)", "header"),
        ("uplink_dhcp", "Uplink IP", "bool", ("DHCP", "Static")),
        ("uplink_ip", "IP address", "ip", "", lambda v: not v["uplink_dhcp"]),
        ("uplink_mask", "Subnet mask", "mask", "Like 255.255.255.0 or 24", lambda v: not v["uplink_dhcp"]),
        ("uplink_gw", "Gateway", "ip", "Leave empty for none", lambda v: not v["uplink_dhcp"]),
        ("uplink_dns", "DNS", "text", "One or two addresses, space between", lambda v: not v["uplink_dhcp"]),
    ]

    def validate(v):
        err = S.check_host(v["share_ip"], v["share_mask"])
        if err:
            return "Share side: " + err
        tag = str(v["share_vlan"]).strip() or "0"
        if not tag.isdigit() or int(tag) > 4094:
            return "VLAN tag must be 0 (none) or 1-4094"
        v["share_vlan"] = str(int(tag))
        if not v["uplink_dhcp"]:
            err = S.check_host(v["uplink_ip"], v["uplink_mask"])
            if err:
                return "Uplink: " + err
            if v["uplink_gw"] and not S.valid_ip(v["uplink_gw"]):
                return f"Bad gateway: {v['uplink_gw']}"
        return None
    app.push(Form(app, "Ethernet settings", fields, app.cfg, lambda v: saved(app, v, "wifi2eth"), validate,
                  help="Share side: the Pi's address on the switch, DHCP for the gear, an optional "
                       "802.1Q VLAN tag (for a trunk or tagged port) and LLDP switch info. "
                       "Uplink: how the Ethernet port gets online when it feeds the hotspot."))


def edit_hotspot(app):
    fields = [
        ("hs_ssid", "Name (SSID)", "text"),
        ("hs_password", "Password", "secret", "8 to 63 characters"),
        ("hs_channel", "Channel (2.4 GHz)", "choice", ["1", "6", "11"]),
        ("hs_bridge", "Network", "bool", ("Bridge to Ethernet", "Own network (NAT)")),
        ("hs_ip", "Pi address", "ip", "Clients get .10 and up", lambda v: not v["hs_bridge"]),
    ]

    def validate(v):
        return S.check_hotspot(v["hs_ssid"], v["hs_password"]) or S.check_host(v["hs_ip"], "24")
    app.push(Form(app, "Hotspot settings", fields, app.cfg, lambda v: saved(app, v, "hotspot"), validate,
                  help="Bridge: Wi-Fi devices join the wired network itself (its DHCP, no NAT), like "
                       "plugging them into the switch. Own network: the Pi gives them addresses and NAT."))


def saved(app, values, mode):
    app.cfg.update(values)
    S.save(app.cfg)
    if app.status.get("mode") == mode or (mode == "wifi2eth" and app.status.get("mode") == "hotspot"):
        start = net.start_wifi_to_eth if app.status.get("mode") == "wifi2eth" else net.start_hotspot
        app.push(Confirm(app, "Saved", "Settings saved. Restart sharing now to use them?",
                         lambda: app.run_task("Restart", "Restarting", lambda: start(app.cfg)),
                         yes="OK restart", no="ESC later"))
    else:
        app.push(Message(app, "Saved", "Settings saved.", L.OK))


# ---------------------------------------------------------------------------

def main_ui() -> int:
    panel = L.Panel()
    kb = Keyboard()
    app = App(panel, kb)

    def on_term(*_):
        app.quit = True
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGHUP, on_term)
    try:
        if net.needs_restore():
            panel.draw(APP_TITLE, [("Cleaning up after last run...", L.WARN)])
            net.restore(log=lambda m: None)
        if not kb.ok:
            panel.draw(APP_TITLE, [("No keyboard found.", L.BAD)], "")
            time.sleep(4)
            return 1
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        if net.needs_restore():
            panel.draw(APP_TITLE, [("Restoring normal network...", L.WARN)], "")
            net.restore(log=lambda m: None)
        panel.draw(APP_TITLE, [("Network restored. Bye!", L.OK)], "")
        kb.close()
    return 0
