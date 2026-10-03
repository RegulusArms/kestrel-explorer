"""Models, delegates and widgets used by the main window."""
import fnmatch
import os

from PyQt6.QtCore import (QDir, QFileInfo, QRect, QRectF, QSize, QStorageInfo, Qt, QThread, QTimer,
                          pyqtSignal)
from PyQt6.QtGui import (QAbstractFileIconProvider, QColor, QFileSystemModel, QFont, QGuiApplication, QIcon, QPainter, QPainterPath, QPalette,
                         QStandardItem, QStandardItemModel)
from PyQt6.QtWidgets import (QAbstractItemView, QCompleter, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QPushButton, QScrollArea,
                             QSizePolicy, QStyle, QStyledItemDelegate, QToolButton, QVBoxLayout, QWidget)

from . import metadata, util

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
        if not (util.is_image(path) or util.is_video(path)):
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
            elif util.is_image(path) or util.is_video(path):
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

    def __init__(self, root, query, hidden, parent=None):
        super().__init__(parent)
        self.root, self.hidden = root, hidden
        q = query.lower()
        self.match = (lambda n: fnmatch.fnmatch(n.lower(), q)) if any(c in q for c in "*?[") else (lambda n: q in n.lower())
        self.stop = False

    def run(self):
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
        if pm is not None:
            spm = self.pane.thumbs.scaled(pm, s)
            dpr = spm.devicePixelRatio()
            w, h = spm.width() / dpr, spm.height() / dpr
            target = QRectF(icon_rect.x() + (s - w) / 2, icon_rect.y() + (s - h), w, h)
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
                if util.is_video(path):
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
        if obj is self.edit and ev.type() == ev.Type.FocusOut and not self.edit.completer().popup().isVisible():
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
        self._mounts = None
        self.refresh()
        self.timer = QTimer(self, interval=4000, timeout=self._check_mounts)
        self.timer.start()

    def _header(self, text):
        it = QListWidgetItem(text)
        it.setFlags(Qt.ItemFlag.NoItemFlags)
        f = it.font()
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() * 0.85)
        it.setFont(f)
        it.setForeground(self.palette().color(QPalette.ColorRole.PlaceholderText))
        self.addItem(it)

    def _add(self, label, path, icon, kind="place", extra=None):
        it = QListWidgetItem(icon if isinstance(icon, QIcon) else util.theme_icon(icon, "folder"), label)
        it.setData(PathRole, path)
        it.setData(Qt.ItemDataRole.UserRole, (kind, extra))
        it.setToolTip(path)
        self.addItem(it)

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
        from .overview import OVERVIEW, OVERVIEW_TITLE
        self._header("PLACES")
        self._add(OVERVIEW_TITLE, OVERVIEW, "computer", "overview")
        self._add("Home", util.HOME, "user-home")
        for key, label, icon in (("DESKTOP", "Desktop", "user-desktop"), ("DOCUMENTS", "Documents", "folder-documents"),
                                 ("DOWNLOAD", "Downloads", "folder-download"), ("MUSIC", "Music", "folder-music"),
                                 ("PICTURES", "Pictures", "folder-pictures"), ("VIDEOS", "Videos", "folder-videos")):
            p = util.xdg_user_dir(key)
            if os.path.isdir(p):
                self._add(label, p, icon)
        trash_files = str(util.TRASH_DIR / "files")
        empty = util.trash_is_empty()  # home trash and every drive's trash
        self._add("Trash", trash_files, "user-trash" if empty else "user-trash-full", "trash")
        bms = util.read_bookmarks()
        if bms:
            self._header("BOOKMARKS")
            for i, (path, label) in enumerate(bms):
                remote = not path.startswith("/") or path.startswith("/run/user/")
                self._add(label, path, "folder-remote" if remote else "folder", "bookmark", i)
        self._header("DEVICES")
        self._add("Computer", "/", "drive-harddisk", "root")
        self._mounts = self._mount_list()
        for name, root, dev, total in self._mounts:
            label = f"{name} ({util.human_size(total)})" if total else name
            icon = "drive-removable-media" if root.startswith(("/media/", "/run/media/")) else "drive-harddisk"
            self._add(label, root, icon, "mount", dev)
        if current:
            self.select_path(current)

    def select_path(self, path):
        self.blockSignals(True)
        self.setCurrentItem(None)
        for i in range(self.count()):
            if self.item(i).data(PathRole) == path:
                self.setCurrentRow(i)
                break
        self.blockSignals(False)

    def _clicked(self, it):
        p = it.data(PathRole)
        if p:
            mods = QGuiApplication.keyboardModifiers()
            self.open_path.emit(p, bool(mods & Qt.KeyboardModifier.ControlModifier))

    def mouseReleaseEvent(self, ev):
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
        if os.path.isdir(path):
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

    # drag & drop onto places
    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dragMoveEvent(self, ev):
        it = self.itemAt(ev.position().toPoint())
        if it and it.data(PathRole) and it.data(PathRole).startswith("/") and os.path.isdir(it.data(PathRole)):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dropEvent(self, ev):
        it = self.itemAt(ev.position().toPoint())
        if not it or not it.data(PathRole):
            return
        paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        target = it.data(PathRole)
        ev.acceptProposedAction()
        QTimer.singleShot(0, lambda: self.dropped.emit(paths, target))


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
        elif util.is_image(path) or util.is_video(path):
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
