import socket
import tempfile
import time
import unittest
from pathlib import Path

from rigzero import drivers
from rigzero.app import make_radio
from rigzero.models import discovery_candidate, radio_info
from rigzero.usbserial import SerialPort

BENSHI = drivers.find("benshi")
KENWOOD = drivers.find("kenwood")
drivers.load(BENSHI)
import sys
benshi = sys.modules["rig_companion_radio_benshi"]
from rigzero.protocol import decode_ax25, message_frame
from rigzero.storage import Store


def channel_record(number, hz, name="", wide=True, high=True):
    data = bytes(25)
    data = benshi.set_bits(data, 0, 8, number)
    data = benshi.set_bits(data, 10, 30, hz)
    data = benshi.set_bits(data, 42, 30, hz)
    data = benshi.set_bits(data, 105, 1, int(high))
    data = benshi.set_bits(data, 107, 1, int(wide))
    return data[:15] + name.encode().ljust(10, b"\0")


class FakeRadioSocket:
    """Answers GAIA commands like a UV-Pro with channels 0, 1 and 3 programmed."""

    def __init__(self):
        self.out = bytearray()
        settings = bytes(22)
        settings = benshi.with_settings_channel(settings, 0, 0)
        settings = benshi.with_settings_channel(settings, 1, 1)
        self.settings = benshi.with_setting(settings, "squelch_level", 3)
        self.channels = {0: channel_record(0, 144390000, "APRS"), 1: channel_record(1, 146520000, "Simplex"),
                         3: channel_record(3, 446000000, "UHF")}
        self.volume = 5
        self.sent_frames = []
        self.fragment = b""

    def reply(self, command, body=b"", status=0):
        message = bytes((0, 2, 0x80 | (command >> 8), command & 0xFF, status)) + body
        self.out += benshi.gaia_frame(message)

    def event(self, kind, body):
        self.out += benshi.gaia_frame(bytes((0, 2, 0, benshi.EVENT_NOTIFICATION, kind)) + body)

    def sendall(self, data):
        buffer = bytearray(data)
        for message in benshi.split_frames(buffer):
            command, payload = ((message[2] & 0x7F) << 8) | message[3], message[4:]
            if command == benshi.GET_DEV_INFO:
                self.reply(command, bytes([1, 0, 1, 1, 0, 1, 0, 0, 8, 0]))
            elif command == benshi.READ_SETTINGS:
                self.reply(command, self.settings)
            elif command == benshi.WRITE_SETTINGS:
                self.settings = bytes(payload)
                self.reply(command)
            elif command == benshi.READ_RF_CH:
                number = payload[0]
                if number in self.channels:
                    self.reply(command, self.channels[number])
                else:
                    self.reply(command, channel_record(number, 0))
            elif command == benshi.GET_VOLUME:
                self.reply(command, bytes([self.volume]))
            elif command == benshi.SET_VOLUME:
                self.volume = payload[0]
                self.reply(command)
            elif command == benshi.GET_HT_STATUS:
                self.reply(command, bytes([0x80 | 0x20, 0x00, 0xC0, 0x00]))
            elif command == benshi.HT_SEND_DATA:
                self.fragment += payload[1:]
                if payload[0] & 0x80:
                    self.sent_frames.append(self.fragment)
                    self.fragment = b""
                self.reply(command)
            else:
                self.reply(command)

    def recv(self, size):
        if not self.out:
            time.sleep(.005)
            raise socket.timeout()
        data = bytes(self.out[:size])
        del self.out[:size]
        return data


class FakeLink:
    def __init__(self):
        self.package = None
        self.socket = FakeRadioSocket()
        self.service = None

    def connect(self, device, service=None):
        self.service = service
        return self.socket

    def devices(self):
        return []

    def disconnect(self):
        pass


class ProtocolTests(unittest.TestCase):
    def test_frames_round_trip(self):
        message = benshi.build_message(benshi.READ_RF_CH, b"\x03")
        buffer = bytearray(b"\x00junk" + benshi.gaia_frame(message) + b"\xff")
        self.assertEqual(benshi.split_frames(buffer), [message])
        self.assertEqual(bytes(buffer), b"\xff")

    def test_fragments_reassemble(self):
        frame = message_frame("N0TNT", "N0CALL", "hello " * 11, "WIDE1-1,WIDE2-1")
        pieces = benshi.fragments(frame)
        self.assertGreater(len(pieces), 1)
        joiner = benshi.Reassembler()
        results = [joiner.add(piece) for piece in pieces]
        self.assertEqual(results[-1], frame)
        self.assertTrue(all(result is None for result in results[:-1]))

    def test_channel_and_settings_fields(self):
        channel = benshi.parse_channel(channel_record(7, 146520000, "Simplex"))
        self.assertEqual((channel["number"], channel["rx_hz"], channel["name"]), (7, 146520000, "Simplex"))
        settings = benshi.with_settings_channel(bytes(22), 1, 0x25)
        self.assertEqual(benshi.settings_channel(settings, 1), 0x25)
        self.assertEqual(benshi.settings_channel(settings, 0), 0)

    def test_status_channel(self):
        status = benshi.parse_status(bytes([0xA0, 0x30, 0xC0, 0x04]))
        self.assertTrue(status["squelch_open"])
        self.assertEqual(status["channel"], 0x13)
        self.assertEqual(status["rssi"], 12)


class PackageTests(unittest.TestCase):
    def test_installed_packages_kenwood_first(self):
        ids = [package["id"] for package in drivers.installed()]
        self.assertEqual(ids[0], "kenwood")
        self.assertIn("benshi", ids)

    def test_features(self):
        self.assertIn("dstar", radio_info(KENWOOD, "TH-D75")["features"])
        self.assertNotIn("dstar", radio_info(BENSHI, "UV-Pro")["features"])
        self.assertEqual(radio_info(BENSHI, "nope")["key"], "UV-Pro")

    def test_discovery_by_package(self):
        self.assertTrue(discovery_candidate("UV-PRO", (), BENSHI))
        self.assertFalse(discovery_candidate("UV-PRO", (), KENWOOD))
        self.assertTrue(discovery_candidate("TH-D75A", (), KENWOOD))
        self.assertTrue(discovery_candidate("Radio", ("00001107-D102-11E1-9B23-00025B00A5A5",), BENSHI))

    def test_only_chosen_driver_loads(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            sys.modules.pop("rig_companion_radio_kenwood", None)
            store.set("radio_package", "benshi")
            radio = make_radio(store, None)
            self.assertNotIn("rig_companion_radio_kenwood", sys.modules)
            self.assertEqual(radio.family, "benshi")
            radio.close()
            store.set("radio_package", "nothing")
            self.assertEqual(type(make_radio(store, None)).__name__, "IdleRadio")
            store.close()


class UsbSerialTests(unittest.TestCase):
    def test_pty_round_trip(self):
        import os
        import pty
        master, slave = pty.openpty()
        port = SerialPort(os.ttyname(slave), 9600)
        try:
            port.sendall(b"ID\r")
            self.assertEqual(os.read(master, 16), b"ID\r")
            os.write(master, b"ID TH-D75\r")
            self.assertEqual(port.recv(64), b"ID TH-D75\r")
            with self.assertRaises(socket.timeout):
                port.recv(64)
        finally:
            port.close()
            os.close(master)
            os.close(slave)


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.store.set("callsign", "N0TNT")
        self.link = FakeLink()
        self.radio = make_radio(self.store, self.link, "benshi", "UV-Pro")

    def tearDown(self):
        self.radio.close()
        self.store.close()
        self.temp.cleanup()

    def run_action(self, action, *args):
        self.radio.update(error="")
        self.radio.submit(action, *args)
        end = time.monotonic() + 4
        while time.monotonic() < end and not self.radio.actions.empty():
            time.sleep(.02)
        time.sleep(.3)
        return self.radio.snapshot()

    def test_connect_controls_and_aprs(self):
        self.assertEqual(self.link.package["id"], "benshi")
        state = self.run_action("connect", {"address": "38:D2:00:00:00:01", "name": "UV-PRO", "paired": True, "path": "/x"})
        self.assertEqual(self.link.service, benshi.GAIA_UUID)
        self.assertTrue(state["connected"] and state["identity"], state)
        self.assertEqual(state["model"], "UV-Pro")
        self.assertEqual(state["bands"][0]["hz"], 144390000)
        self.assertEqual(state["bands"][1]["name"], "Simplex")
        self.assertEqual(state["volume"], 5)

        state = self.run_action("change", "volume", 1)
        self.assertEqual(state["volume"], 6, state["error"])

        state = self.run_action("step", 1)      # A: channel 0 -> 1
        self.assertEqual(state["bands"][0]["number"], 1, state["error"])
        state = self.run_action("step", 1)      # skips empty 2, lands on 3
        self.assertEqual(state["bands"][0]["number"], 3, state["error"])
        self.assertEqual(state["bands"][0]["hz"], 446000000)

        state = self.run_action("tune", "146.52")
        self.assertIn("tunes by channel", state["error"])

        state = self.run_action("send", "N0CALL", "hello", state["session_id"])
        self.assertEqual(state["error"], "")
        self.assertTrue(state["kiss"])
        self.assertEqual(len(self.link.socket.sent_frames), 1)
        source, destination, *_ = decode_ax25(self.link.socket.sent_frames[0])
        self.assertEqual(source, "N0TNT")

        heard = message_frame("N0CALL", "N0TNT", "hi back", "")
        for piece in benshi.fragments(heard):
            self.link.socket.event(benshi.EV_DATA_RXD, piece)
        time.sleep(.4)
        self.assertGreaterEqual(self.radio.snapshot()["rx"], 1)


if __name__ == "__main__":
    unittest.main()
