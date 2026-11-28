"""OAuth 2.1 authorization server for MCP connectors (Claude, ChatGPT, Codex).

The AI app registers itself (Dynamic Client Registration, RFC 7591), sends the
browser to /authorize with PKCE (S256), and swaps the code for tokens at
/token. To approve a connection the person types the one-time pairing code
that the CardputerZero shows on its own screen (TNT MCP Link > Pair new
connection). Nobody on the internet can get in without that code.

  * Pairing codes: 8 characters, one use, 10 minutes, 5 wrong tries and the
    code is burned.
  * Tokens are random 256-bit strings; only their SHA-256 is stored.
  * Access tokens last 1 hour; refresh tokens 60 days and rotate on each use.
  * Redirects are only allowed back to Claude (claude.ai / claude.com),
    ChatGPT (chatgpt.com connector callbacks) or localhost (Claude Code, Codex
    and other local MCP clients).
  * The authorization response carries iss (RFC 9207), which ChatGPT checks.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from urllib.parse import urlparse

from . import brand, config as C

ACCESS_TTL = 3600
REFRESH_TTL = 60 * 86400
CODE_TTL = 300
PAIR_TTL = 600
PAIR_TRIES = 5
MAX_CLIENTS = 30
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # no 0/O, 1/I/L

ALLOWED_REDIRECTS = {
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
    "https://chatgpt.com/connector_platform_oauth_redirect",
    "https://platform.openai.com/apps-manage/oauth",
}
# ChatGPT's per-connector form: https://chatgpt.com/connector/oauth/<callback id>
_PREFIXES = ("https://chatgpt.com/connector/oauth/",)
SCOPE = "device"
KEY_PREFIX = "nab_"     # access keys: nab_<32 url-safe chars>


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def redirect_ok(uri: str) -> bool:
    if uri in ALLOWED_REDIRECTS:
        return True
    for p in _PREFIXES:
        rest = uri[len(p):] if uri.startswith(p) else ""
        if rest and all(c.isalnum() or c in "-_" for c in rest):
            return True
    try:
        u = urlparse(uri)
    except ValueError:
        return False
    # Claude Code and other local clients: loopback, any port
    return u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1", "::1") and not u.fragment


# ---- pairing code (written by the LCD app, checked by the server) ---------------

def new_pairing_code() -> str:
    code = "".join(secrets.choice(ALPHABET) for _ in range(8))
    C.ensure_dirs()
    C.write_json(C.PAIR_FILE, {"hash": _h(code), "exp": time.time() + PAIR_TTL, "tries": 0})
    return code[:4] + "-" + code[4:]


def pairing_state() -> dict:
    """{"active": bool, "left": seconds} for the screens."""
    p = C.read_json(C.PAIR_FILE) or {}
    left = int(p.get("exp", 0) - time.time())
    return {"active": bool(p.get("hash")) and left > 0, "left": max(0, left),
            "used": p.get("used", 0)}


def clear_pairing() -> None:
    try:
        os.unlink(C.PAIR_FILE)
    except OSError:
        pass


_pair_lock = threading.Lock()


def check_pairing(code: str) -> tuple[bool, str]:
    """Consume the pairing code. Returns (ok, message)."""
    code = "".join(ch for ch in code.upper() if ch.isalnum())
    with _pair_lock:
        p = C.read_json(C.PAIR_FILE) or {}
        if not p.get("hash") or p.get("exp", 0) < time.time():
            return False, "No pairing code is active. On the CardputerZero open " + brand.APP_NAME + " and choose Connect an AI app, then your app."
        if hmac.compare_digest(p["hash"], _h(code)):
            # keep a marker so the device can say "accepted" instead of "used or expired"
            C.write_json(C.PAIR_FILE, {"used": time.time()})
            return True, ""
        p["tries"] = p.get("tries", 0) + 1
        if p["tries"] >= PAIR_TRIES:
            clear_pairing()
            return False, "Too many wrong codes. The code was cancelled; make a new one on the device."
        C.write_json(C.PAIR_FILE, p)
        return False, f"Wrong code. {PAIR_TRIES - p['tries']} tries left."


# ---- clients and tokens -----------------------------------------------------------

class Store:
    """auth.json: {"clients": {id: {...}}, "access": {hash: {...}}, "refresh": {hash: {...}},
    "keys": {hash: {"client": id}}}. Keys are access keys made on the device for apps that
    can't do OAuth sign-in well (Codex chats, cloud agents, scripts); they don't expire."""

    def __init__(self, path: str = ""):
        self.path = path or C.AUTH_FILE
        self.lock = threading.RLock()
        self.data = {"clients": {}, "access": {}, "refresh": {}, "keys": {}}
        self.mtime = None
        self.codes: dict[str, dict] = {}     # authorization codes, memory only
        self._load()

    def _load(self) -> None:
        try:
            m = os.stat(self.path).st_mtime
        except OSError:
            m = None
        if m == self.mtime and m is not None:
            return
        d = C.read_json(self.path) or {}
        self.data = {k: dict(d.get(k) or {}) for k in ("clients", "access", "refresh", "keys")}
        self.mtime = m

    def _save(self) -> None:
        now = time.time()
        for k in ("access", "refresh"):
            self.data[k] = {h: v for h, v in self.data[k].items() if v.get("exp", 0) > now}
        C.write_json(self.path, self.data)
        try:
            self.mtime = os.stat(self.path).st_mtime
        except OSError:
            self.mtime = None

    # clients
    def register(self, meta: dict) -> dict:
        uris = meta.get("redirect_uris") or []
        if not isinstance(uris, list) or not uris or not all(isinstance(u, str) and redirect_ok(u) for u in uris):
            raise ValueError("redirect_uris must point back to Claude, ChatGPT or localhost")
        auth_method = meta.get("token_endpoint_auth_method") or "none"
        if auth_method != "none":
            auth_method = "none"   # public client; PKCE protects the code
        with self.lock:
            self._load()
            if len(self.data["clients"]) >= MAX_CLIENTS:
                # drop the oldest client that has no live refresh token
                live = {v["client"] for v in self.data["refresh"].values()} | \
                    {v["client"] for v in self.data["keys"].values()}
                old = sorted((c for c in self.data["clients"].items() if c[0] not in live),
                             key=lambda c: c[1].get("created", 0))
                if not old:
                    raise ValueError("too many connected clients; revoke some on the device")
                del self.data["clients"][old[0][0]]
            cid = "tnt-" + secrets.token_urlsafe(18)
            name = str(meta.get("client_name") or "MCP client")[:80]
            self.data["clients"][cid] = {"name": name, "redirect_uris": uris, "created": int(time.time())}
            self._save()
        return {"client_id": cid, "client_id_issued_at": int(time.time()), "client_name": name,
                "redirect_uris": uris, "token_endpoint_auth_method": auth_method,
                "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]}

    def client(self, cid: str) -> dict | None:
        with self.lock:
            self._load()
            return self.data["clients"].get(cid)

    # authorization codes
    def new_code(self, cid: str, redirect_uri: str, challenge: str, scope: str, resource: str) -> str:
        code = secrets.token_urlsafe(32)
        with self.lock:
            now = time.time()
            self.codes = {k: v for k, v in self.codes.items() if v["exp"] > now}
            self.codes[_h(code)] = {"client": cid, "redirect_uri": redirect_uri, "challenge": challenge,
                                    "scope": scope, "resource": resource, "exp": now + CODE_TTL}
        return code

    def _issue(self, cid: str, scope: str, resource: str) -> dict:
        at, rt = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = time.time()
        self.data["access"][_h(at)] = {"client": cid, "exp": now + ACCESS_TTL, "scope": scope, "resource": resource}
        self.data["refresh"][_h(rt)] = {"client": cid, "exp": now + REFRESH_TTL, "scope": scope, "resource": resource}
        c = self.data["clients"].get(cid, {})
        c["last_auth"] = int(now)
        c.setdefault("paired", int(now))
        self._save()
        return {"access_token": at, "token_type": "Bearer", "expires_in": ACCESS_TTL,
                "refresh_token": rt, "scope": scope}

    def exchange_code(self, code: str, cid: str, redirect_uri: str, verifier: str) -> dict:
        with self.lock:
            self._load()
            c = self.codes.pop(_h(code or ""), None)
            if not c or c["exp"] < time.time():
                raise PermissionError("invalid_grant")
            if c["client"] != cid or (redirect_uri and c["redirect_uri"] != redirect_uri):
                raise PermissionError("invalid_grant")
            if not verifier or not 43 <= len(verifier) <= 128:
                raise PermissionError("invalid_grant")
            calc = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if not hmac.compare_digest(calc, c["challenge"]):
                raise PermissionError("invalid_grant")
            if cid not in self.data["clients"]:
                raise PermissionError("invalid_client")
            return self._issue(cid, c["scope"], c["resource"])

    def refresh(self, token: str, cid: str) -> dict:
        with self.lock:
            self._load()
            r = self.data["refresh"].pop(_h(token or ""), None)
            if not r or r["exp"] < time.time() or (cid and r["client"] != cid):
                self._save()
                raise PermissionError("invalid_grant")
            if r["client"] not in self.data["clients"]:
                raise PermissionError("invalid_grant")
            return self._issue(r["client"], r.get("scope", SCOPE), r.get("resource", ""))

    def revoke(self, token: str) -> None:
        with self.lock:
            self._load()
            h = _h(token or "")
            self.data["access"].pop(h, None)
            self.data["refresh"].pop(h, None)
            self._save()

    def check_access(self, token: str) -> dict | None:
        if not token:
            return None
        with self.lock:
            self._load()
            a = self.data["access"].get(_h(token)) or self.data["keys"].get(_h(token))
            if not a or a.get("exp", float("inf")) < time.time() or a["client"] not in self.data["clients"]:
                return None
            c = self.data["clients"][a["client"]]
            now = int(time.time())
            if now - c.get("last_used", 0) >= 60:      # "last used" for the device, saved at most once a minute
                c["last_used"] = now
                self._save()
            return dict(a, client_name=c.get("name", ""))

    def revoke_client(self, cid: str) -> None:
        """Forget one app: its registration and all its tokens."""
        with self.lock:
            self._load()
            self.data["clients"].pop(cid, None)
            for k in ("access", "refresh", "keys"):
                self.data[k] = {h: v for h, v in self.data[k].items() if v.get("client") != cid}
            self._save()

    def create_key(self, name: str) -> tuple[str, str]:
        """A new access key for one app, shown once: (key, client id). Listed and revocable
        like a paired app."""
        key = KEY_PREFIX + secrets.token_urlsafe(24)
        now = int(time.time())
        with self.lock:
            self._load()
            cid = "key-" + secrets.token_urlsafe(12)
            self.data["clients"][cid] = {"name": (name or "Access key")[:80], "kind": "key",
                                         "created": now, "paired": now}
            self.data["keys"][_h(key)] = {"client": cid, "scope": SCOPE}
            self._save()
        return key, cid

    def summary(self) -> list[dict]:
        """Connected clients for the device screen."""
        with self.lock:
            self._load()
            live = {}
            for v in self.data["refresh"].values():
                if v["exp"] > time.time():
                    live[v["client"]] = max(live.get(v["client"], 0), v["exp"])
            for v in self.data["keys"].values():
                live[v["client"]] = 1
            out = []
            for cid, c in self.data["clients"].items():
                if cid in live:
                    out.append({"id": cid, "name": c.get("name", ""), "last_auth": c.get("last_auth", 0),
                                "paired": c.get("paired") or c.get("created", 0),
                                "last_used": max(c.get("last_used", 0), c.get("last_auth", 0)),
                                "kind": c.get("kind", "oauth")})
            return sorted(out, key=lambda c: -max(c["last_auth"], c["paired"]))


def revoke_all(path: str = "") -> None:
    """Forget every client and token (device menu: Disconnect everyone)."""
    C.write_json(path or C.AUTH_FILE, {"clients": {}, "access": {}, "refresh": {}, "keys": {}})
