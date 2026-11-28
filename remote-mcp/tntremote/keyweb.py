"""Hands a new access key to the user's computer over the local network.

An access key is about 36 characters, so nobody should type it. While the
"access key" screen is open, the device serves http://<ip>:<port>/<path> on the
LAN. A computer (or phone) that opens it waits until the owner presses OK on the
device, which shows that computer's address. Only that address then sees the
key and ready-to-paste setup for Codex, Claude Code and other MCP apps.
"""
from __future__ import annotations

import html
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import brand
from .setupweb import PATH, PORT

PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{refresh}<title>{app} access key</title><style>
body{{margin:0;font-family:system-ui,sans-serif;background:#0b0e14;color:#eee}}
header{{background:#127834;padding:14px 18px;font-size:20px;font-weight:700}}
main{{padding:18px;max-width:760px;margin:auto}} h3{{margin:22px 0 6px}}
pre{{white-space:pre-wrap;word-break:break-all;background:#141a26;border:2px solid #1e5ac8;border-radius:8px;padding:10px;margin:0;font-size:14px}}
button{{margin:6px 0 0;padding:8px 14px;font-size:15px;font-weight:700;border:0;border-radius:6px;background:#1e5ac8;color:#fff}}
.ok{{background:#0f4d22;padding:10px;border-radius:6px}} .warn{{background:#5a4208;padding:10px;border-radius:6px}}
small{{color:#8899aa}} ol{{padding-left:20px}}</style></head>
<body><header>{app} &middot; {title}</header><main>{body}</main>{script}</body></html>"""

COPY_JS = """<script nonce="{nonce}">
document.querySelectorAll('button[data-c]').forEach(function(b){{b.onclick=function(){{
var t=document.getElementById(b.dataset.c).innerText;
(navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(function(){{b.innerText='Copied';}},
function(){{var r=document.createRange();r.selectNodeContents(document.getElementById(b.dataset.c));
var s=getSelection();s.removeAllRanges();s.addRange(r);b.innerText='Selected: press Ctrl+C';}});}};}});
</script>"""


def snippets(name: str, base: str, key: str) -> list[tuple[str, str, str]]:
    """(heading, explanation, text to copy) for each kind of app, best one first."""
    url = f"{base}/k/{key}/mcp"
    slug = "nomad"
    out = [
        ("Your private address", "Works in any app that takes an MCP server address: nothing else to fill in, "
         "no sign-in. Anyone with this address can use the device, so keep it private.", url),
        ("Codex app", "Settings &gt; MCP servers &gt; Add server. Name: <b>nomad</b>, type <b>Streamable HTTP</b>, "
         "URL: the private address above. Save, then start a new chat. If an older <b>nomadmcp</b> entry is there, "
         "remove it first.", url),
        ("Codex CLI / IDE", "Run in a terminal (Codex app, CLI and IDE share this setting):",
         f'codex mcp add {slug} --url "{url}"'),
        ("Codex config.toml", "Or paste into ~/.codex/config.toml:",
         f'[mcp_servers.{slug}]\nurl = "{url}"'),
        ("Claude Code", "Run in a terminal:",
         f'claude mcp add --transport http --scope user {slug} "{url}"'),
        ("Other apps that use headers", "Address <b>" + html.escape(base) + "/mcp</b> with this header:",
         f"Authorization: Bearer {key}"),
    ]
    return out


class KeyWeb:
    def __init__(self, name: str, base: str, key: str):
        self.name, self.base, self.key = name, base, key
        self.asking = ""          # address of the computer waiting for OK on the device
        self.allowed = ""         # the one address allowed to see the key
        self.seen = False
        self.bad = 0
        self.httpd: ThreadingHTTPServer | None = None
        self.error = ""

    def url(self, ip: str) -> str:
        return f"http://{self.short(ip)}"

    def short(self, host: str) -> str:
        return f"{host}:{PORT}/{PATH}"

    def allow(self) -> None:
        self.allowed, self.asking = self.asking, ""

    def deny(self) -> None:
        self.asking = ""

    def start(self) -> bool:
        owner = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, body: str, title: str, refresh: bool = False, copy: bool = False):
                nonce = secrets.token_urlsafe(12)
                data = PAGE.format(app=html.escape(brand.APP_NAME), title=title, body=body,
                                   refresh='<meta http-equiv="refresh" content="2">' if refresh else "",
                                   script=COPY_JS.format(nonce=nonce) if copy else "").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy",
                                 f"default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-{nonce}'")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path.split("?")[0].strip("/").lower() != PATH:
                    owner.bad += 1
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    if owner.bad >= 20:
                        threading.Thread(target=owner.stop, daemon=True).start()
                    return
                ip = self.client_address[0]
                if ip == owner.allowed:
                    owner.seen = True
                    parts = [f'<p class="ok">Access key for <b>{html.escape(owner.name)}</b>. Copy the part for your '
                             'app. It appears in Connected apps on the device, where you can remove it.</p>']
                    for i, (h, why, text) in enumerate(snippets(owner.name, owner.base, owner.key)):
                        parts.append(f'<h3>{h}</h3><p><small>{why}</small></p><pre id="s{i}">{html.escape(text)}</pre>'
                                     f'<button data-c="s{i}">Copy</button>')
                    parts.append('<p><small>Close this page when done. It stops working when you leave the '
                                 'access key screen on the device.</small></p>')
                    return self.reply("".join(parts), "access key", copy=True)
                if owner.allowed:
                    return self.reply('<p class="warn">This key was already shown to another computer.</p>', "access key")
                owner.asking = ip
                self.reply(f'<p class="warn">Press <b>OK</b> on the device to show the key here.<br>'
                           f'It will say this computer is <b>{html.escape(ip)}</b>.</p>'
                           '<p><small>This page refreshes by itself.</small></p>', "access key", refresh=True)

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
