"""Integration with UWP (https://github.com/RegulusArms/UWP), a wallpaper manager for GNOME.

When UWP is installed, "Set as Wallpaper" hands the image to UWP, which adds it to its library and
shows it on every monitor as a new profile. While UWP's editor window is open, files can also be
sent to the monitors selected there ("Add to Selected UWP Monitor").

Talks to the running UWP over D-Bus (GApplication actions); `uwp --set-wallpaper` also starts it.
"""
import os
import shutil
import subprocess

from . import util

BUS_NAME = "io.github.RegulusArms.UWP"
OBJECT_PATH = "/io/github/RegulusArms/UWP"
ACTIONS = "org.gtk.Actions"


def command():
    """Path of the `uwp` launcher, or None if UWP isn't installed."""
    found = shutil.which("uwp")
    if found:
        return found
    local = os.path.expanduser("~/.local/bin/uwp")  # install.sh's location; not always on PATH for GUI apps
    return local if os.access(local, os.X_OK) else None


def available():
    return command() is not None


def wallpaper_label():
    """Menu text for "Set as Wallpaper", saying when it goes through UWP."""
    return "Set as Wallpaper (UWP)" if available() else "Set as Wallpaper"


def set_wallpaper(path):
    """Show path on every monitor as a new UWP profile (starts UWP if needed). False if UWP is missing."""
    cmd = command()
    if not cmd:
        return False
    subprocess.Popen([cmd, "--set-wallpaper", os.path.abspath(path)], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def editor_open():
    """True if UWP is running with its editor window open (so add_to_selected() will work)."""
    if util.Gio:
        try:
            res = _bus().call_sync(BUS_NAME, OBJECT_PATH, ACTIONS, "Describe",
                                   util.GLib.Variant("(s)", ("add-to-selected",)), None,
                                   util.Gio.DBusCallFlags.NO_AUTO_START, 300, None)
            return bool(res.unpack()[0][0])
        except Exception:
            return False
    out = _gdbus("Describe", "add-to-selected")
    return out is not None and out.lstrip("(").startswith("true")


def add_to_selected(path):
    """Add path to UWP's library and put it on the monitors selected in its editor."""
    path = os.path.abspath(path)
    if util.Gio:
        V = util.GLib.Variant
        try:
            _bus().call_sync(BUS_NAME, OBJECT_PATH, ACTIONS, "Activate",
                             V("(sava{sv})", ("add-to-selected", [V("s", path)], {})), None,
                             util.Gio.DBusCallFlags.NO_AUTO_START, 2000, None)
            return True
        except Exception:
            return False
    quoted = path.replace("\\", "\\\\").replace("'", "\\'")
    return _gdbus("Activate", "add-to-selected", f"[<'{quoted}'>]", "{}") is not None


def _bus():
    return util.Gio.bus_get_sync(util.Gio.BusType.SESSION, None)


def _gdbus(method, *args):
    """Call an org.gtk.Actions method with the gdbus CLI; stdout, or None on failure."""
    try:
        r = subprocess.run(["gdbus", "call", "--session", "--dest", BUS_NAME, "--object-path", OBJECT_PATH,
                            "--method", f"{ACTIONS}.{method}", *args],
                           capture_output=True, text=True, timeout=2)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None
