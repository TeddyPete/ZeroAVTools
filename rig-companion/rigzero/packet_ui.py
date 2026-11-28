"""Keyboard-first packet and radio screens for the 320 × 170 display."""

from datetime import datetime, timezone
from pathlib import Path
import secrets

from .capture import export
from .display import ACCENT, DIM, INK, SELECT
from .packet_native import Engine
from .protocol import MODES, POWERS, valid_call


BOXES = (("I", "Inbox"), ("D", "Drafts"), ("O", "Outbox"), ("S", "Sent"))
CONTROL_FIELDS = ("volume", "squelch", "power", "band", "mode", "channel", "dual")


class PacketTools:
    def __init__(self, app):
        self.app = app
        self.store, self.radio = app.store, app.radio
        self.screen = ""
        self.selected = self.offset = 0
        self.filters = ["", "", ""]
        self.capture_cache = None
        self.direction = 0
        self.edit = None
        self.terminal_peer = ""
        self.password = ""
        self.mail = []
        self.message = None
        self.box = 0
        self.form = {}
        self.field = 0
        self.listening_files = False
        self.extended = False

    def open(self, screen):
        self.screen, self.selected, self.offset = screen, 0, 0
        self.edit = None
        if screen == "mail":
            self.refresh_mail()

    def native(self, output=lambda *_: None):
        return Engine(self.store.folder, Path.home() / "Documents" / "Rig-Companion-Transfers", output)

    def refresh_mail(self):
        self.app.flash_loading("Opening the mailbox", "Winlink messages")
        try:
            self._refresh_mail()
        finally:
            self.app.loaded()

    def _refresh_mail(self):
        rows = []
        with self.native(lambda kind, data: rows.append(tuple(data.decode("utf-8", "replace").split("\0", 6))) if kind == 3 else None) as engine:
            engine.require("list_mail", "", error="Could not read the mailbox")
        self.mail = sorted(rows, key=lambda row: row[2], reverse=True)

    def mail_rows(self):
        return [row for row in self.mail if row[0] == BOXES[self.box][0]]

    def read_mail(self, key):
        rows = []
        with self.native(lambda kind, data: rows.append(tuple(data.decode("utf-8", "replace").split("\0", 6))) if kind == 3 else None) as engine:
            engine.require("list_mail", key, error="Could not open the message")
        if not rows:
            raise RuntimeError("Mail could not be loaded")
        return rows[0]

    def stage_mail(self, queued):
        if self.radio.snapshot()["owner"] == "winlink":
            raise RuntimeError("Stop Winlink before editing the mailbox")
        call = self.store.get("callsign")
        if not valid_call(call):
            raise ValueError("Set your callsign under Link")
        if not self.form["to"].strip() or not self.form["subject"].strip():
            raise ValueError("Enter a recipient and subject")
        utc, _ = self.radio.gps_clock.reading()
        utc = utc or datetime.now(timezone.utc)
        with self.native() as engine:
            engine.require("save_mail", self.form["id"], utc.strftime("%Y/%m/%d %H:%M"),
                           call, self.form["to"], self.form["subject"], self.form["body"], queued,
                           error="Could not save mail. Check the recipient and message.")
        self.box = 2 if queued else 1
        self.open("mail")
        self.app.tell("Queued for next Winlink session" if queued else "Draft saved")

    def compose_mail(self, existing=None):
        self.open("compose_mail")
        self.form = ({"id": existing[1], "to": existing[4], "subject": existing[5], "body": existing[6]}
                     if existing else {"id": secrets.token_hex(6).upper(), "to": "", "subject": "", "body": ""})
        self.field = 0

    def capture_rows(self):
        call, kind, direction = self.filters
        key = (self.store.revision, call, kind, direction)
        if self.capture_cache and self.capture_cache[0] == key:
            return self.capture_cache[1]
        rows = [row for row in self.store.rows("traffic", 2500)
                if (not call or call in (row["source"] + " " + row["destination"] + " " + row.get("sender", "")))
                and (not kind or kind.casefold() in row["kind"].casefold())
                and (not direction or row["direction"] == direction)]
        self.capture_cache = (key, rows)
        return rows

    def export_capture(self):
        rows = self.capture_rows()
        _, count, raw = export(rows, Path.home() / "Documents" / "Rig-Companion-Captures")
        self.app.tell(f"Exported {count} CSV / {raw} PCAP")

    def conversations(self):
        local = self.store.get("callsign").removesuffix("-0")
        result = {}
        if not local:
            return result
        for packet in self.store.rows("inbox", 500):
            if packet["recipient"] == local:
                result.setdefault(packet.get("sender", packet["source"]), []).append(
                    ("RX", packet["time"], packet["text"]))
        for remote, body, status, utc in self.store.outbox():
            result.setdefault(remote, []).append(("TX", utc.replace(" ", "T"), body + " [" + status + "]"))
        for rows in result.values():
            rows.sort(key=lambda row: row[1])
        return dict(sorted(result.items(), key=lambda pair: pair[1][-1][1], reverse=True))

    def options(self, state):
        if self.screen == "home":
            return ("Monitor / Capture", "AX.25 Terminal", "Winlink Mail", "Phone / PC Bridge",
                    "APRS Tools", "Packet Setup")
        if self.screen == "aprs":
            return ("Conversations", "Contacts", "WXBOT forecast", "APRS Outbox", "Beacon Setup")
        if self.screen == "bridge":
            return ("RadioMail BLE  " + ("On" if self.app.ble and self.app.ble.enabled else "Off"),
                    "TCP KISS  " + ("On" if self.app.tcp else "Off"))
        if self.screen == "gateways":
            return [f"{call}  {hz or 'Radio frequency'}" for call, hz in self.store.get("gateways", [])]
        if self.screen == "contacts":
            return self.store.get("aprs_contacts", [])
        if self.screen == "conversations":
            return list(self.conversations())
        if self.screen == "controls":
            band = state["bands"][state["active"]]
            def value(options, field):
                index = band[field]
                return options[index] if 0 <= index < len(options) else "—"
            return (f"Volume    {state['volume'] if state['volume'] >= 0 else '—'}",
                    f"Squelch    {band['squelch'] if band['squelch'] >= 0 else '—'}",
                    f"RF power    {value(POWERS, 'power')}",
                    f"Active band    {'AB'[state['active']]}", f"Mode    {value(MODES, 'mode')}",
                    f"Channel    {value(('VFO','Memory','Call'), 'channel')}",
                    "Receive    " + {0: "Dual", 1: "Single"}.get(state["dual"], "—"))
        return ()

    def editor(self, field, initial, maximum, allowed):
        self.edit = [field, initial, maximum, allowed]

    def accept_edit(self, field, value, state):
        if field in ("peer", "contact", "gateway"):
            value = value.strip().upper()
            if not valid_call(value):
                raise ValueError("Enter a valid callsign")
        if field == "peer":
            self.terminal_peer = value
        elif field == "contact":
            contacts = self.store.get("aprs_contacts", [])
            if value not in contacts:
                self.store.set("aprs_contacts", sorted((contacts + [value])[-100:]))
        elif field == "gateway":
            self.pending_gate = value
            self.editor("frequency", "", 11, "0123456789.")
            return
        elif field == "frequency":
            import re
            if value and (not re.fullmatch(r"\d{3}(?:\.\d{1,6})?", value) or not 144 <= float(value) <= 450):
                raise ValueError("Enter a VHF/UHF channel in MHz")
            gates = [pair for pair in self.store.get("gateways", []) if pair[0] != self.pending_gate]
            self.store.set("gateways", sorted((gates + [[self.pending_gate, value]])[-50:]))
        elif field == "password":
            self.password = value
        elif field == "line":
            if value:
                self.radio.submit("packet_send", value)
        elif field == "file":
            if not value or Path(value).name != value or value in (".", ".."):
                raise ValueError("Enter a filename from the Transfers folder")
            folder = Path.home() / "Documents" / "Rig-Companion-Transfers"
            path = folder / value
            if path.is_symlink() or not path.is_file():
                raise ValueError("File not found in Transfers")
            with path.open("rb") as stream:
                data = stream.read(2*1024*1024 + 1)
            if len(data) > 2*1024*1024:
                raise ValueError("YAPP file limit is 2 MiB")
            self.radio.submit("packet_file", value, data)
        elif field == "weather":
            value = value.strip().upper()
            if not value:
                raise ValueError("Enter a location or grid")
            self.app.open_compose("WXBOT")
            self.app.compose["text"] = value
        elif field == "filter call":
            self.filters[0] = value.upper()
            self.selected = 0
        elif field == "filter type":
            self.filters[1] = value
            self.selected = 0
        self.edit = None

    def press(self, name, char, state):
        key = char.lower()
        if self.edit:
            field, value, maximum, allowed = self.edit
            if name == "esc":
                self.edit = None
            elif name == "enter":
                self.accept_edit(field, value, state)
            elif name == "backspace":
                self.edit[1] = value[:-1]
            elif char and len(value) < maximum and (allowed is None or char in allowed):
                self.edit[1] += char
            return
        if self.screen == "compose_mail":
            field = ("to", "subject", "body")[self.field]
            if name == "esc":
                self.open("mail")
            elif name == "tab":
                self.field = (self.field + 1) % 3
            elif name == "enter":
                if self.field < 2:
                    self.field += 1
                else:
                    self.open("save_mail")
            elif name == "backspace":
                self.form[field] = self.form[field][:-1]
            elif char and len(self.form[field]) < (2000 if field == "body" else 88):
                self.form[field] += char
            return
        if self.screen == "save_mail":
            if key in ("5", "6"):
                self.stage_mail(key == "6")
            elif name == "esc" or key == "4":
                self.open("compose_mail")
            return
        if self.screen == "call":
            if name == "enter":
                if self.radio.submit("packet_open", *self.pending_call):
                    self.open("terminal" if self.pending_call[0] == "terminal" else "mail_session")
                    self.listening_files = False
            elif name == "esc" or key == "4":
                self.open("terminal" if self.pending_call[0] == "terminal" else "gateways")
            return
        if name == "esc" or key == "4":
            parents = {"home": "", "controls": "", "conversation": "conversations",
                       "contacts": "aprs", "conversations": "aprs", "mail_detail": "mail",
                       "gateways": "mail", "mail_session": "mail"}
            self.open(parents.get(self.screen, "home"))
            return
        if self.screen == "capture":
            self.capture_press(name, key, state)
        elif self.screen == "terminal":
            if key == "5":
                self.editor("peer", self.terminal_peer, 9, None)
            elif key == "6" and self.terminal_peer:
                self.review_call("terminal", self.terminal_peer, "", "", self.extended, state)
            elif key == "8":
                self.radio.submit("packet_stop")
            elif key == "7" and state["owner"] == "terminal":
                self.editor("line", "", 250, None)
            elif key == "m" and not state["owner"]:
                self.extended = not self.extended
            elif key == "y" and state["owner"] == "terminal":
                self.editor("file", "", 120, None)
            elif key == "l" and state["owner"] == "terminal":
                self.listening_files = not self.listening_files
                self.radio.submit("packet_receive_files", self.listening_files)
            elif key == "s":
                self.save_transcript(state)
            elif name in ("up", "down") or key in ("f", "x"):
                delta = 1 if name == "up" or key == "f" else -1
                self.offset = max(0, min(len(state["packet_log"]) - 1, self.offset + delta))
        elif self.screen == "mail_session":
            if key == "8":
                self.radio.submit("packet_stop")
        elif self.screen == "mail":
            rows = self.mail_rows()
            if key == "5":
                self.box = (self.box + 1) % len(BOXES)
                self.selected = 0
            elif key == "6":
                self.compose_mail()
            elif key == "7":
                self.open("gateways")
            elif key == "8":
                if state["owner"] == "winlink":
                    self.open("mail_session")
                else:
                    self.refresh_mail()
            elif name == "enter" and rows:
                self.message = self.read_mail(rows[min(self.selected, len(rows) - 1)][1])
                self.open("mail_detail")
            else:
                self.move(name, key, len(rows))
        elif self.screen == "mail_detail":
            if key == "8" and self.message[0] in ("D", "O"):
                self.compose_mail(self.message)
            else:
                self.scroll(name, key)
        elif self.screen == "conversation":
            if key == "8":
                self.app.open_compose(self.contact)
            else:
                self.scroll(name, key)
        else:
            self.menu_press(name, key, state)

    def menu_press(self, name, key, state):
        options = self.options(state)
        if self.screen == "controls" and (name in ("left", "right", "enter") or key in ("z", "c")):
            field = CONTROL_FIELDS[self.selected]
            direction = -1 if name == "left" or key == "z" else 1
            self.radio.submit("change", field, direction * (5 if field == "volume" else 1))
        elif self.screen == "gateways" and key == "5":
            self.editor("gateway", "", 9, None)
        elif self.screen == "gateways" and key == "7":
            self.editor("password", self.password, 64, None)
        elif self.screen == "contacts" and key == "5":
            self.editor("contact", "", 9, None)
        elif self.screen == "contacts" and key == "7" and options:
            contacts = list(options)
            contacts.pop(self.selected)
            self.store.set("aprs_contacts", contacts)
            self.selected = max(0, min(self.selected, len(contacts)-1))
        elif name == "enter" and options:
            if self.screen == "home":
                page = ("capture","terminal","mail","bridge","aprs","setup")[self.selected]
                if page == "setup":
                    self.screen = ""
                    self.app.panels.open("packet", state)
                else:
                    self.open(page)
            elif self.screen == "bridge":
                self.app.link_action(("ble","tcp")[self.selected], state)
            elif self.screen == "aprs":
                if self.selected == 2:
                    self.editor("weather", "", 60, None)
                elif self.selected >= 3:
                    self.screen = ""
                    self.app.panels.open(("outbox","beacon")[self.selected - 3], state)
                else:
                    self.open(("conversations","contacts")[self.selected])
            elif self.screen == "gateways":
                call, hz = self.store.get("gateways", [])[self.selected]
                self.review_call("winlink", call, hz, self.password, False, state)
            elif self.screen == "contacts":
                self.app.open_compose(options[self.selected])
            elif self.screen == "conversations":
                self.contact = options[self.selected]
                self.open("conversation")
        else:
            self.move(name, key, len(options))

    def capture_press(self, name, key, state):
        if key == "5":
            self.editor("filter call", self.filters[0], 9, None)
        elif key == "6":
            self.editor("filter type", self.filters[1], 20, None)
        elif key == "7":
            self.direction = (self.direction + 1) % 3
            self.filters[2] = ("", "RX", "TX")[self.direction]
            self.selected = 0
        elif key == "8":
            self.app.background(self.export_capture)
        elif key == "k":
            if state["owner"] in ("terminal", "winlink", "ble", "tcp"):
                self.app.tell("Current packet session stays active")
            else:
                self.app.kiss(state)
        else:
            rows = self.capture_rows()
            if name == "enter" and rows:
                self.app.detail = rows[min(self.selected, len(rows)-1)]
                self.app.scroll = 0
                self.app.raw_detail = False
            else:
                self.move(name, key, len(rows))

    def review_call(self, owner, peer, mhz, password, extended, state):
        if not state["connected"] or not state.get("model"):
            raise RuntimeError("Connect a recognized Kenwood radio first")
        band = self.store.get("packet_band", None)
        band = state["active"] if band is None else band
        hz = state["bands"][band]["hz"]
        if not mhz and not hz:
            raise RuntimeError("Wait for the packet band frequency readback")
        self.pending_call = (owner, peer, mhz, password, extended, hz)
        self.open("call")

    def save_transcript(self, state):
        folder = Path.home() / "Documents" / "Rig-Companion-Transfers"
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        file = folder / ("terminal-" + secrets.token_hex(4) + ".txt")
        file.write_text("\n".join(state["packet_log"]) + "\n", encoding="utf-8")
        self.app.tell("Transcript saved under Transfers")

    def move(self, name, key, count):
        if name in ("up","down") or key in ("f","x"):
            self.selected = max(0, min(count - 1, self.selected + (-1 if name == "up" or key == "f" else 1)))

    def scroll(self, name, key):
        if name in ("up","down") or key in ("f","x"):
            self.offset = max(0, min(getattr(self, "max_offset", 0), self.offset + (-1 if name == "up" or key == "f" else 1)))

    def draw(self, display, state):
        d = display
        if self.edit:
            field, value, _, _ = self.edit
            title = {"weather": "WXBOT forecast", "line": "Send terminal line", "file": "YAPP file name"}.get(field, field.title())
            d.header(title)
            d.wrap(("•" * len(value) if field == "password" else value) + "_", y=51, height=68)
            hint = {"file": "Use Documents/Rig-Companion-Transfers",
                    "weather": "Enter a grid, city or WXBOT request",
                    "frequency": "Blank uses the radio's tuned channel",
                    "password": "Winlink password stays in memory"}.get(field, "Enter confirm   Esc cancel")
            d.text(hint, 9, 130, DIM, 14, 302)
            return
        mode = self.screen
        if mode == "capture":
            rows = self.capture_rows()
            d.header("Packet Monitor", f"{len(rows)}")
            d.text(f"{self.filters[0] or 'All calls'}  ·  {self.filters[1] or 'All types'}  ·  {self.filters[2] or 'RX/TX'}", 10, 29, DIM, 14, 296)
            self.rows(d, [f"{p['direction']}  {p['source']}  {p['kind']}" for p in rows], y=48, visible=4)
            d.text("Enter view   K KISS monitor", 10, 130, DIM, 14, 298)
            d.footer(labels=["Back","Call","Type","Dir","Export"])
        elif mode in ("terminal", "mail_session"):
            d.header("AX.25 Terminal" if mode == "terminal" else "Winlink Session", state["packet_link"])
            if mode == "terminal":
                d.text(f"Peer {self.terminal_peer or '—'}   {('Mod 128' if self.extended else 'Mod 8')}", 10, 30, ACCENT, 14, 298)
            else:
                d.text(f"Gateway {state['packet_peer']}", 10, 30, ACCENT, 14, 298)
            log = state["packet_log"]
            last = max(0,len(log)-self.offset)
            d.wrap("\n".join(log[max(0,last-4):last]) if log else "Ready. Enter a peer or gateway to start.", y=49,height=66)
            if mode == "terminal":
                d.text("Y Send file  L " + ("Rx armed" if self.listening_files else "Rx file") + "  S Save log",
                       10, 129, DIM, 14, 298)
                d.footer(labels=["Back","Peer","Call","Write","Stop"])
            else:
                d.footer(labels=["Back","","","","Stop"])
        elif mode == "mail":
            d.header("Winlink Mail", BOXES[self.box][1])
            rows = self.mail_rows()
            self.rows(d, [f"{(r[3] if r[0]=='I' else r[4])}  ·  {r[5]}" for r in rows])
            d.footer(labels=["Back","Folder","Write","Gate","Session" if state["owner"] == "winlink" else "Reload"])
        elif mode == "mail_detail":
            box, _, _, sender, to, subject, body = self.message
            d.header("Winlink Message")
            d.text(("From " + sender) if box == "I" else "To " + to, 10, 29, DIM, 14, 297)
            d.text(subject, 10, 47, ACCENT, 14, 297)
            self.max_offset = max(0, d.wrap(body, y=66, height=75, offset=self.offset)-4)
            d.footer(labels=["Back","","","","Edit" if box in ("D","O") else ""])
        elif mode == "compose_mail":
            d.header("Write Winlink")
            for i, field in enumerate(("to","subject","body")):
                value = self.form[field] + ("_" if self.field == i else "")
                if i < 2:
                    d.text(field.title() + "  " + value[-35:], 10, 29+i*21, ACCENT if i == self.field else INK, 14, 298)
                else:
                    d.wrap(value[-95:], y=74, height=48, color=ACCENT if i == self.field else INK)
            d.text("Tab field   Enter next / save", 10, 130, DIM, 14, 297)
        elif mode == "save_mail":
            d.header("Keep this message?")
            d.wrap("Draft keeps it editable. Queue adds it to the next Winlink session.", y=39, height=82)
            d.footer(labels=["Back","Draft","Queue","",""])
        elif mode == "call":
            owner, peer, mhz, _, _, hz = self.pending_call
            d.header("Connect to " + peer)
            d.wrap(f"{'Winlink' if owner == 'winlink' else 'AX.25'} via the radio TNC. " +
                   (f"Request {mhz} MHz." if mhz else f"Use the tuned {hz / 1e6:.6f} MHz channel.") +
                   "\nEnter starts the RF session. Esc cancels.", y=39, height=100)
        elif mode == "conversation":
            d.header(self.contact)
            lines = [direction + " " + msg for direction, _, msg in self.conversations().get(self.contact, [])]
            self.max_offset = max(0,d.wrap("\n".join(lines[-40:]), y=31, height=114, offset=self.offset)-6)
            d.footer(labels=["Back","","","","Reply"])
        else:
            title = {"home":"Packet Tools","bridge":"KISS Bridge","aprs":"APRS Tools","gateways":"Winlink Gateways",
                     "contacts":"APRS Contacts","conversations":"APRS Conversations","controls":"Radio Controls"}[mode]
            status = ("KISS " + state["owner"] if state["kiss"] else "CAT" if state["connected"] else "Offline") if mode in ("home","controls") else ""
            d.header(title, status)
            self.rows(d, self.options(state))
            labels = {"gateways":["Back","Add","","Pass",""],"contacts":["Back","Add","","Del",""]}.get(mode,["Back","","","",""])
            if mode == "controls":
                d.text("←/→ change · Enter next value", 10, 130, DIM, 14, 298)
            d.footer(labels=labels)

    def rows(self, d, rows, y=30, visible=5):
        self.selected = max(0, min(self.selected, len(rows)-1))
        start = max(0, self.selected-visible+1)
        if not rows:
            d.text("No entries yet", 10, y+4, DIM, 14)
        for index, row in enumerate(rows[start:start+visible], start):
            if index == self.selected:
                d.rect(7, y, 306, 19, SELECT, 3)
            d.text(row, 12, y+1, INK, 14, 292)
            y += 20
