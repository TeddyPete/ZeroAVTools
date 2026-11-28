import tempfile
import unittest
import socket
from pathlib import Path

from rigzero.packet_native import Engine, md5
from rigzero.protocol import address, kiss_encode
from rigzero.radio import Radio
from rigzero.storage import Store
from test_core import FakeRadio


def response(control, data=b""):
    return address("N0CALL", repeated=False) + address("N0TEST", last=True, repeated=True) + bytes([control]) + data


@unittest.skipUnless((Path(__file__).resolve().parents[1] / "rigzero" / "libpacket.so").exists(),
                     "Build the native packet engine first")
class NativePacketTest(unittest.TestCase):
    def test_terminal_open_send_receive_and_close(self):
        with tempfile.TemporaryDirectory() as temp:
            events = []
            with Engine(temp, temp, lambda kind, data: events.append((kind, data))) as engine:
                engine.require("open", "N0CALL", "N0TEST", "", 0, 0, 100, md5)
                self.assertEqual(events[-1][1][14], 0x3f)
                ua = response(0x73)
                engine.require("rx", ua, len(ua), 200)
                self.assertEqual(engine.call("state"), 2)
                engine.require("send", b"help\r", 5)
                engine.require("tick", 250)
                self.assertEqual(events[-1][1][15:], b"\xf0help\r")
                incoming = response(0x20, b"\xf0welcome\r")
                engine.require("rx", incoming, len(incoming), 300)
                self.assertIn((1, b"welcome\r"), events)
                engine.require("close", 400)
                self.assertEqual(events[-1][1][14], 0x53)
                engine.require("rx", ua, len(ua), 500)
                self.assertEqual(engine.call("state"), 0)

    def test_terminal_shares_passive_capture_and_restores_radio(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            store.set("callsign", "N0CALL")
            radio = Radio(store)
            radio.stop_event.set()
            radio.thread.join(1)
            left, right = socket.socketpair()
            fake = FakeRadio(right)
            with left:
                left.settimeout(.1)
                radio.connection = left
                radio.update(connected=True, identity=True)
                try:
                    with self.assertRaisesRegex(RuntimeError, "frequency changed"):
                        radio.packet_open("terminal", "N0TEST", expected_hz=145000000)
                    self.assertIsNone(store.get("recovery", None))
                    radio.packet_open("terminal", "N0TEST", expected_hz=144390000)
                    with self.assertRaises(RuntimeError):
                        radio.start_kiss("ble")
                    right.sendall(kiss_encode(response(0x73)))
                    radio._receive()
                    radio.packet_service()
                    self.assertEqual(radio.snapshot()["packet_link"], "Connected")
                    self.assertTrue(any(p["source"] == "N0TEST" for p in store.rows()))
                    radio.packet_stop()
                    right.sendall(kiss_encode(response(0x73)))
                    radio._receive()
                    radio.packet_service()
                    self.assertEqual(fake.tn, "TN 1,0")
                    self.assertIsNone(store.get("recovery", None))
                finally:
                    fake.close()
                    store.close()

    def test_yapp_publishes_bounded_file_before_ack(self):
        with tempfile.TemporaryDirectory() as temp:
            events = []
            with Engine(temp, temp, lambda kind, data: events.append((kind, data))) as engine:
                engine.require("open", "N0CALL", "N0TEST", "", 0, 0, 100, md5)
                ua = response(0x73)
                engine.require("rx", ua, len(ua), 200)
                engine.call("accept_yapp", 1)
                frames = (b"\x05\x01", b"\x01\x05a\0" + b"3\0\0", b"\x02\x03abc\x26", b"\x03")
                for seq, message in enumerate(frames):
                    packet = response((seq << 1) | (seq << 5), b"\xf0" + message)
                    engine.require("rx", packet, len(packet), 400+seq*100)
                names = list(Path(temp).glob("received-*-a"))
                self.assertEqual([path.read_bytes() for path in names], [b"abc"])
                self.assertTrue(any(kind == 2 and b"Received " in data for kind, data in events))

    def test_winlink_draft_outbox_and_radio_handshake(self):
        with tempfile.TemporaryDirectory() as temp:
            events = []
            with Engine(temp, temp, lambda kind, data: events.append((kind, data))) as engine:
                engine.require("save_mail","ABCD12345678","2026/11/28 12:00","N0CALL","N0TEST","Test","Hello",0)
                engine.require("list_mail","ABCD12345678")
                self.assertEqual(events[-1][1].split(b"\0"), [b"D",b"ABCD12345678",b"2026/11/28 12:00",b"N0CALL",b"N0TEST",b"Test",b"Hello"])
                engine.require("save_mail","ABCD12345678","2026/11/28 12:00","N0CALL","N0TEST","Test","Hello",1)
                engine.require("list_mail","")
                self.assertTrue(events[-1][1].startswith(b"O\0"))
                engine.require("open", "N0CALL", "N0TEST", "mock", 1, 0, 100, md5)
                ua = response(0x73)
                engine.require("rx", ua, len(ua), 200)
                self.assertEqual(engine.call("state"), 2)
                greeting = response(0, b"\xf0;PQ: 12345678\rReady>\r")
                engine.require("rx", greeting, len(greeting), 500)
                for now in (600,700,800):
                    engine.require("tick", now)
                traffic = b"".join(data for kind, data in events if kind == 0)
                self.assertIn(b";PR: ",traffic)
                self.assertIn(b"FC ",traffic)
