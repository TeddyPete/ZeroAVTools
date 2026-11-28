"""USB serial CAT (radio USB ports and USB-to-serial cables) with the same calls as a Bluetooth socket.

Standard library only (termios), so no extra packages. The pi user is in the
dialout group, so no root is needed.
"""
from __future__ import annotations

import os
import select
import socket
import termios
import time
from pathlib import Path

BAUDS = {1200: termios.B1200, 2400: termios.B2400, 4800: termios.B4800, 9600: termios.B9600,
         19200: termios.B19200, 38400: termios.B38400, 57600: termios.B57600, 115200: termios.B115200}


def usb_ports():
    """Plugged-in USB serial ports as device entries for the Link list."""
    ports, seen = [], set()
    by_id = Path("/dev/serial/by-id")
    if by_id.is_dir():
        for link in sorted(by_id.iterdir()):
            target = str(link.resolve())
            seen.add(target)
            name = link.name.removeprefix("usb-").split("-if")[0].replace("_", " ")
            ports.append({"usb": str(link), "address": "usb:" + link.name, "name": "USB " + name[:24],
                          "paired": True, "path": ""})
    for pattern in ("ttyACM*", "ttyUSB*"):
        for path in sorted(Path("/dev").glob(pattern)):
            if str(path) not in seen:
                ports.append({"usb": str(path), "address": "usb:" + path.name, "name": "USB " + path.name,
                              "paired": True, "path": ""})
    return ports


def usb_present(device):
    return bool(device.get("usb")) and os.path.exists(device["usb"])


class SerialPort:
    """Raw serial port that behaves like the radio's Bluetooth socket (recv/sendall/close)."""

    def __init__(self, path, baud=9600, timeout=.12, rtscts=False):
        if baud not in BAUDS:
            raise ValueError(f"Unsupported baud rate {baud}")
        self.path, self.timeout = path, timeout
        try:
            self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except PermissionError:
            raise RuntimeError("No permission for " + path + " (needs the dialout group)") from None
        except OSError as error:
            raise RuntimeError(f"Could not open {path}: {error.strerror}") from None
        attrs = termios.tcgetattr(self.fd)
        attrs[0] = 0                                    # iflag: raw
        attrs[1] = 0                                    # oflag: raw
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL | (getattr(termios, "CRTSCTS", 0) if rtscts else 0)
        attrs[3] = 0                                    # lflag: no echo, no canonical
        attrs[4] = attrs[5] = BAUDS[baud]
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        termios.tcflush(self.fd, termios.TCIOFLUSH)

    def settimeout(self, value):
        self.timeout = value

    def recv(self, size):
        if self.fd is None:
            raise ConnectionError("USB radio disconnected")
        ready, _, _ = select.select([self.fd], [], [], self.timeout)
        if not ready:
            raise socket.timeout()
        try:
            data = os.read(self.fd, size)
        except BlockingIOError:
            raise socket.timeout() from None
        except OSError as error:
            raise ConnectionError("USB radio unplugged") from error
        if not data:
            raise ConnectionError("USB radio unplugged")
        return data

    def sendall(self, data):
        view = memoryview(bytes(data))
        end = time.monotonic() + 3
        while view:
            if self.fd is None:
                raise ConnectionError("USB radio disconnected")
            _, ready, _ = select.select([], [self.fd], [], .2)
            if not ready:
                if time.monotonic() > end:
                    raise TimeoutError("USB radio not accepting data")
                continue
            try:
                sent = os.write(self.fd, view)
            except BlockingIOError:
                continue
            except OSError as error:
                raise ConnectionError("USB radio unplugged") from error
            view = view[sent:]

    def shutdown(self, how=None):
        pass

    def close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            finally:
                self.fd = None
