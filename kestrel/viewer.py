"""Built-in image viewer with prefetching, zoom/pan, slideshow and animation support."""
import os
from collections import OrderedDict

from PyQt6.QtCore import QObject, QPointF, QRectF, QRunnable, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import (QAction, QColor, QFont, QGuiApplication, QImageReader, QKeySequence, QMovie, QPainter,
                         QPixmap, QTransform)
from PyQt6.QtWidgets import QMenu, QWidget

from . import util


ANIMATABLE = {".gif", ".webp", ".avif", ".jxl", ".mng", ".apng"}


class _Sig(QObject):
    loaded = pyqtSignal(str, object)


class _Load(QRunnable):
    def __init__(self, path, sig):
        super().__init__()
        self.path, self.sig = path, sig

    def run(self):
        img = util.image_reader(self.path).read()
        if img.isNull():
            from .thumbs import _pil_load
            img = _pil_load(self.path, 16384)
        self.sig.loaded.emit(self.path, img)


class ImageViewer(QWidget):
    def __init__(self, paths, index, settings, on_delete=None, on_properties=None, on_close=None, on_open_with=None):
        super().__init__(None, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.paths = list(paths)
        self.index = max(0, min(index, len(self.paths) - 1))
        self.settings = settings
        self.on_delete, self.on_properties, self.on_close, self.on_open_with = on_delete, on_properties, on_close, on_open_with
        self.cache = OrderedDict()
        self.pending = set()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.sig = _Sig()
        self.sig.loaded.connect(self._loaded)
        self.pix = None
        self.movie = None
        self.zoom = None          # None = fit to window
        self.offset = QPointF(0, 0)
        self.rotation = 0
        self.flip_h = False
        self.show_info = True
        self.upscale = False
        self._drag = None
        self.error = ""
        self.slideshow = QTimer(self)
        self.slideshow.timeout.connect(lambda: self.navigate(1, wrap=True))
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(320, 240)
        self.resize(1200, 800)
        self.setStyleSheet("background:#111")
        self._hide_cursor = QTimer(self, singleShot=True, interval=2000,
                                   timeout=lambda: self.isFullScreen() and self.setCursor(Qt.CursorShape.BlankCursor))
        self._add_shortcuts()
        self.show_current()

    # ------------------------------------------------------------ loading
    def _request(self, path):
        if path in self.cache or path in self.pending:
            return
        self.pending.add(path)
        self.pool.start(_Load(path, self.sig))

    def _loaded(self, path, img):
        self.pending.discard(path)
        self.cache[path] = None if (img is None or img.isNull()) else QPixmap.fromImage(img)
        while len(self.cache) > 7:
            self.cache.popitem(last=False)
        if self.paths and path == self.paths[self.index]:
            self._display(path)

    def current(self):
        return self.paths[self.index] if self.paths else None

    def show_current(self):
        if not self.paths:
            self.close()
            return
        path = self.current()
        self.zoom, self.offset, self.rotation, self.flip_h = None, QPointF(0, 0), 0, False
        self._stop_movie()
        self.setWindowTitle(f"{os.path.basename(path)} — {self.index + 1}/{len(self.paths)}")
        reader = QImageReader(path) if util.ext_of(path) in ANIMATABLE else None
        if reader and reader.supportsAnimation() and reader.imageCount() != 1:
            self.movie = QMovie(path)
            self.movie.frameChanged.connect(self._movie_frame)
            self.movie.start()
            self.error = ""
        elif path in self.cache:
            self._display(path)
        else:
            self.pix = None
            self.error = ""
            self._request(path)
        for d in (1, -1, 2):
            j = self.index + d
            if 0 <= j < len(self.paths):
                self._request(self.paths[j])
        self.update()

    def _display(self, path):
        self.pix = self.cache.get(path)
        self.error = "" if self.pix else "Cannot display this image"
        self.update()

    def _movie_frame(self):
        if self.movie:
            self.pix = self.movie.currentPixmap()
            self.update()

    def _stop_movie(self):
        if self.movie:
            self.movie.stop()
            self.movie.deleteLater()
            self.movie = None

    # ------------------------------------------------------------ navigation
    def navigate(self, step, wrap=False):
        if not self.paths:
            return
        n = self.index + step
        if wrap:
            n %= len(self.paths)
        n = max(0, min(n, len(self.paths) - 1))
        if n != self.index:
            self.index = n
            self.show_current()

    def goto(self, n):
        self.index = max(0, min(n, len(self.paths) - 1))
        self.show_current()

    def delete_current(self):
        path = self.current()
        if path and self.on_delete and self.on_delete(path):
            del self.paths[self.index]
            self.cache.pop(path, None)
            if self.index >= len(self.paths):
                self.index = len(self.paths) - 1
            self.show_current()

    # ------------------------------------------------------------ geometry
    def _img_size(self):
        if not self.pix:
            return 1, 1
        w, h = self.pix.width() / self.pix.devicePixelRatio(), self.pix.height() / self.pix.devicePixelRatio()
        return (h, w) if self.rotation % 180 else (w, h)

    def fit_scale(self):
        w, h = self._img_size()
        s = min(self.width() / w, self.height() / h)
        return s if (self.upscale or s < 1) else 1.0

    def scale(self):
        return self.zoom if self.zoom is not None else self.fit_scale()

    def set_zoom(self, z, anchor=None):
        old = self.scale()
        z = max(0.02, min(z, 40.0))
        if anchor is None:
            anchor = QPointF(self.width() / 2, self.height() / 2)
        center = QPointF(self.width() / 2, self.height() / 2) + self.offset
        img_pt = (anchor - center) / old
        self.zoom = z
        self.offset = anchor - QPointF(self.width() / 2, self.height() / 2) - img_pt * z
        self._clamp()
        self.update()

    def _clamp(self):
        w, h = self._img_size()
        s = self.scale()
        mx = max(0.0, (w * s - self.width()) / 2)
        my = max(0.0, (h * s - self.height()) / 2)
        self.offset = QPointF(max(-mx, min(mx, self.offset.x())), max(-my, min(my, self.offset.y())))

    # ------------------------------------------------------------ painting
    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#111"))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.scale() < 2.5)
        if self.pix:
            s = self.scale()
            pw, ph = self.pix.width() / self.pix.devicePixelRatio(), self.pix.height() / self.pix.devicePixelRatio()
            c = QPointF(self.width() / 2, self.height() / 2) + self.offset
            t = QTransform()
            t.translate(c.x(), c.y())
            t.rotate(self.rotation)
            t.scale(-s if self.flip_h else s, s)
            p.setTransform(t)
            p.drawPixmap(QRectF(-pw / 2, -ph / 2, pw, ph), self.pix, QRectF(self.pix.rect()))
            p.resetTransform()
        elif self.paths:
            p.setPen(QColor("#aaa"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.error or "Loading…")
        if self.show_info and self.paths:
            path = self.current()
            dims = f"{self.pix.width()} × {self.pix.height()}" if self.pix else ""
            try:
                size = util.human_size(os.path.getsize(path))
            except OSError:
                size = ""
            extra = "   ▶ slideshow" if self.slideshow.isActive() else ""
            text = f"{os.path.basename(path)}    {self.index + 1} / {len(self.paths)}    {dims}    {size}    {round(self.scale() * 100)}%{extra}"
            f = QFont(self.font())
            f.setPointSizeF(f.pointSizeF() * 1.05)
            p.setFont(f)
            r = p.fontMetrics().boundingRect(text).adjusted(-12, -6, 12, 6)
            r.moveTopLeft(self.rect().topLeft() + QPointF(12, 12).toPoint())
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 150))
            p.drawRoundedRect(QRectF(r), 8, 8)
            p.setPen(QColor("#eee"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
        p.end()

    def resizeEvent(self, ev):
        self._clamp()
        super().resizeEvent(ev)

    # ------------------------------------------------------------ input
    def _add_shortcuts(self):
        def add(keys, fn):
            a = QAction(self)
            a.setShortcuts([QKeySequence(k) for k in keys])
            a.triggered.connect(fn)
            self.addAction(a)
        add(["Right", "Space", "PgDown", "Down"], lambda: self.navigate(1))
        add(["Left", "Backspace", "PgUp", "Up"], lambda: self.navigate(-1))
        add(["Home"], lambda: self.goto(0))
        add(["End"], lambda: self.goto(len(self.paths) - 1))
        add(["F", "F11", "Return"], self.toggle_fullscreen)
        add(["Escape"], self._escape)
        add(["Q", "Ctrl+W"], self.close)
        add(["+", "=", "Ctrl++"], lambda: self.set_zoom(self.scale() * 1.25))
        add(["-", "Ctrl+-"], lambda: self.set_zoom(self.scale() / 1.25))
        add(["0"], self.fit)
        add(["1"], lambda: self.set_zoom(1.0))
        add(["U"], self._toggle_upscale)
        add(["R"], lambda: self._rotate(90))
        add(["L", "Shift+R"], lambda: self._rotate(-90))
        add(["H"], self._flip)
        add(["I", "Tab"], self._toggle_info)
        add(["S"], self.toggle_slideshow)
        add(["Delete"], self.delete_current)
        add(["Ctrl+C"], self._copy_image)
        add(["Alt+Return", "Ctrl+I"], lambda: self.on_properties and self.on_properties(self.current()))

    def _escape(self):
        if self.slideshow.isActive():
            self.toggle_slideshow()
        elif self.isFullScreen():
            self.toggle_fullscreen()
        else:
            self.close()

    def fit(self):
        self.zoom, self.offset = None, QPointF(0, 0)
        self.update()

    def _toggle_upscale(self):
        self.upscale = not self.upscale
        self.fit()

    def _rotate(self, deg):
        self.rotation = (self.rotation + deg) % 360
        self.fit()

    def _flip(self):
        self.flip_h = not self.flip_h
        self.update()

    def _toggle_info(self):
        self.show_info = not self.show_info
        self.update()

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.showFullScreen()
            self._hide_cursor.start()

    def toggle_slideshow(self):
        if self.slideshow.isActive():
            self.slideshow.stop()
        else:
            self.slideshow.start(int(self.settings.value("slideshow_secs", 4)) * 1000)
        self.update()

    def _copy_image(self):
        if self.pix:
            md = QGuiApplication.clipboard()
            md.setPixmap(self.pix)

    def wheelEvent(self, ev):
        dy = ev.angleDelta().y()
        if not dy:
            return
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.set_zoom(self.scale() * (1.15 if dy > 0 else 1 / 1.15), ev.position())
            return
        self._wheel_acc = getattr(self, "_wheel_acc", 0) + dy  # smooth touchpads send small deltas
        if abs(self._wheel_acc) >= 120:
            self.navigate(-1 if self._wheel_acc > 0 else 1)
            self._wheel_acc = 0

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag = (ev.position(), QPointF(self.offset))
        elif ev.button() == Qt.MouseButton.BackButton:
            self.navigate(-1)
        elif ev.button() == Qt.MouseButton.ForwardButton:
            self.navigate(1)
        elif ev.button() == Qt.MouseButton.MiddleButton:
            self.close()

    def mouseMoveEvent(self, ev):
        self.setCursor(Qt.CursorShape.ClosedHandCursor if self._drag else Qt.CursorShape.ArrowCursor)
        if self.isFullScreen():
            self._hide_cursor.start()
        if self._drag:
            start, off = self._drag
            if self.zoom is None:
                self.zoom = self.fit_scale()
            self.offset = off + (ev.position() - start)
            self._clamp()
            self.update()

    def mouseReleaseEvent(self, ev):
        self._drag = None
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.toggle_fullscreen()

    def contextMenuEvent(self, ev):
        path = self.current()
        if not path:
            return
        m = QMenu(self)
        m.addAction("Next\tRight", lambda: self.navigate(1))
        m.addAction("Previous\tLeft", lambda: self.navigate(-1))
        m.addSeparator()
        m.addAction("Exit Fullscreen\tF" if self.isFullScreen() else "Fullscreen\tF", self.toggle_fullscreen)
        m.addAction("Stop Slideshow\tS" if self.slideshow.isActive() else "Start Slideshow\tS", self.toggle_slideshow)
        m.addAction("Fit to Window\t0", self.fit)
        m.addAction("Actual Size\t1", lambda: self.set_zoom(1.0))
        m.addAction("Rotate Right\tR", lambda: self._rotate(90))
        m.addAction("Rotate Left\tL", lambda: self._rotate(-90))
        m.addAction("Flip Horizontally\tH", self._flip)
        m.addSeparator()
        m.addAction("Open With Default Application", lambda: util.open_default(path))
        if self.on_open_with:
            m.addAction("Open With…", lambda: self.on_open_with(path))
        m.addAction("Copy Image\tCtrl+C", self._copy_image)
        m.addAction("Copy Path", lambda: QGuiApplication.clipboard().setText(path))
        m.addAction("Set as Wallpaper", lambda: util.set_wallpaper(path))
        m.addSeparator()
        if self.on_properties:
            m.addAction("Properties\tAlt+Return", lambda: self.on_properties(path))
        if self.on_delete:
            m.addAction("Move to Trash\tDelete", self.delete_current)
        m.exec(ev.globalPos())

    def closeEvent(self, ev):
        self.slideshow.stop()
        self._stop_movie()
        self.pool.clear()
        if self.on_close and self.paths:
            self.on_close(self.current())
        super().closeEvent(ev)
