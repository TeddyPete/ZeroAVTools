"""Render example screens without connecting to or transmitting through a radio."""
from pathlib import Path
import tempfile

from .display import Display
from .protocol import inspect_packet
from .storage import Store


def render_previews(folder: Path):
    from .app import App
    from .radio import Radio
    with tempfile.TemporaryDirectory(prefix="rig-zero-preview-") as temp:
        store = Store(Path(temp))
        store.set("callsign", "N0CALL-7")
        store.set("radio_package", "kenwood")
        store.record(inspect_packet("N0WX-1", "APRS", "WIDE1-1", "_11061542c315s012g023t041r003h74b10182"))
        store.record(inspect_packet("N0TEST-5", "APRS", "", ":N0CALL-7 :Hello from the trail"))
        store.record(inspect_packet("N0TEST-9", "APRS", "WIDE1-1", "!4503.79N/09309.32W-Portable station"))
        store.record(inspect_packet("N0TEST-3", "APRS", "WIDE1-1", "!4515.10N/09238.77W#PHG5450 Test digipeater"))
        store.record(inspect_packet("N0TEST-9", "APRS", "", "T#016,122,075,099,045,128,00000000"))
        radio = Radio(store)
        radio.update(identity=True, connected=True, model="TH-D75", status="TH-D75 connected")
        import time
        radio.identified_at = time.monotonic()
        radio.state["bands"] = [{"hz": 144390000, "mode": 0, "power": 2, "channel": 0,
                                 "squelch": 1, "meter": 6, "busy": 1},
                                {"hz": 14074000, "mode": 4, "power": 1, "channel": 0,
                                 "squelch": 1, "meter": 2, "busy": 0}]
        radio.state["volume"] = 36
        screen = Display(headless=True)
        app = App(store, radio, screen)
        folder.mkdir(parents=True, exist_ok=True)
        for page, name in enumerate(("radio", "traffic", "inbox", "weather", "link")):
            app.page = page
            app.draw()
            screen.save(folder / (name + ".png"))
        app.page = 1
        app.detail = next(p for p in store.rows() if p["kind"] == "Telemetry")
        app.draw()
        screen.save(folder / "telemetry.png")
        app.detail = next(p for p in store.rows() if p["kind"] == "Position")
        app.draw()
        screen.save(folder / "position.png")
        from .mapview import StationMap
        app.map_view = StationMap(folder / "map-preview-cache", network=False)
        app.map_active = True
        app.detail = None
        app.draw()
        screen.save(folder / "map-offline.png")
        app.map_active = False
        app.detail = store.rows("inbox")[0]
        app.scroll = 0
        app.draw()
        screen.save(folder / "message.png")
        app.open_compose("N0TEST-5")
        app.compose["text"] = "Thanks! Good copy here."
        app.draw()
        screen.save(folder / "compose.png")
        app.editor = None
        app.detail = None
        app.panels.open("guide", radio.snapshot(), query="KISS")
        app.draw()
        screen.save(folder / "field-manual.png")
        app.panels.open("packet", radio.snapshot())
        app.draw()
        screen.save(folder / "packet-setup.png")
        app.panels.open("beacon", radio.snapshot())
        app.draw()
        screen.save(folder / "beacon.png")
        app.panels.screen = ""
        render_dstar(app, screen, store, radio, folder, Path(temp))
        radio.update(identity=False, connected=False)
        app.page = 0
        app.draw()
        screen.save(folder / "disconnected.png")
        app.open_picker()
        app.draw()
        screen.save(folder / "radio-picker.png")
        app.picker = None
        app.editor = "about"
        app.draw()
        screen.save(folder / "about.png")
        app.editor = None
        app.picker = None
        radio.close()
        render_benshi(app, screen, store, folder)
        app.close()
        app.radio.close()
        store.close()


def render_benshi(app, screen, store, folder):
    """A UV-Pro on the same Radio and Link screens (no Bluetooth; state only)."""
    import time
    from . import drivers
    package = drivers.find("benshi")
    store.set("radio_package", "benshi")
    radio = drivers.load(package)(store, None, "UV-Pro", package)
    radio.update(identity=True, connected=True, model="UV-Pro", volume=6, dual=1)
    radio.identified_at = time.monotonic()
    radio.state["bands"] = [{"hz": 144390000, "mode": 0, "power": 0, "channel": 1, "squelch": 3,
                             "meter": 4, "busy": 1, "number": 0, "name": "APRS"},
                            {"hz": 146520000, "mode": 6, "power": 2, "channel": 1, "squelch": 3,
                             "meter": 0, "busy": 0, "number": 1, "name": "Simplex"}]
    app.radio = radio
    app.page = 0
    app.draw()
    screen.save(folder / "benshi-radio.png")
    app.page = 4
    app.link_selected = 0
    app.draw()
    screen.save(folder / "benshi-link.png")



def render_dstar(app, screen, store, radio, folder, temp):
    """D-STAR screens with example traffic (no radio, no network)."""
    from datetime import datetime, timedelta, timezone
    from .dstar_gateway import DStarGateway
    store.record(inspect_packet("N0TEST-9", "API51", "DSTAR*", "!4459.20N/09316.10W>D-PRS via D-STAR"))
    gateway = DStarGateway(store, radio, cache_folder=temp)
    gateway.hosts.table = {"REF001": ("104.237.157.7", 20001), "REF030": ("74.208.200.42", 20001),
                           "XRF757": ("44.32.0.1", 30001), "XLX307": ("44.32.0.2", 30001),
                           "DCS001": ("5.252.227.231", 30051), "REF002": ("45.56.119.213", 20001)}
    gateway.hosts.updated = 1e12
    now = datetime.now(timezone.utc)
    def at(seconds):
        return (now - timedelta(seconds=seconds)).isoformat(timespec="seconds")
    heard = [{"call": "N0TEST", "suffix": "D75", "source": "NET", "reflector": "XRF757 A", "time": at(40),
              "seconds": 12.4, "message": "Mobile in Minneapolis", "ur": "CQCQCQ", "rpt1": "XRF757 G",
              "rpt2": "XRF757 A", "lat": 44.9867, "lon": -93.2683, "name": "", "place": ""},
             {"call": "N0CALL", "suffix": "", "source": "RF", "reflector": "XRF757 A", "time": at(95),
              "seconds": 6.0, "message": "Rig Companion", "ur": "CQCQCQ", "rpt1": "N0CALL A",
              "rpt2": "XRF757 A", "lat": None, "lon": None},
             {"call": "N0DEMO", "suffix": "ID52", "source": "NET", "reflector": "XRF757 A", "time": at(300),
              "seconds": 21.8, "message": "", "ur": "CQCQCQ", "rpt1": "XRF757 G", "rpt2": "XRF757 A",
              "lat": None, "lon": None, "name": "Example Operator", "place": "St Paul, MN"}]
    gateway.state.update(running=True, transport="Bluetooth ch 2", radio="Radio ready", version="TH-D75",
                         link="Linked", reflector="XRF757", module="A", protocol="DExtra",
                         linked_at=at(600), heard=heard, notice="", error="")
    from .dstar_route import IrcDDB
    irc = IrcDDB("N0CALL", temp / "routes.json")
    irc.state, irc.online = "Ready", 2914
    irc.book.join("n0demo-1", "203.0.113.7")
    irc.book.apply((1, "2026-11-28 11:41:37", "N0DEMO_B", "N0DEMO__", []))
    irc.book.apply((0, "2026-11-28 11:41:37", "N0DEMO__", "N0DEMO_B", []))
    irc.start = lambda: None
    gateway.ircddb = irc
    tools = app.dstar
    tools.gateway = gateway
    tools.seen_notice = ""
    tools.screen = "home"
    gateway.state["talker"] = dict(heard[0], time=at(7), active=True, name="Example Operator",
                                   place="Minneapolis, MN")
    app.notice_until = 0
    app.draw()
    screen.save(folder / "dstar-live.png")
    gateway.state["talker"] = None
    app.draw()
    screen.save(folder / "dstar-home.png")
    tools.screen, tools.selected = "heard", 0
    app.draw()
    screen.save(folder / "dstar-heard.png")
    from .dstar_nearby import Nearby
    import time as _time
    book = Nearby(temp)
    book.places = {"104.237.157.7": [40.7, -74.0, "Newark, New Jersey", _time.time()],
                   "74.208.200.42": [39.0, -77.5, "Ashburn, Virginia", _time.time()],
                   "44.32.0.1": [44.95, -93.1, "St Paul, Minnesota", _time.time()],
                   "44.32.0.2": [45.0, -93.3, "Minneapolis, Minnesota", _time.time()],
                   "5.252.227.231": [52.4, 4.9, "Amsterdam, NL", _time.time()],
                   "45.56.119.213": [41.9, -87.6, "Chicago, Illinois", _time.time()]}
    tools.nearby = book
    store.set("dstar_grid", "EN34ql")
    tools.screen, tools.category, tools.selected = "reflectors", 1, 0
    app.draw()
    screen.save(folder / "dstar-nearby.png")
    tools.screen, tools.category, tools.selected = "reflectors", 2, 1
    app.draw()
    screen.save(folder / "dstar-reflectors.png")
    tools.screen, tools.pick = "pick", ["REF030", "C"]
    app.draw()
    screen.save(folder / "dstar-module.png")
    tools.screen, tools.selected = "setup", 0
    app.draw()
    screen.save(folder / "dstar-setup.png")
    tools.screen, tools.entry, tools.offset = "entry", dict(heard[0], name="Example Operator", place="Minneapolis, MN"), 0
    app.draw()
    screen.save(folder / "dstar-detail.png")
    tools.screen = "home"
    tools.radio_steps(force=True)
    app.draw()
    screen.save(folder / "dstar-steps.png")
    app.popup = None
    app.loading("Connecting to the TH-D75", "Bluetooth gateway link", timeout=60)
    app.draw()
    screen.save(folder / "loading.png")
    app.loaded()
    irc.pending["N0DEMO"] = 0.0
    tools.screen, tools.call_edit, tools.call_target = "call", None, "N0DEMO"
    app.draw()
    screen.save(folder / "dstar-call.png")
    tools.screen = ""
    gateway.state["running"] = False
