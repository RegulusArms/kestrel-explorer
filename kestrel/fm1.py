"""The org.freedesktop.FileManager1 D-Bus service: how browsers and other apps open "the file manager".

Chromium-based browsers ("Show in folder"), Firefox ("Open Containing Folder") and many other apps call
ShowItems / ShowFolders on this service instead of using the default folder app (xdg-mime). GNOME Files normally
provides it; while Kestrel owns the name, those requests open Kestrel. install.sh --default also installs a D-Bus
activation file so the bus starts `kes --dbus-service` when no Kestrel is running.
"""
from . import util

NAME = "org.freedesktop.FileManager1"
PATH = "/org/freedesktop/FileManager1"
XML = """
<node>
  <interface name="org.freedesktop.FileManager1">
    <method name="ShowFolders"><arg type="as" name="URIs" direction="in"/><arg type="s" name="StartupId" direction="in"/></method>
    <method name="ShowItems"><arg type="as" name="URIs" direction="in"/><arg type="s" name="StartupId" direction="in"/></method>
    <method name="ShowItemProperties"><arg type="as" name="URIs" direction="in"/><arg type="s" name="StartupId" direction="in"/></method>
  </interface>
</node>
"""

_owner_id = None
_registration = None


def start(handler, on_lost=None):
    """Try to own the service name. handler(method, uris, startup_id) runs on the UI thread for each request;
    on_lost() runs if the name couldn't be owned (another file manager has it) or was taken away."""
    global _owner_id
    Gio = util.Gio
    if not Gio or _owner_id is not None:
        return
    node = Gio.DBusNodeInfo.new_for_xml(XML)

    def on_call(_conn, _sender, _path, _iface, method, params, invocation):
        uris, startup_id = params.unpack()
        invocation.return_value(None)  # answer straight away: the caller doesn't wait for the window
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, lambda: handler(method, list(uris), startup_id))

    def on_bus(conn, _name):
        global _registration
        _registration = conn.register_object(PATH, node.interfaces[0], on_call, None, None)

    def lost(_conn, _name):
        if on_lost:
            on_lost()

    _owner_id = Gio.bus_own_name(Gio.BusType.SESSION, NAME, Gio.BusNameOwnerFlags.DO_NOT_QUEUE, on_bus, None, lost)
