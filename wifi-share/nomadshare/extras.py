"""The power features: devices (rename, block, speed limit, reservations),
live dashboard, port forwards, local DNS names, switch port (LLDP), VPN
(WireGuard), the settings menu and the first-run setup wizard."""
from __future__ import annotations

import collections
import threading
import time

from . import lcd as L
from . import net, oui, qr, wgweb
from . import settings as S
from . import nomadui as T
from .ui import SELECT, Busy, Confirm, Form, Menu, Message, Screen, wrap  # noqa: F401

SPEED_CHOICES = ["Off"] + [f"{v} Mb/s" for v in S.SPEEDS[1:]]


def save_rules(app, msg_title="Saved"):
    """Store settings, then load them into the running share (if any)."""
    S.save(app.cfg)
    app.run_task(msg_title, "Loading rules", lambda: net.apply_rules(app.cfg), ok_title=msg_title)


def lan_net(app) -> tuple[str, str]:
    """(Pi address, mask) of the shared side for the running mode."""
    st, c = app.status, app.cfg
    if st.get("mode") == "hotspot" and not st.get("bridge"):
        return c["hs_ip"], "24"
    return c["share_ip"], c["share_mask"]


# ---- devices --------------------------------------------------------------------

def device_label(app, mac: str, seen: dict | None = None) -> str:
    d = (app.cfg.get("devices") or {}).get(mac) or {}
    if d.get("name"):
        return d["name"]
    if seen and seen.get("name"):
        return seen["name"]
    v = oui.vendor(mac)
    b = oui.brand(v)
    return (b or v.split(",")[0].split(" ")[0] or mac)[:12] + " " + mac[-5:].replace(":", "")


class Devices(Menu):
    """Everything on the shared side plus devices you set up before."""
    help = ("Devices on the shared side. OK opens one: rename it, block its internet, give it a "
            "fixed IP (DHCP reservation) or a speed limit. Devices you set up stay listed even "
            "when they're unplugged (grey). Red = blocked.")

    def __init__(self, app):
        super().__init__(app, "Devices", [], "OK edit  4 block  5 rename  ← back")
        self.macs: list[str] = []
        self.refresh_items()

    def refresh_items(self):
        app = self.app
        seen = {c["mac"]: c for c in app.status.get("clients", [])}
        known = app.cfg.get("devices") or {}
        macs = list(seen) + [m for m in known if m not in seen]
        if macs != self.macs:
            self.macs = macs
            self.items = [(lambda m=m: self._label(m), lambda m=m: self.edit(m),
                           lambda m=m: self._right(m), lambda m=m: self._color(m)) for m in macs]
            if not self.items:
                self.items = [("No devices yet", None, "", L.DIM)]
            self.sel = min(self.sel, len(self.items) - 1)

    def _seen(self, m):
        return next((c for c in self.app.status.get("clients", []) if c["mac"] == m), None)

    def _label(self, m):
        d = (self.app.cfg.get("devices") or {}).get(m) or {}
        tags = ("R " if d.get("reserve") else "") + ("L " if d.get("down", "Off") != "Off" or d.get("up", "Off") != "Off" else "")
        return tags + device_label(self.app, m, self._seen(m))

    def _right(self, m):
        c = self._seen(m)
        if (self.app.cfg.get("devices") or {}).get(m, {}).get("block"):
            return "BLOCKED"
        return (c or {}).get("ip") or (self.app.cfg["devices"].get(m, {}).get("ip") or "away")

    def _color(self, m):
        d = (self.app.cfg.get("devices") or {}).get(m) or {}
        if d.get("block"):
            return L.BAD
        return L.FG if self._seen(m) else L.DIM

    def current_mac(self):
        return self.macs[self.sel] if self.macs and self.sel < len(self.macs) else None

    def softkeys(self):
        m = self.current_mac()
        if not m:
            return []
        blocked = (self.app.cfg.get("devices") or {}).get(m, {}).get("block")
        return [(("Unblock", L.OK) if blocked else ("Block", L.BAD), self.toggle_block),
                ("Rename", self.rename), ("Edit", lambda: self.edit(m))]

    def toggle_block(self):
        m = self.current_mac()
        d = self.app.cfg.setdefault("devices", {}).setdefault(m, {})
        d["block"] = not d.get("block")
        save_rules(self.app, "Blocked" if d["block"] else "Unblocked")

    def rename(self):
        from .ui import Prompt
        m = self.current_mac()
        d = self.app.cfg.setdefault("devices", {}).setdefault(m, {})

        def done(v):
            d["name"] = v.strip()[:24]
            save_rules(self.app, "Renamed")
        self.app.push(Prompt(self.app, "Rename", "Name", d.get("name", ""), done,
                             help_text="A name you'll recognise, like DSP rack 1."))

    def edit(self, m):
        edit_device(self.app, m, self._seen(m))

    def tick(self):
        self.refresh_items()


def edit_device(app, mac: str, seen: dict | None = None):
    d = dict((app.cfg.get("devices") or {}).get(mac) or {})
    v = oui.vendor(mac) or "unknown maker"
    vals = dict(name=d.get("name", ""), block=bool(d.get("block")), reserve=bool(d.get("reserve")),
                ip=d.get("ip") or (seen or {}).get("ip", ""), down=d.get("down", "Off"), up=d.get("up", "Off"))
    fields = [
        ("", f"{mac}  {v[:16]}", "header"),
        ("name", "Name", "text", "Shown in the list"),
        ("block", "Internet", "bool", ("Blocked", "Allowed")),
        ("reserve", "Fixed IP", "bool", ("Reserved", "Any (DHCP)")),
        ("ip", "IP address", "ip", "Inside the shared network", lambda x: x["reserve"]),
        ("down", "Download limit", "choice", SPEED_CHOICES),
        ("up", "Upload limit", "choice", SPEED_CHOICES),
    ]
    pi_ip, mask = lan_net(app)

    def validate(x):
        limited = x["down"] != "Off" or x["up"] != "Off"
        if limited and not x["reserve"]:
            if S.valid_ip(x["ip"]):
                x["reserve"] = True     # a limit needs a fixed IP: reserve the one it has
            else:
                return "A speed limit needs a fixed IP. Turn on Fixed IP and enter one."
        if x["reserve"]:
            if not S.in_subnet(x["ip"], pi_ip, mask) or x["ip"].strip() == pi_ip:
                return f"IP must be inside {pi_ip}/{S.mask_to_prefix(mask)} and not the Pi's own."
            for m2, d2 in (app.cfg.get("devices") or {}).items():
                if m2 != mac and d2.get("reserve") and d2.get("ip") == x["ip"].strip():
                    return f"{x['ip']} is already reserved for {d2.get('name') or m2}."
        return None

    def saved(x):
        x["ip"] = x["ip"].strip()
        app.cfg.setdefault("devices", {})[mac] = dict(x)
        save_rules(app)

    def forget():
        app.cfg.get("devices", {}).pop(mac, None)
        app.pop()
        save_rules(app, "Forgotten")
    app.push(Form(app, "Device", fields, vals, saved, validate, actions=[("Forget this device", forget, L.BAD)],
                  help="Settings for one device. A speed limit needs a fixed IP, so the app reserves it."))


class Reservations(Menu):
    help = ("DHCP reservations: a device always gets the same IP from the Pi. Add gear before it's "
            "plugged in by typing its MAC address (on its label).")

    def __init__(self, app):
        super().__init__(app, "DHCP reservations", [], "OK edit  4 add  ← back")
        self.build()

    def build(self):
        devs = self.app.cfg.get("devices") or {}
        res = [(m, d) for m, d in devs.items() if d.get("reserve")]
        res.sort(key=lambda md: tuple(int(p) for p in md[1].get("ip", "0.0.0.0").split(".")) if S.valid_ip(md[1].get("ip", "")) else (999,))
        self.items = [((d.get("name") or m)[:18], lambda m=m: edit_device(self.app, m), d.get("ip", "")) for m, d in res]
        self.items.append(("+ Add by MAC address", self.add, "", L.OK))
        self.sel = min(self.sel, len(self.items) - 1)

    def softkeys(self):
        return [("Add", self.add)]

    def add(self):
        app = self.app
        pi_ip, mask = lan_net(app)
        base = pi_ip.rsplit(".", 1)[0] + "."

        def validate(x):
            if not S.norm_mac(x["mac"]):
                return "MAC looks like 00:05:a6:1c:22:10"
            if not S.in_subnet(x["ip"], pi_ip, mask) or x["ip"].strip() == pi_ip:
                return f"IP must be inside {pi_ip}/{S.mask_to_prefix(mask)}"
            return None

        def saved(x):
            m = S.norm_mac(x["mac"])
            d = app.cfg.setdefault("devices", {}).setdefault(m, {})
            d.update(name=x["name"].strip(), reserve=True, ip=x["ip"].strip())
            save_rules(app)
        app.push(Form(app, "Add reservation", [
            ("mac", "MAC address", "text", "From the device label"),
            ("name", "Name", "text", "Like Projector left"),
            ("ip", "IP address", "ip", f"Inside {pi_ip}/{S.mask_to_prefix(mask)}"),
        ], dict(mac="", name="", ip=base + "20"), saved, validate))

    def tick(self):
        self.build()


# ---- port forwards and local names ------------------------------------------------

class Forwards(Menu):
    help = ("Port forwards let a laptop on the Wi-Fi (uplink) side reach a device on the shared "
            "side: the Pi's uplink address and the outside port go to the device's IP and port. "
            "Handy for a control processor's web page or a DSP's software port.")

    def __init__(self, app):
        super().__init__(app, "Port forwards", [], "OK edit  4 add  ← back")
        self.build()

    def build(self):
        fw = self.app.cfg.get("forwards") or []
        self.items = [((f.get("name") or f"Forward {i + 1}")[:14], lambda i=i: self.edit(i),
                       f"{f.get('proto', 'tcp').upper()[:4]} {f.get('port')}>{f.get('ip', '').split('.')[-1] if f.get('ip') else '?'}:{f.get('to_port') or f.get('port')}")
                      for i, f in enumerate(fw)]
        self.items.append(("+ Add port forward", lambda: self.edit(None), "", L.OK))
        self.sel = min(self.sel, len(self.items) - 1)

    def softkeys(self):
        return [("Add", lambda: self.edit(None))]

    def edit(self, i):
        app = self.app
        fw = app.cfg.setdefault("forwards", [])
        cur = dict(fw[i]) if i is not None else dict(name="", proto="TCP", port="8080", ip="", to_port="80")
        cur["proto"] = cur.get("proto", "TCP").upper() if cur.get("proto", "").lower() != "both" else "Both"
        pi_ip, mask = lan_net(app)

        def validate(x):
            if not S.check_port(x["port"]):
                return "Outside port must be 1-65535"
            if not S.check_port(x["to_port"] or x["port"]):
                return "Device port must be 1-65535"
            if not S.in_subnet(x["ip"], pi_ip, mask):
                return f"Device IP must be inside {pi_ip}/{S.mask_to_prefix(mask)}"
            if x["port"].strip() == "22":
                return "Port 22 is the Pi's own SSH. Pick another outside port."
            return None

        def saved(x):
            x = {k: str(v).strip() for k, v in x.items()}
            x["proto"] = x["proto"].lower()
            if i is None:
                fw.append(x)
            else:
                fw[i] = x
            save_rules(app)

        def delete():
            fw.pop(i)
            app.pop()
            save_rules(app, "Deleted")
        app.push(Form(app, "Port forward", [
            ("name", "Name", "text", "Like DSP web page"),
            ("proto", "Protocol", "choice", ["TCP", "UDP", "Both"]),
            ("port", "Outside port", "number", "Port on the Pi's uplink"),
            ("ip", "Device IP", "ip", f"Inside {pi_ip}/{S.mask_to_prefix(mask)}"),
            ("to_port", "Device port", "number", "Like 80 for a web page"),
        ], cur, saved, validate, actions=[("Delete", delete, L.BAD)] if i is not None else None))

    def tick(self):
        self.build()


class DnsNames(Menu):
    help = ("Local names: devices on the shared side can use a name instead of an IP, like "
            "dsp1.av. Names need a dot; avoid .local (that's mDNS).")

    def __init__(self, app):
        super().__init__(app, "Local DNS names", [], "OK edit  4 add  ← back")
        self.build()

    def build(self):
        names = self.app.cfg.get("dns_names") or []
        self.items = [(n.get("name", "")[:18], lambda i=i: self.edit(i), n.get("ip", "")) for i, n in enumerate(names)]
        self.items.append(("+ Add name", lambda: self.edit(None), "", L.OK))
        self.sel = min(self.sel, len(self.items) - 1)

    def softkeys(self):
        return [("Add", lambda: self.edit(None))]

    def edit(self, i):
        app = self.app
        names = app.cfg.setdefault("dns_names", [])
        cur = dict(names[i]) if i is not None else dict(name="", ip="")

        def validate(x):
            if not S.NAME_RE.match(x["name"].strip()) or "." not in x["name"]:
                return "Name like dsp1.av (letters, digits, dashes and a dot)"
            if x["name"].strip().lower().endswith(".local"):
                return ".local is used by mDNS. Try .av or .lan"
            if not S.valid_ip(x["ip"]):
                return "Bad IP address"
            return None

        def saved(x):
            x = dict(name=x["name"].strip().lower(), ip=x["ip"].strip())
            if i is None:
                names.append(x)
            else:
                names[i] = x
            save_rules(app)

        def delete():
            names.pop(i)
            app.pop()
            save_rules(app, "Deleted")
        app.push(Form(app, "Local name", [("name", "Name", "text", "Like dsp1.av"), ("ip", "IP address", "ip")],
                      cur, saved, validate, actions=[("Delete", delete, L.BAD)] if i is not None else None))

    def tick(self):
        self.build()


def edit_speed_all(app):
    def saved(x):
        app.cfg.update(x)
        save_rules(app)
    app.push(Form(app, "Speed limit (all)", [
        ("", "All shared devices together", "header"),
        ("limit_all_down", "Download", "choice", SPEED_CHOICES),
        ("limit_all_up", "Upload", "choice", SPEED_CHOICES),
    ], dict(limit_all_down=app.cfg.get("limit_all_down") or "Off", limit_all_up=app.cfg.get("limit_all_up") or "Off"),
        saved, help="Keeps the shared devices from using all of a slow or metered uplink."))


# ---- dashboard ------------------------------------------------------------------

class Dashboard(Screen):
    title = "Dashboard"
    hint = "←/ESC back"
    help = ("Live view of the uplink: download (green) and upload (blue) for the last minute, "
            "link speed, gateway ping and the internet check.")
    N = 60

    def __init__(self, app, live: bool = True):
        super().__init__(app)
        self.down = collections.deque([0.0] * self.N, maxlen=self.N)
        self.up = collections.deque([0.0] * self.N, maxlen=self.N)
        self.last = None
        self.dev = None
        self.ping_ms: float | None = None
        self.ping_ok = True
        self.loss = collections.deque(maxlen=20)
        self.links = ""
        self.running = live
        if live:
            threading.Thread(target=self._pinger, daemon=True).start()

    def softkeys(self):
        return [("Devices", lambda: self.app.push(Devices(self.app))), None, None,
                ("Status", lambda: self.app.push(__import__("nomadshare.app", fromlist=["Status"]).Status(self.app)))]

    def _pinger(self):
        while self.running and not self.app.quit:
            r = self.app.status.get("route")
            if r:
                ms = net.ping(r[0])
                self.ping_ms = ms
                self.loss.append(ms is None)
            st = self.app.status
            parts = []
            if st.get("wifi") and st.get("mode") != "hotspot" and st.get("wifi_link"):
                rate = net.wifi_rate(st["wifi"])
                parts.append(f"Wi-Fi {st['wifi_link'].get('signal', 0)}%" + (f" {rate}" if rate else ""))
            elif st.get("mode") == "hotspot":
                parts.append("Hotspot")
            if st.get("eth"):
                sp = net.eth_speed(st["eth"]) if st.get("eth_carrier") else ""
                parts.append(f"Eth {sp or ('up' if st.get('eth_carrier') else 'no cable')}")
            self.links = "  ".join(parts)
            self.app.dirty = True
            time.sleep(2)

    def tick(self):
        r = self.app.status.get("route")
        dev = r[1] if r else (self.app.status.get("wifi") or "")
        now = time.monotonic()
        if self.last and now - self.last[0] < 1.0:
            return
        rx, tx = net.iface_bytes(dev) if dev else (0, 0)
        if self.last and dev == self.dev:
            dt = now - self.last[0]
            self.down.append(max(0, rx - self.last[1]) * 8 / dt / 1e6)
            self.up.append(max(0, tx - self.last[2]) * 8 / dt / 1e6)
        self.last, self.dev = (now, rx, tx), dev
        self.app.dirty = True

    def key(self, k):
        if k.name in ("esc", "left"):
            self.running = False
        super().key(k)

    def rows(self):
        return [], None

    def paint(self, surf, f):
        sm, big = f["small"], f["row"]
        y0 = T.TITLE_H + 2
        d, u = self.down[-1], self.up[-1]
        surf.blit(big.render(f"↓ {fmt_rate(d)}", True, L.OK), (6, y0))
        surf.blit(big.render(f"↑ {fmt_rate(u)}", True, (120, 170, 255)), (165, y0))
        app = self.app
        loss = sum(self.loss) * 100 // max(1, len(self.loss))
        ping = "no gateway" if not app.status.get("route") else (
            f"GW {self.ping_ms:.1f} ms" if self.ping_ms is not None else "GW no reply")
        inet = {"online": ("online", L.OK), "portal": ("login page", L.WARN),
                "checking": ("checking", L.DIM)}.get(app.inet[0], ("offline", L.BAD))
        surf.blit(sm.render(self.links or "Link: checking...", True, L.FG), (6, y0 + 21), (0, 0, 308, 17))
        line = f"{ping}{f' ({loss}% loss)' if loss else ''}  Clients {len(app.status.get('clients', []))}"
        surf.blit(sm.render(line, True, L.FG if self.ping_ms is not None else L.WARN), (6, y0 + 37))
        # graph
        gx, gy, gw, gh = 6, y0 + 56, T.W - 12, T.H - T.KEYBAR_H - (y0 + 56) - 3
        surf.fill((16, 20, 30), (gx, gy, gw, gh))
        peak = max(1.0, max(self.down), max(self.up))
        scale = nice_ceiling(peak)
        bw = gw / self.N
        for i, v in enumerate(self.down):
            h = int(gh * v / scale)
            if h:
                x0, x1 = gx + int(i * bw), gx + int((i + 1) * bw)
                surf.fill((40, 150, 70), (x0, gy + gh - h, max(1, x1 - x0), h))
        pts = [(gx + int(i * bw + bw / 2), gy + gh - int(gh * v / scale)) for i, v in enumerate(self.up)]
        import pygame
        pygame.draw.lines(surf, (120, 170, 255), False, pts, 2)
        lab = sm.render(f"{scale:g} Mb/s", True, L.DIM)
        surf.blit(lab, (gx + gw - lab.get_width() - 2, gy + 1))
        img = sm.render(inet[0], True, inet[1])
        surf.blit(img, (gx + 3, gy + 1))


W = T.W


def fmt_rate(mbit: float) -> str:
    if mbit >= 10:
        return f"{mbit:.0f} Mb/s"
    if mbit >= 1:
        return f"{mbit:.1f} Mb/s"
    return f"{mbit * 1000:.0f} kb/s"


def nice_ceiling(v: float) -> float:
    for n in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000):
        if v <= n:
            return n
    return v


# ---- switch port (LLDP) ------------------------------------------------------------

class SwitchPort(Screen):
    title = "Switch port"
    steps = T.StepsPopup("Switch port setup", [
        "Plug this device's Ethernet port into the switch port you want to check.",
        "Make sure LLDP is on in the switch (on by default on most managed switches; "
        "Netgear AV Line: System > LLDP).",
        "Press 4 to listen. The switch sends LLDP every 30 s, so it can take that long."],
        key="steps-lldp", ok="OK done")
    hint = "4 listen  ←/ESC back"
    help = ("Which switch and port the Ethernet cable goes to, and the port's VLAN, read from "
            "LLDP (most managed switches, including Netgear AV Line, send it every 30 s). "
            "While sharing to Ethernet it updates by itself; otherwise press 4 to listen for "
            "35 s (the Ethernet port's own connection pauses meanwhile).")

    def __init__(self, app):
        super().__init__(app)
        self.nbrs: list[dict] | None = None
        self.top = 0
        self.at = 0.0

    def softkeys(self):
        return [("Listen", self.listen)]

    def listen(self):
        self.app.run_task("Switch port", "Listening for LLDP (35 s)", lambda: net.lldp_listen(35),
                          after=self._got)

    def _got(self, nbrs):
        self.nbrs = nbrs
        if not nbrs:
            self.app.push(Message(self.app, "Switch port", "No LLDP heard. The switch may have LLDP off, "
                                  "or it's an unmanaged switch.", L.WARN))

    def tick(self):
        if time.monotonic() - self.at > 5:
            self.at = time.monotonic()
            e = self.app.status.get("eth")
            if e and self.app.status.get("mode") == "wifi2eth":
                threading.Thread(target=lambda: setattr(self, "nbrs", net.lldp(e) or self.nbrs), daemon=True).start()

    def all_rows(self):
        st = self.app.status
        rows = []
        if not st.get("eth_carrier"):
            rows.append(("Ethernet", L.WARN, "no cable"))
        if self.nbrs is None:
            rows.append(("Press 4 to listen for LLDP.", L.DIM))
        for n in self.nbrs or []:
            rows += [("Switch", L.OK, n["switch"][:20]), ("Port", L.FG, n["port"][:20])]
            if n["port_id"] and n["port_id"] != n["port"]:
                rows.append(("Port ID", L.DIM, n["port_id"][:20]))
            rows.append(("Port VLAN", L.FG, n["vlan"] or "-"))
            if n["vlan_name"]:
                rows.append(("VLAN name", L.FG, n["vlan_name"][:18]))
            if n["mgmt"]:
                rows.append(("Switch IP", L.FG, n["mgmt"].split(",")[0][:18]))
        tag = st.get("vlan")
        rows.append(("Our VLAN tag", L.FG, tag or "none (untagged)"))
        return rows

    def rows(self):
        rows = self.all_rows()
        self.top = min(self.top, max(0, len(rows) - self.nrows))
        return rows[self.top:self.top + self.nrows], None

    def key(self, k):
        if k.name == "up":
            self.top = max(0, self.top - 1)
        elif k.name == "down":
            self.top += 1
        else:
            super().key(k)


# ---- VPN (WireGuard) ------------------------------------------------------------------

class Vpn(Screen):
    title = "VPN (WireGuard)"
    hint = "←/ESC back"
    help = ("Send everything the shared devices (and the Pi) do through your WireGuard VPN, "
            "for a secure path from a venue network back to the office. Load the .conf with "
            "the web page (6, scan the QR) or from a USB stick (7).")

    def softkeys(self):
        return self._keys()

    def _keys(self):
        on = self.app.status.get("vpn") == "on"
        have = bool(net.wg_info())
        return [((("Turn off", L.BAD), self.off) if on else (("Turn on", L.OK), self.on)) if have else None,
                None,
                ("Web", lambda: self.app.push(VpnWeb(self.app))),
                ("USB", self.usb),
                (("Delete", L.BAD), self.delete) if have and not on else None]

    def rows(self):
        info = net.wg_info()
        st = self.app.status.get("vpn")
        rows = [("VPN", L.OK if st == "on" else L.FG, {"on": "ON", "": "off"}.get(st, st))]
        if not info:
            rows += [("No WireGuard config yet.", L.DIM), ("Press 6 (web) or 7 (USB stick).", L.DIM)]
        else:
            rows += [("Server", L.FG, info.get("endpoint", "?")[:20]),
                     ("VPN address", L.FG, info.get("address", "?")[:18]),
                     ("Routes", L.FG, info.get("allowedips", "?")[:18])]
            if info.get("dns"):
                rows.append(("DNS", L.FG, info["dns"][:18]))
        return rows, None

    def on(self):
        self.app.run_task("VPN", "Starting WireGuard", net.vpn_up, ok_title="VPN")

    def off(self):
        self.app.run_task("VPN", "Stopping WireGuard", net.vpn_down, ok_title="VPN")

    def delete(self):
        import os
        def go():
            try:
                os.remove(S.WG_CONF)
            except OSError:
                pass
        self.app.push(Confirm(self.app, "Delete config", "Remove the saved WireGuard config?", go))

    def usb(self):
        found = wgweb.usb_configs()
        if not found:
            self.app.push(Message(self.app, "USB", "No WireGuard .conf found on a USB stick.", L.WARN))
            return
        items = [(p.rsplit("/", 1)[-1][:24], lambda p=p: self._load(p)) for p in found]
        self.app.push(Menu(self.app, "Pick a config", items))

    def _load(self, path):
        def job():
            return net.wg_save(open(path, errors="replace").read())
        self.app.pop()
        self.app.run_task("VPN", "Loading", job, ok_title="VPN")


class VpnWeb(Screen):
    title = "WireGuard upload"
    hint = "←/ESC stop"
    help = "Scan the code with a phone or open the address on a laptop on the same network, then send the .conf."

    def __init__(self, app):
        super().__init__(app)
        self.up = wgweb.Upload()
        self.ok = self.up.start()

    def ip(self):
        st = self.app.status
        for k in ("wifi_ip", "eth_ip", "bridge_ip"):
            if st.get(k):
                return st[k][0].split("/")[0]
        return "127.0.0.1"

    def rows(self):
        return [], None

    def paint(self, surf, f):
        if not self.ok:
            surf.blit(f["small"].render(f"Web page failed: {self.up.error[:30]}", True, L.BAD), (6, 40))
            return
        url = self.up.url(self.ip())
        y = T.TITLE_H + 3
        size = T.H - T.HINT_H - y - 3
        qr.draw(surf, url, 6, y, size)
        x = size + 14
        lines = wrap_lines(f["small"], ["Scan with a phone on the same Wi-Fi or network.",
                                        "Pick your .conf file and press Send."], T.W - x - 4)
        for i, l in enumerate(lines):
            surf.blit(f["small"].render(l, True, L.FG), (x, y + i * 16))
        yy = y + len(lines) * 16 + 4
        for i, l in enumerate(wrap_chars(url.replace("http://", ""), 17)):
            surf.blit(f["small"].render(l, True, L.WARN), (x, yy + i * 16))

    def tick(self):
        if self.up.received:
            self.up.stop()
            self.app.pop(self)
            self.app.push(Message(self.app, "VPN", "WireGuard config received. Turn the VPN on with 4.", L.OK))
        self.app.dirty = True

    def key(self, k):
        if k.name in ("esc", "left"):
            self.up.stop()
        super().key(k)


def wrap_lines(font, paras, width):
    out = []
    for p in paras:
        out += T.wrap_text(font, p, width)
    return out


def wrap_chars(s, n):
    return [s[i:i + n] for i in range(0, len(s), n)]


# ---- settings menu -----------------------------------------------------------------

class SettingsMenu(Menu):
    help = "All settings. Changes to devices, forwards, names and limits load right away while sharing."

    def __init__(self, app):
        from . import app as A
        c = app.cfg

        def n(key):
            return lambda: str(len(app.cfg.get(key) or [])) if app.cfg.get(key) else ""
        items = [
            ("Ethernet side", lambda: A.edit_ethernet(app),
             lambda: f"VLAN {c['share_vlan']}" if net.vlan_id(app.cfg) else ""),
            ("Hotspot", lambda: A.edit_hotspot(app), lambda: "bridge" if app.cfg.get("hs_bridge") else "NAT"),
            ("DHCP reservations", lambda: app.push(Reservations(app)),
             lambda: str(sum(1 for d in (app.cfg.get("devices") or {}).values() if d.get("reserve"))) or ""),
            ("Port forwards", lambda: app.push(Forwards(app)), n("forwards")),
            ("Local DNS names", lambda: app.push(DnsNames(app)), n("dns_names")),
            ("Speed limit (all)", lambda: edit_speed_all(app),
             lambda: "on" if net._mbit(app.cfg.get("limit_all_down")) or net._mbit(app.cfg.get("limit_all_up")) else "off"),
            ("Setup wizard", lambda: app.push(Wizard(app))),
        ]
        super().__init__(app, "Settings", items)


# ---- first-run wizard -----------------------------------------------------------------

class Wizard(Screen):
    """Five short steps: job, Wi-Fi, gear addressing, (hotspot), start."""
    title = "Setup"
    help = ("A quick setup. Pick what you want to do and answer a couple of questions; "
            "ESC goes back a step. You can run it again from Settings.")

    def __init__(self, app):
        super().__init__(app)
        self.steps = ["welcome"]
        self.sel = 0
        self.job = None
        self.form = None
        self.enter("welcome")

    @property
    def step(self):
        return self.steps[-1]

    @property
    def text_entry(self):
        return bool(self.form and self.form.editing)

    @property
    def hint(self):
        if self.form:
            return self.form.hint()
        return "↑↓ move  OK/→ pick  ESC back"

    def enter(self, step):
        if step != self.steps[-1]:
            self.steps.append(step)
        self.sel = 0
        self.form = None
        c = self.app.cfg
        if step == "addr":
            self.form = T.EditList([T.Field("share_ip", "Pi address", "ip"),
                                    T.Field("share_mask", "Subnet mask", "mask", help="Like 24"),
                                    T.Field("next", "Next", "action")],
                                   dict(share_ip=c["share_ip"], share_mask=c["share_mask"]))
        elif step == "hotspot":
            self.form = T.EditList([T.Field("hs_ssid", "Name (SSID)", "text"),
                                    T.Field("hs_password", "Password", "secret", help="8-63 characters"),
                                    T.Field("hs_bridge", "Network", "bool", choices=("Bridge", "Own (NAT)")),
                                    T.Field("next", "Next", "action")],
                                   dict(hs_ssid=c["hs_ssid"], hs_password=c["hs_password"], hs_bridge=c["hs_bridge"]))

    def back(self):
        if len(self.steps) > 1:
            self.steps.pop()
            self.enter(self.steps[-1])
        else:
            self.finish(False)

    def finish(self, start):
        self.app.cfg["setup_done"] = True
        S.save(self.app.cfg)
        self.app.pop(self)
        if start:
            fn = net.start_wifi_to_eth if self.job == "share" else net.start_hotspot
            self.app.run_task("Setup", "Starting", lambda: fn(self.app.cfg), ok_title="Running")

    def options(self):
        st = self.app.status
        s = self.step
        if s == "welcome":
            return [("Wi-Fi to Ethernet (rack)", lambda: self.pick("share")),
                    ("Hotspot from Ethernet", lambda: self.pick("hotspot")),
                    ("Skip setup", lambda: self.finish(False))]
        if s == "wifi":
            link = st.get("wifi_link") or {}
            from . import app as A
            opts = [("Join a Wi-Fi network", lambda: A.open_wifi_list(self.app))]
            if link:
                opts.insert(0, (f"Use {link.get('ssid', '')[:20]}", lambda: self.enter("gear")))
            return opts
        if s == "gear":
            return [("Pi hands out addresses (DHCP)", lambda: self.gear(True)),
                    ("My gear has static IPs", lambda: self.gear(False))]
        if s == "done":
            return [("Start now", lambda: self.finish(True)), ("Finish, start later", lambda: self.finish(False))]
        return []

    def pick(self, job):
        self.job = job
        if job == "share":
            self.enter("wifi")
        else:
            self.enter("hotspot")

    def gear(self, dhcp):
        self.app.cfg["share_dhcp"] = dhcp
        S.save(self.app.cfg)
        self.enter("done" if dhcp else "addr")

    def intro(self):
        s, c = self.step, self.app.cfg
        if s == "welcome":
            return "Welcome! What's the job today?"
        if s == "wifi":
            link = self.app.status.get("wifi_link") or {}
            return f"Internet comes from Wi-Fi. Now on: {link.get('ssid') or 'nothing'}"
        if s == "gear":
            return "How does the gear on the switch get its IP?"
        if s == "addr":
            return "Give the Pi an address in your gear's network (it's their gateway):"
        if s == "hotspot":
            return "Hotspot name and password. Bridge = devices join the wired network."
        if s == "done":
            if self.job == "share":
                how = "DHCP on" if c["share_dhcp"] else f"Pi {c['share_ip']}, set gear gateway to it"
                return f"Ready: Wi-Fi to Ethernet, {how}."
            return f"Ready: hotspot {c['hs_ssid']}, " + ("bridged." if c["hs_bridge"] else "own network (NAT).")
        return ""

    def rows(self):
        lines = [(l, L.WARN) for l in wrap(self.app.panel, self.intro())][:2]
        if self.form:
            rows = lines + list(self.form.view(T.ROWS - len(lines)))
            return rows, None
        opts = self.options()
        self.sel = min(self.sel, max(0, len(opts) - 1))
        return lines + [(o[0], L.FG) for o in opts], (len(lines) + self.sel) if opts else None

    def tick(self):
        self.app.dirty = True

    def key(self, k):
        if self.form:
            ev = self.form.handle(k)
            if ev == "action:next":
                v = self.form.values
                if self.step == "addr":
                    err = S.check_host(v["share_ip"], v["share_mask"])
                else:
                    err = S.check_hotspot(v["hs_ssid"], v["hs_password"])
                if err:
                    self.app.push(Message(self.app, "Check", err, L.BAD))
                    return
                self.app.cfg.update(v)
                S.save(self.app.cfg)
                self.enter("done")
            elif ev == "back":
                self.back()
            return
        opts = self.options()
        if k.name == "up" and opts:
            self.sel = (self.sel - 1) % len(opts)
        elif k.name == "down" and opts:
            self.sel = (self.sel + 1) % len(opts)
        elif k.name in SELECT and opts:
            opts[self.sel][1]()
        elif k.name in ("esc", "left"):
            self.back()
