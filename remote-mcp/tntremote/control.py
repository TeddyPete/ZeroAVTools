"""Turn the server on/off (systemd user service) and set up the tunnel."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import urllib.request

from . import config as C

RELEASE = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{arch}"


def _systemctl(*args: str) -> tuple[int, str]:
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={env['XDG_RUNTIME_DIR']}/bus")
    try:
        p = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=30, env=env)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)


def is_on() -> bool:
    return _systemctl("is-active", "--quiet", C.SERVICE)[0] == 0


def set_on(on: bool) -> str:
    """On = start now and at every boot. Off = stop now and stay off."""
    if on:
        rc, out = _systemctl("enable", "--now", C.SERVICE)
    else:
        rc, out = _systemctl("disable", "--now", C.SERVICE)
    return "" if rc == 0 else (out or "systemctl failed")


def restart() -> str:
    if not is_on():
        return ""
    rc, out = _systemctl("restart", C.SERVICE)
    return "" if rc == 0 else out


def cloudflared_path() -> str:
    if os.access(C.CLOUDFLARED, os.X_OK):
        return C.CLOUDFLARED
    return shutil.which("cloudflared") or ""


def cloudflared_version() -> str:
    b = cloudflared_path()
    if not b:
        return ""
    try:
        out = subprocess.run([b, "--version"], capture_output=True, text=True, timeout=20).stdout
        return out.split("version", 1)[1].split()[0] if "version" in out else out.strip()[:30]
    except (OSError, subprocess.SubprocessError, IndexError):
        return "?"


def install_cloudflared(log=print) -> str:
    """Download Cloudflare's cloudflared into ~/.local/lib/tnt-remote (no root needed)."""
    arch = _arch()
    C.ensure_dirs()
    tmp = C.CLOUDFLARED + ".download"
    log("Downloading cloudflared...")
    with urllib.request.urlopen(RELEASE.format(arch=arch), timeout=60) as r, open(tmp, "wb") as f:
        total = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            total += len(chunk)
    if total < 5_000_000:
        os.unlink(tmp)
        raise RuntimeError("download looks incomplete")
    os.chmod(tmp, 0o755)
    os.replace(tmp, C.CLOUDFLARED)
    v = cloudflared_version()
    log(f"cloudflared {v} installed")
    return v


NGROK_DL = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-{arch}.tgz"


def _arch() -> str:
    m = platform.machine()
    arch = {"aarch64": "arm64", "arm64": "arm64", "armv7l": "arm", "armv6l": "arm", "x86_64": "amd64"}.get(m)
    if not arch:
        raise RuntimeError(f"unknown CPU type {m}")
    return arch


def ngrok_path() -> str:
    if os.access(C.NGROK, os.X_OK):
        return C.NGROK
    return shutil.which("ngrok") or ""


def ngrok_version() -> str:
    b = ngrok_path()
    if not b:
        return ""
    try:
        out = subprocess.run([b, "version"], capture_output=True, text=True, timeout=20).stdout
        return out.split("version", 1)[1].split()[0] if "version" in out else out.strip()[:30]
    except (OSError, subprocess.SubprocessError, IndexError):
        return "?"


def install_ngrok(log=print) -> str:
    """Download the ngrok agent into ~/.local/lib/tnt-remote (no root needed)."""
    import io
    import tarfile
    C.ensure_dirs()
    log("Downloading ngrok...")
    with urllib.request.urlopen(NGROK_DL.format(arch=_arch()), timeout=90) as r:
        data = r.read(64 << 20)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        member = next((m for m in tf.getmembers() if m.isfile() and os.path.basename(m.name) == "ngrok"), None)
        if member is None or member.size < 5_000_000:
            raise RuntimeError("download looks incomplete")
        src = tf.extractfile(member)
        tmp = C.NGROK + ".download"
        with open(tmp, "wb") as f:
            shutil.copyfileobj(src, f)
    os.chmod(tmp, 0o755)
    os.replace(tmp, C.NGROK)
    v = ngrok_version()
    log(f"ngrok {v} installed")
    return v


def mdns_name() -> str:
    """"<hostname>.local" when avahi answers for it on the LAN, else ""."""
    import socket
    try:
        active = subprocess.run(["systemctl", "is-active", "--quiet", "avahi-daemon"], timeout=3).returncode == 0
    except Exception:
        active = False
    host = socket.gethostname().split(".")[0].lower()
    return f"{host}.local" if active and host and host != "localhost" else ""


def lan_ip() -> str:
    """This device's address on the local network ("" when offline)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))      # no packet is sent; just picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


def set_tunnel_kind(kind: str) -> dict:
    """Switch tunnel type. The running server notices within 2 s and restarts only its tunnel."""
    if kind not in C.TUNNEL_KINDS:
        raise ValueError(kind)
    cfg = C.load()
    cfg["tunnel_kind"] = kind
    C.save(cfg)
    return cfg
