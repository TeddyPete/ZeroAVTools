"""Benshi radio driver (BTECH UV-Pro, Radioddity GA-5WB, Vero VR-N76 and relatives).

Same worker interface as the Kenwood `Radio`, so every screen and key works the
same way. The radio is driven over its Bluetooth Classic GAIA serial service.

Protocol notes come from the open-source benlink (Apache-2.0, Kyle Husmann) and
HTCommander (Apache-2.0, Ylian Saint-Hilaire) projects, and match
native/benshi_proto.h. No source files are copied.

GAIA frame:  FF 01 flags n  [group_hi group_lo cmd_hi|reply<<7 cmd_lo  payload(n bytes)]  [checksum if flags&1]
"""
from __future__ import annotations

import socket
import time

from rigzero.models import GAIA_UUID
from rigzero.protocol import MODES, decode_ax25
from rigzero.radio import Radio

GROUP_BASIC = 2

GET_DEV_INFO = 4
REGISTER_NOTIFICATION = 6
EVENT_NOTIFICATION = 9
READ_SETTINGS = 10
WRITE_SETTINGS = 11
READ_RF_CH = 13
GET_HT_STATUS = 20
GET_VOLUME = 22
SET_VOLUME = 23
HT_SEND_DATA = 31

EV_HT_STATUS_CHANGED = 1
EV_DATA_RXD = 2
EV_HT_CH_CHANGED = 5
EV_HT_SETTINGS_CHANGED = 6

STATUS_TEXT = {1: "not supported", 2: "not authenticated", 3: "busy", 4: "authenticating",
               5: "invalid value", 6: "wrong state", 7: "in progress"}

FRAGMENT_DATA = 50

# Settings bitfield, most significant bit first (benlink Settings).
SETTINGS_FIELDS = (
    ("channel_a_lower", 4), ("channel_b_lower", 4), ("scan", 1), ("aghfp_call_mode", 1),
    ("double_channel", 2), ("squelch_level", 4), ("tail_elim", 1), ("auto_relay_en", 1),
    ("auto_power_on", 1), ("keep_aghfp_link", 1), ("mic_gain", 3), ("tx_hold_time", 4),
    ("tx_time_limit", 5), ("local_speaker", 2), ("bt_mic_gain", 3), ("adaptive_response", 1),
    ("dis_tone", 1), ("power_saving_mode", 1), ("auto_power_off", 3), ("auto_share_loc_ch", 5),
    ("hm_speaker", 2), ("positioning_system", 4), ("time_offset", 6), ("use_freq_range_2", 1),
    ("ptt_lock", 1), ("leading_sync_bit_en", 1), ("pairing_at_power_on", 1), ("screen_timeout", 5),
    ("kiss_upload_tx_msg", 1), ("kiss_en", 1), ("imperial_unit", 1),
    ("channel_a_upper", 4), ("channel_b_upper", 4),
)


def _offsets(fields):
    offsets, position = {}, 0
    for name, width in fields:
        offsets[name] = (position, width)
        position += width
    return offsets


SETTINGS = _offsets(SETTINGS_FIELDS)


def get_bits(data, offset, width):
    total = len(data) * 8
    if offset + width > total:
        raise ValueError("Short radio record")
    return (int.from_bytes(data, "big") >> (total - offset - width)) & ((1 << width) - 1)


def set_bits(data, offset, width, value):
    total = len(data) * 8
    if offset + width > total or not 0 <= value < (1 << width):
        raise ValueError("Value does not fit the radio record")
    shift = total - offset - width
    number = int.from_bytes(data, "big")
    number = (number & ~(((1 << width) - 1) << shift)) | (value << shift)
    return bytes(number.to_bytes(len(data), "big"))


def setting(data, name):
    return get_bits(data, *SETTINGS[name])


def with_setting(data, name, value):
    return set_bits(data, *SETTINGS[name], value)


def settings_channel(data, band):
    side = "a" if band == 0 else "b"
    return setting(data, f"channel_{side}_lower") | (setting(data, f"channel_{side}_upper") << 4)


def with_settings_channel(data, band, channel):
    side = "a" if band == 0 else "b"
    data = with_setting(data, f"channel_{side}_lower", channel & 15)
    return with_setting(data, f"channel_{side}_upper", (channel >> 4) & 15)


def parse_channel(data):
    """RfCh record (25 bytes, 27 with DMR) -> dict, or None when too short."""
    if len(data) < 25:
        return None
    name = bytes(data[15:25]).split(b"\0", 1)[0].decode("ascii", "replace").strip()
    rx_mod = get_bits(data, 40, 2)
    return {"number": data[0], "tx_hz": get_bits(data, 10, 30), "rx_hz": get_bits(data, 42, 30),
            "mode": {0: "FM", 1: "AM", 2: "DMR"}.get(rx_mod, "FM"),
            "wide": bool(get_bits(data, 107, 1)), "high": bool(get_bits(data, 105, 1)),
            "medium": bool(get_bits(data, 110, 1)), "tx_off": bool(get_bits(data, 111, 1)),
            "name": name}


def parse_status(data):
    """HT status (2 or 4 bytes) -> dict."""
    if len(data) < 2:
        return None
    status = {"power": bool(data[0] & 0x80), "tx": bool(data[0] & 0x40), "squelch_open": bool(data[0] & 0x20),
              "rx": bool(data[0] & 0x10), "dual": (data[0] >> 2) & 3, "scan": bool(data[0] & 2),
              "channel": data[1] >> 4, "rssi": 0}
    if len(data) >= 4:
        status["rssi"] = data[2] >> 4
        status["channel"] |= ((data[3] >> 2) & 15) << 4
    return status


def build_message(command, payload=b""):
    return bytes((0, GROUP_BASIC, (command >> 8) & 0x7F, command & 0xFF)) + (bytes(payload) or b"\0")


def gaia_frame(message):
    payload = message[4:]
    if len(payload) > 255:
        raise ValueError("Radio command too long")
    return bytes((0xFF, 0x01, 0x00, len(payload))) + message


def split_frames(buffer):
    """Pull whole GAIA frames off the front of `buffer` (a bytearray). Returns messages."""
    messages = []
    while True:
        start = buffer.find(b"\xff\x01")
        if start < 0:
            del buffer[:max(0, len(buffer) - 1)]
            return messages
        del buffer[:start]
        if len(buffer) < 4:
            return messages
        size = 4 + 4 + buffer[3] + (1 if buffer[2] & 1 else 0)
        if len(buffer) < size:
            return messages
        messages.append(bytes(buffer[4:8 + buffer[3]]))
        del buffer[:size]


def fragments(frame, channel=None):
    """Split one AX.25 frame into HT_SEND_DATA payloads."""
    pieces = []
    for index, start in enumerate(range(0, len(frame), FRAGMENT_DATA)):
        data = frame[start:start + FRAGMENT_DATA]
        header = index & 0x3F
        if start + len(data) >= len(frame):
            header |= 0x80
        if channel is not None:
            header |= 0x40
        pieces.append(bytes([header]) + bytes(data) + (bytes([channel]) if channel is not None else b""))
    return pieces


class Reassembler:
    def __init__(self):
        self.buffer, self.last, self.active = b"", 0, False

    def add(self, fragment):
        if not fragment:
            return None
        header = fragment[0]
        data = fragment[1:-1] if header & 0x40 else fragment[1:]
        index = header & 0x3F
        if index == 0:
            self.buffer, self.last, self.active = data, 0, True
        elif self.active and index == self.last + 1 and len(self.buffer) + len(data) <= 512:
            self.buffer += data
            self.last = index
        else:
            self.active = False
            return None
        if header & 0x80:
            self.active = False
            return self.buffer
        return None


class Driver(Radio):
    """Benshi family driver behind the same worker interface as the Kenwood `Radio`."""

    family = "benshi"

    def __init__(self, store, link=None, model="UV-Pro", package=None):
        self.model_key = model
        self.buffer = bytearray()
        self.replies = {}
        self.settings = b""
        self.channels = {}
        self.channel_count = 32
        self.reassembler = Reassembler()
        super().__init__(store, link)
        self.package = package or {}
        self.update(model="", status="Find your " + model + " under Link")

    # ---------------------------------------------------------------- link
    def _connect(self, device):
        self.update(status="Connecting to " + self.model_key)
        self.connection = self.open_link(device, GAIA_UUID)
        self.device_address = device["address"]
        self.buffer.clear()
        self.replies.clear()
        self.channels.clear()
        self.settings = b""
        with self.lock:
            for band in self.state["bands"]:
                band.update(hz=0, mode=-1, power=-1, channel=-1, squelch=-1, meter=-1, busy=-1, number=-1, name="")
            self.state.update(connected=True, identity=False, model="", active=0, dual=-1,
                              volume=-1, kiss=False, owner="")
        self.store.set("recovery", None)
        try:
            self.identify()
        except (RuntimeError, TimeoutError):
            self.store.set("device", device)
            self.update(status="Paired; radio did not answer yet", error="")
            return
        self.store.set("device", device)
        for event in (EV_HT_STATUS_CHANGED, EV_DATA_RXD, EV_HT_CH_CHANGED, EV_HT_SETTINGS_CHANGED):
            self.send(REGISTER_NOTIFICATION, bytes([event]))
        self.read_settings()
        try:
            self.request(GET_VOLUME)
        except (RuntimeError, TimeoutError):
            pass
        self.send(GET_HT_STATUS)
        self.update(status=self.state["model"] + " connected", error="")

    def send(self, command, payload=b""):
        if not self.connection:
            raise RuntimeError("Connect the radio first")
        self.replies.pop(command, None)
        self.connection.sendall(gaia_frame(build_message(command, payload)))

    def request(self, command, payload=b"", timeout=2.5):
        self.send(command, payload)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self._receive()
            if command in self.replies:
                reply = self.replies.pop(command)
                if not reply or reply[0] != 0:
                    code = reply[0] if reply else -1
                    raise RuntimeError("Radio said " + STATUS_TEXT.get(code, f"error {code}"))
                return reply[1:]
        raise TimeoutError("Radio did not answer")

    def _receive(self):
        try:
            data = self.connection.recv(4096)
        except (socket.timeout, BlockingIOError):
            return
        if not data:
            raise ConnectionError("Bluetooth radio disconnected")
        self.buffer.extend(data)
        for message in split_frames(self.buffer):
            self._message(message)

    def _message(self, message):
        if len(message) < 4 or ((message[0] << 8) | message[1]) != GROUP_BASIC:
            return
        command = ((message[2] & 0x7F) << 8) | message[3]
        payload = message[4:]
        if self.state["identity"]:
            self.identified_at = time.monotonic()
        if message[2] & 0x80:
            self.replies[command] = payload
            if not payload or payload[0] != 0:
                return
            body = payload[1:]
            if command == READ_SETTINGS:
                self._settings(body)
            elif command == READ_RF_CH:
                self._channel(body)
            elif command == GET_VOLUME and body:
                self.state["volume"] = body[0]
            elif command == GET_HT_STATUS:
                self._status(body)
            elif command == GET_DEV_INFO and len(body) >= 9:
                self.channel_count = body[8] or 32
        elif command == EVENT_NOTIFICATION and payload:
            event, body = payload[0], payload[1:]
            if event == EV_HT_STATUS_CHANGED:
                self._status(body)
            elif event == EV_DATA_RXD:
                frame = self.reassembler.add(body)
                if frame:
                    self._rx_frame(frame)
            elif event == EV_HT_CH_CHANGED:
                self._channel(body)
            elif event == EV_HT_SETTINGS_CHANGED:
                self._settings(body)

    # ---------------------------------------------------------------- state
    def _settings(self, data):
        if len(data) < 9:
            return
        self.settings = bytes(data)
        self.state["dual"] = 1 if setting(data, "double_channel") else 0
        squelch = setting(data, "squelch_level")
        for band in (0, 1):
            number = settings_channel(data, band)
            entry = self.state["bands"][band]
            entry.update(number=number, squelch=squelch, channel=1)
            if number in self.channels:
                self._show_channel(band, self.channels[number])
            elif self.connection:
                self.send(READ_RF_CH, bytes([number]))

    def _channel(self, data):
        channel = parse_channel(data)
        if not channel:
            return
        self.channels[channel["number"]] = channel
        for band, entry in enumerate(self.state["bands"]):
            if entry.get("number") == channel["number"]:
                self._show_channel(band, channel)

    def _show_channel(self, band, channel):
        entry = self.state["bands"][band]
        entry.update(hz=channel["rx_hz"], mode=MODES.index("AM" if channel["mode"] == "AM" else
                                                           "FM" if channel["wide"] else "NFM"),
                     power=0 if channel["high"] else 1 if channel["medium"] else 2,
                     name=channel["name"])

    def _status(self, data):
        status = parse_status(data)
        if not status:
            return
        for entry in self.state["bands"]:
            on_air = entry.get("number") == status["channel"]
            entry["busy"] = 1 if on_air and (status["squelch_open"] or status["rx"]) else 0
            entry["meter"] = min(5, (status["rssi"] + 2) // 3) if on_air else 0

    # ---------------------------------------------------------------- worker hooks
    def identify(self):
        body = self.request(GET_DEV_INFO, b"\x03", timeout=3)
        if len(body) >= 9:
            self.channel_count = body[8] or 32
        with self.lock:
            self.state.update(identity=True, model=self.model_key)
        self.identified_at = time.monotonic()
        return self.model_key

    def read_settings(self):
        self.request(READ_SETTINGS)         # the reply handler stores and shows it
        if not self.settings:
            raise RuntimeError("Radio settings unreadable")

    def _poll(self):
        self.next_poll = time.monotonic() + 2
        if not self.state["identity"]:
            self.next_poll = time.monotonic() + 8
            try:
                self.identify()
            except (RuntimeError, TimeoutError):
                pass
            return
        self.poll_index += 1
        self.send(READ_SETTINGS if self.poll_index % 5 == 0 else GET_HT_STATUS)

    def require_control(self, packet=False):
        if not self.state["connected"] or not self.state["identity"]:
            raise RuntimeError("Radio is not connected yet")

    def cat(self, command, expected=None, timeout=1.8):
        raise RuntimeError("Not available on " + self.model_key)

    # ---------------------------------------------------------------- controls
    def change(self, field, amount=1):
        self.require_control()
        if field == "band":
            self.state["active"] = 1 - self.state["active"]
            return
        if field == "channel":
            return self.step(1 if amount >= 0 else -1)
        if field == "volume":
            current = max(0, self.state["volume"])
            value = max(0, min(15, current + (1 if amount > 0 else -1 if amount < 0 else 0)))
            self.request(SET_VOLUME, bytes([value]))
            self.state["volume"] = self.request(GET_VOLUME)[0]
            return
        if not self.settings:
            self.read_settings()
        if field == "squelch":
            value = (setting(self.settings, "squelch_level") + amount) % 10
            self.write_settings(with_setting(self.settings, "squelch_level", value), "squelch_level")
            return
        if field == "dual":
            value = 0 if setting(self.settings, "double_channel") else 1
            self.write_settings(with_setting(self.settings, "double_channel", value), "double_channel")
            return
        raise RuntimeError("Set " + field + " on the handset for the " + self.model_key)

    def write_settings(self, data, *names):
        """Write the whole settings record, then confirm the changed fields read back."""
        self.request(WRITE_SETTINGS, data)
        self.read_settings()
        if any(setting(self.settings, name) != setting(data, name) for name in names):
            raise RuntimeError("Radio did not confirm this setting")

    def read_channel(self, number):
        for _ in range(3):
            if number in self.channels:
                break
            self.request(READ_RF_CH, bytes([number]))   # the reply handler caches it
        return self.channels.get(number)

    def step(self, direction):
        self.require_control()
        if not self.settings:
            self.read_settings()
        band = self.state["active"]
        current = settings_channel(self.settings, band)
        count = max(1, min(256, self.channel_count))
        for i in range(1, count + 1):
            number = (current + direction * i) % count
            try:
                channel = self.read_channel(number)
            except RuntimeError:
                continue
            if channel and channel["rx_hz"]:
                side = "a" if band == 0 else "b"
                self.write_settings(with_settings_channel(self.settings, band, number),
                                    f"channel_{side}_lower", f"channel_{side}_upper")
                return
        raise RuntimeError("No programmed channel found")

    def tune(self, mhz):
        raise RuntimeError(self.model_key + " tunes by channel. Use Up/Down.")

    def suspend(self, reason, dv_band=None):
        raise RuntimeError("D-STAR needs a TH-D75")

    # ---------------------------------------------------------------- packet (built-in TNC)
    def start_kiss(self, owner="local", frequency=None, speed=None):
        if self.state["kiss"]:
            if self.state["owner"] != owner:
                raise RuntimeError("Another session owns the radio TNC")
            return
        self.require_control(packet=True)
        band = self.state["active"]
        hz = self.state["bands"][band]["hz"]
        if frequency and frequency != hz:
            raise RuntimeError(f"Set band {'AB'[band]} to {frequency / 1e6:.3f} MHz on the radio first")
        if speed == 1:
            raise RuntimeError(self.model_key + " packet runs at 1200 bps only")
        self.update(kiss=True, owner=owner, packet_band=band, packet_hz=hz, packet_rate=0,
                    session_id=self.state["session_id"] + 1, status="Radio TNC ready", error="")

    def restore(self):
        self.stop_beacon()
        self.store.set("recovery", None)
        self.pending_kiss = False
        self.remote_reply = None
        self.update(kiss=False, owner="", packet_band=None, packet_hz=0, packet_rate=None,
                    status=(self.state["model"] or self.model_key) + " connected", error="")

    def manual_monitor(self):
        if not self.connection:
            raise RuntimeError("Connect the radio over Bluetooth first")
        self.start_kiss("monitor")

    def require_local_kiss(self):
        if not self.state["identity"]:
            raise RuntimeError("Radio is unavailable")
        if not self.state["kiss"]:
            self.start_kiss("local")
        elif self.state["owner"] not in ("local", "monitor"):
            raise RuntimeError("Another session is using the radio TNC")

    def send_ax25(self, frame):
        if decode_ax25(frame) is None:
            raise ValueError("Invalid AX.25 frame")
        for piece in fragments(frame):
            self.request(HT_SEND_DATA, piece)

    def send_kiss_param(self, payload, command):
        pass        # TX delay / persistence belong to the radio's own TNC
