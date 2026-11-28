"""TNT MCP Link: the app on the CardputerZero's own screen and keyboard.

Turns remote access on and off, shows the pairing code that approves a new
AI app connection (Claude, ChatGPT, Codex), shows what it is doing, and holds the permission
switches (including Admin / sudo).
"""
from __future__ import annotations

import signal
import textwrap
import threading
import time

from . import __version__, adminsetup, brand, config as C, control, oauth
from . import lcd as L
from . import tntui as T
from .keys import Keyboard

APP_TITLE = brand.APP_NAME

if getattr(brand, "THEME", "") == "cyber":
    T.use_cyber()

HELP = (
    brand.APP_NAME + " turns this CardputerZero into an MCP server that AI apps can use from "
    "anywhere it has internet: Claude (phone app, claude.ai, Claude Code), ChatGPT "
    "(plugins) and OpenAI Codex. They can run commands, read and change "
    "files, install and run programs, and use " + brand.NET_TOOLS + ".\n"
    "It connects out through a tunnel (ngrok or Cloudflare), so no ports are opened on the "
    "network you plug into.\n"
    "To connect: Setup > Connector address shows the address (text and QR). Add it as a "
    "custom connector / MCP server in your AI app, then choose Connect an AI app here: "
    "Claude and ChatGPT sign in with a code on the Allow page; Codex, Claude Code and other "
    "apps get an access key (a private address) shown on your computer.\n"
    "While it is on, the title bar (here and in " + brand.APPS_WORD + " apps) shows an orange dot (red when Admin "
    "/ sudo is allowed). Turn Remote access Off to cut every AI app off at once; "
    "Disconnect everyone forgets all sign-ins.\n"
    "Keys: ESC goes back, hold ESC exits to HOME, number keys 4-8 do the actions shown at the "
    "bottom of the screen.\n"
    + (brand.CREDITS + "\n" if brand.CREDITS else "") +
    "\n".join(brand.DEVELOPER)
)

ABOUT = (brand.APP_NAME + "\n" + brand.TAGLINE + ".\n\n" + "\n".join(brand.DEVELOPER)
         + ("\n\n" + brand.CREDITS if brand.CREDITS else ""))


def ago(t: float) -> str:
    s = int(time.time() - t)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 172800:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def wrap(text: str, width: int = 30) -> list[str]:
    out = []
    for para in text.split("\n"):
        out += textwrap.wrap(para, width) or [""]
    return out


class Screen:
    title = APP_TITLE
    live = False   # redraw every second

    def __init__(self, app):
        self.app = app

    def rows(self) -> list:
        return []

    def hint(self) -> str:
        return "ESC back"

    def right(self) -> str:
        return ""

    def keys(self) -> list:
        """Labels for number keys 4..8 (bottom label bar); [] = plain hint bar."""
        return []

    def soft(self, i: int) -> None:
        """Number key 4..8 pressed (i = 0..4)."""

    def typing(self) -> bool:
        return False

    def nrows(self) -> int:
        return T.ROWS_KEYS if any(self.keys()) else T.ROWS

    def key(self, k) -> None:
        if T.nav_name(k.name, k.char) in T.BACK:
            self.app.pop()


class TextScreen(Screen):
    """Scrolling read-only text."""

    def __init__(self, app, title: str, lines: list, live=None):
        super().__init__(app)
        self.title, self._lines, self.top, self.fn = title, lines, 0, live
        self.live = live is not None

    def lines(self) -> list:
        return self.fn() if self.fn else self._lines

    def rows(self):
        ls = self.lines()
        n = self.nrows()
        self.top = max(0, min(self.top, len(ls) - n))
        return ls[self.top:self.top + n]

    def hint(self):
        return "↑↓ scroll  ESC back" if len(self.lines()) > self.nrows() else "ESC back"

    def key(self, k):
        n = T.nav_name(k.name, k.char)
        if n in T.UP:
            self.top -= 1
        elif n in T.DOWN:
            self.top += 1
        elif n == "pagedown":
            self.top += T.ROWS
        elif n == "pageup":
            self.top -= T.ROWS
        else:
            super().key(k)


class Confirm(Screen):
    def __init__(self, app, title: str, text: str, on_yes, color=T.WARN):
        super().__init__(app)
        self.title, self.text, self.on_yes, self.color = title, text, on_yes, color

    def rows(self):
        return [(line, self.color) for line in wrap(self.text, 30)][:T.ROWS]

    def hint(self):
        return "OK = yes   ESC = no"

    def key(self, k):
        if k.name == "enter":
            self.app.pop()
            self.on_yes()
        elif k.name == "esc" or T.nav_name(k.name, k.char) == "left":
            self.app.pop()


class FormScreen(Screen):
    def __init__(self, app, title: str, form: T.EditList):
        super().__init__(app)
        self.title, self.form = title, form

    def rows(self):
        return self.form.view(self.nrows())

    def hint(self):
        return self.form.hint()

    def typing(self):
        return self.form.editing

    def key(self, k):
        ev = self.form.handle(k)
        if ev == "back":
            self.app.pop()
        elif ev:
            self.event(ev)

    def event(self, ev: str) -> None:
        pass


class Home(FormScreen):
    live = True

    def __init__(self, app):
        fields = [T.Field("remote", "Remote access", "bool"),
                  T.Field("pair", "Connect an AI app", "action"),
                  T.Field("status", "Status & activity", "action"),
                  T.Field("clients", "Connected apps", "action"),
                  T.Field("perms", "Permissions", "action"),
                  T.Field("setup", "Setup", "action"),
                  T.Field("help", "Help", "action"),
                  T.Field("about", "About", "action")]
        super().__init__(app, APP_TITLE, T.EditList(fields, {"remote": control.is_on()}))

    def right(self):
        if not self.app.on:
            return "OFF"
        return "ROOT" if self.app.cfg.get("allow_sudo") else "ON"

    def rows(self):
        self.form.values["remote"] = self.app.on
        rows = self.form.view(self.nrows())
        for r in rows:
            if r.label == "Connected apps":
                r.value = str(len(self.app.clients))
        return rows

    def hint(self):
        return "↑↓ move  OK select  hold ESC exit"

    def keys(self):
        return ["Connect", "Status", "Apps", "Perms", "Help"]

    def soft(self, i):
        self.event("action:" + ("pair", "status", "clients", "perms", "help")[i])

    def key(self, k):
        if k.name == "esc":
            # like Rig Companion: a short ESC on the main page asks first; holding ESC exits
            def yes():
                self.app.quit = True
            on = "Remote access stays ON, so your AI apps can still reach the device." if self.app.on else ""
            self.app.push(Confirm(self.app, "Exit " + APP_TITLE + "?",
                                  ("OK exits, ESC stays. " + on).strip(), yes, T.FG))
            return
        super().key(k)

    def event(self, ev):
        app = self.app
        if ev == "changed:remote":
            want = bool(self.form.values["remote"])
            if want and not tunnel_ready(app.cfg):
                app.toast("The tunnel isn't set up yet. The server runs, but can't be reached until Setup is done.")
            err = control.set_on(want)
            if err:
                app.toast("Could not switch: " + err)
            app.refresh(now=True)
        elif ev == "action:pair":
            app.push(AppPick(app))
        elif ev == "action:status":
            app.push(TextScreen(app, "Status", [], live=app.status_lines))
        elif ev == "action:clients":
            app.push(ClientsScreen(app))
        elif ev == "action:perms":
            app.push(PermsScreen(app))
        elif ev == "action:setup":
            app.push(SetupScreen(app))
        elif ev == "action:help":
            app.push(TextScreen(app, "Help", wrap(HELP, 30) + [f"Version {__version__}"]))
        elif ev == "action:about":
            app.push(TextScreen(app, "About", wrap(ABOUT, 30) + ["", f"Version {__version__}"]))


class PairedScreen(TextScreen):
    """Shown once an AI app has signed in with the pairing code."""

    def __init__(self, app, name: str):
        super().__init__(app, "Connected!", [
            (f"{name} is paired.", T.OK), "",
            "It can now use this device.",
            "Admin (sudo) is off; switch it",
            "on in Permissions if needed."])

    def hint(self):
        return "OK done"

    def key(self, k):
        if k.name == "enter" or T.nav_name(k.name, k.char) in T.BACK:
            self.app.pop()
        else:
            super().key(k)


class PairScreen(Screen):
    """Big one-time code to type on the Allow page."""
    title = "Sign-in code"
    live = True

    def __init__(self, app, wizard: bool = False):
        super().__init__(app)
        self.wizard = wizard
        if wizard:
            self.title = "Last step: pair your app"
        self.code = oauth.new_pairing_code()
        self.since = time.time() - 1
        app.refresh(now=True)

    def paired(self) -> dict | None:
        """The client that signed in since this screen opened, if any."""
        return next((c for c in self.app.clients if c.get("last_auth", 0) >= self.since), None)

    def rows(self):
        c = self.paired()
        if c:
            app = self.app
            if self.wizard:
                while len(app.stack) > 1:
                    app.pop()
            else:
                app.pop()
            app.push(PairedScreen(app, c.get("name") or "your AI app"))
            return []
        st = oauth.pairing_state()
        if not st["active"] and st["used"] and time.time() - st["used"] < 120:
            return [("Code accepted!", T.OK), ("", T.FG), ("Finishing sign-in with", T.FG),
                    ("your AI app...", T.FG)]
        if not st["active"]:
            return [("Code used or expired.", T.WARN), ("", T.FG), ("OK = make a new code", T.FG)]
        return [("Authenticate in your app, type:" if self.wizard else "Type this on the Allow page:", T.DIM),
                "", "", "",
                (f"Valid {st['left'] // 60}:{st['left'] % 60:02d} min, one use", T.DIM),
                ("Remote access is OFF!", T.BAD) if not self.app.on else
                # free ngrok shows browsers a "You are about to visit" page once first
                ("ngrok warning page? Visit Site", T.DIM) if self.app.cfg.get("tunnel_kind") == "ngrok" else ""]

    def draw_extra(self, surf):
        if oauth.pairing_state()["active"]:
            import pygame
            f = big_font()
            T.code_box(surf, self.code, f, T.TITLE_H + T.ROW_H + 6 + f.get_height() // 2)

    def hint(self):
        return "OK new code   ESC back"


    def key(self, k):
        if k.name == "enter":
            self.code = oauth.new_pairing_code()
        else:
            super().key(k)


_big = []


def big_font():
    if not _big:
        import os
        import pygame
        path = next((p for p in T.FONT_PATHS if os.path.exists(p)), None)
        _big.append(pygame.font.Font(path, 36 if T.CYBER else 40) if path else pygame.font.Font(None, 56))
    return _big[0]


def app_badge(name: str) -> tuple:
    """Small letter badge for an AI app in lists: (text, color)."""
    n = (name or "").lower()
    if "claude code" in n:
        return (">", (200, 98, 60))
    if "claude" in n:
        return ("C", (204, 110, 74))
    if "chatgpt" in n or "openai" in n:
        return ("G", (16, 150, 120))
    if "codex" in n:
        return ("X", (90, 96, 200))
    return ("•", (100, 110, 130))


class ClientsScreen(Screen):
    """Connected apps: who, last used. OK on one shows details and Disconnect."""
    title = "Connected apps"
    live = True

    def __init__(self, app):
        super().__init__(app)
        self.sel = 0

    def rows(self):
        cl = self.app.clients
        n = len(cl) + 1                     # + Disconnect everyone
        self.sel = max(0, min(self.sel, n - 1))
        nr = self.nrows()
        top = max(0, self.sel - (nr - 1))
        rows = []
        for i, c in enumerate(cl):
            label = (c["name"] or c["id"][:12]) + (" (key)" if c.get("kind") == "key" else "")
            rows.append(T.RowView(label, "used " + ago(c["last_used"]) if c["last_used"] else "",
                                  "selected" if i == self.sel else "normal", icon=app_badge(c["name"] or "")))
        if not cl:
            rows.append(T.RowView("No apps connected", "", "normal", T.DIM))
        rows.append(T.RowView("Disconnect everyone", "", "selected" if self.sel == len(cl) else "normal", T.BAD,
                              chevron=True))
        return rows[top:top + nr]

    def hint(self):
        return "OK select  ESC back"

    def keys(self):
        return ["Info", ("Remove", T.BAD) if self.sel < len(self.app.clients) else "", "Connect"]

    def soft(self, i):
        cl = self.app.clients
        if i == 0 and self.sel < len(cl):
            self.app.push(ClientInfo(self.app, cl[self.sel]))
        elif i == 1 and self.sel < len(cl):
            ClientInfo(self.app, cl[self.sel]).ask()
        elif i == 2:
            self.app.push(AppPick(self.app))

    def key(self, k):
        n = T.nav_name(k.name, k.char)
        cl = self.app.clients
        if n in T.UP:
            self.sel -= 1
        elif n in T.DOWN:
            self.sel += 1
        elif k.name == "enter" or n == "right":
            if self.sel < len(cl):
                self.app.push(ClientInfo(self.app, cl[self.sel]))
                return

            def yes():
                oauth.revoke_all()
                self.app.refresh(now=True)
                self.app.toast("Everyone disconnected. Each AI app must pair again.")
            self.app.push(Confirm(self.app, "Disconnect everyone",
                                  "Forget every sign-in (Claude, ChatGPT, Codex...)? Each one has to pair again with a new code.", yes))
        else:
            super().key(k)


class ClientInfo(Screen):
    """One connected app."""

    def __init__(self, app, c: dict):
        super().__init__(app)
        self.c = c
        self.title = c["name"] or "AI app"

    def rows(self):
        c = self.c
        when = time.strftime("%b %d %H:%M", time.localtime(c["paired"])) if c.get("paired") else "?"
        return [("Paired:     " + when, T.FG),
                ("Last used:  " + (ago(c["last_used"]) + " ago" if c.get("last_used") else "never"), T.FG),
                ("", T.FG),
                ("OK = disconnect this app", T.BAD)]

    def hint(self):
        return "OK disconnect  ESC back"

    def ask(self, from_info: bool = False):
        def yes():
            oauth.Store().revoke_client(self.c["id"])
            if from_info:
                self.app.pop()
            self.app.refresh(now=True)
            self.app.toast(f"{self.title} disconnected. It must pair again to come back.")
        self.app.push(Confirm(self.app, "Disconnect " + self.title,
                              f"Sign {self.title} out of this device? It has to pair again with a new code.", yes))

    def key(self, k):
        if k.name == "enter":
            self.ask(True)
        else:
            super().key(k)


class PermsScreen(FormScreen):
    def __init__(self, app):
        cfg = app.cfg
        self.setup_needed = adminsetup.needed()
        self.admin_ok = adminsetup.enabled() if self.setup_needed else True
        fields = [T.Field("allow_commands", "Run commands", "bool"),
                  T.Field("allow_sudo", "Admin (sudo)", "bool", color=T.BAD),
                  T.Field("allow_writes", "Change files", "bool"),
                  T.Field("allow_net_tools", "Net Tools", "bool"),
                  T.Field("admin_remove", "Remove Admin setup", "action", color=T.WARN,
                          visible=lambda v: self.setup_needed and self.admin_ok)]
        super().__init__(app, "Permissions", T.EditList(fields, {f.key: bool(cfg.get(f.key)) for f in fields[:4]}))

    def event(self, ev):
        if ev == "action:admin_remove":
            def yes():
                err = adminsetup.disable()
                if err:
                    self.app.toast("Could not remove it: " + err)
                    return
                self.admin_ok = False
                self.form.values["allow_sudo"] = False
                self.save()
                self.app.toast("Admin setup removed. The AI can no longer run commands as root.")
            self.app.push(Confirm(self.app, "Remove Admin",
                                  "Remove the Admin (sudo) setup? Turning Admin on later asks for the password again.", yes))
            return
        if not ev.startswith("changed:"):
            return
        key = ev.split(":", 1)[1]
        if key == "allow_sudo" and self.form.values["allow_sudo"]:
            self.form.values["allow_sudo"] = False

            def yes():
                self.form.values["allow_sudo"] = True
                self.save()
                self.app.toast("Admin is ON: the AI can run commands as root. The dot in the title bar turns red.")

            def ask():
                self.app.push(Confirm(self.app, "Admin (sudo)",
                                      "Let the AI run commands as root (apt install, system settings)? Turn it off when you're done.",
                                      yes, T.BAD))
            if self.setup_needed and not self.admin_ok:
                def done():
                    self.admin_ok = True
                    ask()
                self.app.push(AdminSetupScreen(self.app, done))
            else:
                ask()
            return
        self.save()

    def save(self):
        cfg = C.load()
        cfg.update({k: v for k, v in self.form.values.items() if k.startswith("allow_")})
        C.save(cfg)
        self.app.cfg = cfg


class AdminSetupScreen(FormScreen):
    """First time Admin is switched on: the owner types the pi password once. It goes
    straight to sudo on stdin to add the rule, and is then forgotten (never saved)."""

    def __init__(self, app, on_done):
        self.on_done = on_done
        fields = [T.Field("h1", "Type the pi password once", "header"),
                  T.Field("pw", "Password", "password", maxlen=128),
                  T.Field("go", "Set up Admin", "action", color=T.BAD)]
        super().__init__(app, "Admin setup", T.EditList(fields, {"pw": ""}))
        self.form.sel = 1

    def hint(self):
        return "OK type/run  ESC cancel" if not self.form.editing else "type password, OK done"

    def keys(self):
        return [] if self.form.editing else [("Set up", T.BAD), "Cancel"]

    def soft(self, i):
        if i == 0:
            self.event("action:go")
        elif i == 1:
            self.form.values["pw"] = ""
            self.app.pop()

    EXPLAIN = ("Lets the AI run commands as root (apt install, settings) only while "
               "Admin is on. Password never saved.")

    def draw_extra(self, surf):
        f = self.app.panel.fonts["small"]
        y = T.TITLE_H + 1 + 3 * T.ROW_H + 3
        surf.fill((40, 46, 62), (6, y - 2, T.W - 12, 1))
        bottom = T.H - (T.KEYBAR_H if any(self.keys()) else T.HINT_H) - 2
        for line in QRScreen._wrap(f, self.EXPLAIN, T.W - 14):
            if y + 15 > bottom:
                break
            surf.blit(f.render(line, True, T.DIM), (7, y))
            y += 15

    def event(self, ev):
        if ev == "changed:pw" and self.form.values.get("pw"):
            self.form.sel = 2
        elif ev == "action:go":
            pw = self.form.values.get("pw") or ""
            self.form.values["pw"] = ""          # forget it right away
            if not pw:
                self.app.toast("Type the pi password first (the one you use for sudo).")
                return
            err = adminsetup.enable(pw)
            del pw
            if err:
                self.app.toast("Admin setup failed: " + err + ".")
                return
            self.app.pop()
            self.on_done()

    def key(self, k):
        ev = self.form.handle(k)
        if ev == "back":
            self.form.values["pw"] = ""
            self.app.pop()
        elif ev:
            self.event(ev)


KIND_NAMES = {"ngrok": "ngrok", "cloudflare": "Cloudflare", "quick": "Quick test"}
NGROK_SIGNUP = "https://dashboard.ngrok.com/signup"
NGROK_TOKEN_PAGE = "https://dashboard.ngrok.com/get-started/your-authtoken"


def tunnel_ready(cfg: dict) -> bool:
    kind = cfg.get("tunnel_kind")
    if kind == "cloudflare":
        return bool(C.tunnel_token())
    if kind == "ngrok":
        return bool(C.ngrok_token())
    return True


BIG = "\x01"     # QRScreen text: marks a paragraph drawn large


class QRScreen(Screen):
    """A QR code on the left, short text on the right. OK = next step (when there is one)."""
    live = True

    def __init__(self, app, title: str, url: str, text: str, on_ok=None, ok_label: str = "next"):
        super().__init__(app)
        self.title, self.url, self.text, self.on_ok, self.ok_label = title, url, text, on_ok, ok_label

    def content(self) -> tuple[str, str, tuple]:
        """(qr text, side text, side color). Subclasses make this live."""
        return self.url, self.text, T.FG

    def rows(self):
        return []

    def hint(self):
        return f"OK {self.ok_label}   ESC back" if self.on_ok else "ESC back"

    def draw_extra(self, surf):
        from . import qr
        url, text, color = self.content()
        f = self.app.panel.fonts["small"]
        top, bottom = T.TITLE_H + 3, T.H - T.HINT_H - 2
        x = 4
        if url:
            try:
                px = qr.draw(surf, url, 4, top, bottom - top)
                x = px + 12
            except Exception:
                text = url + "\n" + text
        width = T.W - x - 4
        y = top
        for para in text.split("\n"):
            # a paragraph that starts with BIG is drawn in the larger font (an address to type)
            big = para.startswith(BIG)
            font, step = (self.app.panel.fonts["row"], 22) if big else (f, 16)
            for line in self._wrap(font, para[len(BIG):] if big else para, width):
                if y + step > bottom + 2:
                    break
                surf.blit(font.render(line, True, T.FG if big else color), (x, y))
                y += step

    @staticmethod
    def _wrap(font, text: str, width: int) -> list:
        out = []
        for para in text.split("\n"):
            line = ""
            for word in para.split(" "):
                cand = (line + " " + word).strip()
                if font.size(cand)[0] <= width:
                    line = cand
                    continue
                if line:
                    out.append(line)
                line = word
                while font.size(line)[0] > width:      # long URL: break it by characters
                    i = len(line)
                    while i > 1 and font.size(line[:i])[0] > width:
                        i -= 1
                    out.append(line[:i])
                    line = line[i:]
            out.append(line)
        return out

    def key(self, k):
        if k.name == "enter" and self.on_ok:
            self.on_ok()
        else:
            super().key(k)


class ConnectorScreen(QRScreen):
    """The address to add in Claude / ChatGPT / Codex, as text and as a QR code."""
    title = "Connector address"

    def __init__(self, app, wizard: bool = False):
        super().__init__(app, "Your connector address" if wizard else "Connector address", "", "",
                         lambda: app.push(AppPick(app, wizard)), "next: your AI app" if wizard else "connect an app")

    def content(self):
        app = self.app
        st, cfg = app.status, app.cfg
        base = st.get("public_url") if st.get("tunnel_kind") == cfg.get("tunnel_kind") else ""
        if not app.on:
            return "", "Remote access is OFF. Turn it on in the main menu first.", T.BAD
        if cfg.get("tunnel_kind") == "cloudflare":
            base = cfg.get("public_url", "")
            if not base:
                return "", "No address set. Setup > Address (your workers.dev or domain address).", T.WARN
        if not base or st.get("tunnel") != "connected":
            return "", f"Waiting for the tunnel... {st.get('tunnel', '')} {st.get('tunnel_detail', '')}".strip(), T.WARN
        extra = ("\nThis test address changes every time the device restarts." if cfg.get("tunnel_kind") == "quick" else "")
        return base + "/mcp", "Add as a custom connector (MCP server) in Claude, ChatGPT or Codex:\n" + base + "/mcp" + extra, T.FG


def base_url(app) -> str:
    """The public https address of this device right now ("" while the tunnel isn't up)."""
    st, cfg = app.status, app.cfg
    if cfg.get("tunnel_kind") == "cloudflare":
        return cfg.get("public_url", "").rstrip("/")
    if st.get("tunnel") == "connected" and st.get("tunnel_kind") == cfg.get("tunnel_kind"):
        return (st.get("public_url") or "").rstrip("/")
    return ""


# Which AI app: (key, label, how it connects, steps shown before pairing)
AI_APPS = [
    ("claude", "Claude (app, web)", "pair",
     "In Claude (phone, desktop or claude.ai): Settings > Connectors > Add custom connector. Paste the "
     "connector address and click Connect. If ngrok shows 'You are about to visit', click Visit Site. "
     "On the Allow page, type the code this device shows next. Works in Claude's cloud too."),
    ("chatgpt", "ChatGPT", "pair",
     "In ChatGPT on the web: Plugins (left sidebar) > Add (top right) > Add custom MCP server. Name: "
     "Nomad AI Bridge. Connection: Server URL (not Tunnel) and paste the connector address, Authentication OAuth, "
     "tick I understand, Create as a plugin. Needs a Business, Enterprise or Edu plan (Pro: read "
     "only). "
     "If ngrok shows 'You are about to visit', click Visit Site. On the Allow page, type the code "
     "this device shows next."),
    ("codex", "Codex (app, CLI, IDE)", "key", ""),
    ("claudecode", "Claude Code", "key", ""),
    ("other", "Other MCP app", "key", ""),
]


class AppPick(FormScreen):
    """Which AI app? Claude and ChatGPT sign in with a pairing code; Codex, Claude Code and
    other apps get an access key, which works the first time in chats and cloud agents too."""

    def __init__(self, app, wizard: bool = False):
        self.wizard = wizard
        fields = [T.Field(k, label, "action") for k, label, _, _ in AI_APPS]
        super().__init__(app, "Which AI app?", T.EditList(fields, {}))

    def rows(self):
        rows = super().rows()
        for r in rows:
            r.icon = app_badge(r.label)
        return rows

    def event(self, ev):
        if not ev.startswith("action:"):
            return
        k = ev.split(":", 1)[1]
        key, label, how, steps = next(a for a in AI_APPS if a[0] == k)
        app = self.app
        if how == "pair":
            app.push(StepsScreen(app, label.split(" (")[0], steps, lambda: app.push(PairScreen(app, self.wizard))))
        else:
            app.push(KeyScreen(app, label.split(" (")[0], self.wizard))


class StepsScreen(TextScreen):
    """What to do in the AI app; OK goes on."""

    def __init__(self, app, name: str, steps: str, on_ok):
        super().__init__(app, name + ": steps", QRScreen._wrap(app.panel.fonts["row"], steps, T.W - 14))
        self.on_ok = on_ok

    def hint(self):
        return "↑↓ read  OK show code  ESC back"

    def key(self, k):
        if k.name == "enter" or T.nav_name(k.name, k.char) == "right":
            self.app.pop()
            self.on_ok()
        else:
            super().key(k)


class KeyScreen(QRScreen):
    """Makes an access key for one app and hands it to the user's computer over the LAN
    (after OK on the device), or as a QR code for a phone."""

    def __init__(self, app, name: str, wizard: bool = False):
        super().__init__(app, name + ": access key", "", "")
        from .keyweb import KeyWeb
        self.name, self.wizard = name, wizard
        self.base = base_url(app)
        self.akey = self.cid = ""
        self.show_qr = False
        self.web = None
        self.mdns = control.mdns_name()
        if self.base:
            self.akey, self.cid = oauth.Store().create_key(name)
            self.web = KeyWeb(name, self.base, self.akey)
            self.web.start()

    def shown(self) -> bool:
        return bool(self.web and (self.web.seen or self.show_qr))

    def used(self) -> dict | None:
        return next((c for c in self.app.clients if c["id"] == self.cid and c.get("last_used")), None)

    def content(self):
        if not self.base:
            return "", "The tunnel isn't connected yet, so there is no address to give out. " \
                       "Check Setup, then try again.", T.WARN
        c = self.used()
        if c:
            app = self.app
            self.close(keep=True)
            if self.wizard:
                while len(app.stack) > 1:
                    app.pop()
            else:
                app.pop()
            app.push(PairedScreen(app, self.name))
            return "", "", T.FG
        w = self.web
        if w.asking:
            return "", f"Computer {w.asking} wants this key.\n{BIG}OK = show it\nESC = no, not that one", T.WARN
        if self.show_qr:
            return f"{self.base}/k/{self.akey}/mcp", "Scan to get the private address for " + self.name + \
                ". Paste it as the MCP server URL. Waiting for " + self.name + " to connect...", T.FG
        if w.seen:
            return "", f"Shown on {w.allowed}. Paste it in {self.name} (the page says how).\n\n" \
                       f"Waiting for {self.name} to connect... ESC = done", T.OK
        ip = control.lan_ip()
        if not w.httpd or not ip:
            return "", "No local page right now. Press OK to show the key as a QR code instead.", T.WARN
        text = "On your computer (same Wi-Fi) open:\n" + BIG + ip + "\n" + BIG + w.short("")
        if self.mdns:
            text += f"\n(or {self.mdns} in place of the IP)"
        return w.url(ip), text, T.FG

    def hint(self):
        if not self.web:
            return "ESC back"
        if self.web.asking:
            return "OK show it   ESC no"
        if self.shown():
            return "ESC done"
        return "OK key as QR   ESC cancel"

    def close(self, keep: bool) -> None:
        if self.web:
            self.web.stop()
        if not keep and self.cid:
            oauth.Store().revoke_client(self.cid)        # never handed out: don't leave it behind
        self.cid = "" if not keep else self.cid

    def key(self, k):
        back = T.nav_name(k.name, k.char) in T.BACK
        w = self.web
        if w and w.asking:
            if k.name == "enter" or T.nav_name(k.name, k.char) == "right":
                w.allow()
            elif back:
                w.deny()
            return
        if k.name == "enter" and w and not self.shown():
            self.show_qr = True
            return
        if back:
            self.close(keep=self.shown())
            self.app.refresh(now=True)
            if self.wizard and self.shown():
                while len(self.app.stack) > 1:
                    self.app.pop()
                return
        super().key(k)


class TunnelPick(FormScreen):
    """Choose how the device is reached: ngrok, Cloudflare or a quick test tunnel."""

    def __init__(self, app):
        fields = [T.Field("ngrok", "ngrok (easy, free)", "action"),
                  T.Field("cloudflare", "Cloudflare (advanced)", "action"),
                  T.Field("quick", "Quick test (no account)", "action"),
                  T.Field("about", "Which one?", "action")]
        super().__init__(app, "Tunnel type", T.EditList(fields, {}))
        self.form.select(app.cfg.get("tunnel_kind", "ngrok"))

    def rows(self):
        rows = self.form.view(T.ROWS)
        cur = self.app.cfg.get("tunnel_kind")
        for r, f in zip(rows, self.form.shown()):
            if f.key == cur:
                r.value = "in use"
        return rows

    def event(self, ev):
        if not ev.startswith("action:"):
            return
        kind = ev.split(":", 1)[1]
        app = self.app
        if kind == "about":
            app.push(TextScreen(app, "Which tunnel?", wrap(
                "ngrok: make a free ngrok account (QR codes guide you). You get a fixed address like "
                "name.ngrok-free.dev that never changes. Best for most people.\n"
                "Cloudflare: for people with a Cloudflare account (tunnel token plus a workers.dev or "
                "domain address). Most control, most setup.\n"
                "Quick test: works right away with no account, but the address changes every time the "
                "device restarts, so the connector must be added again.", 30)))
            return
        cur = app.cfg.get("tunnel_kind")

        def go():
            if kind == "ngrok":
                app.pop()
                start_ngrok_wizard(app)
            elif kind == "quick":
                control.set_tunnel_kind("quick")
                app.refresh(now=True)
                app.pop()
                app.push(ConnectorScreen(app))
            else:
                control.set_tunnel_kind("cloudflare")
                app.refresh(now=True)
                app.pop()
                app.toast("Cloudflare selected. Set the Address and load the tunnel token (see Setup).")
        if kind == cur and kind != "ngrok":
            return go()
        if kind != cur and tunnel_ready(app.cfg) and app.clients:
            return app.push(Confirm(app, "Change tunnel",
                                    f"Switch from {KIND_NAMES.get(cur, cur)} to {KIND_NAMES[kind]}? The connector "
                                    "address changes, so every AI app has to add the new address and pair again.", go))
        go()


def start_ngrok_wizard(app, again: bool = False) -> None:
    """3 QR steps: make an account, open the authtoken page, paste the token on the phone."""
    def step3():
        app.push(NgrokTokenScreen(app))

    def step2():
        app.push(QRScreen(app, "ngrok 2/3: authtoken", NGROK_TOKEN_PAGE,
                          "Scan it. Of ngrok's 4 steps only step 2 matters: copy the token after 'ngrok config add-authtoken'. Then press OK.", step3))
    if C.ngrok_token() and control.ngrok_path() and not again:
        # already set up: just switch over and show the address
        control.set_tunnel_kind("ngrok")
        app.refresh(now=True)
        app.push(ConnectorScreen(app))
        return
    app.push(QRScreen(app, "ngrok 1/3: account", NGROK_SIGNUP,
                      "Scan and make a free ngrok account (or sign in). After sign-up choose Share localhost. Then press OK.", step2))


class WelcomeScreen(Screen):
    """First start with no tunnel set up: what the app does and how, then a
    pre-selected [ Start setup ] button. OK goes straight to the ngrok account step."""
    title = brand.APP_NAME if len(brand.APP_NAME) > 12 else "Welcome to " + brand.APP_NAME

    ABOUT = (
        "{app} lets your AI (Claude, ChatGPT, Codex) connect to and control this CardputerZero from anywhere: "
        "run commands, change files, install programs and run network checks.\n"
        "How: the device connects out through a secure ngrok tunnel, so no ports are opened "
        "on the network. Each AI app must be approved with a one-time code shown on this "
        "screen, and you can switch it off or disconnect everyone at any time.\n"
        "Setup takes a free ngrok account and your phone: 3 QR steps, then you pair your AI app."
    ) + ("\n" + brand.CREDITS if brand.CREDITS else "")

    def __init__(self, app):
        super().__init__(app)
        self.top = 0

    def lines(self) -> list:
        if not hasattr(self, "_lines"):
            font = self.app.panel.fonts["row"]
            self._lines = QRScreen._wrap(font, self.ABOUT.format(app=brand.APP_NAME), T.W - 14)
        return self._lines

    def rows(self):
        n = T.ROWS - 1                                  # last row is the button
        ls = self.lines()
        self.top = max(0, min(self.top, len(ls) - n))
        rows = [T.RowView(l, "", "normal", T.FG) for l in ls[self.top:self.top + n]]
        rows += [T.RowView("", "", "normal")] * (n - len(rows))
        rows.append(T.RowView("Start setup", "OK", "selected", T.FG, chevron=True))
        return rows

    def hint(self):
        return "↑↓ read more  OK start  ESC later"

    def key(self, k):
        n = T.nav_name(k.name, k.char)
        if k.name == "enter" or n == "right":
            app = self.app
            app.pop()
            app.wizard = True
            start_ngrok_wizard(app, again=True)
        elif n in T.UP:
            self.top -= 1
        elif n in T.DOWN:
            self.top += 1
        else:
            super().key(k)


class NgrokTokenScreen(QRScreen):
    """Step 3: a page on this device where the phone pastes the token."""

    def __init__(self, app):
        super().__init__(app, "ngrok 3/3: paste token", "", "", self.type_it, "type it instead")
        from .setupweb import SetupWeb
        self.web = SetupWeb(on_saved=self.saved)
        self.mdns = control.mdns_name()
        self.done = False
        self.install = "" if control.ngrok_path() else "installing"
        if self.install:
            threading.Thread(target=self._install, daemon=True).start()
        self.web.start()

    def _install(self):
        try:
            control.install_ngrok(lambda m: None)
            self.install = ""
        except Exception as e:
            self.install = f"ngrok download failed: {e}"

    def saved(self):
        self.done = True

    def content(self):
        if self.done and not self.install:
            app = self.app
            self.web.stop()
            control.set_tunnel_kind("ngrok")
            if app.wizard:
                # first-run setup: switch remote access on and go straight to the address, then pairing
                if not control.is_on():
                    control.set_on(True)
                app.wizard = False
                while len(app.stack) > 1:
                    app.pop()
                app.refresh(now=True)
                app.push(ConnectorScreen(app, wizard=True))
                return "", "", T.FG
            app.pop()
            app.refresh(now=True)
            app.push(ConnectorScreen(app))
            return "", "", T.FG
        if self.install.startswith("ngrok download failed"):
            return "", self.install, T.BAD
        if self.done:
            return "", "Token saved. Downloading ngrok...", T.WARN
        if self.web.pending:
            # a phone sent a token: the owner must confirm it here
            return "", ("A token came in from the setup page. It ends in:\n" + BIG + "..." +
                        self.web.pending[-4:] + "\nThe phone shows the same 4. Use it only if they match."), T.WARN
        ip = control.lan_ip()
        if not self.web.httpd or not ip:
            return "", ("No network address yet. " if not ip else f"Setup page failed: {self.web.error}. ") + \
                "Press OK to type the token instead.", T.WARN
        text = "Phone on the same Wi-Fi: scan it, or type:\n" + BIG + ip + "\n" + \
            BIG + self.web.short("")
        if self.mdns:
            text += f"\n(or {self.mdns} in place of the IP)"
        return self.web.url(ip), text, T.FG

    def type_it(self):
        scr = FormScreen(self.app, "ngrok authtoken", T.EditList(
            [T.Field("tok", "Token", "text", maxlen=200, help="paste or type, OK saves")], {"tok": ""}))

        def ev(e, scr=scr):
            if e != "changed:tok":
                return
            try:
                C.set_ngrok_token(scr.form.values["tok"])
            except ValueError as err:
                return self.app.toast(str(err))
            self.done = True
            self.app.pop()
        scr.event = ev
        self.app.push(scr)

    def hint(self):
        if self.web.pending and not self.done:
            return "OK use it   ESC reject"
        return super().hint()

    def key(self, k):
        back = T.nav_name(k.name, k.char) in T.BACK
        if self.web.pending and not self.done:
            if k.name == "enter" or T.nav_name(k.name, k.char) == "right":
                try:
                    self.web.accept()
                except ValueError as err:
                    self.app.toast(str(err))
            elif back:
                self.web.reject()
            return
        if back:
            self.web.stop()
        super().key(k)


class SetupScreen(FormScreen):
    live = True

    def __init__(self, app):
        cf = lambda v: app.cfg.get("tunnel_kind") == "cloudflare"
        fields = [T.Field("kind", "Tunnel", "action"),
                  T.Field("addr", "Connector address", "action"),
                  T.Field("ngrok", "ngrok setup", "action", visible=lambda v: app.cfg.get("tunnel_kind") == "ngrok"),
                  T.Field("public_url", "Address", "text", maxlen=120, help="https://...", visible=cf),
                  T.Field("token", "Tunnel token", "action", visible=cf),
                  T.Field("cf", "cloudflared", "action", visible=lambda v: app.cfg.get("tunnel_kind") != "ngrok"),
                  T.Field("log", "Recent tool calls", "action")]
        super().__init__(app, "Setup", T.EditList(fields, {"public_url": app.cfg.get("public_url", "")}))
        self.cfv = control.cloudflared_version()

    def rows(self):
        rows = self.form.view(T.ROWS)
        for r in rows:
            if r.label == "Tunnel":
                r.value = KIND_NAMES.get(self.app.cfg.get("tunnel_kind"), "?")
            elif r.label == "Tunnel token":
                r.value = "set" if C.tunnel_token() else "missing"
            elif r.label == "ngrok setup":
                r.value = "done" if C.ngrok_token() else "start"
            elif r.label == "cloudflared":
                r.value = self.cfv or "install"
        return rows

    def event(self, ev):
        app = self.app
        if ev == "action:kind":
            app.push(TunnelPick(app))
        elif ev == "action:addr":
            app.push(ConnectorScreen(app))
        elif ev == "action:ngrok":
            start_ngrok_wizard(app, again=True)
        elif ev == "changed:public_url":
            cfg = C.load()
            v = self.form.values["public_url"].strip().rstrip("/")
            if v and not v.startswith("https://"):
                v = "https://" + v
            cfg["public_url"] = v
            self.form.values["public_url"] = v
            C.save(cfg)
            app.cfg = cfg
        elif ev == "action:token":
            app.push(TextScreen(app, "Tunnel token", wrap(
                "The token comes from Cloudflare (Zero Trust > Networks > Tunnels). It is too long to type, "
                "so load it over SSH:\n"
                f"{brand.CMD} --set-token  (paste, then Ctrl+D)\n"
                + ("A token is saved." if C.tunnel_token() else "No token saved yet."), 30)))
        elif ev == "action:cf":
            def job():
                try:
                    self.cfv = control.install_cloudflared(lambda m: None)
                    return f"cloudflared {self.cfv} installed. The tunnel restarts with it."
                except Exception as e:
                    return f"Install failed: {e}"
            app.busy("Installing cloudflared", job)
        elif ev == "action:log":
            def lines():
                try:
                    import json
                    out = []
                    for raw in open(C.AUDIT_LOG).readlines()[-60:][::-1]:
                        d = json.loads(raw)
                        out.append((f"{d['t'][11:16]} {d['tool']}", T.FG if d.get("ok") else T.BAD))
                    return out or ["(nothing yet)"]
                except (OSError, ValueError):
                    return ["(nothing yet)"]
            app.push(TextScreen(app, "Recent tool calls", [], live=lines))


class App:
    def __init__(self, panel: L.Panel, kb: Keyboard | None):
        self.panel, self.kb = panel, kb
        self.cfg = C.load()
        self.on = control.is_on()
        self.status: dict = {}
        self.clients: list = []
        self.stack: list[Screen] = []
        self.quit = False
        self._toast = ("", 0.0)
        self._last = 0.0
        self.refresh(now=True)
        self.wizard = False
        self.push(Home(self))
        if brand.FIRST_RUN_WIZARD and not tunnel_ready(self.cfg):
            self.push(WelcomeScreen(self))

    def push(self, s: Screen) -> None:
        self.stack.append(s)

    def pop(self) -> None:
        if len(self.stack) > 1:
            self.stack.pop()

    def toast(self, text: str) -> None:
        self.push(TextScreen(self, APP_TITLE, [(l, T.WARN) for l in wrap(text, 30)]))

    def busy(self, label: str, fn) -> None:
        scr = TextScreen(self, label, [("Working...", T.DIM)])
        self.push(scr)

        def run():
            res = fn()
            scr._lines = [(l, T.OK if "fail" not in res.lower() else T.BAD) for l in wrap(res, 30)]
        threading.Thread(target=run, daemon=True).start()

    def refresh(self, now: bool = False) -> None:
        if not now and time.time() - self._last < 1.5:
            return
        self._last = time.time()
        self.on = control.is_on()
        self.status = (C.read_json(C.STATUS_FILE) or {}) if self.on else {}
        self.cfg = C.load()
        try:
            self.clients = oauth.Store().summary()
        except Exception:
            self.clients = []

    def status_lines(self) -> list:
        st, cfg = self.status, self.cfg
        tun = st.get("tunnel", "off") if self.on else "off"
        col = T.OK if tun == "connected" else (T.WARN if self.on else T.DIM)
        base = cfg.get("public_url") if cfg.get("tunnel_kind") == "cloudflare" else st.get("public_url", "")
        kind = KIND_NAMES.get(cfg.get("tunnel_kind"), "")
        out = [("Server: " + ("running" if self.on else "OFF"), T.OK if self.on else T.BAD),
               (f"Tunnel ({kind}): {tun} {st.get('tunnel_detail', '') if st.get('tunnel_detail') != cfg.get('tunnel_kind') else ''}".strip(), col),
               ("Connector address:", T.DIM)]
        out += [(l, T.FG) for l in wrap((base + "/mcp") if base else "(not known yet)", 30)]
        out.append((f"Connected: {len(self.clients)}   Admin: {'ON' if cfg.get('allow_sudo') else 'off'}",
                    T.BAD if cfg.get("allow_sudo") else T.FG))
        out.append(("Recent activity:", T.DIM))
        for r in st.get("recent", []):
            out.append((f"{ago(r['t']):>3} {r['text']}", T.FG))
        if not st.get("recent"):
            out.append(("  (none yet)", T.DIM))
        return out

    def draw(self) -> None:
        s = self.stack[-1]
        T.render(self.panel.surf, s.title, s.rows(), s.hint(), None, s.right(), self.panel.fonts,
                 [] if s.typing() else s.keys())
        if hasattr(s, "draw_extra"):
            s.draw_extra(self.panel.surf)
        self.panel.flush()

    def run(self) -> None:
        next_draw = 0.0
        while not self.quit:
            keys = self.kb.poll(0.25) if self.kb else []
            for k in keys:
                if k.name == "esc_long":           # hold ESC: exit to HOME from any screen
                    self.quit = True
                    break
                s = self.stack[-1]
                i = T.soft_index(getattr(k, "char", ""))
                if i is not None and not s.typing() and any(s.keys()):
                    s.soft(i)
                else:
                    s.key(k)
                next_draw = 0
            self.refresh()
            if time.time() >= next_draw:
                self.draw()
                next_draw = time.time() + (1.0 if self.stack[-1].live else 3.0)


def main_ui() -> int:
    panel = L.Panel()
    kb = Keyboard()
    app = App(panel, kb)

    def on_term(*_):
        app.quit = True
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGHUP, on_term)
    try:
        if not kb.ok:
            panel.draw(APP_TITLE, [("No keyboard found.", T.BAD)], "")
            time.sleep(4)
            return 1
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        msg = "Remote access stays ON." if control.is_on() else "Remote access is off."
        panel.draw(APP_TITLE, [(msg, T.OK), ("Bye!", T.FG)], "")
        kb.close()
    return 0
