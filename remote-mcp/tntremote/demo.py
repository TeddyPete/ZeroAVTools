"""Render sample screens to PNG (store screenshots, checks on a PC)."""
from __future__ import annotations

import os
import time

from . import app as A, lcd as L, oauth


def render_screens(out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    A.control.is_on = lambda: True
    panel = L.Panel(headless=True)
    app = A.App(panel, None)
    app.refresh = lambda now=False: None
    while len(app.stack) > 1:      # no first-run welcome in the store screenshots
        app.pop()
    app.on = True
    app.cfg["tunnel_kind"] = "ngrok"
    now = time.time()
    app.clients = [{"id": "x", "name": "Claude", "last_auth": now - 300, "paired": now - 86400 * 3,
                    "last_used": now - 60, "kind": "oauth", "live": True},
                   {"id": "y", "name": "ChatGPT", "last_auth": now - 900, "paired": now - 86400,
                    "last_used": now - 1500, "kind": "oauth", "live": True},
                   {"id": "z", "name": "Codex", "last_auth": now - 7200, "paired": now - 7200,
                    "last_used": now - 4000, "kind": "key", "live": True}]
    app.status = {"tunnel": "connected", "tunnel_detail": "ngrok", "tunnel_kind": "ngrok",
                  "public_url": "https://your-name.ngrok-free.dev", "recent": [
        {"t": time.time() - 4, "text": "run_command"}, {"t": time.time() - 4, "text": "$ git pull && ./build.sh"},
        {"t": time.time() - 40, "text": "net_tool"}, {"t": time.time() - 41, "text": "net: find_av_gear"},
        {"t": time.time() - 300, "text": "paired: Claude"}]}
    shots = []

    def shot():
        app.draw()
        p = os.path.join(out_dir, f"lcd_{len(shots)}.png")
        panel.save(p)
        shots.append(p)

    shot()                                         # 0 home
    app.push(A.QRScreen(app, "ngrok 1/3: account", A.NGROK_SIGNUP,   # 1 setup: QR steps
                        "Scan and make a free ngrok account (or sign in). After sign-up choose Share localhost. Then press OK.",
                        lambda: None))
    shot()
    app.pop()
    app.push(A.AppPick(app))                       # 2 which AI app
    shot()
    app.pop()
    app.push(A.PairScreen(app))                    # 3 one-time code
    shot()
    app.pop()
    app.push(A.ClientsScreen(app))                 # 4 connected apps
    shot()
    app.pop()
    A.adminsetup.enabled = lambda: False
    app.push(A.PermsScreen(app))
    st = A.AdminSetupScreen(app, lambda: None)     # 5 one-time Admin setup, password hidden
    app.push(st)
    st.form.values["pw"] = "password"
    st.form.sel = 3
    shot()
    app.pop()
    app.pop()
    oauth.clear_pairing()
