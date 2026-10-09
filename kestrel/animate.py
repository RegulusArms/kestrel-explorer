"""Animated GIFs and WebM clips playing in the file view (Preferences → Play … in the file view).

GIFs play straight from the file. Qt can't play video without a multimedia library, so a WebM gets a small silent
looping preview made by ffmpeg (animated WebP, thumbnail-sized, at most the first 15 seconds), cached in
~/.cache/kestrel-explorer/animated, which then plays like a GIF. Only items on screen play: one that hasn't been
painted for a moment (scrolled away, another folder) is stopped and freed.
"""
import os
import shutil
import subprocess
import time

from PyQt6.QtCore import (QModelIndex, QObject, QPersistentModelIndex, QRunnable, QSize, Qt, QThreadPool, QTimer,
                          pyqtSignal)
from PyQt6.QtGui import QImageReader, QMovie

from . import thumbs, util

MAX_PLAYING = 40
PREVIEW_SECONDS = 15
PREVIEW_FPS = 12
ANIM_DIR = util.APP_CACHE / "animated"


def enabled(settings, path):
    ext = util.ext_of(path)
    if ext == ".gif":
        return settings.value("play_gifs", False, type=bool)
    if ext == ".webm":
        return settings.value("play_webm", False, type=bool) and shutil.which("ffmpeg") is not None
    return False


def preview_path(path, mtime, size):
    """Where the looping preview of a video is cached (named after its path, modification time and size)."""
    return str(ANIM_DIR / f"{util.md5(f'{path}|{int(mtime)}')}-{size}.webp")


def make_preview(path, out, size):
    """Encode a looping, silent, thumbnail-sized animated WebP of the start of a video. True on success."""
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        tmp = os.path.join(os.path.dirname(out), util.part_name() + ".webp")   # unique: two Kestrels may make it
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-t", str(PREVIEW_SECONDS), "-i", path, "-an",
                            "-vf", f"fps={PREVIEW_FPS},scale={size}:{size}:force_original_aspect_ratio=decrease",
                            "-c:v", "libwebp_anim", "-loop", "0", "-q:v", "70", "-compression_level", "3", tmp],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=120, start_new_session=True)
        if r.returncode == 0 and os.path.getsize(tmp) > 0:
            os.replace(tmp, out)
            return True
        os.unlink(tmp)
    except (OSError, subprocess.SubprocessError):
        pass
    return False


class _Signals(QObject):
    made = pyqtSignal(str, bool)   # video path, ok


class _Make(QRunnable):
    def __init__(self, path, out, size, signals):
        super().__init__()
        self.args, self.signals = (path, out, size), signals

    def run(self):
        ok = make_preview(*self.args)
        try:
            self.signals.made.emit(self.args[0], ok)
        except RuntimeError:   # shutting down
            pass


class Animator(QObject):
    """Plays the animated items of one view. The grid delegate asks frame() for each item it paints."""

    def __init__(self, view, settings, parent=None):
        super().__init__(parent)
        self.view, self.settings = view, settings
        self.movies = {}     # path -> [QMovie, QPersistentModelIndex, last painted, size]
        self.making = set()  # videos whose preview is being made
        self.failed = set()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.signals = _Signals()
        self.signals.made.connect(self._made)
        self.timer = QTimer(self, interval=1000, timeout=self._prune)
        self.timer.start()

    def frame(self, path, index, size):
        """The current frame (QPixmap, already scaled to fit size) if path is playing, else None (and it starts
        playing if it can)."""
        if not enabled(self.settings, path):
            return None
        entry = self.movies.get(path)
        if entry is not None and entry[3] != size:   # zoomed: start again at the new size
            self._stop(path)
            entry = None
        if entry is None:
            entry = self._start(path, index, size)
            if entry is None:
                return None
        entry[1] = QPersistentModelIndex(index)
        entry[2] = time.monotonic()
        pm = entry[0].currentPixmap()
        return None if pm.isNull() else pm

    def _start(self, path, index, size):
        if len(self.movies) >= MAX_PLAYING or path in self.failed:
            return None
        source = path
        if util.ext_of(path) == ".webm":
            bucket = thumbs.bucket_for(size)
            try:
                source = preview_path(path, os.stat(path).st_mtime, bucket)
            except OSError:
                return None
            if not os.path.exists(source):
                if path not in self.making:
                    self.making.add(path)
                    self.pool.start(_Make(path, source, bucket, self.signals))
                return None
        reader = QImageReader(source)
        src = reader.size()
        movie = QMovie(source)
        if not movie.isValid():
            self.failed.add(path)
            return None
        if src.isValid():
            movie.setScaledSize(src.scaled(QSize(size, size), Qt.AspectRatioMode.KeepAspectRatio)
                                if src.width() > size or src.height() > size else src)
        movie.setCacheMode(QMovie.CacheMode.CacheAll if util.ext_of(path) == ".webm" else QMovie.CacheMode.CacheNone)
        movie.frameChanged.connect(lambda _n, p=path: self._frame_changed(p))
        entry = [movie, QPersistentModelIndex(index), time.monotonic(), size]
        self.movies[path] = entry
        movie.start()
        return entry

    def _frame_changed(self, path):
        entry = self.movies.get(path)
        if entry is not None and entry[1].isValid():
            self.view.update(QModelIndex(entry[1]))

    def _made(self, path, ok):
        self.making.discard(path)
        if not ok:
            self.failed.add(path)
        self.view.viewport().update()

    def _stop(self, path):
        entry = self.movies.pop(path, None)
        if entry is not None:
            entry[0].stop()
            entry[0].deleteLater()

    def _prune(self):
        now = time.monotonic()
        for path in [p for p, e in self.movies.items() if now - e[2] > 1.5]:
            self._stop(path)

    def clear(self):
        """Stop everything (another folder, changed settings)."""
        for path in list(self.movies):
            self._stop(path)
        self.failed.clear()
