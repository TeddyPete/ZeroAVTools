"""Hercules-style terminal: SSH, Telnet, TCP, UDP and serial (USB-RS232).

Type a line and press Enter to send it. Esc opens the terminal menu: view
ASCII/HEX, send as ASCII/HEX, line ending, saved command strings for this
device, clear, save the log to the site report, disconnect.
Hex input looks like: 02 41 42 03  (spaces optional)."""
from __future__ import annotations

import glob
import queue
import re
import socket
import threading
import time

from . import nomadui as T
from . import store, util
from .keys import Key
from .ui import DIM, INFO, OK, WARN, BAD, Field, Form, Line, Menu, Screen, View, WHITE

ENDINGS = {"CR": "\r", "LF": "\n", "CRLF": "\r\n", "none": ""}


def serial_ports() -> list[str]:
    found = sorted(set(glob.glob("/dev/serial/by-id/*")) | set(glob.glob("/dev/ttyUSB*")) |
                   set(glob.glob("/dev/ttyACM*")))
    return found or ["/dev/ttyUSB0"]


class Conn:
    """A connection with a reader thread feeding self.rx (bytes chunks)."""

    def __init__(self):
        self.rx: queue.Queue = queue.Queue()
        self.alive = True
        self.err = ""

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while self.alive:
            try:
                data = self.read()
            except Exception as e:
                self.err = str(e) or e.__class__.__name__
                self.alive = False
                break
            if data:
                self.rx.put(data)

    def read(self) -> bytes:
        raise NotImplementedError

    def write(self, data: bytes) -> None:
        raise NotImplementedError

    def close(self) -> None:
        self.alive = False


class TcpConn(Conn):
    def __init__(self, host, port, telnet=False):
        super().__init__()
        self.sock = socket.create_connection((host, int(port)), timeout=6)
        self.sock.settimeout(0.3)
        self.telnet = telnet

    def read(self) -> bytes:
        try:
            d = self.sock.recv(4096)
        except socket.timeout:
            return b""
        if not d:
            raise EOFError("closed by the far end")
        return util.strip_telnet(d, self.sock) if self.telnet else d

    def write(self, data: bytes) -> None:
        if self.telnet:
            data = data.replace(b"\xff", b"\xff\xff")
        self.sock.sendall(data)

    def close(self) -> None:
        super().close()
        try:
            self.sock.close()
        except OSError:
            pass


class UdpConn(Conn):
    def __init__(self, host, port, local_port=0):
        super().__init__()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind(("", int(local_port or 0)))
        self.sock.settimeout(0.3)
        self.dest = (host, int(port))
        self.last_from = ""

    def read(self) -> bytes:
        try:
            d, addr = self.sock.recvfrom(65535)
        except socket.timeout:
            return b""
        self.last_from = f"{addr[0]}:{addr[1]}"
        return d

    def write(self, data: bytes) -> None:
        self.sock.sendto(data, self.dest)

    def close(self) -> None:
        super().close()
        self.sock.close()


class SshConn(Conn):
    def __init__(self, host, port, user, password):
        super().__init__()
        self.lc = util.LineClient().open_ssh(host, user, password, int(port or 22))

    def read(self) -> bytes:
        try:
            d = self.lc.chan.recv(4096)
        except socket.timeout:
            return b""
        if not d and self.lc.chan.closed:
            raise EOFError("session closed")
        return d

    def write(self, data: bytes) -> None:
        self.lc.chan.send(data)

    def close(self) -> None:
        super().close()
        self.lc.close()


class SerialConn(Conn):
    def __init__(self, dev, baud, bits, parity, stop):
        super().__init__()
        import serial
        self.ser = serial.Serial(dev, int(baud), bytesize=int(bits), parity={"None": "N", "Even": "E", "Odd": "O",
                                 "Mark": "M", "Space": "S"}[parity], stopbits=float(stop), timeout=0.2)

    def read(self) -> bytes:
        return self.ser.read(4096)

    def write(self, data: bytes) -> None:
        self.ser.write(data)

    def close(self) -> None:
        super().close()
        try:
            self.ser.close()
        except Exception:
            pass


def parse_hex(text: str) -> bytes:
    h = re.sub(r"(0x|\\x|[\s,])", "", text)
    if len(h) % 2:
        raise ValueError("odd number of hex digits")
    return bytes.fromhex(h)


def show_ascii(data: bytes) -> str:
    out = []
    for b in data:
        if b in (10, 13, 9) or 32 <= b < 127:
            out.append(chr(b))
        else:
            out.append(f"<{b:02X}>")
    return "".join(out)


class TerminalScreen(Screen):
    def __init__(self, conn: Conn, target: str, ending: str = "CR", send_hex=False, view_hex=False):
        self.conn, self.target = conn, target
        self.title = f"Term {target}"
        self.ending, self.send_hex, self.view_hex = ending, send_hex, view_hex
        self.lines: list[Line] = []
        self.partial = ""
        self.edit = ""
        self.history: list[str] = []
        self.hpos = 0
        self.scroll = 0        # lines up from the end
        self.log: list[str] = []
        self.msg = ""
        conn.start()
        self._add(f"Connected to {target}. Esc = menu", DIM)

    def _add(self, text: str, color=WHITE) -> None:
        self.lines.append(Line(text, color))
        if len(self.lines) > 3000:
            del self.lines[:500]

    def pump(self) -> None:
        while True:
            try:
                data = self.conn.rx.get_nowait()
            except queue.Empty:
                break
            stamp = time.strftime("%H:%M:%S")
            if self.view_hex:
                for i in range(0, len(data), 16):
                    chunk = data[i:i + 16]
                    self._add(f"RX {' '.join(f'{b:02X}' for b in chunk):48} {show_ascii(chunk).replace(chr(13), '.').replace(chr(10), '.')}", OK)
                self.log.append(f"{stamp} RX {data.hex(' ')}")
            else:
                text = util.ANSI.sub("", show_ascii(data)).replace("\r\n", "\n").replace("\r", "\n")
                text = self.partial + text
                parts = text.split("\n")
                self.partial = parts.pop()
                if self.lines and self.lines[-1].color == OK and getattr(self.lines[-1], "payload", None) == "partial":
                    self.lines.pop()
                for p in parts:
                    self._add(p, OK)
                if self.partial:
                    self.lines.append(Line(self.partial, OK, "partial"))
                self.log.append(f"{stamp} RX {show_ascii(data)!r}")
        if not self.conn.alive and self.conn.err:
            self._add(f"Disconnected: {self.conn.err}", BAD)
            self.log.append(f"Disconnected: {self.conn.err}")
            self.conn.err = ""

    def view(self, rows: int) -> View:
        self.pump()
        lines = self.lines
        if self.scroll:
            lines = lines[: max(1, len(lines) - self.scroll)]
        mode = f"{'HEX' if self.send_hex else 'ASC'}>{self.ending} view:{'HEX' if self.view_hex else 'ASC'}"
        if self.msg:
            hint = self.msg
        elif getattr(self, "editing", True):
            hint = f"{mode}  OK send  Esc stop typing"
        else:
            hint = "OK type  F/X scroll  Esc menu"
        return View(self.title, lines, hint, len(lines) - 1 if lines else -1, 0, self.edit,
                    editing=getattr(self, "editing", True))

    def send_line(self, text: str, send_hex: bool | None = None, ending: str | None = None) -> None:
        send_hex = self.send_hex if send_hex is None else send_hex
        ending = self.ending if ending is None else ending
        try:
            data = parse_hex(text) if send_hex else (text + ENDINGS[ending]).encode("latin-1", "replace")
            if send_hex and ENDINGS[ending]:
                data += ENDINGS[ending].encode()
        except ValueError as e:
            self.msg = f"Bad hex: {e}"
            return
        if not self.conn.alive:
            self.msg = "Not connected"
            return
        try:
            self.conn.write(data)
        except Exception as e:
            self._add(f"Send failed: {e}", BAD)
            return
        shown = " ".join(f"{b:02X}" for b in data) if send_hex or self.view_hex else show_ascii(data).rstrip("\r\n")
        self._add(f"TX {shown}", INFO)
        self.log.append(f"{time.strftime('%H:%M:%S')} TX {data!r}")

    def on_key(self, k: Key, app) -> None:
        self.msg = ""
        if not getattr(self, "editing", True):
            # move mode: scroll the output, OK/Right goes back to typing
            nav = k.nav
            if nav in ("esc", "left"):
                app.push(self.menu())
            elif nav in ("enter", "right"):
                self.editing = True
            elif nav == "up" or k.name == "pgup":
                self.scroll = min(max(0, len(self.lines) - 1), self.scroll + (6 if k.name == "pgup" else 1))
            elif nav == "down" or k.name == "pgdn":
                self.scroll = max(0, self.scroll - (6 if k.name == "pgdn" else 1))
            return
        if k.name == "esc":
            self.editing = False
        elif k.name == "enter":
            if self.edit or not self.send_hex:
                self.send_line(self.edit)
                if self.edit and (not self.history or self.history[-1] != self.edit):
                    self.history.append(self.edit)
                self.hpos = len(self.history)
                self.edit = ""
                self.scroll = 0
        elif k.name == "bksp":
            self.edit = self.edit[:-1]
        elif k.name == "del":
            self.edit = ""
        elif k.name == "up":
            if self.history:
                self.hpos = max(0, self.hpos - 1)
                self.edit = self.history[self.hpos]
        elif k.name == "down":
            if self.history:
                self.hpos = min(len(self.history), self.hpos + 1)
                self.edit = self.history[self.hpos] if self.hpos < len(self.history) else ""
        elif k.name == "pgup":
            self.scroll = min(len(self.lines) - 1, self.scroll + 6)
        elif k.name == "pgdn":
            self.scroll = max(0, self.scroll - 6)
        elif k.ctrl and k.char and k.char.lower() in "cdz]":
            # control characters (Ctrl+C etc.) go straight out
            code = ord(k.char.lower()) - 96 if k.char.isalpha() else 29
            try:
                self.conn.write(bytes([code]))
                self._add(f"TX <{code:02X}>", INFO)
            except Exception:
                pass
        elif k.char:
            self.edit += k.char

    def menu(self) -> Menu:
        def toggle(attr):
            def f(app):
                setattr(self, attr, not getattr(self, attr))
                app.pop()
            return f

        def cycle_end(app):
            keys = list(ENDINGS)
            self.ending = keys[(keys.index(self.ending) + 1) % len(keys)]

        def save_cmd(app):
            if self.edit:
                store.add_command(self.target, self.edit, self.send_hex, self.ending)
                self.msg = "Saved command"
            elif self.history:
                store.add_command(self.target, self.history[-1], self.send_hex, self.ending)
                self.msg = "Saved last sent command"
            app.pop()

        def saved(app):
            cmds = store.get_commands(self.target)
            if not cmds:
                self.msg = "No saved commands for this device"
                app.pop()
                return
            items = []
            for i, c in enumerate(cmds):
                def send(app, c=c):
                    app.pop()
                    app.pop()
                    self.send_line(c["text"], c["hex"], c["ending"])
                items.append((f"{'HEX ' if c['hex'] else ''}{c['text']}", send))
            app.push(Menu("Saved commands", items, "Enter send  Esc back"))

        def delete(app):
            cmds = store.get_commands(self.target)
            items = []
            for i, c in enumerate(cmds):
                def rm(app, i=i):
                    store.delete_command(self.target, i)
                    app.pop()
                items.append((f"Delete: {c['text']}", rm))
            if items:
                app.push(Menu("Delete a command", items))

        def clear(app):
            self.lines.clear()
            app.pop()

        def save_log(app):
            from . import reports
            path = reports.save(app.cfg, f"terminal {self.target}", "\n".join(self.log))
            self.msg = "Saved " + path.rsplit("/", 1)[-1]
            app.pop()

        def disconnect(app):
            self.conn.close()
            app.pop()
            app.pop()

        return Menu("Terminal menu", [
            ("Back to terminal", lambda app: app.pop()),
            ("Saved commands (send)", saved),
            (lambda: f"Save {'typed' if self.edit else 'last'} command", save_cmd),
            (lambda: f"View: {'HEX' if self.view_hex else 'ASCII'} (toggle)", toggle("view_hex")),
            (lambda: f"Send as: {'HEX' if self.send_hex else 'ASCII'} (toggle)", toggle("send_hex")),
            (lambda: f"Line end: {self.ending} (cycle)", cycle_end),
            ("Clear screen", clear),
            ("Save log to site report", save_log),
            ("Delete a saved command", delete),
            ("Disconnect", disconnect),
        ], "Enter pick  Esc back")

    def close(self) -> None:
        self.conn.close()


def setup(app, host: str = "", proto: str = "TCP", port: str = "") -> None:
    fields = [
        Field("proto", "Type", proto, choices=["SSH", "Telnet", "TCP", "UDP", "Serial"]),
        Field("host", "Host / IP", host),
        Field("port", "Port", port),
        Field("user", "SSH user", ""),
        Field("password", "SSH password", "", secret=True),
        Field("serial", "Serial port", serial_ports()[0], choices=serial_ports()),
        Field("baud", "Baud", "9600", choices=["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200"]),
        Field("bits", "Data bits", "8", choices=["8", "7"]),
        Field("parity", "Parity", "None", choices=["None", "Even", "Odd", "Mark", "Space"]),
        Field("stop", "Stop bits", "1", choices=["1", "2"]),
        Field("ending", "Line end", "CR", choices=list(ENDINGS)),
        Field("send", "Send as", "ASCII", choices=["ASCII", "HEX"]),
        Field("view", "View", "ASCII", choices=["ASCII", "HEX"]),
        Field("lport", "UDP local port", ""),
    ]

    def go(v, app):
        p = v["proto"]
        defaults = {"SSH": "22", "Telnet": "23", "TCP": "23", "UDP": "9"}
        port = v["port"] or defaults.get(p, "")
        try:
            if p == "Serial":
                conn = SerialConn(v["serial"], v["baud"], v["bits"], v["parity"], v["stop"])
                target = f"{v['serial'].rsplit('/', 1)[-1]}@{v['baud']}"
            elif not v["host"]:
                app.toast("Type a host or IP")
                return
            elif p == "SSH":
                with app.r.loading(f"Connecting to {v['host']}"):
                    conn = SshConn(v["host"], port, v["user"], v["password"])
                target = f"{v['host']}:{port}"
            elif p == "UDP":
                conn = UdpConn(v["host"], port, v["lport"] or 0)
                target = f"udp {v['host']}:{port}"
            else:
                with app.r.loading(f"Connecting to {v['host']}"):
                    conn = TcpConn(v["host"], port, telnet=(p == "Telnet"))
                target = f"{v['host']}:{port}"
        except Exception as e:
            app.toast(f"Connect failed: {e}")
            return
        app.pop()
        app.push(TerminalScreen(conn, target, v["ending"], v["send"] == "HEX", v["view"] == "HEX"))
        if p == "Serial":
            app.show_steps(T.StepsPopup("Serial setup", [
                "Plug a USB to RS-232 adapter into this device (FTDI or Prolific chip).",
                "Cable it to the gear's RS-232 control port. Nothing comes back? Add a null-modem "
                "adapter (swaps TX and RX) or check the pinout of 3-pin Phoenix ports.",
                "Set Baud, Data bits, Parity and Stop bits to match the gear (often 9600 8 N 1).",
                "Pick the Line end the protocol wants (CR for most AV gear)."],
                key="steps-serial", ok="OK done"))

    app.push(Form("Terminal setup", fields, go,
                  "SSH login is not saved. Serial: USB-RS232 adapter."))
