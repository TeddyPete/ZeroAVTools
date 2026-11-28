"""Linux binding for the Tab5 AX.25, YAPP and Winlink engines."""

import ctypes as c
import hashlib
from pathlib import Path
import time

EMIT = c.CFUNCTYPE(None, c.c_void_p, c.c_int, c.c_void_p, c.c_size_t)
DIGEST = c.CFUNCTYPE(c.c_int, c.c_void_p, c.c_size_t, c.c_void_p)


@DIGEST
def md5(data, count, out):
    try:
        c.memmove(out, hashlib.md5(c.string_at(data, count), usedforsecurity=False).digest(), 16)
        return 1
    except Exception:
        return 0


def milliseconds():
    return int(time.monotonic() * 1000) & 0xffffffff


class Engine:
    def __init__(self, root, files, output):
        self.lib = c.CDLL(str(Path(__file__).with_name("libpacket.so")))
        pointer, string, integer, size, stamp = c.c_void_p, c.c_char_p, c.c_int, c.c_size_t, c.c_uint32
        signatures = {
            "new": (pointer, [string, string, EMIT, pointer]),
            "free": (None, [pointer]),
            "state": (integer, [pointer]),
            "open": (integer, [pointer, string, string, string, integer, integer, stamp, DIGEST]),
            "tick": (integer, [pointer, stamp]),
            "rx": (integer, [pointer, string, size, stamp]),
            "send": (integer, [pointer, string, size]),
            "yapp": (integer, [pointer, string, string, size, stamp]),
            "accept_yapp": (None, [pointer, integer]),
            "close": (integer, [pointer, stamp]),
            "save_mail": (integer, [pointer, string, string, string, string, string, string, integer]),
            "list_mail": (integer, [pointer, string]),
        }
        for name, (result, args) in signatures.items():
            method = getattr(self.lib, "rc_" + name)
            method.restype, method.argtypes = result, args
        self.error = None

        @EMIT
        def callback(_, kind, data, count):
            try:
                output(kind, c.string_at(data, count))
            except Exception as error:
                self.error = error

        self.callback = callback
        self.handle = self.lib.rc_new(str(root).encode(), str(files).encode(), callback, None)
        if not self.handle:
            raise RuntimeError("Could not start the packet engine")

    def call(self, action, *args):
        values = tuple(value.encode() if isinstance(value, str) else value for value in args)
        result = getattr(self.lib, "rc_" + action)(self.handle, *values)
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        return result

    def require(self, action, *args, error="Packet operation failed"):
        if not self.call(action, *args):
            raise RuntimeError(error)

    def close(self):
        if self.handle:
            self.lib.rc_free(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
