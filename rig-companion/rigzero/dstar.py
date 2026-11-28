"""D-STAR building blocks for the TH-D75 Reflector Terminal mode.

The TH-D75 speaks the MMDVM modem serial protocol in Reflector Terminal mode
(Menu 650) over Bluetooth or USB (Menu 985). The radio encodes and decodes
AMBE itself, so this module only moves D-STAR headers, 12-byte voice/data
frames and slow data between the radio and a reflector network.

Reflector protocols (DExtra for XRF/XLX, DCS, DPlus for REF) are written from
the public protocol descriptions used by MMDVMHost, ircDDBGateway and
DroidStar. Everything here is pure Python with no I/O so it can be tested.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- MMDVM serial
FRAME_START = 0xE0
GET_VERSION, GET_STATUS, SET_CONFIG, SET_MODE, SET_FREQ = 0x00, 0x01, 0x02, 0x03, 0x04
DSTAR_HEADER, DSTAR_DATA, DSTAR_LOST, DSTAR_EOT = 0x10, 0x11, 0x12, 0x13
ACK, NAK = 0x70, 0x7F
MODE_IDLE, MODE_DSTAR = 0, 1

SYNC = b"\x55\x2d\x16"                      # slow data of frame 0 in each superframe
FILLER = b"\x16\x29\xf5"                    # scrambled 0x66 0x66 0x66 (no data)
NULL_AMBE = bytes.fromhex("9e8d3288261a3f61e8")   # AMBE silence
END_FRAME = NULL_AMBE + b"\x55\x55\x55"       # DExtra end-of-stream payload
SCRAMBLE = (0x70, 0x4F, 0x93)
FRAMES_PER_SUPERFRAME = 21


def mmdvm_frame(command: int, payload: bytes = b"") -> bytes:
    return bytes([FRAME_START, len(payload) + 3, command]) + payload


class MmdvmDecoder:
    """Split a byte stream from the radio into (command, payload) frames."""

    def __init__(self):
        self.buffer = bytearray()
        self.invalid = 0

    def feed(self, data: bytes):
        self.buffer.extend(data)
        frames = []
        while self.buffer:
            if self.buffer[0] != FRAME_START:
                start = self.buffer.find(bytes([FRAME_START]))
                self.invalid += 1
                if start < 0:
                    self.buffer.clear()
                    break
                del self.buffer[:start]
            if len(self.buffer) < 3:
                break
            length = self.buffer[1]
            if length < 3:
                del self.buffer[:1]
                self.invalid += 1
                continue
            if len(self.buffer) < length:
                break
            frames.append((self.buffer[2], bytes(self.buffer[3:length])))
            del self.buffer[:length]
        return frames


def mmdvm_config(protocol: int = 1) -> bytes:
    """SET_CONFIG with only D-STAR enabled, simplex, idle start."""
    if protocol == 2:
        payload = bytes([0x80, 0x01, 0x00, 10, MODE_IDLE, 128, 128, 128, 0, 128, 128, 128, 128, 128,
                         128, 128, 128, 128, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 128, 30, 2, 128, 0, 0, 0, 0, 0])
        return mmdvm_frame(SET_CONFIG, payload)
    payload = bytes([0x80, 0x01, 10, MODE_IDLE, 128, 128, 0, 0, 128, 128, 128, 128, 128,
                     128, 128, 128, 0, 128, 128, 0, 0, 128, 0])
    return mmdvm_frame(SET_CONFIG, payload)


def version_text(payload: bytes) -> tuple[int, str]:
    if not payload:
        return 0, ""
    protocol = payload[0]
    text = payload[(23 if protocol == 2 else 1):]
    return protocol, text.decode("ascii", "replace").strip("\0 ").strip()


# --------------------------------------------------------------------------- header
def crc_ccitt(data: bytes) -> bytes:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    crc = ~crc & 0xFFFF
    return bytes([crc & 0xFF, crc >> 8])


def pad(call: str, size: int = 8) -> str:
    return (call or "").upper()[:size].ljust(size)


def module_call(call: str, module: str) -> str:
    """'N0TNT' + 'C' -> 'N0TNT  C' (callsign in 7, module in column 8)."""
    return (call or "").upper()[:7].ljust(7) + (module or " ").upper()[:1]


@dataclass
class Header:
    rpt2: str = "DIRECT  "
    rpt1: str = "DIRECT  "
    ur: str = "CQCQCQ  "
    my: str = ""
    suffix: str = "    "
    flags: bytes = b"\x00\x00\x00"

    def pack(self) -> bytes:
        body = (bytes(self.flags[:3]).ljust(3, b"\0") + pad(self.rpt2).encode("ascii", "replace")
                + pad(self.rpt1).encode("ascii", "replace") + pad(self.ur).encode("ascii", "replace")
                + pad(self.my).encode("ascii", "replace") + pad(self.suffix, 4).encode("ascii", "replace"))
        return body + crc_ccitt(body)

    @classmethod
    def parse(cls, data: bytes) -> "Header":
        if len(data) < 39:
            raise ValueError("Short D-STAR header")
        text = data[3:39].decode("ascii", "replace")
        return cls(rpt2=text[0:8], rpt1=text[8:16], ur=text[16:24], my=text[24:32],
                   suffix=text[32:36], flags=bytes(data[:3]))

    @property
    def caller(self) -> str:
        return self.my.strip()

    def label(self) -> str:
        suffix = self.suffix.strip()
        return self.caller + (" /" + suffix if suffix else "")


def is_sync(slow: bytes) -> bool:
    return bytes(slow[:3]) == SYNC


# --------------------------------------------------------------------------- slow data
class SlowData:
    """Decode the 20-character message and D-PRS GPS text carried in slow data."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.position = None
        self.half = None
        self.message_parts = {}
        self.message = ""
        self.gps = bytearray()
        self.lines = []
        self.header = bytearray()

    def feed(self, seq: int, slow: bytes):
        """Feed one frame's 3 slow-data bytes.

        Block alignment follows the sync pattern in the data itself: some
        reflectors number frames with an offset from the sender's superframe.
        seq is only used until the first sync frame is seen.
        """
        if is_sync(slow):
            self.position, self.half = 0, None
            return
        self.position = seq if self.position is None else self.position + 1
        if self.position % 21 == 0:
            self.half = None
            return
        plain = bytes(b ^ k for b, k in zip(slow[:3], SCRAMBLE))
        if (self.position % 21) % 2 == 1:
            self.half = plain
            return
        if self.half is None:
            return
        block, self.half = self.half + plain, None
        kind, size = block[0] & 0xF0, block[0] & 0x0F
        if kind == 0x40 and size <= 3:
            self.message_parts[size] = block[1:6]
            if len(self.message_parts) == 4:
                text = b"".join(self.message_parts[i] for i in range(4))
                self.message = text.decode("ascii", "replace").replace("\0", " ").rstrip()
        elif kind == 0x30 and 0 < size <= 5:
            self.gps.extend(block[1:1 + size])
            self._split_gps()
        elif kind == 0x50 and 0 < size <= 5:
            self.header.extend(block[1:1 + size])
            del self.header[:-41]

    def _split_gps(self):
        while True:
            match = re.search(rb"[\r\n]", self.gps)
            if not match:
                if len(self.gps) > 512:
                    self.gps.clear()
                return
            line = self.gps[:match.start()].decode("ascii", "replace").strip()
            del self.gps[:match.end()]
            if line:
                self.lines.append(line)
                del self.lines[:-16]

    def take_lines(self):
        lines, self.lines = self.lines, []
        return lines


def encode_message(text: str) -> list[bytes]:
    """Slow data (3 bytes per frame) for frames 1..20 carrying a 20-char message."""
    raw = text.encode("ascii", "replace")[:20].ljust(20)
    frames = []
    for index in range(4):
        block = bytes([0x40 | index]) + raw[index * 5:index * 5 + 5]
        for part in (block[:3], block[3:]):
            frames.append(bytes(b ^ k for b, k in zip(part, SCRAMBLE)))
    while len(frames) < 20:
        frames.append(FILLER)
    return frames


def slow_data_for(seq: int, message_frames: list[bytes] | None = None) -> bytes:
    if seq == 0:
        return SYNC
    if message_frames and seq - 1 < len(message_frames):
        return message_frames[seq - 1]
    return FILLER


# --------------------------------------------------------------------------- D-PRS
def _nmea_ok(line: str) -> bool:
    if "*" not in line:
        return True
    body, _, check = line[1:].partition("*")
    value = 0
    for char in body:
        value ^= ord(char)
    try:
        return int(check[:2], 16) == value
    except ValueError:
        return False


def _nmea_degrees(value: str, hemisphere: str, width: int) -> float | None:
    if not value or len(value) < width + 2:
        return None
    try:
        degrees = int(value[:width]) + float(value[width:]) / 60
    except ValueError:
        return None
    return -degrees if hemisphere in ("S", "W") else degrees


def aprs_position(lat: float, lon: float, symbol: str = "/[", comment: str = "") -> str:
    ns, ew = "N" if lat >= 0 else "S", "E" if lon >= 0 else "W"
    lat, lon = abs(lat), abs(lon)
    la = f"{int(lat):02d}{(lat - int(lat)) * 60:05.2f}{ns}"
    lo = f"{int(lon):03d}{(lon - int(lon)) * 60:05.2f}{ew}"
    return "!" + la + symbol[0] + lo + symbol[1] + comment


def dprs_to_tnc2(line: str, mycall: str = "") -> str | None:
    """Turn one D-PRS slow-data line into a TNC2 APRS line, or None."""
    line = line.strip()
    if line.startswith("$$CRC"):
        # $$CRCxxxx,CALL-7>API510,DSTAR*:!lat/lon...
        _, _, tnc2 = line.partition(",")
        return tnc2 if ">" in tnc2 and ":" in tnc2 else None
    if not mycall or not line.startswith("$GP") and not line.startswith("$GN"):
        return None
    if not _nmea_ok(line):
        return None
    fields = line.split("*")[0].split(",")
    kind = fields[0][3:]
    lat = lon = None
    if kind == "RMC" and len(fields) >= 7 and fields[2] == "A":
        lat, lon = _nmea_degrees(fields[3], fields[4], 2), _nmea_degrees(fields[5], fields[6], 3)
    elif kind == "GGA" and len(fields) >= 7 and fields[6] not in ("", "0"):
        lat, lon = _nmea_degrees(fields[2], fields[3], 2), _nmea_degrees(fields[4], fields[5], 3)
    if lat is None or lon is None:
        return None
    return f"{mycall.strip()}>API75,DSTAR*:" + aprs_position(lat, lon, "/[", "D-STAR GPS")


# --------------------------------------------------------------------------- reflectors
PROTOCOLS = {"REF": ("DPlus", 20001), "XRF": ("DExtra", 30001), "XLX": ("DExtra", 30001),
             "DCS": ("DCS", 30051)}


def reflector_kind(name: str) -> str:
    return (name or "")[:3].upper()


def valid_reflector(name: str) -> bool:
    return bool(re.fullmatch(r"(REF|XRF|XLX|DCS)[0-9A-Z]{3}", (name or "").upper()))


def parse_hosts(text: str, prefix: str = "") -> dict[str, tuple[str, int]]:
    """Pi-Star style host files: 'XRF757<tab>1.2.3.4[<tab>port]' or XLX 'nnn;ip;port'."""
    hosts = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ";" in line:
            parts = line.split(";")
            if len(parts) >= 2 and parts[0].strip():
                name = "XLX" + parts[0].strip().upper()[:3]
                if valid_reflector(name):
                    hosts[name] = (parts[1].strip(), PROTOCOLS["XLX"][1])
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0].upper()
        if prefix and not name.startswith(prefix):
            continue
        if not valid_reflector(name):
            continue
        port = PROTOCOLS[reflector_kind(name)][1]
        if len(parts) >= 3 and parts[2].isdigit():
            port = int(parts[2])
        hosts[name] = (parts[1], port)
    return hosts


@dataclass
class NetFrame:
    """A reflector packet reduced to what the gateway needs."""
    kind: str                       # "header", "data", "keepalive", "ack", "nak", "status", "other"
    stream: int = 0
    seq: int = 0
    end: bool = False
    header: Header | None = None
    frame: bytes = b""              # 12 bytes: AMBE + slow data
    text: str = ""


@dataclass
class Reflector:
    """Packet builder/parser for one linked reflector (one module)."""
    name: str
    module: str
    callsign: str
    local_module: str = ""
    protocol: str = field(init=False)

    def __post_init__(self):
        self.name = self.name.upper()
        self.module = self.module.upper()[:1]
        # Like DroidStar, link as the same module letter unless told otherwise.
        self.local_module = (self.local_module or self.module).upper()[:1]
        self.callsign = self.callsign.upper().split("-")[0][:8]
        self.protocol = PROTOCOLS[reflector_kind(self.name)][0]

    # ---- link management
    def connect_packets(self) -> list[bytes]:
        call8 = pad(self.callsign).encode()
        if self.protocol == "DExtra":
            return [call8 + self.local_module.encode() + self.module.encode() + b"\x0b"]
        if self.protocol == "DCS":
            packet = bytearray(519)
            packet[0:8] = call8
            packet[8] = ord(self.local_module)
            packet[9] = ord(self.module)
            packet[10] = 0x0B
            html = b"<table border=\"0\" width=\"95%\"><tr><td width=\"4%\">Rig Companion</td></tr></table>"
            packet[19:19 + len(html)] = html
            return [bytes(packet)]
        return [b"\x05\x00\x18\x00\x01"]

    def login_packet(self) -> bytes:
        """DPlus second step after the server echoes the connect packet."""
        return (b"\x1c\xc0\x04\x00" + self.callsign[:8].encode().ljust(16, b"\0")
                + b"DV019999".ljust(8, b"\0"))

    def keepalive(self) -> bytes:
        call = self.callsign.encode()
        if self.protocol == "DExtra":
            return pad(self.callsign).encode() + b"\x00"
        if self.protocol == "DCS":
            return (call[:7].ljust(7) + self.local_module.encode() + b"\x00"
                    + self.name.encode()[:6] + b"\x00" + self.module.encode())
        return b"\x03\x60\x00"

    def keepalive_seconds(self) -> float:
        return {"DExtra": 3.0, "DCS": 2.0, "DPlus": 1.0}[self.protocol]

    def disconnect_packets(self) -> list[bytes]:
        if self.protocol in ("DExtra", "DCS"):
            return [pad(self.callsign).encode() + self.local_module.encode() + b" \x00"]
        return [b"\x05\x00\x18\x00\x00"]

    # ---- voice
    def tx_header(self, radio_header: Header) -> Header:
        """Rewrite the radio's header for this reflector link."""
        return Header(rpt2=module_call(self.name, self.module),
                      rpt1=module_call(self.callsign, self.local_module),
                      ur="CQCQCQ", my=radio_header.my, suffix=radio_header.suffix,
                      flags=b"\x00\x00\x00")

    def _dsvt(self, kind: int, stream: int, seq: int) -> bytes:
        band = b"\x00\x01\x02" if self.protocol != "DPlus" else b"\x00\x02\x01"
        return b"DSVT" + bytes([kind, 0, 0, 0, 0x20]) + band + stream.to_bytes(2, "big") + bytes([seq])

    def header_packet(self, header: Header, stream: int) -> bytes:
        if self.protocol == "DCS":
            raise ValueError("DCS has no separate header packet")
        body = self._dsvt(0x10, stream, 0x80) + header.pack()
        return (b"\x3a\x80" + body) if self.protocol == "DPlus" else body

    def data_packet(self, header: Header, stream: int, seq: int, frame: bytes, counter: int,
                    end: bool = False) -> bytes:
        frame = bytes(frame[:12]).ljust(12, b"\0")
        if self.protocol == "DCS":
            packet = bytearray(100)
            packet[0:4] = b"0001"
            packet[4:7] = bytes(header.flags[:3]).ljust(3, b"\0")
            packet[7:39] = (pad(header.rpt2) + pad(header.rpt1) + pad(header.ur) + pad(header.my)).encode("ascii", "replace")
            packet[39:43] = pad(header.suffix, 4).encode("ascii", "replace")
            packet[43:45] = stream.to_bytes(2, "big")
            packet[45] = (seq % 21) | (0x40 if end else 0)
            packet[46:58] = (bytes.fromhex("dc8e0a40adedad396e") + frame[9:12]) if end else frame
            packet[58:61] = (counter & 0xFFFFFF).to_bytes(3, "little")
            packet[61] = 0x01
            return bytes(packet)
        if end:
            body = self._dsvt(0x20, stream, (seq % 21) | 0x40)
            if self.protocol == "DPlus":
                return b"\x20\x80" + body + NULL_AMBE + b"\x55\x55\x55\x55\xc8\x7a"
            return body + END_FRAME
        body = self._dsvt(0x20, stream, seq % 21) + frame
        return (b"\x1d\x80" + body) if self.protocol == "DPlus" else body

    # ---- receive
    def parse(self, packet: bytes) -> NetFrame:
        size = len(packet)
        if self.protocol == "DPlus":
            if size == 3:
                return NetFrame("keepalive")
            if size == 5 and packet[:4] == b"\x05\x00\x18\x00":
                return NetFrame("echo" if packet[4] == 1 else "unlinked")
            if size == 8 and packet[1] == 0xC0:
                code = packet[4:8]
                if code in (b"OKRW", b"OKRO"):
                    return NetFrame("ack", text=code.decode())
                if code == b"BUSY":
                    return NetFrame("nak", text="Reflector busy")
                return NetFrame("nak", text="Login refused")
            if size >= 2 and packet[1] == 0x80 and packet[2:6] == b"DSVT":
                return self._parse_dsvt(packet[2:])
            return NetFrame("other")
        if self.protocol == "DExtra":
            if size == 9:
                return NetFrame("keepalive")
            if size == 14 and packet[10:13] == b"ACK":
                return NetFrame("ack")
            if size == 14 and packet[10:13] == b"NAK":
                return NetFrame("nak", text="Link refused")
            if size >= 27 and packet[:4] == b"DSVT":
                return self._parse_dsvt(packet)
            return NetFrame("other")
        # DCS
        if size in (17, 22):
            return NetFrame("keepalive")
        if size == 14 and packet[10:13] == b"ACK":
            return NetFrame("ack")
        if size == 14 and packet[10:13] == b"NAK":
            return NetFrame("nak", text="Link refused")
        if size == 35:
            return NetFrame("status", text=packet.split(b"\0")[0].decode("ascii", "replace").strip())
        if size >= 100 and packet[:4] == b"0001":
            header = Header(rpt2=packet[7:15].decode("ascii", "replace"), rpt1=packet[15:23].decode("ascii", "replace"),
                            ur=packet[23:31].decode("ascii", "replace"), my=packet[31:39].decode("ascii", "replace"),
                            suffix=packet[39:43].decode("ascii", "replace"), flags=bytes(packet[4:7]))
            seq = packet[45]
            return NetFrame("data", stream=int.from_bytes(packet[43:45], "big"), seq=seq & 0x1F,
                            end=bool(seq & 0x40), header=header, frame=bytes(packet[46:58]))
        return NetFrame("other")

    @staticmethod
    def _parse_dsvt(packet: bytes) -> NetFrame:
        stream = int.from_bytes(packet[12:14], "big")
        if packet[4] == 0x10 and len(packet) >= 56:
            return NetFrame("header", stream=stream, header=Header.parse(packet[15:56]))
        seq = packet[14]
        return NetFrame("data", stream=stream, seq=seq & 0x1F, end=bool(seq & 0x40),
                        frame=bytes(packet[15:27]))


def dplus_auth_packet(callsign: str) -> bytes:
    """Registration ping for the D-Plus trust server (auth.dstargateway.org:20001)."""
    buffer = bytearray(b" " * 56)
    buffer[0:4] = b"\x38\xc0\x01\x00"
    buffer[4:12] = pad(callsign).encode()
    buffer[12:20] = b"DV019999"
    buffer[28:32] = b"W7IB"
    buffer[32] = ord("2")
    buffer[40:47] = b"DHS0257"
    return bytes(buffer)


# --------------------------------------------------------------------------- URCALL commands
def urcall_command(ur: str):
    """Commands typed into the radio's UR field, like other D-STAR gateways.

    'REF001CL' link REF001 C, '       U' unlink, '       E' echo, '       I' info.
    Returns (action, reflector, module) or None for normal CQCQCQ traffic.
    """
    ur = pad(ur)
    if ur.strip() in ("CQCQCQ", ""):
        return None
    if ur[:7] == "       " and ur[7] in "UEI":
        return ({"U": "unlink", "E": "echo", "I": "info"}[ur[7]], "", "")
    if ur[7] == "L" and valid_reflector(ur[:6]) and ur[6].isalpha():
        return ("link", ur[:6], ur[6])
    return ("route", ur.strip(), "")
