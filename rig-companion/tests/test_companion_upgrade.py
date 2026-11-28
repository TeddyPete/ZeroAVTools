import csv
import socket
import struct
import tempfile
import time
import unittest
from pathlib import Path

from rigzero.capture import export
from rigzero.manual import MENUS, entries
from rigzero.models import discovery_candidate, model_from_id
from rigzero.protocol import decode_ax25, inspect_packet, kiss_encode, position_frame
from rigzero.radio import Radio
from rigzero.storage import Store
from test_core import FakeRadio


class CompanionUpgrade(unittest.TestCase):
    def test_model_detection_and_model_specific_manual(self):
        for model in ("TH-D74", "TH-D75", "TM-D750"):
            self.assertEqual(model_from_id("ID " + model), model)
        self.assertEqual(model_from_id("ID TM-D750E"), "TM-D750")
        self.assertIsNone(model_from_id("ID TH-D750"))
        self.assertTrue(discovery_candidate("Renamed radio", ["00001101-0000-1000-8000-00805f9b34fb"]))
        self.assertFalse(discovery_candidate("Phone", []))
        self.assertEqual(len(MENUS), 216)
        self.assertTrue(entries(0, "983", "TH-D75")[0][0].startswith("983"))
        self.assertTrue(entries(0, "KISS", "TM-D750")[0][0].startswith("984"))
        self.assertTrue(entries(0, "raw packets", "TH-D74")[0][0].startswith("580"))

    def test_capture_preserves_wire_bytes_without_fabricating_native_frames(self):
        raw = position_frame("N0CALL-7", "45,-93")
        self.assertEqual(decode_ax25(raw)[3], "!4500.00N/09300.00W-")
        with self.assertRaises(ValueError):
            position_frame("N0CALL", "45,200")
        packets = [dict(inspect_packet(*decode_ax25(raw)), ax25_hex=raw.hex()),
                   inspect_packet("N0TEST", "APRS", "", ">text")]
        with tempfile.TemporaryDirectory() as temp:
            folder, total, count = export(packets, temp)
            self.assertEqual((total, count), (2, 1))
            with (folder / "packets.csv").open(newline="", encoding="utf-8") as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 2)
            pcap = (folder / "kiss.pcap").read_bytes()
            self.assertEqual(struct.unpack_from("<I", pcap, 20)[0], 3)
            self.assertEqual(pcap[40:], raw)

    def test_outbox_only_accepts_matching_packet_ack(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            message_id = store.stage_message("N0CALL", "N0TEST-1", "Hello")
            store.mark_message_sent(message_id)
            body = f":N0CALL   :ack{message_id:05d}"
            store.record(inspect_packet("N0TEST-2", "APRS", "", body))
            self.assertEqual(store.outbox()[0][2], "Awaiting ACK")
            store.record(inspect_packet("N0TEST-1", "APRS", "", body))
            self.assertEqual(store.outbox()[0][2], "Acknowledged")
            store.close()

    def test_monitor_works_without_id_and_does_not_write(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            radio = Radio(store)
            radio.stop_event.set()
            radio.thread.join(1)
            left, right = socket.socketpair()
            with left, right:
                right.settimeout(.05)
                radio.connection = left
                radio.manual_monitor()
                right.sendall(kiss_encode(position_frame("N0CALL", "45,-93")))
                radio._receive()
                self.assertEqual(store.rows()[0]["source"], "N0CALL")
                self.assertTrue(store.rows()[0]["ax25_hex"])
                radio.end_kiss()
                with self.assertRaises(socket.timeout):
                    right.recv(1)
                self.assertIsNone(store.get("recovery", None))
            store.close()

    def test_confirmed_beacon_uses_current_session_and_cancels_on_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            store.set("callsign", "N0CALL-7")
            radio = Radio(store)
            radio.stop_event.set()
            radio.thread.join(1)
            left, right = socket.socketpair()
            fake = FakeRadio(right)
            with left:
                left.settimeout(.05)
                radio.connection = left
                radio.state.update(connected=True, identity=True)
                try:
                    radio.start_kiss()
                    s = radio.snapshot()
                    details = (s["packet_band"], s["packet_hz"], s["packet_rate"])
                    with self.assertRaises(RuntimeError):
                        radio.configure_beacon("45,-93", 300, details, s["session_id"] - 1)
                    radio.configure_beacon("45,-93", 300, details, s["session_id"])
                    deadline = time.monotonic() + 1
                    while not fake.frames and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertEqual(len(fake.frames), 1)
                    self.assertTrue(radio.snapshot()["beacon_armed"])
                    radio.end_kiss()
                    self.assertFalse(radio.snapshot()["beacon_armed"])
                    self.assertEqual(fake.tn, "TN 1,0")
                finally:
                    fake.close()
                    store.close()

    def test_compatible_family_packet_controls_are_enabled_after_readback(self):
        for model in ("TH-D74", "TM-D750"):
            with self.subTest(model=model), tempfile.TemporaryDirectory() as temp:
                store = Store(Path(temp))
                radio = Radio(store)
                radio.stop_event.set()
                radio.thread.join(1)
                left, right = socket.socketpair()
                fake = FakeRadio(right, model=model)
                with left:
                    left.settimeout(.05)
                    radio.connection = left
                    radio.update(connected=True)
                    try:
                        radio.identify()
                        radio.start_kiss()
                        self.assertEqual(radio.snapshot()["model"], model)
                        self.assertTrue(radio.snapshot()["kiss"])
                        self.assertEqual(store.get("recovery")["model"], model)
                        radio.end_kiss()
                        self.assertEqual(fake.tn, "TN 1,0")
                        self.assertIsNone(store.get("recovery", None))
                    finally:
                        fake.close()
                        store.close()
