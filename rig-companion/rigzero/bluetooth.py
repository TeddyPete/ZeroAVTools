"""BlueZ pairing and outgoing authenticated Serial Port Profile connection."""
from __future__ import annotations

import queue
import logging
import socket
import threading

from .models import GAIA_UUID, SPP_UUID, discovery_candidate

SPP = SPP_UUID
GAIA = GAIA_UUID


class BluetoothLink:
    def __init__(self):
        self.events = queue.Queue(maxsize=32)
        self.waiting_confirmation = None
        self.pair_target = "TH-D75"
        self.family = None          # "kenwood" or "benshi": which radios scans list
        self.radio_device = None
        self.socket = None
        self.bus = None
        self.ready = threading.Event()
        self.error = ""
        self.thread = threading.Thread(target=self._run, daemon=True, name="rig-bluez")
        self.thread.start()

    def _run(self):
        try:
            import dbus
            import dbus.service
            from dbus.mainloop.glib import DBusGMainLoop
            from gi.repository import GLib
            DBusGMainLoop(set_as_default=True)
            self.bus = dbus.SystemBus()
            link = self

            class Agent(dbus.service.Object):
                object_path = "/org/rigcompanion/agent"
                @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="", async_callbacks=("accept", "reject"))
                def RequestConfirmation(self, device, number, accept, reject):
                    logging.warning("Bluetooth pairing: confirmation requested")
                    link.pair_target = "radio" if str(device) == link.radio_device else "phone"
                    link.waiting_confirmation = (accept, reject)
                    link.emit("pair", f"{int(number):06d}")

                @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
                def RequestAuthorization(self, device):
                    raise dbus.exceptions.DBusException("Pair using the app", name="org.bluez.Error.Rejected")

                @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
                def AuthorizeService(self, device, uuid):
                    if str(uuid).lower() not in (SPP, GAIA):
                        raise dbus.exceptions.DBusException("Radio serial only", name="org.bluez.Error.Rejected")

                @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
                def Cancel(self):
                    logging.warning("Bluetooth pairing: request cancelled by BlueZ")
                    link.waiting_confirmation = None
                    link.emit("pair_cancel", "Pairing cancelled")

                @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
                def Release(self):
                    self.Cancel()

            class Profile(dbus.service.Object):
                object_path = "/org/rigcompanion/spp"
                @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}", out_signature="")
                def NewConnection(self, device, descriptor, properties):
                    if link.socket is not None:
                        raise dbus.exceptions.DBusException("Already connected", name="org.bluez.Error.Rejected")
                    link.socket = socket.socket(fileno=descriptor.take())
                    link.socket.settimeout(.12)
                    link.emit("connected", str(device))

                @dbus.service.method("org.bluez.Profile1", in_signature="o", out_signature="")
                def RequestDisconnection(self, device):
                    link.disconnect()

                @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
                def Release(self):
                    link.disconnect()

            self.agent = Agent(self.bus, "/org/rigcompanion/agent")
            self.profile = Profile(self.bus, "/org/rigcompanion/spp")
            self.gaia_profile = Profile(self.bus, "/org/rigcompanion/gaia")
            self.manager = dbus.Interface(self.bus.get_object("org.bluez", "/org/bluez"), "org.bluez.AgentManager1")
            self.manager.RegisterAgent(self.agent.object_path, "DisplayYesNo")
            profiles = dbus.Interface(self.bus.get_object("org.bluez", "/org/bluez"), "org.bluez.ProfileManager1")
            profiles.RegisterProfile(self.profile.object_path, SPP,
                                     {"Name": "Rig Companion", "Role": "client", "RequireAuthentication": True})
            # Benshi radios (UV-Pro, GA-5WB, VR-N76...) take commands on their GAIA serial service.
            profiles.RegisterProfile(self.gaia_profile.object_path, GAIA,
                                     {"Name": "Rig Companion radio control", "Role": "client"})
            self.loop = GLib.MainLoop()
            self.ready.set()
            self.loop.run()
        except Exception as error:
            self.error = str(error)
            self.ready.set()
            self.emit("error", self.error)

    def emit(self, kind, value):
        try:
            self.events.put_nowait((kind, value))
        except queue.Full:
            self.error = "Bluetooth event queue full"

    def confirm(self, accept):
        from gi.repository import GLib
        import dbus
        pending, self.waiting_confirmation = self.waiting_confirmation, None
        logging.warning("Bluetooth pairing: user accepted=%s, pending=%s", accept, bool(pending))
        if pending:
            def answer():
                logging.warning("Bluetooth pairing: sending agent reply")
                if accept:
                    pending[0]()
                else:
                    pending[1](dbus.exceptions.DBusException("Pairing declined", name="org.bluez.Error.Rejected"))
                logging.warning("Bluetooth pairing: agent reply sent")
                return False
            GLib.idle_add(answer)

    def objects(self):
        import dbus
        if not self.ready.wait(3) or self.error:
            raise RuntimeError(self.error or "Bluetooth not ready")
        return dbus.Interface(self.bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager").GetManagedObjects()

    def _call(self, method, *args, timeout):
        from gi.repository import GLib
        done = threading.Event()
        outcome = []

        def success(*values):
            done.set()

        def failure(error):
            outcome.append(error)
            done.set()

        def invoke():
            try:
                method(*args, reply_handler=success, error_handler=failure, timeout=timeout)
            except Exception as error:
                failure(error)
            return False

        # Pair and ConnectProfile both call back into our exported objects.
        GLib.idle_add(invoke)
        if not done.wait(timeout + 1):
            raise TimeoutError("Bluetooth request timed out")
        if outcome:
            raise outcome[0]

    def scan(self):
        import dbus
        objects = self.objects()
        adapters = [path for path, interfaces in objects.items() if "org.bluez.Adapter1" in interfaces]
        if not adapters:
            raise RuntimeError("Bluetooth adapter unavailable")
        adapter = dbus.Interface(self.bus.get_object("org.bluez", adapters[0]), "org.bluez.Adapter1")
        adapter.SetDiscoveryFilter({"Transport": "bredr", "DuplicateData": False})
        try:
            adapter.StartDiscovery()
        except dbus.exceptions.DBusException as error:
            if error.get_dbus_name() != "org.bluez.Error.InProgress":
                raise
        self.scanning_adapter = adapter

    def devices(self):
        devices = []
        for path, interfaces in self.objects().items():
            props = interfaces.get("org.bluez.Device1", {})
            name = str(props.get("Alias", props.get("Name", "")))
            if discovery_candidate(name, props.get("UUIDs", ()), self.family):
                devices.append({"path": str(path), "name": name, "address": str(props["Address"]),
                                "paired": bool(props.get("Paired", False))})
        return devices

    def connect(self, device, service=SPP):
        import dbus
        self.radio_device = device["path"]
        if getattr(self, "scanning_adapter", None):
            try:
                self.scanning_adapter.StopDiscovery()
            except dbus.exceptions.DBusException:
                pass
            self.scanning_adapter = None
        remote = dbus.Interface(self.bus.get_object("org.bluez", device["path"]), "org.bluez.Device1")
        if not device["paired"]:
            self.manager.RequestDefaultAgent(self.agent.object_path)
            logging.warning("Bluetooth pairing: requesting bond")
            self._call(remote.Pair, timeout=65)
            logging.warning("Bluetooth pairing: bond complete")
        self._call(remote.ConnectProfile, service, timeout=15)
        if self.socket is None:
            raise RuntimeError("Radio did not open its serial Bluetooth service")
        return self.socket

    def disconnect(self):
        connection, self.socket = self.socket, None
        if connection:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

    def close(self):
        self.disconnect()
        if getattr(self, "loop", None):
            self.loop.quit()

