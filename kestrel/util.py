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
ARCHIVE_EXTS = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz", ".7z", ".rar", ".tar.zst")

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


def is_archive(path):
    p = path.lower()
    return any(p.endswith(e) for e in ARCHIVE_EXTS)


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
            out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "icon-theme"],
                                 capture_output=True, text=True, timeout=2).stdout.strip().strip("'")
            if out:
                theme = out
        except Exception:
            pass
        QIcon.setThemeName(theme)
    QIcon.setFallbackThemeName("Adwaita")


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
    uri = file_uri(path)
    for key in ("picture-uri", "picture-uri-dark"):
        subprocess.run(["gsettings", "set", "org.gnome.desktop.background", key, uri], timeout=5)


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


def trash_original_path(trashed_file):
    """For a file in ~/.local/share/Trash/files return its original path (or None)."""
    name = os.path.basename(trashed_file)
    info = TRASH_DIR / "info" / (name + ".trashinfo")
    try:
        for line in info.read_text().splitlines():
            if line.startswith("Path="):
                return unquote(line[5:])
    except OSError:
        pass
    return None


def in_trash(path):
    return os.path.dirname(path) == str(TRASH_DIR / "files")


def empty_trash():
    for sub in ("files", "info", "expunged"):
        d = TRASH_DIR / sub
        if not d.is_dir():
            continue
        for entry in os.scandir(d):
            try:
                if entry.is_dir(follow_symlinks=False):
                    shutil.rmtree(entry.path)
                else:
                    os.unlink(entry.path)
            except OSError:
                pass


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
    launcher = Path(__file__).resolve().parent.parent / APP_COMMAND  # running from the repo
    if not launcher.exists():  # installed with pip: use the `kes` entry point, or the module as a last resort
        launcher = shutil.which(APP_COMMAND) or f"{sys.executable} -m kestrel"
    try:
        DESKTOP_ENTRY.parent.mkdir(parents=True, exist_ok=True)
        DESKTOP_ENTRY.write_text(
            f"[Desktop Entry]\nType=Application\nName={APP_NAME}\nGenericName=File Manager\n"
            "Comment=Browse files and image galleries with folder previews\n"
            f"Exec={launcher} %U\nIcon=folder\nTerminal=false\n"
            "Categories=System;FileTools;FileManager;Viewer;\n"
            f"MimeType=inode/directory;\nStartupWMClass={APP_ID}\n")
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
