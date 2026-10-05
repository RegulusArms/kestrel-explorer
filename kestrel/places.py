"""Starred and Recent: places that list files from anywhere, like the combined Trash.

Starred items are Kestrel's own list (~/.config/kestrel-explorer/starred.json). Recent is the desktop's shared
recently-used list (~/.local/share/recently-used.xbel), which GTK apps write too; files opened from Kestrel are
added to it unless "Remember recent files" is off in GNOME's privacy settings.
"""
import json
import os
import time
import xml.etree.ElementTree as ET

from PyQt6.QtCore import QObject, pyqtSignal

from . import atc, util

STARRED = "starred://"
RECENT = "recent://"
VIRTUAL = {STARRED: ("Starred", "starred"), RECENT: ("Recent", "document-open-recent")}
EMPTY_TEXT = {STARRED: "No starred items — right-click a file or folder and choose Star",
              RECENT: "No recent files"}
STARRED_FILE = util.CONFIG_DIR / "starred.json"
RECENT_FILE = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.join(util.HOME, ".local/share")),
                           "recently-used.xbel")
_NS = {"bookmark": "http://www.freedesktop.org/standards/desktop-bookmarks",
       "mime": "http://www.freedesktop.org/standards/shared-mime-info"}
for _prefix, _uri in _NS.items():
    ET.register_namespace(_prefix, _uri)


class _Signals(QObject):
    starred_changed = pyqtSignal()


signals = _Signals()
_starred = None


def title(place):
    return VIRTUAL[place][0]


def icon_name(place):
    return VIRTUAL[place][1]


# ---------------------------------------------------------------- starred

def starred():
    global _starred
    if _starred is None:
        try:
            _starred = [p for p in json.loads(STARRED_FILE.read_text()) if isinstance(p, str)]
        except (OSError, ValueError):
            _starred = []
    return _starred


def is_starred(path):
    return path in _starred_set()


_set_cache = (None, frozenset())


def _starred_set():
    global _set_cache
    lst = starred()
    if _set_cache[0] is not lst:
        _set_cache = (lst, frozenset(lst))
    return _set_cache[1]


def set_starred(paths, on):
    global _starred
    _starred = None   # another Kestrel may have changed it since
    current = list(starred())
    if on:
        current += [p for p in paths if p not in current]
    else:
        drop = set(paths)
        current = [p for p in current if p not in drop]
    _starred = current
    try:
        STARRED_FILE.parent.mkdir(parents=True, exist_ok=True)
        STARRED_FILE.write_text(json.dumps(current, indent=1))
    except OSError:
        pass
    signals.starred_changed.emit()
    atc.announce("starred")


def reload_starred():
    """Re-read the list (another Kestrel changed it) and emit starred_changed."""
    global _starred
    _starred = None
    starred()
    signals.starred_changed.emit()


def items(place):
    """[(path, original path)] for a place, as util.trashed_items() gives for the trash (here both the same)."""
    paths = [p for p in starred() if os.path.lexists(p)] if place == STARRED else recent_files()
    return [(p, p) for p in paths]


# ---------------------------------------------------------------- recent

def recent_files(limit=500):
    """Local files from the recently-used list, newest first (files that no longer exist are left out)."""
    try:
        root = ET.parse(RECENT_FILE).getroot()
    except (OSError, ET.ParseError):
        return []
    entries = []
    for bm in root.findall("bookmark"):
        path = util.uri_to_path(bm.get("href", ""))
        if not path or not os.path.lexists(path):
            continue
        stamp = max(bm.get("visited", ""), bm.get("modified", ""), bm.get("added", ""))
        entries.append((stamp, path))
    entries.sort(reverse=True)
    out, seen = [], set()
    for _, p in entries:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out[:limit]


def _remember_recent():
    Gio = util.Gio
    try:
        schema = util.desktop_schema("org.gnome.desktop.privacy")  # GNOME's, or Cinnamon's own
        if util.has_schema_key(schema, "remember-recent-files"):
            return Gio.Settings.new(schema).get_boolean("remember-recent-files")
    except Exception:
        pass
    return True


def add_recent(paths):
    """Add files opened from Kestrel to the recently-used list (as GNOME Files does)."""
    paths = [p for p in paths if os.path.isfile(p)]
    if not paths or not util.Gio or not _remember_recent():
        return
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime())
    try:
        tree = ET.parse(RECENT_FILE)
        root = tree.getroot()
    except (OSError, ET.ParseError):
        root = ET.Element("xbel", {"version": "1.0"})
        tree = ET.ElementTree(root)
    by_href = {bm.get("href"): bm for bm in root.findall("bookmark")}
    b, m = "{%s}" % _NS["bookmark"], "{%s}" % _NS["mime"]
    for p in paths:
        href = util.file_uri(p)
        bm = by_href.get(href)
        if bm is None:
            bm = ET.SubElement(root, "bookmark", {"href": href, "added": now})
            info = ET.SubElement(bm, "info")
            meta = ET.SubElement(info, "metadata", {"owner": "http://freedesktop.org"})
            ET.SubElement(meta, m + "mime-type", {"type": util.content_type(p)})
            ET.SubElement(meta, b + "applications")
        bm.set("modified", now)
        bm.set("visited", now)
        apps = bm.find(f"info/metadata/{b}applications")
        if apps is not None:
            app = next((a for a in apps.findall(b + "application") if a.get("name") == util.APP_NAME), None)
            if app is None:
                app = ET.SubElement(apps, b + "application",
                                    {"name": util.APP_NAME, "exec": f"'{util.APP_COMMAND} %u'", "count": "0"})
            app.set("modified", now)
            app.set("count", str(int(app.get("count", "0") or 0) + 1))
    tmp = RECENT_FILE + ".kestrel-tmp"
    try:
        os.makedirs(os.path.dirname(RECENT_FILE), exist_ok=True)
        tree.write(tmp, encoding="UTF-8", xml_declaration=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, RECENT_FILE)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
