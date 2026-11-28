"""TH-D75 CAT, KISS and APRS formats, adapted from our verified CYD paths."""
from __future__ import annotations

import re
from datetime import datetime, timezone

MODES = ("FM", "DV", "AM", "LSB", "USB", "CW", "NFM", "DR", "WFM", "CW-R")
POWERS = ("High", "Medium", "Low", "Extra low")
STEPS = (5000, 6250, 8330, 9000, 10000, 12500, 15000, 20000, 25000, 30000, 50000, 100000)


def valid_call(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Z0-9]{1,6}(?:-(?:[0-9]|1[0-5]))?", value))


def kiss_encode(payload: bytes, command: int = 0) -> bytes:
    if not 0 <= command <= 255 or len(payload) > 1024:
        raise ValueError("KISS frame too large")
    data = bytes([command]) + payload
    return b"\xc0" + data.replace(b"\xdb", b"\xdb\xdd").replace(b"\xc0", b"\xdb\xdc") + b"\xc0"


class KissDecoder:
    def __init__(self):
        self.data = bytearray()
        self.open = self.escape = self.bad = False
        self.invalid = 0

    def feed(self, data: bytes):
        for byte in data:
            if byte == 192:
                if self.open and self.data:
                    if self.bad or self.escape:
                        self.invalid += 1
                    else:
                        yield self.data[0], bytes(self.data[1:])
                self.data.clear()
                self.open, self.escape, self.bad = True, False, False
            elif self.open and not self.bad:
                if self.escape:
                    self.escape = False
                    if byte not in (220, 221):
                        self.bad = True
                        continue
                    byte = 192 if byte == 220 else 219
                elif byte == 219:
                    self.escape = True
                    continue
                if len(self.data) == 1025:
                    self.bad = True
                else:
                    self.data.append(byte)


def address(call: str, last: bool = False, repeated: bool = False) -> bytes:
    if not valid_call(call):
        raise ValueError("Use a callsign with an optional SSID from 0 to 15")
    base, _, ssid = call.partition("-")
    return bytes(ord(c) << 1 for c in base.ljust(6)) + bytes([0x60 | (int(ssid or 0) << 1) | last | (128 if repeated else 0)])


def message_frame(source: str, recipient: str, text: str, path: str = "", message_id: str = "") -> bytes:
    if not valid_call(recipient) or not 1 <= len(text) <= 67:
        raise ValueError("Enter a recipient and 1–67 message characters")
    if any(ord(c) < 32 or ord(c) > 126 or c in "{|~" for c in text):
        raise ValueError("Use plain APRS text without {, | or ~")
    if message_id and not re.fullmatch(r"[A-Za-z0-9]{1,5}", message_id):
        raise ValueError("Invalid APRS message number")
    suffix = "{" + message_id if message_id else ""
    return aprs_frame(source, f":{recipient:<9}:{text}{suffix}", path)


def aprs_frame(source: str, info: str, path: str = "") -> bytes:
    route = path.split(",") if path else []
    if len(route) > 2:
        raise ValueError("Use at most two APRS path entries")
    calls = ["APRS", source] + route
    headers = b"".join(address(call, i == len(calls) - 1) for i, call in enumerate(calls))
    return headers + b"\x03\xf0" + info.encode("ascii")


def position_frame(source: str, position: str, path: str = "") -> bytes:
    """Create a fixed, uncompressed APRS position report."""
    parts = position.split(",")
    if len(parts) != 2 or any(not re.fullmatch(r"[+-]?\d{1,3}(?:\.\d{1,8})?", part.strip()) for part in parts):
        raise ValueError("Use decimal latitude, longitude")
    lat, lon = (float(part) for part in parts)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Latitude or longitude out of range")

    def coordinate(value, width, positive, negative):
        degrees, hundredths = divmod(round(abs(value) * 6000), 6000)
        return f"{degrees:0{width}d}{hundredths / 100:05.2f}{negative if value < 0 else positive}"

    return aprs_frame(source, "!" + coordinate(lat, 2, "N", "S") + "/" +
                      coordinate(lon, 3, "E", "W") + "-", path)


def decode_ax25(raw: bytes):
    calls = []
    offset = 0
    while offset + 7 <= len(raw) and len(calls) < 10:
        part = raw[offset:offset + 7]
        base = "".join(chr(c >> 1) for c in part[:6]).rstrip()
        ssid = (part[6] >> 1) & 15
        call = base + (f"-{ssid}" if ssid else "")
        if not valid_call(call) or any(c & 1 for c in part[:6]):
            return None
        calls.append(call + ("*" if offset >= 14 and part[6] & 128 else ""))
        offset += 7
        if part[6] & 1:
            break
    else:
        return None
    if len(calls) < 2 or offset >= len(raw):
        return None
    control = raw[offset]
    if control & 0xef == 3 and raw[offset + 1:offset + 2] == b"\xf0":
        info = raw[offset + 2:].decode("latin-1")
    else:
        info = f"AX.25 control 0x{control:02X}  {raw[offset + 1:].hex(' ')}"
    return calls[1], calls[0], ",".join(calls[2:]), info


def decode_tnc2(line: str):
    header, sep, info = line.partition(":")
    source, arrow, rest = header.partition(">")
    if not sep or not arrow or not valid_call(source) or not rest or not info:
        return None
    destination, _, path = rest.partition(",")
    return source, destination, path, info


def inspect_packet(source: str, destination: str, path: str, info: str, direction="RX") -> dict:
    packet = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "source": source, "destination": destination, "path": path, "info": info,
              "direction": direction, "kind": "Packet", "recipient": "", "text": info, "weather": {}}
    inner = (source, destination, path, info)
    for _ in range(3):
        if not inner[3].startswith("}"):
            break
        nested = decode_tnc2(inner[3][1:])
        if not nested:
            break
        inner = nested
    sender, inner_destination, _, body = inner
    packet["sender"] = sender
    if len(body) >= 12 and body[0] == ":" and body[10] == ":":
        packet.update(kind="Message", recipient=body[1:10].rstrip(), text=body[11:])
        if re.fullmatch(r"(?:ack|rej)[A-Za-z0-9]{1,5}", packet["text"]):
            packet["kind"] = "Ack"
    elif body[:1] in ("!", "=", "/", "@", "`", "'"):
        packet["kind"] = "Position"
    elif body.startswith(">"):
        packet["kind"] = "Status"
    weather = {}
    if body.startswith("_") or (body[:1] in ("!", "=", "/", "@") and "_" in body):
        fields = {"temp": (r"t(-?\d{3})", 1), "humidity": (r"h(\d{2})", 1),
                  "wind": (r"s(\d{3})", 1), "gust": (r"g(\d{3})", 1),
                  "bearing": (r"c(\d{3})", 1), "pressure": (r"b(\d{5})", .1), "rain": (r"r(\d{3})", .01)}
        for key, (pattern, scale) in fields.items():
            match = re.search(pattern, body)
            if match:
                weather[key] = int(match[1]) * scale
        if weather.get("humidity") == 0:
            weather["humidity"] = 100
        wind = re.search(r"_(\d{3})/(\d{3})", body)
        if wind:
            weather.update(bearing=int(wind[1]), wind=int(wind[2]))
    if weather:
        packet.update(kind="Weather", weather=weather)
    from .aprs import decode
    decoded = decode(sender, inner_destination, body)
    packet["decoded"] = decoded
    types = {"telemetry": "Telemetry", "telemetry setup": "Telemetry setup", "object": "Object", "item": "Item"}
    if decoded.get("type") in types:
        packet["kind"] = types[decoded["type"]]
    if "lat" in decoded and packet["kind"] == "Packet":
        packet["kind"] = "Position"
    return packet


def fo_frequency(record: str, band: int, frequency: int) -> str:
    prefix = f"FO {band},"
    fields = record.split(",")
    if not record.startswith(prefix) or len(fields) != 21 or not fields[1].isdigit():
        raise ValueError("Radio returned an invalid VFO record")
    if not 100000 <= frequency <= 524000000 or (band == 0 and not 136000000 <= frequency <= 470000000):
        raise ValueError("Frequency outside this band's tuning range")
    fields[1] = f"{frequency:010d}"
    return ",".join(fields)
