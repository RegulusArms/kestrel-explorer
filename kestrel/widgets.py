"""Models, delegates and widgets used by the main window."""
import fnmatch
import os
import re
import shutil
import subprocess

from PyQt6.QtCore import (QByteArray, QDir, QFileInfo, QMimeData, QPersistentModelIndex, QRect, QRectF, QSize,
                          QStorageInfo, Qt, QThread, QTimer, pyqtSignal)
from PyQt6.QtGui import (QAbstractFileIconProvider, QColor, QDrag, QFileSystemModel, QFont, QGuiApplication, QIcon, QPainter, QPainterPath, QPalette, QPen,
                         QStandardItem, QStandardItemModel)
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCompleter, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QPushButton, QScrollArea,
                             QSizePolicy, QStyle, QStyledItemDelegate, QToolButton, QVBoxLayout, QWidget)

from . import atc, metadata, places, thumbs, util

ThumbRole = Qt.ItemDataRole.UserRole + 50
PathRole = Qt.ItemDataRole.UserRole + 51
KindRole = Qt.ItemDataRole.UserRole + 53   # (is_dir, is_link)


# ---------------------------------------------------------------- models

class FastIconProvider(QAbstractFileIconProvider):
    """Type/icon lookup without touching the disk.

    The default provider sniffs file contents (opens every file) to name its type,
    and QFileSystemModel calls it on the UI thread - thousands of opens on a slow
    disk freezes the window. Icons are drawn by FSModel.data(), so return none here.
    """

    def __init__(self):
        super().__init__()
        self._types = {}

    def icon(self, _arg):
        return QIcon()

    def type(self, fi):
        if fi.isDir():
            return "Folder"
        suf = fi.suffix().lower()
        t = self._types.get(suf)
        if t is None:
            t = self._types[suf] = util.mime_for(fi.fileName(), False).comment() or "File"
        return t


class FSModel(QFileSystemModel):
    """QFileSystemModel that serves thumbnails and folder previews."""

    def __init__(self, thumbs, parent=None):
        super().__init__(parent)
        self.thumbs = thumbs
        self.thumb_size = 128
        self.folder_previews = True
        self.setReadOnly(False)
        self._icon_provider = FastIconProvider()  # model doesn't take ownership
        self.setIconProvider(self._icon_provider)
        self.drop_handler = None
        self._suffix_icons = {}
        thumbs.updated.connect(self._thumb_ready)

    def _thumb(self, index):
        fi = self.fileInfo(index)
        path = fi.absoluteFilePath()
        mtime = fi.lastModified().toSecsSinceEpoch()
        if fi.isDir():
            return self.thumbs.folder_pixmap(path, mtime, self.thumb_size, self.folder_previews)
        if not thumbs.can_thumbnail(path):
            return None
        return self.thumbs.get(path, mtime, False, self.thumb_size, fi.size())

    def _plain_icon(self, index):
        fi = self.fileInfo(index)
        if fi.isDir():
            return util.icon_for_path(fi.absoluteFilePath(), True)
        suf = fi.suffix().lower()
        icon = self._suffix_icons.get(suf)
        if icon is None:
            icon = self._suffix_icons[suf] = util.icon_for_path(fi.absoluteFilePath(), False)
        return icon

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.column() == 0:
            if role == ThumbRole:
                return self._thumb(index)
            if role == Qt.ItemDataRole.DecorationRole:
                pm = self._thumb(index)
                return QIcon(pm) if pm is not None else self._plain_icon(index)
            if role == PathRole:
                return self.filePath(index)
            if role == KindRole:
                fi = self.fileInfo(index)
                return fi.isDir(), fi.isSymLink()
        return super().data(index, role)

    def _thumb_ready(self, path):
        if os.path.dirname(path) != self.rootPath():
            return
        idx = self.index(path)
        if idx.isValid():
            self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole, ThumbRole])

    def dropMimeData(self, data, action, row, column, parent):
        if not data.hasUrls() or not self.drop_handler:
            return False
        target = self.filePath(parent) if parent.isValid() else self.rootPath()
        if not os.path.isdir(target):
            target = os.path.dirname(target)
        paths = [u.toLocalFile() for u in data.urls() if u.isLocalFile()]
        QTimer.singleShot(0, lambda: self.drop_handler(paths, target))
        return True


class SearchModel(QStandardItemModel):
    """Results of a recursive search."""

    def __init__(self, thumbs, parent=None):
        super().__init__(parent)
        self.thumbs = thumbs
        self.thumb_size = 128
        self.folder_previews = True
        self.rows = {}
        self.setHorizontalHeaderLabels(["Name", "Location", "Size", "Modified"])
        thumbs.updated.connect(self._thumb_ready)

    def add_paths(self, paths, locations=None):
        """Add result rows; `locations` optionally maps a path to the text for its Location column."""
        for p in paths:
            try:
                st = os.stat(p)
            except OSError:
                continue
            is_dir = os.path.isdir(p)
            name = QStandardItem(os.path.basename(p))
            name.setData(p, PathRole)
            name.setData((is_dir, st.st_mtime, st.st_size), Qt.ItemDataRole.UserRole + 52)
            name.setData((is_dir, os.path.islink(p)), KindRole)
            name.setToolTip(p)
            name.setEditable(False)
            loc = QStandardItem(locations.get(p, os.path.dirname(p)) if locations else os.path.dirname(p))
            size = QStandardItem("" if is_dir else util.human_size(st.st_size))
            size.setData(st.st_size, Qt.ItemDataRole.UserRole)
            from time import localtime, strftime
            mod = QStandardItem(strftime("%Y-%m-%d %H:%M", localtime(st.st_mtime)))
            for it in (loc, size, mod):
                it.setEditable(False)
            self.appendRow([name, loc, size, mod])
            self.rows[p] = name

    def clear_results(self):
        self.removeRows(0, self.rowCount())
        self.rows.clear()

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.column() == 0 and role in (ThumbRole, Qt.ItemDataRole.DecorationRole):
            item = self.itemFromIndex(index)
            path = item.data(PathRole)
            is_dir, mtime, size = item.data(Qt.ItemDataRole.UserRole + 52)
            pm = None
            if is_dir:
                pm = self.thumbs.folder_pixmap(path, mtime, self.thumb_size, self.folder_previews)
            elif thumbs.can_thumbnail(path):
                pm = self.thumbs.get(path, mtime, False, self.thumb_size, size)
            if role == ThumbRole:
                return pm
            return QIcon(pm) if pm is not None else util.icon_for_path(path, is_dir)
        return super().data(index, role)

    def _thumb_ready(self, path):
        item = self.rows.get(path)
        if item:
            idx = item.index()
            self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole, ThumbRole])


class SearchThread(QThread):
    found = pyqtSignal(list)

    def __init__(self, root, query, hidden, parent=None, contents=False):
        super().__init__(parent)
        self.root, self.hidden, self.contents, self.query = root, hidden, contents, query
        q = query.lower()
        self.match = (lambda n: fnmatch.fnmatch(n.lower(), q)) if any(c in q for c in "*?[") else (lambda n: q in n.lower())
        self.stop = False

    def run(self):
        if self.contents:
            self._run_contents()
            return
        batch, count = [], 0
        for root, dirs, files in os.walk(self.root):
            if self.stop:
                return
            if not self.hidden:
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                files = [f for f in files if not f.startswith(".")]
            dirs.sort(key=util.natural_key)
            for n in dirs + sorted(files, key=util.natural_key):
                if self.match(n):
                    batch.append(os.path.join(root, n))
                    count += 1
            if len(batch) >= 50:
                self.found.emit(batch)
                batch = []
            if count >= 10000:
                break
        if batch:
            self.found.emit(batch)


    def _run_contents(self):
        """Search file contents (and names) with the desktop's search index (localsearch), within root."""
        terms = [t for t in re.split(r"[\s*?\[\]]+", self.query) if t]
        if not terms:
            return
        try:
            out = subprocess.run(["localsearch", "search", "-f", "--limit", "20000", *terms], capture_output=True,
                                 text=True, timeout=60, stdin=subprocess.DEVNULL).stdout
        except (OSError, subprocess.SubprocessError):
            return
        prefix = self.root.rstrip("/") + "/"
        batch = []
        for line in out.splitlines():
            if self.stop:
                return
            p = util.uri_to_path(line.strip())
            if not p or not p.startswith(prefix) or not os.path.lexists(p):
                continue
            if not self.hidden and any(part.startswith(".") for part in p[len(prefix):].split("/")):
                continue
            batch.append(p)
            if len(batch) >= 50:
                self.found.emit(batch)
                batch = []
        if batch:
            self.found.emit(batch)


def can_search_contents():
    return shutil.which("localsearch") is not None


# ---------------------------------------------------------------- grid delegate

class GridDelegate(QStyledItemDelegate):
    def __init__(self, pane, parent=None):
        super().__init__(parent)
        self.pane = pane
        self.icon_size = 128

    def cell_size(self):
        fm = self.pane.fontMetrics()
        w = max(self.icon_size + 16, 96)
        return QSize(w, self.icon_size + 14 + fm.height() * 2 + 6)

    def sizeHint(self, option, index):
        return self.cell_size()

    def _lines(self, fm, text, width):
        if fm.horizontalAdvance(text) <= width:
            return [text]
        # break the first line at a natural boundary if possible
        cut = len(text)
        while cut > 1 and fm.horizontalAdvance(text[:cut]) > width:
            cut -= 1
        brk = max(text.rfind(c, 0, cut) for c in " _-.")
        if brk > cut // 2:
            cut = brk + 1
        first, rest = text[:cut], text[cut:]
        return [first, fm.elidedText(rest, Qt.TextElideMode.ElideMiddle, width)]

    def paint(self, p, option, index):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = option.rect.adjusted(3, 3, -3, -3)
        pal = option.palette
        selected = option.state & QStyle.StateFlag.State_Selected
        hover = option.state & QStyle.StateFlag.State_MouseOver
        if selected or hover:
            c = QColor(pal.color(QPalette.ColorRole.Highlight))
            c.setAlpha(110 if selected else 40)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(r), 8, 8)
        path = index.data(PathRole) or ""
        name = index.data(Qt.ItemDataRole.DisplayRole) or os.path.basename(path)
        is_dir, is_link = index.data(KindRole) or (False, False)
        dim = name.startswith(".") or path in self.pane.cut_paths
        if dim:
            p.setOpacity(0.5)
        s = self.icon_size
        icon_rect = QRect(r.x() + (r.width() - s) // 2, r.y() + 6, s, s)
        pm = index.data(ThumbRole)
        animator = getattr(self.pane, "animator", None)
        anim = animator.frame(path, index, s) if animator is not None and not is_dir else None  # GIF / WebM playing
        badge_box = QRectF(icon_rect)   # where the star goes: the corner of what's drawn
        if anim is not None or pm is not None:
            spm = anim if anim is not None else self.pane.thumbs.scaled(pm, s)
            dpr = spm.devicePixelRatio()
            w, h = spm.width() / dpr, spm.height() / dpr
            target = QRectF(icon_rect.x() + (s - w) / 2, icon_rect.y() + (s - h), w, h)
            badge_box = target
            if not is_dir:
                clip = QPainterPath()
                clip.addRoundedRect(target, 4, 4)
                p.save()
                p.setClipPath(clip)
                p.drawPixmap(target, spm, QRectF(spm.rect()))
                p.restore()
                p.setPen(QColor(0, 0, 0, 50))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(target, 4, 4)
                if util.is_video(path) and anim is None:
                    self._play_badge(p, target)
            else:
                p.drawPixmap(target, spm, QRectF(spm.rect()))
        else:
            icon = index.data(Qt.ItemDataRole.DecorationRole)
            if isinstance(icon, QIcon):
                icon.paint(p, icon_rect)
        if is_link:
            em = util.theme_icon("emblem-symbolic-link", "emblem-link")
            es = max(16, s // 5)
            em.paint(p, QRect(icon_rect.right() - es, icon_rect.bottom() - es, es, es))
        if places.is_starred(path):
            bs = max(16, s // 6)
            self._star_badge(p, QRectF(badge_box.right() - bs * 0.75, badge_box.top() - bs * 0.25, bs, bs))
        # text
        p.setPen(pal.color(QPalette.ColorRole.Text))
        if selected:
            f = QFont(option.font)
            f.setWeight(QFont.Weight.DemiBold)
            p.setFont(f)
        fm = p.fontMetrics()
        text_rect = QRect(r.x() + 4, icon_rect.bottom() + 6, r.width() - 8, fm.height() * 2 + 2)
        for i, line in enumerate(self._lines(fm, name, text_rect.width())):
            lr = QRect(text_rect.x(), text_rect.y() + i * fm.height(), text_rect.width(), fm.height())
            p.drawText(lr, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, line)
        p.restore()

    @staticmethod
    def _star_badge(p, rect):
        """A small gold star (starred items)."""
        import math
        c, r = rect.center(), rect.width() / 2
        star = QPainterPath()
        for i in range(10):
            radius = r if i % 2 == 0 else r * 0.45
            a = math.pi / 2 + i * math.pi / 5
            x, y = c.x() + radius * math.cos(a), c.y() - radius * math.sin(a)
            if i == 0:
                star.moveTo(x, y)
            else:
                star.lineTo(x, y)
        star.closeSubpath()
        p.save()
        p.setOpacity(1.0)
        p.setPen(QColor(120, 80, 0, 220))
        p.setBrush(QColor("#f6c02d"))
        p.drawPath(star)
        p.restore()

    @staticmethod
    def _play_badge(p, rect):
        d = max(14.0, min(rect.width(), rect.height()) * 0.28)
        c = rect.center()
        circle = QRectF(c.x() - d / 2, c.y() - d / 2, d, d)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 140))
        p.drawEllipse(circle)
        tri = QPainterPath()
        tri.moveTo(c.x() - d * 0.15, c.y() - d * 0.22)
        tri.lineTo(c.x() + d * 0.25, c.y())
        tri.lineTo(c.x() - d * 0.15, c.y() + d * 0.22)
        tri.closeSubpath()
        p.setBrush(QColor(255, 255, 255, 230))
        p.drawPath(tri)


# ---------------------------------------------------------------- path bar

class _Crumbs(QWidget):
    clicked = pyqtSignal()

    def mousePressEvent(self, ev):
        self.clicked.emit()


class PathBar(QWidget):
    navigate = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path = ""
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.crumb_box = _Crumbs()
        # Ignored: the crumbs' width must not depend on which buttons are visible,
        # otherwise hiding a button resizes the bar, which re-runs _fit -> jitter.
        self.crumb_box.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(200)
        self._fitting = False
        self._fit_width = -1
        self.crumb_lay = QHBoxLayout(self.crumb_box)
        self.crumb_lay.setSizeConstraint(QHBoxLayout.SizeConstraint.SetNoConstraint)
        self.crumb_lay.setContentsMargins(0, 0, 0, 0)
        self.crumb_lay.setSpacing(0)
        self.crumb_box.clicked.connect(self.start_edit)
        self.edit = QLineEdit()
        self.edit.setClearButtonEnabled(True)
        self.edit.hide()
        comp_model = QFileSystemModel(self)
        comp_model.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot)
        comp_model.setRootPath("/")
        comp = QCompleter(comp_model, self)
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.edit.setCompleter(comp)
        self.edit.returnPressed.connect(self._commit)
        self.edit.installEventFilter(self)
        self.edit_btn = QToolButton()
        self.edit_btn.setIcon(util.theme_icon("document-edit-symbolic", "edit-symbolic", "document-edit"))
        self.edit_btn.setToolTip("Type a location (Ctrl+L)")
        self.edit_btn.setAutoRaise(True)
        self.edit_btn.clicked.connect(lambda: self.cancel_edit() if self.edit.isVisible() else self.start_edit())
        lay.addWidget(self.crumb_box, 1)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.edit_btn)
        self.buttons = []
        self.overflow = QToolButton()
        self.overflow.setText("…")
        self.overflow.setAutoRaise(True)
        self.overflow.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

    def eventFilter(self, obj, ev):
        if obj is self.edit and ev.type() == ev.Type.KeyPress and ev.key() == Qt.Key.Key_Escape:
            self.cancel_edit()
            return True
        # a popup (the right-click menu, the completer) takes focus while still editing
        if (obj is self.edit and ev.type() == ev.Type.FocusOut and ev.reason() != Qt.FocusReason.PopupFocusReason
                and not self.edit.completer().popup().isVisible()):
            QTimer.singleShot(0, self.cancel_edit)
        return False

    def start_edit(self):
        self.crumb_box.hide()
        self.edit.setText("" if "://" in self.path else self.path)
        self.edit.show()
        self.edit.setFocus()
        self.edit.selectAll()

    def cancel_edit(self):
        self.edit.hide()
        self.crumb_box.show()

    def _commit(self):
        t = os.path.expanduser(self.edit.text().strip())
        if t.startswith("file://"):
            t = util.uri_to_path(t)
        self.cancel_edit()
        if t:
            self.navigate.emit(t)

    def set_path(self, path):
        from .overview import OVERVIEW, OVERVIEW_TITLE
        self.path = path
        while self.crumb_lay.count():
            w = self.crumb_lay.takeAt(0).widget()
            if w and w is not self.overflow:
                w.deleteLater()
        self.buttons = []
        parts = []
        if path == OVERVIEW:
            parts.append((OVERVIEW_TITLE, OVERVIEW, "computer"))
            rest, base = "", ""
        elif path in places.VIRTUAL:
            parts.append((places.title(path), path, places.icon_name(path)))
            rest, base = "", ""
        elif path == util.HOME or path.startswith(util.HOME + "/"):
            parts.append(("Home", util.HOME, "user-home"))
            rest = path[len(util.HOME):].strip("/")
            base = util.HOME
        else:
            parts.append(("/", "/", "drive-harddisk"))
            rest = path.strip("/")
            base = ""
        for seg in [s for s in rest.split("/") if s]:
            base = base + "/" + seg
            parts.append((seg, base, None))
        self.crumb_lay.addWidget(self.overflow)
        for label, p, icon in parts:
            b = QToolButton()
            b.setText(label)
            b.setAutoRaise(True)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            if icon:
                b.setIcon(util.theme_icon(icon))
            if p == path:
                f = b.font()
                f.setBold(True)
                b.setFont(f)
            b.clicked.connect(lambda _=False, p=p: self.navigate.emit(p))
            b.setProperty("crumb_path", p)
            self.crumb_lay.addWidget(b)
            self.buttons.append(b)
        self.crumb_lay.addStretch(1)
        QTimer.singleShot(0, lambda: self._fit(force=True))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._fit()

    def _fit(self, force=False):
        avail = self.crumb_box.width() - 30
        if self._fitting or (avail == self._fit_width and not force):
            return
        self._fitting = True
        self._fit_width = avail
        try:
            # sizeHint() is valid for hidden widgets, so decide first, then apply once
            widths = [b.sizeHint().width() for b in self.buttons]
            total, n_hide = sum(widths), 0
            while n_hide < len(self.buttons) - 1 and total > avail:
                total -= widths[n_hide]
                n_hide += 1
            for i, b in enumerate(self.buttons):
                if b.isHidden() != (i < n_hide):
                    b.setVisible(i >= n_hide)
            self.overflow.setVisible(n_hide > 0)
            if n_hide:
                m = QMenu(self.overflow)
                for b in self.buttons[:n_hide]:
                    m.addAction(b.text(), lambda p=b.property("crumb_path"): self.navigate.emit(p))
                self.overflow.setMenu(m)
        finally:
            self._fitting = False


# ---------------------------------------------------------------- sidebar

# ---------------------------------------------------------------- sidebar order (shared with the C++ version)
# settings: sidebar_sections, sidebar_collapsed, sidebar_places_order, sidebar_devices_order; the bookmarks' order is
# the GTK bookmarks file's

SECTIONS = ["places", "bookmarks", "devices"]  # the default order
SECTION_ROLE = Qt.ItemDataRole.UserRole + 1
KEY_ROLE = Qt.ItemDataRole.UserRole + 2
SIDEBAR_MIME = "application/x-kestrel-sidebar"


def section_order(saved):
    """The saved order, without unknown ids, plus any missing."""
    out = []
    for i in list(saved or []) + SECTIONS:
        if i in SECTIONS and i not in out:
            out.append(i)
    return out


def ordered(keys, saved):
    """Keys in the saved order (those present), then the rest in their natural order."""
    out = [k for k in dict.fromkeys(saved or []) if k in keys]
    return out + [k for k in keys if k not in out]


def moved(keys, key, index):
    """Keys with `key` moved to insertion point `index` (0 = first, len(keys) = last, counted before the move)."""
    keys = list(keys)
    if key not in keys:
        return keys
    frm = keys.index(key)
    to = max(0, min(index, len(keys)))
    if to > frm:
        to -= 1
    keys.insert(to, keys.pop(frm))
    return keys


def _settings():
    from . import app
    return app._settings


def _setting_list(key):
    return [str(x) for x in (_settings().value(key, [], type=list) or [])]


class Sidebar(QListWidget):
    open_path = pyqtSignal(str, bool)            # path, new tab
    dropped = pyqtSignal(list, str)              # sources, target dir
    bookmarks_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setIconSize(QSize(18, 18))
        self.setFrameShape(QListWidget.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.itemClicked.connect(self._clicked)
        self.setStyleSheet("QListWidget { background: palette(window); } QListWidget::item { padding: 3px; }")
        util.on_palette_change(self, self.refresh)  # the headers' colour is set per item
        self._mounts = None
        self._phones = []  # phones and cameras (Gio)
        self._press_pos = None
        self._press_index = QPersistentModelIndex()
        self._drop_line = -1  # y of the drop indicator while rearranging, -1 = none
        # phones and cameras come and go through Gio's volume monitor (QStorageInfo doesn't see them)
        self._phone_timer = QTimer(self, singleShot=True, interval=300, timeout=self.refresh)
        self._monitor = util.Gio.VolumeMonitor.get() if util.Gio else None
        if self._monitor is not None:
            for sig in ("volume-added", "volume-removed", "volume-changed", "mount-added", "mount-removed",
                        "mount-changed"):
                self._monitor.connect(sig, lambda *a: self._phone_timer.start())
        self.refresh()
        self.timer = QTimer(self, interval=4000, timeout=self._check_mounts)
        self.timer.start()

    def _header(self, section, title, collapsed):
        it = QListWidgetItem(("▸  " if collapsed else "▾  ") + title)
        it.setFlags(Qt.ItemFlag.ItemIsEnabled)  # clickable (collapse), not selectable
        f = it.font()
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() * 0.85)
        it.setFont(f)
        it.setForeground(self.palette().color(QPalette.ColorRole.PlaceholderText))
        it.setData(Qt.ItemDataRole.UserRole, ("header", section))
        it.setData(SECTION_ROLE, section)
        it.setToolTip("Click to expand · drag to move the section" if collapsed
                      else "Click to collapse · drag to move the section")
        self.addItem(it)

    def _add(self, e, section):
        """e: dict with label, path, icon, kind, extra, key (what the saved order lists: the path; "phone:<name>" for
        a phone) and tip."""
        icon = e["icon"]
        it = QListWidgetItem(icon if isinstance(icon, QIcon) else util.theme_icon(icon, "folder"), e["label"])
        it.setData(PathRole, e["path"])
        it.setData(Qt.ItemDataRole.UserRole, (e.get("kind", "place"), e.get("extra")))
        it.setData(SECTION_ROLE, section)
        it.setData(KEY_ROLE, e.get("key") or e["path"])
        it.setToolTip(e.get("tip") or e["path"])
        self.addItem(it)

    def _in_order(self, entries, setting):
        keys = [e.get("key") or e["path"] for e in entries]
        return [entries[keys.index(k)] for k in ordered(keys, _setting_list(setting))]

    def _mount_list(self):
        out = []
        for v in QStorageInfo.mountedVolumes():
            dev = bytes(v.device()).decode(errors="ignore")
            root = v.rootPath()
            if not v.isValid() or not v.isReady() or root == "/":
                continue
            if not dev.startswith("/dev/") or root.startswith(("/boot", "/snap", "/var/snap", "/run/", "/sys")):
                if not root.startswith(("/media/", "/run/media/", "/mnt")):
                    continue
            if dev.startswith("/dev/loop"):
                continue
            out.append((v.displayName() or os.path.basename(root), root, dev, v.bytesTotal()))
        return sorted(out, key=lambda m: m[1])

    def _check_mounts(self):
        if self._mount_list() != self._mounts:
            self.refresh()

    def refresh(self):
        current = self.currentItem().data(PathRole) if self.currentItem() else None
        self.clear()
        from .overview import OVERVIEW, OVERVIEW_TITLE, phone_infos

        def entry(label, path, icon, kind="place", extra=None, key=None, tip=None):
            return {"label": label, "path": path, "icon": icon, "kind": kind, "extra": extra, "key": key, "tip": tip}
        place_list = [entry(OVERVIEW_TITLE, OVERVIEW, "computer", "overview"), entry("Home", util.HOME, "user-home"),
                      entry("Recent", places.RECENT, util.theme_icon("document-open-recent", "folder-recent", "folder"),
                            "recent"),
                      entry("Starred", places.STARRED, util.theme_icon("starred", "starred-symbolic", "folder"),
                            "starred")]
        for key, label, icon in (("DESKTOP", "Desktop", "user-desktop"), ("DOCUMENTS", "Documents", "folder-documents"),
                                 ("DOWNLOAD", "Downloads", "folder-download"), ("MUSIC", "Music", "folder-music"),
                                 ("PICTURES", "Pictures", "folder-pictures"), ("VIDEOS", "Videos", "folder-videos")):
            p = util.xdg_user_dir(key)
            if os.path.isdir(p):
                place_list.append(entry(label, p, icon))
        empty = util.trash_is_empty()  # home trash and every drive's trash
        place_list.append(entry("Trash", str(util.TRASH_DIR / "files"), "user-trash" if empty else "user-trash-full",
                                "trash"))
        bookmark_list = []  # in the bookmarks file's order (moving one rewrites the file)
        for i, (path, label) in enumerate(util.read_bookmarks()):
            remote = not path.startswith("/") or path.startswith("/run/user/")
            bookmark_list.append(entry(label, path, "folder-remote" if remote else "folder", "bookmark", i))
        device_list = [entry("Computer", "/", "drive-harddisk", "root")]
        self._mounts = self._mount_list()
        for name, root, dev, total in self._mounts:
            label = f"{name} ({util.human_size(total)})" if total else name
            icon = "drive-removable-media" if root.startswith(("/media/", "/run/media/")) else "drive-harddisk"
            device_list.append(entry(label, root, icon, "mount", dev))
        self._phones = phone_infos(self._monitor)
        for i, ph in enumerate(self._phones):
            # a phone that isn't mounted yet opens its URI: open_location mounts it
            mounted = ph.get("mounted", True)
            icon = ph["icon"] if not ph["icon"].isNull() else util.theme_icon("phone", "drive-removable-media")
            device_list.append(entry(ph["name"], ph["root"] if mounted else ph["uri"], icon, "phone", i,
                                     "phone:" + ph["name"], ph["fs"] if mounted else ph["fs"] + " — click to connect"))
        collapsed = _setting_list("sidebar_collapsed")
        for sid in section_order(_setting_list("sidebar_sections")):
            entries = (self._in_order(place_list, "sidebar_places_order") if sid == "places" else
                       self._in_order(device_list, "sidebar_devices_order") if sid == "devices" else bookmark_list)
            if not entries:
                continue
            shut = sid in collapsed
            self._header(sid, sid.upper(), shut)
            if not shut:
                for e in entries:
                    self._add(e, sid)
        if current:
            self.select_path(current)

    def shown_sections(self):
        """In order."""
        return [self.item(i).data(SECTION_ROLE) for i in range(self.count())
                if self.item(i).data(Qt.ItemDataRole.UserRole)[0] == "header"]

    def entry_keys(self, section):
        """In order; empty when collapsed."""
        return [self.item(i).data(KEY_ROLE) for i in range(self.count())
                if self.item(i).data(SECTION_ROLE) == section
                and self.item(i).data(Qt.ItemDataRole.UserRole)[0] != "header"]

    def _section_row(self, section):
        """Its header's row, -1 if not shown."""
        for i in range(self.count()):
            if self.item(i).data(SECTION_ROLE) == section:
                return i
        return -1

    def _section_end(self, section):
        """The row after its last entry."""
        end = self._section_row(section)
        if end < 0:
            return -1
        while end < self.count() and self.item(end).data(SECTION_ROLE) == section:
            end += 1
        return end

    def _save_order(self, setting, keys):
        # keep the places of entries that aren't shown now (a drive that isn't plugged in)
        keys = list(keys) + [k for k in _setting_list(setting) if k not in keys]
        _settings().setValue(setting, keys)
        _settings().sync()  # other Kestrels read the file as soon as they hear the report
        atc.announce("sidebar")  # refreshes every sidebar, here and in other Kestrels

    # rearranging (what drag and drop does): an entry within its section, or a whole section, to insertion point
    # `index` (counted before the move); click a header to collapse/expand it
    def move_entry(self, section, key, index):
        keys = self.entry_keys(section)
        if key not in keys:
            return
        if section == "bookmarks":
            bms = util.read_bookmarks()
            frm = next((i for i, b in enumerate(bms) if b[0] == key), -1)
            if frm < 0:
                return
            to = max(0, min(index, len(bms)))
            if to > frm:
                to -= 1
            if to == frm:
                return
            bms.insert(to, bms.pop(frm))
            util.write_bookmarks(bms)  # announces "bookmarks": every sidebar refreshes
            return
        now = moved(keys, key, index)
        if now != keys:
            self._save_order("sidebar_places_order" if section == "places" else "sidebar_devices_order", now)

    def move_section(self, section, index):
        order = section_order(_setting_list("sidebar_sections"))
        # index counts the sections shown; hidden ones (no bookmarks) keep their place relative to the next shown one
        shown = self.shown_sections()
        now_shown = moved(shown, section, index)
        if now_shown == shown:
            return
        now = []
        for sid in now_shown:
            for other in order[:order.index(sid)]:  # hidden sections that came before it
                if other not in shown and other not in now:
                    now.append(other)
            now.append(sid)
        now += [sid for sid in order if sid not in now]
        _settings().setValue("sidebar_sections", now)
        _settings().sync()
        atc.announce("sidebar")

    def toggle_section(self, section):
        collapsed = _setting_list("sidebar_collapsed")
        if section in collapsed:
            collapsed.remove(section)
        else:
            collapsed.append(section)
        _settings().setValue("sidebar_collapsed", collapsed)
        _settings().sync()
        atc.announce("sidebar")

    def select_path(self, path):
        self.blockSignals(True)
        self.setCurrentItem(None)
        for i in range(self.count()):
            if self.item(i).data(PathRole) == path:
                self.setCurrentRow(i)
                break
        self.blockSignals(False)

    def _clicked(self, it):
        if it.data(Qt.ItemDataRole.UserRole)[0] == "header":
            self.toggle_section(it.data(SECTION_ROLE))
            return
        p = it.data(PathRole)
        if p:
            mods = QGuiApplication.keyboardModifiers()
            self.open_path.emit(p, bool(mods & Qt.KeyboardModifier.ControlModifier))

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._press_pos = ev.position().toPoint()
            self._press_index = QPersistentModelIndex(self.indexAt(self._press_pos))
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        # press and drag an entry (within its section) or a header (the whole section) to rearrange
        if (ev.buttons() & Qt.MouseButton.LeftButton and self._press_index.isValid()
                and (ev.position().toPoint() - self._press_pos).manhattanLength() >= QApplication.startDragDistance()):
            it = self.item(self._press_index.row())
            self._press_index = QPersistentModelIndex()
            if it is None:
                return
            is_header = it.data(Qt.ItemDataRole.UserRole)[0] == "header"
            key = "" if is_header else it.data(KEY_ROLE)
            mime = QMimeData()
            mime.setData(SIDEBAR_MIME, QByteArray((it.data(SECTION_ROLE) + "\n" + key).encode()))
            drag = QDrag(self)
            drag.setMimeData(mime)
            r = self.visualItemRect(it)
            drag.setPixmap(self.viewport().grab(r))
            drag.setHotSpot(self._press_pos - r.topLeft())
            drag.exec(Qt.DropAction.MoveAction)
            self._drop_line = -1
            self.viewport().update()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        self._press_index = QPersistentModelIndex()
        if ev.button() == Qt.MouseButton.MiddleButton:
            it = self.itemAt(ev.position().toPoint())
            if it and it.data(PathRole):
                self.open_path.emit(it.data(PathRole), True)
                return
        super().mouseReleaseEvent(ev)

    def _menu(self, pos):
        it = self.itemAt(pos)
        if not it or not it.data(PathRole):
            return
        path = it.data(PathRole)
        kind, extra = it.data(Qt.ItemDataRole.UserRole)
        m = QMenu(self)
        m.addAction("Open", lambda: self.open_path.emit(path, False))
        m.addAction("Open in New Tab", lambda: self.open_path.emit(path, True))
        if kind != "phone" and os.path.isdir(path):  # a stat on a phone can wait behind its transfers
            m.addAction("Open in Terminal", lambda: util.open_terminal(path))
        if kind == "bookmark":
            m.addSeparator()
            m.addAction("Edit Bookmark…", lambda: self._edit_bookmark(path))
            m.addAction("Remove Bookmark", lambda: self._remove_bookmark(extra))
            m.addAction("Move Up", lambda: self._move_bookmark(extra, -1))
            m.addAction("Move Down", lambda: self._move_bookmark(extra, 1))
        elif kind == "trash":
            m.addSeparator()
            m.addAction("Empty Trash", lambda: self.window().empty_trash())
        elif kind == "mount":
            m.addSeparator()
            m.addAction("Unmount", lambda: self._unmount(path))
        elif kind == "phone" and extra < len(self._phones) and self._phones[extra].get("mount") is not None:
            m.addSeparator()
            m.addAction("Eject" if self._phones[extra]["mount"].can_eject() else "Unmount",
                        lambda: self._eject_phone(extra))
        m.exec(self.viewport().mapToGlobal(pos))

    def _unmount(self, path):
        import subprocess
        r = subprocess.run(["gio", "mount", "-u", path], capture_output=True, text=True)
        if r.returncode != 0:
            r = subprocess.run(["umount", path], capture_output=True, text=True)
        if r.returncode != 0:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Unmount", r.stderr or "Unmount failed")
        self.refresh()

    def _eject_phone(self, i):
        if i >= len(self._phones) or self._phones[i].get("mount") is None:
            return
        from .overview import unmount

        def done(err):
            if err:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "Unmount", err)
            self.refresh()
        unmount(self, self._phones[i]["mount"], done)

    def _edit_bookmark(self, path):
        from .dialogs import edit_bookmark
        if edit_bookmark(self, path):
            self.refresh()

    def _remove_bookmark(self, i):
        bms = util.read_bookmarks()
        del bms[i]
        util.write_bookmarks(bms)
        self.refresh()

    def _move_bookmark(self, i, d):
        bms = util.read_bookmarks()
        j = i + d
        if 0 <= j < len(bms):
            bms[i], bms[j] = bms[j], bms[i]
            util.write_bookmarks(bms)
            self.refresh()

    def add_bookmark(self, path):
        bms = util.read_bookmarks()
        if path not in [b[0] for b in bms]:
            bms.append((path, os.path.basename(path.rstrip("/")) or path))
            util.write_bookmarks(bms)
        self.refresh()

    def _drop_for(self, pos, section, key):
        """The insertion point under pos, as (row, index): between the section's entries, or (key empty: a whole
        section) between sections. row -1: nowhere."""
        n = self.count()
        row = n
        it = self.itemAt(pos)
        if it is not None:
            r = self.visualItemRect(it)
            row = self.row(it) + (1 if pos.y() >= r.center().y() else 0)
        elif n and pos.y() < self.visualItemRect(self.item(0)).top():
            row = 0
        if key:
            first, end = self._section_row(section) + 1, self._section_end(section)
            if first <= 0:
                return -1, -1
            row = max(first, min(row, end))
            return row, row - first
        # a whole section: snap to the nearer edge of the section under the pointer
        shown = self.shown_sections()
        target = len(shown)
        if row < n:
            over = self.item(row).data(SECTION_ROLE)
            if over in shown:
                top, end = self._section_row(over), self._section_end(over)
                i = shown.index(over)
                target = i if (row - top) * 2 <= end - top else i + 1
        return (self._section_row(shown[target]) if target < len(shown) else n), target

    def _sidebar_drag(self, ev):
        """(section, key) of a rearranging drag from this sidebar, or None."""
        if not ev.mimeData().hasFormat(SIDEBAR_MIME) or ev.source() is not self:
            return None
        section, _, key = bytes(ev.mimeData().data(SIDEBAR_MIME)).decode().partition("\n")
        return section, key

    # drag & drop: rearranging, or files onto places
    def dragEnterEvent(self, ev):
        if self._sidebar_drag(ev) is not None or ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dragMoveEvent(self, ev):
        if ev.mimeData().hasFormat(SIDEBAR_MIME):
            sk = self._sidebar_drag(ev)
            row = self._drop_for(ev.position().toPoint(), *sk)[0] if sk else -1
            if row < 0:
                self._drop_line = -1
                ev.ignore()
            else:
                self._drop_line = (self.visualItemRect(self.item(row)).top() if row < self.count()
                                   else self.visualItemRect(self.item(self.count() - 1)).bottom() + 1)
                ev.acceptProposedAction()
            self.viewport().update()
            return
        it = self.itemAt(ev.position().toPoint())
        if it and it.data(PathRole) and it.data(PathRole).startswith("/") and os.path.isdir(it.data(PathRole)):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragLeaveEvent(self, ev):
        self._drop_line = -1
        self.viewport().update()
        super().dragLeaveEvent(ev)

    def dropEvent(self, ev):
        if ev.mimeData().hasFormat(SIDEBAR_MIME):
            self._drop_line = -1
            self.viewport().update()
            sk = self._sidebar_drag(ev)
            if sk is None:
                return
            row, index = self._drop_for(ev.position().toPoint(), *sk)
            if row < 0:
                return
            ev.acceptProposedAction()
            section, key = sk
            # after the drag has finished: moving rebuilds the list
            QTimer.singleShot(0, lambda: self.move_entry(section, key, index) if key
                              else self.move_section(section, index))
            return
        it = self.itemAt(ev.position().toPoint())
        if not it or not it.data(PathRole):
            return
        paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        target = it.data(PathRole)
        ev.acceptProposedAction()
        QTimer.singleShot(0, lambda: self.dropped.emit(paths, target))

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if self._drop_line < 0:
            return
        p = QPainter(self.viewport())
        p.setPen(QPen(self.palette().color(QPalette.ColorRole.Highlight), 2))
        p.drawLine(4, self._drop_line, self.viewport().width() - 4, self._drop_line)
        p.end()


# ---------------------------------------------------------------- info panel

class InfoPanel(QScrollArea):
    def __init__(self, thumbs, parent=None):
        super().__init__(parent)
        self.thumbs = thumbs
        self.path = None
        self.folder_previews = True
        self.setWidgetResizable(True)
        self.setMinimumWidth(220)
        w = QWidget()
        self.setWidget(w)
        lay = QVBoxLayout(w)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(180)
        lay.addWidget(self.preview)
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        f = QFont(self.font())
        f.setBold(True)
        self.title.setFont(f)
        lay.addWidget(self.title)
        self.form = QFormLayout()
        lay.addLayout(self.form)
        self.extra = QVBoxLayout()
        lay.addLayout(self.extra)
        lay.addStretch(1)
        self.timer = QTimer(self, singleShot=True, interval=120, timeout=self._refresh)
        thumbs.updated.connect(lambda p: p == self.path and self._set_preview())

    def show_path(self, path):
        self.path = path
        self.timer.start()

    def _set_preview(self):
        path = self.path
        if not path or not os.path.exists(path):
            self.preview.clear()
            return
        fi = QFileInfo(path)
        is_dir = fi.isDir()
        size = max(160, min(self.viewport().width() - 24, 512))
        pm = None
        if is_dir:
            pm = self.thumbs.folder_pixmap(path, fi.lastModified().toSecsSinceEpoch(), 512, self.folder_previews)
        elif thumbs.can_thumbnail(path):
            pm = self.thumbs.get(path, fi.lastModified().toSecsSinceEpoch(), False, 512, fi.size())
        if pm is not None:
            self.preview.setPixmap(self.thumbs.scaled(pm, size))
        else:
            self.preview.setPixmap(util.icon_for_path(path, is_dir).pixmap(128, 128))

    def _clear(self):
        while self.form.rowCount():
            self.form.removeRow(0)
        while self.extra.count():
            w = self.extra.takeAt(0).widget()
            if w:
                w.deleteLater()

    def _row(self, k, v):
        lab = QLabel(v)
        lab.setWordWrap(True)
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.form.addRow(k + ":", lab)

    def _refresh(self):
        self._clear()
        path = self.path
        if not path or not os.path.lexists(path):
            self.title.setText("")
            self.preview.clear()
            return
        self.title.setText(os.path.basename(path.rstrip("/")) or path)
        self._set_preview()
        try:
            st = os.stat(path)
        except OSError:
            st = os.lstat(path)
        from time import localtime, strftime
        if os.path.isdir(path):
            try:
                n = len(os.listdir(path))
                self._row("Contents", f"{n} item{'s' if n != 1 else ''}")
            except OSError:
                self._row("Contents", "unreadable")
        else:
            self._row("Size", util.human_size(st.st_size))
            self._row("Type", util.mime_for(path, False).comment())
        self._row("Modified", strftime("%Y-%m-%d %H:%M", localtime(st.st_mtime)))
        if os.path.islink(path):
            self._row("Link to", os.readlink(path))
        if util.is_image(path):
            for k, v in metadata.basic_info(path):
                self._row(k, v)
            for k, v in metadata.ai_info(path).items():
                lab = QLabel(f"<b>{k}</b>")
                self.extra.addWidget(lab)
                if len(v) > 60 or "\n" in v:
                    ed = QPlainTextEdit(v)
                    ed.setReadOnly(True)
                    ed.setMaximumHeight(120)
                    self.extra.addWidget(ed)
                    btn = QPushButton("Copy")
                    btn.clicked.connect(lambda _=False, v=v: QGuiApplication.clipboard().setText(v))
                    self.extra.addWidget(btn)
                else:
                    t = QLabel(v)
                    t.setWordWrap(True)
                    t.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                    self.extra.addWidget(t)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.path:
            self._set_preview()
