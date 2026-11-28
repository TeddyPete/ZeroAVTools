"""D-STAR callsign routing over QuadNet (ircDDB) and G2, like a Pi-Star hotspot.

How it works (same as ircDDBGateway):
* The gateway logs in to the QuadNet ircDDB IRC server as "<callsign>-N" and
  joins #dstar. Everybody's IRC host is their gateway's public IP address.
* The server bot ("s-...") answers "FIND <callsign>" with the repeater the
  user was last heard on and (table 1) the gateway that repeater belongs
  to. The gateway's IP is the host of its "<gateway>-N" IRC nick.
* Voice then goes straight to that gateway over UDP port 40000 (G2, DSVT
  packets). Calls to us arrive on our UDP port 40000.
* Each transmission is reported with "UPDATE ..." so others can find us.

Everything except IrcDDB's socket thread is pure and testable.
"""
from __future__ import annotations

import json
import random
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .dstar import Header, NetFrame, pad

QUADNET = ("ircv4.openquad.net", 9007)
CHANNEL = "#dstar"
G2_PORT = 40000
ROUTE_MAX_AGE = 15 * 60          # cached lookups are reused for 15 minutes
FIND_TIMEOUT = 4.0


def utc_stamp(when: float | None = None) -> str:
    return datetime.fromtimestamp(when or time.time(), timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def irc_split(line: str):
    """':nick!user@host CMD a b :rest' -> (prefix, command, [params])."""
    prefix = ""
    if line.startswith(":"):
        prefix, _, line = line[1:].partition(" ")
    if " :" in line:
        head, _, trailing = line.partition(" :")
        parts = head.split() + [trailing]
    else:
        parts = line.split()
    if not parts:
        return prefix, "", []
    return prefix, parts[0].upper(), parts[1:]


def prefix_nick(prefix: str) -> str:
    return prefix.split("!", 1)[0]


def prefix_host(prefix: str) -> str:
    return prefix.rsplit("@", 1)[-1] if "@" in prefix else ""


def irc_call(call: str) -> str:
    """'N0TNT  B' -> 'N0TNT__B' (ircDDB keeps callsigns 8 wide with underscores)."""
    return pad(call).replace(" ", "_")


def from_irc(call: str) -> str:
    return call.replace("_", " ").ljust(8)[:8]


def nick_base(call: str) -> str:
    """Gateway or repeater callsign -> IRC nick stem ('N0TNT  G' -> 'n0tnt')."""
    return from_irc(call)[:7].strip().lower()


def parse_update(text: str):
    """'[1] 2026-11-28 10:00:00 KEY VALUE ...' -> (table, stamp, key, value, rest) or None."""
    parts = text.split()
    table = 0
    if parts and parts[0].isdigit() and len(parts[0]) == 1:
        table = int(parts.pop(0))
    if len(parts) < 4 or len(parts[0]) != 10 or parts[0][4] != "-" or len(parts[1]) != 8 or parts[1][2] != ":":
        return None
    return table, parts[0] + " " + parts[1], parts[2], parts[3], parts[4:]


def heard_message(my, suffix, ur, rpt1, rpt2, flags=b"\0\0\0", message=""):
    """The UPDATE line a gateway sends to the ircDDB server when a user transmits."""
    def clean(value, size=8):
        text = (value or "").upper()[:size].ljust(size)
        return "".join(c if c.isalnum() or c == "/" else "_" for c in text)
    flags = bytes(flags[:3]).ljust(3, b"\0")
    line = (f"UPDATE {utc_stamp()} {clean(my)} {clean(rpt1)} 0 {clean(rpt2)} {clean(ur)} "
            f"{flags[0]:02X} {flags[1]:02X} {flags[2]:02X} {clean(suffix, 4)} 00 ________")
    text = (message or "")[:20]
    if text.strip():
        line += " " + "".join(c if c.isalnum() or c in "/.-" else "_" for c in text.ljust(20))
    return line


# --------------------------------------------------------------------------- G2 packets
def _dsvt(kind: int, stream: int, seq: int) -> bytes:
    return b"DSVT" + bytes([kind, 0x00, 0x15, 0x09, 0x20, 0x00, 0x01, 0x02]) + stream.to_bytes(2, "big") + bytes([seq])


def g2_header_packet(header: Header, stream: int) -> bytes:
    return _dsvt(0x10, stream, 0x80) + header.pack()


def g2_data_packet(stream: int, seq: int, frame: bytes, end: bool = False) -> bytes:
    from .dstar import END_FRAME
    if end:
        return _dsvt(0x20, stream, (seq % 21) | 0x40) + END_FRAME
    return _dsvt(0x20, stream, seq % 21) + bytes(frame[:12]).ljust(12, b"\0")


def parse_g2(packet: bytes) -> NetFrame:
    if len(packet) < 27 or packet[:4] != b"DSVT":
        return NetFrame("other")
    stream = int.from_bytes(packet[12:14], "big")
    if packet[4] == 0x10 and len(packet) >= 56:
        try:
            return NetFrame("header", stream=stream, header=Header.parse(packet[15:56]))
        except ValueError:
            return NetFrame("other")
    seq = packet[14]
    return NetFrame("data", stream=stream, seq=seq & 0x1F, end=bool(seq & 0x40), frame=bytes(packet[15:27]))


# --------------------------------------------------------------------------- ircDDB client
class RouteBook:
    """What we know from ircDDB: users, repeaters -> gateways, gateway IPs."""

    def __init__(self):
        self.users = {}          # 'W1XYZ' -> (repeater 'W1ABC  B', stamp, time.time())
        self.repeaters = {}      # 'W1ABC  B' -> zone 'W1ABC  G'
        self.nicks = {}          # 'w1abc-1' -> (host, order)
        self.missing = {}        # 'W1XYZ' -> time.time() of NOT_FOUND
        self.order = 0
        self.newest = "2000-02-01 00:00:00"

    def join(self, nick: str, host: str):
        self.order += 1
        self.nicks[nick.lower()] = (host, self.order)

    def leave(self, nick: str):
        self.nicks.pop(nick.lower(), None)

    def gateway_ip(self, gateway: str) -> str:
        stem = nick_base(gateway)
        best = None
        for n in range(1, 5):
            entry = self.nicks.get(f"{stem}-{n}")
            if entry and (best is None or entry[1] > best[1]):
                best = entry
        return best[0] if best else ""

    def apply(self, update):
        table, stamp, key, value, _rest = update
        if table == 1:
            self.repeaters[from_irc(key)] = from_irc(value)
            if stamp > self.newest:
                self.newest = stamp
        elif table == 0:
            call = from_irc(key).strip()
            self.users[call] = (from_irc(value), stamp, time.time())
            self.missing.pop(call, None)
            if len(self.users) > 4000:            # channel updates never stop; keep memory small
                for old in sorted(self.users, key=lambda c: self.users[c][2])[:1000]:
                    del self.users[old]
        return table

    def route(self, call: str):
        """-> {'call','repeater','gateway','ip','stamp'} or None if unknown."""
        call = call.upper().strip()
        user = self.users.get(call)
        if not user:
            return None
        repeater, stamp, _ = user
        zone = self.repeaters.get(repeater) or repeater
        gateway = zone[:7].ljust(7) + "G"
        return {"call": call, "repeater": repeater, "gateway": gateway, "ip": self.gateway_ip(gateway), "stamp": stamp}

    def fresh(self, call: str):
        user = self.users.get(call.upper().strip())
        return user is not None and time.time() - user[2] < ROUTE_MAX_AGE

    def save(self, path: Path):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {"newest": self.newest, "repeaters": {irc_call(k): irc_call(v) for k, v in self.repeaters.items()}}
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(path)
        except OSError:
            pass

    def load(self, path: Path):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return
        self.newest = data.get("newest", self.newest)
        self.repeaters = {from_irc(k): from_irc(v) for k, v in data.get("repeaters", {}).items()}


class IrcDDB:
    """Background QuadNet (ircDDB) client. Thread-safe find()/route()/heard()."""

    def __init__(self, callsign: str, cache_file: Path, server=QUADNET):
        self.callsign = callsign.upper().split("-")[0][:7]
        self.server = server
        self.cache_file = cache_file
        self.book = RouteBook()
        self.book.load(cache_file)
        self.lock = threading.RLock()
        self.sock = None
        self.nick = ""
        self.bot = ""
        self.state = "Off"        # Off, Connecting, Joining, Ready, Offline
        self.online = 0
        self.error = ""
        self.pending = {}         # call -> time FIND was sent
        self.queue_heard = []
        self.stop_event = threading.Event()
        self.thread = None

    # ---------------------------------------------------------------- public
    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="rig-ircddb")
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self._send("QUIT :Rig Companion off")
        if self.thread:
            self.thread.join(3)
        with self.lock:
            self.state = "Off"

    @property
    def ready(self):
        return self.state == "Ready"

    def status(self):
        with self.lock:
            return {"state": self.state, "online": self.online, "nick": self.nick, "error": self.error}

    def find(self, call: str):
        call = call.upper().strip()
        with self.lock:
            self.pending[call] = time.time()
        if self.bot:
            self._send(f"PRIVMSG {self.bot} :FIND {irc_call(call)}")

    def result(self, call: str):
        """Answer to the last find(): ('ok', route) | ('missing', route or None) | ('wait', None)."""
        call = call.upper().strip()
        with self.lock:
            asked = self.pending.get(call, 0.0)
            route = self.book.route(call)
            user = self.book.users.get(call)
            if user and user[2] >= asked - 0.5:
                return ("ok", route) if route["ip"] else ("missing", route)
            if self.book.missing.get(call, 0) >= asked - 0.5:
                return "missing", route
            if not self.ready or time.time() - asked > FIND_TIMEOUT:
                return "missing", route
        return "wait", None

    def lookup(self, call: str):
        """Cached route if fresh and with a known IP, else None (and a FIND is sent)."""
        call = call.upper().strip()
        with self.lock:
            if self.book.fresh(call):
                route = self.book.route(call)
                if route and route["ip"]:
                    return route
        self.find(call)
        return None

    def heard(self, **fields):
        line = heard_message(**fields)
        if self.bot:
            self._send(f"PRIVMSG {self.bot} :{line}")
        else:
            with self.lock:
                self.queue_heard = (self.queue_heard + [line])[-4:]

    # ---------------------------------------------------------------- socket thread
    def _send(self, line: str):
        sock = self.sock
        if not sock:
            return
        try:
            with self.lock:
                sock.sendall((line + "\r\n").encode("latin-1", "replace"))
        except OSError:
            pass

    def _run(self):
        delay = 5
        while not self.stop_event.is_set():
            try:
                self._session()
                delay = 5
            except OSError as error:
                with self.lock:
                    self.state, self.error = "Offline", str(error)
            if self.sock:
                try:
                    self.sock.close()
                except OSError:
                    pass
                self.sock = None
            self.bot = ""
            if self.stop_event.wait(delay):
                break
            delay = min(delay * 2, 120)
        self.book.save(self.cache_file)

    def _session(self):
        with self.lock:
            self.state, self.error, self.online = "Connecting", "", 0
        self.book.nicks.clear()
        sock = socket.create_connection(self.server, timeout=15)
        sock.settimeout(1.0)
        self.sock = sock
        nicks = [f"{self.callsign.lower()}-{n}" for n in (1, 2, 3, 4)]
        random.shuffle(nicks)
        self.nick = nicks.pop()
        self._send("NICK " + self.nick)
        self._send(f"USER {self.callsign.lower()} 0 * :RigCompanion-{__version__}")
        buffer = b""
        last_rx = time.monotonic()
        pinged = False
        while not self.stop_event.is_set():
            try:
                data = sock.recv(16384)
                if not data:
                    raise ConnectionError("QuadNet closed the connection")
                buffer += data
                last_rx = time.monotonic()
                pinged = False
            except socket.timeout:
                idle = time.monotonic() - last_rx
                if idle > 150:
                    raise ConnectionError("QuadNet stopped answering")
                if idle > 90 and not pinged:
                    self._send("PING " + self.nick)
                    pinged = True
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                self._line(raw.decode("latin-1").rstrip("\r"), nicks)

    def _line(self, line: str, spare_nicks):
        prefix, command, params = irc_split(line)
        if command == "PING":
            self._send("PONG :" + (params[0] if params else ""))
        elif command == "001" and params:
            self.nick = params[0]
        elif command == "004":
            with self.lock:
                self.state = "Joining"
            self._send("JOIN " + CHANNEL)
        elif command == "433":
            if not spare_nicks:
                raise ConnectionError("All four QuadNet nicknames are in use")
            self.nick = spare_nicks.pop()
            self._send("NICK " + self.nick)
        elif command == "JOIN" and params and params[0].lower() == CHANNEL:
            nick = prefix_nick(prefix)
            if nick.lower() == self.nick.lower():
                self._send(f"WHO {CHANNEL} *")
            else:
                self.book.join(nick, prefix_host(prefix))
                self.online += 1
        elif command in ("PART", "QUIT"):
            self.book.leave(prefix_nick(prefix))
            self.online = max(0, self.online - 1)
        elif command == "KICK" and len(params) >= 2:
            if params[1].lower() == self.nick.lower():
                raise ConnectionError("Kicked from QuadNet")
            self.book.leave(params[1])
        elif command == "352" and len(params) >= 7:       # WHO reply: me chan user host server nick flags
            nick, host, flags = params[5], params[3], params[6]
            self.book.join(nick, host)
            self.online += 1
            if nick.startswith("s-") and "@" in flags and not self.bot:
                self.bot = nick
        elif command == "315":                             # end of WHO
            if not self.bot:
                raise ConnectionError("No QuadNet server bot found")
            with self.lock:
                self.state = "Ready"
                queued, self.queue_heard = self.queue_heard, []
            for heard in queued:
                self._send(f"PRIVMSG {self.bot} :{heard}")
            for call in list(self.pending):
                self._send(f"PRIVMSG {self.bot} :FIND {irc_call(call)}")
        elif command == "MODE" and len(params) >= 3 and params[0].lower() == CHANNEL:
            if "+o" in params[1] and params[2].startswith("s-") and not self.bot:
                self.bot = params[2]
        elif command == "PRIVMSG" and len(params) >= 2 and prefix_nick(prefix).startswith("s-"):
            self._bot_message(params[0], params[1])
        elif command == "ERROR":
            raise ConnectionError(params[0] if params else "QuadNet error")

    def _bot_message(self, target: str, text: str):
        if target.lower() == CHANNEL:
            update = parse_update(text)
            if update:
                with self.lock:
                    self.book.apply(update)
            return
        words = text.split()
        if not words:
            return
        kind = words[0].upper()
        if kind == "UPDATE":
            update = parse_update(text.partition(" ")[2])
            if update:
                with self.lock:
                    self.book.apply(update)
        elif kind == "NOT_FOUND":
            rest = words[1:]
            if rest and rest[0].isdigit() and len(rest[0]) == 1:
                rest = rest[1:]
            if rest:
                with self.lock:
                    self.book.missing[from_irc(rest[0]).strip()] = time.time()
        elif kind == "LIST_END":
            self.book.save(self.cache_file)
