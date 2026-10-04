"""Shared helpers for the test scripts (see tests/README.md).

Each test is one script that runs the real code offscreen and prints PASS / FAIL lines. run.sh starts every test with a
throwaway HOME on a private D-Bus session bus, so your own files, settings and running Kestrel windows are never
touched.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from gi.repository import Gio, GLib  # noqa: E402
from PyQt6.QtCore import QEventLoop, QSettings, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import kestrel.app as A  # noqa: E402
from kestrel import atc, thumbs, util  # noqa: E402

fails = 0


def check(ok, what):
    global fails
    print(("PASS " if ok else "FAIL ") + what, flush=True)
    fails += 0 if ok else 1


def skip(what):
    print("SKIP " + what, flush=True)


def spin(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_for(f, ms=5000):
    t = 0
    while t < ms:
        if f():
            return True
        spin(50)
        t += 50
    return bool(f())


def finish():
    """Print the summary and exit without running destructors (worker threads may still be stopping)."""
    print(f"{'FAILED' if fails else 'ALL PASSED'} ({fails} failed)", flush=True)
    os._exit(1 if fails else 0)


def setup_app():
    """The app's startup, minus the window: the QApplication, settings and thumbnail manager, as main() sets them
    up."""
    app = QApplication(sys.argv)
    A._settings = QSettings(util.APP_ID, util.APP_ID)
    A._thumbs = thumbs.ThumbnailManager()
    A.apply_thumb_settings(A._thumbs, A._settings)
    return app


def start_radio():
    atc.radio().heard.connect(A.on_atc)
    atc.radio().start()


def home_path(name):
    return os.path.join(os.path.expanduser("~"), name)


class FakeFlight:
    """Plays "another Kestrel" through its own bus connection: it can check in, report, call tower methods, and
    records every Broadcast it hears in `heard` as (flight, message)."""

    def __init__(self):
        self.heard = []
        self.connect_bus()

    def connect_bus(self):
        addr = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
        self.conn = Gio.DBusConnection.new_for_address_sync(
            addr, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None)
        self.conn.signal_subscribe(None, atc.IFACE, "Broadcast", atc.PATH, None, Gio.DBusSignalFlags.NONE,
                                   self._on_broadcast)

    def _on_broadcast(self, _conn, _sender, _path, _iface, _signal, params):
        flight, msg = params.unpack()
        self.heard.append((flight, json.loads(msg)))

    def disconnect_bus(self):
        self.conn.close_sync(None)

    def name(self):
        return self.conn.get_unique_name()

    def call(self, method, arg=None):
        """A tower method; its string result ("ok" if it has none), or None on error."""
        try:
            r = self.conn.call_sync(atc.NAME, atc.PATH, atc.IFACE, method,
                                    GLib.Variant("(s)", (arg,)) if arg is not None else None, None,
                                    Gio.DBusCallFlags.NONE, 3000, None)
        except GLib.Error:
            return None
        return r.unpack()[0] if r.n_children() else "ok"

    def check_in(self):
        self.call("CheckIn", '{"pid":1,"impl":"fake"}')

    def report(self, msg):
        self.call("Report", json.dumps(msg))

    def heard_type(self, kind, sender):
        return any(m.get("type") == kind and f == sender for f, m in self.heard)

    def count_type(self, kind, sender):
        return sum(1 for f, m in self.heard if m.get("type") == kind and f == sender)

    def last_of(self, kind):
        return next((m for f, m in reversed(self.heard) if m.get("type") == kind), {})

    def flights_have(self, pid, impl):
        r = self.call("Flights")
        return bool(r) and any(f.get("pid") == pid and f.get("impl") == impl for f in json.loads(r))

    def tower_pid(self):
        try:
            return self.conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                       "GetConnectionUnixProcessID", GLib.Variant("(s)", (atc.NAME,)), None,
                                       Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
        except GLib.Error:
            return 0
