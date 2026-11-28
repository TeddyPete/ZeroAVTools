import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from rigzero.gps_clock import GpsClock, rmc_utc
from rigzero.radio import Radio
from rigzero.storage import Store


def rmc(utc, day="281126", status="A"):
    body = f"GPRMC,{utc},{status},4500.000,N,09300.000,W,0.0,0.0,{day},,,A"
    checksum = 0
    for char in body:
        checksum ^= ord(char)
    return f"${body}*{checksum:02X}"


class GpsClockTest(unittest.TestCase):
    def test_checksum_validity_and_calendar(self):
        self.assertEqual(rmc_utc(rmc("013059.50")),
                         datetime(2026, 11, 28, 1, 30, 59, 500000, timezone.utc))
        self.assertIsNone(rmc_utc(rmc("013059", status="V")))
        self.assertIsNone(rmc_utc(rmc("013059", day="311126")))
        self.assertIsNone(rmc_utc(rmc("013059").replace(",A,", ",V,", 1)))

    def test_requires_progress_and_keeps_time_through_kiss(self):
        elapsed = [100.0]
        clock = GpsClock(lambda: elapsed[0])
        clock.observe(rmc("235959"))
        clock.observe(rmc("235959"))
        self.assertEqual(clock.reading(), (None, None))
        elapsed[0] += 1
        clock.observe(rmc("000000", day="291126"))
        self.assertEqual(clock.position(), "45.000000,-93.000000")
        clock.break_stream()
        elapsed[0] += 120
        utc, age = clock.reading()
        self.assertEqual(utc.isoformat(), "2026-11-29T00:02:00+00:00")
        self.assertEqual(age, 120)
        self.assertIsNone(clock.position())

    def test_confirmed_kenwood_uv_time_is_used_for_packet_timestamps(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder))
            radio = Radio(store)
            radio.stop_event.set()
            radio.thread.join(1)
            elapsed = [20.0]
            radio.gps_clock = GpsClock(lambda: elapsed[0])
            radio._line("ID TH-D75")
            radio._line(rmc("123400"))
            elapsed[0] += 1
            radio._line(rmc("123401"))
            radio._line("N0TEST>APRS:>match")
            self.assertEqual(store.rows()[0]["time"], "2026-11-28T12:34:01+00:00")
            self.assertEqual(radio.lines, ["ID TH-D75"])
            store.close()
