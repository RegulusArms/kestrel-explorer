"""Asynchronous thumbnail generation.

File thumbnails are read from / written to the shared freedesktop cache
(~/.cache/thumbnails), so thumbnails made by GNOME Files are reused and vice versa.
Folder previews are a mosaic of the first images in the folder, cached in
~/.cache/kestrel-explorer/folders.
"""
import io
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from collections import OrderedDict

from PyQt6.QtCore import QObject, QRectF, QRunnable, QSize, Qt, QThread, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPixmap

from . import atc, util

FLAVORS = {128: "normal", 256: "large", 512: "x-large"}
COVER_NAMES = ("cover", "folder", ".cover", ".folder", "front", "poster")
COVERS_FILE = util.CONFIG_DIR / "covers.json"
STYLES_FILE = util.CONFIG_DIR / "folder_styles.json"   # per-folder {"color": "#rrggbb", "previews": false}

# colours offered for folder icons (right-click a folder → Folder Colour, or its Properties)
FOLDER_COLORS = [("Red", "#e01b24"), ("Orange", "#ff7800"), ("Yellow", "#f6d32d"), ("Green", "#33d17a"),
                 ("Teal", "#2aa198"), ("Blue", "#3584e4"), ("Purple", "#9141ac"), ("Pink", "#e66ba5"),
                 ("Brown", "#986a44"), ("Grey", "#77767b")]


def bucket_for(size):
    for b in (128, 256, 512):
        if size <= b:
            return b
    return 512


# ---------------------------------------------------------------- workers (run in thread pool)

def _header_size(path):
    """Image dimensions from the file header via Pillow (its file I/O releases the GIL).

    Not QImageReader.size(): PyQt holds the GIL for that call, and for HEIC/RAW it
    can take hundreds of ms, freezing the UI thread.
    """
    try:
        from PIL import Image
        with Image.open(path) as im:
            return QSize(*im.size)
    except Exception:
        return QSize()


def load_scaled(path, size):
    # Only threads blocked in GIL-releasing calls (reader.read(), file I/O) are
    # harmless to the UI; keep everything slow inside those.
    reader = util.image_reader(path)
    if util.ext_of(path) in (".jpg", ".jpeg", ".jfif"):
        # JPEG can decode straight to a smaller size (much faster), but that needs
        # the dimensions up front to keep the aspect ratio.
        src = _header_size(path)
        if src.isValid() and (src.width() > size or src.height() > size):
            reader.setScaledSize(src.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio))
    img = reader.read()
    if img.isNull():
        img = _pil_load(path, size)
    elif img.width() > size or img.height() > size:
        img = img.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return img if img is not None and not img.isNull() else None


def _pil_load(path, size):
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((size, size))
            buf = io.BytesIO()
            im.convert("RGBA").save(buf, "PNG")
        return QImage.fromData(buf.getvalue())
    except Exception:
        return None


def video_duration(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=noprint_wrappers=1:nokey=1", path],
                             capture_output=True, text=True, timeout=15).stdout.strip()
        return float(out)
    except Exception:
        return None


def video_frame(path, size):
    """A frame from the middle of the video (falls back to near the start)."""
    dur = video_duration(path)
    seeks = [f"{dur / 2:.2f}"] if dur and dur > 0.5 else []
    for seek in seeks + ["3", "0"]:
        try:
            out = subprocess.run(
                ["ffmpeg", "-v", "quiet", "-ss", seek, "-i", path, "-frames:v", "1",
                 "-vf", f"scale={size}:{size}:force_original_aspect_ratio=decrease",
                 "-f", "image2pipe", "-vcodec", "png", "-"],
                capture_output=True, timeout=30).stdout
        except Exception:
            return None
        if out:
            img = QImage.fromData(out)
            if not img.isNull():
                return img
    return None


# ---------------------------------------------------------------- system thumbnailers (PDF, fonts, audio, …)

_thumbnailers = None


def thumbnailers():
    """{mime type: [Exec line, …]} from the freedesktop .thumbnailer files (the same ones GNOME Files uses), in
    order of preference: a user's own ~/.local/share/thumbnailers comes first."""
    global _thumbnailers
    if _thumbnailers is None:
        table = {}
        data_dirs = [os.environ.get("XDG_DATA_HOME", os.path.join(util.HOME, ".local/share"))]
        data_dirs += os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
        for d in data_dirs:
            folder = os.path.join(d, "thumbnailers")
            try:
                names = sorted(os.listdir(folder))
            except OSError:
                continue
            for name in names:
                if not name.endswith(".thumbnailer"):
                    continue
                entry = {}
                try:
                    with open(os.path.join(folder, name), errors="replace") as f:
                        for line in f:
                            key, eq, value = line.strip().partition("=")
                            if eq:
                                entry[key] = value
                except OSError:
                    continue
                exe, try_exec = entry.get("Exec", ""), entry.get("TryExec", "")
                if not exe or (try_exec and not shutil.which(try_exec)):
                    continue
                for mt in entry.get("MimeType", "").split(";"):
                    if mt and exe not in table.setdefault(mt, []):
                        table[mt].append(exe)
        _thumbnailers = table
    return _thumbnailers


def thumbnailers_for(path):
    """The system thumbnailers' Exec lines for this file's type, best first (empty if there are none)."""
    table = thumbnailers()
    if not table:
        return []
    m = util.mime_for(path, False)
    for name in [m.name(), *m.aliases(), *m.allAncestors()]:
        if name in table:
            return table[name]
    return []


def can_thumbnail(path):
    """Whether a file can get a thumbnail: images and videos (Kestrel's own), anything else a system
    thumbnailer handles."""
    return util.is_image(path) or util.is_video(path) or bool(thumbnailers_for(path))


_tries = {}   # Exec line -> [successes, failures]: one that only ever fails is skipped after a few tries


def _run_thumbnailer(exe, path, size):
    # The output goes to /tmp/gnome-desktop-thumbnailer-*.png like GNOME's own: Ubuntu's AppArmor profiles only let
    # thumbnailers such as evince/papers write there.
    fd, out = tempfile.mkstemp(prefix="gnome-desktop-thumbnailer-", suffix=".png", dir="/tmp")
    os.close(fd)
    try:
        subst = {"%i": os.path.abspath(path), "%u": util.file_uri(path), "%o": out, "%s": str(size), "%%": "%"}
        argv = []
        for arg in shlex.split(exe):
            for k, v in subst.items():
                arg = arg.replace(k, v)
            argv.append(arg)
        subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=30, start_new_session=True)
        img = QImage(out)
        return None if img.isNull() else img
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass


def system_thumb(path, size):
    """A thumbnail from the system thumbnailers for this file's type (QImage or None); if one fails, the next one
    registered for the type is tried."""
    for exe in thumbnailers_for(path):
        ok, bad = _tries.setdefault(exe, [0, 0])
        if not ok and bad >= 3:
            continue
        img = _run_thumbnailer(exe, path, size)
        _tries[exe][0 if img is not None else 1] += 1
        if img is not None:
            if img.width() > size or img.height() > size:
                img = img.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
            return img
    return None


def file_thumb(path, mtime, size):
    """Return a QImage thumbnail (max size x size) using the freedesktop cache."""
    flavor = FLAVORS[bucket_for(size)]
    uri = util.file_uri(path)
    cache_path = util.THUMB_DIR / flavor / (util.md5(uri) + ".png")
    in_cache_dir = path.startswith(str(util.THUMB_DIR))
    if not in_cache_dir and cache_path.exists():
        img = QImage(str(cache_path))
        if not img.isNull() and img.text("Thumb::MTime") == str(int(mtime)):
            return img
    if util.is_image(path):
        img = load_scaled(path, size)
    elif util.is_video(path):
        img = video_frame(path, size)
    else:
        img = None
    own = img is not None
    if img is None:   # PDFs, fonts, … (and images Qt can't decode): the system thumbnailers
        img = system_thumb(path, size)
    if img is None:
        return None
    # don't bother caching images that are already thumbnail sized
    if not in_cache_dir and (img.width() >= size or img.height() >= size or util.is_video(path) or not own):
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            img.setText("Thumb::URI", uri)
            img.setText("Thumb::MTime", str(int(mtime)))
            img.setText("Software", util.APP_NAME)
            fd, tmp = tempfile.mkstemp(dir=cache_path.parent, suffix=".png")
            os.close(fd)
            if img.save(tmp, "PNG"):
                os.chmod(tmp, 0o600)
                os.replace(tmp, cache_path)
            else:
                os.unlink(tmp)
        except OSError:
            pass
    return img


def pick_folder_images(folder, count, order, cover=None):
    """Choose up to `count` image paths to represent `folder`.

    Uses os.listdir rather than QDir.entryList: PyQt holds the GIL for the whole
    entryList() call (seconds on a cold disk with thousands of files), which
    freezes the UI thread; os.listdir releases it while reading.
    """
    picks = []
    if cover and os.path.isfile(cover):
        picks.append(cover)
    try:
        names = os.listdir(folder)
    except OSError:
        return picks
    exts = util.image_exts()
    images = [n for n in names if not n.startswith(".") and os.path.splitext(n)[1].lower() in exts]
    if not images:
        # no pictures: use videos (a still from the middle of each)
        images = [n for n in names if not n.startswith(".") and os.path.splitext(n)[1].lower() in util.VIDEO_EXTS]
    newest = order == "newest"

    def mtime(n):
        try:
            return os.stat(os.path.join(folder, n)).st_mtime
        except OSError:
            return 0

    if newest:
        images.sort(key=mtime, reverse=True)
    else:
        images.sort(key=util.natural_key)
    if not picks:
        for name in images:
            if os.path.splitext(name)[0].lower() in COVER_NAMES:
                picks.append(os.path.join(folder, name))
                break
    for name in images:
        if len(picks) >= count:
            break
        p = os.path.join(folder, name)
        if p not in picks and os.path.isfile(p):
            picks.append(p)
    if not picks:  # look one level down for nested galleries
        dirs = [n for n in names if not n.startswith(".") and os.path.isdir(os.path.join(folder, n))]
        dirs.sort(key=mtime if newest else util.natural_key, reverse=newest)
        for d in dirs[:12]:
            if len(picks) >= count:
                break
            picks.extend(pick_folder_images(os.path.join(folder, d), 1, order)[:1])
    return picks[:count]


def _draw_cover(p, img, rect):
    """Draw img into rect, center-cropped to fill."""
    iw, ih = img.width(), img.height()
    if iw == 0 or ih == 0:
        return
    scale = max(rect.width() / iw, rect.height() / ih)
    sw, sh = rect.width() / scale, rect.height() / scale
    src = QRectF((iw - sw) / 2, (ih - sh) / 2, sw, sh)
    p.drawImage(rect, img, src)


def _play_badge(p, cell):
    d = max(10.0, min(cell.width(), cell.height()) * 0.32)
    c = cell.center()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 150))
    p.drawEllipse(QRectF(c.x() - d / 2, c.y() - d / 2, d, d))
    tri = QPainterPath()
    tri.moveTo(c.x() - d * 0.15, c.y() - d * 0.22)
    tri.lineTo(c.x() + d * 0.25, c.y())
    tri.lineTo(c.x() - d * 0.15, c.y() + d * 0.22)
    tri.closeSubpath()
    p.setBrush(QColor(255, 255, 255, 235))
    p.drawPath(tri)


def compose_folder(images, size, color, videos=()):
    canvas = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    p = QPainter(canvas)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    s = float(size)
    m = s * 0.03
    back = QColor(color)
    front = back.lighter(118)
    # folder tab + back panel
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(back.darker(115))
    p.drawRoundedRect(QRectF(m, m + s * 0.06, s * 0.42, s * 0.16), s * 0.04, s * 0.04)
    p.setBrush(back)
    p.drawRoundedRect(QRectF(m, m + s * 0.12, s - 2 * m, s * 0.82), s * 0.06, s * 0.06)
    # front panel
    body = QRectF(m, m + s * 0.19, s - 2 * m, s * 0.75)
    p.setBrush(front)
    p.drawRoundedRect(body, s * 0.06, s * 0.06)
    if not images:  # a plain folder in this colour (no previews)
        p.end()
        return canvas
    # mosaic
    inner = body.adjusted(s * 0.035, s * 0.035, -s * 0.035, -s * 0.035)
    clip = QPainterPath()
    clip.addRoundedRect(inner, s * 0.035, s * 0.035)
    p.setClipPath(clip)
    p.fillRect(inner, QColor(0, 0, 0, 60))
    g = max(1.0, s * 0.012)
    x, y, w, h = inner.x(), inner.y(), inner.width(), inner.height()
    n = len(images)
    if n == 1:
        cells = [QRectF(x, y, w, h)]
    elif n == 2:
        cells = [QRectF(x, y, w / 2 - g / 2, h), QRectF(x + w / 2 + g / 2, y, w / 2 - g / 2, h)]
    elif n == 3:
        cells = [QRectF(x, y, w * 0.6 - g / 2, h),
                 QRectF(x + w * 0.6 + g / 2, y, w * 0.4 - g / 2, h / 2 - g / 2),
                 QRectF(x + w * 0.6 + g / 2, y + h / 2 + g / 2, w * 0.4 - g / 2, h / 2 - g / 2)]
    else:
        cw, ch = w / 2 - g / 2, h / 2 - g / 2
        cells = [QRectF(x, y, cw, ch), QRectF(x + cw + g, y, cw, ch),
                 QRectF(x, y + ch + g, cw, ch), QRectF(x + cw + g, y + ch + g, cw, ch)]
    for i, (img, cell) in enumerate(zip(images, cells)):
        _draw_cover(p, img, cell)
        if i < len(videos) and videos[i]:
            _play_badge(p, cell)
    p.end()
    return canvas


def folder_thumb(path, mtime, size, opts):
    count, order, color, cover = opts["count"], opts["order"], opts["color"], opts.get("cover")
    tag = f"v2|{int(mtime)}|{count}|{order}|{color}|{cover or ''}"
    cache = util.APP_CACHE / "folders" / f"{util.md5(path)}-{size}.png"
    if cache.exists():
        img = QImage(str(cache))
        if not img.isNull() and img.text("FE::Tag") == tag:
            return img
        if not img.isNull() and img.text("FE::Tag") == "empty|" + tag:
            return None
    picks = pick_folder_images(path, count, order, cover)
    images, videos = [], []
    tile = 128 if (len(picks) > 1 or size <= 128) else 256
    for pth in picks:
        try:
            st = os.stat(pth)
        except OSError:
            continue
        im = file_thumb(pth, st.st_mtime, tile)
        if im is not None:
            images.append(im)
            videos.append(util.is_video(pth))
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    if not images:
        marker = QImage(1, 1, QImage.Format.Format_ARGB32)
        marker.setText("FE::Tag", "empty|" + tag)
        marker.save(str(cache), "PNG")
        return None
    img = compose_folder(images, size, color, videos)
    img.setText("FE::Tag", tag)
    img.save(str(cache), "PNG")
    return img


def _made_by_us(png_path):
    """True if a cached thumbnail PNG carries our Software tag (reads chunk headers only)."""
    try:
        with open(png_path, "rb") as f:
            if f.read(8) != b"\x89PNG\r\n\x1a\n":
                return False
            while True:
                head = f.read(8)
                if len(head) < 8:
                    return False
                length, ctype = int.from_bytes(head[:4], "big"), head[4:]
                if ctype == b"IDAT" or length > 1 << 20:
                    return False
                data = f.read(length)
                f.seek(4, 1)  # crc
                if ctype in (b"tEXt", b"iTXt") and data.startswith(b"Software\0"):
                    return any(n.encode() in data for n in (util.APP_NAME, *util.LEGACY_NAMES))
    except OSError:
        return False


def purge_thumbnails(include_shared=False):
    """Delete every thumbnail this app may have written. Returns (files, bytes).

    Always removes the folder-mosaic cache and the PNGs we tagged in the shared
    freedesktop cache. With include_shared, empties ~/.cache/thumbnails entirely
    (that also removes thumbnails made by GNOME Files and other apps).
    """
    files = size = 0
    targets = []
    for sub in ("folders", "animated"):   # folder mosaics, looping video previews
        for root, _, names in os.walk(util.APP_CACHE / sub):
            targets += [os.path.join(root, n) for n in names]
    for flavor in FLAVORS.values():
        d = util.THUMB_DIR / flavor
        if not d.is_dir():
            continue
        for e in os.scandir(d):
            if e.is_file() and e.name.endswith(".png") and (include_shared or _made_by_us(e.path)):
                targets.append(e.path)
    if include_shared:
        for sub in ("fail",):
            for root, _, names in os.walk(util.THUMB_DIR / sub):
                targets += [os.path.join(root, n) for n in names]
    for t in targets:
        try:
            size += os.path.getsize(t)
            os.unlink(t)
            files += 1
        except OSError:
            pass
    return files, size


_manager = None


def manager():
    """The app's ThumbnailManager (the first one created)."""
    return _manager


def color_swatch(color, size=16):
    """A small rounded square of `color` for menus and lists."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QColor(0, 0, 0, 80))
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), 3, 3)
    p.end()
    return QIcon(pm)


class _Signals(QObject):
    done = pyqtSignal(object, str, object)  # key, path, QImage|None


class _Job(QRunnable):
    def __init__(self, key, path, mtime, is_dir, size, opts, signals):
        super().__init__()
        self.args = (key, path, mtime, is_dir, size, opts)
        self.signals = signals

    def run(self):
        key, path, mtime, is_dir, size, opts = self.args
        try:
            img = folder_thumb(path, mtime, size, opts) if is_dir else file_thumb(path, mtime, size)
        except Exception:
            img = None
        try:
            self.signals.done.emit(key, path, img)
        except RuntimeError:  # app is shutting down
            pass


# ---------------------------------------------------------------- manager (main thread)

class ThumbnailManager(QObject):
    updated = pyqtSignal(str)  # path
    progress = pyqtSignal(int, int)  # done, total for the current batch (0, 0 = idle)

    def __init__(self, parent=None):
        super().__init__(parent)
        global _manager
        _manager = _manager or self
        # Keep the pools small: decoding releases the GIL, but every thread still
        # competes with the UI thread for it between C++ calls.
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max(2, min(6, (os.cpu_count() or 4) // 2)))
        self.dir_pool = QThreadPool(self)  # folder mosaics (directory scans)
        self.dir_pool.setMaxThreadCount(2)
        util.image_exts()  # initialise on the main thread
        thumbnailers()
        self.batch_total = 0
        self.batch_done = 0
        self._progress_timer = QTimer(self, singleShot=True, interval=100, timeout=self._emit_progress)
        self.signals = _Signals()
        self.signals.done.connect(self._done)
        self.cache = OrderedDict()   # key -> QPixmap
        self.icons = {}               # key -> QIcon
        self.failed = set()
        self.pending = set()
        self.max_items = 1500
        self.max_file_mb = 200
        self.folder_count = 4
        self.folder_order = "name"
        self.folder_color = "#d9652f"
        self.covers = self._load_covers()
        self.styles = self._load_json(STYLES_FILE)
        self._plain = {}   # (color, size) -> QPixmap of a plain folder
        self._prio = 0
        self._scaled = OrderedDict()

    # -- covers
    def _load_covers(self):
        try:
            return json.loads(COVERS_FILE.read_text())
        except Exception:
            return {}

    @staticmethod
    def _load_json(path):
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}

    # -- per-folder style: colour and previews on/off
    def custom_color(self, folder):
        """The colour chosen for this folder, or None for the default."""
        return self.styles.get(folder, {}).get("color")

    def color_for(self, folder):
        return self.custom_color(folder) or self.folder_color

    def previews_for(self, folder):
        """False if image previews are turned off for this folder."""
        return self.styles.get(folder, {}).get("previews", True)

    def reload_styles(self):
        """Re-read covers and folder styles (another Kestrel changed them)."""
        self.covers = self._load_covers()
        self.styles = self._load_json(STYLES_FILE)

    def _set_style(self, folders, key, value):
        self.reload_styles()   # another Kestrel may have changed them since
        for f in folders:
            st = dict(self.styles.get(f, {}))
            if value is None:
                st.pop(key, None)
            else:
                st[key] = value
            if st:
                self.styles[f] = st
            else:
                self.styles.pop(f, None)
        try:
            STYLES_FILE.parent.mkdir(parents=True, exist_ok=True)
            STYLES_FILE.write_text(json.dumps(self.styles, indent=1))
        except OSError:
            pass
        for f in folders:
            self.invalidate(f)
        atc.announce("folders", paths=list(folders))

    def set_folder_color(self, folders, color):
        """color "#rrggbb", or None for the default."""
        self._set_style(folders, "color", color)

    def set_folder_previews(self, folders, on):
        self._set_style(folders, "previews", None if on else False)

    def plain_folder(self, color, size):
        """A plain folder icon in `color` (cached; cheap enough to draw on the UI thread)."""
        k = (color, bucket_for(size))
        pm = self._plain.get(k)
        if pm is None:
            pm = self._plain[k] = QPixmap.fromImage(compose_folder([], k[1], color))
        return pm

    def folder_pixmap(self, path, mtime, size, previews=True):
        """What to draw for a folder: its preview mosaic (in its colour), a plain folder in its custom colour while
        there is no mosaic, or None for the theme's folder icon. previews=False skips the mosaic (e.g. when folder
        previews are switched off globally)."""
        if previews and self.previews_for(path):
            pm = self.get(path, mtime, True, size)
            if pm is not None:
                return pm
        color = self.custom_color(path)
        return self.plain_folder(color, size) if color else None

    def set_cover(self, folder, image):
        self.reload_styles()
        if image:
            self.covers[folder] = image
        else:
            self.covers.pop(folder, None)
        COVERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        COVERS_FILE.write_text(json.dumps(self.covers, indent=1))
        self.invalidate(folder)
        atc.announce("folders", paths=[folder])

    # -- api
    def key(self, path, mtime, is_dir, size):
        b = bucket_for(size)
        if is_dir:
            return ("d", b, path, int(mtime), self.folder_count, self.folder_order, self.color_for(path),
                    self.covers.get(path, ""))
        return ("f", b, path, int(mtime))

    def get(self, path, mtime, is_dir, size, fsize=0):
        """Return a QPixmap if ready; otherwise schedule generation and return None."""
        k = self.key(path, mtime, is_dir, size)
        pm = self.cache.get(k)
        if pm is not None:
            self.cache.move_to_end(k)
            return pm
        if k in self.failed:
            return None
        if k not in self.pending:
            if not is_dir and fsize > self.max_file_mb * 1_000_000 and not util.is_video(path):
                self.failed.add(k)
                return None
            self.pending.add(k)
            self._prio += 1
            opts = {"count": self.folder_count, "order": self.folder_order,
                    "color": self.color_for(path), "cover": self.covers.get(path)}
            job = _Job(k, path, mtime, is_dir, bucket_for(size), opts, self.signals)
            (self.dir_pool if is_dir else self.pool).start(job, self._prio)
            self.batch_total += 1
            self._schedule_progress()
        return None

    def get_icon(self, path, mtime, is_dir, size, fsize=0):
        pm = self.get(path, mtime, is_dir, size, fsize)
        if pm is None:
            return None
        k = self.key(path, mtime, is_dir, size)
        icon = self.icons.get(k)
        if icon is None:
            icon = self.icons[k] = QIcon(pm)
        return icon

    def scaled(self, pm, size):
        """Cached smooth downscale of pm to fit size x size."""
        k = (pm.cacheKey(), size)
        out = self._scaled.get(k)
        if out is None:
            if pm.width() <= size and pm.height() <= size:
                out = pm
            else:
                out = pm.scaled(QSize(size, size), Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation)
            self._scaled[k] = out
            if len(self._scaled) > 800:
                self._scaled.popitem(last=False)
        return out

    def cancel_pending(self):
        self.pool.clear()
        self.dir_pool.clear()
        self.pending.clear()
        self.batch_total = self.batch_done = 0
        self._schedule_progress()

    def _schedule_progress(self):
        if not self._progress_timer.isActive():
            self._progress_timer.start()

    def _emit_progress(self):
        if not self.pending:
            self.batch_total = self.batch_done = 0
        self.progress.emit(self.batch_done, self.batch_total)

    def invalidate(self, path, disk=True):
        """disk=False keeps the cached mosaic on disk."""
        for d in (self.cache, self.icons):
            for k in [k for k in d if k[2] == path]:
                d.pop(k, None)
        self.failed = {k for k in self.failed if k[2] != path}
        for size in (128, 256, 512) if disk else ():
            try:
                (util.APP_CACHE / "folders" / f"{util.md5(path)}-{size}.png").unlink()
            except OSError:
                pass
        self.updated.emit(path)

    def clear_memory(self):
        self.cache.clear()
        self.icons.clear()
        self.failed.clear()
        self._scaled.clear()

    def _done(self, key, path, img):
        if key in self.pending:
            self.pending.discard(key)
            self.batch_done += 1
            self._schedule_progress()
        if img is None or img.isNull():
            if key[0] == "f" and time.time() - key[3] < 30:
                # file probably still being written; try again shortly
                QTimer.singleShot(3000, lambda: self.updated.emit(path))
                return
            self.failed.add(key)
        else:
            self.cache[key] = QPixmap.fromImage(img)
            while len(self.cache) > self.max_items:
                old, _ = self.cache.popitem(last=False)
                self.icons.pop(old, None)
        self.updated.emit(path)


# ---------------------------------------------------------------- recursive pre-generation

class RecursiveBuilder(QThread):
    """Pre-generate file thumbnails and folder previews for a whole tree.

    Scanning and generating overlap: items are queued to a small worker pool as
    the walk finds them, so work starts immediately and the total grows until the
    walk completes. Everything goes to the on-disk caches.
    """
    progress = pyqtSignal(int, int, bool, str)   # done, total so far, still scanning, current path
    finished_build = pyqtSignal(int, int, bool)  # done, total, cancelled

    def __init__(self, root, size, manager, parent=None, workers=3):
        super().__init__(parent)
        self.root = root
        self.size = bucket_for(size)
        self.stop = False
        self.workers = workers
        self.max_bytes = manager.max_file_mb * 1_000_000
        self.opts = {"count": manager.folder_count, "order": manager.folder_order,
                     "color": manager.folder_color}
        self.covers = dict(manager.covers)
        self.colors = {f: st["color"] for f, st in manager.styles.items() if st.get("color")}
        self.no_previews = {f for f, st in manager.styles.items() if st.get("previews") is False}
        self.done = self.total = 0
        self.scanning = True
        self.current = root
        self._lock = threading.Lock()
        self._last_emit = 0.0

    def cancel(self):
        self.stop = True

    def _emit(self, force=False):
        now = time.monotonic()
        with self._lock:
            if not force and now - self._last_emit < 0.1:
                return
            self._last_emit = now
            args = (self.done, self.total, self.scanning, self.current)
        self.progress.emit(*args)

    def _one(self, item):
        kind, path = item
        try:
            if self.stop:
                return
            st = os.stat(path)
            if kind == "d":
                folder_thumb(path, st.st_mtime, self.size, dict(self.opts, cover=self.covers.get(path),
                                                                color=self.colors.get(path, self.opts["color"])))
            elif st.st_size <= self.max_bytes or util.is_video(path):
                file_thumb(path, st.st_mtime, self.size)
                if self.size != 128:  # mosaic tiles use the small size
                    file_thumb(path, st.st_mtime, 128)
        except Exception:
            pass
        finally:
            with self._lock:
                self.done += 1
                self.current = path
            self._slots.release()
            self._emit()

    def run(self):
        from concurrent.futures import ThreadPoolExecutor
        exts = util.image_exts() | util.VIDEO_EXTS
        self._slots = threading.BoundedSemaphore(self.workers * 16)  # cap queued work / memory
        with ThreadPoolExecutor(self.workers) as ex:
            def submit(item):
                while not self._slots.acquire(timeout=0.2):
                    if self.stop:
                        return False
                with self._lock:
                    self.total += 1
                ex.submit(self._one, item)
                return True

            for root, dirs, files in os.walk(self.root):
                if self.stop:
                    break
                dirs[:] = sorted((d for d in dirs if not d.startswith(".")), key=util.natural_key)
                for f in sorted(files, key=util.natural_key):
                    if not f.startswith(".") and os.path.splitext(f)[1].lower() in exts:
                        if not submit(("f", os.path.join(root, f))):
                            break
                if self.stop or (root not in self.no_previews and not submit(("d", root))):
                    break
                self._emit()
            with self._lock:
                self.scanning = False
            self._emit(force=True)
            ex.shutdown(wait=True, cancel_futures=self.stop)
        self._emit(force=True)
        self.finished_build.emit(self.done, self.total, self.stop)
