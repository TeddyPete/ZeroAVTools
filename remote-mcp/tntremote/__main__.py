"""Command line for TNT MCP Link.

  tnt-mcp-link                     the app (LCD + keyboard)
  tnt-mcp-link --serve             the server (run by tnt-remote.service)
  tnt-mcp-link --status            server, tunnel and connection status
  tnt-mcp-link --on / --off        start (and start at boot) / stop the server
  tnt-mcp-link --pair              make a pairing code (approves Claude, ChatGPT, Codex...)
  tnt-mcp-link --url               print the connector address (https://.../mcp)
  tnt-mcp-link --tunnel KIND       ngrok | cloudflare | quick
  tnt-mcp-link --set-ngrok-token   read the ngrok authtoken from stdin
  tnt-mcp-link --install-ngrok
  tnt-mcp-link --set-url URL       Cloudflare: public address, e.g. https://cp0.example.workers.dev
  tnt-mcp-link --set-token         Cloudflare: read the tunnel token from stdin
  tnt-mcp-link --install-cloudflared
  tnt-mcp-link --revoke-all        disconnect every client
  tnt-mcp-link --log               last tool calls
  sudo tnt-mcp-link --enable-admin   one-time Admin (sudo) setup; --disable-admin removes it
  tnt-mcp-link --screens DIR       render sample LCD screens to PNG
"""
from __future__ import annotations

import argparse
import os
import json
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for flag in ("--serve", "--status", "--on", "--off", "--pair", "--set-token", "--install-cloudflared",
                 "--revoke-all", "--log", "--url", "--set-ngrok-token", "--install-ngrok",
                 "--enable-admin", "--disable-admin"):
        ap.add_argument(flag, action="store_true")
    ap.add_argument("--tunnel", choices=("ngrok", "cloudflare", "quick"))
    ap.add_argument("--set-url")
    ap.add_argument("--screens", metavar="DIR")
    a = ap.parse_args(argv)
    if a.enable_admin or a.disable_admin:   # before config: may run as root
        from . import adminsetup
        if a.enable_admin and os.geteuid() != 0:
            import getpass
            err = adminsetup.enable(getpass.getpass("password for sudo: "))
        else:
            err = adminsetup.enable() if a.enable_admin else adminsetup.disable()
        if err:
            print("failed:", err, file=sys.stderr)
            return 1
        print("Admin set up. Switch it on in Permissions > Admin (sudo) when you need it." if a.enable_admin
              else "Admin removed: the AI can no longer run commands as root.")
        return 0
    from . import config as C, control, oauth
    if a.serve:
        from .server import serve
        return serve()
    if a.status:
        st = C.read_json(C.STATUS_FILE) or {}
        cfg = C.load()
        base = cfg["public_url"] if cfg["tunnel_kind"] == "cloudflare" else st.get("public_url", "")
        print(json.dumps({"running": control.is_on(), "tunnel_kind": cfg["tunnel_kind"], "public_url": base,
                          "mcp_url": (base + "/mcp") if base else "",
                          "tunnel": st.get("tunnel"), "tunnel_detail": st.get("tunnel_detail"),
                          "token_set": bool(C.tunnel_token()), "ngrok_token_set": bool(C.ngrok_token()),
                          "cloudflared": control.cloudflared_version(), "ngrok": control.ngrok_version(),
                          "clients": [c["name"] for c in oauth.Store().summary()], "pairing": oauth.pairing_state(),
                          "allow_sudo": cfg["allow_sudo"], "recent": st.get("recent", [])[:5]}, indent=1))
        return 0
    if a.on or a.off:
        err = control.set_on(bool(a.on))
        print(err or ("on" if a.on else "off"))
        return 1 if err else 0
    if a.url:
        st = C.read_json(C.STATUS_FILE) or {}
        cfg = C.load()
        base = cfg["public_url"] if cfg["tunnel_kind"] == "cloudflare" else st.get("public_url", "")
        if not base:
            print("no address yet (is it on and connected? see --status)", file=sys.stderr)
            return 1
        print(base + "/mcp")
        return 0
    if a.tunnel:
        control.set_tunnel_kind(a.tunnel)
        print("tunnel:", a.tunnel, "(the running server switches within a few seconds)")
        return 0
    if a.set_ngrok_token:
        words = sys.stdin.read().split()
        try:
            C.set_ngrok_token(words[-1] if words else "")
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
        print("ngrok authtoken saved")
        return 0
    if a.install_ngrok:
        control.install_ngrok()
        return 0
    if a.pair:
        print(oauth.new_pairing_code(), "(valid 10 minutes, one use)")
        return 0
    if a.set_url is not None:
        cfg = C.load()
        cfg["public_url"] = a.set_url.strip().rstrip("/")
        C.save(cfg)
        print("public URL:", cfg["public_url"] or "(cleared)")
        return 0
    if a.set_token:
        tok = sys.stdin.read().strip()
        if tok.startswith("cloudflared") or " " in tok:
            tok = tok.split()[-1]
        if len(tok) < 40:
            print("that doesn't look like a tunnel token", file=sys.stderr)
            return 1
        C.set_tunnel_token(tok)
        print("tunnel token saved; the running server picks it up within a few seconds")
        return 0
    if a.install_cloudflared:
        control.install_cloudflared()
        return 0
    if a.revoke_all:
        oauth.revoke_all()
        print("all clients disconnected")
        return 0
    if a.log:
        try:
            print("".join(open(C.AUDIT_LOG).readlines()[-30:]), end="")
        except OSError:
            print("(no calls yet)")
        return 0
    if a.screens:
        from .demo import render_screens
        render_screens(a.screens)
        return 0
    from .app import main_ui
    return main_ui()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
