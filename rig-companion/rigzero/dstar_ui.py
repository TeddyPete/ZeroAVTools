"""D-STAR screens: live dashboard, reflector picker, last heard and setup.

Keys follow the CardputerZero spec used across Rig Companion: F/X or arrows
move, Enter or Right opens, Left or short Esc goes back, keys 4-8 are the
footer actions, hold Esc exits the app.
"""
from __future__ import annotations

import string
import time
from datetime import datetime, timezone

from .display import ACCENT, ALERT, DIM, INK, LINE, PANEL, SELECT, SELECT_DIM

CATEGORIES = ("Favorites", "REF", "XRF", "XLX", "DCS")
DEFAULT_FAVORITES = ["REF001C", "REF030C", "XRF757A"]
GUIDE = ("TH-D75 setup\n"
         "1. Band A in DV mode (8 Start sets this over Bluetooth when the radio is connected).\n"
         "2. Menu 650 DV Gateway Mode: Reflector TERM Mode.\n"
         "3. Menu 651: your callsign.\n"
         "4. Menu 985 DV Gateway: Bluetooth (or USB).\n"
         "5. Here: 8 Start, then 5 Link a reflector.\n"
         "\nTalk with UR = CQCQCQ. From the radio, UR commands also work:\n"
         "REF001CL links REF001 C, 7 spaces + U unlinks, + E echo test, + I link info.\n"
         "\nCallsign routing (QuadNet, like a Pi-Star hotspot): put a callsign in UR, "
         "or press D here and type one. Calls to you arrive when UDP port 40000 is "
         "forwarded to the Cardputer on your router; calls out work without it.\n"
         "\nREF reflectors need gateway registration (regist.dstargateway.org) to transmit. "
         "XRF, XLX and DCS do not.\n"
         "\nThe TH-D75 transmits nothing on RF in this mode. It has no Access Point mode.")


RADIO_STEPS = ("Menu 650: Reflector TERM Mode",
               "Menu 651: your callsign",
               "Menu 985: Bluetooth",
               "Press 8 Start here. Band A goes to DV for you.")
RADIO_NOTE = "No Bluetooth command sets these menus. Set them once; the radio keeps them."


def ago(iso: str) -> str:
    try:
        then = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    seconds = max(0, int((datetime.now(timezone.utc) - then).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


class DStarTools:
    def __init__(self, app):
        self.app = app
        self.store = app.store
        self.gateway = None
        self.screen = ""
        self.selected = self.offset = 0
        self.category = 0
        self.query = ""
        self.edit = None
        self.pick = None            # [name, module letter]
        self.entry = None
        self.seen_notice = ""
        self.hosts_loading = False
        self.call_edit = None       # text being typed on the call screen
        self.call_target = ""       # callsign last looked up there

    # ------------------------------------------------------------------ helpers
    def gw(self):
        if self.gateway is None:
            from .dstar_gateway import DStarGateway
            self.gateway = DStarGateway(self.store, self.app.radio)
        return self.gateway

    def open(self, screen="home"):
        self.gw()
        first = not self.screen
        self.screen, self.selected, self.offset = screen, 0, 0
        self.edit = None
        if screen == "home" and first:
            self.radio_steps()
        if screen == "reflectors" and self.gateway.hosts.stale() and not self.hosts_loading:
            self.update_hosts()

    def radio_steps(self, force=False):
        self.app.show_steps("dstar", "Set up the TH-D75 first", RADIO_STEPS, force=force, note=RADIO_NOTE)

    def update_hosts(self, force=False):
        self.hosts_loading = True
        self.app.loading("Downloading reflector lists", "REF, XRF, XLX and DCS", done=lambda: not self.hosts_loading,
                         timeout=60)

        def work():
            try:
                error = self.gateway.refresh_hosts(force)
                self.app.tell(error or f"{len(self.gateway.hosts.table)} reflectors loaded")
            finally:
                self.hosts_loading = False
        self.app.background(work)

    def favorites(self):
        return self.store.get("dstar_favorites", DEFAULT_FAVORITES)

    def reflector_rows(self):
        if CATEGORIES[self.category] == "Favorites":
            recent = [r for r in self.store.get("dstar_recent", []) if r not in self.favorites()]
            rows = [r[:6] + " " + r[6:] for r in self.favorites() + recent]
            return [r for r in rows if self.query in r.replace(" ", "")]
        return self.gw().hosts.names(CATEGORIES[self.category], self.query)

    def setup_rows(self):
        port = self.store.get("dstar_port", "bt")
        return [("Radio link   " + ("USB cable" if port == "usb" else "Bluetooth"), "port"),
                ("Bluetooth channel   " + str(self.store.get("dstar_channel", 2)), "channel"),
                ("Relink last reflector   " + ("On" if self.store.get("dstar_relink", True) else "Off"), "relink"),
                ("Name lookup (callook.info)   " + ("On" if self.store.get("dstar_lookup", True) else "Off"), "lookup"),
                ("Callsign   " + (self.store.get("callsign") or "set under Link"), "callsign"),
                ("Callsign routing (QuadNet)   " + ("On" if self.store.get("dstar_routing", True) else "Off"), "routing"),
                ("My module   " + self.store.get("dstar_module", "B"), "module"),
                ("Update reflector lists", "hosts"),
                ("Clear last heard", "clear"),
                ("TH-D75 setup guide", "guide")]

    def move(self, name, key, count):
        if name in ("up", "down") or key in ("f", "x"):
            delta = -1 if name == "up" or key == "f" else 1
            self.selected = max(0, min(max(0, count - 1), self.selected + delta))
            return True
        return False

    # ------------------------------------------------------------------ keys
    def press(self, name, char, state):
        key = char.lower()
        gw = self.gw()
        if self.edit is not None:
            if name == "esc":
                self.edit = None
            elif name == "enter":
                self.query, self.edit, self.selected = self.edit.upper(), None, 0
            elif name == "backspace":
                self.edit = self.edit[:-1]
            elif char and char.upper() in string.ascii_uppercase + string.digits and len(self.edit) < 7:
                self.edit += char.upper()
            return
        if self.screen == "pick":
            self.press_pick(name, key)
            return
        if self.screen == "call" and self.call_edit is not None:
            self.press_call_edit(name, char)
            return
        back = name == "esc" or key == "4" or (name == "left" and self.screen != "pick")
        if back:
            parents = {"home": "", "reflectors": "home", "heard": "home", "setup": "home", "call": "home",
                       "entry": "heard", "guide": "setup", "help": "home"}
            target = parents.get(self.screen, "home")
            if self.screen == "entry" and self.entry is not None and self.entry.get("_from") == "home":
                target = "home"
            if target:
                self.open(target)
            else:
                self.screen = ""
            return
        if char in ("?",) or (key == "h" and self.screen != "reflectors"):
            self.screen = "help"
            return
        if self.screen == "home":
            snap = gw.snapshot()
            if key == "5":
                self.open("reflectors")
            elif key == "6":
                self.open("heard")
            elif key == "7":
                if snap["link"] in ("Linked", "Linking"):
                    gw.submit("unlink")
                elif snap["route_to"]:
                    self.route(None)
                else:
                    last = (self.store.get("dstar_recent", []) or [""])[0]
                    if len(last) == 7:
                        self.start_if_needed(snap)
                        self.link(last[:6], last[6])
                    else:
                        self.open("reflectors")
            elif key == "8":
                if snap["running"]:
                    gw.submit("stop")
                else:
                    self.start(state)
            elif key == "s":
                self.open("setup")
            elif key == "d":
                self.open_call()
            elif key == "m":
                talker = snap["talker"] or (snap["heard"][0] if snap["heard"] else None)
                if talker:
                    self.show_on_map(talker)
            elif name in ("enter", "right"):
                talker = snap["talker"] or (snap["heard"][0] if snap["heard"] else None)
                if talker:
                    self.entry = dict(talker, _from="home")
                    self.screen = "entry"
                    self.offset = 0
        elif self.screen == "reflectors":
            rows = self.reflector_rows()
            if key == "5":
                self.category = (self.category + 1) % len(CATEGORIES)
                self.selected, self.query = 0, ""
            elif key == "6" or char == "/":
                self.edit = self.query
            elif key == "8":
                self.update_hosts(force=True)
            elif key == "7" and rows:
                self.toggle_favorite(rows[min(self.selected, len(rows) - 1)])
            elif name in ("enter", "right") and rows:
                row = rows[min(self.selected, len(rows) - 1)]
                name6, _, module = row.partition(" ")
                default = module or {"REF": "C", "DCS": "A", "XRF": "A", "XLX": "A"}.get(name6[:3], "A")
                self.pick = [name6, default]
                self.screen = "pick"
            else:
                self.move(name, key, len(rows))
        elif self.screen == "heard":
            heard = gw.snapshot()["heard"]
            if name in ("enter", "right") and heard:
                self.entry = dict(heard[min(self.selected, len(heard) - 1)], _from="heard")
                self.screen, self.offset = "entry", 0
            elif key == "5" and heard:
                self.show_on_map(heard[min(self.selected, len(heard) - 1)])
            else:
                self.move(name, key, len(heard))
        elif self.screen == "entry":
            if key == "5" and self.entry:
                self.show_on_map(self.entry)
            elif name in ("up", "down") or key in ("f", "x"):
                self.offset = max(0, self.offset + (-1 if name == "up" or key == "f" else 1))
        elif self.screen == "call":
            if name in ("enter", "right"):
                self.call_edit = self.call_target
            elif key == "8" and self.call_target:
                self.route(self.call_target)
                self.open("home")
            elif key == "7":
                self.route(None)
            elif key == "5" and self.call_target:
                self.lookup(self.call_target)
        elif self.screen == "setup":
            rows = self.setup_rows()
            if name in ("enter", "right") and rows:
                self.setup_action(rows[min(self.selected, len(rows) - 1)][1], state)
            else:
                self.move(name, key, len(rows))
        elif self.screen in ("guide", "help"):
            if name in ("up", "down") or key in ("f", "x"):
                self.offset = max(0, min(getattr(self, "max_offset", 0),
                                         self.offset + (-1 if name == "up" or key == "f" else 1)))
            elif name == "enter":
                self.open("setup" if self.screen == "guide" else "home")

    def press_pick(self, name, key):
        letter = self.pick[1]
        if name == "esc" or key == "4":
            self.screen = "reflectors"
        elif name in ("left", "up") or key in ("5", "z", "f", "-"):
            self.pick[1] = chr((ord(letter) - ord("A") - 1) % 26 + ord("A"))
        elif name in ("right", "down") or key in ("6", "c", "x", "+", "="):
            self.pick[1] = chr((ord(letter) - ord("A") + 1) % 26 + ord("A"))
        elif len(key) == 1 and key.isalpha() and key not in "fzxc":
            self.pick[1] = key.upper()
        elif key == "7":
            self.toggle_favorite(self.pick[0] + " " + self.pick[1])
        elif key == "8" or name == "enter":
            snap = self.gw().snapshot()
            self.start_if_needed(snap)
            self.link(self.pick[0], self.pick[1])
            self.open("home")

    # ------------------------------------------------------------------ callsign routing
    def open_call(self):
        self.open("call")
        self.call_edit = ""
        if self.gw().ensure_ircddb() is None:
            self.app.tell("Callsign routing is off (S setup)" if self.store.get("callsign") else "Set your callsign under Link first")

    def press_call_edit(self, name, char):
        if name == "esc":
            self.call_edit = None
        elif name == "enter":
            call, self.call_edit = self.call_edit.strip().upper(), None
            if call:
                self.call_target = call
                self.lookup(call)
        elif name == "backspace":
            self.call_edit = self.call_edit[:-1]
        elif char and (char.isalnum() or char == "/") and len(self.call_edit) < 8:
            self.call_edit += char.upper()

    def lookup(self, call):
        irc = self.gw().ensure_ircddb()
        if irc is None:
            self.app.tell("Turn on callsign routing in setup")
            return
        irc.find(call)
        self.app.loading(f"Looking up {call}", "on QuadNet",
                         done=lambda: irc.ready and irc.result(call)[0] != "wait", timeout=20)

    def route(self, call):
        try:
            self.gw().route_to(call or "")
        except Exception as error:
            self.app.tell(str(error))
            return
        if call:
            self.app.tell(f"CQCQCQ now goes to {call} (direct)")

    def toggle_favorite(self, row):
        name, _, module = row.partition(" ")
        if not module:
            self.pick = [name, "C" if name.startswith("REF") else "A"]
            self.screen = "pick"
            self.app.tell("Choose the module, then 7 Fav")
            return
        favorites = list(self.favorites())
        key = name + module
        if key in favorites:
            favorites.remove(key)
            self.app.tell(f"Removed {name} {module} from favorites")
        else:
            favorites = (favorites + [key])[-20:]
            self.app.tell(f"Added {name} {module} to favorites")
        self.store.set("dstar_favorites", favorites)

    def link(self, name, module):
        gw = self.gw()
        gw.update(link="Linking", error="")
        gw.submit("link", name, module)
        if self.app.busy:                       # still connecting to the radio: say both
            self.app.busy["detail"] = f"then linking {name} {module}"
            return

        def linked():
            snap = gw.snapshot()
            return snap["link"] != "Linking" or bool(snap["error"])
        self.app.loading(f"Linking {name} {module}", "Reflector", done=linked, timeout=15)

    def start_if_needed(self, snap):
        if not snap["running"]:
            self.start(self.app.radio.snapshot())

    def start(self, state):
        if state.get("kiss") or state.get("owner"):
            self.app.tell("End KISS / packet sessions first")
            return
        if not self.store.get("callsign"):
            self.app.tell("Set your callsign under Link first")
            return
        gw = self.gw()
        gw.update(radio="Opening radio link", error="")
        gw.submit("start")

        def ready():
            snap = gw.snapshot()
            return snap["radio"] == "Radio ready" or bool(snap["error"]) or snap["radio"].startswith("Radio not")
        self.app.loading("Connecting to the TH-D75", "Bluetooth gateway link", done=ready, timeout=35)

    def setup_action(self, action, state):
        gw = self.gw()
        if gw.snapshot()["running"] and action in ("port", "channel"):
            self.app.tell("Stop the gateway (8) to change the radio link")
            return
        if action == "port":
            self.store.set("dstar_port", "usb" if self.store.get("dstar_port", "bt") == "bt" else "bt")
        elif action == "channel":
            self.store.set("dstar_channel", self.store.get("dstar_channel", 2) % 4 + 1)
        elif action == "relink":
            self.store.set("dstar_relink", not self.store.get("dstar_relink", True))
        elif action == "lookup":
            self.store.set("dstar_lookup", not self.store.get("dstar_lookup", True))
        elif action == "routing":
            on = not self.store.get("dstar_routing", True)
            self.store.set("dstar_routing", on)
            if not on:
                gw.stop_ircddb()
            self.app.tell("Callsign routing " + ("on (applies at next Start)" if on else "off"))
        elif action == "module":
            letter = self.store.get("dstar_module", "B")
            self.store.set("dstar_module", "ABCD"[("ABCD".index(letter) + 1) % 4] if letter in "ABCD" else "B")
        elif action == "callsign":
            self.screen = ""
            self.app.page = 4
            self.app.editor, self.app.edit_value = "callsign", self.store.get("callsign")
        elif action == "hosts":
            self.update_hosts(force=True)
        elif action == "clear":
            with gw.lock:
                gw.state["heard"] = []
            self.store.set("dstar_heard", [])
            self.app.tell("Last heard cleared")
        elif action == "guide":
            self.screen, self.offset = "guide", 0

    def show_on_map(self, entry):
        call = (entry.get("call") or "").upper()
        stations = self.store.stations()
        match = next((s for s in stations if s.get("sender", s.get("source", "")).split("-")[0] == call), None)
        if match is None:
            self.app.tell(f"No D-PRS position from {call} yet")
            return
        self.app.open_map(match)

    # ------------------------------------------------------------------ drawing
    def draw(self, d, state):
        gw = self.gw()
        snap = gw.snapshot()
        if snap["radio"] == "Radio ready":
            if not self.store.get("steps_ok_dstar", False):
                self.app.steps_done("dstar")
        elif snap["radio"].startswith("Radio not") or "gateway mode" in snap["error"] or "not answering" in snap["error"]:
            if not getattr(self, "steps_shown_for", "") == snap["error"] + snap["radio"]:
                self.steps_shown_for = snap["error"] + snap["radio"]
                self.app.loaded()
                self.radio_steps(force=True)
        notice = snap["error"] or snap["notice"]
        if notice and notice != self.seen_notice:
            self.seen_notice = notice
            self.app.tell(notice)
        if self.edit is not None:
            d.header("Find reflector", CATEGORIES[self.category])
            d.rect(8, 48, 304, 35, PANEL, 4)
            d.text(self.edit + "_", 15, 53, ACCENT, 23, 290)
            d.text("Letters / digits, e.g. 030 or REF03", 10, 97, DIM, 14, 300)
            d.text("Enter search · Esc cancel", 10, 130, DIM, 14, 300)
            return
        getattr(self, "draw_" + self.screen)(d, snap)

    def status(self, snap):
        if not snap["running"]:
            return "Off"
        if snap["link"] == "Linked":
            return snap["reflector"] + " " + snap["module"]
        return snap["link"]

    def draw_home(self, d, snap):
        d.header("D-STAR", self.status(snap))
        talker = snap["talker"]
        if snap["running"]:
            link = snap["link"]
            if link == "Linked":
                line = f"{snap['reflector']} {snap['module']}  ·  {snap['protocol']}"
                if snap["read_only"]:
                    line += "  ·  listen only"
            elif link == "Linking":
                line = f"Linking {snap['reflector']} {snap['module']}…"
            elif snap["route_to"]:
                line = f"Direct call to {snap['route_to']}  ·  7 clear"
            else:
                line = "Not linked · 5 Link · D Direct call"
            d.text(line, 10, 29, ACCENT if link == "Linked" or snap["route_to"] else INK, 14, 300)
            routing = self.gw().routing_status() if self.store.get("dstar_routing", True) else ""
            status = snap["radio"] + ("  ·  " + routing.replace(" online", "") if routing else "")
            d.text(status, 10, 47, DIM, 12, 300)
        else:
            d.text("Reflector terminal is off", 10, 29, INK, 14, 300)
            d.text("8 Start  ·  S Setup  ·  ? Help", 10, 47, DIM, 12, 300)
        if talker:
            rf = talker["source"] == "RF"
            d.rect(8, 64, 304, 62, "#3a1f1f" if rf else "#173a35", 5)
            d.text(talker["call"] or "—", 15, 66, INK, 23, 170)
            seconds = int(time.time() - datetime.fromisoformat(talker["time"]).timestamp())
            d.text(("TX " if rf else "RX ") + f"{seconds}s", 252, 70, ALERT if rf else ACCENT, 14)
            extra = " · ".join(x for x in (talker.get("name"), talker.get("place")) if x)
            d.text(extra or ("/" + talker["suffix"] if talker.get("suffix") else ""), 15, 94, SELECT_DIM, 12, 292)
            d.text(talker.get("message") or ("UR " + talker.get("ur", "")), 15, 109, INK, 12, 292)
        else:
            heard = snap["heard"][:2]
            if not heard:
                d.text("Nobody heard yet", 10, 72, DIM, 14)
            for i, entry in enumerate(heard):
                y = 64 + i * 32
                d.rect(8, y, 304, 31, PANEL, 4)
                d.text(entry["call"], 14, y + 1, ACCENT, 14, 110)
                d.text(f"{ago(entry['time'])} ago  {entry.get('seconds', 0):.0f}s", 214, y + 2, DIM, 12, 94)
                d.text(entry.get("message") or entry.get("name") or entry.get("reflector", ""), 14, y + 14, INK, 12, 292)
        hint = "Enter details · D direct · M map · S setup"
        d.text(hint, 10, 130, DIM, 12, 302)
        linked = snap["link"] in ("Linked", "Linking")
        seven = "Unlink" if linked else ("Clear" if snap["route_to"] else "Relink")
        d.footer(labels=["Back", "Link", "Heard", seven, "Stop" if snap["running"] else "Start"])

    def draw_reflectors(self, d, snap):
        rows = self.reflector_rows()
        title = CATEGORIES[self.category]
        loading = "Loading…" if self.hosts_loading else f"{len(rows)}"
        d.header("Reflectors · " + title, loading)
        if self.query:
            d.text("Find: " + self.query, 10, 28, DIM, 12, 300)
        favorites = self.favorites()
        self.selected = max(0, min(self.selected, len(rows) - 1))
        start = max(0, self.selected - 4)
        y = 41 if self.query else 30
        visible = 4 if self.query else 5
        if not rows:
            d.text("No reflectors" + (" yet · 8 Update" if not self.gw().hosts.table else ""), 10, y + 4, DIM, 14)
        for index, row in enumerate(rows[start:start + visible], start):
            chosen = index == self.selected
            if chosen:
                d.rect(7, y, 306, 19, SELECT, 3)
            star = "★ " if row.replace(" ", "") in favorites or any(f.startswith(row) for f in favorites) else ""
            d.text(star + row, 12, y + 1, INK, 14, 200)
            address = self.gw().hosts.lookup(row.split(" ")[0])
            if address:
                d.text(address[0], 200, y + 2, SELECT_DIM if chosen else DIM, 12, 110)
            y += 20
        d.footer(labels=["Back", "Type", "Find", "Fav", "Update"])

    def draw_pick(self, d, snap):
        name, letter = self.pick
        d.header("Link " + name, "module")
        d.rect(108, 36, 104, 60, PANEL, 6)
        d.text(letter, 160 - d.fonts[23].size(letter)[0] // 2, 50, ACCENT, 23)
        d.text("←/→ or a letter picks the module", 10, 104, DIM, 14, 300)
        fav = (name + letter) in self.favorites()
        d.text("Enter links · 7 " + ("un-favorite" if fav else "favorite"), 10, 124, DIM, 12, 300)
        d.footer(labels=["Back", "−", "+", "Fav", "Link"])

    def draw_heard(self, d, snap):
        heard = snap["heard"]
        d.header("Last heard", str(len(heard)))
        self.selected = max(0, min(self.selected, len(heard) - 1))
        if not heard:
            d.text("Nobody heard yet", 10, 40, DIM, 14)
        start = max(0, self.selected - 2)
        for i, entry in enumerate(heard[start:start + 3]):
            y = 30 + i * 32
            chosen = start + i == self.selected
            if chosen:
                d.rect(7, y, 306, 31, SELECT, 3)
            d.text(entry["call"] + ("  /" + entry["suffix"] if entry.get("suffix") else ""), 12, y, INK if chosen else ACCENT, 14, 150)
            d.text(("TX" if entry["source"] == "RF" else "RX") + f" {entry.get('seconds', 0):.0f}s · {ago(entry['time'])}",
                   190, y + 2, SELECT_DIM if chosen else DIM, 12, 118)
            text = entry.get("message") or " · ".join(x for x in (entry.get("name"), entry.get("place")) if x) or entry.get("reflector", "")
            d.text(text, 12, y + 16, INK, 12, 294)
        d.text("→ details · 5 map", 10, 130, DIM, 12, 302)
        d.footer(labels=["Back", "Map", "", "", ""])

    def draw_entry(self, d, snap):
        e = self.entry or {}
        d.header(e.get("call", ""), "TX" if e.get("source") == "RF" else "RX")
        lines = []
        if e.get("name") or e.get("place"):
            lines.append(" · ".join(x for x in (e.get("name"), e.get("place"), e.get("grid")) if x))
        if e.get("message"):
            lines.append("Message: " + e["message"])
        lines.append(f"{e.get('time', '').replace('T', ' ').replace('+00:00', ' UTC')} · {e.get('seconds', 0):.1f} s")
        lines.append("Via " + (e.get("reflector") or "gateway") + ("  /" + e["suffix"] if e.get("suffix") else ""))
        lines.append(f"UR {e.get('ur', '')}")
        lines.append(f"RPT1 {e.get('rpt1', '')}   RPT2 {e.get('rpt2', '')}")
        if e.get("lat") is not None:
            lines.append(f"D-PRS {e['lat']:.4f}, {e['lon']:.4f}")
        d.wrap("\n".join(lines), y=31, height=110, offset=self.offset)
        d.footer(labels=["Back", "Map" if e.get("lat") is not None else "", "", "", ""])

    def draw_call(self, d, snap):
        gw = self.gw()
        d.header("Direct call", "QuadNet")
        d.rect(8, 28, 304, 33, PANEL, 4)
        if self.call_edit is not None:
            d.text(self.call_edit + "_", 15, 32, ALERT, 23, 290)
        else:
            d.text(self.call_target or "Enter to type", 15, 32, ACCENT if self.call_target else DIM, 23, 290)
        irc = gw.ircddb
        lines = []
        if self.call_edit is not None:
            lines = ["Type a callsign, Enter looks it up", "Esc cancels"]
        elif self.call_target and irc:
            answer, route = irc.result(self.call_target)
            if answer == "wait":
                lines = ["Looking up on QuadNet…"]
            elif route:
                when = route["stamp"][11:16] + " UTC" if route["stamp"] else ""
                lines = [f"Last on {route['repeater'].strip()}  {when}",
                         f"Gateway {route['gateway'].strip()}" + ("  · online" if route["ip"] else "  · offline")]
            else:
                lines = ["Not heard on QuadNet"]
        elif not irc:
            lines = ["Callsign routing is off (S setup)"]
        y = 68
        for line in lines:
            d.text(line, 10, y, INK, 14, 300)
            y += 19
        d.text(gw.routing_status(), 10, 112, DIM, 12, 300)
        if snap["route_to"]:
            d.text("CQCQCQ goes to " + snap["route_to"], 10, 127, ACCENT, 12, 300)
        d.footer(labels=["Back", "Find" if self.call_target else "", "", "Clear" if snap["route_to"] else "",
                         "Talk" if self.call_target else ""])

    def draw_setup(self, d, snap):
        d.header("D-STAR setup", "Running" if snap["running"] else "Off")
        rows = [label for label, _ in self.setup_rows()]
        self.selected = max(0, min(self.selected, len(rows) - 1))
        start = max(0, self.selected - 4)
        y = 30
        for index, row in enumerate(rows[start:start + 5], start):
            if index == self.selected:
                d.rect(7, y, 306, 19, SELECT, 3)
            d.text(row, 12, y + 1, INK, 14, 292)
            y += 20
        d.footer(labels=["Back", "", "", "", ""])

    def draw_guide(self, d, snap):
        d.header("TH-D75 Reflector Terminal")
        self.max_offset = max(0, d.wrap(GUIDE, y=30, height=112, offset=self.offset) - 6)
        d.footer(labels=["Back", "", "", "", ""])

    def draw_help(self, d, snap):
        d.header("D-STAR keys")
        text = ("5 Link reflector · 6 Last heard\n7 Unlink / relink last · 8 Start / stop\n"
                "Enter talker details · M map · S setup\nReflectors: 5 type · 6 find · 7 fav · 8 update\n"
                "D direct call: type a callsign, Enter finds it, 8 Talk sends CQCQCQ there\n"
                "Or put a callsign in the radio's UR field\n"
                "Esc back · hold Esc exit")
        self.max_offset = max(0, d.wrap(text, y=30, height=112, offset=self.offset) - 6)
        d.footer(labels=["Back", "", "", "", ""])

    def close(self):
        if self.gateway:
            self.gateway.close()
