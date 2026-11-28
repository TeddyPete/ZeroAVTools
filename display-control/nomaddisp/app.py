"""Nomad Display Control: the app. Screens on the LCD, input from the keyboard."""
from __future__ import annotations

import signal
import threading
import time

from . import __version__
from . import lcd as L
from . import ops, store
from . import nomadui as T
from .base import KIND_NAMES, KIND_SHORT, SECRET_LABEL, Display, DisplayError
from .drivers import DRIVERS
from .keys import Keyboard
from .lgwebos import legacy_key
from .ui import BACK, DOWN, SELECT, UP, Busy, Confirm, Form, Message, Prompt, Screen, nav_key, wrap

APP_TITLE = "Nomad Display Control"
T.set_about(APP_TITLE, __version__)
T.set_popups("nomad-display-control")
HELP_MAIN = ("Nomad Display Control turns projectors and TVs on and off, switches inputs and sets "
             "volume over the network: PJLink projectors, LG webOS, Samsung, Sony Bravia and "
             "Roku TVs. Find displays scans this network; Rooms switches a whole room at once. "
             "TheTechNomad.com")

POWER_COLOR = {"on": L.OK, "off": L.DIM, "standby": L.DIM, "warming": L.WARN, "cooling": L.WARN,
               "screen off": L.WARN}
KIND_CHOICES = ["PJLink", "LG webOS", "Samsung", "Sony Bravia", "Roku"]
KIND_OF_CHOICE = dict(zip(KIND_CHOICES, ["pjlink", "lg", "samsung", "sony", "roku"]))
CHOICE_OF_KIND = {v: k for k, v in KIND_OF_CHOICE.items()}


class App:
    def __init__(self, panel: L.Panel, kb: Keyboard | None):
        self.panel, self.kb = panel, kb
        self.displays: list[Display] = store.load()
        self.state: dict[str, dict] = {}      # display id -> last status
        self.busy: set[str] = set()           # display ids with a command running
        self.stack: list[Screen] = []
        self.helpview = None
        self.steps = None                 # nomadui.StepsPopup shown over the screen
        self.dirty = True
        self.quit = False
        self._flash = ("", L.FG, 0.0)

    # ---- stack ----
    def push(self, s: Screen) -> None:
        self.stack.append(s)
        st = getattr(s, "steps", None)       # setup steps the user must do by hand
        if st is not None and not T.steps_hidden(st.key):
            st.top = 0
            self.steps = st
        self.dirty = True

    def show_steps(self, popup) -> None:
        """Show setup steps (nomadui.StepsPopup) over the current screen,
        unless the user hid that popup with D."""
        if popup is not None and not T.steps_hidden(popup.key):
            popup.top = 0
            self.steps = popup
            self.dirty = True

    def pop(self, s: Screen | None = None) -> None:
        if s is None:
            if len(self.stack) > 1:
                self.stack.pop()
            else:
                self.quit = True
        elif s in self.stack:
            self.stack.remove(s)
        self.dirty = True

    def open_about(self) -> None:
        """The About screen (shown like Fn+H help, ESC closes it)."""
        self.helpview = T.AboutView()
        self.dirty = True

    def wake(self) -> None:
        self.dirty = True

    def save(self) -> None:
        store.save(self.displays)

    # ---- short result line ----
    def flash(self, text: str, color=L.OK, secs: float = 4.0) -> None:
        self._flash = (text, color, time.monotonic() + secs)
        self.dirty = True

    def flash_line(self):
        text, color, until = self._flash
        return (text, color) if text and time.monotonic() < until else None

    # ---- background commands ----
    def do(self, d: Display, cmd: str, arg=None, label: str = "", then=None, refresh: bool = True) -> None:
        """Run a command on a display without blocking the screen; the result
        shows on the flash line."""
        self.flash((label or cmd) + "...", L.WARN, 30)
        self.busy.add(d.id)

        def work():
            try:
                r = ops.run(d, cmd, arg)
                if then:
                    then(r)
                elif isinstance(r, str):
                    self.flash(r, L.OK)
                else:
                    self.flash("Done", L.OK)
                if cmd == "pair":
                    self.save()
            except (DisplayError, OSError, ValueError) as e:
                self.flash(str(e) or e.__class__.__name__, L.BAD, 8)
            finally:
                self.busy.discard(d.id)
                self.dirty = True
            if refresh and cmd not in ("status", "info", "inputs", "pictures"):
                time.sleep(1.5)
                self.poll(d)
        threading.Thread(target=work, daemon=True).start()

    def poll(self, d: Display) -> None:
        try:
            self.state[d.id] = ops.run(d, "status")
        except (DisplayError, OSError, ValueError) as e:
            self.state[d.id] = {"power": "?", "error": str(e)}
        self.dirty = True

    def poll_all(self) -> None:
        def work():
            ds = list(self.displays)
            import concurrent.futures as cf
            with cf.ThreadPoolExecutor(min(6, max(1, len(ds)))) as ex:
                list(ex.map(self.poll, ds))
        threading.Thread(target=work, daemon=True).start()

    def task(self, title, label, fn, done):
        def finish(result, error):
            if error is not None:
                self.push(Message(self, "Problem", str(error), L.BAD))
            else:
                done(result)
        self.push(Busy(self, title, label, fn, finish))

    # ---- main loop ----
    def run(self) -> None:
        self.push(Home(self))
        self.poll_all()
        last_draw = 0.0
        while not self.quit:
            keys = self.kb.poll(0.1) if self.kb else (time.sleep(0.1) or [])
            for k in keys:
                if k.name == "exit":     # Esc held: leave the app (key spec)
                    self.quit = True
                    break
                if self.stack:
                    self.dispatch(k)
                self.dirty = True
            if self.stack:
                self.stack[-1].tick()
            now = time.monotonic()
            if self.dirty or now - last_draw > 1.0:
                self.draw()
                last_draw = now
                self.dirty = False

    def dispatch(self, k) -> None:
        top = self.stack[-1]
        if self.helpview is not None:
            if self.helpview.handle(k) == "close":
                self.helpview = None
            return
        if self.steps is not None:
            if self.steps.handle(k) == "close":
                self.steps = None
            return
        if k.name == "help":
            text = top.help or HELP_MAIN
            if getattr(top, "steps", None) is not None:
                text = top.steps.title + "\n" + top.steps.help_text() + "\n\n" + text
            self.helpview = T.HelpView(top.title, text, top.keybar())
            return
        if not top.text_entry and top.soft(k):
            return
        top.key(k if top.text_entry else nav_key(k))

    def draw(self) -> None:
        if not self.stack:
            return
        surf, f = self.panel.surf, self.panel.fonts
        if self.helpview is not None:
            self.helpview.render(surf, f)
            self.panel.flush()
            return
        s = self.stack[-1]
        busy = s if isinstance(s, Busy) else None
        if busy is not None and len(self.stack) > 1:
            s = self.stack[-2]      # the Loading popup sits over the screen it came from
        rows, sel = s.rows()
        T.render(surf, s.title, rows, s.hint, sel, s.right(), f, s.keybar())
        if busy is not None:
            T.draw_loading(surf, busy.label, busy.t0, f)
        elif self.steps is not None:
            self.steps.render(surf, f)
        self.panel.flush()


# ---------------------------------------------------------------------------

class List(Screen):
    """Menu with optional non-selectable lines at the top.
    items: (label, action, right, color) where any of them may be callable."""
    hint = "↑↓ move  OK/→ open  ESC back"

    def __init__(self, app, title=""):
        super().__init__(app)
        self.title = title or self.title
        self.sel = 0
        self.top = 0

    def items(self) -> list:
        return []

    def header(self) -> list:
        return []

    def rows(self):
        head = self.header()
        its = self.items()
        n = max(1, self.nrows - len(head))
        if its:
            self.sel = max(0, min(self.sel, len(its) - 1))
        if self.sel < self.top:
            self.top = self.sel
        if self.sel >= self.top + n:
            self.top = self.sel - n + 1
        out = list(head)
        for it in its[self.top:self.top + n]:
            ev = [x() if callable(x) and i != 1 else x for i, x in enumerate(it)]
            label, right = ev[0], ev[2] if len(ev) > 2 else ""
            color = ev[3] if len(ev) > 3 and ev[3] else L.FG
            out.append((label, color, right or ""))
        return out, (len(head) + self.sel - self.top) if its else None

    def key(self, k):
        its = self.items()
        if k.name in UP and its:
            self.sel = (self.sel - 1) % len(its)
        elif k.name in DOWN and its:
            self.sel = (self.sel + 1) % len(its)
        elif k.name == "pageup":
            self.sel = max(0, self.sel - self.nrows)
        elif k.name == "pagedown" and its:
            self.sel = min(len(its) - 1, self.sel + self.nrows)
        elif k.name in SELECT and its:
            act = its[self.sel][1]
            if act:
                act()
        elif k.name in BACK:
            self.app.pop(self)


def power_badge(app, d: Display):
    st = app.state.get(d.id) or {}
    p = str(st.get("power", ""))
    if d.id in app.busy:
        return "...", L.WARN
    if not p:
        return "", L.DIM
    short = {"standby": "STBY", "warming": "WARM", "cooling": "COOL", "screen off": "BLANK"}.get(p, p.upper()[:5])
    return short, POWER_COLOR.get(p, L.BAD if p == "?" else L.FG)


class Home(List):
    title = APP_TITLE
    hint = "↑↓ move  OK open  hold ESC exit"
    help = HELP_MAIN

    def items(self):
        app = self.app
        out = []
        for d in app.displays:
            out.append((d.name, (lambda d=d: app.push(DisplayScreen(app, d))),
                        (lambda d=d: f"{power_badge(app, d)[0]} {KIND_SHORT.get(d.kind, '')}".strip()),
                        (lambda d=d: power_badge(app, d)[1] if power_badge(app, d)[0] else L.FG)))
        out += [("Find displays", lambda: find_displays(app), "", L.OK),
                ("Check a display (IP)", lambda: check_prompt(app), "", L.OK),
                ("Add by IP address", lambda: edit_display(app, None), "", L.OK),
                ("Rooms (all on / off)", lambda: app.push(Rooms(app)), lambda: str(len(store.rooms(app.displays)) or ""), L.OK),
                ("Wake-on-LAN", lambda: wake_prompt(app), "", L.OK),
                ("About", lambda: app.open_about(), "", L.OK)]
        return out

    def header(self):
        f = self.app.flash_line()
        if f:
            return [f]
        if not self.app.displays:
            return [("No displays yet: Find displays", L.DIM)]
        return []

    def current(self) -> Display | None:
        return self.app.displays[self.sel] if self.sel < len(self.app.displays) else None

    def softkeys(self):
        d = self.current()
        app = self.app
        return [
            ("Find", lambda: find_displays(app)),
            ("Rooms", lambda: app.push(Rooms(app))),
            ("On", lambda: app.do(d, "on", label=f"{d.name} on")) if d else None,
            (("Off", L.BAD), lambda: app.do(d, "off", label=f"{d.name} off")) if d else None,
            ("Refresh", lambda: (app.flash("Checking all displays", L.WARN, 3), app.poll_all())),
        ]

    def key(self, k):
        if k.name == "esc":
            self.app.quit = True
        elif k.name == "left":
            pass
        else:
            super().key(k)


class DisplayScreen(List):
    help = ("Keys 4 and 5 turn this display on and off, 6 and 7 change the volume and 8 mutes. "
            "Input lists the display's inputs. Power on for LG and Samsung TVs uses Wake-on-LAN, "
            "so the TV needs network standby on (LG: Turn on via Wi-Fi / Mobile TV on).")

    def __init__(self, app, d: Display):
        super().__init__(app, d.name)
        self.d = d
        self.drv = DRIVERS[d.kind](d)
        self.next_poll = 0.0

    def right(self):
        return KIND_SHORT.get(self.d.kind, "")

    def st(self) -> dict:
        return self.app.state.get(self.d.id) or {}

    def header(self):
        f = self.app.flash_line()
        if f:
            return [f]
        st = self.st()
        if not st:
            return [("Checking...", L.DIM)]
        if st.get("error"):
            return [(st["error"], L.BAD)]
        p = str(st.get("power", ""))
        return [(ops.status_text(st), POWER_COLOR.get(p, L.FG))]

    def items(self):
        app, d, caps = self.app, self.d, self.drv.caps
        st = self.st()
        out = [("Power on", lambda: app.do(d, "on", label="Power on"), "", L.OK),
               ("Power off", lambda: app.do(d, "off", label="Power off"), "", L.BAD)]
        if "input" in caps:
            out.append(("Input", lambda: choose_input(app, d), lambda: str(st.get("input") or "")[:12]))
        if "remote" in caps:
            out.append(("Remote (menu, arrows)", lambda: app.push(RemoteScreen(app, d)), "", L.OK))
        if self.drv.service_menus():
            out.append(("Installer / service menus", lambda: service_menus(app, d), "", L.WARN))
        if "volume" in caps:
            out.append(("Volume up", lambda: app.do(d, "vol", 1, "Volume up"),
                        lambda: "" if st.get("volume") is None else str(st["volume"])))
            out.append(("Volume down", lambda: app.do(d, "vol", -1, "Volume down")))
        if "mute" in caps:
            out.append(("Mute", lambda: app.do(d, "mute", None, "Mute"),
                        lambda: "" if st.get("mute") is None else ("ON" if st["mute"] else "off")))
        if "avmute" in caps:
            name = {"pjlink": "Picture + sound mute", "lg": "Screen off (TV on)", "sony": "Picture off"}.get(d.kind, "Picture mute")
            out.append((name, lambda: app.do(d, "avmute", None, name),
                        lambda: "" if st.get("avmute") is None else ("ON" if st["avmute"] else "off")))
        if "picture" in caps:
            out.append(("Picture mode", lambda: choose_picture(app, d)))
        if "message" in caps:
            out.append(("Show a message", lambda: message_prompt(app, d)))
        if self.drv.needs_pairing:
            out.append(("Pair with this TV", lambda: pair(app, d), lambda: ("paired" if d.secret else "not paired")
                        if d.kind != "lg" or (d.extra or {}).get("manifest") == "full" or not d.secret
                        else "redo for remote"))
        if d.mac:
            out.append(("Wake-on-LAN", lambda: app.do(d, "wol", label="Wake-on-LAN"), d.mac[-8:]))
        out += [("Info", lambda: show_info(app, d)),
                ("Setup steps", lambda: show_kind_steps(app, d.kind)) if d.kind in KIND_STEPS else None,
                ("Edit settings", lambda: edit_display(app, d)),
                ("Delete", lambda: delete_display(app, d, self), "", L.BAD)]
        return [o for o in out if o]

    def softkeys(self):
        app, d, caps = self.app, self.d, self.drv.caps
        return [("On", lambda: app.do(d, "on", label="Power on")),
                (("Off", L.BAD), lambda: app.do(d, "off", label="Power off")),
                ("Vol -", lambda: app.do(d, "vol", -1, "Volume down")) if "volume" in caps else None,
                ("Vol +", lambda: app.do(d, "vol", 1, "Volume up")) if "volume" in caps else None,
                ("Mute", lambda: app.do(d, "mute", None, "Mute")) if "mute" in caps else None]

    def tick(self):
        now = time.monotonic()
        if now > self.next_poll and self.d.id not in self.app.busy:
            self.next_poll = now + 10
            threading.Thread(target=self.app.poll, args=(self.d,), daemon=True).start()


class RemoteScreen(Screen):
    """The TV's remote: arrows (F/X/Z/C or the arrow keys), OK, and the menu
    buttons on keys 4-8. Presses go out in order on one connection."""
    help = ("Arrow keys or F X Z C move on the TV, OK selects. DEL is the TV's Back button. "
            "4 Home, 5 Back, 6 Menu (TV settings), 7 Input list, 8 Exit. ESC leaves the remote; "
            "hold ESC for HOME.")
    LABEL = {"up": "↑", "down": "↓", "left": "←", "right": "→", "ok": "OK"}

    def __init__(self, app, d: Display):
        super().__init__(app)
        self.title, self.d = f"Remote: {d.name}", d
        self.drv = DRIVERS[d.kind](d)
        self.q: list[str] = []
        self.cv = threading.Condition()
        self.alive = True
        self.last = ("Ready", L.DIM)
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        while True:
            with self.cv:
                while self.alive and not self.q:
                    self.cv.wait()
                if not self.alive:
                    break
                b = self.q.pop(0)
            try:
                with ops.lock(self.d):
                    self.drv.remote(b)
                self.last = (f"Sent {self.LABEL.get(b, b.capitalize())}", L.OK)
            except (DisplayError, OSError, ValueError, KeyError) as e:
                self.last = (str(e) or e.__class__.__name__, L.BAD)
                with self.cv:
                    self.q.clear()
            self.app.dirty = True
        if hasattr(self.drv, "close_remote"):
            self.drv.close_remote()

    def press(self, b: str):
        with self.cv:
            if len(self.q) < 8:
                self.q.append(b)
            self.cv.notify()
        self.last = (f"{self.LABEL.get(b, b.capitalize())}...", L.WARN)

    def right(self):
        return KIND_SHORT.get(self.d.kind, "")

    def rows(self):
        return [self.last, ("F ↑   X ↓   Z ←   C →", L.FG),
                ("OK select    DEL back", L.FG), ("ESC leaves the remote", L.DIM)], None

    def softkeys(self):
        return [("Home", lambda: self.press("home")), ("Back", lambda: self.press("back")),
                ("Menu", lambda: self.press("menu")), ("Input", lambda: self.press("source")),
                ("Exit", lambda: self.press("exit"))]

    def key(self, k):
        if k.name in ("up", "down", "left", "right"):
            self.press(k.name)
        elif k.name == "enter":
            self.press("ok")
        elif k.name == "backspace":
            self.press("back")
        elif k.name == "esc":
            with self.cv:
                self.alive = False
                self.cv.notify()
            self.app.pop(self)


class Pick(List):
    """Choose one value from a list; on_pick(value)."""

    def __init__(self, app, title, choices: list[tuple[str, str]], on_pick, current: str = ""):
        super().__init__(app, title)
        self.choices, self.on_pick, self.current = choices, on_pick, current
        for i, (v, _) in enumerate(choices):
            if v == current:
                self.sel = i

    def items(self):
        return [(lab, (lambda v=v: (self.app.pop(self), self.on_pick(v))), "✓" if v == self.current else "",
                 L.OK if v == self.current else L.FG) for v, lab in self.choices]


def choose_input(app, d):
    def got(inputs):
        if not inputs:
            app.push(Message(app, "Inputs", "The display reported no inputs.", L.WARN))
            return
        cur = str((app.state.get(d.id) or {}).get("input", ""))
        cur_id = next((v for v, lab in inputs if lab == cur or lab.startswith(cur + " (") or v == cur), "")
        app.push(Pick(app, "Input", inputs, lambda v: app.do(d, "input", v, "Switching input"), cur_id))
    app.task(d.name, "Reading inputs", lambda: ops.run(d, "inputs"), got)


def choose_picture(app, d):
    def got(r):
        modes, cur = r
        app.push(Pick(app, "Picture mode", [(m, m) for m in modes],
                      lambda v: app.do(d, "picture", v, "Picture mode"), cur))
    app.task(d.name, "Reading picture modes", lambda: ops.run(d, "pictures"), got)


def show_info(app, d):
    def got(rows):
        app.push(Message(app, f"Info: {d.name}", "\n".join(f"{k}: {v}" for k, v in rows)))
    app.task(d.name, "Reading info", lambda: ops.run(d, "info"), got)


def message_prompt(app, d):
    app.push(Prompt(app, "Show a message", "Text", "", lambda t: t.strip() and app.do(d, "message", t.strip(), "Sending"),
                    help_text="Shows briefly on the TV screen (LG: bottom corner notice)."))


def pair(app, d):
    hint = {"lg": "Accept the request on the TV within 60 seconds.",
            "samsung": "Choose Allow on the TV within 40 seconds."}.get(d.kind, "")

    def go():
        app.task("Pairing", "Look at the TV", lambda: ops.run(d, "pair"),
                 lambda r: (app.save(), app.push(Message(app, "Paired", f"{d.name}: {r}", L.OK)), app.poll(d)))
    app.push(Confirm(app, "Pair with TV", f"The TV will ask to allow Nomad Display Control. {hint}", go,
                     yes="OK pair", no="ESC back"))


def service_menus(app, d):
    """Hidden installer / service menus, behind a warning. Only opens them."""
    menus = DRIVERS[d.kind](d).service_menus()

    def opened(r):
        app.push(RemoteScreen(app, d))      # drive the menu with the TV's remote
        app.flash(r, L.WARN, 10)

    def pick():
        app.push(Pick(app, "Service menus", [(m[0], f"{m[1]}") for m in menus],
                      lambda v: app.task(d.name, "Opening menu", lambda: ops.run(d, "svcmenu", v), opened)))
    app.push(Confirm(app, "Warning", "Service and installer menus can change panel, power and firmware "
                     "settings, break picture calibration and void the warranty. This only opens the menu; "
                     "change nothing you don't know. Back/Exit closes it.", pick,
                     yes="OK I understand", no="ESC back"))


def check_prompt(app):
    from . import check
    from .settings_check import valid_ip
    color = {check.READY: L.OK, check.NEEDS: L.WARN, check.NO: L.BAD, check.INFO: L.DIM}
    mark = {check.READY: "OK", check.NEEDS: "!", check.NO: "x", check.INFO: "-"}

    def go(ip):
        ip = ip.strip()
        if not valid_ip(ip):
            app.push(Message(app, "Check a display", f"Bad IP address: {ip or '(empty)'}", L.BAD))
            return

        def done(r):
            app.push(CheckResult(app, ip, r, color, mark))
        known = next((d.mac for d in app.displays if d.host == ip and d.mac), "")
        app.task("Check a display", f"Probing {ip}", lambda: check.check(ip, known), done)
    app.push(Prompt(app, "Check a display", "IP", "", go,
                    help_text="Tries PJLink, LG, Samsung, Sony and Roku control. Changes nothing on the display."))


class CheckResult(Screen):
    help = ("OK means the display answers and can be controlled now. ! means it answers but needs "
            "something first (a password, key or setting). x means that method is off or missing. "
            "Key 4 adds the display with what was found.")

    def __init__(self, app, ip, r, color, mark):
        super().__init__(app)
        self.title, self.ip, self.r = f"Check {ip}", ip, r
        self.lines = []
        for state, text in r["lines"]:
            for i, l in enumerate(wrap(app.panel, f"{mark[state]} {text}", 300)):
                self.lines.append(((l if i == 0 else "   " + l), color[state]))
        self.top = 0

    def rows(self):
        return self.lines[self.top:self.top + self.nrows], None

    def softkeys(self):
        from . import check
        d = check.to_display(self.ip, self.r)
        known = any(x.host == self.ip for x in self.app.displays)
        mac = self.r.get("mac")
        return [("Add", lambda: (self.app.pop(self), edit_display(self.app, d))) if d and not known else None,
                ("Wake", lambda: self.wake(mac)) if mac and not self.r["kinds"] else None,
                None, None, ("Again", lambda: (self.app.pop(self), check_prompt(self.app)))]

    def wake(self, mac):
        from . import wol
        try:
            self.app.push(Message(self.app, "Wake-on-LAN", wol.wake(mac, self.ip) +
                                  ". Give the display 10-20 s, then check again.", L.OK))
        except (ValueError, OSError) as e:
            self.app.push(Message(self.app, "Wake-on-LAN", str(e), L.BAD))

    def key(self, k):
        if k.name in UP:
            self.top = max(0, self.top - 1)
        elif k.name in DOWN:
            self.top = min(max(0, len(self.lines) - self.nrows), self.top + 1)
        elif k.name in BACK or k.name in SELECT:
            self.app.pop(self)


def wake_prompt(app):
    def send(mac):
        try:
            from . import wol
            app.push(Message(app, "Wake-on-LAN", wol.wake(mac), L.OK))
        except (ValueError, OSError) as e:
            app.push(Message(app, "Wake-on-LAN", str(e), L.BAD))
    app.push(Prompt(app, "Wake-on-LAN", "MAC", "", send, help_text="Like 30:34:db:6e:bc:84 (any separator)"))
    app.show_steps(T.StepsPopup("Wake-on-LAN", [
        "On the display, turn on Wake-on-LAN or network standby (LG: Turn on via Wi-Fi; "
        "Samsung: Power On with Mobile; Sony: Remote Start; projectors: Network standby).",
        "Plug the display into wired Ethernet if you can: many only wake that way.",
        "Find its MAC on the display's network info page (or Find displays fills it in)."],
        key="wol", ok="OK done"))


def delete_display(app, d, screen):
    def yes():
        app.displays = [x for x in app.displays if x is not d]
        app.save()
        app.pop(screen)
        app.flash(f"Deleted {d.name}", L.WARN)
    app.push(Confirm(app, "Delete", f"Remove {d.name} ({d.host}) from the list?", yes, yes="OK delete", no="ESC keep"))


# Settings the display needs before it can be controlled. The app can't set
# these itself, so they pop up once a display of that type is added.
KIND_STEPS = {
    "pjlink": ["On the projector, open the Network (or LAN) menu.",
               "Turn PJLink on. If it has a PJLink password, type the same one here under Password / key.",
               "Set network standby (standby mode: Network or Eco off), so it can be turned on from standby."],
    "lg": ["On the TV: Settings > General > Devices > TV Management (or External Devices).",
           "Turn on 'Turn on via Wi-Fi' (also called Mobile TV On) so the TV can be woken over the network.",
           "Keep the TV on. Next, it asks to allow Nomad Display Control: press OK on the TV's remote."],
    "samsung": ["On the TV: Settings > General > External Device Manager > Device Connection Manager.",
                "Set Access Notification to First Time Only (or Always).",
                "Settings > General > Network > Expert Settings: turn on Power On with Mobile.",
                "Keep the TV on. Next, it asks to allow Nomad Display Control: choose Allow on the TV."],
    "sony": ["On the TV: Settings > Network > Home Network > IP Control.",
             "Set Authentication to Normal and Pre-Shared Key, and type a key (like 0000).",
             "Type that same key here under Password / key.",
             "Turn on Remote Start (Network > Remote Start) so it can be turned on over the network."],
    "roku": ["On the Roku: Settings > System > Advanced system settings > Control by mobile apps.",
             "Set Network access to Default (or Permissive).",
             "For power on from standby: Settings > System > Power > Fast TV start on."],
}


def kind_steps(kind: str):
    steps = KIND_STEPS.get(kind)
    if not steps:
        return None
    return T.StepsPopup(f"Set up the {CHOICE_OF_KIND.get(kind, kind)}", steps, key=f"setup-{kind}",
                        intro="Do these on the display once:", ok="OK done")


def show_kind_steps(app, kind: str) -> None:
    """Show the setup steps again, even after D hid them."""
    p = kind_steps(kind)
    if p is not None:
        p.key = ""              # shown on request: no "don't show again"
        app.show_steps(p)


def edit_display(app, d: Display | None, then=None):
    """Add (d None, or an unsaved Display from a scan) or edit a display."""
    new = d is None or d not in app.displays
    d = d or Display("", "pjlink", "")
    if new and d.kind == "lg" and not d.secret:
        d.secret = legacy_key()       # a TV this device already paired with won't ask again
    vals = {"name": d.name, "kind": CHOICE_OF_KIND.get(d.kind, "PJLink"), "host": d.host,
            "port": str(d.port or ""), "mac": d.mac, "room": d.room, "secret": d.secret}
    fields = [
        ("name", "Name", "text"),
        ("kind", "Type", "choice", KIND_CHOICES),
        ("host", "IP address", "ip"),
        ("room", "Room", "text", "Same room name = one group"),
        ("mac", "MAC (wake)", "text", "For power on by Wake-on-LAN"),
        ("secret", "Password / key", "secret", "PJLink password or Sony PSK",
         lambda v: KIND_OF_CHOICE.get(v["kind"]) in ("pjlink", "sony")),
        ("port", "Port", "number", "Empty = standard port"),
    ]

    def validate(v):
        from .wol import norm_mac
        from .settings_check import valid_ip
        if not v["name"].strip():
            return "Give the display a name"
        if not valid_ip(v["host"]):
            return f"Bad IP address: {v['host'] or '(empty)'}"
        if v["mac"].strip() and not norm_mac(v["mac"]):
            return f"Bad MAC address: {v['mac']}"
        if v["port"] and not 0 < int(v["port"]) < 65536:
            return "Port must be 1-65535"
        return None

    def saved(v):
        kind = KIND_OF_CHOICE.get(v["kind"], "pjlink")
        from .wol import norm_mac
        changed_kind = kind != d.kind
        d.name, d.kind, d.host = v["name"].strip(), kind, v["host"].strip()
        d.port = int(v["port"]) if v["port"] else 0
        d.mac = norm_mac(v["mac"]) or ""
        d.room = v["room"].strip()
        if kind in ("pjlink", "sony"):
            d.secret = v["secret"]
        elif changed_kind:
            d.secret = ""
        if d not in app.displays:
            app.displays.append(d)
        app.save()
        app.flash(f"Saved {d.name}", L.OK)
        threading.Thread(target=app.poll, args=(d,), daemon=True).start()
        if then:
            then(d)
        elif new and DRIVERS[d.kind].needs_pairing and not d.secret:
            pair(app, d)
        if new or changed_kind:
            app.show_steps(kind_steps(d.kind))     # over the pairing question
    app.push(Form(app, "Add display" if new else "Edit display", fields, vals, saved, validate))


# ---- find ------------------------------------------------------------------

def find_displays(app):
    busy = {}

    def scan():
        from . import discover
        def progress(t):
            if busy.get("s"):
                busy["s"].label = t
                app.dirty = True
        found = discover.scan(progress=progress)
        hit = {(d.host, d.kind) for d in found}
        miss = [d for d in app.displays if (d.host, d.kind) not in hit]
        if miss:
            progress(f"Retrying {len(miss)} saved")
            found += discover.retry_saved(miss)
        return found

    app.task("Find displays", "Scanning network", scan, lambda found: app.push(Found(app, found)))
    busy["s"] = app.stack[-1]


SLEEP_HINT = ("TVs that are off sleep their network. Turn on: LG 'Turn on via Wi-Fi' (Quick Start+), "
              "Samsung 'Network standby' / 'Power on with mobile', Sony 'Remote start'. Then Wake works.")


class Found(List):
    title = "Found displays"
    help = ("Displays found on this network, then saved ones that did not answer (OFFLINE: off or asleep). "
            "OK adds a new one, or opens a saved one. Key 4 adds every new one, key 5 wakes the highlighted "
            "display by Wake-on-LAN. " + SLEEP_HINT)

    def __init__(self, app, found: list[Display]):
        super().__init__(app)
        self.found = found
        hit = {(d.host, d.kind) for d in found}
        self.offline = [d for d in app.displays if (d.host, d.kind) not in hit]

    def saved(self, d) -> bool:
        return any(x.host == d.host and x.kind == d.kind for x in self.app.displays)

    def all(self) -> list:
        return list(self.found) + self.offline

    def header(self):
        f = self.app.flash_line()
        if f:
            return [f]
        if not self.found:
            return [("No display answered. TV off? Its network sleeps", L.WARN)]
        return []

    def items(self):
        out = [(f"{d.name}"[:22], (lambda d=d: self.add(d)),
                lambda d=d: ("saved " if self.saved(d) else "") + KIND_SHORT.get(d.kind, ""),
                lambda d=d: L.DIM if self.saved(d) else L.FG) for d in self.found]
        out += [(f"{d.name}"[:20], (lambda d=d: self.app.push(DisplayScreen(self.app, d))),
                 "OFFLINE", L.WARN) for d in self.offline]
        return out

    def current(self):
        a = self.all()
        return a[self.sel] if 0 <= self.sel < len(a) else None

    def add(self, d):
        if self.saved(d):
            self.app.flash(f"{d.name} is already saved", L.WARN)
            return
        edit_display(self.app, d)

    def add_all(self):
        n = 0
        for d in self.found:
            if not self.saved(d):
                if d.kind == "lg" and not d.secret:
                    d.secret = legacy_key()
                self.app.displays.append(d)
                n += 1
        self.app.save()
        self.app.flash(f"Added {n} display{'s' if n != 1 else ''}", L.OK)
        self.app.poll_all()

    def wake(self):
        d = self.current()
        if not d:
            return
        if not d.mac:
            self.app.flash(f"No MAC saved for {d.name}", L.BAD, 6)
            return
        self.app.do(d, "wol", label=f"Waking {d.name}")

    def softkeys(self):
        d = self.current()
        return [("Add all", self.add_all) if any(not self.saved(x) for x in self.found) else None,
                ("Wake", self.wake) if d and d.mac else None, None, None,
                ("Rescan", lambda: (self.app.pop(self), find_displays(self.app)))]


# ---- rooms -----------------------------------------------------------------

ALL = "\x00all"


class Rooms(List):
    title = "Rooms"
    help = ("A room is every display with the same Room name (set it under Edit settings). "
            "Open a room to turn all of its displays on or off together. All displays is every saved one.")

    def items(self):
        app = self.app
        out = [(r, (lambda r=r: app.push(RoomScreen(app, r))),
                lambda r=r: str(sum(1 for d in app.displays if d.room.strip() == r))) for r in store.rooms(app.displays)]
        out.append(("All displays", lambda: app.push(RoomScreen(app, ALL)), str(len(app.displays)), L.OK))
        return out

    def header(self):
        if not store.rooms(self.app.displays):
            return [("No rooms yet (Edit > Room)", L.DIM)]
        return []


class RoomScreen(List):
    help = ("4 turns every display in this room on, 5 turns them all off (it asks first), "
            "6 mutes and 7 unmutes. Samsung and Roku only have a mute toggle, so group mute skips them.")

    def __init__(self, app, room):
        super().__init__(app, "All displays" if room == ALL else room)
        self.room = room
        self.result: list = []

    def members(self) -> list[Display]:
        return list(self.app.displays) if self.room == ALL else [d for d in self.app.displays if d.room.strip() == self.room]

    def header(self):
        f = self.app.flash_line()
        n = len(self.members())
        return [f] if f else [(f"{n} display{'' if n == 1 else 's'}", L.DIM)]

    def items(self):
        app = self.app
        out = [("All on", self.all_on, "", L.OK), ("All off", self.all_off, "", L.BAD)]
        if self.result:
            out.append(("Last result", self.show_result, ops.summary(self.result)))
        for d in self.members():
            out.append((d.name, (lambda d=d: app.push(DisplayScreen(app, d))),
                        lambda d=d: power_badge(app, d)[0], lambda d=d: power_badge(app, d)[1]))
        return out

    def group(self, cmd, label, arg=None):
        app, ds = self.app, self.members()
        if not ds:
            return
        app.flash(f"{label}: {len(ds)} displays...", L.WARN, 60)
        for d in ds:
            app.busy.add(d.id)

        def work():
            res = ops.group(ds, cmd, arg)
            for d in ds:
                app.busy.discard(d.id)
            self.result = res
            bad = [f"{d.name}: {t}" for d, ok, t in res if not ok]
            app.flash(f"{label}: {ops.summary(res)}" + (f"  {bad[0]}" if bad else ""), L.OK if not bad else L.WARN, 8)
            time.sleep(2)
            for d in ds:
                app.poll(d)
        threading.Thread(target=work, daemon=True).start()

    def all_on(self):
        self.group("on", "All on")

    def all_off(self):
        n = len(self.members())
        self.app.push(Confirm(self.app, "All off", f"Turn off all {n} displays in {self.title}?",
                              lambda: self.group("off", "All off"), yes="OK turn off", no="ESC back"))

    def show_result(self):
        self.app.push(Message(self.app, "Last result", "\n".join(
            f"{'OK' if ok else 'FAIL'} {d.name}: {t}" for d, ok, t in self.result)))

    def softkeys(self):
        return [("All on", self.all_on), (("All off", L.BAD), self.all_off),
                ("Mute", lambda: self.group("mute", "Mute", "on")),
                ("Unmute", lambda: self.group("mute", "Unmute", "off")), None]


# ---------------------------------------------------------------------------

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
            panel.draw(APP_TITLE, [("No keyboard found.", L.BAD)], "")
            time.sleep(4)
            return 1
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        panel.draw(APP_TITLE, [("Bye!", L.OK)], "")
        kb.close()
    return 0
