import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rigzero import dstar
from rigzero.dstar import DSTAR_DATA, DSTAR_EOT, DSTAR_HEADER, Header, encode_message, mmdvm_frame
from rigzero.dstar_route import (IrcDDB, RouteBook, g2_data_packet, g2_header_packet, heard_message, irc_split,
                                 parse_g2, parse_update)
from rigzero.storage import Store
import rigzero.dstar_gateway as gateway_module

import test_dstar as base
from test_dstar import superframes


class RecordingIrc(IrcDDB):
    """IrcDDB without a socket: lines are fed in by hand, sent lines are kept."""
    def __init__(self, folder):
        super().__init__("N0TNT", Path(folder) / "routes.json")
        self.sent = []

    def _send(self, line):
        self.sent.append(line)


class RouteCoreTests(unittest.TestCase):
    def test_irc_parsing(self):
        prefix, command, params = irc_split(":s-grp1s1!s-grp1s1@127.0.0.1 PRIVMSG n0tnt-4 :UPDATE 0 2026-11-28 11:41:37 W2ECR___ W2ECR__B")
        self.assertEqual((command, params[0]), ("PRIVMSG", "n0tnt-4"))
        self.assertEqual(parse_update(params[1].partition(" ")[2])[:4], (0, "2026-11-28 11:41:37", "W2ECR___", "W2ECR__B"))
        self.assertEqual(parse_update("2026-11-28 11:41:33 W2ECR___ W2ECR__B  (from: w2ecr-3)")[0], 0)
        self.assertIsNone(parse_update("LIST_END"))

    def test_lookup_flow_like_quadnet(self):
        # Lines as the QuadNet server sent them to the Cardputer on 2026-11-28.
        with tempfile.TemporaryDirectory() as folder:
            irc = RecordingIrc(folder)
            spare = ["n0tnt-2"]
            irc.nick = "n0tnt-4"
            for line in [":grp1s1.ircDDB 004 n0tnt-4 grp1s1.ircDDB InspIRCd-2.0",
                         ":n0tnt-4!n0tnt@70.59.119.172 JOIN :#dstar",
                         ":grp1s1.ircDDB 352 n0tnt-4 #dstar w2ecr 162.81.1.2 grp1s1.ircDDB w2ecr-3 H :0",
                         ":grp1s1.ircDDB 352 n0tnt-4 #dstar s-grp1s1 127.0.0.1 grp1s1.ircDDB s-grp1s1 H@ :0",
                         ":grp1s1.ircDDB 315 n0tnt-4 #dstar :End of /WHO list."]:
                irc._line(line, spare)
            self.assertEqual(irc.bot, "s-grp1s1")
            self.assertTrue(irc.ready)
            self.assertEqual(irc.sent[:2], ["JOIN #dstar", "WHO #dstar *"])
            irc.find("W2ECR")
            self.assertEqual(irc.sent[-1], "PRIVMSG s-grp1s1 :FIND W2ECR___")
            self.assertEqual(irc.result("W2ECR")[0], "wait")
            irc._line(":s-grp1s1!s-grp1s1@127.0.0.1 PRIVMSG n0tnt-4 :UPDATE 1 2026-11-28 11:41:37 W2ECR__B W2ECR___", spare)
            irc._line(":s-grp1s1!s-grp1s1@127.0.0.1 PRIVMSG n0tnt-4 :UPDATE 0 2026-11-28 11:41:37 W2ECR___ W2ECR__B", spare)
            answer, route = irc.result("W2ECR")
            self.assertEqual(answer, "ok")
            self.assertEqual((route["repeater"], route["gateway"], route["ip"]), ("W2ECR  B", "W2ECR  G", "162.81.1.2"))
            self.assertEqual(irc.lookup("W2ECR")["ip"], "162.81.1.2")     # cached now
            irc.find("W1AW")
            irc._line(":s-grp1s1!s-grp1s1@127.0.0.1 PRIVMSG n0tnt-4 :NOT_Found 0 W1AW", spare)
            self.assertEqual(irc.result("W1AW"), ("missing", None))
            irc.heard(my="N0TNT", suffix="D75", ur="CQCQCQ", rpt1="N0TNT  B", rpt2="N0TNT  G", message="Hi from Ted")
            self.assertRegex(irc.sent[-1], r"^PRIVMSG s-grp1s1 :UPDATE \d{4}-\d\d-\d\d \d\d:\d\d:\d\d N0TNT___ N0TNT__B 0 "
                                           r"N0TNT__G CQCQCQ__ 00 00 00 D75_ 00 ________ Hi_from_Ted_________$")

    def test_gateway_ip_uses_newest_nick(self):
        book = RouteBook()
        book.join("k5abc-1", "1.1.1.1")
        book.join("k5abc-3", "2.2.2.2")
        self.assertEqual(book.gateway_ip("K5ABC  G"), "2.2.2.2")
        book.leave("k5abc-3")
        self.assertEqual(book.gateway_ip("K5ABC__G"), "1.1.1.1")

    def test_g2_packets(self):
        header = Header(rpt2="W2ECR  B", rpt1="W2ECR  G", ur="W2ECR", my="N0TNT", suffix="D75")
        packet = g2_header_packet(header, 0x1234)
        self.assertEqual((len(packet), packet[4], packet[14]), (56, 0x10, 0x80))
        parsed = parse_g2(packet)
        self.assertEqual((parsed.kind, parsed.stream, parsed.header.rpt2), ("header", 0x1234, "W2ECR  B"))
        frame = bytes(range(12))
        data = parse_g2(g2_data_packet(0x1234, 7, frame))
        self.assertEqual((data.kind, data.seq, data.frame, data.end), ("data", 7, frame, False))
        self.assertTrue(parse_g2(g2_data_packet(0x1234, 8, b"", end=True)).end)


class FakeIrc:
    """Stands in for QuadNet: knows one user, answers after the first poll."""
    def __init__(self, port):
        self.port, self.asked, self.heard_lines = port, [], []
        self.callsign = "N0TNT"

    def start(self):
        pass

    def stop(self):
        pass

    def status(self):
        return {"state": "Ready", "online": 2, "nick": "n0tnt-1", "error": ""}

    def route(self, call):
        if call == "W2ECR":
            return {"call": call, "repeater": "W2ECR  B", "gateway": "W2ECR  G", "ip": "127.0.0.1",
                    "port": self.port, "stamp": ""}
        return None

    def lookup(self, call):
        self.asked.append(call)
        return None

    def find(self, call):
        self.asked.append(call)

    def result(self, call):
        route = self.route(call)
        return ("ok", route) if route else ("missing", None)

    def heard(self, **fields):
        self.heard_lines.append(fields)


class RoutingGatewayTests(base.GatewayTests):
    def setUp(self):
        super().setUp()
        self.store.set("dstar_routing", True)
        self.remote = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.remote.bind(("127.0.0.1", 0))
        self.remote.settimeout(.05)
        self.irc = FakeIrc(self.remote.getsockname()[1])
        self.original_irc, self.original_port_number = gateway_module.IrcDDB, gateway_module.G2_PORT
        gateway_module.IrcDDB = lambda *a, **k: self.irc
        gateway_module.G2_PORT = 0

    def tearDown(self):
        super().tearDown()
        gateway_module.IrcDDB, gateway_module.G2_PORT = self.original_irc, self.original_port_number
        self.remote.close()

    def talk(self, ur, frames=42):
        own = Header(rpt2="DIRECT", rpt1="DIRECT", ur=ur, my="N0TNT", suffix="D75")
        self.radio_side.sendall(mmdvm_frame(DSTAR_HEADER, own.pack()))
        for seq, slow in superframes(encode_message("Direct call test"), [], frames):
            self.radio_side.sendall(mmdvm_frame(DSTAR_DATA, dstar.NULL_AMBE + slow))
        self.radio_side.sendall(mmdvm_frame(DSTAR_EOT))

    def start(self):
        self.gw.submit("start")
        self.assertTrue(self.pump(3, lambda: self.gw.snapshot()["radio"] == "Radio ready"))
        self.assertTrue(self.gw.snapshot()["g2_port"])

    def test_urcall_routes_to_callsign_over_g2(self):
        self.start()
        self.talk("W2ECR")
        packets = []
        def ended():
            try:
                packets.append(self.remote.recvfrom(2048)[0])
            except socket.timeout:
                pass
            return any(parse_g2(p).end for p in packets)
        self.assertTrue(self.pump(4, ended))
        parsed = [parse_g2(p) for p in packets]
        headers = [p.header for p in parsed if p.kind == "header"]
        self.assertEqual(len(headers), 5)
        self.assertEqual((headers[0].rpt1, headers[0].rpt2, headers[0].ur.strip(), headers[0].my.strip()),
                         ("W2ECR  G", "W2ECR  B", "W2ECR", "N0TNT"))
        self.assertEqual(len([p for p in parsed if p.kind == "data" and not p.end]), 42)
        self.assertIn("W2ECR", self.irc.asked)
        self.assertTrue(any(h["ur"] == "W2ECR" for h in self.irc.heard_lines))

    def test_route_target_and_unknown_call(self):
        self.start()
        self.gw.route_to("K9NOPE")
        self.assertTrue(self.pump(2, lambda: self.gw.snapshot()["route_to"] == "K9NOPE"))
        self.from_gateway.clear()
        self.talk("CQCQCQ", frames=21)
        # Not found: the gateway answers on the radio's display.
        self.assertTrue(self.pump(4, lambda: any(c == DSTAR_EOT for c, _ in self.from_gateway)))
        header = Header.parse(next(p for c, p in self.from_gateway if c == DSTAR_HEADER))
        self.assertEqual(header.my.strip(), "RIGCOMP")

    def test_incoming_direct_call_plays_on_radio(self):
        self.start()
        port = self.gw.snapshot()["g2_port"]
        self.from_gateway.clear()
        header = Header(rpt2="N0TNT  B", rpt1="N0TNT  G", ur="N0TNT", my="W2ECR", suffix="ID52")
        target = ("127.0.0.1", port)
        for _ in range(5):
            self.remote.sendto(g2_header_packet(header, 0x5151), target)
        for n, (seq, slow) in enumerate(superframes(encode_message("Hi Ted"), [], 42)):
            self.remote.sendto(g2_data_packet(0x5151, seq, dstar.NULL_AMBE + slow), target)
            time.sleep(.004)
        self.remote.sendto(g2_data_packet(0x5151, 0, b"", end=True), target)
        self.assertTrue(self.pump(5, lambda: any(c == DSTAR_EOT for c, _ in self.from_gateway)))
        headers = [Header.parse(p) for c, p in self.from_gateway if c == DSTAR_HEADER]
        self.assertEqual(len(headers), 1)
        self.assertEqual(headers[0].caller, "W2ECR")
        self.assertEqual(len([c for c, _ in self.from_gateway if c == DSTAR_DATA]), 42)
        heard = self.gw.snapshot()["heard"][0]
        self.assertEqual((heard["call"], heard["message"], heard["reflector"]), ("W2ECR", "Hi Ted", "Direct call"))

    # The plain gateway tests are run by test_dstar; skip them here.
    test_link_receive_and_transmit = None
    test_echo_command_plays_back = None


if __name__ == "__main__":
    unittest.main()
