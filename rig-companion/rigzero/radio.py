"""One worker owns the radio stream, including CAT/native-APRS/KISS handoff."""
from __future__ import annotations

import copy
import queue
import re
import socket
import threading
import time
from decimal import Decimal, InvalidOperation

from .models import model_from_id
from .gps_clock import GpsClock
from .packet_native import Engine, md5, milliseconds
from .protocol import (KissDecoder, MODES, STEPS, decode_ax25, decode_tnc2, fo_frequency,
                       inspect_packet, kiss_encode, message_frame, position_frame)


class Radio:
    def __init__(self, store, link=None):
        self.store, self.link = store, link
        self.connection = None
        self.actions = queue.Queue(maxsize=32)
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.state = {"connected": False, "identity": False, "status": "Choose your TH-D75 under Link",
                      "active": 0, "dual": -1, "volume": -1, "kiss": False, "owner": "",
                      "rx": 0, "tx": 0, "invalid": 0, "last_rx": "", "error": "",
                      "bands": [{"hz": 0, "mode": -1, "power": -1, "channel": -1,
                                 "squelch": -1, "meter": -1, "busy": -1} for _ in range(2)]}
        self.state.update(packet_band=None, packet_hz=0, packet_rate=None, beacon_armed=False, session_id=0,
                          native_tnc=None, native_rate=None)
        self.state["model"] = ""
        self.device_address = None
        self.gps_clock = GpsClock()
        self.native = None
        self.native_log = []
        self.native_closing = 0
        self.state.update(packet_link="Idle", packet_peer="", packet_log=[])
        self.beacon = None
        self.line = bytearray()
        self.discard = False
        self.lines = []
        self.decoder = KissDecoder()
        self.identified_at = 0.0
        self.pending_kiss = False
        self.remote_reply = None
        self.next_poll = 0.0
        self.poll_index = 0
        self.closed = False
        self.suspended = ""         # another feature (D-STAR gateway) is using the radio link
        self.thread = threading.Thread(target=self._run, name="rig-radio", daemon=True)
        self.thread.start()

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.state)
        result["fresh"] = result["identity"] and time.monotonic() - self.identified_at < 6
        return result

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def submit(self, action, *args):
        try:
            self.actions.put_nowait((action, args))
            return True
        except queue.Full:
            self.update(error="Radio command queue full")
            return False

    def _receive(self):
        try:
            data = self.connection.recv(4096)
        except (socket.timeout, BlockingIOError):
            return
        if not data:
            raise ConnectionError("Bluetooth radio disconnected")
        if self.state["kiss"]:
            before = self.decoder.invalid
            for command, payload in self.decoder.feed(data):
                if command == 0:
                    frame = decode_ax25(payload)
                    if frame:
                        self.record(frame, raw=payload)
                    else:
                        self.state["invalid"] += 1
                    if self.remote_reply:
                        self.remote_reply(kiss_encode(payload))
                    if self.native:
                        try:
                            self.native.require("rx", payload, len(payload), milliseconds())
                        except OSError:
                            raise
                        except Exception:
                            self.packet_finish()
                            raise
            self.state["invalid"] += self.decoder.invalid - before
            return
        for byte in data:
            if byte in (10, 13):
                if self.line and not self.discard:
                    self._line(self.line.decode("ascii", "replace"))
                self.line.clear()
                self.discard = False
            elif byte < 32 or byte > 127 or len(self.line) >= 1024:
                self.discard = True
            elif not self.discard:
                self.line.append(byte)

    def record(self, frame, direction="RX", raw=None):
        packet = inspect_packet(*frame, direction=direction)
        utc, _ = self.gps_clock.reading()
        if utc is not None:
            packet["time"] = utc.isoformat(timespec="seconds")
        if raw is not None:
            packet["ax25_hex"] = raw.hex()
        if self.state["kiss"]:
            packet["packet_band"] = self.state["packet_band"]
            packet["frequency_hz"] = self.state["packet_hz"]
            packet["packet_rate"] = self.state["packet_rate"]
        self.store.record(packet)
        self.state["rx" if direction == "RX" else "tx"] += 1
        self.state["last_rx"] = packet["source"] + "  " + packet["kind"]

    def _line(self, line):
        if line.startswith("$"):
            if self.state["model"] == "TH-D75":
                self.gps_clock.observe(line)
            return
        frame = decode_tnc2(line)
        if frame:
            self.record(frame)
            return
        self.lines.append(line)
        self.lines = self.lines[-32:]
        model = model_from_id(line)
        if model:
            self.state["identity"] = True
            self.state["model"] = model
            self.identified_at = time.monotonic()
        match = re.fullmatch(r"(FQ|MD|PC|VM|SQ|SM|BY) ([01]),([0-9]+)", line)
        if match:
            field = {"FQ": "hz", "MD": "mode", "PC": "power", "VM": "channel",
                     "SQ": "squelch", "SM": "meter", "BY": "busy"}[match[1]]
            self.state["bands"][int(match[2])][field] = int(match[3])
        for code, key in (("BC", "active"), ("DL", "dual"), ("AG", "volume")):
            if re.fullmatch(code + r" [0-9]+", line):
                self.state[key] = int(line.split()[1])
        if re.fullmatch(r"TN [01],[01]", line):
            self.state["native_tnc"] = tuple(int(value) for value in line[3:].split(","))
        if re.fullmatch(r"AS [01]", line):
            self.state["native_rate"] = int(line[-1])

    def cat(self, command, expected=None, timeout=1.8):
        if self.state["kiss"]:
            raise RuntimeError("Stop KISS before changing radio controls")
        if not self.connection:
            raise RuntimeError("Connect the TH-D75 first")
        if not re.fullmatch(r"[ -~]{2,239}", command):
            raise ValueError("Invalid CAT command")
        self.lines.clear()
        self.connection.sendall((command + "\r").encode("ascii"))
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self._receive()
            for line in self.lines:
                if line in ("?", "N"):
                    raise RuntimeError(f"Radio rejected {command.split()[0]}")
                if expected and expected(line):
                    return line
            if expected is None:
                return ""
        raise TimeoutError(f"No readback for {command.split()[0]}")

    def query(self, command):
        return self.cat(command, lambda line: line == command or line.startswith(command + ",") or
                        (re.fullmatch(r"MR [01]", command) and re.fullmatch(r"MR [0-9]{3}", line)) or
                        (" " not in command and line.startswith(command + " ")))

    def identify(self):
        for attempt in range(3):
            try:
                return self.cat("ID", lambda line: model_from_id(line) is not None)
            except RuntimeError:
                if attempt == 2:
                    raise

    def require_control(self, packet=False):
        if not self.state["connected"] or not self.state["identity"]:
            raise RuntimeError("Radio identity is not confirmed")
        if self.store.get("recovery", None):
            raise RuntimeError("Restore the previous KISS session first")
        self.identify()
        if not packet and self.state["model"] != "TH-D75":
            raise RuntimeError("Use the handset controls for " + self.state["model"])

    def change(self, field, amount=1):
        self.require_control()
        band = self.state["active"]
        if field == "band":
            target = 1 - band
            self.cat(f"BC {target}")
            if self.query("BC") != f"BC {target}":
                raise RuntimeError("Band selection did not confirm")
            return
        mapping = {"mode": ("MD", 8), "power": ("PC", 4), "channel": ("VM", 2),
                   "squelch": ("SQ", 6), "dual": ("DL", 2), "volume": ("AG", 201)}
        code, count = mapping[field]
        query = code if field in ("dual", "volume") else f"{code} {band}"
        current = int(re.split(r"[, ]", self.query(query))[-1])
        if field == "volume":
            candidates = [max(0, min(200, current + amount))]
        elif field == "mode":
            direction = 1 if amount >= 0 else -1
            candidates = [(current + i * direction) % count for i in range(1, count + 1)]
        else:
            candidates = [(current + amount) % count]
        for value in candidates:
            separator = " " if field in ("dual", "volume") else ","
            try:
                self.cat(query + separator + str(value))
                actual = int(re.split(r"[, ]", self.query(query))[-1])
            except RuntimeError:
                if field == "mode":
                    continue
                raise
            if actual == value:
                return
        raise RuntimeError("Radio did not confirm this setting")

    def tune(self, mhz):
        self.require_control()
        try:
            decimal = Decimal(mhz) * 1000000
            if decimal != decimal.to_integral_value():
                raise ValueError("Frequency has more than six decimal places")
            frequency = int(decimal)
        except (InvalidOperation, OverflowError):
            raise ValueError("Enter a frequency in MHz") from None
        band = self.state["active"]
        if self.query(f"VM {band}") != f"VM {band},0":
            raise RuntimeError("Select VFO mode before frequency entry")
        record = self.query(f"FO {band}")
        self.cat(fo_frequency(record, band, frequency))
        if self.query(f"FQ {band}") != f"FQ {band},{frequency:010d}":
            raise RuntimeError("Frequency did not confirm")

    def step(self, direction):
        self.require_control()
        band = self.state["active"]
        mode = self.query(f"VM {band}")
        if mode == f"VM {band},1":
            readback = self.query(f"MR {band}")
            channel = int(re.split(r"[, ]", readback)[-1])
            for i in range(1, 1001):
                number = (channel + direction * i) % 1000
                try:
                    self.query(f"ME {number:03d}")
                except RuntimeError:
                    continue
                self.cat(f"MR {band},{number:03d}")
                if int(re.split(r"[, ]", self.query(f"MR {band}"))[-1]) == number:
                    self.query(f"FQ {band}")
                    return
            raise RuntimeError("No populated memory channel found")
        if mode != f"VM {band},0":
            raise RuntimeError("Select VFO or memory mode")
        current = int(self.query(f"FQ {band}").split(",")[1])
        record = self.query(f"FO {band}")
        code = record.split(",")[3]
        index = ord(code) - ord("A") + 10 if code in ("A", "B") else int(code)
        self.tune(str(Decimal(current + direction * STEPS[index]) / 1000000))

    def start_kiss(self, owner="local", frequency=None, speed=None):
        if self.state["kiss"]:
            if self.state["owner"] != owner:
                raise RuntimeError("Another session owns KISS")
            if frequency:
                self.end_kiss()
            else:
                return
        self.require_control(packet=True)
        model = self.state["model"]
        if frequency and model != "TH-D75":
            raise RuntimeError("Tune this model from the handset before starting KISS")
        band = self.state["active"]
        if owner in ("local", "terminal", "winlink"):
            selected_band = self.store.get("packet_band", None)
            if selected_band in (0, 1):
                band = selected_band
            speed = self.store.get("packet_speed", None) if speed is None else speed
        tn = self.query("TN")
        if not re.fullmatch(r"TN [01],[01]", tn):
            raise RuntimeError("End the radio's existing data session first")
        snapshot = {"tn": tn, "band": band, "model": model, "fo": self.query(f"FO {band}"),
                    "vm": self.query(f"VM {band}"), "rate": self.query("AS")}
        if self.device_address:
            snapshot["address"] = self.device_address
        if snapshot["rate"] not in ("AS 0", "AS 1"):
            raise RuntimeError("Radio did not report a supported packet rate")
        if (not re.fullmatch(rf"VM {band},[012]", snapshot["vm"])
                or not re.fullmatch(rf"FO {band},[0-9]+(?:,[^,]*){{19}}", snapshot["fo"])):
            raise RuntimeError("Can't verify the radio's restore values; try Handset KISS monitor")
        result = self.query(f"FQ {band}")
        if not re.fullmatch(rf"FQ {band},[0-9]+", result):
            raise RuntimeError("Could not verify the data band frequency")
        packet_hz = int(result.split(",")[1])
        self.store.set("recovery", snapshot)
        self.pending_kiss = True
        try:
            if frequency:
                self.cat(f"VM {band},0")
                if self.query(f"VM {band}") != f"VM {band},0":
                    raise RuntimeError("VFO setup did not confirm")
                record = fo_frequency(snapshot["fo"], band, frequency).split(",")
                record[5] = "0"
                self.cat(",".join(record))
                if self.query(f"FQ {band}") != f"FQ {band},{frequency:010d}":
                    raise RuntimeError("Station frequency did not confirm")
                if self.query(f"MD {band}") != f"MD {band},0":
                    raise RuntimeError("Packet FM did not confirm")
                packet_hz = frequency
            if speed is not None:
                if speed not in (0, 1):
                    raise ValueError("Invalid packet speed")
                self.cat(f"AS {speed}")
                if self.query("AS") != f"AS {speed}":
                    raise RuntimeError("Packet speed did not confirm")
            # Persist before TN, since a disconnect can happen after the write.
            self.cat(f"TN 2,{band}", lambda line: line == f"TN 2,{band}")
            self.gps_clock.break_stream()
            self.update(kiss=True, owner=owner, packet_band=band, packet_hz=packet_hz,
                        session_id=self.state["session_id"] + 1,
                        packet_rate=int(snapshot["rate"][-1]) if speed is None else speed,
                        status="KISS active; radio controls paused", error="")
            self.decoder = KissDecoder()
            self.pending_kiss = False
            self.line.clear()
        except Exception:
            self.restore()
            raise

    def restore(self):
        self.stop_beacon()
        snapshot = self.store.get("recovery", None)
        if not snapshot:
            return
        if snapshot.get("address") and snapshot["address"] != self.device_address:
            raise RuntimeError("Reconnect the original radio to restore")
        self.state["kiss"] = False
        self.connection.sendall(b"\xc0\xff\xc0")
        self.line.clear()
        self.discard = False
        time.sleep(.15)
        self.identify()
        if self.state["model"] != snapshot.get("model", "TH-D75"):
            raise RuntimeError("Waiting for the original Kenwood radio to restore")
        band = snapshot["band"]
        if self.query("AS") != snapshot["rate"]:
            self.cat(snapshot["rate"])
            if self.query("AS") != snapshot["rate"]:
                raise RuntimeError("Packet speed restoration did not confirm")
        if self.query(f"FO {band}") != snapshot["fo"]:
            # FO writes require VFO mode; a local monitor may have stayed in MR.
            self.cat(f"VM {band},0")
            if self.query(f"VM {band}") != f"VM {band},0":
                raise RuntimeError("VFO mode restoration did not confirm")
            self.cat(snapshot["fo"])
            if self.query(f"FO {band}") != snapshot["fo"]:
                raise RuntimeError("VFO restoration did not confirm")
        if self.query(f"VM {band}") != snapshot["vm"]:
            self.cat(snapshot["vm"])
            if self.query(f"VM {band}") != snapshot["vm"]:
                raise RuntimeError("Memory mode restoration did not confirm")
        self.cat(snapshot["tn"], lambda line: line == snapshot["tn"])
        if self.query("TN") != snapshot["tn"]:
            raise RuntimeError("APRS mode restoration did not confirm")
        self.store.set("recovery", None)
        self.pending_kiss = False
        self.remote_reply = None
        self.update(kiss=False, owner="", packet_band=None, packet_hz=0, packet_rate=None,
                    status="Radio settings restored", error="")

    def end_kiss(self):
        if self.native:
            self.packet_stop()
            return
        if self.state["owner"] == "monitor":
            self.update(kiss=False, owner="", status="Monitor stopped; end KISS on radio")
            self.line.clear()
            self.discard = False
            return
        if self.state["kiss"] or self.store.get("recovery", None):
            self.restore()

    def packet_event(self, kind, data):
        if kind == 0:
            if self.state["owner"] not in ("terminal", "winlink") or not self.state["kiss"]:
                raise RuntimeError("Packet session lost its radio link")
            self.connection.sendall(kiss_encode(data))
            decoded = decode_ax25(data)
            if decoded:
                self.record(decoded, "TX", raw=data)
        else:
            message = data.decode("utf-8", "replace").replace("\r", "\n")
            for line in message.splitlines():
                self.native_log.append(("RX " if kind == 1 else "") + line[:300])
            self.native_log = self.native_log[-200:]
            self.update(packet_log=list(self.native_log))

    def packet_open(self, owner, peer, frequency="", password="", extended=False, expected_hz=0):
        from decimal import Decimal
        from .protocol import valid_call
        from pathlib import Path
        source = self.store.get("callsign")
        if owner not in ("terminal", "winlink") or not valid_call(peer) or not valid_call(source) or peer == source:
            raise ValueError("Enter different valid local and destination callsigns")
        if self.native or self.state["owner"]:
            raise RuntimeError("End the current packet session first")
        hz = None
        def packet_frequency(value):
            return (144000000 <= value <= 148000000 or 222000000 <= value <= 225000000
                    or 430000000 <= value <= 450000000)
        if frequency:
            if not re.fullmatch(r"[0-9]{3}(?:\.[0-9]{1,6})?", frequency):
                raise ValueError("Enter a channel in MHz")
            hz = int(Decimal(frequency) * 1000000)
            if not packet_frequency(hz):
                raise ValueError("Choose an amateur packet frequency")
        if not self.connection:
            raise RuntimeError("Connect the radio first")
        self.native_log = []
        self.remote_reply = None
        self.update(packet_link="Calling", packet_peer=peer, packet_log=[])
        try:
            self.native = Engine(self.store.folder, Path.home() / "Documents" / "Rig-Companion-Transfers", self.packet_event)
            self.start_kiss(owner, hz)
            actual_hz = self.state["packet_hz"]
            if not packet_frequency(actual_hz) or (hz or expected_hz) != actual_hz:
                raise RuntimeError("Packet frequency changed. Review and confirm again.")
            self.native.require("open", source, peer, password, owner == "winlink", extended,
                                milliseconds(), md5, error="Could not open connected AX.25")
        except Exception:
            self.packet_finish()
            raise

    def packet_finish(self):
        if self.native:
            self.native.close()
            self.native = None
        self.native_closing = 0
        self.update(packet_link="Closed")
        if self.state["owner"] in ("terminal", "winlink") or self.store.get("recovery", None):
            self.restore()

    def packet_stop(self):
        if self.native and not self.native_closing:
            self.native.require("close", milliseconds())
            self.native_closing = time.monotonic() + 7
            self.update(packet_link="Closing")

    def packet_service(self):
        if not self.native:
            return
        self.native.require("tick", milliseconds())
        current = self.native.call("state")
        if current == 0 or self.native_closing and time.monotonic() >= self.native_closing:
            self.packet_finish()
        else:
            self.update(packet_link={1: "Calling", 2: "Connected", 3: "Closing"}[current])

    def packet_send(self, text):
        if not self.native or self.state["owner"] != "terminal":
            raise RuntimeError("Connect an AX.25 terminal first")
        data = (text + "\r").encode()
        self.native.require("send", data, len(data), error="Terminal is busy or disconnected")
        self.packet_event(2, ("TX " + text).encode())

    def packet_file(self, name, data):
        if not self.native or self.state["owner"] != "terminal":
            raise RuntimeError("Connect an AX.25 terminal first")
        self.native.require("yapp", name, data, len(data), milliseconds(), error="YAPP is busy or disconnected")
        self.packet_event(2, ("YAPP sending " + name).encode())

    def packet_receive_files(self, enabled):
        if not self.native or self.state["owner"] != "terminal":
            raise RuntimeError("Connect an AX.25 terminal first")
        self.native.call("accept_yapp", enabled)

    def manual_monitor(self):
        if not self.connection:
            raise RuntimeError("Connect the radio over Bluetooth first")
        if self.state["kiss"] or self.store.get("recovery", None):
            raise RuntimeError("End or restore the existing KISS session first")
        self.decoder = KissDecoder()
        self.update(kiss=True, owner="monitor", packet_band=None, packet_hz=0,
                    packet_rate=None, status="Listening to handset KISS")

    def send_message(self, recipient, text, session_id=None):
        if session_id is not None and session_id != self.state["session_id"]:
            raise RuntimeError("Packet session changed; confirm the message again")
        source = self.store.get("callsign")
        recipient = recipient.upper()
        path = self.store.get("path", "")
        message_frame(source, recipient, text, path)
        self.require_local_kiss()
        utc, _ = self.gps_clock.reading()
        message_id = self.store.stage_message(source.removesuffix("-0"), recipient.removesuffix("-0"), text, utc)
        frame = message_frame(source, recipient, text, path, f"{message_id % 100000:05d}")
        self.send_local_frame(frame)
        self.store.mark_message_sent(message_id)
        self.state["status"] = "Sent to TNC; awaiting APRS ACK"

    def require_local_kiss(self):
        if not self.state["kiss"] or self.state["owner"] != "local":
            raise RuntimeError("Start local KISS before sending")
        if not self.state["identity"]:
            raise RuntimeError("Radio is unavailable")

    def send_local_frame(self, frame):
        self.require_local_kiss()
        self.connection.sendall(kiss_encode(frame))
        self.record(decode_ax25(frame), "TX", raw=frame)

    def configure_beacon(self, position, interval, expected_session, session_id, gps=False):
        actual = (self.state["packet_band"], self.state["packet_hz"], self.state["packet_rate"])
        if (actual[0] not in (0, 1) or actual[1] <= 0 or actual[2] != 0
                or tuple(expected_session) != actual or session_id != self.state["session_id"]):
            raise RuntimeError("Start 1200 bps KISS on the intended APRS frequency")
        if interval not in (0, 300, 600, 1200):
            raise ValueError("Choose a valid beacon interval")
        if gps and (interval or self.gps_clock.position() != position):
            raise RuntimeError("GPS position expired; get a fresh fix")
        frame = position_frame(self.store.get("callsign"), position, self.store.get("path", ""))
        self.stop_beacon()
        self.send_local_frame(frame)
        if interval:
            self.beacon = (frame, interval, time.monotonic() + interval)
            self.update(beacon_armed=True)
        self.update(status="Position queued to radio")

    def stop_beacon(self):
        self.beacon = None
        self.update(beacon_armed=False)

    def service_beacon(self):
        if self.beacon:
            frame, interval, next_at = self.beacon
            if time.monotonic() >= next_at:
                try:
                    self.send_local_frame(frame)
                except Exception:
                    self.stop_beacon()
                    raise
                self.beacon = (frame, interval, time.monotonic() + interval)

    def _connect(self, device):
        snapshot = self.store.get("recovery", None)
        if snapshot:
            original = snapshot.get("address") or (self.store.get("device") or {}).get("address")
            if original != device["address"]:
                raise RuntimeError("Reconnect the original radio to restore")
        self.state["status"] = "Connecting to Kenwood radio"
        self.gps_clock.break_stream()
        self.connection = self.link.connect(device)
        self.device_address = device["address"]
        with self.lock:
            for band in self.state["bands"]:
                band.update(hz=0, mode=-1, power=-1, channel=-1, squelch=-1, meter=-1, busy=-1)
            self.state.update(connected=True, identity=False, model="", active=0, dual=-1,
                              volume=-1, kiss=False, owner="", native_tnc=None, native_rate=None)
        self.line.clear()
        self.discard = False
        if self.store.get("recovery", None):
            self.restore()
        try:
            self.identify()
        except (RuntimeError, TimeoutError):
            self.store.set("device", device)
            self.update(status="Paired; CAT model unconfirmed", error="")
            return
        self.store.set("device", device)
        self.update(status=self.state["model"] + " connected", error="")

    def _poll(self):
        self.next_poll = time.monotonic() + .08
        if not self.state["identity"]:
            self.next_poll = time.monotonic() + 8
            try:
                self.identify()
            except (RuntimeError, TimeoutError):
                pass
            return
        commands = ["ID", "BC", "FQ 0", "FQ 1", "MD 0", "MD 1", "PC 0", "PC 1",
                    "VM 0", "VM 1", "DL", "AG", "SQ 0", "SQ 1", "SM 0", "SM 1", "BY 0", "BY 1",
                    "TN", "AS"]
        if self.state["model"] != "TH-D75":
            commands = ["ID", "FQ 0", "FQ 1"]
        command = commands[self.poll_index % len(commands)]
        self.poll_index += 1
        try:
            self.query(command)
        except (RuntimeError, TimeoutError):
            if command not in ("TN", "AS"):
                raise

    def _run(self):
        next_connect = time.monotonic() + 1
        while not self.stop_event.is_set():
            try:
                action, args = self.actions.get(timeout=.03)
            except queue.Empty:
                action, args = None, ()
            try:
                if action == "connect":
                    if self.suspended:
                        raise RuntimeError(f"Stop the {self.suspended} first")
                    if self.native:
                        raise RuntimeError("Disconnect Terminal or Winlink before changing radios")
                    if self.connection:
                        self.end_kiss()
                        self.link.disconnect()
                        self.connection = None
                        self.update(connected=False, identity=False)
                    self._connect(*args)
                elif action:
                    self.state["error"] = ""
                    {"change": self.change, "tune": self.tune, "step": self.step,
                     "start_kiss": self.start_kiss, "end_kiss": self.end_kiss,
                         "send": self.send_message, "remote": self._remote,
                         "beacon": self.configure_beacon, "stop_beacon": self.stop_beacon,
                         "monitor": self.manual_monitor,
                         "packet_open": self.packet_open, "packet_stop": self.packet_stop,
                         "packet_send": self.packet_send, "packet_file": self.packet_file,
                         "packet_receive_files": self.packet_receive_files,
                         "remote_open": self._remote_open,
                         "suspend": self.suspend, "resume": self.resume}[action](*args)
                elif self.connection:
                    self._receive()
                    try:
                        self.packet_service()
                    except OSError:
                        raise
                    except Exception:
                        self.packet_finish()
                        raise
                    self.service_beacon()
                    if not self.state["kiss"] and time.monotonic() >= self.next_poll:
                        if not self.store.get("recovery", None):
                            self._poll()
                elif self.link and not self.suspended and time.monotonic() >= next_connect:
                    next_connect = time.monotonic() + 8
                    device = self.store.get("device", None)
                    if device:
                        devices = self.link.devices()
                        match = next((d for d in devices if d["address"] == device["address"]), None)
                        if match:
                            self._connect(match)
            except TimeoutError as error:
                self.update(error=str(error), status=str(error))
            except OSError as error:
                self._lost(str(error))
            except Exception as error:
                self.update(error=str(error), status=str(error))
        try:
            if self.connection:
                if self.native:
                    self.packet_stop()
                    deadline = time.monotonic() + 2
                    while self.native and time.monotonic() < deadline:
                        self._receive()
                        self.packet_service()
                    if self.native:
                        self.packet_finish()
                else:
                    self.end_kiss()
        except Exception as error:
            self.update(error=f"Restore pending on reconnect: {error}")
        if self.link:
            self.link.disconnect()
        self.closed = True

    def suspend(self, reason, dv_band=None):
        """Release the Bluetooth link so the D-STAR gateway can use the radio.

        dv_band (0 = A): first switch that band to DV mode over CAT and make it active.
        """
        if self.native:
            raise RuntimeError("Disconnect Terminal or Winlink before D-STAR")
        if self.state["kiss"]:
            self.end_kiss()
        note = ""
        if dv_band is not None and self.connection and self.state.get("identity"):
            try:
                if self.query(f"MD {dv_band}") != f"MD {dv_band},1":
                    self.cat(f"MD {dv_band},1")
                    if self.query(f"MD {dv_band}") != f"MD {dv_band},1":
                        raise RuntimeError("DV mode did not confirm")
                if self.query("BC") != f"BC {dv_band}":
                    self.cat(f"BC {dv_band}")
                note = "Band A set to DV. "
            except (RuntimeError, TimeoutError) as error:
                note = f"Could not set DV mode ({error}). "
        self.stop_beacon()
        self.remote_reply = None
        if self.connection and self.link:
            self.link.disconnect()
        self.connection = None
        self.suspended = reason
        self.gps_clock.break_stream()
        self.update(connected=False, identity=False, kiss=False, owner="",
                    status=f"{note}{reason} is using the radio", error="", dv_note=note.strip())

    def resume(self):
        self.suspended = ""
        self.update(status="Reconnecting to the radio", error="")

    def _lost(self, reason):
        if self.native:
            self.native.close()
            self.native = None
            self.native_closing = 0
            self.update(packet_link="Link lost")
        self.gps_clock.break_stream()
        self.stop_beacon()
        self.remote_reply = None
        if self.link:
            self.link.disconnect()
        self.connection = None
        self.device_address = None
        self.update(connected=False, identity=False, kiss=False, owner="", packet_band=None,
                    packet_hz=0, packet_rate=None, native_tnc=None, native_rate=None,
                    status="Radio disconnected", error=reason)

    def _remote(self, owner, command, payload, reply):
        if self.state["owner"] and self.state["owner"] != owner:
            raise RuntimeError("Another KISS session owns the radio")
        self.remote_reply = reply
        if command == 6 and payload:
            op = payload[0]
            if op in (0x7b, 0x7e, 0x28):
                values = {0x7b: b"\x01\x00", 0x7e: b"\x08\x10", 0x28: b"0.1.0"}
                reply(kiss_encode(bytes([op]) + values[op], 6))
            elif op == 0xf4 and len(payload) == 2 and payload[1] <= 1:
                self.store.set("remote_rate", payload[1])
            elif op == 0xea and len(payload) == 5:
                frequency = int.from_bytes(payload[1:], "big")
                if not (136000000 <= frequency <= 174000000 or 216000000 <= frequency <= 260000000 or 400000000 <= frequency <= 470000000):
                    raise ValueError("Unsupported packet station frequency")
                self.start_kiss(owner, frequency, self.store.get("remote_rate", 0))
                self.remote_reply = reply
            elif op == 0xeb:
                self.end_kiss()
            return
        if command == 255:
            self.end_kiss()
            self.remote_reply = None
            return
        if command not in range(6):
            return
        if not self.state["kiss"]:
            self.start_kiss(owner)
        if command == 0 and payload:
            if decode_ax25(payload) is None:
                raise ValueError("Invalid AX.25 transmit frame")
            self.connection.sendall(kiss_encode(payload))
            self.record(decode_ax25(payload), "TX", raw=payload)
        elif command in range(1, 6) and len(payload) == 1:
            self.connection.sendall(kiss_encode(payload, command))

    def _remote_open(self, owner, reply):
        self.start_kiss(owner)
        self.remote_reply = reply

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=16)
        if not self.closed:
            raise RuntimeError("Waiting for radio restoration. Exit again after it completes.")

