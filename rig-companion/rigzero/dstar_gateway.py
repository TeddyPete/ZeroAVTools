"""TH-D75 Reflector Terminal gateway: radio (MMDVM serial) <-> D-STAR reflector.

One worker thread owns the radio's gateway stream and the reflector UDP
socket. The UI talks to it only through submit() and snapshot().
"""
from __future__ import annotations

import copy
import json
import os
import queue
import random
import select
import socket
import termios
import threading
import time
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .dstar import (DSTAR_DATA, DSTAR_EOT, DSTAR_HEADER, DSTAR_LOST, GET_STATUS, GET_VERSION, ACK, NAK,
                    MODE_DSTAR, NULL_AMBE, SET_MODE, SYNC, Header, MmdvmDecoder, Reflector, SlowData,
                    dplus_auth_packet, dprs_to_tnc2, encode_message, is_sync, mmdvm_config, mmdvm_frame,
                    module_call, pad, parse_hosts, reflector_kind, slow_data_for, urcall_command, valid_reflector,
                    version_text)
from .dstar_route import G2_PORT, IrcDDB, g2_data_packet, g2_header_packet, parse_g2
from .protocol import decode_tnc2, inspect_packet

HOST_FILES = {"REF": "DPlus_Hosts.txt", "XRF": "DExtra_Hosts.txt", "DCS": "DCS_Hosts.txt", "XLX": "XLXHosts.txt"}
HOST_URL = "https://www.pistar.uk/downloads/"
HOST_MAX_AGE = 7 * 86400
FRAME_SECONDS = 0.020
HEARD_LIMIT = 60
ECHO_LIMIT = 3000          # 60 s of voice
USER_AGENT = f"RigCompanion/{__version__} (CardputerZero; D-STAR)"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def base_call(call: str) -> str:
    return (call or "").upper().split("-")[0].strip()


class Hosts:
    """Reflector address book from the Pi-Star host lists, cached on disk."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.table = {}
        self.updated = 0.0
        self.error = ""
        self.load()

    def load(self):
        table, newest = {}, 0.0
        for prefix, name in HOST_FILES.items():
            path = self.folder / name
            try:
                table.update(parse_hosts(path.read_text(encoding="utf-8", errors="replace"),
                                         "" if prefix == "XLX" else prefix))
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                continue
        self.table, self.updated = table, newest

    def stale(self):
        return not self.table or time.time() - self.updated > HOST_MAX_AGE

    def refresh(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        failures = 0
        for name in HOST_FILES.values():
            try:
                request = urllib.request.Request(HOST_URL + name, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(request, timeout=12) as response:
                    data = response.read(400_000)
                if b"\t" not in data and b";" not in data:
                    raise ValueError("Unexpected host list")
                temp = self.folder / (name + ".part")
                temp.write_bytes(data)
                temp.replace(self.folder / name)
            except Exception:
                failures += 1
        self.load()
        self.error = "" if failures == 0 else ("Reflector list download failed" if not self.table else
                                               "Some reflector lists could not update")
        return failures == 0

    def lookup(self, name: str):
        return self.table.get(name.upper())

    def names(self, prefix: str = "", query: str = ""):
        query = query.upper()
        return sorted(name for name in self.table if name.startswith(prefix) and query in name)


class Callbook:
    """Optional name / location lookup for US callsigns (callook.info), cached."""

    def __init__(self, store):
        self.store = store
        self.pending = queue.Queue(maxsize=16)
        self.cache = store.get("dstar_names", {}) or {}
        self.thread = threading.Thread(target=self._run, daemon=True, name="rig-dstar-callbook")
        self.thread.start()

    def get(self, call: str):
        call = base_call(call)
        entry = self.cache.get(call)
        if entry is None and self.store.get("dstar_lookup", True) and self._us(call):
            self.cache[call] = None
            try:
                self.pending.put_nowait(call)
            except queue.Full:
                self.cache.pop(call, None)
        return entry or None

    @staticmethod
    def _us(call: str) -> bool:
        return 3 <= len(call) <= 6 and call[0] in "KNW" or (call[:1] == "A" and call[1:2] in "ABCDEFGHIJKL")

    def _run(self):
        while True:
            call = self.pending.get()
            result = {}
            try:
                request = urllib.request.Request(f"https://callook.info/{call}/json", headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(request, timeout=8) as response:
                    data = json.loads(response.read(20_000))
                if data.get("status") == "VALID":
                    name = (data.get("name") or "").title()
                    place = (data.get("address") or {}).get("line2", "")
                    place = place.rsplit(" ", 1)[0].title() if place else ""
                    result = {"name": name, "place": place,
                              "grid": (data.get("location") or {}).get("gridsquare", "")}
            except Exception:
                result = {}
            self.cache[call] = result
            if len(self.cache) > 400:
                for key in list(self.cache)[:100]:
                    self.cache.pop(key, None)
            self.store.set("dstar_names", {k: v for k, v in self.cache.items() if v is not None})


class RadioPort:
    """The TH-D75 gateway stream over Bluetooth RFCOMM or USB serial."""

    def __init__(self, kind, address="", channel=2, device=""):
        self.kind = kind
        self.sock = None
        self.fd = None
        if kind == "usb":
            self.fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            attrs = termios.tcgetattr(self.fd)
            attrs[0] = attrs[1] = attrs[3] = 0
            attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            attrs[4] = attrs[5] = termios.B115200
            termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
            self.label = "USB " + device
        else:
            errors = []
            for ch in dict.fromkeys((channel, 2, 1)):
                sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
                sock.settimeout(8)
                try:
                    sock.connect((address, ch))
                except OSError as error:
                    sock.close()
                    errors.append(f"ch {ch}: {error.strerror or error}")
                    continue
                sock.setblocking(False)
                self.sock, self.channel = sock, ch
                break
            if not self.sock:
                self.detail = "; ".join(errors)
                raise ConnectionError("TH-D75 not answering. Check power, Menu 650 and 985.")
            self.label = f"Bluetooth ch {self.channel}"

    def fileno(self):
        return self.sock.fileno() if self.sock else self.fd

    def read(self):
        try:
            data = self.sock.recv(4096) if self.sock else os.read(self.fd, 4096)
        except (BlockingIOError, InterruptedError, socket.timeout):
            return b""
        if not data:
            raise ConnectionError("Radio closed the gateway link")
        return data

    def write(self, data: bytes):
        view = memoryview(data)
        deadline = time.monotonic() + 1
        while view:
            try:
                sent = self.sock.send(view) if self.sock else os.write(self.fd, view)
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise TimeoutError("Radio is not accepting gateway data")
                select.select([], [self.fileno()], [], .05)
                continue
            view = view[sent:]

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
        self.sock = self.fd = None


class Stream:
    """One transmission, from the network or from the radio."""

    def __init__(self, source, header, reflector=""):
        self.source = source            # "NET" or "RF"
        self.header = header
        self.slow = SlowData()
        self.started = time.time()
        self.last = time.monotonic()
        self.frames = 0
        self.seq = 0
        self.stream_id = 0
        self.net_header = None
        self.route = None               # direct call: {"call", "state", "ip", "header"}
        self.held = []                  # frames kept while the route is looked up
        self.g2 = None                  # sender address of an incoming direct call
        self.entry = {"call": header.caller, "suffix": header.suffix.strip(), "ur": header.ur.strip(),
                      "rpt1": header.rpt1.strip(), "rpt2": header.rpt2.strip(), "source": source,
                      "reflector": reflector, "time": now_iso(), "seconds": 0.0, "message": "",
                      "lat": None, "lon": None, "active": True}


class DStarGateway:
    def __init__(self, store, radio=None, cache_folder: Path | None = None):
        self.store, self.radio = store, radio
        cache = cache_folder or Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "rig-companion-zero"
        self.hosts = Hosts(cache / "dstar-hosts")
        self.callbook = Callbook(store)
        self.actions = queue.Queue(maxsize=32)
        self.lock = threading.RLock()
        self.port = None
        self.udp = None
        self.reflector = None
        self.peer = None
        self.decoder = MmdvmDecoder()
        self.rx = None                  # stream from network to radio
        self.tx = None                  # stream from radio to network
        self.out = deque()              # MMDVM frames waiting to go to the radio, paced at 20 ms
        self.next_out = 0.0
        self.echo = None
        self.pending_command = None
        self.counter = 0
        self.next_keepalive = self.next_status = 0.0
        self.last_net = self.last_radio = 0.0
        self.link_started = 0.0
        self.relink_at = 0.0
        self.ircddb = None
        self.g2 = None
        self.g2_counter = 0
        heard = store.get("dstar_heard", []) or []
        self.state = {"running": False, "transport": "", "radio": "Gateway off", "version": "",
                      "link": "Unlinked", "reflector": "", "module": "", "protocol": "", "linked_at": "",
                      "keepalive_age": None, "net_rx": 0, "net_tx": 0, "radio_frames": 0, "nak": 0,
                      "talker": None, "heard": heard[:HEARD_LIMIT], "error": "", "notice": "",
                      "read_only": False, "status_text": "",
                      "route_to": store.get("dstar_route_to", "") or "", "routing": "Off", "g2_port": 0}
        self.stop_event = threading.Event()
        self.thread = None

    # ------------------------------------------------------------------ UI side
    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.state)
        if result["keepalive_age"] is not None:
            result["keepalive_age"] = time.monotonic() - result["keepalive_age"]
        return result

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def submit(self, action, *args):
        if action == "start" and not (self.thread and self.thread.is_alive()):
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._run, daemon=True, name="rig-dstar")
            self.thread.start()
        try:
            self.actions.put_nowait((action, args))
            return True
        except queue.Full:
            self.update(error="D-STAR command queue full")
            return False

    def routing_enabled(self):
        return bool(self.store.get("dstar_routing", True))

    def my_repeater(self):
        return module_call(base_call(self.store.get("callsign")), self.store.get("dstar_module", "B"))

    def my_gateway(self):
        return module_call(base_call(self.store.get("callsign")), "G")

    def ensure_ircddb(self):
        """Start the QuadNet client (also used by the lookup screen while the gateway is off)."""
        call = base_call(self.store.get("callsign"))
        if not call or not self.routing_enabled():
            return None
        if self.ircddb is None or self.ircddb.callsign != call[:7]:
            if self.ircddb:
                self.ircddb.stop()
            self.ircddb = IrcDDB(call, self.hosts.folder.parent / "ircddb-routes.json")
        self.ircddb.start()
        return self.ircddb

    def route_to(self, call):
        """Set (or clear with '') the direct-call target from the UI."""
        if self.state["running"]:
            return self.submit("route", call) if call else self.submit("unroute")
        if call:
            self._do_route(call)
        else:
            self._do_unroute()
        return True

    def stop_ircddb(self):
        if self.ircddb:
            self.ircddb.stop()
            self.ircddb = None

    def routing_status(self):
        if not self.ircddb:
            return "Off" if not self.routing_enabled() else "Not connected"
        info = self.ircddb.status()
        if info["state"] == "Ready":
            return f"QuadNet ready · {info['online']} online"
        return f"QuadNet {info['state'].lower()}" + (f": {info['error']}" if info["error"] else "")

    def refresh_hosts(self, force=False):
        if force or self.hosts.stale():
            self.hosts.refresh()
        return self.hosts.error

    def close(self, timeout=4):
        if self.thread and self.thread.is_alive():
            self.submit("stop")
            self.thread.join(timeout)
        self.stop_event.set()
        self.stop_ircddb()

    # ------------------------------------------------------------------ worker
    def _run(self):
        while not self.stop_event.is_set():
            try:
                self._step()
            except (ConnectionError, OSError, TimeoutError) as error:
                self._radio_lost(str(error))
            except Exception as error:      # keep the gateway alive; show the problem
                self.update(error=str(error))
        self._shutdown()

    def _step(self):
        try:
            action, args = self.actions.get_nowait()
        except queue.Empty:
            action, args = None, ()
        if action:
            self.update(error="")
            getattr(self, "_do_" + action)(*args)
            return
        if not self.state["running"]:
            self.stop_event.wait(.05)
            return
        sockets = [s for s in (self.port, self.udp, self.g2) if s is not None]
        timeout = .02
        if self.out:
            timeout = max(0, min(timeout, self.next_out - time.monotonic()))
        ready = select.select(sockets, [], [], timeout)[0] if sockets else []
        if self.port and self.port in ready:
            data = self.port.read()
            if data:
                self.last_radio = time.monotonic()
                for command, payload in self.decoder.feed(data):
                    self._from_radio(command, payload)
        if self.udp and self.udp in ready:
            try:
                packet, sender = self.udp.recvfrom(2048)
            except (BlockingIOError, InterruptedError):
                packet = None
            if packet and self.reflector and sender[0] == self.peer[0]:
                self.last_net = time.monotonic()
                self._from_net(packet)
        if self.g2 and self.g2 in ready:
            try:
                packet, sender = self.g2.recvfrom(2048)
            except (BlockingIOError, InterruptedError):
                packet = None
            if packet:
                self._from_g2(packet, sender)
        self._timers()

    def _do_start(self):
        if self.state["running"]:
            return
        if self.radio:
            radio_state = self.radio.snapshot()
            if radio_state.get("kiss") or radio_state.get("owner"):
                raise RuntimeError("End KISS / packet sessions before starting D-STAR")
            self.radio.submit("suspend", "D-STAR gateway")
            deadline = time.monotonic() + 4
            while self.radio.snapshot().get("connected") and time.monotonic() < deadline:
                time.sleep(.1)
        kind = self.store.get("dstar_port", "bt")
        device = self.store.get("device", None) or {}
        self.update(radio="Opening radio link", error="")
        try:
            if kind == "usb":
                path = self.store.get("dstar_usb", "") or self._find_usb()
                self.port = RadioPort("usb", device=path)
            else:
                if not device.get("address"):
                    raise RuntimeError("Pair the TH-D75 under Link first")
                self.port = RadioPort("bt", address=device["address"], channel=int(self.store.get("dstar_channel", 2)))
        except Exception:
            if self.radio:
                self.radio.submit("resume")
            self.update(radio="Gateway off")
            raise
        self.decoder = MmdvmDecoder()
        self.last_radio = time.monotonic()
        self.update(running=True, transport=self.port.label, radio="Waiting for radio (Menu 650)", version="")
        self.port.write(mmdvm_frame(GET_VERSION))
        self.next_status = time.monotonic() + 1
        self._open_routing()
        favourite = self.store.get("dstar_autolink", "") if self.store.get("dstar_relink", True) else ""
        if favourite and valid_reflector(favourite[:6]) and len(favourite) >= 7:
            self.actions.put_nowait(("link", favourite[:6], favourite[6]))

    def _open_routing(self):
        if not self.routing_enabled():
            self.update(routing="Off")
            return
        irc = self.ensure_ircddb()
        if irc is None:
            return
        call = base_call(self.store.get("callsign"))
        irc.heard(my=call, suffix="", ur="CQCQCQ", rpt1=self.my_repeater(), rpt2=self.my_gateway())
        if self.g2 is None:
            self.g2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.g2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                self.g2.bind(("", G2_PORT))
            except OSError:
                self.g2.bind(("", 0))
                self.update(notice=f"UDP {G2_PORT} busy: direct calls in won't arrive")
            self.g2.setblocking(False)
            self.update(g2_port=self.g2.getsockname()[1])

    def _close_routing(self):
        if self.g2:
            self.g2.close()
            self.g2 = None
        self.update(g2_port=0)

    @staticmethod
    def _find_usb():
        for name in sorted(os.listdir("/dev")):
            if name.startswith("ttyACM"):
                return "/dev/" + name
        raise RuntimeError("Plug the TH-D75 into USB, or choose Bluetooth")

    def _do_stop(self):
        self._unlink(quiet=True)
        if self.port:
            self.port.close()
            self.port = None
        if self.state["running"] and self.radio:
            self.radio.submit("resume")
        self.out.clear()
        self.rx = self.tx = None
        self._close_routing()
        self.stop_ircddb()
        self.update(running=False, transport="", radio="Gateway off", talker=None)
        self.stop_event.set()

    def _shutdown(self):
        try:
            self._unlink(quiet=True)
        except Exception:
            pass
        if self.port:
            self.port.close()
            self.port = None
            if self.radio:
                self.radio.submit("resume")
        self._close_routing()
        self.update(running=False, radio="Gateway off")

    def _radio_lost(self, reason):
        if self.port:
            self.port.close()
            self.port = None
        self._unlink(quiet=True)
        if self.radio and self.state["running"]:
            self.radio.submit("resume")
        self._close_routing()
        self.update(running=False, radio="Radio link lost", error=reason, talker=None)
        self.stop_event.set()

    # ------------------------------------------------------------------ reflector link
    def _do_link(self, name, module):
        name, module = name.upper(), module.upper()
        if not valid_reflector(name) or not module.isalpha():
            raise ValueError("Choose a reflector like REF001 C")
        call = base_call(self.store.get("callsign"))
        if not call:
            raise RuntimeError("Set your callsign under Link first")
        address = self.hosts.lookup(name)
        if address is None and self.hosts.stale():
            self.update(notice="Updating reflector list")
            self.hosts.refresh()
            address = self.hosts.lookup(name)
        if address is None:
            raise RuntimeError(f"{name} is not in the reflector list")
        self._unlink(quiet=True)
        self._set_route_to("")
        host, port = address
        info = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
        self.reflector = Reflector(name, module, call)
        if self.reflector.protocol == "DPlus":
            self._dplus_register(call)
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.setblocking(False)
        self.udp.bind(("", 0))
        self.peer = info
        for packet in self.reflector.connect_packets():
            self._send_net(packet)
        self.link_started = time.monotonic()
        self.next_keepalive = time.monotonic() + self.reflector.keepalive_seconds()
        self.update(link="Linking", reflector=name, module=module, protocol=self.reflector.protocol,
                    keepalive_age=None, read_only=False, status_text="")
        recent = [entry for entry in self.store.get("dstar_recent", []) if entry != name + module]
        self.store.set("dstar_recent", ([name + module] + recent)[:12])

    # ------------------------------------------------------------------ direct calls (callsign routing)
    def _set_route_to(self, call):
        call = (call or "").upper().strip()
        self.store.set("dstar_route_to", call)
        self.update(route_to=call)

    def _do_route(self, call):
        """Talk straight to one callsign: CQCQCQ from the radio is routed to it."""
        call = base_call(call)[:8]
        if not call:
            raise ValueError("Type a callsign")
        if call == base_call(self.store.get("callsign")):
            raise ValueError("That's your own callsign")
        if self.reflector:
            self._do_unlink()
        self._set_route_to(call)
        irc = self.ensure_ircddb()
        if irc is None:
            raise RuntimeError("Turn on callsign routing in D-STAR setup")
        irc.find(call)
        self.update(notice=f"Calls go to {call} (direct)")

    def _do_unroute(self):
        self._set_route_to("")
        self.update(notice="Direct calls off")

    def _start_route(self, stream, call, header):
        irc = self.ircddb
        if irc is None:
            stream.route = {"call": call, "state": "failed", "why": "routing off"}
            return
        stream.route = {"call": call, "state": "wait", "ip": "", "header": header}
        route = irc.lookup(call)
        if route:
            self._route_found(stream, route)
        else:
            self.update(notice=f"Looking up {call}…")

    def _route_found(self, stream, route):
        if route["repeater"].strip() == self.my_repeater().strip():
            stream.route.update(state="failed", why="that's you")
            return
        radio = stream.route["header"]
        header = Header(rpt2=route["repeater"], rpt1=route["gateway"], ur=pad(stream.route["call"]),
                        my=radio.my, suffix=radio.suffix, flags=b"\x00\x00\x00")
        stream.route.update(state="ok", ip=route["ip"], port=route.get("port", G2_PORT), net=header,
                            repeater=route["repeater"].strip())
        stream.entry["reflector"] = f"Direct {stream.route['call']} via {route['repeater'].strip()}"
        target = (route["ip"], route.get("port", G2_PORT))
        for _ in range(5):
            self._send_g2(g2_header_packet(header, stream.stream_id), target)
        for seq, frame in stream.held:
            self._send_g2(g2_data_packet(stream.stream_id, seq, frame), target)
        stream.held = []
        self.update(notice=f"Calling {stream.route['call']} at {route['repeater'].strip()}",
                    talker=copy.deepcopy(stream.entry))

    def _check_route(self, stream):
        route = stream.route
        if not route or route["state"] != "wait" or not self.ircddb:
            return
        answer, found = self.ircddb.result(route["call"])
        if answer == "ok":
            self._route_found(stream, found)
        elif answer == "missing":
            why = "not on QuadNet" if not found else "gateway offline"
            route.update(state="failed", why=why)
            stream.held = []
            self.update(notice=f"{route['call']} {why}")

    def _send_g2(self, packet, target):
        if self.g2:
            try:
                self.g2.sendto(packet, target)
            except OSError:
                return
            with self.lock:
                self.state["net_tx"] += 1

    def _from_g2(self, packet, sender):
        frame = parse_g2(packet)
        if frame.kind == "header":
            header = frame.header
            if header.flags[0] & 0x01:                      # busy reply
                return
            mine = base_call(self.store.get("callsign"))
            to_me = header.rpt2.strip() == self.my_repeater().strip() or base_call(header.ur) == mine
            if not to_me or self.tx or (self.rx and self.rx.g2 != sender):
                return
            if self.rx and self.rx.stream_id == frame.stream:
                return                                      # repeated header
            if self.rx:
                self._end_rx(lost=True)
            self.rx = Stream("NET", header, "Direct call")
            self.rx.stream_id, self.rx.g2 = frame.stream, sender
            to_radio = Header(rpt2=self.my_gateway(), rpt1=self.my_repeater(), ur=header.ur, my=header.my,
                              suffix=header.suffix, flags=b"\x40\x00\x00")
            self._queue_radio(mmdvm_frame(DSTAR_HEADER, to_radio.pack()), hold=3)
            self._start_talker(self.rx)
            with self.lock:
                self.state["net_rx"] += 1
        elif frame.kind == "data" and self.rx and self.rx.g2 == sender and frame.stream == self.rx.stream_id:
            with self.lock:
                self.state["net_rx"] += 1
            stream = self.rx
            stream.last = time.monotonic()
            if frame.end:
                self._end_rx()
                return
            stream.frames += 1
            stream.slow.feed(frame.seq, frame.frame[9:12])
            self._slow_events(stream)
            self._queue_radio(mmdvm_frame(DSTAR_DATA, frame.frame))

    def _dplus_register(self, call):
        try:
            with socket.create_connection(("auth.dstargateway.org", 20001), timeout=6) as auth:
                auth.sendall(dplus_auth_packet(call))
                auth.settimeout(4)
                try:
                    auth.recv(4096)
                except socket.timeout:
                    pass
        except OSError:
            self.update(notice="D-Plus registry unreachable; REF may be listen-only")

    def _unlink(self, quiet=False):
        if self.reflector and self.udp:
            for packet in self.reflector.disconnect_packets():
                try:
                    self._send_net(packet)
                except OSError:
                    pass
        if self.udp:
            self.udp.close()
        self._end_rx(lost=True)
        self.udp = self.reflector = self.peer = None
        self.update(link="Unlinked", reflector="", module="", protocol="", linked_at="", keepalive_age=None)
        if not quiet:
            self.update(notice="Unlinked")

    def _do_unlink(self):
        self.store.set("dstar_autolink", "")
        self._unlink()

    def _send_net(self, packet):
        if self.udp and self.peer:
            self.udp.sendto(packet, self.peer)
            if len(packet) > 20:
                with self.lock:
                    self.state["net_tx"] += 1

    def _from_net(self, packet):
        frame = self.reflector.parse(packet)
        if frame.kind == "keepalive":
            self.update(keepalive_age=time.monotonic())
            if self.state["link"] == "Linking" and self.reflector.protocol == "DCS":
                self._linked()
        elif frame.kind == "echo":
            self._send_net(self.reflector.login_packet())
        elif frame.kind == "ack":
            self._linked(read_only=frame.text == "OKRO")
        elif frame.kind == "nak":
            name = self.state["reflector"]
            self._unlink(quiet=True)
            self.update(link="Refused", error=f"{name}: {frame.text or 'link refused'}")
        elif frame.kind == "status":
            self.update(status_text=frame.text)
        elif frame.kind in ("header", "data"):
            with self.lock:
                self.state["net_rx"] += 1
            if self.state["link"] == "Linking":
                self._linked()
            self._net_voice(frame)

    def _linked(self, read_only=False):
        self.update(link="Linked", linked_at=now_iso(), keepalive_age=time.monotonic(), read_only=read_only,
                    notice=f"Linked {self.state['reflector']} {self.state['module']}")
        self.store.set("dstar_autolink", self.state["reflector"] + self.state["module"])
        self.relink_at = 0

    def _timers(self):
        now = time.monotonic()
        if self.reflector and now >= self.next_keepalive:
            self._send_net(self.reflector.keepalive())
            self.next_keepalive = now + self.reflector.keepalive_seconds()
        if self.reflector:
            if self.state["link"] == "Linking" and now - self.link_started > 10:
                name, module = self.state["reflector"], self.state["module"]
                self._unlink(quiet=True)
                self.update(link="No answer", error=f"{name} {module} did not answer")
            elif self.state["link"] == "Linked" and self.last_net and now - self.last_net > 30:
                name, module = self.state["reflector"], self.state["module"]
                self.update(notice="Reflector silent; relinking")
                self._do_link(name, module)
        if self.port and now >= self.next_status and not self.out and not self.tx:
            self.port.write(mmdvm_frame(GET_STATUS))
            self.next_status = now + 1
            if now - self.last_radio > 6:
                self.update(radio="Radio not answering. Check Menu 650 and 985.")
            elif not self.state["version"]:
                self.port.write(mmdvm_frame(GET_VERSION))
        if self.tx and self.tx.route:
            self._check_route(self.tx)
        if self.rx and now - self.rx.last > .5:
            self._end_rx(lost=True)
        if self.tx and now - self.tx.last > 1.0:
            self._end_tx()
        self._pump_radio(now)

    # ------------------------------------------------------------------ network -> radio
    def _net_voice(self, frame):
        if self.tx:                         # local operator is talking: half duplex
            return
        if self.rx and self.rx.g2:          # a direct call is playing
            return
        if self.rx and frame.stream != self.rx.stream_id:
            if frame.kind == "header" or frame.header is not None:
                self._end_rx(lost=True)
            else:
                return
        if not self.rx:
            if frame.header is None:
                return                      # data without a header yet (late join)
            self.rx = Stream("NET", frame.header, f"{self.state['reflector']} {self.state['module']}")
            self.rx.stream_id = frame.stream
            to_radio = Header(rpt2=frame.header.rpt2, rpt1=frame.header.rpt1, ur=frame.header.ur,
                              my=frame.header.my, suffix=frame.header.suffix, flags=b"\x40\x00\x00")
            self._queue_radio(mmdvm_frame(DSTAR_HEADER, to_radio.pack()), hold=3)
            self._start_talker(self.rx)
            if frame.kind == "header":
                return
        stream = self.rx
        stream.last = time.monotonic()
        if frame.end:
            self._end_rx()
            return
        data = frame.frame
        stream.frames += 1
        stream.slow.feed(frame.seq, data[9:12])
        self._slow_events(stream)
        self._queue_radio(mmdvm_frame(DSTAR_DATA, data))

    def _queue_radio(self, packet, hold=0):
        if not self.out:
            self.next_out = time.monotonic() + hold * FRAME_SECONDS
        self.out.append(packet)
        if len(self.out) > 150:                       # >3 s behind: drop oldest voice
            for _ in range(len(self.out) - 100):
                self.out.popleft()

    def _pump_radio(self, now):
        if not self.port:
            self.out.clear()
            return
        while self.out and now >= self.next_out:
            packet = self.out.popleft()
            self.port.write(packet)
            self.next_out = max(self.next_out + (FRAME_SECONDS if packet[2] == DSTAR_DATA else 0), now - .06)

    def _end_rx(self, lost=False):
        stream, self.rx = self.rx, None
        if not stream:
            return
        self._queue_radio(mmdvm_frame(DSTAR_LOST if lost else DSTAR_EOT))
        self._finish(stream)

    # ------------------------------------------------------------------ radio -> network
    def _from_radio(self, command, payload):
        with self.lock:
            self.state["radio_frames"] += 1
        if command == GET_VERSION:
            protocol, text = version_text(payload)
            self.port.write(mmdvm_config(protocol or 1))
            self.port.write(mmdvm_frame(SET_MODE, bytes([MODE_DSTAR])))
            self.update(version=text or f"MMDVM protocol {protocol}", radio="Radio ready")
        elif command == ACK:
            if self.state["radio"].startswith(("Waiting", "Radio not")):
                self.update(radio="Radio ready")
        elif command == NAK:
            with self.lock:
                self.state["nak"] += 1
        elif command == GET_STATUS:
            if self.state["radio"].startswith(("Waiting", "Radio not")):
                self.update(radio="Radio ready")
        elif command == DSTAR_HEADER and len(payload) >= 39:
            self._radio_header(Header.parse(payload))
        elif command == DSTAR_DATA and len(payload) >= 12 and self.tx:
            self._radio_data(payload[:12])
        elif command in (DSTAR_EOT, DSTAR_LOST) and self.tx:
            self._end_tx()

    def _radio_header(self, header):
        if self.tx:
            self._end_tx()
        if self.rx:                     # operator keyed over incoming audio
            self._end_rx(lost=True)
            self.out.clear()
        self.tx = Stream("RF", header, f"{self.state['reflector']} {self.state['module']}".strip())
        self.tx.stream_id = random.randint(1, 0xFFFF)
        self.pending_command = urcall_command(header.ur)
        if self.pending_command and self.pending_command[0] == "echo":
            self.echo = []
        route_call = ""
        if self.pending_command and self.pending_command[0] == "route":
            route_call, self.pending_command = self.pending_command[1], None
        elif not self.pending_command and self.state["route_to"] and not self.reflector:
            route_call = self.state["route_to"]
        self._start_talker(self.tx)
        if route_call:
            self._start_route(self.tx, route_call, header)
            return
        if self.pending_command:
            return
        if not self.reflector or self.state["link"] != "Linked":
            self.update(notice="Not linked: choose a reflector")
            return
        self.tx.net_header = self.reflector.tx_header(header)
        if self.reflector.protocol != "DCS":
            for _ in range(2):
                self._send_net(self.reflector.header_packet(self.tx.net_header, self.tx.stream_id))

    def _radio_data(self, frame):
        stream = self.tx
        stream.last = time.monotonic()
        if is_sync(frame[9:12]):
            stream.seq = 0
        seq = stream.seq
        stream.seq = (stream.seq + 1) % 21
        stream.frames += 1
        stream.slow.feed(seq, frame[9:12])
        self._slow_events(stream)
        if self.echo is not None and len(self.echo) < ECHO_LIMIT:
            self.echo.append(frame)
        if stream.route:
            if stream.route["state"] == "ok":
                self._send_g2(g2_data_packet(stream.stream_id, seq, frame), (stream.route["ip"], stream.route["port"]))
            elif stream.route["state"] == "wait" and len(stream.held) < 250:
                stream.held.append((seq, frame))
            return
        if self.pending_command or not self.reflector or stream.net_header is None:
            return
        self.counter += 1
        self._send_net(self.reflector.data_packet(stream.net_header, stream.stream_id, seq, frame, self.counter))

    def _end_tx(self):
        stream, self.tx = self.tx, None
        if not stream:
            return
        self._report_heard(stream)
        if stream.route:
            if stream.route["state"] == "ok":
                self._send_g2(g2_data_packet(stream.stream_id, stream.seq, b"", end=True), (stream.route["ip"], stream.route["port"]))
            elif stream.route["state"] in ("failed", "wait"):
                self._announce(stream.header.caller, f"{stream.route['call']} {stream.route.get('why', 'not found')}"[:20])
            self._finish(stream)
            return
        if self.reflector and stream.net_header is not None and not self.pending_command:
            self.counter += 1
            packet = self.reflector.data_packet(stream.net_header, stream.stream_id, stream.seq,
                                                NULL_AMBE + SYNC, self.counter, end=True)
            self._send_net(packet)
        self._finish(stream)
        command, self.pending_command = self.pending_command, None
        if command:
            self._run_command(command, stream.header)

    def _report_heard(self, stream):
        if not self.ircddb:
            return
        header = stream.header
        ur = stream.route["call"] if stream.route else header.ur
        try:
            self.ircddb.heard(my=header.my, suffix=header.suffix, ur=ur, rpt1=self.my_repeater(),
                              rpt2=self.my_gateway(), flags=header.flags, message=stream.slow.message or "")
        except Exception:
            pass

    def _run_command(self, command, header):
        action, name, module = command
        call = header.caller
        if action == "link":
            try:
                self._do_link(name, module)
            except Exception as error:
                self._announce(call, str(error)[:20])
                raise
        elif action == "unlink":
            self._do_unlink()
            self._announce(call, "Unlinked")
        elif action == "info":
            text = (f"Linked {self.state['reflector']} {self.state['module']}" if self.state["link"] == "Linked"
                    else "Not linked")
            self._announce(call, text)
        elif action == "echo":
            frames, self.echo = self.echo or [], None
            if frames:
                self._play(Header(rpt2=module_call("ECHO", "G"), rpt1=module_call("ECHO", "E"), ur=call,
                                  my="ECHO", suffix="TEST", flags=b"\x40\x00\x00"), frames, hold=50)

    def _announce(self, ur, text):
        """Short silent transmission to the radio that shows a 20-character message."""
        message = encode_message(text)
        frames = [NULL_AMBE + slow_data_for(i % 21, message) for i in range(42)]
        self._play(Header(rpt2=module_call("RIGCOMP", "G"), rpt1=module_call("RIGCOMP", "D"), ur=ur,
                          my="RIGCOMP", suffix="INFO", flags=b"\x40\x00\x00"), frames, hold=25)

    def _play(self, header, frames, hold=0):
        if self.rx:
            return
        self._queue_radio(mmdvm_frame(DSTAR_HEADER, header.pack()), hold=hold)
        for frame in frames:
            self._queue_radio(mmdvm_frame(DSTAR_DATA, frame))
        self._queue_radio(mmdvm_frame(DSTAR_EOT))

    # ------------------------------------------------------------------ dashboard
    def _start_talker(self, stream):
        entry = stream.entry
        info = self.callbook.get(entry["call"])
        if info:
            entry.update(name=info.get("name", ""), place=info.get("place", ""), grid=info.get("grid", ""))
        self.update(talker=copy.deepcopy(entry))

    def _slow_events(self, stream):
        changed = False
        if stream.slow.message and stream.slow.message != stream.entry["message"]:
            stream.entry["message"] = stream.slow.message
            changed = True
        for line in stream.slow.take_lines():
            tnc2 = dprs_to_tnc2(line, stream.header.caller)
            frame = decode_tnc2(tnc2) if tnc2 else None
            if not frame:
                continue
            packet = inspect_packet(*frame)
            packet.update(via="D-STAR", text=packet.get("text", ""))
            decoded = packet.get("decoded", {})
            if "lat" in decoded:
                stream.entry.update(lat=decoded["lat"], lon=decoded["lon"])
                changed = True
            try:
                self.store.record(packet)
            except Exception:
                pass
        if changed:
            self.update(talker=copy.deepcopy(stream.entry))

    def _finish(self, stream):
        entry = stream.entry
        entry["seconds"] = round(stream.frames * FRAME_SECONDS, 1)
        entry["active"] = False
        info = self.callbook.get(entry["call"])
        if info:
            entry.update(name=info.get("name", ""), place=info.get("place", ""), grid=info.get("grid", ""))
        with self.lock:
            heard = [entry] + self.state["heard"]
            self.state["heard"] = heard[:HEARD_LIMIT]
            self.state["talker"] = None
        if stream.frames or stream.source == "RF":
            self.store.set("dstar_heard", self.state["heard"])

    def names_for(self, call):
        return self.callbook.get(call)
