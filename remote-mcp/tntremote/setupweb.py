"""One-time setup page on the local network, for pasting the ngrok authtoken.

The token is about 50 characters, too long to type on the small keyboard, so
the ngrok setup screen shows a QR code (and short text) for
http://<device ip>:<port>/<app name>, e.g. 192.168.1.10:8776/aibridge. The
phone (on the same Wi-Fi or LAN) opens it and pastes the token.

The address is easy to guess, so the device itself is the gate:
  * The page runs inside the LCD app only while that screen is open, then stops.
  * A pasted token is NOT saved straight away. The device screen shows its last
    4 characters and the owner presses OK to use it (Esc rejects it). The
    phone shows the same 4 characters so they can be compared.
  * Any other path gets 404, and 20 wrong requests or 10 posted tokens shut
    the page down.
  * It never shows a saved token back.
"""
from __future__ import annotations

import html
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from . import brand, config as C

PORT = brand.SETUP_PORT
PATH = getattr(brand, "SETUP_PATH", "") or "".join(c for c in brand.CMD.lower() if c.isalnum())

PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{app} setup</title><style>
body{{margin:0;font-family:system-ui,sans-serif;background:#0b0e14;color:#eee}}
header{{background:#127834;padding:14px 18px;font-size:20px;font-weight:700}}
main{{padding:18px;max-width:480px;margin:auto}} a{{color:#8fc1ff}}
textarea{{font-size:16px;width:100%;box-sizing:border-box;padding:10px;border-radius:8px;border:2px solid #1e5ac8;background:#141a26;color:#fff}}
button{{margin-top:14px;width:100%;padding:14px;font-size:18px;font-weight:700;border:0;border-radius:8px;background:#1e5ac8;color:#fff}}
.err{{background:#5a0808;padding:10px;border-radius:6px}} .ok{{background:#0f4d22;padding:10px;border-radius:6px}}
b.k{{font-family:monospace;font-size:22px}} small{{color:#8899aa}}</style></head>
<body><header>{app} &middot; ngrok setup</header><main>{body}</main></body></html>"""

FORM = """<p>1. Open <a href="https://dashboard.ngrok.com/get-started/your-authtoken" target="_blank" rel="noopener">your ngrok authtoken page</a>
(sign in or sign up free first) and tap <b>Copy</b>.</p>
<p>2. Paste it here and tap Save. ngrok lists 4 steps; only <b>Add your authtoken</b> matters.
Pasting the whole <code>ngrok config add-authtoken ...</code> line is fine.</p>{msg}
<form method="post" autocomplete="off"><textarea name="token" rows="3" placeholder="paste authtoken" autocapitalize="off" spellcheck="false"></textarea>
<button type="submit">Save</button></form>
<p><small>The token stays on the CardputerZero (only the pi user can read it).</small></p>"""

WAIT = """<p class="ok">Got it. On the device screen, check that it says the token ends in
<b class="k">{end}</b>, then press <b>OK</b> there to use it.</p>
<p><small>Not your token? Press Esc on the device to reject it.</small></p>"""


def tail(token: str) -> str:
    return token[-4:]


class SetupWeb:
    def __init__(self, on_saved=None):
        self.key = PATH
        self.on_saved = on_saved          # called after the owner accepts on the device
        self.pending = ""                 # token waiting for OK on the device
        self.saved = False
        self.bad = 0
        self.posts = 0
        self.httpd: ThreadingHTTPServer | None = None
        self.error = ""
        self.lock = threading.Lock()

    def url(self, ip: str) -> str:
        return f"http://{self.short(ip)}"

    def short(self, host: str) -> str:
        """What to type in the phone's browser (it adds http:// itself)."""
        return f"{host}:{PORT}/{self.key}"

    # --- the device side ---
    def accept(self) -> None:
        """Owner pressed OK on the device: save the pending token, close the page."""
        with self.lock:
            tok, self.pending = self.pending, ""
        if not tok:
            return
        C.set_ngrok_token(tok)
        self.saved = True
        self.stop()
        if self.on_saved:
            self.on_saved()

    def reject(self) -> None:
        with self.lock:
            self.pending = ""

    def start(self) -> bool:
        owner = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, code: int, body: str):
                data = PAGE.format(app=html.escape(brand.APP_NAME), body=body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
                self.end_headers()
                self.wfile.write(data)

            def allowed(self) -> bool:
                # forgiving: phones capitalise the first letter or add a slash
                if self.path.split("?")[0].strip("/").lower() == owner.key:
                    return True
                owner.bad += 1
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                if owner.bad >= 20:
                    threading.Thread(target=owner.stop, daemon=True).start()
                return False

            def do_GET(self):
                if not self.allowed():
                    return
                if owner.saved:
                    return self.reply(200, '<p class="ok">Saved. Look at the device screen.</p>')
                if owner.pending:
                    return self.reply(200, WAIT.format(end=html.escape(tail(owner.pending))))
                self.reply(200, FORM.format(msg=""))

            def do_POST(self):
                if not self.allowed():
                    return
                n = min(int(self.headers.get("Content-Length") or 0), 4096)
                raw = (parse_qs(self.rfile.read(n).decode(errors="replace")).get("token") or [""])[0]
                try:
                    tok = C.clean_ngrok_token(raw)
                except ValueError as e:
                    return self.reply(400, FORM.format(msg=f'<p class="err">{html.escape(str(e))}</p>'))
                with owner.lock:
                    owner.posts += 1
                    too_many = owner.posts > 10
                    owner.pending = "" if too_many else tok
                if too_many:
                    threading.Thread(target=owner.stop, daemon=True).start()
                    return self.reply(429, '<p class="err">Too many tries. Open the setup step on the device again.</p>')
                self.reply(200, WAIT.format(end=html.escape(tail(tok))))

        try:
            self.httpd = ThreadingHTTPServer(("0.0.0.0", PORT), H)
        except OSError as e:
            self.error = str(e)
            return False
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True).start()
        return True

    def stop(self) -> None:
        h, self.httpd = self.httpd, None
        if h:
            h.shutdown()
            h.server_close()
