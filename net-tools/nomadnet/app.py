"""Nomad Net Tools: network tools for AV techs on the CardputerZero.

Results show on the built-in LCD and, when the Pi desktop is running, in a
window on the HDMI desktop. Run:  python3 -m nomadnet.app
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402

from . import keys, reports, store, util  # noqa: E402
from . import nomadui as T  # noqa: E402
from .ui import AboutScreen, DIM, Field, Form, Line, Menu, Message, Renderer, Screen, TaskScreen, View  # noqa: E402

VERSION = "0.5.3"
T.set_about("Nomad Net Tools", VERSION)
T.set_popups("nomad-net-tools")


class App:
    def __init__(self, window: bool = True):
        self.cfg = store.Config()
        self.stack = []
        self.steps = None         # nomadui.StepsPopup over the screen
        self.quit = False
        pygame.font.init()
        self.r = Renderer(window)
        from .share import Share
        self.share = Share()
        self.kb = None
        path = keys.find_keyboard()
        if path:
            try:
                self.kb = keys.EvdevKeyboard(path)
            except OSError as e:
                print(f"keyboard {path}: {e}; using window keys", flush=True)

    # ---- navigation
    def push(self, s) -> None:
        self.stack.append(s)
        s.on_show(self)
        if getattr(s, "steps", None) is not None:
            self.show_steps(s.steps)

    def show_steps(self, popup) -> None:
        """Setup steps the user does by hand, over the current screen
        (skipped once the user hid that popup with D)."""
        if popup is not None and not T.steps_hidden(popup.key):
            popup.top = 0
            self.steps = popup

    def pop(self) -> None:
        if self.stack:
            self.stack.pop().close()
        if not self.stack:
            self.quit = True

    def toast(self, msg: str) -> None:
        self.push(Message("Note", [msg], "Any key"))

    def task(self, title, fn, on_pick=None, **kw) -> None:
        self.push(TaskScreen(title, fn, kw, on_pick=on_pick, report=title))

    @property
    def site(self) -> str:
        return self.cfg.get("site") or ""

    def iface_field(self, default: str = "") -> Field:
        names = util.iface_names() or ["eth0"]
        pref = default or self.cfg.get("iface") or ""
        choices = ["auto"] + names
        return Field("iface", "Interface", pref if pref in names else "auto", choices=choices)

    def dispatch(self, k) -> None:
        """One key: Fn+H help, number keys 4..8, then the screen itself."""
        from .ui import HelpScreen
        top = self.stack[-1]
        if self.steps is not None:
            if self.steps.handle(k) == "close":
                self.steps = None
            return
        if k.name == "help" and not isinstance(top, HelpScreen):
            self.push(HelpScreen(top, self))
            return
        if top.soft(k, self):
            return
        top.on_key(k, self)

    # ---- loop
    def run(self, script: str = "") -> None:
        """script: comma-separated keys fed in for remote testing, e.g.
        "down,down,enter,wait,esc". 'wait' pauses 2 s; 'shot:NAME' saves the
        LCD image to /tmp/NAME.png."""
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "quit", True))
        self.push(main_menu(self))
        steps = [x for x in script.split(",") if x] if script else []
        next_step = time.monotonic() + 1.5
        while not self.quit and self.stack:
            evs = self.kb.poll(0.04) if self.kb else []
            if steps and time.monotonic() >= next_step:
                st = steps.pop(0)
                next_step = time.monotonic() + (2.0 if st == "wait" else 0.6)
                if st.startswith("shot:"):
                    self.r.render(self.stack[-1], self.site, force=True, keys=self.stack[-1].keybar(self),
                                  popup=self.steps)
                    self.r.save_lcd(f"/tmp/{st[5:]}.png")
                elif st == "quit":
                    self.quit = True
                elif st != "wait":
                    evs.append(keys.Key(st, "") if len(st) > 1 else keys.Key("", st))
            for ev in (pygame.event.get() if pygame.display.get_init() else []):
                if ev.type == pygame.QUIT:
                    self.quit = True
                elif ev.type == pygame.VIDEORESIZE and self.r.win is not None:
                    self.r.resize(ev.w, ev.h)
                    self.r.last = None
                elif ev.type == pygame.KEYDOWN and not self.kb:
                    k = keys.from_pygame(ev)
                    if k:
                        evs.append(k)
            if not self.kb and not evs:
                time.sleep(0.03)
            for k in evs:
                if k.name == "exit":          # Esc held: leave the app
                    self.quit = True
                    break
                if self.stack:
                    self.dispatch(k)
            if self.stack:
                self.r.render(self.stack[-1], self.site, keys=self.stack[-1].keybar(self), popup=self.steps)
        for s in self.stack:
            s.close()
        if self.kb:
            self.kb.close()


MIRROR_STEPS = [
    "On the switch, set up a mirror (SPAN / port monitor) port that copies the port of the "
    "gear you want to watch. Netgear AV Line: Monitoring > Mirroring.",
    "Plug this device's Ethernet port into that mirror port.",
    "Start. Nothing shows? Check the mirror source port and that both Tx and Rx are copied."]


def with_steps(screen, title: str, steps: list, key: str, ok: str = "OK done"):
    """Give a screen the setup steps the user must do by hand; they pop up
    when it opens (nomadui.StepsPopup) and show again with Fn+H."""
    screen.steps = T.StepsPopup(title, steps, key=key, ok=ok)
    return screen


def ifc(v: dict) -> str:
    x = v.get("iface", "")
    return "" if x == "auto" else x


# ------------------------------------------------------------------ actions

def do_basics(app):
    from . import basics
    app.task("Network basics", basics.basics)


def do_ping(app, host: str = ""):
    from . import basics

    def go(v, app):
        if not v["host"]:
            return
        app.pop()
        app.task(f"Ping {v['host']}", basics.ping, target=v["host"], count=v["count"], size=v["size"])
    gw = util.default_gateway() or ""
    app.push(Form("Ping", [Field("host", "Host / IP", host or gw), Field("count", "Count (blank=forever)", ""),
                           Field("size", "Size bytes", "")], go))


def do_trace(app, host: str = ""):
    from . import basics

    def go(v, app):
        if not v["host"]:
            return
        app.pop()
        app.task(f"Trace {v['host']}", basics.traceroute, target=v["host"])
    app.push(Form("Traceroute", [Field("host", "Host / IP", host or "1.1.1.1")], go))


def do_scan(app):
    from . import scan

    def go(v, app):
        app.pop()
        app.task("IP scan", scan.scan, on_pick=pick_host, iface=ifc(v))
    app.push(Form("IP scan", [app.iface_field()], go, "Uses arp-scan; names via DNS, mDNS, NetBIOS"))


def do_findall(app):
    from . import scan

    def go(v, app):
        app.pop()
        app.task("Find all devices", scan.find_all, on_pick=pick_host, iface=ifc(v))
    app.push(Form("Find all devices", [app.iface_field()], go, "Your IP, gateway, DNS, then every device"))


def do_findav(app):
    from . import av

    def go(v, app):
        app.pop()
        app.task("Find AV gear", av.find_av, on_pick=pick_host, iface=ifc(v))
    app.push(Form("Find AV gear", [app.iface_field()], go,
                  "Crestron, Extron, Q-SYS, Biamp, Shure, Netgear AV Line"))


def pick_host(app, payload):
    """Actions for a device picked from a scan or discovery list."""
    from . import av, scan
    brand = ""
    if isinstance(payload, tuple) and payload and payload[0] == "dev":
        _, brand, h = payload
    else:
        h = payload
    ip = h.ip
    brand = brand or getattr(h, "brand", "")
    label = f"{ip} {h.name or ''}".strip()
    items = []
    if brand and brand in BRAND_ORDER:
        items.append((f"{brand} info pull (login)", lambda app: login_form(app, brand, ip)))
    items += [
        ("Info pull: pick brand", lambda app: brand_menu(app, ip)),
        ("Control-port check", lambda app: app.task(f"Ports {ip}", av.port_check, ip=ip)),
        ("Ping", lambda app: do_ping(app, ip)),
        ("Traceroute", lambda app: do_trace(app, ip)),
        ("Terminal (Telnet/SSH/TCP)", lambda app: term(app, ip)),
        ("IP conflict check", lambda app: conflict_ip(app, ip)),
    ]
    app.push(Menu(label[:30], items))


def term(app, ip="", proto="Telnet"):
    from . import terminal
    terminal.setup(app, ip, proto)


def conflict_ip(app, ip):
    from . import checks
    app.task(f"Conflict {ip}", checks.conflict, target=ip)


BRAND_ORDER = ["Crestron", "Extron", "Q-SYS", "Biamp", "Shure", "Netgear"]


# ---------------------------------------------------------------- reports out

class ShareScreen(Screen):
    """QR code + address of the reports download page (Nomad standard)."""
    title = "Download reports"

    def __init__(self, app):
        self.app = app
        if not app.share.on:
            app.share.start()
        self.pick = 0

    def addrs(self):
        out = [(i.name, i.ip) for i in util.interfaces() if i.ip and not i.ip.startswith("127.")]
        return sorted(out, key=lambda a: (not a[0].startswith("wl"), a[0]))

    def view(self, rows: int) -> View:
        sh = self.app.share
        if not sh.on:
            return View(self.title, [Line("Sharing is OFF", (255, 190, 60)), Line(""),
                                     Line("OK puts your reports on a", DIM), Line("web page for a phone/laptop.", DIM)],
                        "OK turn on  Esc back")
        a = self.addrs()
        if not a:
            return View(self.title, [Line("No IP address yet.", (255, 95, 95)),
                                     Line("Join Wi-Fi or plug in a cable.", DIM)], "Esc back")
        ifc, ip = a[self.pick % len(a)]
        url = f"http://{ip}:{sh.port}/"
        lines = [Line("Scan or open:", DIM), Line(ip, (110, 230, 120)), Line(f":{sh.port} {ifc}", (110, 230, 120)),
                 Line("Tap a file to", DIM), Line("download it.", DIM)]
        more = "  F/X IP" if len(a) > 1 else ""
        return View(self.title, lines, "OK turn off" + more + "  Esc back", qr=url)

    def on_key(self, k, app) -> None:
        if k.name == "enter":
            if app.share.on:
                app.share.stop()
            elif not app.share.start():
                app.toast(app.share.error or "Couldn't start the web page.")
        elif k.up:
            self.pick -= 1
        elif k.down:
            self.pick += 1
        elif k.nav in ("esc", "left"):
            app.pop()


def reports_out_menu(app):
    from . import share
    app.push(Menu("Download reports", [
        ("Web page + QR code", lambda app: app.push(ShareScreen(app))),
        ("Copy all to USB stick", lambda app: app.task("Copy to USB", share.copy_all)),
    ]))


# ---------------------------------------------------------------- Crestron NVX

class Confirm(Screen):
    """Asks before something changes customer gear. OK = do it, Esc = cancel."""

    def __init__(self, title: str, lines: list, go):
        self.title, self.lines, self.go = title, lines, go
        self.hint = "OK = yes, do it   Esc = cancel"

    def view(self, rows: int) -> View:
        return View(self.title, [Line(t, (255, 190, 60) if i == 0 else (235, 235, 235))
                                 for i, t in enumerate(self.lines)], self.hint)

    def on_key(self, k, app) -> None:
        if k.name == "enter":
            app.pop()
            self.go(app)
        elif k.nav in ("esc", "left"):
            app.pop()


def nvx_menu(app):
    from . import crestron_setup as cs

    def find(app):
        app.task("Find NVX", cs.scan, iface="")

    def setup(app):
        def go(v, app):
            if not v["user"] or not v["password"]:
                return app.toast("Enter a user name and password.")
            if v["password"] != v["again"]:
                return app.toast("The two passwords don't match.")
            if len(v["password"]) < 8:
                return app.toast("NVX needs a password of 8 or more characters.")
            app.pop()
            user, pw = v["user"], v["password"]
            found = {}

            def scan_task(out):
                devs = cs.scan(out, "", True)
                todo = [d for d in devs if d.get("state") == "needs setup"]
                found["todo"] = todo
                if todo:
                    out.item(f">> Set up these {len(todo)} NVX (OK)", ("setup", todo), (255, 190, 60))
                else:
                    out.ok("Nothing to set up: every NVX already has an admin account.")

            def picked(app, payload):
                if not (isinstance(payload, tuple) and payload[0] == "setup"):
                    return
                todo = payload[1]
                lines = [f"Create admin '{user}' on {len(todo)} NVX?",
                         "This changes the devices.", ""] + [f"{d['ip']} {d['host'][:14]}" for d in todo]

                def do(app):
                    def run(out):
                        done = []
                        for d in todo:
                            if out.stopped:
                                break
                            out.dim(f"{d['ip']}: creating admin account...")
                            ok = cs.create_admin(d["ip"], user, pw, out.warn)
                            out.replace_last(f"{d['ip']}: {'admin set, login checked' if ok else 'FAILED'}",
                                             (110, 230, 120) if ok else (255, 95, 95))
                            if ok:
                                done.append(d)
                        if done:
                            out.head("Settings capture")
                            cs.capture(out, app.cfg, done, user, pw, "NVX-setup")
                    app.task("NVX setup", run)
                app.push(Confirm("Set up NVX", lines, do))
            app.task("NVX needing setup", scan_task, on_pick=picked)
        app.push(Form("Set up new NVX", [
            Field("user", "Admin user", "admin"),
            Field("password", "Password", "", secret=True),
            Field("again", "Password again", "", secret=True)], go,
            "Typed here only, never saved. You confirm before anything changes."))

    def capture(app, nvx_only=True):
        what = "NVX" if nvx_only else "Crestron"

        def go(v, app):
            app.pop()

            def run(out):
                devs = cs.discover(out, "", nvx_only)
                if not devs:
                    return out.warn(f"No {what} devices found.")
                out.replace_last(f"{len(devs)} {what} device(s). Logging in...", DIM)
                cs.capture(out, app.cfg, devs, v["user"], v["password"], f"{what}-settings")
            app.task(f"{what} settings capture", run)
        app.push(Form(f"{what} settings capture", [
            Field("user", "User", "admin"),
            Field("password", "Password", "", secret=True)], go,
            "Same login for every device. Not saved."))

    app.push(Menu("Crestron NVX", [
        ("Find NVX (needs setup?)", find),
        ("Set up new NVX (password)", setup),
        ("NVX settings capture (CSV)", capture),
        ("All Crestron capture (CSV)", lambda app: capture(app, False)),
    ]))


def brand_menu(app, ip: str = ""):
    items = [(("Netgear AV Line" if b == "Netgear" else b), (lambda app, b=b: login_form(app, b, ip)))
             for b in BRAND_ORDER]
    app.push(Menu("Device info pull", items))


def login_form(app, brand: str, ip: str = ""):
    from . import devices
    fn, modes, default_user = devices.BRANDS[brand]
    kind = brand.lower()
    saved = store.get_login(kind, ip) if ip else None
    needs_login = brand not in ("Shure",)
    fields = [Field("ip", "IP", ip)]
    if needs_login:
        fields += [Field("user", "User", (saved or {}).get("user", default_user)),
                   Field("password", "Password", (saved or {}).get("password", ""), secret=True)]
    if len(modes) > 1:
        fields.append(Field("mode", "Connect by", modes[0], choices=modes))
    if needs_login:
        fields.append(Field("save", "Save this login", "Yes" if saved else "No", choices=["No", "Yes"]))
    note = {"Crestron": "SSH; falls back to port 41795 for old gear. ERRLOG not pulled.",
            "Q-SYS": "Leave user blank if the core has no QRC login.",
            "Biamp": "Tesira default user is 'default', blank password.",
            "Shure": "Uses the Shure command port 2202 (no login).",
            "Netgear": "AV Line M4250/M4300/M4350. Run IP scan first to name MACs.",
            "Extron": "Password only if the unit has one set."}.get(brand, "")

    def go(v, app):
        if not v["ip"]:
            return
        user, pw = v.get("user", ""), v.get("password", "")
        if v.get("save") == "Yes":
            store.put_login(kind, v["ip"], user, pw)
        elif saved:
            store.forget_login(kind, v["ip"])
        app.pop()
        app.task(f"{brand} {v['ip']}", fn, ip=v["ip"], user=user, password=pw,
                 mode=v.get("mode", modes[0]))
    app.push(Form(f"{brand} login", fields, go, note + " Login is not saved unless you pick Yes."))


def av_menu(app):
    from . import av

    def withform(title, fn, secs=None, iface_default=""):
        def f(app):
            fields = [app.iface_field(iface_default)]
            if secs:
                fields.append(Field("seconds", "Seconds", secs))

            def go(v, app):
                app.pop()
                kw = {"iface": ifc(v)}
                if secs:
                    kw["seconds"] = v["seconds"]
                app.task(title, fn, on_pick=pick_host, **kw)
            app.push(Form(title, fields, go))
        return f

    app.push(Menu("AV discovery", [
        ("Dante devices", withform("Dante devices", av.dante)),
        ("Dante health (PTP, floods)", withform("Dante health", av.dante_health, "30", util.wired_name())),
        ("NDI sources", withform("NDI sources", av.ndi)),
        ("AES67 / SAP streams", withform("AES67 / SAP", av.sap, "35")),
        ("PTP leader view", withform("PTP leader", av.ptp_view, "15", util.wired_name())),
        ("Multicast / IGMP", withform("Multicast / IGMP", av.multicast, "30", util.wired_name())),
        ("All mDNS services", withform("mDNS services", av.mdns_all)),
    ]))


def capture_menu(app):
    from . import sniff

    def sniffer(app):
        def go(v, app):
            app.pop()
            app.task("Control sniffer", sniff.sniffer, iface=ifc(v), extra=v["extra"])
        app.push(with_steps(Form("Control sniffer", [app.iface_field(util.wired_name()), Field("extra", "Extra ports", "")], go,
                                 "Plug into a mirror (SPAN) port of the controller's port."),
                            "Mirror port setup", MIRROR_STEPS, "steps-mirror"))

    def pcap(app):
        def go(v, app):
            app.pop()
            app.task("pcap capture", sniff.pcap, cfg=app.cfg, iface=ifc(v), bpf=v["bpf"], seconds=v["seconds"],
                     max_mb=v["mb"])
        app.push(with_steps(Form("pcap capture", [app.iface_field(util.wired_name()), Field("bpf", "Filter (tcpdump)", ""),
                                                  Field("seconds", "Seconds (blank=until Q)", ""), Field("mb", "Max MB", "100")],
                                 go, "Saved in the site's report folder."),
                            "Mirror port setup", MIRROR_STEPS, "steps-mirror"))

    app.push(Menu("Capture", [("Control-protocol sniffer", sniffer), ("pcap capture (mirror port)", pcap)]))


def checks_menu(app):
    from . import av, checks

    def simple(title, fn, secs=None, extra=None, steps=None):
        def f(app):
            fields = [app.iface_field(util.wired_name())]
            if secs:
                fields.append(Field("seconds", "Seconds", secs))

            def go(v, app):
                app.pop()
                kw = {"iface": ifc(v)}
                if secs:
                    kw["seconds"] = v["seconds"]
                app.task(title, fn, **kw)
            form = Form(title, fields, go)
            if steps:
                with_steps(form, *steps)
            app.push(form)
        return f

    def ports(app):
        def go(v, app):
            if v["ip"]:
                app.pop()
                app.task(f"Ports {v['ip']}", av.port_check, ip=v["ip"], extra_ports=v["extra"])
        app.push(Form("Control-port check", [Field("ip", "IP", ""), Field("extra", "Extra ports", "")], go))

    app.push(Menu("Network checks", [
        ("DHCP: rogue server check", simple("DHCP check", checks.dhcp, "6")),
        ("IP conflict check", simple("IP conflict", checks.conflict)),
        ("LLDP/CDP switch port, VLAN", simple("LLDP / CDP", checks.lldp, "65", steps=(
            "Switch port setup", [
                "Plug this device's Ethernet port straight into the switch port you want to check.",
                "LLDP (or CDP on Cisco) must be on in the switch. It is on by default on most "
                "managed switches; Netgear AV Line: System > LLDP.",
                "Switches send it every 30 to 60 s, so the check listens for about a minute."],
            "steps-lldp"))),
        ("Link / cable errors", simple("Link check", checks.link, "15")),
        ("Control-port check", ports),
    ]))


def site_menu(app):
    def set_site(app):
        def go(v, app):
            app.cfg["site"] = v["site"]
            app.cfg.write()
            app.pop()
        app.push(Form("Site name", [Field("site", "Site", app.site)], go,
                      "Reports are saved under this site. Press S on any result."))

    def browse(app):
        sites = reports.sites()
        if not sites:
            app.toast("No reports yet. Press S on a result screen to save one.")
            return
        items = [(s, (lambda app, s=s: browse_site(app, s))) for s in sites]
        app.push(Menu("Report sites", items))

    def browse_site(app, s):
        files = reports.files(s)
        items = [(f, (lambda app, f=f: view_file(app, s, f))) for f in files[:60]]
        app.push(Menu(s, items or [("(empty)", lambda app: None)]))

    def view_file(app, s, f):
        path = os.path.join(reports.ROOT, s, f)
        if f.endswith(".pcap"):
            app.toast(f"{path} ({os.path.getsize(path) / 1e6:.1f} MB). Open it in Wireshark.")
            return
        try:
            lines = open(path, errors="replace").read().splitlines()
        except OSError as e:
            lines = [str(e)]
        app.push(Message(f[:30], lines, "F/X scroll  Esc back"))

    app.push(Menu("Site & reports", [
        (lambda: f"Site: {app.site or '(not set)'}", set_site),
        ("Download reports (QR/USB)", reports_out_menu),
        ("Browse saved reports", browse),
        ("Where reports are saved", lambda app: app.toast(reports.ROOT)),
    ]))


def settings_menu(app):
    def iface(app):
        def go(v, app):
            app.cfg["iface"] = "" if v["iface"] == "auto" else v["iface"]
            app.cfg.write()
            app.pop()
        app.push(Form("Default interface", [app.iface_field()], go))

    def forget(app):
        n = store.forget_all_logins()
        app.toast(f"Forgot {n} saved login(s).")

    def about(app):
        app.push(AboutScreen())

    def device_info(app):
        app.push(Message("Device info", [
            f"Keyboard: {app.kb.path if app.kb else 'window keys'}",
            f"LCD: {app.r.lcd_dev or 'none'}  Desktop window: {'yes' if app.r.win else 'no'}",
            "Arrows: Fn+F up, Fn+X down, Fn+Z back, Fn+C open",
            "(F/X/Z/C alone also work in lists). OK opens.",
            "Esc back, S save, R rerun, Q stop.",
        ]))

    app.push(Menu("Settings", [
        ("Keyboard test", lambda app: app.push(KeyTest())),
        ("Default interface", iface),
        ("Forget all saved logins", forget),
        ("Device info", device_info),
        ("About", about),
    ]))


class KeyTest(Screen):
    """Shows what each key press arrives as. Esc twice to leave."""
    title = "Keyboard test"
    hint = "Press keys  Esc twice to leave"

    def __init__(self):
        self.seen: list[str] = []
        self.esc = 0

    def view(self, rows: int) -> View:
        return View(self.title, [Line(t) for t in (self.seen[-(rows):] or ["Press any key..."])], self.hint)

    def on_key(self, k, app) -> None:
        raw = " ".join(app.kb.last_raw[-2:]) if app.kb else ""
        what = "UP" if k.up else "DOWN" if k.down else (k.name or repr(k.char))
        self.seen.append(f"{what:<6} {raw}")
        self.esc = self.esc + 1 if k.name == "esc" else 0
        if self.esc >= 2:
            app.pop()


def main_menu(app) -> Menu:
    from . import terminal
    return Menu("Nomad Net Tools", [
        ("Find all devices", do_findall),
        ("Network basics", do_basics),
        ("Ping", do_ping),
        ("Traceroute", do_trace),
        ("IP scan (MAC maker, names)", do_scan),
        ("Find AV gear", do_findav),
        ("AV discovery (Dante, NDI)", av_menu),
        ("Device login & info pull", lambda app: brand_menu(app)),
        ("Crestron NVX setup/capture", nvx_menu),
        ("Terminal (SSH/Telnet/serial)", lambda app: terminal.setup(app)),
        ("Capture (sniffer, pcap)", capture_menu),
        ("Network checks", checks_menu),
        (lambda: f"Site & reports [{app.site or 'no site'}]", site_menu),
        ("Settings / about", settings_menu),
        ("About", lambda app: app.push(AboutScreen())),
    ], "F/X move  OK open  hold Esc exit",
        softkeys=[("Find all", do_findall), ("Ping", do_ping), ("AV gear", do_findav),
                  ("Term", lambda app: terminal.setup(app)), ("Reports", site_menu)],
        help="AV network tools. Find all devices lists everything on the network with "
             "this device's own IP, gateway and DNS. Find AV gear looks for Crestron, "
             "Extron, Q-SYS, Biamp, Shure and Netgear AV switches. Results show on HDMI "
             "too when a display is connected. TheTechNomad.com")


def shots(out_dir: str) -> None:
    """Store screenshots (LCD size) with sample content, no hardware needed."""
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    from .ui import OK, WARN, HEAD, Line, Output, TaskScreen as TS
    app = App(window=False)
    app.cfg["site"] = "Demo Site"
    os.makedirs(out_dir, exist_ok=True)
    m = main_menu(app)
    app.r.render(m, "Demo Site", force=True, keys=m.keybar(app))
    app.r.save_lcd(os.path.join(out_dir, "lcd_0.png"))

    samples = [
        ("Find AV gear", [("6 AV device(s) found", DIM), ("-- Crestron", HEAD),
                          ("192.168.1.20    CP4 v2.8001.00123        CP4-Lobby", (130, 200, 255)),
                          ("   00:10:7f:aa:10:20  ports: 22 SSH, 41794 CIP", DIM), ("-- Q-SYS", HEAD),
                          ("192.168.1.30    Core 110f                Core-1", (130, 200, 255)),
                          ("-- Netgear AV Line", HEAD),
                          ("192.168.1.2     M4250-26G4F-PoE+         SW-Rack", (130, 200, 255))]),
        ("Dante health", [("Watched 30s, 48211 packets", DIM), ("-- PTPv1 (Dante) domain _DFLT", HEAD),
                          ("Leader 00:1d:c1:12:34:56  192.168.1.40 DSP", OK),
                          ("   120 Sync, every 250 ms, worst gap 252 ms", None), ("No leader changes while watching.", OK),
                          ("-- IGMP", HEAD), ("Querier 192.168.1.2 (1 queries)", OK),
                          ("-- Multicast reaching this port", HEAD),
                          ("None that we did not ask for. Snooping looks OK.", OK)]),
    ]
    for n, (title, lines) in enumerate(samples, 1):
        t = TS(title, None, autostart=False)
        for text, color in lines:
            t.out.line(text, color or (235, 235, 235))
        t.cur = 0
        app.r.render(t, "Demo Site", force=True, keys=t.keybar(app))
        app.r.save_lcd(os.path.join(out_dir, f"lcd_{n}.png"))
    print("screenshots in", out_dir)


def main() -> None:
    ap = argparse.ArgumentParser(description="Nomad Net Tools")
    ap.add_argument("--no-window", action="store_true", help="LCD only, no desktop window")
    ap.add_argument("--shots", metavar="DIR", help="write store screenshots and exit")
    ap.add_argument("--keys", default="", help=argparse.SUPPRESS)
    ap.add_argument("--version", action="version", version=VERSION)
    a = ap.parse_args()
    if a.shots:
        shots(a.shots)
        return
    app = App(window=not a.no_window)
    try:
        app.run(a.keys)
    finally:
        app.share.stop()
    pygame.quit()


if __name__ == "__main__":
    main()
