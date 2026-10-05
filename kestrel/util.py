"""Shared helpers: paths, mime types, icons, desktop integration."""
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from PyQt6.QtCore import QMimeDatabase, QUrl
from PyQt6.QtGui import QIcon, QImageReader

try:
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib  # noqa: F401
except Exception:  # pragma: no cover - Gio is optional
    Gio = None

APP_NAME = "Kestrel Explorer"
APP_ID = "kestrel-explorer"
APP_COMMAND = "kes"
LEGACY_NAMES = ("Folder Explorer",)  # previous name: thumbnails/settings may carry it
LEGACY_ID = "folder-explorer"
HOME = str(Path.home())
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_ID
CACHE_ROOT = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
THUMB_DIR = CACHE_ROOT / "thumbnails"
APP_CACHE = CACHE_ROOT / APP_ID
TRASH_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "Trash"
GTK_BOOKMARKS = Path.home() / ".config/gtk-3.0/bookmarks"

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg", ".3gp", ".ts", ".m2ts"}
# camera RAW formats (decoded by the kimageformats/LibRaw plugin)
RAW_EXTS = {".3fr", ".arw", ".crw", ".cr2", ".cr3", ".dcr", ".dng", ".erf", ".fff", ".iiq", ".k25", ".kdc",
            ".mdc", ".mef", ".mos", ".mrw", ".nef", ".nrw", ".orf", ".pef", ".raf", ".raw", ".rw2", ".rwl",
            ".sr2", ".srf", ".srw", ".x3f"}

_image_exts = None
_mime_db = QMimeDatabase()
_icon_cache = {}


def image_exts():
    global _image_exts
    if _image_exts is None:
        skip = {"ani", "cur", "pdf"}
        _image_exts = {"." + bytes(f).decode().lower() for f in QImageReader.supportedImageFormats()
                       if bytes(f).decode().lower() not in skip}
    return _image_exts


def ext_of(path):
    return os.path.splitext(path)[1].lower()


def is_image(path):
    return ext_of(path) in image_exts()


def is_raw(path):
    return ext_of(path) in RAW_EXTS


def image_reader(path):
    """QImageReader set up for fast, correctly oriented loading.

    For camera RAW files, quality 0 makes the LibRaw plugin return the embedded
    JPEG preview (~0.3 s) instead of demosaicing the sensor data (~20 s).
    """
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    if is_raw(path):
        reader.setQuality(0)
    return reader


def is_video(path):
    return ext_of(path) in VIDEO_EXTS


DEVICE_PATH_RE = re.compile(r"^/run/user/\d+/gvfs/(afc|gphoto2|mtp):")


def is_device_path(path):
    """A phone or camera mounted by gvfs (/run/user/<uid>/gvfs/afc:…, gphoto2:…, mtp:…): each read is slow (gphoto2
    downloads the whole file) and the backend serves one request at a time, so nothing may scan it in bulk."""
    return bool(DEVICE_PATH_RE.match(path or ""))


LOCAL_COPY_RE = re.compile(r"^/run/user/\d+/gvfs/gphoto2:")


def needs_local_copy(path):
    """On a device whose backend downloads the whole file on every open (gvfs gphoto2), so a player that opens and
    seeks it repeatedly downloads it again each time: open a local copy instead (fileops.fetch_local)."""
    return bool(LOCAL_COPY_RE.match(path or ""))


def device_uri(path):
    """The gvfs URI (gphoto2://…, mtp://…) of a file on a device, from the mount that holds its FUSE path; None if
    none. Main thread: uses Gio's volume monitor."""
    if not Gio:
        return None
    for m in Gio.VolumeMonitor.get().get_mounts():
        root = m.get_root()
        r = root.get_path()
        if r and path.startswith(r + "/"):
            return root.resolve_relative_path(path[len(r) + 1:]).get_uri()
    return None


def file_uri(path):
    """URI escaped the way GLib does it (needed for the freedesktop thumbnail cache)."""
    return "file://" + quote(os.path.abspath(path), safe="/!$&'()*+,;=:@")


def uri_to_path(uri):
    u = urlparse(uri)
    if u.scheme not in ("", "file"):
        return None
    return unquote(u.path)


def md5(s):
    return hashlib.md5(s.encode("utf-8", "surrogateescape")).hexdigest()


def human_size(n):
    if n is None:
        return ""
    n = float(n)
    for unit in ("bytes", "kB", "MB", "GB", "TB", "PB"):
        if n < 1000 or unit == "PB":
            return f"{int(n)} bytes" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1000


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def unique_path(directory, name, style="copy"):
    """Return a non-existing path in directory based on name ("x (copy).jpg", "x (2).jpg", ...)."""
    target = os.path.join(directory, name)
    if not os.path.lexists(target):
        return target
    stem, ext = split_ext(name)
    stem = re.sub(r" \((copy|copy \d+|\d+)\)$", "", stem)
    i = 1
    while True:
        if style == "copy":
            suffix = " (copy)" if i == 1 else f" (copy {i})"
        else:
            suffix = f" ({i + 1})"
        target = os.path.join(directory, f"{stem}{suffix}{ext}")
        if not os.path.lexists(target):
            return target
        i += 1


def split_ext(name):
    low = name.lower()
    for e in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst"):
        if low.endswith(e) and len(name) > len(e):
            return name[: -len(e)], name[-len(e):]
    stem, ext = os.path.splitext(name)
    if not stem:  # dotfile like ".bashrc"
        return name, ""
    return stem, ext


def mime_for(path, is_dir=None):
    if is_dir if is_dir is not None else os.path.isdir(path):
        return _mime_db.mimeTypeForName("inode/directory")
    return _mime_db.mimeTypeForFile(path, QMimeDatabase.MatchMode.MatchExtension)


def mime_for_content(path):
    return _mime_db.mimeTypeForFile(path, QMimeDatabase.MatchMode.MatchDefault)


SPECIAL_DIR_ICONS = {}


def _init_special_dirs():
    names = {"DESKTOP": "user-desktop", "DOCUMENTS": "folder-documents", "DOWNLOAD": "folder-download",
             "MUSIC": "folder-music", "PICTURES": "folder-pictures", "VIDEOS": "folder-videos",
             "TEMPLATES": "folder-templates", "PUBLICSHARE": "folder-publicshare"}
    for key, icon in names.items():
        p = xdg_user_dir(key)
        if p and p != HOME:
            SPECIAL_DIR_ICONS[p] = icon
    SPECIAL_DIR_ICONS[HOME] = "user-home"
    SPECIAL_DIR_ICONS[str(TRASH_DIR / "files")] = "user-trash"


_xdg_dirs = None


def xdg_user_dir(key):
    global _xdg_dirs
    if _xdg_dirs is None:
        _xdg_dirs = {}
        cfg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "user-dirs.dirs"
        try:
            for line in cfg.read_text().splitlines():
                m = re.match(r'XDG_(\w+)_DIR="(.*)"', line.strip())
                if m:
                    _xdg_dirs[m.group(1)] = m.group(2).replace("$HOME", HOME)
        except OSError:
            pass
    defaults = {"DESKTOP": "Desktop", "DOCUMENTS": "Documents", "DOWNLOAD": "Downloads", "MUSIC": "Music",
                "PICTURES": "Pictures", "VIDEOS": "Videos", "TEMPLATES": "Templates", "PUBLICSHARE": "Public"}
    return _xdg_dirs.get(key) or os.path.join(HOME, defaults.get(key, key.title()))


def theme_icon(*names):
    key = names
    icon = _icon_cache.get(key)
    if icon is None:
        icon = QIcon()
        for n in names:
            if n and QIcon.hasThemeIcon(n):
                icon = QIcon.fromTheme(n)
                break
        _icon_cache[key] = icon
    return icon


def icon_for_path(path, is_dir=None):
    if is_dir is None:
        is_dir = os.path.isdir(path)
    if is_dir:
        if not SPECIAL_DIR_ICONS:
            _init_special_dirs()
        return theme_icon(SPECIAL_DIR_ICONS.get(path, "folder"), "folder")
    m = mime_for(path, False)
    return theme_icon(m.iconName(), m.genericIconName(), "text-x-generic")


def has_schema_key(schema, key=None):
    """The settings schema is installed, and has that key (None: any)."""
    source = Gio.SettingsSchemaSource.get_default() if Gio else None
    s = source.lookup(schema, True) if source else None
    return s is not None and (key is None or s.has_key(key))


def desktop_schema(gnome_schema):
    """The desktop's settings schema for a GNOME one: Cinnamon (Linux Mint) keeps its own copies,
    org.cinnamon.desktop.*, and uses those; everything else uses GNOME's."""
    desktops = os.environ.get("XDG_CURRENT_DESKTOP", "").lower().split(":")
    if "x-cinnamon" in desktops and gnome_schema.startswith("org.gnome."):
        cinnamon = "org.cinnamon." + gnome_schema[len("org.gnome."):]
        if has_schema_key(cinnamon):
            return cinnamon
    return gnome_schema


def setup_icon_theme():
    paths = list(QIcon.themeSearchPaths())
    data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    for d in [os.path.join(HOME, ".local/share")] + data_dirs:
        p = os.path.join(d, "icons")
        if os.path.isdir(p) and p not in paths:
            paths.append(p)
    QIcon.setThemeSearchPaths(paths)
    if not QIcon.themeName() or QIcon.themeName() == "hicolor":
        theme = "Adwaita"
        try:
            out = subprocess.run(["gsettings", "get", desktop_schema("org.gnome.desktop.interface"), "icon-theme"],
                                 capture_output=True, text=True, timeout=2).stdout.strip().strip("'")
            if out:
                theme = out
        except Exception:
            pass
        QIcon.setThemeName(theme)
    QIcon.setFallbackThemeName("Adwaita")


# ---------------------------------------------------------------- theme
# Colours derived from the desktop's palette, so they suit any theme, light or dark.

def dark_theme():
    """The window background is dark."""
    from PyQt6.QtGui import QGuiApplication, QPalette
    return QGuiApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128


def blend(a, b, t):
    """QColor between a (t = 0) and b (t = 1)."""
    from PyQt6.QtGui import QColor
    return QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * t, a.greenF() + (b.greenF() - a.greenF()) * t,
                           a.blueF() + (b.blueF() - a.blueF()) * t)


def card_color():
    """A card (Overview) that stands out a little from the window background: the theme's base colour when it differs
    from the window's (most light themes); otherwise a shade towards the text."""
    from PyQt6.QtGui import QGuiApplication, QPalette
    pal = QGuiApplication.palette()
    base, window = pal.color(QPalette.ColorRole.Base), pal.color(QPalette.ColorRole.Window)
    if abs(base.lightness() - window.lightness()) >= 8:
        return base
    return blend(window, pal.color(QPalette.ColorRole.Text), 0.05)


def card_border():
    from PyQt6.QtGui import QGuiApplication, QPalette
    pal = QGuiApplication.palette()
    return blend(pal.color(QPalette.ColorRole.Window), pal.color(QPalette.ColorRole.Text), 0.14)


def error_color():
    """Red text that is readable on the window background (GNOME's error colours)."""
    from PyQt6.QtGui import QColor
    return QColor("#ff7b63" if dark_theme() else "#c01c28")


def accent_color():
    """The desktop's accent (the theme's selection colour)."""
    from PyQt6.QtGui import QGuiApplication, QPalette
    return QGuiApplication.palette().color(QPalette.ColorRole.Highlight)


_palette_watcher = None


def on_palette_change(owner, fn):
    """Call fn whenever the desktop's colours change (a light/dark switch, another theme). Qt updates its palette, but
    a stylesheet resolves palette(...) once and colours read earlier stay as they were: stylesheets that use
    palette(...) are reapplied first, then fn runs. Stops when owner is deleted."""
    global _palette_watcher
    if _palette_watcher is None:
        from PyQt6 import sip
        from PyQt6.QtCore import QEvent, QTimer
        from PyQt6.QtWidgets import QApplication, QWidget

        class PaletteWatcher(QWidget):
            """Hears the application's palette change: every widget gets ApplicationPaletteChange, this hidden one
            included."""

            def __init__(self):
                super().__init__()
                self.fns = []
                self.queued = False

            def event(self, ev):
                if ev.type() == QEvent.Type.ApplicationPaletteChange and not self.queued:
                    self.queued = True  # a theme switch can change the palette several times in a row
                    QTimer.singleShot(0, self.apply)
                return super().event(ev)

            def apply(self):
                self.queued = False
                for w in QApplication.allWidgets():
                    ss = w.styleSheet()
                    if "palette(" in ss:
                        w.setStyleSheet("")
                        w.setStyleSheet(ss)
                self.fns = [(o, f) for o, f in self.fns if not sip.isdeleted(o)]
                for o, f in list(self.fns):
                    if not sip.isdeleted(o):
                        f()
        _palette_watcher = PaletteWatcher()
    _palette_watcher.fns.append((owner, fn))


# ---------------------------------------------------------------- the GTK theme's colours (Qt < 6.5)
# Qt before 6.5 (Ubuntu 24.04, Linux Mint 22) takes no colours from the GTK theme and doesn't follow theme changes; Qt
# 6.5+ does both. There Kestrel reads the theme's named colours through GTK (GTK_COLORS_SCRIPT, run by the system's
# python3 with GTK's bindings) and follows the desktop's theme setting. follow_gtk_theme() does nothing on newer Qt.

# prints the theme's named colours as JSON (GTK 3 reads the desktop's current theme when it starts)
GTK_COLORS_SCRIPT = """import json, gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
ctx = Gtk.Window().get_style_context()
out = {}
for name in ("theme_bg_color", "theme_fg_color", "theme_base_color", "theme_text_color", "theme_selected_bg_color",
             "theme_selected_fg_color", "insensitive_fg_color", "link_color"):
    found, c = ctx.lookup_color(name)
    if found:
        out[name] = "#%02x%02x%02x" % (round(c.red * 255), round(c.green * 255), round(c.blue * 255))
print(json.dumps(out))
"""


def needs_gtk_palette(qt_version):
    v = (qt_version.split(".") + ["0", "0"])[:2]
    return v[0] == "6" and int(v[1]) < 5


def gtk_palette_from(colors):
    """The palette for a GTK theme's named colours, or None without the basic ones."""
    from PyQt6.QtGui import QColor, QPalette

    def color(name, fallback=None):
        c = QColor(colors.get(name, ""))
        return c if c.isValid() else fallback
    bg, fg, sel = color("theme_bg_color"), color("theme_fg_color"), color("theme_selected_bg_color")
    if bg is None or fg is None or sel is None:
        return None
    base, text = color("theme_base_color", bg), color("theme_text_color", fg)
    sel_text = color("theme_selected_fg_color", QColor("white" if sel.lightness() < 150 else "black"))
    disabled, link = color("insensitive_fg_color", blend(fg, bg, 0.5)), color("link_color", sel)
    R = QPalette.ColorRole
    p = QPalette(bg, bg)  # derives the frame shades (light, mid, dark, shadow) from the background
    for role, c in ((R.Window, bg), (R.WindowText, fg), (R.Button, bg), (R.ButtonText, fg), (R.Base, base),
                    (R.AlternateBase, blend(base, text, 0.04)), (R.Text, text),
                    (R.PlaceholderText, blend(text, base, 0.45)), (R.Highlight, sel), (R.HighlightedText, sel_text),
                    (R.Link, link), (R.LinkVisited, link), (R.ToolTipBase, base), (R.ToolTipText, text)):
        p.setColor(role, c)
    for role in (R.WindowText, R.Text, R.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    p.setColor(QPalette.ColorGroup.Disabled, R.Highlight, blend(sel, bg, 0.5))
    return p


def _apply_gtk_palette(wait):
    """Read the theme's colours with GTK in a helper process (Kestrel itself doesn't use GTK)."""
    import json
    from PyQt6.QtCore import QProcess
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtWidgets import QApplication
    proc = QProcess(QApplication.instance())

    def apply():
        try:
            colors = json.loads(bytes(proc.readAllStandardOutput()).decode() or "{}")
        except ValueError:
            colors = {}
        p = gtk_palette_from(colors)
        if p is not None and p != QGuiApplication.palette():
            QApplication.setPalette(p)  # on_palette_change() then updates what was drawn in the old colours
        proc.deleteLater()
    if wait:  # at startup: the first window opens in the theme's colours
        proc.start("/usr/bin/python3", ["-c", GTK_COLORS_SCRIPT])
        proc.waitForFinished(3000)
        apply()
        return
    proc.finished.connect(apply)
    proc.errorOccurred.connect(lambda _e: proc.deleteLater())
    proc.start("/usr/bin/python3", ["-c", GTK_COLORS_SCRIPT])


_gtk_watch = None


def follow_gtk_theme():
    """After QApplication: apply the theme's colours now, then follow changes."""
    global _gtk_watch
    from PyQt6.QtCore import QTimer, qVersion
    from PyQt6.QtGui import QGuiApplication
    if not needs_gtk_palette(qVersion()) or QGuiApplication.platformName() not in ("xcb", "wayland"):
        return
    _apply_gtk_palette(True)
    # the desktop changes several settings at once; GTK picks them up shortly after
    later = QTimer(singleShot=True, interval=600, timeout=lambda: _apply_gtk_palette(False))
    schema = desktop_schema("org.gnome.desktop.interface")  # GNOME's, or Cinnamon's own
    if not has_schema_key(schema):
        return
    settings = Gio.Settings.new(schema)
    for key in ("gtk-theme", "color-scheme"):
        if has_schema_key(schema, key):
            settings.connect("changed::" + key, lambda *_a: later.start())
    _gtk_watch = (settings, later)  # kept for the life of the app


# ---------------------------------------------------------------- desktop integration

def open_default(path):
    """Open with the default application."""
    if Gio:
        try:
            Gio.AppInfo.launch_default_for_uri(file_uri(path), None)
            return True
        except Exception:
            pass
    return subprocess.Popen(["xdg-open", path], start_new_session=True) is not None


def content_type(path):
    if Gio:
        try:
            info = Gio.File.new_for_path(path).query_info("standard::content-type", Gio.FileQueryInfoFlags.NONE, None)
            return info.get_content_type()
        except Exception:
            pass
    return mime_for_content(path).name()


def apps_for(path):
    """(recommended, others) lists of Gio.AppInfo."""
    if not Gio:
        return [], []
    ct = content_type(path)
    rec = Gio.AppInfo.get_recommended_for_type(ct) or []
    ids = {a.get_id() for a in rec}
    others = [a for a in Gio.AppInfo.get_all() if a.should_show() and a.get_id() not in ids]
    others.sort(key=lambda a: a.get_name().lower())
    return rec, others


def default_app(path):
    if not Gio:
        return None
    return Gio.AppInfo.get_default_for_type(content_type(path), False)


def apps_for_type(ct):
    """All installed Gio.AppInfo that handle content type `ct`, sorted by name."""
    if not Gio:
        return []
    apps = [a for a in Gio.AppInfo.get_all_for_type(ct) if a.should_show()]
    return sorted(apps, key=lambda a: a.get_name().lower())


def app_by_id(app_id):
    if not Gio or not app_id:
        return None
    try:
        return Gio.DesktopAppInfo.new(app_id)
    except Exception:
        return None


def launch_app(app, paths):
    app.launch_uris([file_uri(p) for p in paths], None)


def gicon_to_qicon(gicon):
    if gicon is None:
        return QIcon()
    try:
        if isinstance(gicon, Gio.ThemedIcon):
            return theme_icon(*gicon.get_names(), "application-x-executable")
        if isinstance(gicon, Gio.FileIcon):
            return QIcon(gicon.get_file().get_path())
        s = gicon.to_string()
        return QIcon(s) if s.startswith("/") else theme_icon(s, "application-x-executable")
    except Exception:
        return theme_icon("application-x-executable")


def open_terminal(directory):
    candidates = [
        ["ptyxis", "--new-window", f"--working-directory={directory}"],
        ["gnome-terminal", f"--working-directory={directory}"],
        ["kgx", f"--working-directory={directory}"],
        ["konsole", "--workdir", directory],
        ["xfce4-terminal", f"--working-directory={directory}"],
        ["x-terminal-emulator"],
    ]
    for cmd in candidates:
        if shutil.which(cmd[0]):
            subprocess.Popen(cmd, cwd=directory, start_new_session=True)
            return True
    return False


def set_wallpaper(path):
    """Through UWP when it's installed (new UWP profile with the image on every monitor), else the desktop's own."""
    from . import uwp
    if uwp.set_wallpaper(path):
        return
    uri = file_uri(path)
    schema = desktop_schema("org.gnome.desktop.background")
    for key in ("picture-uri", "picture-uri-dark"):  # Cinnamon has no -dark one
        if has_schema_key(schema, key):
            subprocess.run(["gsettings", "set", schema, key, uri], timeout=5)


def trash(path):
    """Move to trash. Raises OSError on failure."""
    if Gio:
        try:
            Gio.File.new_for_path(path).trash(None)
            return
        except GLib.Error as e:
            raise OSError(str(e.message)) from None
    from PyQt6.QtCore import QFile
    if not QFile.moveToTrash(path):
        raise OSError(f"Cannot move {path} to trash")


def mark_trusted(path):
    if Gio:
        try:
            Gio.File.new_for_path(path).set_attribute_string("metadata::trusted", "true",
                                                              Gio.FileQueryInfoFlags.NONE, None)
        except Exception:
            pass


# Filesystems that never hold a trash folder, or that could hang when probed (network mounts)
_NO_TRASH_FS = {"proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "ramfs", "cgroup", "cgroup2", "securityfs",
                "debugfs", "tracefs", "pstore", "bpf", "mqueue", "hugetlbfs", "configfs", "fusectl", "autofs",
                "squashfs", "overlay", "nsfs", "binfmt_misc", "efivarfs", "rpc_pipefs", "fuse.portal",
                "fuse.gvfsd-fuse", "fuse.snapfuse", "cifs", "smb3", "smbfs", "nfs", "nfs4", "fuse.sshfs", "sshfs",
                "davfs", "fuse.davfs2", "fuse.rclone", "9p", "afs", "ceph", "glusterfs"}


def trash_dirs():
    """Every trash folder (containing files/ and info/) for this user: the home trash, plus the per-drive
    $topdir/.Trash-$uid and $topdir/.Trash/$uid folders that files deleted on other drives go to."""
    uid = os.getuid()
    out = [str(TRASH_DIR)] if (TRASH_DIR / "files").is_dir() else []
    try:
        with open("/proc/self/mounts") as f:
            mounts = [line.split() for line in f]
    except OSError:
        mounts = []
    seen = set(out)
    for fields in mounts:
        if len(fields) < 3 or fields[2] in _NO_TRASH_FS:
            continue
        top = fields[1].replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")
        for d in (os.path.join(top, f".Trash-{uid}"), os.path.join(top, ".Trash", str(uid))):
            if d not in seen and os.path.isdir(os.path.join(d, "files")):
                seen.add(d)
                out.append(d)
    return out


def trash_root(path):
    """The trash folder a trashed item belongs to (parent of its files/ folder), or None if not in a trash."""
    files = os.path.dirname(path)
    if os.path.basename(files) != "files":
        return None
    root = os.path.dirname(files)
    name = os.path.basename(root)
    if root == str(TRASH_DIR) or name.startswith(".Trash-") or \
            (os.path.basename(os.path.dirname(root)) == ".Trash" and name == str(os.getuid())):
        return root
    return None


def in_trash(path):
    return trash_root(path) is not None


def trash_info_path(trashed_file):
    root = trash_root(trashed_file)
    return os.path.join(root, "info", os.path.basename(trashed_file) + ".trashinfo") if root else None


def trash_original_path(trashed_file):
    """Where a trashed item was deleted from (or None). Per-drive trash stores paths relative to the drive."""
    info = trash_info_path(trashed_file)
    if not info:
        return None
    try:
        with open(info) as f:
            for line in f:
                if line.startswith("Path="):
                    orig = unquote(line[5:].strip())
                    if os.path.isabs(orig):
                        return orig
                    root = trash_root(trashed_file)
                    top = os.path.dirname(root) if os.path.basename(root).startswith(".Trash-") \
                        else os.path.dirname(os.path.dirname(root))
                    return os.path.join(top, orig)
    except OSError:
        pass
    return None


def trashed_items():
    """[(path inside a trash files/ folder, original path or None)] across every trash folder."""
    out = []
    for root in trash_dirs():
        try:
            entries = list(os.scandir(os.path.join(root, "files")))
        except OSError:
            continue
        out += [(e.path, trash_original_path(e.path)) for e in entries]
    return out


def trash_is_empty():
    for root in trash_dirs():
        try:
            if any(os.scandir(os.path.join(root, "files"))):
                return False
        except OSError:
            pass
    return True


def read_bookmarks():
    out = []
    try:
        for line in GTK_BOOKMARKS.read_text().splitlines():
            if not line.strip():
                continue
            uri, _, label = line.partition(" ")
            target = uri_to_path(uri) or uri  # keep network bookmarks (smb://, sftp://) as URIs
            name = os.path.basename(target.rstrip("/")) or target
            out.append((target, label.strip() or unquote(name)))
    except OSError:
        pass
    return out


def write_bookmarks(items):
    GTK_BOOKMARKS.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for target, label in items:
        default = unquote(os.path.basename(target.rstrip("/")) or target)
        uri = file_uri(target) if target.startswith("/") else target
        lines.append(uri + ("" if label == default else " " + label))
    GTK_BOOKMARKS.write_text("\n".join(lines) + "\n")
    from . import atc
    atc.announce("bookmarks")


def url_list(paths):
    return [QUrl.fromLocalFile(p) for p in paths]


DESKTOP_ENTRY = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "applications" / f"{APP_ID}.desktop"


def ensure_desktop_entry():
    """Register a .desktop file if none exists.

    On GNOME/Wayland the top bar and dock take the app's icon from the .desktop
    file whose name matches the app id; the window icon is ignored. install.sh
    writes a fuller entry; this only fills the gap when running from the repo
    or after a pip install.
    """
    if DESKTOP_ENTRY.exists():
        return
    # the .deb's entry in /usr/share/applications: a user one would hide it
    for d in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":"):
        if d and (Path(d) / "applications" / DESKTOP_ENTRY.name).exists():
            return
    launcher = Path(__file__).resolve().parent.parent / APP_COMMAND  # running from the repo
    if not launcher.exists():  # installed with pip: use the `kes` entry point, or the module as a last resort
        launcher = shutil.which(APP_COMMAND) or f"{sys.executable} -m kestrel"
    try:
        DESKTOP_ENTRY.parent.mkdir(parents=True, exist_ok=True)
        DESKTOP_ENTRY.write_text(
            f"[Desktop Entry]\nType=Application\nName={APP_NAME}\nGenericName=File Manager\n"
            "Comment=Manage files, with archive, admin, permission and metadata tools built in\n"
            f"Exec={launcher} %U\nIcon=folder\nTerminal=false\n"
            "Categories=System;FileTools;FileManager;Viewer;\n"
            f"MimeType=inode/directory;x-directory/normal;\nStartupWMClass={APP_ID}\n")
    except OSError:
        pass


def migrate_legacy():
    """One-time move of settings/caches/desktop entry from the old "folder-explorer" name."""
    config_root = CONFIG_DIR.parent
    old_cfg, new_cfg = config_root / LEGACY_ID, CONFIG_DIR
    try:
        if old_cfg.is_dir() and not new_cfg.exists():
            old_cfg.rename(new_cfg)
            old_conf = new_cfg / f"{LEGACY_ID}.conf"
            if old_conf.exists():
                old_conf.rename(new_cfg / f"{APP_ID}.conf")
        old_cache = CACHE_ROOT / LEGACY_ID
        if old_cache.is_dir() and not APP_CACHE.exists():
            old_cache.rename(APP_CACHE)
        old_desktop = DESKTOP_ENTRY.parent / f"{LEGACY_ID}.desktop"
        if old_desktop.exists() and any(n in old_desktop.read_text(errors="ignore") for n in LEGACY_NAMES):
            old_desktop.unlink()
            r = subprocess.run(["xdg-mime", "query", "default", "inode/directory"],
                               capture_output=True, text=True, timeout=5)
            if r.stdout.strip() == f"{LEGACY_ID}.desktop":
                ensure_desktop_entry()
                subprocess.run(["xdg-mime", "default", f"{APP_ID}.desktop", "inode/directory"], timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass
