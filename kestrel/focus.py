"""Giving the focus to the window a drag from Kestrel was dropped into (a browser, an editor, another app).

On X11 Kestrel asks the window manager to activate the window under the pointer. On GNOME on Wayland an app can't
focus another app's window, so a small GNOME Shell extension does it (data/gnome-shell/, installed and enabled by
kes-setup --default); without it the focus stays on Kestrel.
"""
import ctypes
import ctypes.util

from PyQt6.QtGui import QGuiApplication

from . import util


class _XClientMessageEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p), ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
                ("format", ctypes.c_int), ("l", ctypes.c_long * 5)]


class _XEvent(ctypes.Union):
    _fields_ = [("xclient", _XClientMessageEvent), ("pad", ctypes.c_long * 24)]


_x = None  # (libX11, display), opened on first use


def _xlib():
    global _x
    if _x is None:
        _x = (None, None)
        name = ctypes.util.find_library("X11")
        if name:
            lib = ctypes.CDLL(name)
            lib.XOpenDisplay.restype = ctypes.c_void_p
            lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
            lib.XDefaultRootWindow.restype = ctypes.c_ulong
            lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            lib.XInternAtom.restype = ctypes.c_ulong
            lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
            lib.XQueryPointer.argtypes = [ctypes.c_void_p, ctypes.c_ulong] + [ctypes.c_void_p] * 7
            lib.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong] + [ctypes.c_void_p] * 4
            lib.XGetWindowProperty.argtypes = ([ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long,
                                                ctypes.c_long, ctypes.c_int, ctypes.c_ulong] + [ctypes.c_void_p] * 5)
            lib.XFree.argtypes = [ctypes.c_void_p]
            lib.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.c_void_p]
            lib.XFlush.argtypes = [ctypes.c_void_p]
            dpy = lib.XOpenDisplay(None)
            if dpy:
                _x = (lib, dpy)
    return _x


def _has_property(lib, dpy, w, prop):
    typ, fmt = ctypes.c_ulong(), ctypes.c_int()
    n, after, data = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
    ok = lib.XGetWindowProperty(dpy, w, prop, 0, 0, 0, 0, ctypes.byref(typ), ctypes.byref(fmt), ctypes.byref(n),
                                ctypes.byref(after), ctypes.byref(data)) == 0
    if data:
        lib.XFree(data)
    return ok and typ.value != 0


def _client_window(lib, dpy, w, wm_state, depth=0):
    """The app's own window in a top-level (the window manager's frame around it): the one with WM_STATE."""
    if _has_property(lib, dpy, w, wm_state):
        return w
    if depth > 4:
        return 0
    root, parent, kids, n = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.POINTER(ctypes.c_ulong)(), ctypes.c_uint()
    if not lib.XQueryTree(dpy, w, ctypes.byref(root), ctypes.byref(parent), ctypes.byref(kids), ctypes.byref(n)):
        return 0
    found = 0
    for i in range(n.value - 1, -1, -1):  # topmost first
        found = _client_window(lib, dpy, kids[i], wm_state, depth + 1)
        if found:
            break
    if kids:
        lib.XFree(kids)
    return found


def _activate_x11():
    lib, dpy = _xlib()
    if not dpy:
        return
    root = lib.XDefaultRootWindow(dpy)
    r, child = ctypes.c_ulong(), ctypes.c_ulong()
    xy = [ctypes.c_int() for _ in range(4)]
    mask = ctypes.c_uint()
    if not lib.XQueryPointer(dpy, root, ctypes.byref(r), ctypes.byref(child), *[ctypes.byref(v) for v in xy],
                             ctypes.byref(mask)) or not child.value:
        return
    win = _client_window(lib, dpy, child.value, lib.XInternAtom(dpy, b"WM_STATE", 0))
    if not win:
        return
    # _NET_ACTIVE_WINDOW from a pager (source 2): window managers honour it, unlike an app's own request
    ev = _XEvent()
    ev.xclient.type = 33  # ClientMessage
    ev.xclient.send_event = 1
    ev.xclient.display = dpy
    ev.xclient.window = win
    ev.xclient.message_type = lib.XInternAtom(dpy, b"_NET_ACTIVE_WINDOW", 0)
    ev.xclient.format = 32
    ev.xclient.l[0] = 2
    ev.xclient.l[1] = 0  # CurrentTime
    lib.XSendEvent(dpy, root, 0, (1 << 20) | (1 << 19), ctypes.byref(ev))  # SubstructureRedirect | SubstructureNotify
    lib.XFlush(dpy)


def _activate_gnome_shell():
    """The Kestrel drop focus extension (data/gnome-shell/); without it the call fails, and nothing happens."""
    Gio = util.Gio
    if not Gio:
        return
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except Exception:
        return
    bus.call("org.gnome.Shell", "/com/regulusarms/KestrelFocus", "com.regulusarms.KestrelFocus", "ActivateAtPointer",
             None, None, Gio.DBusCallFlags.NO_AUTO_START, 2000, None, None)


def activate_at_pointer():
    """The window under the pointer gets the focus (if the desktop lets us)."""
    platform = QGuiApplication.platformName()
    if platform == "xcb":
        _activate_x11()
    elif platform.startswith("wayland"):
        _activate_gnome_shell()
