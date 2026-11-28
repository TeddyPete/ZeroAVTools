"""UTC holdover from checked Kenwood NMEA RMC sentences."""

import re
import threading
import time
from datetime import datetime, timedelta, timezone

LIVE_SECONDS = 5


def rmc_utc(line):
    match = re.fullmatch(r"\$((?:GP|GN)RMC,[!-~]*)\*([0-9A-Fa-f]{2})", line)
    if not match or len(line) > 128 or "*" in match[1]:
        return None
    checksum = 0
    for char in match[1]:
        checksum ^= ord(char)
    fields = match[1].split(",")
    if (checksum != int(match[2], 16) or len(fields) < 10 or fields[2] != "A"
            or not re.fullmatch(r"[0-9]{6}(?:\.[0-9]{1,9})?", fields[1])
            or not re.fullmatch(r"[0-9]{6}", fields[9])):
        return None
    utc, _, fraction = fields[1].partition(".")
    date = fields[9]
    try:
        return datetime(2000 + int(date[4:6]), int(date[2:4]), int(date[:2]),
                        int(utc[:2]), int(utc[2:4]), int(utc[4:6]),
                        int(fraction[:6].ljust(6, "0")), timezone.utc)
    except ValueError:
        return None


def boot_time():
    if hasattr(time, "CLOCK_BOOTTIME"):
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    return time.monotonic()


def rmc_position(line):
    fields = line.split(",")
    if len(fields) < 7:
        return None
    parts = []
    for raw, side, digits, hemispheres, limit in (
        (fields[3], fields[4], 2, "NS", 90),
        (fields[5], fields[6], 3, "EW", 180),
    ):
        if not re.fullmatch(rf"[0-9]{{{digits+2}}}(?:\.[0-9]+)?", raw) or side not in tuple(hemispheres):
            return None
        minutes = float(raw[digits:])
        value = int(raw[:digits]) + minutes / 60
        if minutes >= 60 or value > limit:
            return None
        parts.append(-value if side == hemispheres[1] else value)
    return f"{parts[0]:.6f},{parts[1]:.6f}"


class GpsClock:
    def __init__(self, clock=boot_time):
        self.clock = clock
        self.lock = threading.Lock()
        self.previous = None
        self.anchor = None
        self.fix = None

    def observe(self, line):
        utc = rmc_utc(line)
        if utc is None:
            return
        arrival = self.clock()
        with self.lock:
            if self.previous:
                last, received = self.previous
                elapsed = arrival - received
                advance = (utc - last).total_seconds()
                if utc == last:
                    return
                if 0 < elapsed <= LIVE_SECONDS and 0 < advance <= elapsed + 2:
                    if self.anchor is None or utc >= self.anchor[0]:
                        self.anchor = utc, arrival
                        position = rmc_position(line)
                        self.fix = (position, arrival) if position else None
            self.previous = utc, arrival

    def break_stream(self):
        with self.lock:
            self.previous = None

    def reading(self):
        with self.lock:
            if self.anchor is None:
                return None, None
            utc, received = self.anchor
            age = self.clock() - received
            return utc + timedelta(seconds=age), age

    def position(self, max_age=30):
        with self.lock:
            if self.fix and self.clock() - self.fix[1] <= max_age:
                return self.fix[0]
            return None
