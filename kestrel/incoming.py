"""Requests from outside this Kestrel: changes other Kestrels report through the tower (on_atc), folders handed over to
open as tabs (Preferences → "Open folders from other apps as tabs in an open Kestrel window"), and other apps'
FileManager1 requests such as a browser's "Show in folder" (handle_fm1)."""
import os

from PyQt6.QtCore import QDateTime, Qt

from . import atc, env, places, util

_last_active = None      # the window used most recently
_last_active_ms = 0


def on_atc(msg):
    """A change reported by another Kestrel through the tower (see atc.py), or by this one ("own")."""
    kind = msg.get("type")
    if kind == "sidebar":   # its order or collapsed sections; also refreshes this process's other windows
        if not msg.get("own"):
            A._settings.sync()
        for w in A.WINDOWS:
            w.sidebar.refresh()
    if kind == "bookmarks":   # also refreshes this process's other windows
        for w in A.WINDOWS:
            w.sidebar.refresh()
            for p in w.panes():
                if p.is_overview():
                    p.refresh()
    if msg.get("own"):
        return   # the rest was already applied where it was changed
    if kind == "open" and msg.get("flight") == atc.radio().flight():
        # folders another Kestrel handed over (open_in_tabs)
        folders = [f for f in msg.get("folders") or [] if isinstance(f, str)]
        select = [f if isinstance(f, str) else "" for f in msg.get("select") or []]
        if not folders:
            return
        use_token(msg.get("token") or "")
        w = recent_window()
        if w is not None:
            open_as_tabs(w, folders, select)
        else:   # our last window closed in the meantime
            w = A.open_window(folders[:1])
            open_as_tabs(w, folders[1:], select[1:])
            if w.pane() is not None and select[:1] and select[0]:
                w.pane().select_later(select[0])
    elif kind == "settings":
        A._settings.sync()
        A.apply_preferences()
    elif kind == "starred":
        places.reload_starred()
    elif kind == "folders":
        A._thumbs.reload_styles()
        for path in msg.get("paths") or []:
            if isinstance(path, str):
                A._thumbs.invalidate(path, disk=False)   # the sender already removed the cached mosaic
    elif kind == "thumbs_cleared":
        A._thumbs.clear_memory()
        for w in A.WINDOWS:
            for p in w.panes():
                p.animator.clear()
        A.repaint_all()


_last_active = None      # the window used most recently
_last_active_ms = 0


def report_windows():
    """For the tower's Handoff: whether we have windows, and when one was last used."""
    atc.announce("windows", keep=True, count=len(A.WINDOWS), active=float(_last_active_ms))


def window_focused(win):
    """(focusWindowChanged) Remembers which window was used last."""
    global _last_active, _last_active_ms
    for w in A.WINDOWS:
        if win is not None and w.windowHandle() is win:
            _last_active, _last_active_ms = w, QDateTime.currentMSecsSinceEpoch()
            report_windows()


def open_in_tabs():
    # never from a conda environment the user activated: the Kestrel taking over may run in a different one
    return A._settings.value("open_in_tabs", False, type=bool) and not env.explicit_conda_env()


def recent_window():
    if _last_active is not None and _last_active in A.WINDOWS:
        return _last_active
    return A.WINDOWS[-1] if A.WINDOWS else None


def use_token(token):
    # the launcher's activation token: without it GNOME (Wayland) won't let the window come to the front
    if token:
        os.environ["XDG_ACTIVATION_TOKEN"] = token
        os.environ["DESKTOP_STARTUP_ID"] = token


def open_as_tabs(w, folders, select):
    """Folders as new tabs (selecting select[i] in folders[i] when given); the first becomes current."""
    first = w.tabs.count()
    for i, folder in enumerate(folders):
        item = select[i] if i < len(select) else ""
        if not item:
            w.open_location(folder, new_tab=True)
        else:
            pane = w.new_tab(folder, activate=False)
            if pane is not None:
                pane.select_later(item)
    if w.tabs.count() > first:
        w.tabs.setCurrentIndex(first)
        w.pane().view().setFocus()
    w.setWindowState(w.windowState() & ~Qt.WindowState.WindowMinimized)
    w.raise_()
    w.activateWindow()


def tabs_instead(folders, select):
    """True if the folders went to an open window as tabs: one of ours, or another Kestrel's (through the tower)."""
    if not open_in_tabs():
        return False
    w = recent_window()
    if w is not None:
        open_as_tabs(w, folders, select)
        return True
    return bool(atc.hand_off(folders, select))


def handle_fm1(method, uris, startup_id=""):
    """A request to the org.freedesktop.FileManager1 service (see fm1.py), e.g. a browser's "Show in folder"."""
    paths = [p for p in (util.uri_to_path(u) for u in uris) if p]
    if not paths:
        return
    if startup_id:
        # the caller's activation token: without it GNOME (Wayland) won't let the new window take focus
        os.environ["XDG_ACTIVATION_TOKEN"] = startup_id
        os.environ["DESKTOP_STARTUP_ID"] = startup_id
    if method == "ShowItemProperties":
        w = A.WINDOWS[-1] if A.WINDOWS else A.open_window([os.path.dirname(paths[0])])
        w.properties(paths)
        return
    if method == "ShowFolders":
        if tabs_instead(paths, []):
            return
        w = A.open_window(paths)
    else:
        # ShowItems: each item's folder in a tab, with the item selected, scrolled to and focused (folders too:
        # they're shown in their parent, not opened)
        groups = {}
        for p in paths:
            groups.setdefault(os.path.dirname(p.rstrip("/")) or "/", []).append(p)
        if tabs_instead(list(groups), [items[0] for items in groups.values()]):
            return
        w = A.open_window([next(iter(groups))])
        for i, (folder, items) in enumerate(groups.items()):
            pane = w.pane() if i == 0 else w.new_tab(folder, activate=False)
            if pane is not None:
                pane.select_later(items[0])
    w.raise_()
    w.activateWindow()
    if w.pane():
        w.tabs.setCurrentIndex(0)
        w.pane().view().setFocus()


# Last, so either module can be imported first (app.py imports this one): the window list, settings, thumbnails and
# open_window, read when the functions above are called.
from . import app as A  # noqa: E402
