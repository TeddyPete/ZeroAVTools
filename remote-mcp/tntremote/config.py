"""Paths and settings. Folder names come from brand.py: "tnt-remote" for
TNT MCP Link (so an upgrade from TNT Remote keeps everything), "nomad-mcp"
for Nomad MCP. Below, <app> is that name.

Everything lives in the pi user's home, so the server never needs root:

  ~/.config/<app>/settings.json   public URL, port, permission switches
  ~/.config/<app>/tunnel.token    Cloudflare tunnel token (mode 600)
  ~/.config/<app>/ngrok.yml       ngrok authtoken (mode 600)
  ~/.local/state/<app>/auth.json  OAuth clients and hashed tokens (600)
  ~/.local/state/<app>/audit.log  one line per tool call
  ~/.local/lib/<app>/cloudflared  tunnel programs (downloaded, no root)
  ~/.local/lib/<app>/ngrok
  $XDG_RUNTIME_DIR/<app>/         status.json, pairing.json, "active"
"""
from __future__ import annotations

import json
import os
import tempfile

from . import brand

HOME = os.path.expanduser("~")
CONF_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"), brand.STATE)
STATE_DIR = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.join(HOME, ".local", "state"), brand.STATE)
LIB_DIR = os.path.join(HOME, ".local", "lib", brand.STATE)
RUN_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}", brand.STATE)



def _migrate_state() -> None:
    """A renamed app (Nomad MCP -> Nomad AI Bridge) keeps its settings, sign-ins and ngrok:
    move the old folders over once, when the new ones don't exist yet."""
    old = getattr(brand, "OLD_STATE", "")
    if not old:
        return
    for new in (CONF_DIR, STATE_DIR, LIB_DIR):
        src = os.path.join(os.path.dirname(new), old)
        if os.path.isdir(src) and not os.path.exists(new):
            try:
                os.rename(src, new)
            except OSError:
                pass


_migrate_state()

SETTINGS = os.path.join(CONF_DIR, "settings.json")
TOKEN_FILE = os.path.join(CONF_DIR, "tunnel.token")
AUTH_FILE = os.path.join(STATE_DIR, "auth.json")
AUDIT_LOG = os.path.join(STATE_DIR, "audit.log")
TUNNEL_LOG = os.path.join(STATE_DIR, "cloudflared.log")
STATUS_FILE = os.path.join(RUN_DIR, "status.json")
PAIR_FILE = os.path.join(RUN_DIR, "pairing.json")
ACTIVE_FLAG = os.path.join(RUN_DIR, "active")      # other TNT apps show a badge while it exists
CLOUDFLARED = os.path.join(LIB_DIR, "cloudflared")
NGROK = os.path.join(LIB_DIR, "ngrok")
NGROK_CONF = os.path.join(CONF_DIR, "ngrok.yml")

# How the device is reached from the internet:
#   ngrok       free ngrok account, fixed https://<name>.ngrok-free.dev address (default for new devices)
#   cloudflare  Cloudflare tunnel token + your own address (workers.dev or a domain): advanced
#   quick       Cloudflare quick tunnel: no account, but the address changes on every restart
TUNNEL_KINDS = ("ngrok", "cloudflare", "quick")

SERVICE = brand.SERVICE

DEFAULTS = {
    "tunnel_kind": "",         # ngrok | cloudflare | quick ("" = decide on load, see load())
    "public_url": "",          # cloudflare only, e.g. https://cp0.example.workers.dev  (no trailing slash)
    "ngrok_url": "",           # ngrok only, optional: a reserved/custom domain. Empty = the account's free dev domain.
    "port": brand.PORT,
    "tunnel_protocol": "auto", # auto (QUIC, falls back to http2) | quic | http2
    "allow_commands": True,    # run_command / background jobs
    "allow_sudo": False,       # run_command sudo=true (root). Only switched on from the device menu.
    "allow_writes": True,      # write/edit/move/delete files
    "allow_net_tools": True,
    "default_cwd": HOME,
}


def ensure_dirs() -> None:
    for d in (CONF_DIR, STATE_DIR, LIB_DIR, RUN_DIR):
        os.makedirs(d, mode=0o700, exist_ok=True)


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(SETTINGS) as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    cfg["public_url"] = str(cfg.get("public_url") or "").rstrip("/")
    if cfg.get("tunnel_kind") not in TUNNEL_KINDS:
        # Devices set up before the tunnel choice existed have a Cloudflare token (or an
        # address): they stay on Cloudflare. Only a brand new device starts on ngrok.
        old = brand.MIGRATE_CLOUDFLARE and (tunnel_token() or cfg["public_url"])
        cfg["tunnel_kind"] = "cloudflare" if old else "ngrok"
    return cfg


def save(cfg: dict) -> None:
    ensure_dirs()
    write_json(SETTINGS, {k: cfg.get(k, v) for k, v in DEFAULTS.items()})


def write_json(path: str, data, mode: int = 0o600) -> None:
    """Atomic write, so a power cut never leaves half a file."""
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: str, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def tunnel_token() -> str:
    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip()
    except OSError:
        return ""


def set_tunnel_token(token: str) -> None:
    ensure_dirs()
    token = token.strip()
    fd = os.open(TOKEN_FILE + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    os.replace(TOKEN_FILE + ".tmp", TOKEN_FILE)


def ngrok_token() -> str:
    try:
        with open(NGROK_CONF) as f:
            for line in f:
                line = line.strip()
                if line.startswith("authtoken:"):
                    return line.split(":", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


def clean_ngrok_token(token: str) -> str:
    """The bare token, or ValueError. Accepts the whole line ngrok shows:
    "ngrok config add-authtoken <token>"."""
    token = (token or "").strip()
    if token.lower().startswith("ngrok") and "add-authtoken" in token:
        token = token.split("add-authtoken", 1)[1].strip()
    if not token or any(c.isspace() or c in "\"'\\" for c in token) or len(token) > 200:
        raise ValueError("That doesn't look like an ngrok authtoken.")
    return token


def set_ngrok_token(token: str) -> None:
    """ngrok agent config (v3 format) holding only the authtoken."""
    ensure_dirs()
    token = clean_ngrok_token(token)
    fd = os.open(NGROK_CONF + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f'version: "3"\nagent:\n  authtoken: "{token}"\n  update_check: false\n')
    os.replace(NGROK_CONF + ".tmp", NGROK_CONF)
