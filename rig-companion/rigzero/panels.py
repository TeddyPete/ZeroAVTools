"""Field Manual and APRS setup screens for the Cardputer keyboard."""

from pathlib import Path

from .capture import export
from .display import ACCENT, DIM, INK, SELECT
from .manual import entries
from .models import MODEL_NAMES
from .protocol import position_frame


class Panels:
    def __init__(self, app):
        self.app = app
        self.store = app.store
        self.screen = ""
        self.selected = 0
        self.section = 0
        self.model = MODEL_NAMES[0]
        self.query = ""
        self.detail = None
        self.typing = False
        self.scroll = 0
        self.confirmation = None

    def open(self, screen, state, section=0, query=""):
        self.screen, self.selected = screen, 0
        if screen == "monitor":
            self.app.show_steps("kiss", "Handset KISS on the TH-D75", (
                "Menu 983 KISS: Bluetooth",
                "Turn on KISS on the radio (TNC key until KISS shows)",
                "Back here, press Enter to start monitoring",
                "When done, turn KISS off on the radio"),
                note="The radio has no Bluetooth command for these steps.")
        if screen == "guide":
            self.model = state.get("model") or MODEL_NAMES[0]
            self.section, self.query = section, query
            self.detail, self.typing, self.scroll = None, False, 0

    def options(self, state):
        if self.screen == "beacon":
            return (
                ("Fixed position: " + (self.store.get("beacon_position") or "Set coordinates"), "position"),
                ("Send once", 0), ("Repeat every 5 minutes", 300),
                ("Repeat every 10 minutes", 600), ("Repeat every 20 minutes", 1200),
                ("Stop app beaconing" + (" (running)" if state.get("beacon_armed") else ""), "stop"),
                ("Send recent TH-D75 GPS fix", "gps_beacon"),
                ("Native beaconing on radio", "native"),
            )
        band = self.store.get("packet_band", None)
        speed = self.store.get("packet_speed", None)
        return (
            ("Start / end / restore KISS", "kiss"),
            ("Handset KISS monitor", "monitor"),
            ("App data band: " + {None: "Active", 0: "A", 1: "B"}[band], "band"),
            ("App rate: " + {None: "Radio", 0: "1200 bps", 1: "9600 bps"}[speed], "speed"),
            ("APRS callsign: " + (self.store.get("callsign") or "Set yours"), "call"),
            ("Beacon: " + ("Running" if state.get("beacon_armed") else "Setup"), "beacon"),
            ("APRS Outbox / acknowledgments", "outbox"),
            ("Export packet CSV / PCAP", "export"),
        )

    def export_capture(self):
        folder = Path.home() / "Documents" / "Rig-Companion-Captures"
        _, rows, raw = export(self.store.rows("traffic", 2500), folder)
        self.app.tell(f"Saved {rows} CSV / {raw} PCAP frames")

    def action(self, action, state):
        radio = self.app.radio
        if action == "kiss":
            self.app.kiss(state)
        elif action == "monitor":
            if state["owner"] == "monitor":
                radio.submit("end_kiss")
            else:
                self.screen = "monitor"
        elif action in ("band", "speed"):
            setting = "packet_band" if action == "band" else "packet_speed"
            values = (None, 0, 1)
            self.store.set(setting, values[(values.index(self.store.get(setting, None)) + 1) % len(values)])
            if state["kiss"]:
                self.app.tell("Applies to the next local KISS session")
        elif action == "call":
            self.app.editor, self.app.edit_value = "callsign", self.store.get("callsign")
        elif action in ("beacon", "outbox"):
            self.open(action, state)
        elif action == "position":
            self.position = self.store.get("beacon_position")
            self.screen = "position"
        elif action == "stop":
            radio.submit("stop_beacon")
        elif action == "native":
            self.open("guide", state, 2, "beacon")
        elif action == "export":
            self.app.background(self.export_capture)
        elif isinstance(action, int) or action == "gps_beacon":
            gps = action == "gps_beacon"
            position = self.app.radio.gps_clock.position() if gps else self.store.get("beacon_position")
            if gps and not position:
                raise RuntimeError("Get a fresh GPS fix before entering KISS")
            position_frame(self.store.get("callsign"), position, self.store.get("path"))
            session = (state.get("packet_band"), state.get("packet_hz"), state.get("packet_rate"))
            if state["owner"] != "local" or not session[1] or session[2] != 0:
                raise RuntimeError("Start local 1200 bps KISS first")
            self.confirmation = (position, 0 if gps else action, session, state.get("session_id"), gps)
            self.screen = "confirm"

    def handle(self, name, char, state):
        key = char.lower()
        if self.screen == "guide":
            self.guide_key(name, char)
        elif self.screen in ("outbox", "outbox_detail"):
            if name == "esc" or key == "4":
                self.screen = "outbox" if self.screen == "outbox_detail" else "packet"
                self.selected = 0
            elif self.screen == "outbox":
                messages = self.store.outbox()
                if name == "enter" and messages:
                    self.outgoing = messages[min(self.selected, len(messages) - 1)]
                    self.screen = "outbox_detail"
                elif name in ("up", "down") or key in ("f", "x"):
                    self.move(len(messages), name, key)
        elif self.screen == "position":
            if name == "esc":
                self.screen = "beacon"
            elif name == "backspace":
                self.position = self.position[:-1]
            elif name == "enter":
                position_frame("N0CALL", self.position)
                self.store.set("beacon_position", self.position)
                self.screen = "beacon"
            elif char in "+-.,0123456789 " and len(self.position) < 29:
                self.position += char
        elif self.screen in ("confirm", "monitor"):
            if name == "esc" or name == "enter":
                if name == "enter":
                    if self.screen == "confirm":
                        self.app.radio.submit("beacon", *self.confirmation)
                    else:
                        self.app.radio.submit("monitor")
                self.screen = "beacon" if self.screen == "confirm" else "packet"
                self.confirmation = None
        elif name == "esc" or key == "4":
            self.screen = "packet" if self.screen == "beacon" else ""
            self.selected = 0
        else:
            rows = self.options(state)
            if name == "enter":
                self.action(rows[self.selected][1], state)
            elif name in ("up", "down") or key in ("f", "x"):
                self.move(len(rows), name, key)

    def move(self, count, name, key):
        delta = -1 if name == "up" or key == "f" else 1
        self.selected = max(0, min(count - 1, self.selected + delta))

    def guide_key(self, name, char):
        if name == "esc":
            if self.typing:
                self.typing = False
            elif self.detail:
                self.detail = None
            else:
                self.screen = ""
        elif self.typing:
            if name == "enter":
                self.typing = False
            elif name == "backspace":
                self.query = self.query[:-1]
            elif char and len(self.query) < 35:
                self.query += char
            self.selected = 0
        elif self.detail:
            if char == "4":
                self.detail = None
            elif name == "up" or char.lower() == "f":
                self.scroll = max(0, self.scroll - 1)
            elif name == "down" or char.lower() == "x":
                self.scroll = min(self.max_scroll, self.scroll + 1)
        elif char in ("4", "5", "6"):
            self.section, self.selected, self.query = int(char) - 4, 0, ""
        elif char == "7":
            self.typing = True
        elif char == "8":
            self.model = MODEL_NAMES[(MODEL_NAMES.index(self.model) + 1) % len(MODEL_NAMES)]
            self.selected, self.query = 0, ""
        else:
            rows = entries(self.section, self.query, self.model)
            if name == "enter" and rows:
                self.detail, self.scroll, self.max_scroll = rows[self.selected], 0, 0
            elif name in ("up", "down") or char.lower() in ("f", "x"):
                self.move(len(rows), name, char.lower())

    def draw(self, d, state):
        if self.screen == "guide":
            if self.detail:
                title, detail = self.detail
                d.header(title)
                self.max_scroll = max(0, d.wrap(detail, y=31, height=110, offset=self.scroll) - 6)
                d.footer(labels=["Back", "", "", "", ""])
                return
            d.header("Field Manual", self.model)
            rows = entries(self.section, self.query, self.model)
            query = self.query + ("_" if self.typing else "")
            d.text(query or ("Menus", "Keys", "Tips & Tricks")[self.section], 10, 30, ACCENT, 12, 298)
            self.draw_rows(d, [item[0] for item in rows], 49, 4)
            d.footer(self.section, ["Menus", "Keys", "Tips", "Search", "Model"])
        elif self.screen in ("outbox", "outbox_detail"):
            d.header("APRS Outbox")
            if self.screen == "outbox_detail":
                recipient, message, status, created = self.outgoing
                d.text(f"To {recipient}   {status}", 10, 33, ACCENT, 14, 298)
                d.wrap(message, y=59, height=65)
                d.text(created + " UTC", 10, 128, DIM, 12)
            else:
                messages = self.store.outbox()
                self.draw_rows(d, [f"{recipient}  |  {status}" for recipient, _, status, _ in messages], 30, 5)
            d.footer(labels=["Back", "", "", "", ""])
        elif self.screen == "monitor":
            d.header("Handset KISS monitor")
            d.wrap("Set KISS output to Bluetooth and enter KISS on the radio. Enter starts receive-only monitoring. End KISS on the radio afterward.", y=36, height=105)
            d.text("Enter monitor   Esc back", 10, 149, DIM, 12)
        elif self.screen == "position":
            d.header("Fixed beacon position")
            d.text(self.position + "_", 10, 53, ACCENT, 17, 298)
            d.wrap("Enter decimal latitude, longitude. A beacon publishes this location.", y=83, height=51)
            d.text("Enter save   Esc cancel", 10, 149, DIM, 12)
        elif self.screen == "confirm":
            position, interval, (band, hz, _), _, gps = self.confirmation
            d.header("Transmit position?")
            d.text(f"{self.store.get('callsign')}   Band {'AB'[band]}   {hz / 1e6:.6f} MHz", 10, 39, ACCENT, 14, 298)
            d.wrap(f"{'Recent GPS fix' if gps else 'Fixed position'}: {position}\n" + (f"Send now and every {interval // 60} min." if interval else "Send one APRS position report."),
                   y=67, height=68)
            d.text("Enter transmit   Esc cancel", 10, 149, DIM, 12)
        else:
            title = "APRS / Packet" if self.screen == "packet" else "APRS Beacon"
            band, hz, rate = (state.get(key) for key in ("packet_band", "packet_hz", "packet_rate"))
            tn = state.get("native_tnc")
            status = (f"{'AB'[band]} {hz / 1e6:.3f}  {1200 if rate == 0 else 9600}" if band in (0, 1) and hz and rate in (0, 1)
                      else "KISS monitor" if state["kiss"] else
                      f"Native APRS {'AB'[tn[1]]}" if tn and tn[0] == 1 else
                      "CAT" if state["connected"] else "Offline")
            d.header(title, status)
            self.draw_rows(d, [label for label, _ in self.options(state)], 30, 5)
            d.footer(labels=["Back", "", "", "", ""])

    def draw_rows(self, d, rows, y, visible):
        if not rows:
            d.text("No matching entries", 10, y + 9, DIM, 14)
        start = max(0, self.selected - visible + 1)
        for index, label in enumerate(rows[start:start + visible], start):
            if index == self.selected:
                d.rect(7, y, 306, 19, SELECT, 3)
            d.text(label, 12, y + 1, INK, 14, 292)
            y += 20
