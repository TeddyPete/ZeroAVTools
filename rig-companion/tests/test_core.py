import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rigzero.protocol import KissDecoder, decode_ax25, decode_tnc2, fo_frequency, inspect_packet, kiss_encode, message_frame
from rigzero.radio import Radio
from rigzero.storage import Store

FO = "FO 0,0144390000,0000600000,0,A,0,0,1,1,0,0,0,0,1,08,12,000,0,CQCQCQ,0,00"


class FakeRadio:
    def __init__(self, connection, model="TH-D75"):
        self.connection = connection
        self.model = model
        self.connection.settimeout(.05)
        self.fo, self.vm, self.rate, self.tn = FO, "VM 0,0", "AS 0", "TN 1,0"
        self.commands = []
        self.frames = []
        self.stopped = False
        self.worker = threading.Thread(target=self.run, daemon=True)
        self.worker.start()

    def run(self):
        line = bytearray()
        decoder = KissDecoder()
        while not self.stopped:
            try:
                data = self.connection.recv(4096)
                if not data:
                    break
                if data == b"\xc0\xff\xc0":
                    self.tn = "TN 0,0"
                    line.clear()
                    continue
                if self.tn == "TN 2,0":
                    self.frames.extend(decoder.feed(data))
                    continue
                for byte in data:
                    if byte != 13:
                        line.append(byte)
                        continue
                    command = line.decode("ascii", "replace")
                    line.clear()
                    self.commands.append(command)
                    if command == "ID":
                        answer = "ID " + self.model
                    elif command == "FO 0":
                        answer = self.fo
                    elif command.startswith("FO 0,"):
                        if self.vm != "VM 0,0":
                            answer = "?"
                        else:
                            self.fo, answer = command, command
                    elif command == "FQ 0":
                        answer = "FQ 0," + self.fo.split(",")[1]
                    elif command == "VM 0":
                        answer = self.vm
                    elif command.startswith("VM 0,"):
                        self.vm, answer = command, command
                    elif command == "AS":
                        answer = self.rate
                    elif command.startswith("AS "):
                        self.rate, answer = command, command
                    elif command == "TN":
                        answer = self.tn
                    elif command.startswith("TN "):
                        self.tn, answer = command, command
                    elif command == "MD 0":
                        answer = "MD 0," + self.fo.split(",")[5]
                    else:
                        answer = "?"
                    self.connection.sendall(answer.encode() + b"\r")
            except socket.timeout:
                continue
            except OSError:
                break

    def close(self):
        self.stopped = True
        self.connection.close()
        self.worker.join(1)


class CoreTest(unittest.TestCase):
    def test_memory_mode_kiss_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            worker = Radio(store)
            worker.stop_event.set()
            worker.thread.join(1)
            client, server = socket.socketpair()
            client.settimeout(.05)
            fake = FakeRadio(server)
            worker.connection = client
            worker.state.update(connected=True, identity=True)
            fake.vm = "VM 0,1"
            try:
                worker.start_kiss("local")
                worker.end_kiss()
                self.assertEqual(fake.vm, "VM 0,1")
                self.assertIsNone(store.get("recovery", None))
                # A changed VFO also restores when the handheld is put into MR.
                worker.start_kiss("ble", 145670000, 0)
                fake.vm = "VM 0,1"
                worker.end_kiss()
                self.assertEqual((fake.fo, fake.vm), (FO, "VM 0,1"))
                self.assertIsNone(store.get("recovery", None))
            finally:
                client.close()
                fake.close()
                store.close()

    def test_kiss_stream_edges(self):
        payload = bytes(range(256)) * 4
        wire = kiss_encode(payload)
        decoder = KissDecoder()
        frames = []
        for byte in wire:
            frames.extend(decoder.feed(bytes([byte])))
        self.assertEqual(frames, [(0, payload)])
        self.assertEqual(list(decoder.feed(wire + kiss_encode(b"hello"))), [(0, payload), (0, b"hello")])
        self.assertEqual(list(decoder.feed(b"\xc0\x00\xdb\x01\xc0")), [])
        self.assertEqual(list(decoder.feed(b"\xc0\x00" + b"x" * 1100 + b"\xc0")), [])
        self.assertEqual(list(decoder.feed(kiss_encode(b"recovered"))), [(0, b"recovered")])

    def test_ax25_and_native(self):
        wire = message_frame("N0CALL-7", "N0TEST-15", "Hello", "WIDE1-1,WIDE2-1")
        self.assertEqual(decode_ax25(wire), ("N0CALL-7", "APRS", "WIDE1-1,WIDE2-1", ":N0TEST-15:Hello"))
        self.assertIsNone(decode_ax25(b"garbage"))
        self.assertIsNone(decode_tnc2("ID TH-D75"))
        packet = inspect_packet("IGATE", "APRS", "", "}N0TEST-7>APRS::N0CALL-7 :Hello{42")
        self.assertEqual((packet["sender"], packet["recipient"], packet["kind"]), ("N0TEST-7", "N0CALL-7", "Message"))
        weather = inspect_packet("N0WX", "APRS", "", "_11061542c315s012g023t041r003h00b10182")
        self.assertEqual(weather["weather"]["humidity"], 100)
        self.assertEqual(weather["weather"]["pressure"], 1018.2)
        with self.assertRaises(ValueError):
            message_frame("N0CALL-16", "N0TEST", "Hello")
        changed = fo_frequency(FO, 0, 145670000)
        self.assertEqual(changed.split(",")[1], "0145670000")
        self.assertEqual(changed.split(",")[2:], FO.split(",")[2:])

    def test_persistence_and_bounds(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            store = Store(folder)
            packet = inspect_packet("N0TEST", "APRS", "", ":N0CALL   :Hello")
            first = store.record(packet)
            store.record(packet)
            store.save(first)
            self.assertEqual(len(store.rows("inbox")), 1)
            store.set("callsign", "N0CALL-7")
            store.close()
            store = Store(folder)
            self.assertEqual(store.get("callsign"), "N0CALL-7")
            self.assertEqual(store.rows("saved")[0]["text"], "Hello")
            for i in range(2010):
                store.record(inspect_packet("N0TEST", "APRS", "", ">" + str(i)))
            self.assertEqual(store.db.execute("SELECT count(*) FROM packets WHERE saved=0").fetchone()[0], 2000)
            self.assertEqual(len(store.rows("saved")), 1)
            store.close()

    def test_radio_setup_restore_and_transmit(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            store.set("callsign", "N0CALL-7")
            worker = Radio(store)
            worker.stop_event.set()
            worker.thread.join(1)
            client, server = socket.socketpair()
            client.settimeout(.05)
            fake = FakeRadio(server)
            worker.connection = client
            worker.state.update(connected=True, identity=True)
            try:
                worker.tune("145.670")
                self.assertEqual(fake.fo.split(",")[1], "0145670000")
                original = fake.fo
                worker.start_kiss("local")
                self.assertTrue(worker.state["kiss"])
                self.assertEqual(store.get("recovery")["tn"], "TN 1,0")
                with self.assertRaises(RuntimeError):
                    worker.tune("144.390")
                worker.send_message("N0TEST", "Hello")
                end = time.monotonic() + 1
                while not fake.frames and time.monotonic() < end:
                    time.sleep(.02)
                self.assertEqual(len(fake.frames), 1)
                self.assertEqual(decode_ax25(fake.frames[0][1])[0], "N0CALL-7")
                worker.end_kiss()
                self.assertFalse(worker.state["kiss"])
                self.assertEqual((fake.fo, fake.tn), (original, "TN 1,0"))
                self.assertIsNone(store.get("recovery", None))
                worker.start_kiss("ble", 145170000, 0)
                self.assertEqual(fake.fo.split(",")[1], "0145170000")
                with self.assertRaises(RuntimeError):
                    worker._remote_open("tcp", lambda _: None)
                worker.end_kiss()
                self.assertEqual(fake.fo, original)
                replies = []
                worker._remote("ble", 6, b"\x7e", replies.append)
                self.assertEqual(list(KissDecoder().feed(replies[-1])), [(6, b"\x7e\x08\x10")])
                worker._remote("ble", 6, b"\xf4\x00", replies.append)
                worker._remote("ble", 6, b"\xea" + (145670000).to_bytes(4, "big"), replies.append)
                self.assertEqual(fake.fo.split(",")[1], "0145670000")
                worker._remote("ble", 6, b"\xeb", replies.append)
                self.assertEqual((fake.fo, fake.tn), (original, "TN 1,0"))
                worker.start_kiss("local")
                # A fresh worker can recover the persisted pre-KISS state.
                worker.state.update(kiss=False, owner="")
                worker.restore()
                self.assertIsNone(store.get("recovery", None))
                self.assertEqual((fake.fo, fake.tn), (original, "TN 1,0"))
            finally:
                client.close()
                fake.close()
                store.close()


if __name__ == "__main__":
    unittest.main()

