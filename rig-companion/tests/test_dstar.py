import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rigzero import dstar
from rigzero.dstar import (DSTAR_DATA, DSTAR_EOT, DSTAR_HEADER, GET_VERSION, SCRAMBLE, SYNC, Header,
                           MmdvmDecoder, Reflector, SlowData, dprs_to_tnc2, encode_message, mmdvm_frame,
                           parse_hosts, urcall_command)
from rigzero.storage import Store
import rigzero.dstar_gateway as gateway_module


def gps_slow(text):
    """Slow data frames (1..20) carrying text as D-PRS GPS blocks."""
    raw = text.encode()
    frames = []
    for i in range(0, len(raw), 5):
        chunk = raw[i:i + 5]
        block = bytes([0x30 | len(chunk)]) + chunk.ljust(5, b"f")
        for part in (block[:3], block[3:]):
            frames.append(bytes(b ^ k for b, k in zip(part, SCRAMBLE)))
    return frames


def superframes(message_frames, gps_frames, count):
    """Yield (seq, slow) for count frames: message in the first superframe, GPS afterwards."""
    gps = list(gps_frames)
    for n in range(count):
        seq = n % 21
        if seq == 0:
            yield seq, SYNC
        elif n < 21:
            yield seq, message_frames[seq - 1]
        else:
            yield seq, gps.pop(0) if gps else dstar.FILLER


class CoreTests(unittest.TestCase):
    def test_header_crc_matches_reference(self):
        packed = Header(my="N0TNT", suffix="D75").pack()
        self.assertEqual(packed[-2:].hex(), "7353")    # value from DroidStar's CCRC::addCCITT161
        parsed = Header.parse(packed)
        self.assertEqual(parsed.caller, "N0TNT")
        self.assertEqual(parsed.ur.strip(), "CQCQCQ")

    def test_mmdvm_decoder_handles_split_and_junk(self):
        decoder = MmdvmDecoder()
        stream = b"\x00\x11" + mmdvm_frame(DSTAR_DATA, bytes(12)) + mmdvm_frame(DSTAR_EOT)
        out = decoder.feed(stream[:7]) + decoder.feed(stream[7:])
        self.assertEqual([c for c, _ in out], [DSTAR_DATA, DSTAR_EOT])
        self.assertEqual(len(dstar.mmdvm_config(1)), 26)
        self.assertEqual(len(dstar.mmdvm_config(2)), 40)

    def test_slow_data_message_and_dprs(self):
        line = "$$CRCB0D2,N0TNT-7>API75,DSTAR*:!3851.38N/09432.54W[Rig Companion\r"
        slow = SlowData()
        for seq, data in superframes(encode_message("Ted in Kansas City"), gps_slow(line), 120):
            slow.feed(seq, data)
        self.assertEqual(slow.message, "Ted in Kansas City")
        lines = slow.take_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(dprs_to_tnc2(lines[0]), "N0TNT-7>API75,DSTAR*:!3851.38N/09432.54W[Rig Companion")

    def test_slow_data_follows_sync_not_network_numbering(self):
        # Seen live on DCS001: the reflector's frame numbers ran 11 ahead of the sync frame.
        slow = SlowData()
        message = encode_message("Offset numbering ok")
        frames = [SYNC if n % 21 == 0 else message[n % 21 - 1] for n in range(63)]
        for n, data in enumerate(frames[5:]):
            slow.feed((n + 11) % 21, data)
        self.assertEqual(slow.message, "Offset numbering ok")

    def test_nmea_dprs(self):
        rmc = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
        tnc2 = dprs_to_tnc2(rmc, "N0TNT")
        self.assertTrue(tnc2.startswith("N0TNT>API75,DSTAR*:!4807.04N/01131.00E["))
        self.assertIsNone(dprs_to_tnc2(rmc.replace("*6A", "*00"), "N0TNT"))

    def test_hosts_and_urcall(self):
        hosts = parse_hosts("# x\nREF001\t1.2.3.4\nXRF757\t5.6.7.8\t30001\nbad\n", "")
        self.assertEqual(hosts["REF001"], ("1.2.3.4", 20001))
        self.assertEqual(parse_hosts("307;9.9.9.9;4004")["XLX307"], ("9.9.9.9", 30001))
        self.assertEqual(urcall_command("REF030CL"), ("link", "REF030", "C"))
        self.assertEqual(urcall_command("       E"), ("echo", "", ""))
        self.assertIsNone(urcall_command("CQCQCQ  "))
        self.assertEqual(urcall_command("W1AW")[0], "route")

    def test_reflector_packets_roundtrip(self):
        header = Header(rpt2="XRF757 A", rpt1="N0TNT  A", my="N0TNT", suffix="D75")
        for name in ("XRF757", "REF001", "DCS001"):
            r = Reflector(name, "A", "N0TNT-7")
            if r.protocol != "DCS":
                parsed = r.parse(r.header_packet(header, 0x1234))
                self.assertEqual((parsed.kind, parsed.stream, parsed.header.caller), ("header", 0x1234, "N0TNT"))
            frame = bytes(range(12))
            parsed = r.parse(r.data_packet(header, 0x1234, 5, frame, 9))
            self.assertEqual((parsed.kind, parsed.seq, parsed.frame, parsed.end), ("data", 5, frame, False))
            self.assertTrue(r.parse(r.data_packet(header, 0x1234, 6, frame, 10, end=True)).end)
        self.assertEqual(len(Reflector("DCS001", "A", "N0TNT").connect_packets()[0]), 519)
        self.assertEqual(Reflector("XRF757", "A", "N0TNT").connect_packets()[0], b"N0TNT   AA\x0b")


class FakePort:
    """Radio side of a socketpair standing in for the Bluetooth gateway link."""
    def __init__(self, sock):
        self.sock, self.label = sock, "test"
        sock.setblocking(False)

    def fileno(self):
        return self.sock.fileno()

    def read(self):
        try:
            return self.sock.recv(4096)
        except BlockingIOError:
            return b""

    def write(self, data):
        self.sock.sendall(data)

    def close(self):
        self.sock.close()


class FakeReflector:
    """Minimal DExtra reflector on localhost."""
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(.05)
        self.port = self.sock.getsockname()[1]
        self.client = None
        self.received = []
        self.running = True
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while self.running:
            try:
                packet, addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            self.received.append(packet)
            if len(packet) == 11 and packet[10] == 0x0B:
                self.client = addr
                self.sock.sendto(packet[:10] + b"ACK\x00", addr)

    def send_stream(self, call="KC0ABC", message="Hello from KC", gps=""):
        r = Reflector("XRF757", "A", "XRF757")
        header = Header(rpt2="XRF757 G", rpt1="XRF757 A", my=call, suffix="ID51")
        self.sock.sendto(r.header_packet(header, 0x4242), self.client)
        frames = list(superframes(encode_message(message), gps_slow(gps) if gps else [], 84))
        for n, (seq, slow) in enumerate(frames):
            self.sock.sendto(r.data_packet(header, 0x4242, seq, dstar.NULL_AMBE + slow, n), self.client)
            time.sleep(.004)
        self.sock.sendto(r.data_packet(header, 0x4242, 84 % 21, dstar.NULL_AMBE + SYNC, 85, end=True), self.client)

    def close(self):
        self.running = False
        self.sock.close()


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        folder = Path(self.temp.name)
        self.store = Store(folder)
        self.store.set("callsign", "N0TNT-7")
        self.store.set("dstar_lookup", False)
        self.store.set("dstar_routing", False)
        self.store.set("device", {"address": "00:11:22:33:44:55"})
        self.radio_side, gateway_side = socket.socketpair()
        self.radio_side.settimeout(.05)
        self.original_port = gateway_module.RadioPort
        gateway_module.RadioPort = lambda *a, **k: FakePort(gateway_side)
        self.reflector = FakeReflector()
        self.gw = gateway_module.DStarGateway(self.store, None, cache_folder=folder)
        self.gw.hosts.table = {"XRF757": ("127.0.0.1", self.reflector.port)}
        self.gw.hosts.updated = time.time()
        self.from_gateway = []
        self.decoder = MmdvmDecoder()

    def tearDown(self):
        self.gw.close()
        gateway_module.RadioPort = self.original_port
        self.reflector.close()
        self.radio_side.close()
        self.store.close()
        self.temp.cleanup()

    def pump(self, seconds, until=None):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                data = self.radio_side.recv(4096)
            except socket.timeout:
                data = b""
            for command, payload in self.decoder.feed(data):
                self.from_gateway.append((command, payload))
                if command == GET_VERSION:
                    self.radio_side.sendall(mmdvm_frame(GET_VERSION, b"\x01TH-D75 MMDVM"))
            if until and until():
                return True
        return False

    def test_link_receive_and_transmit(self):
        self.gw.submit("start")
        self.assertTrue(self.pump(3, lambda: self.gw.snapshot()["radio"] == "Radio ready"))
        self.gw.submit("link", "XRF757", "A")
        self.assertTrue(self.pump(3, lambda: self.gw.snapshot()["link"] == "Linked"))

        gps = "$$CRC1234,KC0ABC-9>API51,DSTAR*:!3851.38N/09432.54W>On the road\r"
        threading.Thread(target=self.reflector.send_stream, kwargs={"gps": gps}, daemon=True).start()
        self.assertTrue(self.pump(6, lambda: any(c == DSTAR_EOT for c, _ in self.from_gateway)))
        commands = [c for c, _ in self.from_gateway]
        self.assertIn(DSTAR_HEADER, commands)
        self.assertGreaterEqual(commands.count(DSTAR_DATA), 80)
        header = Header.parse(next(p for c, p in self.from_gateway if c == DSTAR_HEADER))
        self.assertEqual(header.caller, "KC0ABC")
        heard = self.gw.snapshot()["heard"][0]
        self.assertEqual((heard["call"], heard["message"], heard["source"]), ("KC0ABC", "Hello from KC", "NET"))
        self.assertAlmostEqual(heard["lat"], 38.8563, places=3)
        self.assertTrue(any(s["source"] == "KC0ABC-9" for s in self.store.stations()))

        # Operator transmits from the radio: header + 42 frames + EOT.
        self.reflector.received.clear()
        own = Header(rpt2="DIRECT", rpt1="DIRECT", ur="CQCQCQ", my="N0TNT", suffix="D75")
        self.radio_side.sendall(mmdvm_frame(DSTAR_HEADER, own.pack()))
        for seq, slow in superframes(encode_message("Rig Companion test"), [], 42):
            self.radio_side.sendall(mmdvm_frame(DSTAR_DATA, dstar.NULL_AMBE + slow))
        self.radio_side.sendall(mmdvm_frame(DSTAR_EOT))
        r = Reflector("XRF757", "A", "N0TNT")
        def got_end():
            return any(len(p) == 27 and r.parse(p).end for p in self.reflector.received)
        self.assertTrue(self.pump(3, got_end))
        parsed = [r.parse(p) for p in self.reflector.received if p[:4] == b"DSVT"]
        sent_header = next(p.header for p in parsed if p.kind == "header")
        self.assertEqual((sent_header.rpt2, sent_header.rpt1, sent_header.my.strip()), ("XRF757 A", "N0TNT  A", "N0TNT"))
        data = [p for p in parsed if p.kind == "data" and not p.end]
        self.assertEqual(len(data), 42)
        self.assertEqual([p.seq for p in data[:22]], list(range(21)) + [0])
        self.assertEqual(self.gw.snapshot()["heard"][0]["message"], "Rig Companion test")

    def test_echo_command_plays_back(self):
        self.gw.submit("start")
        self.assertTrue(self.pump(3, lambda: self.gw.snapshot()["radio"] == "Radio ready"))
        self.from_gateway.clear()
        own = Header(ur="       E", my="N0TNT")
        self.radio_side.sendall(mmdvm_frame(DSTAR_HEADER, own.pack()))
        for seq, slow in superframes(encode_message("echo"), [], 30):
            self.radio_side.sendall(mmdvm_frame(DSTAR_DATA, dstar.NULL_AMBE + slow))
        self.radio_side.sendall(mmdvm_frame(DSTAR_EOT))
        self.assertTrue(self.pump(5, lambda: any(c == DSTAR_EOT for c, _ in self.from_gateway)))
        self.assertEqual([c for c, _ in self.from_gateway if c == DSTAR_DATA].__len__(), 30)
        self.assertEqual(Header.parse(next(p for c, p in self.from_gateway if c == DSTAR_HEADER)).ur.strip(), "N0TNT")


if __name__ == "__main__":
    unittest.main()
