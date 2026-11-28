"""The TNT MCP Link server: OAuth + MCP on 127.0.0.1, plus the tunnel (ngrok or Cloudflare).

Run by the user service tnt-remote.service (python3 -m tntremote --serve).
Only cloudflared, running next to it, can reach the port; there is no open
port on the network.
"""
from __future__ import annotations

import html
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from . import __version__, brand, config as C, mcp, oauth

MAX_BODY = 8 * 1024 * 1024



def key_path(path: str) -> str:
    """The access key in /k/<key>/mcp, else ""."""
    parts = path.split("/")
    if len(parts) == 4 and parts[1] == "k" and parts[3] == "mcp" and parts[2].startswith(oauth.KEY_PREFIX):
        return parts[2]
    return ""

class Server:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.store = oauth.Store()
        self.started = time.time()
        self.recent: deque = deque(maxlen=8)
        self.tunnel = "off"
        self.tunnel_detail = ""
        self.tproc: subprocess.Popen | None = None
        self.live_url = ""             # ngrok / quick tunnel address, learned from the tunnel's log
        self.running_key: tuple = ()
        self.stop = threading.Event()
        self.reg_times: deque = deque(maxlen=40)
        self.lock = threading.Lock()

    # ---- status for the LCD app ----
    def activity(self, text: str, client: str = "") -> None:
        with self.lock:
            self.recent.appendleft({"t": time.time(), "text": text[:80], "client": client[:40]})
        self.write_status()

    def write_status(self) -> None:
        with self.lock:
            st = {"pid": os.getpid(), "version": __version__, "started": self.started, "port": self.cfg["port"],
                  "public_url": self.public_base(), "tunnel_kind": self.cfg.get("tunnel_kind"),
                  "tunnel": self.tunnel, "tunnel_detail": self.tunnel_detail,
                  "recent": list(self.recent), "updated": time.time()}
        try:
            C.write_json(C.STATUS_FILE, st)
        except OSError:
            pass

    # ---- tunnel (cloudflare | ngrok | quick) ----
    def tunnel_cmd(self, kind: str) -> tuple[list, dict, str]:
        """(argv, env, problem). problem is "" when the tunnel can start."""
        port = str(self.cfg["port"])
        go = {"GOMEMLIMIT": "48MiB", "GOGC": "50"}
        if kind == "ngrok":
            binary = C.NGROK if os.access(C.NGROK, os.X_OK) else (shutil_which("ngrok") or "")
            if not binary:
                return [], {}, "ngrok not installed"
            if not C.ngrok_token():
                return [], {}, "no ngrok authtoken"
            argv = [binary, "http", "127.0.0.1:" + port, "--config", C.NGROK_CONF,
                    "--log", "stdout", "--log-format", "logfmt", "--log-level", "info"]
            if self.cfg.get("ngrok_url"):
                argv += ["--url", self.cfg["ngrok_url"]]
            return argv, dict(os.environ, **go), ""
        binary = C.CLOUDFLARED if os.access(C.CLOUDFLARED, os.X_OK) else (shutil_which("cloudflared") or "")
        if not binary:
            return [], {}, "cloudflared not installed"
        proto = self.cfg.get("tunnel_protocol") or "auto"
        if kind == "quick":
            return ([binary, "tunnel", "--no-autoupdate", "--protocol", proto, "--metrics", "127.0.0.1:0",
                     "--url", "http://127.0.0.1:" + port], dict(os.environ, **go), "")
        token = C.tunnel_token()
        if not token:
            return [], {}, "no tunnel token"
        return ([binary, "tunnel", "--no-autoupdate", "--protocol", proto, "--metrics", "127.0.0.1:0", "run"],
                dict(os.environ, TUNNEL_TOKEN=token, **go), "")

    def parse_line(self, kind: str, line: str) -> None:
        if kind == "ngrok":
            # t=... lvl=info msg="started tunnel" obj=tunnels name=command_line addr=http://... url=https://x.ngrok-free.dev
            m = re.search(r"\burl=(https://[^\s\"]+)", line)
            if "started tunnel" in line and m:
                self.live_url = m.group(1).rstrip("/")
                self.tunnel, self.tunnel_detail = "connected", "ngrok"
                self.write_status()
            elif ("lvl=eror" in line or "lvl=crit" in line or "ERR_NGROK" in line) and \
                    self.tunnel_detail != "ngrok authtoken rejected":
                m = re.search(r'err="([^"]+)"', line) or re.search(r"(ERR_NGROK_\d+)", line)
                self.tunnel_detail = (m.group(1) if m else line.strip())[:120]
                if "authtoken" in line.lower() or re.search(r"ERR_NGROK_(105|107|4018)\b", line):
                    self.tunnel_detail = "ngrok authtoken rejected"
                self.write_status()
            return
        if kind == "quick":
            m = re.search(r"(https://[a-z0-9-]+\.trycloudflare\.com)", line)
            if m:
                self.live_url = m.group(1)
                self.write_status()
        if "Registered tunnel connection" in line:
            proto = re.search(r"protocol=(\w+)", line)
            self.tunnel = "connected"
            self.tunnel_detail = proto.group(1) if proto else ""
            self.write_status()
        elif " ERR " in line and self.tunnel != "connected":
            self.tunnel_detail = line.split(" ERR ", 1)[1].strip()[:120]
            self.write_status()
        elif "Unauthorized" in line or "Invalid tunnel secret" in line:
            self.tunnel_detail = "token rejected"

    def tunnel_key(self) -> tuple:
        """What the running tunnel depends on; a change restarts the tunnel (only the tunnel)."""
        kind = self.cfg.get("tunnel_kind")
        if kind == "ngrok":
            return (kind, C.ngrok_token(), self.cfg.get("ngrok_url", ""))
        if kind == "cloudflare":
            return (kind, C.tunnel_token(), self.cfg.get("tunnel_protocol"))
        return (kind, self.cfg.get("tunnel_protocol"))

    def restart_tunnel(self) -> None:
        p = self.tproc
        if p and p.poll() is None:
            p.terminate()

    def run_tunnel(self) -> None:
        backoff = 3
        while not self.stop.is_set():
            kind = self.cfg.get("tunnel_kind") or "cloudflare"
            self.running_key = self.tunnel_key()
            argv, env, problem = self.tunnel_cmd(kind)
            self.live_url = ""
            if problem:
                self.tunnel, self.tunnel_detail = "not set up", problem
                self.write_status()
                self.stop.wait(5)
                continue
            self.tunnel, self.tunnel_detail = "connecting", kind
            self.write_status()
            try:
                log = open(C.TUNNEL_LOG, "ab")
                if log.tell() > 1_000_000:
                    log.close()
                    os.replace(C.TUNNEL_LOG, C.TUNNEL_LOG + ".1")
                    log = open(C.TUNNEL_LOG, "ab")
                self.tproc = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                              stdin=subprocess.DEVNULL)
            except OSError as e:
                self.tunnel, self.tunnel_detail = "error", str(e)
                self.write_status()
                self.stop.wait(30)
                continue
            t0 = time.time()
            for raw in iter(self.tproc.stdout.readline, b""):
                log.write(raw)
                log.flush()
                self.parse_line(kind, raw.decode(errors="replace"))
                if self.tunnel == "connected":
                    backoff = 3
            self.tproc.wait()
            log.close()
            if self.stop.is_set():
                break
            self.tunnel = "reconnecting"
            self.live_url = ""
            self.write_status()
            if self.running_key != self.tunnel_key():
                continue                      # settings changed: start the new tunnel now
            if time.time() - t0 > 120:
                backoff = 3
            self.stop.wait(backoff)
            backoff = min(backoff * 2, 120)

    def public_base(self) -> str:
        """The https address people add as the connector (without /mcp)."""
        if self.cfg.get("tunnel_kind") == "cloudflare":
            return self.cfg.get("public_url", "")
        return self.live_url

    def shutdown(self) -> None:
        self.stop.set()
        if self.tproc and self.tproc.poll() is None:
            self.tproc.terminate()
            try:
                self.tproc.wait(5)
            except subprocess.TimeoutExpired:
                self.tproc.kill()
        from .tools import stop_all_jobs
        stop_all_jobs()
        for p in (C.ACTIVE_FLAG, C.STATUS_FILE):
            try:
                os.unlink(p)
            except OSError:
                pass


def shutil_which(name: str) -> str | None:
    import shutil
    return shutil.which(name)


# ---- HTTP --------------------------------------------------------------------------

PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{app}</title><style>
body{{margin:0;font-family:system-ui,sans-serif;background:#0b0e14;color:#eee}}
header{{background:#127834;padding:14px 18px;font-size:20px;font-weight:700}}
main{{padding:18px;max-width:440px;margin:auto}} .who{{color:#9fb3c8}}
input[type=text]{{font-size:26px;letter-spacing:4px;text-transform:uppercase;width:100%;box-sizing:border-box;padding:12px;border-radius:8px;border:2px solid #1e5ac8;background:#141a26;color:#fff}}
button{{margin-top:16px;width:100%;padding:14px;font-size:18px;font-weight:700;border:0;border-radius:8px;background:#1e5ac8;color:#fff}}
.err{{background:#5a0808;padding:10px;border-radius:6px}} small{{color:#8899aa}}</style></head>
<body><header>{app} &middot; CardputerZero</header><main>{body}</main></body></html>"""


def page(body: str) -> bytes:
    return PAGE.format(body=body, app=html.escape(brand.APP_NAME)).encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "TNT-Remote"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    app: Server = None   # set in serve()

    def log_message(self, fmt, *args):   # quiet; journal gets errors only
        pass

    # helpers
    def base(self) -> str:
        cfg = self.app.cfg
        pub = self.app.public_base()
        if pub:
            return pub
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or f"127.0.0.1:{cfg['port']}"
        proto = self.headers.get("X-Forwarded-Proto") or ("http" if host.startswith(("127.", "localhost")) else "https")
        return f"{proto}://{host}"

    def parse_request(self):          # one handler serves every request on a kept-alive connection
        self._body = None
        return super().parse_request()

    def send(self, code: int, body: bytes = b"", ctype: str = "application/json", headers: dict | None = None) -> None:
        self.drain()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, code: int, obj, headers: dict | None = None) -> None:
        self.send(code, json.dumps(obj).encode(), "application/json", headers)

    def body(self) -> bytes:
        if getattr(self, "_body", None) is not None:
            return self._body
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            self.close_connection = True
            raise ValueError("body too large")
        self._body = self.rfile.read(n) if n else b""
        return self._body

    def drain(self) -> None:
        """Answering before reading the body (e.g. 401) must still consume it, or the
        leftover bytes are read as the next request on this kept-alive connection."""
        if getattr(self, "_body", None) is None and self.command in ("POST", "PUT", "PATCH"):
            try:
                self.body()
            except (ValueError, OSError):
                self.close_connection = True

    def form(self) -> dict:
        raw = self.body().decode(errors="replace")
        ctype = self.headers.get("Content-Type", "")
        if "json" in ctype:
            try:
                return {k: str(v) for k, v in json.loads(raw or "{}").items()}
            except ValueError:
                return {}
        return {k: v[0] for k, v in parse_qs(raw).items()}

    def prm(self) -> dict:
        b = self.base()
        return {"resource": b + "/mcp", "authorization_servers": [b], "scopes_supported": [oauth.SCOPE],
                "bearer_methods_supported": ["header"], "resource_name": brand.APP_NAME + " (CardputerZero)"}

    def asm(self) -> dict:
        b = self.base()
        return {"issuer": b, "authorization_endpoint": b + "/authorize", "token_endpoint": b + "/token",
                "registration_endpoint": b + "/register", "revocation_endpoint": b + "/revoke",
                "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none"],
                "authorization_response_iss_parameter_supported": True,
                "revocation_endpoint_auth_methods_supported": ["none"], "scopes_supported": [oauth.SCOPE]}

    def challenge(self) -> None:
        meta = self.base() + "/.well-known/oauth-protected-resource/mcp"
        self.json(401, {"error": "invalid_token", "error_description": "sign in required"},
                  {"WWW-Authenticate": f'Bearer resource_metadata="{meta}", scope="{oauth.SCOPE}"'})

    # routes
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
            return self.json(200, self.prm())
        if path in ("/.well-known/oauth-authorization-server", "/.well-known/openid-configuration",
                    "/.well-known/oauth-authorization-server/mcp"):
            return self.json(200, self.asm())
        if path == "/authorize":
            return self.authorize_page(parse_qs(urlparse(self.path).query))
        if path == "/mcp" or key_path(path):
            return self.send(405, b"", headers={"Allow": "POST"})
        if path in ("/", "/health"):
            return self.send(200, page(f"<p>{html.escape(brand.APP_NAME)} is running. Add <b>" + html.escape(self.base()) +
                                       "/mcp</b> as a custom connector (MCP server) in Claude, ChatGPT or Codex.</p>"), "text/html; charset=utf-8")
        self.send(404, b'{"error":"not found"}')

    def do_DELETE(self):
        self.send(405, b"", headers={"Allow": "POST"})

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        try:
            if path == "/mcp":
                return self.mcp_post()
            k = key_path(path)
            if k:
                return self.mcp_post(k)
            if path == "/register":
                return self.register()
            if path == "/token":
                return self.token()
            if path == "/authorize":
                return self.authorize_post()
            if path == "/revoke":
                self.app.store.revoke(self.form().get("token", ""))
                return self.send(200, b"{}")
        except ValueError as e:
            return self.json(400, {"error": "invalid_request", "error_description": str(e)})
        self.send(404, b'{"error":"not found"}')

    # OAuth
    def register(self):
        now = time.time()
        recent = [t for t in self.app.reg_times if now - t < 3600]
        if len(recent) >= 20:
            return self.json(429, {"error": "slow_down"})
        self.app.reg_times.append(now)
        try:
            meta = json.loads(self.body() or b"{}")
            res = self.app.store.register(meta)
        except (ValueError, TypeError) as e:
            return self.json(400, {"error": "invalid_redirect_uri", "error_description": str(e)})
        self.json(201, res)

    def _auth_params(self, q: dict) -> tuple[dict, str]:
        g = {k: (v[0] if isinstance(v, list) else v) for k, v in q.items()}
        cid, ruri = g.get("client_id", ""), g.get("redirect_uri", "")
        client = self.app.store.client(cid)
        if not client:
            return g, "This connection request isn't known to the device. Remove the connector in your AI app and add it again."
        if ruri not in client["redirect_uris"]:
            return g, "The return address doesn't match. Remove the connector in your AI app and add it again."
        if g.get("response_type") != "code" or g.get("code_challenge_method") != "S256" or len(g.get("code_challenge", "")) < 43:
            return g, "This sign-in request is missing its security check (PKCE)."
        g["client_name"] = client.get("name", "")
        return g, ""

    def authorize_page(self, q: dict, msg: str = "") -> None:
        g, err = self._auth_params(q)
        if err:
            return self.send(400, page(f'<p class="err">{html.escape(err)}</p>'), "text/html; charset=utf-8")
        hidden = "".join(f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
                         for k, v in g.items() if k in ("client_id", "redirect_uri", "state", "code_challenge",
                                                         "code_challenge_method", "response_type", "scope", "resource"))
        st = oauth.pairing_state()
        info = (f"A code is showing on the device ({st['left'] // 60} min left)." if st["active"] else
                f"On the CardputerZero open <b>{html.escape(brand.APP_NAME)}</b> and choose <b>Connect an AI app</b>, then your app (Claude or ChatGPT). A code appears on its screen.")
        body = (f'<p><b class="who">{html.escape(g.get("client_name") or "An AI app")}</b> wants full control of this CardputerZero: '
                f'run commands, read and change files, use Net Tools.</p><p>{info}</p>'
                + (f'<p class="err">{html.escape(msg)}</p>' if msg else "") +
                f'<form method="post" action="/authorize" autocomplete="off">{hidden}'
                '<label>Pairing code from the device screen</label><br><br>'
                '<input type="text" name="pair_code" maxlength="9" placeholder="ABCD-EFGH" autofocus autocapitalize="characters">'
                '<button type="submit">Allow</button></form>'
                '<p><small>Only approve if you started this yourself, from your own Claude, ChatGPT or Codex account. '
                'You can disconnect everyone from the device menu at any time.</small></p>')
        self.send(200, page(body), "text/html; charset=utf-8",
                  {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self' https://claude.ai https://claude.com https://chatgpt.com https://chat.openai.com https://platform.openai.com http://localhost:* http://127.0.0.1:*; frame-ancestors 'none'"})

    def authorize_post(self):
        f = self.form()
        g, err = self._auth_params(f)
        if err:
            return self.send(400, page(f'<p class="err">{html.escape(err)}</p>'), "text/html; charset=utf-8")
        ok, msg = oauth.check_pairing(f.get("pair_code", ""))
        if not ok:
            self.app.activity("pairing: wrong code" if "Wrong" in msg else "pairing refused")
            return self.authorize_page({k: v for k, v in f.items() if k != "pair_code"}, msg)
        code = self.app.store.new_code(g["client_id"], g["redirect_uri"], g["code_challenge"],
                                       g.get("scope") or oauth.SCOPE, g.get("resource", ""))
        self.app.activity(f"paired: {g.get('client_name', '')}")
        q = {"code": code}
        if g.get("state"):
            q["state"] = g["state"]
        q["iss"] = self.base()
        sep = "&" if "?" in g["redirect_uri"] else "?"
        self.send(302, b"", "text/plain", {"Location": g["redirect_uri"] + sep + urlencode(q)})

    def token(self):
        f = self.form()
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("basic ") and not f.get("client_id"):
            import base64
            from urllib.parse import unquote
            try:
                f["client_id"] = unquote(base64.b64decode(auth[6:].strip()).decode().split(":", 1)[0])
            except (ValueError, UnicodeDecodeError):
                pass
        gt = f.get("grant_type")
        try:
            if gt == "authorization_code":
                res = self.app.store.exchange_code(f.get("code", ""), f.get("client_id", ""),
                                                   f.get("redirect_uri", ""), f.get("code_verifier", ""))
            elif gt == "refresh_token":
                res = self.app.store.refresh(f.get("refresh_token", ""), f.get("client_id", ""))
            else:
                return self.json(400, {"error": "unsupported_grant_type"})
        except PermissionError as e:
            return self.json(400, {"error": str(e)})
        self.json(200, res, {"Pragma": "no-cache"})

    # MCP
    def mcp_post(self, path_key: str = ""):
        # who is calling: OAuth access token or access key as a Bearer token, an access key in
        # X-Nomad-Key, or an access key in the address itself (https://<host>/k/<key>/mcp), which
        # works in every app that only takes a URL (cloud agents, connectors without auth)
        auth = self.headers.get("Authorization", "")
        tok = path_key or (auth[7:].strip() if auth.lower().startswith("bearer ") else "") or \
            self.headers.get("X-Nomad-Key", "").strip()
        who = self.app.store.check_access(tok)
        if not who:
            if path_key:
                self.drain()
                return self.json(401, {"error": "invalid_token",
                                       "error_description": "this access key was removed on the device"})
            return self.challenge()
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).hostname not in ("claude.ai", "claude.com", "chatgpt.com", "chat.openai.com",
                                                        "localhost", "127.0.0.1",
                                                        urlparse(self.base()).hostname):
            return self.json(403, {"error": "origin not allowed"})
        try:
            msg = json.loads(self.body() or b"null")
        except ValueError:
            return self.json(400, mcp._err(None, -32700, "parse error"))
        client = who.get("client_name") or who["client"]
        if isinstance(msg, list):
            res = [r for r in (mcp.handle(m, self.app, client) for m in msg) if r is not None]
            return self.json(200, res) if res else self.send(202, b"")
        res = mcp.handle(msg, self.app, client)
        if res is None:
            return self.send(202, b"")
        self.json(200, res)


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):   # broken pipes etc.: not worth a traceback
        pass


def serve() -> int:
    C.ensure_dirs()
    cfg = C.load()
    if (C.read_json(C.SETTINGS) or {}).get("tunnel_kind") not in C.TUNNEL_KINDS:
        C.save(cfg)                           # remember the tunnel choice made by load() (upgrade: cloudflare)
    app = Server(cfg)
    Handler.app = app
    httpd = QuietServer(("127.0.0.1", int(cfg["port"])), Handler)
    def flag():
        with open(C.ACTIVE_FLAG, "w") as fh:
            fh.write("root" if app.cfg.get("allow_sudo") else "on")

    flag()
    app.write_status()

    def reload_cfg():
        while not app.stop.wait(2):
            app.cfg = C.load()
            flag()
            if app.running_key and app.running_key != app.tunnel_key():
                app.restart_tunnel()          # tunnel choice / token changed on the device
            app.write_status()

    def bye(*_):
        app.shutdown()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    threading.Thread(target=app.run_tunnel, daemon=True).start()
    threading.Thread(target=reload_cfg, daemon=True).start()
    print(f"{brand.APP_NAME} {__version__} on 127.0.0.1:{cfg['port']}  tunnel: {cfg['tunnel_kind']}", flush=True)
    try:
        httpd.serve_forever(poll_interval=1)
    finally:
        app.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(serve())
