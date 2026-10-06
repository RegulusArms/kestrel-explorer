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
    <method name="UndoPush"><arg type="s" name="Op" direction="in"/></method>
    <method name="UndoPop"><arg type="s" name="Op" direction="out"/></method>
    <method name="Handoff"><arg type="s" name="Request" direction="in"/><arg type="s" name="Flight" direction="out"/></method>
    <signal name="Broadcast"><arg type="s" name="Flight"/><arg type="s" name="Message"/></signal>
  </interface>
</node>
"""
LAND_MS = 10_000   # the tower lands this long after the last flight has left (or if none checks in)
UNDO_MAX = 50

# The protocol (the same in the C++ version). A flight checks in with {"pid", "impl", "version", "protocol"}; one
# speaking another protocol is ignored (one that names none predates the version and speaks 1). Messages from other
# programs on the session bus are checked by the tower and again by each radio: a JSON object of at most MAX_MESSAGE
# bytes, a known "type", each known field of the right type, absolute paths. Unknown fields are allowed (and ignored),
# so a newer Kestrel can add some.
PROTOCOL = 1
MAX_MESSAGE = 1 << 20

# Each message type and its fields. A field that's missing is fine (the receiver uses a default); one that's there must
# have this type. "paths": a list of absolute paths; "strs": a list of strings; "tasks": a list of tasks (TASK_FIELDS).
TYPES = {
    "settings": {},        # Preferences were saved
    "sidebar": {},         # the sidebar's order or collapsed sections changed
    "bookmarks": {},       # the GTK bookmarks changed
    "starred": {},         # starred.json changed
    "thumbs_cleared": {},  # the preview cache was cleared
    "left": {},            # (the tower's) a flight has gone
    "folders": {"paths": "paths"},   # folder colours, covers or previews changed
    "tasks": {"tasks": "tasks"},     # keep: the flight's running jobs
    "cancel": {"flight": "str", "task": "str"},   # cancel a job of that flight
    "windows": {"count": "num", "active": "num"},   # keep: how many windows, when one was last used
    "undo_changed": {"label": "str"},   # keep (the tower's): what Ctrl+Z would undo now
    "open": {"flight": "str", "folders": "paths", "select": "strs", "token": "str"},   # (the tower's) a hand-off
}
TASK_FIELDS = {"id": "str", "title": "str", "text": "str", "fraction": "num", "cancellable": "bool",
               "cancelling": "bool", "admin": "bool"}
UNDO_KINDS = ("move", "rename", "trash", "create")
TOWER_ONLY = ("left", "open", "undo_changed")   # a flight's report of these isn't passed on


def _valid_field(v, kind):
    if kind == "str":
        return isinstance(v, str)
    if kind == "num":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if kind == "bool":
        return isinstance(v, bool)
    if not isinstance(v, list):
        return False
    if kind == "tasks":
        return all(isinstance(e, dict) and _valid_fields(e, TASK_FIELDS) for e in v)
    return all(isinstance(e, str) and (kind == "strs" or e.startswith("/")) for e in v)


def _valid_fields(obj, fields):
    return all(name not in obj or _valid_field(obj[name], kind) for name, kind in fields.items())


def valid_message(msg):
    """A Report / Broadcast message."""
    return (isinstance(msg, dict) and isinstance(msg.get("type"), str) and msg["type"] in TYPES
            and _valid_fields(msg, TYPES[msg["type"]]) and isinstance(msg.get("keep", False), bool))


def valid_undo(op):
    """A shared undo entry: {"kind", "label", "items": [[a, b], …]} (absolute paths; b is "" for trash and create)."""
    if not (isinstance(op, dict) and op.get("kind") in UNDO_KINDS and isinstance(op.get("label"), str)
            and isinstance(op.get("items"), list)):
        return False
    return all(isinstance(p, list) and len(p) == 2 and all(isinstance(x, str) for x in p) and p[0].startswith("/")
               and (p[1] == "" or p[1].startswith("/")) for p in op["items"])


def parse(raw):
    """A JSON object from a message argument, or {} if it's too big or isn't one."""
    if not isinstance(raw, str) or len(raw.encode()) > MAX_MESSAGE:
        return {}
    try:
        obj = json.loads(raw)
    except ValueError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _speaks_our_protocol(info):
    return "protocol" not in info or info.get("protocol") == PROTOCOL


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
    undo = []      # the shared undo history (Preferences → Share undo…), newest last

    def undo_changed(conn):
        # tell every flight what Ctrl+Z would undo now; kept, so flights checking in later know it too
        me = conn.get_unique_name()
        msg = {"type": "undo_changed", "label": undo[-1].get("label", "") if undo else "", "keep": True}
        kept.setdefault(me, {})["undo_changed"] = msg
        conn.emit_signal(None, PATH, IFACE, "Broadcast", GLib.Variant("(ss)", (me, _compact(msg))))
    land = QTimer(singleShot=True, interval=LAND_MS, timeout=app.quit)
    land.start()

    def on_call(conn, sender, _path, _iface, method, params, invocation):
        if method == "Kept":   # the current state for a flight that just checked in (e.g. the others' running tasks)
            out = [{"flight": f, "message": m} for f, msgs in kept.items() for m in msgs.values()]
            invocation.return_value(GLib.Variant("(s)", (_compact(out),)))
            return
        if method == "UndoPop":   # each entry is handed out once, so two windows can't undo the same thing
            out = ""
            if undo:
                out = _compact(undo.pop())
                undo_changed(conn)
            invocation.return_value(GLib.Variant("(s)", (out,)))
            return
        if method == "Handoff":   # a new Kestrel's folders, for the flight whose window was used last
            best, best_active = "", -1.0
            for name, msgs in kept.items():
                w = msgs.get("windows") or {}
                if name in flights and _speaks_our_protocol(flights[name]) and int(w.get("count", 0)) > 0 \
                        and float(w.get("active", 0)) > best_active:
                    best, best_active = name, float(w.get("active", 0))
            msg = dict(parse(params.unpack()[0]), type="open", flight=best)
            if not valid_message(msg):
                best = ""  # not a hand-off: nobody takes it
            if best:
                conn.emit_signal(None, PATH, IFACE, "Broadcast", GLib.Variant("(ss)", (conn.get_unique_name(),
                                                                                       _compact(msg))))
            invocation.return_value(GLib.Variant("(s)", (best,)))
            return
        if method == "Flights":
            out = [dict(info, flight=name) for name, info in flights.items()]
            invocation.return_value(GLib.Variant("(s)", (_compact(out),)))
            return
        arg = params.unpack()[0]
        if method == "CheckIn":
            flights[sender] = parse(arg)
        elif method == "UndoPush":
            op = parse(arg)
            if valid_undo(op) and _speaks_our_protocol(flights.get(sender, {})):
                undo.append(op)
                del undo[:-UNDO_MAX]
                undo_changed(conn)
        else:   # Report: pass it on to every flight (the sender ignores its own)
            flights.setdefault(sender, {})
            msg = parse(arg)
            # only a flight's own news: "left", "open" and "undo_changed" are the tower's
            if valid_message(msg) and msg["type"] not in TOWER_ONLY and _speaks_our_protocol(flights[sender]):
                if msg.get("keep"):   # state, not an event: remembered for flights that check in later
                    kept.setdefault(sender, {})[msg["type"]] = msg
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

def hand_off(folders, select=()):
    """Preferences → "Open folders from other apps as tabs": give folders to the Kestrel whose window was used last
    (it opens them as tabs, selecting select[i] in folders[i] when given, and comes to the front). Returns that
    Kestrel's bus name, or "" if there's none (no tower, or no Kestrel with a window): then open our own window."""
    Gio, GLib = util.Gio, getattr(util, "GLib", None)
    if not Gio:
        return ""
    token = os.environ.get("XDG_ACTIVATION_TOKEN") or os.environ.get("DESKTOP_STARTUP_ID") or ""
    req = {"folders": list(folders), "select": list(select), "token": token}
    try:
        conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        # no tower, no answer within a moment: open our own window
        r = conn.call_sync(NAME, PATH, IFACE, "Handoff", GLib.Variant("(s)", (_compact(req),)),
                           GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NO_AUTO_START, 2000, None)
        return r.unpack()[0]
    except Exception:
        return ""


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

    def tower_up(self):
        return self._conn is not None and bool(self._tower)

    def request(self, method, arg=None):
        """Call a tower method and wait for the answer (its string result, "" if it has none); None if the tower
        isn't up or didn't answer."""
        if not self.tower_up():
            return None
        try:
            r = self._conn.call_sync(NAME, PATH, IFACE, method,
                                     util.GLib.Variant("(s)", (arg,)) if arg is not None else None, None,
                                     util.Gio.DBusCallFlags.NONE, 2000, None)
        except Exception:
            return None
        return r.unpack()[0] if r is not None and r.n_children() else ""

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
        info = {"pid": os.getpid(), "impl": "python", "version": __version__, "protocol": PROTOCOL}
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
            if not valid_message(msg) or e.get("flight") == me:
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
        msg = parse(raw)
        if not valid_message(msg):
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
