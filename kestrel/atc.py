"""Air traffic control: keeps every running Kestrel ("flight") up to date with what the others change.

Kestrel isn't single-instance: a folder opened from another app may start its own Kestrel process, and a crash only
takes down that one. The tower (`kes --atc`, no window) is a small service on the D-Bus session bus. Each Kestrel's
radio checks in with it, reports changes to shared state (Preferences, stars, folder colours and covers, bookmarks,
cleared caches) and hears what the other flights report. The first Kestrel that finds no tower starts one, and the
tower lands itself shortly after the last flight leaves. If the tower goes down, the flights start a new one.

The Python and C++ versions speak the same protocol (JSON messages), so they share one tower. Admin sessions are
never shared: each window keeps its own.
"""
import json
import os
import subprocess
import sys
import time

from PyQt6.QtCore import QCoreApplication, QObject, QTimer, pyqtSignal

from . import __version__, util

NAME = "kestrel_explorer.ATC"
PATH = "/kestrel_explorer/ATC"
IFACE = "kestrel_explorer.ATC"
XML = """
<node>
  <interface name="kestrel_explorer.ATC">
    <method name="CheckIn"><arg type="s" name="Info" direction="in"/></method>
    <method name="Report"><arg type="s" name="Message" direction="in"/></method>
    <method name="Flights"><arg type="s" name="Flights" direction="out"/></method>
    <method name="Kept"><arg type="s" name="Messages" direction="out"/></method>
    <signal name="Broadcast"><arg type="s" name="Flight"/><arg type="s" name="Message"/></signal>
  </interface>
</node>
"""
LAND_MS = 10_000   # the tower lands this long after the last flight has left (or if none checks in)


def _compact(obj):
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------- the tower

def run_tower(argv):
    """`kes --atc`: run the tower until the last flight has left."""
    Gio, GLib = util.Gio, util.GLib
    if not Gio:
        return 1
    app = QCoreApplication(argv)
    node = Gio.DBusNodeInfo.new_for_xml(XML)
    flights = {}   # unique bus name -> what it said when checking in
    kept = {}      # flight -> {type: its latest "keep" message}
    land = QTimer(singleShot=True, interval=LAND_MS, timeout=app.quit)
    land.start()

    def on_call(conn, sender, _path, _iface, method, params, invocation):
        if method == "Kept":   # the current state for a flight that just checked in (e.g. the others' running tasks)
            out = [{"flight": f, "message": m} for f, msgs in kept.items() for m in msgs.values()]
            invocation.return_value(GLib.Variant("(s)", (_compact(out),)))
            return
        if method == "Flights":
            out = [dict(info, flight=name) for name, info in flights.items()]
            invocation.return_value(GLib.Variant("(s)", (_compact(out),)))
            return
        arg = params.unpack()[0]
        if method == "CheckIn":
            try:
                info = json.loads(arg)
            except ValueError:
                info = {}
            flights[sender] = info if isinstance(info, dict) else {}
        else:   # Report: pass it on to every flight (the sender ignores its own)
            flights.setdefault(sender, {})
            try:
                msg = json.loads(arg)
            except ValueError:
                msg = None
            if isinstance(msg, dict) and msg.get("keep"):   # state, not an event: remembered for later flights
                kept.setdefault(sender, {})[str(msg.get("type", ""))] = msg
            conn.emit_signal(None, PATH, IFACE, "Broadcast", GLib.Variant("(ss)", (sender, arg)))
        land.stop()
        invocation.return_value(None)

    def owner_changed(conn, _sender, _path, _iface, _signal, params):
        # a flight that quit or crashed leaves the bus: tell the others, so they forget its state (e.g. its tasks)
        name, _old, new = params.unpack()
        if new or flights.pop(name, None) is None:
            return
        kept.pop(name, None)
        conn.emit_signal(None, PATH, IFACE, "Broadcast", GLib.Variant("(ss)", (name, '{"type":"left"}')))
        if not flights:
            land.start()

    registration = []

    def on_bus(conn, _name):
        registration.append(conn.register_object(PATH, node.interfaces[0], on_call, None, None))
        conn.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                              "/org/freedesktop/DBus", None, Gio.DBusSignalFlags.NONE, owner_changed)

    # another tower got there first (or there's no session bus): leave it to that one
    Gio.bus_own_name(Gio.BusType.SESSION, NAME, Gio.BusNameOwnerFlags.DO_NOT_QUEUE, on_bus, None,
                     lambda *_: QTimer.singleShot(0, app.quit))
    return app.exec()


# ---------------------------------------------------------------- a flight's radio

class Radio(QObject):
    # a change from another flight ("from": its name), or our own ("own": True); {"type": "left"} when a flight has gone
    heard = pyqtSignal(dict)
    reset = pyqtSignal()   # checked in with a (new) tower: forget the others' kept state, it's sent again next

    def __init__(self):
        super().__init__()
        self._conn = None
        self._tower = None   # the tower's unique bus name while it's up
        self._sub = None
        self._last_launch = 0.0
        self._watch = None
        self._kept = {}   # our latest "keep" message of each type, sent again to a new tower

    def start(self):
        """Check in with the tower, starting it if there is none."""
        Gio = util.Gio
        if not Gio or self._watch is not None:
            return
        self._watch = Gio.bus_watch_name(Gio.BusType.SESSION, NAME, Gio.BusNameWatcherFlags.NONE,
                                         self._appeared, self._vanished)

    def flight(self):
        """Our name on the bus ("from" in the others' messages), or "" if not connected."""
        return self._conn.get_unique_name() if self._conn is not None else ""

    def announce(self, type_, keep=False, **extra):
        """Tell this process's windows and every other flight. heard() repeats it here with "own": True.

        keep: this is our current state of that type (e.g. our running tasks), not an event. The tower remembers the
        latest one and hands it to flights that check in later; when we leave, it tells the others ("left")."""
        msg = dict(extra, type=type_)
        if keep:
            msg["keep"] = True
            self._kept[type_] = msg
        if self._conn is not None and self._tower:
            self._conn.call(NAME, PATH, IFACE, "Report", util.GLib.Variant("(s)", (_compact(msg),)), None,
                            util.Gio.DBusCallFlags.NONE, -1, None, None, None)
        own = dict(msg, own=True)
        QTimer.singleShot(0, lambda: self.heard.emit(own))

    def _appeared(self, conn, _name, owner):
        Gio, GLib = util.Gio, util.GLib
        self._conn = conn
        self._tower = owner
        if self._sub is None:
            self._sub = conn.signal_subscribe(None, IFACE, "Broadcast", PATH, None, Gio.DBusSignalFlags.NONE,
                                              self._broadcast)
        info = {"pid": os.getpid(), "impl": "python", "version": __version__}
        conn.call(NAME, PATH, IFACE, "CheckIn", GLib.Variant("(s)", (_compact(info),)), None,
                  Gio.DBusCallFlags.NONE, -1, None, None, None)
        for msg in self._kept.values():   # a new tower: tell it our state again
            conn.call(NAME, PATH, IFACE, "Report", GLib.Variant("(s)", (_compact(msg),)), None,
                      Gio.DBusCallFlags.NONE, -1, None, None, None)
        # then catch up with the others' state (calls on one connection are answered in order)
        conn.call(NAME, PATH, IFACE, "Kept", None, GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NONE, -1, None,
                  self._caught_up, None)

    def _caught_up(self, conn, res, _data):
        try:
            entries = json.loads(conn.call_finish(res).unpack()[0])
        except Exception:
            return
        me = conn.get_unique_name()
        self.reset.emit()
        for e in entries if isinstance(entries, list) else []:
            msg = e.get("message") if isinstance(e, dict) else None
            if not isinstance(msg, dict) or not msg.get("type") or e.get("flight") == me:
                continue
            msg["from"] = e.get("flight")
            self.heard.emit(msg)

    def _vanished(self, conn, _name):
        self._tower = None
        if conn is not None:
            self._launch_tower()

    def _launch_tower(self):
        # no tower (yet, or it went down): start one. If several flights do this at once, only one gets the name.
        if self._tower or QCoreApplication.closingDown():
            return
        wait = self._last_launch + 3 - time.monotonic()
        if wait > 0:   # launched one moment ago: give it time, then try again if it still isn't up
            QTimer.singleShot(int(wait * 1000) + 1, self._launch_tower)
            return
        self._last_launch = time.monotonic()
        launcher = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kes")
        try:
            subprocess.Popen([sys.executable, launcher, "--atc"], cwd="/", stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            pass

    def _broadcast(self, conn, sender, _path, _iface, _signal, params):
        if sender != self._tower:
            return   # only the tower speaks on this channel
        flight, raw = params.unpack()
        if flight == conn.get_unique_name():
            return   # our own report: already delivered here by announce()
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if not isinstance(msg, dict) or not msg.get("type"):
            return
        msg["from"] = flight
        self.heard.emit(msg)


_radio = None


def radio():
    global _radio
    if _radio is None:
        _radio = Radio()
    return _radio


def announce(type_, keep=False, **extra):
    radio().announce(type_, keep, **extra)
