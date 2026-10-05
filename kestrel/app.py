"""Main window and browser panes."""
import os
import shutil
import sys

from PyQt6 import sip
from PyQt6.QtCore import QDateTime, QDir, QEvent, QFileSystemWatcher, QItemSelectionModel, QMimeData, QSettings, QSize, QStorageInfo, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QCursor, QDrag, QGuiApplication, QKeySequence, QPainter, QPixmap, QWindow
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QHBoxLayout, QHeaderView,
                             QInputDialog, QLabel, QLineEdit, QListView, QMainWindow, QMenu, QMessageBox,
                             QProgressBar, QSlider, QSplitter, QStackedWidget, QStyle, QStyleOptionViewItem, QTabWidget,
                             QToolBar, QToolButton, QTreeView, QVBoxLayout, QWidget)

from . import (__version__, admin, animate, archive, archive_ui, atc, chooser, dialogs, env, fileops, fm1, focus,
               places,
               sharing, thumbs, undo, util, uwp)
from .chooser import ChooserBar
from .overview import OVERVIEW, OVERVIEW_TITLE, OverviewPage, is_phone_scheme, is_uri, mount_uri, phone_hint
from .viewer import ImageViewer
from .widgets import (FSModel, GridDelegate, InfoPanel, PathBar, PathRole, SearchModel, SearchThread, Sidebar,
                      can_search_contents)

SEL = QItemSelectionModel.SelectionFlag

GRID_MIN, GRID_MAX = 48, 320
LIST_MIN, LIST_MAX = 16, 128
GRID_DEFAULT, LIST_DEFAULT = 160, 28
CHOOSER_GRID, CHOOSER_LIST = 96, 24  # a chooser window starts with smaller icons
SORT_COLUMNS = ["Name", "Size", "Type", "Modified"]
WINDOWS = []


def icon(*names):
    return util.theme_icon(*names)


# ---------------------------------------------------------------- pane

class FileViewDrag:
    """The file views. Their drags are Qt's, apart from giving the focus to the app the files are dropped into
    (focus.py)."""

    def startDrag(self, supported):
        indexes = [i for i in self.selectedIndexes() if self.model().flags(i) & Qt.ItemFlag.ItemIsDragEnabled]
        data = self.model().mimeData(indexes) if indexes else None
        if data is None:
            return
        drag = QDrag(self)
        drag.setMimeData(data)
        pixmap, hot = self._drag_pixmap(indexes)
        drag.setPixmap(pixmap)
        drag.setHotSpot(hot)
        default = self.defaultDropAction()
        if default == Qt.DropAction.IgnoreAction or not (supported & default):
            default = Qt.DropAction.CopyAction if supported & Qt.DropAction.CopyAction else Qt.DropAction.IgnoreAction
        if drag.exec(supported, default) != Qt.DropAction.IgnoreAction and drag.target() is None:  # into another app
            focus.activate_at_pointer()

    def _drag_pixmap(self, indexes):
        """The dragged items as they look in the view (what Qt draws)."""
        vp = self.viewport().rect()
        rects = [(self.visualRect(i), i) for i in indexes if self.visualRect(i).intersects(vp)]
        full = rects[0][0].intersected(vp) if rects else None
        for r, _ in rects[1:]:
            full = full.united(r.intersected(vp))
        if full is None:
            return QPixmap(), self.viewport().mapFromGlobal(QCursor.pos())
        hot = self.viewport().mapFromGlobal(QCursor.pos()) - full.topLeft()
        dpr = self.devicePixelRatioF()
        pm = QPixmap(full.size() * dpr)
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        for r, i in rects:
            opt = QStyleOptionViewItem()
            self.initViewItemOption(opt)
            opt.rect = r.translated(-full.topLeft())
            opt.state |= QStyle.StateFlag.State_Selected
            self.itemDelegateForIndex(i).paint(p, opt, i)
        p.end()
        return pm, hot


class FileListView(FileViewDrag, QListView):
    pass


class FileTreeView(FileViewDrag, QTreeView):
    pass


class Pane(QWidget):
    path_changed = pyqtSignal()
    selection_changed = pyqtSignal()

    def __init__(self, win, path):
        super().__init__()
        self.win = win
        self.thumbs = win.thumbs
        self.settings = win.settings
        self.back_stack, self.fwd_stack = [], []
        self.path = None
        self.in_search = False
        self.search_thread = None
        self.type_filters = []  # chooser: the chosen file type's globs
        self._search_recorded = False  # the folder as it was before the active search is on back_stack
        self._pending_select = None
        self._trash_gen = 0          # bumps on every combined-trash reload, so stale loads are dropped
        self._trash_watch = None     # QFileSystemWatcher on every trash files/ folder while showing the trash
        places.signals.starred_changed.connect(self._starred_changed)
        self.grid_size = int(win.view_value("grid_size"))
        self.list_size = int(win.view_value("list_size"))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        # search bar
        self.search_bar = QWidget()
        sl = QHBoxLayout(self.search_bar)
        sl.setContentsMargins(6, 4, 6, 4)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search… (supports * and ? wildcards)")
        self.search_edit.setClearButtonEnabled(True)
        self.search_sub = QCheckBox("Include subfolders")
        self.search_sub.setChecked(win.view_value("search_recursive", False, type=bool))
        close = QToolButton()
        close.setIcon(icon("window-close-symbolic", "window-close"))
        close.setAutoRaise(True)
        close.clicked.connect(lambda: self.close_search())
        self.search_contents = QCheckBox("File contents")
        self.search_contents.setToolTip("Search inside files too, using the desktop's search index (localsearch).\n"
                                        "Includes subfolders; only finds files in indexed folders.")
        self.search_contents.setChecked(win.view_value("search_contents", False, type=bool))
        self.search_contents.toggled.connect(lambda v: (self.win.set_view_value("search_contents", v),
                                                        self._do_search()))
        sl.addWidget(self.search_edit, 1)
        sl.addWidget(self.search_sub)
        sl.addWidget(self.search_contents)
        sl.addWidget(close)
        # only once it has a parent: showing a parentless widget opens it as a window of its own, which on Wayland
        # uses up the launch's activation token and leaves GNOME's busy cursor spinning until it times out
        self.search_contents.setVisible(can_search_contents())
        self.search_bar.hide()
        self.search_timer = QTimer(self, singleShot=True, interval=250, timeout=self._do_search)
        self.search_edit.textChanged.connect(lambda: self.search_timer.start())
        self.search_sub.toggled.connect(lambda v: (self.win.set_view_value("search_recursive", v), self._do_search()))
        self.search_edit.installEventFilter(self)
        lay.addWidget(self.search_bar)

        self.stack = QStackedWidget()
        self.grid = FileListView()
        self.animator = animate.Animator(self.grid, self.settings, self)   # GIF / WebM playing in the grid
        self.tree = FileTreeView()
        self._setup_grid()
        self._setup_tree()
        self.stack.addWidget(self.grid)
        self.stack.addWidget(self.tree)
        self.mode_view = self.grid  # grid or tree; stays set while the overview page is shown
        self.overview = OverviewPage(win)
        self.stack.addWidget(self.overview)
        lay.addWidget(self.stack)
        self.empty = QLabel("Folder is empty", self.stack)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet("color: palette(placeholder-text); font-size: 16px; background: transparent")
        self.empty.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.empty.hide()

        self.model = self._make_model()
        self.search_model = SearchModel(self.thumbs, self)
        self._attach(self.model)
        self.set_view_mode(win.view_value("view_mode", "grid"))
        if win.chooser is not None:
            self.set_type_filter(win.chooser.type_filter())
        self.set_path(path)

    def set_type_filter(self, globs):
        """Chooser: show folders and only these files."""
        self.type_filters = list(globs)
        self.model.setFilter(self._filters())
        if not self.in_search and not self.search_bar.isVisible():
            self.model.setNameFilters(self.type_filters)

    # -- setup
    def _setup_common(self, v):
        v.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        v.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        v.setDragEnabled(True)
        v.setAcceptDrops(True)
        v.setDropIndicatorShown(True)
        v.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        v.setDefaultDropAction(Qt.DropAction.MoveAction)
        v.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        v.customContextMenuRequested.connect(lambda pos, v=v: self.win.context_menu(self, v, pos))
        v.doubleClicked.connect(self._double_clicked)
        v.clicked.connect(self._clicked)
        v.installEventFilter(self)
        v.viewport().installEventFilter(self)
        v.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        v.verticalScrollBar().setSingleStep(40)

    def _setup_grid(self):
        g = self.grid
        g.setViewMode(QListView.ViewMode.IconMode)
        g.setMovement(QListView.Movement.Static)
        g.setResizeMode(QListView.ResizeMode.Adjust)
        g.setWrapping(True)
        g.setUniformItemSizes(True)
        g.setLayoutMode(QListView.LayoutMode.Batched)
        g.setBatchSize(400)
        g.setMouseTracking(True)
        g.setFrameShape(QListView.Shape.NoFrame)
        self.delegate = GridDelegate(self, g)
        self.delegate.icon_size = self.grid_size
        g.setItemDelegate(self.delegate)
        self._setup_common(g)

    def _setup_tree(self):
        t = self.tree
        t.setRootIsDecorated(False)
        t.setItemsExpandable(False)
        t.setUniformRowHeights(True)
        t.setAlternatingRowColors(True)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSortingEnabled(True)
        t.setFrameShape(QTreeView.Shape.NoFrame)
        t.setIconSize(QSize(self.list_size, self.list_size))
        col = int(self.win.view_value("sort_col", 0))
        order = Qt.SortOrder(int(self.win.view_value("sort_order", 0)))
        t.header().setSortIndicator(col, order)
        t.header().sortIndicatorChanged.connect(self._sort_changed)
        self._setup_common(t)

    def _filters(self):
        f = QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot | QDir.Filter.System
        if self.win.show_hidden:
            f |= QDir.Filter.Hidden
        if self.type_filters:
            f |= QDir.Filter.AllDirs  # a chooser's file types don't hide folders (name filters would)
        return f

    def _make_model(self):
        m = FSModel(self.thumbs, self)
        m.drop_handler = self.win.handle_drop
        m.setFilter(self._filters())
        m.setNameFilterDisables(False)
        m.directoryLoaded.connect(self._dir_loaded)
        m.rowsInserted.connect(self._update_empty)
        m.rowsRemoved.connect(self._update_empty)
        m.thumb_size = self.grid_size
        return m

    def _attach(self, model):
        for v in (self.grid, self.tree):
            v.setModel(model)
        self.grid.setSelectionModel(self.tree.selectionModel())
        self.tree.selectionModel().selectionChanged.connect(lambda *_: self.selection_changed.emit())
        hdr = self.tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hdr.resizeSection(0, 380)
        hdr.setStretchLastSection(True)
        self._apply_folder_previews()

    # -- view mode / zoom
    def is_grid(self):
        return self.mode_view is self.grid

    def view(self):
        return self.mode_view

    def is_overview(self):
        return self.path == OVERVIEW

    def is_trash(self):
        """Showing the Trash: the combined contents of the home trash and every drive's trash."""
        return self.path == str(util.TRASH_DIR / "files")

    def is_virtual(self):
        """Showing Starred or Recent: files from anywhere, not a folder."""
        return self.path in places.VIRTUAL

    def is_listing(self):
        """Showing a list of files from many folders (Trash, Starred, Recent) in the results model."""
        return self.is_trash() or self.is_virtual()

    @property
    def dir(self):
        """The current folder, or None on the overview page and Starred / Recent."""
        return None if self.is_overview() or self.is_virtual() else self.path

    def set_view_mode(self, mode):
        self.mode_view = self.grid if mode == "grid" else self.tree
        if not self.is_overview():
            self.stack.setCurrentWidget(self.mode_view)
        self._apply_folder_previews()
        self._update_grid_size()

    def _apply_folder_previews(self):
        grid = self.mode_view is self.grid
        on = self.win.folder_previews and (grid or self.settings.value("list_folder_previews", False, type=bool))
        size = self.grid_size if grid else max(self.list_size, 96)
        for m in (getattr(self, "model", None), getattr(self, "search_model", None)):
            if m is not None:
                m.folder_previews = on
                m.thumb_size = size

    def zoom(self, step=None, absolute=None):
        if self.is_grid():
            cur = self.grid_size
            new = absolute if absolute is not None else int(cur * (1.15 if step > 0 else 1 / 1.15))
            self.grid_size = max(GRID_MIN, min(GRID_MAX, new))
            self.delegate.icon_size = self.grid_size
            self.win.set_view_value("grid_size", self.grid_size)
            self._update_grid_size()
        else:
            cur = self.list_size
            new = absolute if absolute is not None else cur + (8 if step > 0 else -8)
            self.list_size = max(LIST_MIN, min(LIST_MAX, new))
            self.tree.setIconSize(QSize(self.list_size, self.list_size))
            self.win.set_view_value("list_size", self.list_size)
        self._apply_folder_previews()
        self.view().viewport().update()
        self.win.sync_zoom_slider()

    def zoom_value(self):
        return self.grid_size if self.is_grid() else self.list_size

    def _update_grid_size(self):
        cell = self.delegate.cell_size()
        # Width as if the scrollbar were always shown: otherwise the scrollbar
        # appearing/disappearing changes the column width and the grid re-flows.
        sb = 0 if self.grid.verticalScrollBar().isVisible() else \
            self.grid.style().pixelMetric(self.grid.style().PixelMetric.PM_ScrollBarExtent)
        avail = self.grid.viewport().width() - sb
        cols = max(1, avail // cell.width())
        extra = (avail - cols * cell.width()) // cols if cols > 1 else 0
        size = QSize(cell.width() + max(0, extra), cell.height())
        if size != self.grid.gridSize():
            self.grid.setGridSize(size)

    # -- navigation
    def set_path(self, path, record=True, select=None):
        if path == OVERVIEW:
            if record and self.path and self.path != OVERVIEW:
                self.back_stack.append(self._snapshot(OVERVIEW))
                self.fwd_stack.clear()
            if self.in_search or self.search_bar.isVisible():
                self.close_search(refocus=False, navigating=True)
            self.path = OVERVIEW
            self.view().clearSelection()
            self.stack.setCurrentWidget(self.overview)
            self.empty.hide()
            self.path_changed.emit()
            return True
        if path in places.VIRTUAL:
            if record and self.path and self.path != path:
                self.back_stack.append(self._snapshot(path))
                self.fwd_stack.clear()
            if self.in_search or self.search_bar.isVisible():
                self.close_search(refocus=False, navigating=True)
            self.path = path
            self.stack.setCurrentWidget(self.mode_view)
            self.view().selectionModel().clear()
            self._pending_select = select
            self.path_changed.emit()
            self.show_trash()
            return True
        path = os.path.abspath(os.path.expanduser(path))
        if os.path.isfile(path):
            select, path = path, os.path.dirname(path)
        if not os.path.isdir(path):
            QMessageBox.warning(self, "Not found", f"“{path}” does not exist.")
            return False
        if not os.access(path, os.R_OK | os.X_OK):
            QMessageBox.warning(self, "Permission denied", f"You don't have permission to open “{path}”.")
            return False
        prev = self.path
        searching = self.search_bar.isVisible() and self.search_edit.text().strip()
        if record and prev and (prev != path or searching):
            self.back_stack.append(self._snapshot(path))
            self.fwd_stack.clear()
        if self.in_search or self.search_bar.isVisible():
            self.close_search(refocus=False, navigating=True)
        self.path = path
        self.stack.setCurrentWidget(self.mode_view)
        self.thumbs.cancel_pending()
        self.animator.clear()
        self.model.setNameFilters(self.type_filters)
        root = self.model.setRootPath(path)
        self.grid.setRootIndex(root)
        self.tree.setRootIndex(root)
        self.view().selectionModel().clear()  # drop the previous folder's selection and current item
        self._pending_select = select or (prev if prev and os.path.dirname(prev) == path else None)
        QTimer.singleShot(0, self._try_select)
        self.grid.scrollToTop()
        self.tree.scrollToTop()
        self._update_empty()
        self.path_changed.emit()
        if self.is_trash():
            self.show_trash()
        return True

    # -- combined trash, Starred and Recent
    def show_trash(self):
        """List the items of every trash folder (home + each drive's .Trash-$uid) in the results model,
        with the folder each item was deleted from as its Location; or the Starred / Recent files with their
        folder. Loaded on a thread."""
        self._trash_gen += 1
        gen = self._trash_gen
        fn = util.trashed_items if self.is_trash() else (lambda place=self.path: places.items(place))
        fileops.run_task(self, "", fn, lambda items: self._fill_trash(items, gen), quiet=True)

    def _starred_changed(self):
        if self.path == places.STARRED:
            self.show_trash()
        self.view().viewport().update()

    def _fill_trash(self, items, gen):
        if gen != self._trash_gen or not self.is_listing():
            return
        text = self.search_edit.text().strip().lower() if self.search_bar.isVisible() else ""
        if text:
            items = [(p, o) for p, o in items if text in os.path.basename(p).lower()]
        if not self.in_search:
            self.in_search = True
            self._attach(self.search_model)
            self.grid.setRootIndex(self.search_model.index(-1, -1))
            self.tree.setRootIndex(self.search_model.index(-1, -1))
        sel = set(self.selected_paths())
        self.search_model.clear_results()
        locations = {p: os.path.dirname(o) if o else "(unknown)" for p, o in items}
        self._add_trash_rows([p for p, _ in items], locations, gen, sel)
        if not self.is_trash():
            return
        if self._trash_watch is None:
            self._trash_watch = QFileSystemWatcher(self)
            self._trash_watch.directoryChanged.connect(self._trash_changed)
        dirs = [os.path.join(r, "files") for r in util.trash_dirs()]
        new = [d for d in dirs if d not in self._trash_watch.directories()]
        if new:
            self._trash_watch.addPaths(new)

    def _add_trash_rows(self, paths, locations, gen, sel):
        # in chunks, so a trash with tens of thousands of items doesn't block the window
        if gen != self._trash_gen or not self.is_listing():
            return
        self.search_model.add_paths(paths[:1000], locations)
        if paths[1000:]:
            QTimer.singleShot(0, lambda: self._add_trash_rows(paths[1000:], locations, gen, sel))
            return
        if sel:
            self.select_paths([p for p in sel if p in self.search_model.rows])
        self._try_select()
        self._update_empty()
        if self.win.pane() is self:
            self.win.update_status()

    def _trash_changed(self, _d):
        if self.is_trash() and not getattr(self, "_trash_reload", False):
            self._trash_reload = True  # coalesce bursts (deleting thousands of items) into one reload

            def reload():
                self._trash_reload = False
                if self.is_trash():
                    self.show_trash()
            QTimer.singleShot(400, reload)

    def _dir_loaded(self, p):
        if p == self.path:
            self._update_empty()
            if self.win.pane() is self:
                self.win.update_status()
            QTimer.singleShot(30, self._try_select)

    def _try_select(self):
        if not self._pending_select or self.is_overview():
            return
        idx = self._index_for(self._pending_select)
        if idx is not None and idx.isValid():
            self.select_paths([self._pending_select])
            self._pending_select = None

    def select_later(self, path):
        """Select path once it shows up in the model (after create/rename/paste)."""
        self._pending_select = path
        for ms in (60, 300, 1000, 2500):
            QTimer.singleShot(ms, self._try_select)

    def select_paths(self, paths):
        if self.is_overview():
            return
        sm = self.view().selectionModel()
        sm.clearSelection()
        first = None
        for p in paths:
            idx = self._index_for(p)
            if idx is not None and idx.isValid():
                sm.select(idx, SEL.Select | SEL.Rows)
                first = first or idx
        if first is not None:
            sm.setCurrentIndex(first, SEL.NoUpdate)
            self.view().scrollTo(first)
            # large folders are laid out in batches, so the item may not have its final position yet: scroll again
            # once layout has caught up (unless the user has moved on to another item)
            target = first.siblingAtColumn(0).data(PathRole)
            for ms in (50, 250, 700):
                QTimer.singleShot(ms, lambda t=target: self._scroll_to_current(t))

    def _scroll_to_current(self, path):
        idx = self._index_for(path)
        cur = self.view().currentIndex()
        if idx is not None and idx.isValid() and cur.isValid() and cur.siblingAtColumn(0) == idx.siblingAtColumn(0):
            self.view().scrollTo(idx)

    def _index_for(self, p):
        if self.in_search:
            item = self.search_model.rows.get(p)
            return item.index() if item else None
        return self.model.index(p)

    def _snapshot(self, dest=None, search=True):
        """History entry for the current location: the item to refocus on returning (the folder that
        leads to dest, else the current item) and, if search, the active search."""
        if self.is_overview():
            return {"path": OVERVIEW}
        focus = None
        if dest and dest != OVERVIEW:
            if self.in_search:
                focus = dest if dest in self.search_model.rows else None
            elif dest.startswith(self.path.rstrip("/") + "/"):
                focus = os.path.join(self.path, os.path.relpath(dest, self.path).split(os.sep)[0])
        if not focus:
            cur = self.current_path()  # the view's current index can be left over from a previous folder
            if cur and (cur in self.search_model.rows if self.in_search else os.path.dirname(cur) == self.path):
                focus = cur
        text = self.search_edit.text().strip() if search and self.search_bar.isVisible() else ""
        return {"path": self.path, "focus": focus,
                "search": (text, self.search_sub.isChecked()) if text else None}

    def _restore(self, entry):
        if not self.set_path(entry["path"], record=False, select=entry.get("focus")):
            return
        if entry.get("search"):
            text, recursive = entry["search"]
            self.search_sub.blockSignals(True)
            self.search_sub.setChecked(recursive)
            self.search_sub.blockSignals(False)
            self.search_edit.blockSignals(True)
            self.search_edit.setText(text)
            self.search_edit.blockSignals(False)
            self.search_bar.show()
            self._search_recorded = True  # its folder is already in the history
            self._do_search()
            self.path_changed.emit()

    def go_back(self):
        if self.back_stack:
            entry = self.back_stack.pop()
            self.fwd_stack.append(self._snapshot(entry["path"]))
            self._restore(entry)

    def go_forward(self):
        if self.fwd_stack:
            entry = self.fwd_stack.pop()
            self.back_stack.append(self._snapshot(entry["path"]))
            self._restore(entry)

    def go_up(self):
        if self.is_overview() or self.is_virtual():
            return
        parent = os.path.dirname(self.path)
        if parent != self.path:
            self.set_path(parent, select=self.path)

    def refresh(self):
        if self.is_overview():
            self.overview.refresh()
            return
        if self.is_listing():
            self.show_trash()
            return
        for p in self.all_paths():
            if os.path.isdir(p):
                self.thumbs.invalidate(p)
        sel = self.selected_paths()
        self.model.deleteLater()
        self.model = self._make_model()
        if not self.in_search:
            self._attach(self.model)
            root = self.model.setRootPath(self.path)
            self.grid.setRootIndex(root)
            self.tree.setRootIndex(root)
            self._pending_select = sel[0] if sel else None
        self._update_grid_size()

    def apply_hidden(self):
        self.model.setFilter(self._filters())

    def _update_empty(self, *a):
        if self.is_overview():
            self.empty.hide()
            return
        if self.in_search and self.is_listing():
            searching = self.search_bar.isVisible() and self.search_edit.text().strip()
            empty = "Trash is empty" if self.is_trash() else places.EMPTY_TEXT[self.path]
            text = "" if self.search_model.rowCount() else ("No matches" if searching else empty)
        elif self.in_search:
            n = self.search_model.rowCount()
            text = "No results" if n == 0 and not (self.search_thread and self.search_thread.isRunning()) else ""
        else:
            n = self.model.rowCount(self.model.index(self.path)) if self.path else 0
            text = "Folder is empty" if n == 0 else ""
        self.empty.setText(text)
        self.empty.setVisible(bool(text))
        self.empty.setGeometry(self.stack.rect())

    # -- search
    def start_search(self):
        if self.is_overview():
            return
        self.search_bar.show()
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def close_search(self, refocus=True, navigating=False):
        if not navigating:
            self._unrecord_search()  # a cancelled search leaves no step in the history
        self._search_recorded = False
        self.search_edit.blockSignals(True)
        self.search_edit.clear()
        self.search_edit.blockSignals(False)
        self.search_bar.hide()
        self._stop_search()
        self.model.setNameFilters(self.type_filters)
        if self.in_search:
            self.in_search = False
            self._attach(self.model)
            root = self.model.index(self.path)
            self.grid.setRootIndex(root)
            self.tree.setRootIndex(root)
        self._update_empty()
        if refocus:
            self.view().setFocus()
        self.path_changed.emit()
        if self.is_listing():
            self.show_trash()

    def _stop_search(self):
        if self.search_thread:
            self.search_thread.stop = True
            self.search_thread.wait(2000)
            self.search_thread = None

    def _record_search(self):
        """A search is its own step in the history: Back from it returns to the plain folder."""
        if not self._search_recorded:
            self._search_recorded = True
            self.back_stack.append(self._snapshot(search=False))
            self.fwd_stack.clear()
            self.path_changed.emit()

    def _unrecord_search(self):
        if self._search_recorded:
            self._search_recorded = False
            top = self.back_stack[-1] if self.back_stack else None
            if top and top["path"] == self.path and not top.get("search"):
                self.back_stack.pop()
            self.path_changed.emit()

    def _do_search(self):
        text = self.search_edit.text().strip()
        self._stop_search()
        if text:
            self._record_search()
        else:
            self._unrecord_search()
        if self.is_listing():  # filter the combined trash / Starred / Recent list by name
            self.show_trash()
            return
        if not text:
            self.model.setNameFilters(self.type_filters)
            if self.in_search:
                self.in_search = False
                self._attach(self.model)
                root = self.model.index(self.path)
                self.grid.setRootIndex(root)
                self.tree.setRootIndex(root)
            self._update_empty()
            return
        contents = can_search_contents() and self.search_contents.isChecked()
        if self.search_sub.isChecked() or contents:
            if not self.in_search:
                self.in_search = True
                self._attach(self.search_model)
                self.grid.setRootIndex(self.search_model.index(-1, -1))
                self.tree.setRootIndex(self.search_model.index(-1, -1))
            self.search_model.clear_results()
            t = SearchThread(self.path, text, self.win.show_hidden, self, contents=contents)
            t.found.connect(self.search_model.add_paths)
            t.found.connect(lambda *_: self._try_select())
            t.found.connect(self._update_empty)
            t.finished.connect(self._update_empty)
            t.finished.connect(lambda: self.win.update_status())
            self.search_thread = t
            t.start()
        else:
            if self.in_search:
                self.in_search = False
                self._attach(self.model)
                root = self.model.index(self.path)
                self.grid.setRootIndex(root)
                self.tree.setRootIndex(root)
            pattern = text if any(c in text for c in "*?[") else f"*{text}*"
            self.model.setNameFilters([pattern])
        self._update_empty()
        self.win.update_status()

    # -- selection helpers
    def selected_paths(self):
        if self.is_overview():
            return []
        sm = self.view().selectionModel()
        if sm is None:
            return []
        idxs = sorted({(i.row(), i.parent()) for i in sm.selectedIndexes()}, key=lambda t: t[0])
        model = self.view().model()
        out = []
        for row, parent in idxs:
            p = model.index(row, 0, parent).data(PathRole)
            if p:
                out.append(p)
        return out

    def all_paths(self):
        if self.is_overview():
            return []
        if self.in_search:
            return [self.search_model.item(r, 0).data(PathRole) for r in range(self.search_model.rowCount())]
        root = self.model.index(self.path)
        return [self.model.filePath(self.model.index(r, 0, root)) for r in range(self.model.rowCount(root))]

    def current_path(self):
        idx = self.view().currentIndex()
        return idx.siblingAtColumn(0).data(PathRole) if idx.isValid() else None

    def invert_selection(self):
        sel = set(self.selected_paths())
        self.select_paths([p for p in self.all_paths() if p not in sel])

    def _sort_changed(self, col, order):
        self.win.set_view_value("sort_col", col)
        self.win.set_view_value("sort_order", order.value)

    def sort_by(self, col, order=None):
        if order is None:
            order = self.tree.header().sortIndicatorOrder()
        self.tree.header().setSortIndicator(col, order)
        self.tree.model().sort(col, order)

    # -- activation
    def _clicked(self, idx):
        if self.settings.value("single_click", False, type=bool) and not QGuiApplication.keyboardModifiers():
            self.win.open_paths(self, [idx.siblingAtColumn(0).data(PathRole)])

    def _double_clicked(self, idx):
        if not self.settings.value("single_click", False, type=bool):
            self.win.open_paths(self, [idx.siblingAtColumn(0).data(PathRole)])

    def eventFilter(self, obj, ev):
        t = ev.type()
        if obj is self.search_edit and t == QEvent.Type.KeyPress:
            if ev.key() == Qt.Key.Key_Escape:
                self.close_search()
                return True
            if ev.key() in (Qt.Key.Key_Down, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.view().setFocus()
                if self.view().model().rowCount(self.view().rootIndex()):
                    first = self.view().model().index(0, 0, self.view().rootIndex())
                    if not self.view().currentIndex().isValid():
                        self.view().setCurrentIndex(first)
                return True
            return False
        if obj in (self.grid, self.tree) and t == QEvent.Type.KeyPress:
            k, mods = ev.key(), ev.modifiers()
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (mods & Qt.KeyboardModifier.AltModifier):
                paths = self.selected_paths()
                if paths:
                    self.win.open_paths(self, paths, new_tab=bool(mods & Qt.KeyboardModifier.ControlModifier))
                return True
            if k == Qt.Key.Key_Space and not mods:
                paths = self.selected_paths()
                if paths:
                    self.win.quick_view(self, paths)
                return True
            if k == Qt.Key.Key_Escape and self.search_bar.isVisible():
                self.close_search()
                return True
        if obj in (self.grid.viewport(), self.tree.viewport()):
            if t == QEvent.Type.Wheel and ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.zoom(1 if ev.angleDelta().y() > 0 else -1)
                return True
            if t == QEvent.Type.MouseButtonPress:
                b = ev.button()
                if b == Qt.MouseButton.BackButton:
                    self.go_back()
                    return True
                if b == Qt.MouseButton.ForwardButton:
                    self.go_forward()
                    return True
            if t == QEvent.Type.MouseButtonRelease and ev.button() == Qt.MouseButton.MiddleButton:
                idx = self.view().indexAt(ev.position().toPoint())
                p = idx.siblingAtColumn(0).data(PathRole) if idx.isValid() else None
                if p and os.path.isdir(p):
                    self.win.new_tab(p, activate=False)
                    return True
            if t == QEvent.Type.Resize:
                if obj is self.grid.viewport():
                    QTimer.singleShot(0, self._update_grid_size)
                self.empty.setGeometry(self.stack.rect())
        return super().eventFilter(obj, ev)

    @property
    def cut_paths(self):
        return self.win.cut_paths

    def title(self):
        if self.is_overview():
            return OVERVIEW_TITLE
        if self.is_listing() and not (self.search_bar.isVisible() and self.search_edit.text().strip()):
            return "Trash" if self.is_trash() else places.title(self.path)
        if self.in_search:
            return f"Search: {self.search_edit.text()}"
        return os.path.basename(self.path.rstrip("/")) or "/"


# ---------------------------------------------------------------- main window

class MainWindow(QMainWindow):
    def __init__(self, paths, thumb_mgr, settings, chooser_mode=False):
        super().__init__()
        self.settings = settings
        self.thumbs = thumb_mgr
        self.chooser = None  # a file chooser window (see chooser.py)
        self.chooser_mode = chooser_mode  # a chooser window: has its own view settings (view_value)
        self.cut_paths = set()
        self.show_hidden = self.view_value("show_hidden", False, type=bool)
        self.folder_previews = self.view_value("folder_previews", True, type=bool)
        self.viewers = []
        self._prefs = None   # the open Preferences window
        self.setWindowTitle(util.APP_NAME)
        self.setWindowIcon(icon("folder"))
        if chooser_mode:
            self.resize(960, 620)  # a dialog: smaller than a main window
        else:
            self.resize(1280, 820)

        self._build_toolbar()
        self.sidebar = Sidebar()
        self.sidebar.open_path.connect(lambda p, new: self.open_location(p, new_tab=new))
        self.sidebar.dropped.connect(self.handle_drop)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setTabBarAutoHide(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.info = InfoPanel(self.thumbs)
        self.info.folder_previews = self.folder_previews
        self.split = QSplitter()
        self.split.addWidget(self.sidebar)
        self.split.addWidget(self.tabs)
        self.split.addWidget(self.info)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([220, 900, 300])
        self.split.setCollapsible(1, False)
        self.setCentralWidget(self.split)
        self.sidebar.setVisible(self.settings.value("sidebar", True, type=bool))
        self.info.setVisible(self.settings.value("info_panel", False, type=bool))

        self.status_label = QLabel()
        self.free_label = QLabel()
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setFixedWidth(140)
        self.zoom_slider.setToolTip("Zoom (Ctrl+scroll)")
        self.zoom_slider.valueChanged.connect(lambda v: self.pane() and self.pane().zoom_value() != v and self.pane().zoom(absolute=v))
        self.thumb_progress = QProgressBar()
        self.thumb_progress.setFixedWidth(220)
        self.thumb_progress.setMaximumHeight(16)
        self.thumb_progress.setFormat("Thumbnails %v / %m")
        self.thumb_progress.setToolTip("Generating thumbnails and folder previews in the background")
        keep = self.thumb_progress.sizePolicy()
        keep.setRetainSizeWhenHidden(True)  # showing/hiding must not shift the layout
        self.thumb_progress.setSizePolicy(keep)
        self.thumb_progress.hide()
        self.thumbs.progress.connect(self._thumb_progress)
        self.builder = None
        self.build_box = QWidget()
        bl = QHBoxLayout(self.build_box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(4)
        self.build_label = QLabel()
        self.build_label.setMaximumWidth(260)
        self.build_bar = QProgressBar()
        self.build_bar.setFixedWidth(200)
        self.build_bar.setMaximumHeight(16)
        self.build_stop = QToolButton()
        self.build_stop.setText("✕")
        self.build_stop.setAutoRaise(True)
        self.build_stop.setToolTip("Stop building previews")
        self.build_stop.clicked.connect(self.stop_build)
        for wdg in (self.build_label, self.build_bar, self.build_stop):
            bl.addWidget(wdg)
        self.build_box.hide()
        self.task_panel = fileops.TaskPanel()  # file operations running in the background
        self.admin_indicator = admin.Indicator()  # 🛡 while an admin session is open
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.admin_indicator)
        self.statusBar().addPermanentWidget(self.task_panel)
        self.statusBar().addPermanentWidget(self.build_box)
        self.statusBar().addPermanentWidget(self.thumb_progress)
        self.statusBar().addPermanentWidget(self.free_label)
        self.statusBar().addPermanentWidget(self.zoom_slider)

        self._build_actions()
        geo = self.view_value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        st = self.view_value("splitter")
        if st is not None:
            self.split.restoreState(st)
        for p in paths or [self.homepage()]:
            self.open_location(p, new_tab=True)
        if not self.tabs.count():  # e.g. homepage was an unreachable network share
            self.new_tab(OVERVIEW)

    # -- toolbar & actions
    def _build_toolbar(self):
        tb = QToolBar()
        tb.setMovable(False)
        tb.setIconSize(QSize(18, 18))
        self.addToolBar(tb)
        self.toolbar = tb
        self.a_back = tb.addAction(icon("go-previous-symbolic", "go-previous"), "Back", lambda: self.pane().go_back())
        self.a_fwd = tb.addAction(icon("go-next-symbolic", "go-next"), "Forward", lambda: self.pane().go_forward())
        self.a_up = tb.addAction(icon("go-up-symbolic", "go-up"), "Parent Folder", lambda: self.pane().go_up())
        self.a_home = tb.addAction(icon("go-home-symbolic", "go-home"), "Homepage (Alt+Home)", self.go_home)
        self.pathbar = PathBar()
        self.pathbar.navigate.connect(self.navigate)
        tb.addWidget(self.pathbar)
        self.preview_box = QCheckBox("Folder previews")
        self.preview_box.setToolTip("Show image mosaics on folder icons (Ctrl+Shift+P).\n"
                                    "Turn off in large or slow folders to speed things up.")
        self.preview_box.setChecked(self.folder_previews)
        self.preview_box.toggled.connect(self.set_folder_previews)
        tb.addWidget(self.preview_box)
        self.a_search = tb.addAction(icon("system-search-symbolic", "edit-find"), "Search (Ctrl+F)",
                                     lambda: self.pane().start_search())
        self.view_btn = QToolButton()
        self.view_btn.setAutoRaise(True)
        self.view_btn.clicked.connect(self.toggle_view)
        tb.addWidget(self.view_btn)
        self.sort_btn = QToolButton()
        self.sort_btn.setAutoRaise(True)
        self.sort_btn.setIcon(icon("view-sort-ascending-symbolic", "view-sort-ascending"))
        self.sort_btn.setToolTip("Sort")
        self.sort_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.sort_menu = QMenu(self)
        self.sort_menu.aboutToShow.connect(self._fill_sort_menu)
        self.sort_btn.setMenu(self.sort_menu)
        tb.addWidget(self.sort_btn)
        self.menu_btn = QToolButton()
        self.menu_btn.setAutoRaise(True)
        self.menu_btn.setIcon(icon("open-menu-symbolic", "application-menu", "preferences-system"))
        self.menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        tb.addWidget(self.menu_btn)

    def _fill_sort_menu(self):
        m = self.sort_menu
        m.clear()
        p = self.pane()
        col = p.tree.header().sortIndicatorSection()
        order = p.tree.header().sortIndicatorOrder()
        for i, name in enumerate(SORT_COLUMNS):
            a = m.addAction(name, lambda i=i: p.sort_by(i))
            a.setCheckable(True)
            a.setChecked(i == col)
        m.addSeparator()
        for o, name in ((Qt.SortOrder.AscendingOrder, "Ascending"), (Qt.SortOrder.DescendingOrder, "Descending")):
            a = m.addAction(name, lambda o=o: p.sort_by(p.tree.header().sortIndicatorSection(), o))
            a.setCheckable(True)
            a.setChecked(o == order)

    def _act(self, text, keys, fn, icon_names=None, checkable=False, menu=None):
        a = QAction(text, self)
        if keys:
            a.setShortcuts([QKeySequence(k) for k in (keys if isinstance(keys, list) else [keys])])
        if icon_names:
            a.setIcon(icon(*icon_names))
        a.setCheckable(checkable)
        a.triggered.connect(fn)
        self.addAction(a)
        if menu is not None:
            menu.addAction(a)
        return a

    def _build_actions(self):
        menu = QMenu(self)
        self.menu_btn.setMenu(menu)
        A = self._act
        fm = menu.addMenu("File")
        A("New Window", "Ctrl+N", lambda: open_window([self.pane().path]), menu=fm)
        A("New Tab", "Ctrl+T", lambda: self.new_tab(self.pane().path), menu=fm)
        A("Close Tab", "Ctrl+W", lambda: self.close_tab(self.tabs.currentIndex()), menu=fm)
        fm.addSeparator()
        A("New Folder…", "Ctrl+Shift+N", self.new_folder, ["folder-new"], menu=fm)
        A("New Empty File…", "Ctrl+Alt+N", lambda: self.new_file(), ["document-new"], menu=fm)
        A("Open Terminal Here", "Ctrl+Alt+T", lambda: self.cur_dir() and util.open_terminal(self.cur_dir()), ["utilities-terminal"], menu=fm)
        fm.addSeparator()
        A("Bookmark This Location", "Ctrl+D", lambda: self.cur_dir() and self.sidebar.add_bookmark(self.cur_dir()), ["bookmark-new"], menu=fm)
        A("Properties of This Folder", None, lambda: self.cur_dir() and self.properties([self.cur_dir()]), menu=fm)
        fm.addSeparator()
        A("Quit", "Ctrl+Q", QApplication.quit, menu=fm)

        em = menu.addMenu("Edit")
        self.a_undo = A("Undo", "Ctrl+Z", lambda: undo.undo(self), ["edit-undo"], menu=em)
        undo.signals.changed.connect(self._sync_undo)
        self._sync_undo()
        em.addSeparator()
        A("Cut", "Ctrl+X", lambda: self.clip(True), ["edit-cut"], menu=em)
        A("Copy", "Ctrl+C", lambda: self.clip(False), ["edit-copy"], menu=em)
        A("Paste", "Ctrl+V", lambda: self.paste(), ["edit-paste"], menu=em)
        A("Paste as Link", "Ctrl+Shift+V", lambda: self.paste(as_link=True), menu=em)
        em.addSeparator()
        A("Select All", "Ctrl+A", lambda: self.pane().view().selectAll(), menu=em)
        A("Invert Selection", "Ctrl+Shift+I", lambda: self.pane().invert_selection(), menu=em)
        em.addSeparator()
        A("Rename…", "F2", lambda: self.rename(self.pane().selected_paths()), menu=em)
        A("Duplicate", "Ctrl+Shift+D", lambda: self.duplicate(self.pane().selected_paths()), menu=em)
        A("Move to Trash", "Delete", lambda: self.trash_paths(self.pane().selected_paths()), ["user-trash"], menu=em)
        A("Delete Permanently", "Shift+Delete", lambda: self.delete_paths(self.pane().selected_paths()), menu=em)
        A("Copy Path", "Ctrl+Shift+C", lambda: self.copy_text(self.pane().selected_paths() or [self.cur_dir()] if self.cur_dir() else []), menu=em)
        em.addSeparator()
        A("Properties", ["Alt+Return", "Ctrl+I"], lambda: self.properties(self.pane().selected_paths() or ([self.cur_dir()] if self.cur_dir() else [])),
          ["document-properties"], menu=em)

        vm = menu.addMenu("View")
        A("Grid View", "Ctrl+1", lambda: self.set_view("grid"), menu=vm)
        A("List View", "Ctrl+2", lambda: self.set_view("list"), menu=vm)
        vm.addSeparator()
        A("Zoom In", ["Ctrl++", "Ctrl+="], lambda: self.pane().zoom(1), menu=vm)
        A("Zoom Out", "Ctrl+-", lambda: self.pane().zoom(-1), menu=vm)
        A("Reset Zoom", "Ctrl+0", lambda: self.pane().zoom(absolute=self.default_zoom(self.pane().is_grid())), menu=vm)
        vm.addSeparator()
        A("Toggle Folder Previews", "Ctrl+Shift+P", self.preview_box.toggle, menu=vm)
        self.a_hidden = A("Show Hidden Files", "Ctrl+H", self.toggle_hidden, checkable=True, menu=vm)
        self.a_hidden.setChecked(self.show_hidden)
        self.a_sidebar = A("Sidebar", "F9", lambda v: self._toggle_panel(self.sidebar, "sidebar", v), checkable=True, menu=vm)
        self.a_sidebar.setChecked(self.sidebar.isVisible())
        self.a_info = A("Info Panel", "F3", lambda v: self._toggle_panel(self.info, "info_panel", v), checkable=True, menu=vm)
        self.a_info.setChecked(self.info.isVisible())
        A("Fullscreen", "F11", lambda: self.showNormal() if self.isFullScreen() else self.showFullScreen(), menu=vm)
        A("Reload", ["F5", "Ctrl+R"], lambda: self.pane().refresh(), ["view-refresh"], menu=vm)

        gm = menu.addMenu("Go")
        A("Back", ["Alt+Left", "Backspace"], lambda: self.pane().go_back(), menu=gm)
        A("Forward", "Alt+Right", lambda: self.pane().go_forward(), menu=gm)
        A("Parent Folder", "Alt+Up", lambda: self.pane().go_up(), menu=gm)
        A("Open Selected", "Alt+Down", lambda: self.open_paths(self.pane(), self.pane().selected_paths()), menu=gm)
        A("Homepage", "Alt+Home", self.go_home, menu=gm)
        A("Home Folder", None, lambda: self.navigate(util.HOME), menu=gm)
        A("Overview (Drives && Bookmarks)", None, lambda: self.navigate(OVERVIEW), menu=gm)
        A("Enter Location…", ["Ctrl+L", "F6"], lambda: self.pathbar.start_edit(), menu=gm)
        A("Search", ["Ctrl+F"], lambda: self.pane().start_search(), menu=gm)
        A("Next Tab", ["Ctrl+PgDown", "Ctrl+Tab"], lambda: self.tabs.setCurrentIndex((self.tabs.currentIndex() + 1) % self.tabs.count()), menu=gm)
        A("Previous Tab", ["Ctrl+PgUp", "Ctrl+Shift+Tab"], lambda: self.tabs.setCurrentIndex((self.tabs.currentIndex() - 1) % self.tabs.count()), menu=gm)

        menu.addSeparator()
        A("Preferences…", "Ctrl+,", self.preferences, ["preferences-system"], menu=menu)
        A("Start Admin Session…", None, lambda: admin.start_session(self), ["security-high", "dialog-password"],
          menu=menu)
        A("Clear Folder Preview Cache", None, self.clear_cache, menu=menu)
        A("Delete All Thumbnails…", None, self.purge_thumbnails, ["edit-clear-all", "edit-delete"], menu=menu)
        A("Keyboard Shortcuts", "F1", self.show_shortcuts, menu=menu)
        A("About", None, lambda: QMessageBox.about(
            self, util.APP_NAME, f"<b>{util.APP_NAME}</b> {__version__}<br>"
                                 "A lightweight image-gallery-oriented file manager."), menu=menu)

    def _sync_undo(self):
        what = undo.label()
        self.a_undo.setText(f"Undo {what}" if what else "Undo")
        self.a_undo.setEnabled(bool(what))

    # -- tabs
    def pane(self):
        return self.tabs.currentWidget()

    def panes(self):
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def new_tab(self, path, activate=True):
        pane = Pane(self, path)
        if pane.path is None:
            pane.deleteLater()
            return None
        i = self.tabs.addTab(pane, pane.title())
        pane.path_changed.connect(lambda p=pane: self._pane_path_changed(p))
        pane.selection_changed.connect(lambda p=pane: p is self.pane() and self._selection_changed())
        if activate:
            self.tabs.setCurrentIndex(i)
            pane.view().setFocus()
        return pane

    def close_tab(self, i):
        if self.tabs.count() <= 1:
            self.close()
            return
        w = self.tabs.widget(i)
        w._stop_search()
        self.tabs.removeTab(i)
        w.deleteLater()

    def _tab_changed(self, i):
        if self.pane():
            self._pane_path_changed(self.pane())
            self.pane().view().setFocus()

    def _pane_path_changed(self, pane):
        i = self.tabs.indexOf(pane)
        if i >= 0:
            self.tabs.setTabText(i, pane.title())
            self.tabs.setTabToolTip(i, pane.path)
        if pane is not self.pane():
            return
        self.pathbar.set_path(pane.path)
        self.sidebar.select_path(pane.path)
        self.setWindowTitle(f"{pane.title()} — {util.APP_NAME}")
        self.a_back.setEnabled(bool(pane.back_stack))
        self.a_fwd.setEnabled(bool(pane.fwd_stack))
        self.a_up.setEnabled(not pane.is_overview() and not pane.is_virtual() and pane.path != "/")
        self._sync_view_btn()
        self.sync_zoom_slider()
        # statfs on a phone waits behind its file transfers
        vol = QStorageInfo(pane.dir) if pane.dir and not util.is_device_path(pane.dir) else None
        self.free_label.setText(f"{util.human_size(vol.bytesAvailable())} free" if vol and vol.isValid() else "")
        self.update_status()

    def navigate(self, path):
        self.open_location(path)

    # -- locations: folders, the overview page, network URIs
    def homepage(self):
        hp = self.settings.value("homepage", "overview")
        if hp == "overview":
            return OVERVIEW
        if hp == "home":
            return util.HOME
        return hp

    def go_home(self):
        self.navigate(self.homepage())

    def _selection_changed(self):
        self.update_status()
        if self.chooser is not None:
            self.chooser.selection_changed()

    def make_chooser(self, req, done):
        """A file chooser window: a normal window with the chooser's bar at the bottom (see chooser.py)."""
        self.chooser = ChooserBar(self, req, done)
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.takeCentralWidget()
        lay.addWidget(self.split, 1)
        lay.addWidget(self.chooser)
        self.setCentralWidget(box)
        self.setWindowTitle(req.title or chooser.button_text(req))
        for p in self.panes():
            p.set_type_filter(self.chooser.type_filter())
        if self.chooser.name is not None:
            self.chooser.name.setFocus()

    # A file chooser window keeps its own view settings, under "chooser/": changing the zoom, view, sort, panels and
    # so on there leaves the main windows alone, and the next chooser starts from them. Until changed in a chooser
    # they follow the main windows', apart from the size (never the main windows' maximized one) and the smaller icons.
    def view_value(self, key, default=None, type=None):
        """The window's and its tabs' view settings (size, zoom, view, sort, panels, hidden files, folder previews,
        search options); a chooser window keeps its own."""
        s = self.settings
        kw = {} if type is None else {"type": type}
        if self.chooser_mode and s.contains("chooser/" + key):
            return s.value("chooser/" + key, default, **kw)
        if key in ("grid_size", "list_size"):
            grid = key == "grid_size"
            return self.default_zoom(grid) if self.chooser_mode else s.value(key, self.default_zoom(grid))
        if self.chooser_mode and key in ("geometry", "splitter"):
            return None
        return s.value(key, default, **kw)

    def set_view_value(self, key, value):
        self.settings.setValue("chooser/" + key if self.chooser_mode else key, value)

    def default_zoom(self, grid):
        if self.chooser_mode:
            return CHOOSER_GRID if grid else CHOOSER_LIST
        return GRID_DEFAULT if grid else LIST_DEFAULT

    def cur_dir(self):
        """Current folder of the active tab, or None on the overview page."""
        return self.pane().dir if self.pane() else None

    def open_location(self, target, new_tab=False):
        """Open a folder path, OVERVIEW, or a network URI (mounted via gvfs first)."""
        if not target:
            return
        if target.startswith("file://"):
            target = util.uri_to_path(target)
        if is_uri(target):
            self.statusBar().showMessage(f"Connecting to {target}…")

            def done(path, err):
                self.statusBar().clearMessage()
                if err:
                    scheme = target.split(":", 1)[0]
                    msg = f"Could not open {target}:\n\n{err}"
                    if is_phone_scheme(scheme):
                        msg += "\n\n" + phone_hint(scheme, target)
                    title = "Connect to Device" if is_phone_scheme(scheme) else "Connect to Server"
                    QMessageBox.warning(self, title, msg)
                elif path:
                    self._remember_server(target)
                    self.sidebar.refresh()
                    self.open_location(path, new_tab=new_tab)
            mount_uri(self, target, done)
            return
        if new_tab or not self.pane():
            self.new_tab(target)
        else:
            self.pane().set_path(target)
            if not self.pane().is_overview():
                self.pane().view().setFocus()

    def _remember_server(self, uri):
        recents = [u for u in (self.settings.value("recent_servers", [], type=list) or []) if u != uri]
        self.settings.setValue("recent_servers", [uri] + recents[:9])

    # -- view
    def set_view(self, mode):
        self.set_view_value("view_mode", mode)
        self.pane().set_view_mode(mode)
        self._sync_view_btn()
        self.sync_zoom_slider()

    def toggle_view(self):
        self.set_view("list" if self.pane().is_grid() else "grid")

    def _sync_view_btn(self):
        grid = self.pane().is_grid()
        self.view_btn.setIcon(icon("view-list-symbolic", "view-list-details") if grid else
                              icon("view-grid-symbolic", "view-grid", "view-list-icons"))
        self.view_btn.setToolTip("Switch to list view (Ctrl+2)" if grid else "Switch to grid view (Ctrl+1)")

    def sync_zoom_slider(self):
        p = self.pane()
        if not p:
            return
        self.zoom_slider.blockSignals(True)
        if p.is_grid():
            self.zoom_slider.setRange(GRID_MIN, GRID_MAX)
        else:
            self.zoom_slider.setRange(LIST_MIN, LIST_MAX)
        self.zoom_slider.setValue(p.zoom_value())
        self.zoom_slider.blockSignals(False)

    def set_folder_previews(self, on):
        """Global switch for folder mosaics; off means no directory scanning at all (a chooser's is its own)."""
        for w in ({self} if self.chooser_mode else set(WINDOWS) | {self}):
            w.folder_previews = on
            w.info.folder_previews = on
            if w.info.path:
                w.info.show_path(w.info.path)
            w.preview_box.blockSignals(True)
            w.preview_box.setChecked(on)
            w.preview_box.blockSignals(False)
            for p in w.panes():
                p._apply_folder_previews()
                p.view().viewport().update()
        self.set_view_value("folder_previews", on)
        if not on:
            self.thumbs.cancel_pending()

    # -- recursive preview build
    def build_previews(self, root):
        if self.builder is not None:
            QMessageBox.information(self, "Generate Previews",
                                    "A preview build is already running. Stop it first (✕ in the status bar).")
            return
        size = self.pane().grid_size if self.pane() else 160
        b = thumbs.RecursiveBuilder(root, size, self.thumbs, self)
        b.progress.connect(self._build_progress)
        b.finished_build.connect(self._build_finished)
        b.finished.connect(b.deleteLater)
        self.builder = b
        self.build_root = root
        self.build_label.setText(f"Previews: {os.path.basename(root.rstrip('/')) or root}")
        self.build_label.setToolTip(root)
        self.build_bar.setRange(0, 0)  # busy while scanning
        self.build_bar.setFormat("Scanning…")
        self.build_box.show()
        b.start()

    def _build_progress(self, done, total, scanning, current):
        if total == 0:
            self.build_bar.setRange(0, 0)
        else:
            self.build_bar.setRange(0, total)
            self.build_bar.setValue(done)
            # total keeps growing while the folder tree is still being walked
            self.build_bar.setFormat(f"{done:,} / {total:,}+ scanning…" if scanning else "%v / %m  (%p%)")
        self.build_bar.setToolTip(current)

    def stop_build(self):
        if self.builder:
            self.builder.cancel()
            self.build_label.setText("Stopping…")
            self.build_stop.setEnabled(False)

    def _build_finished(self, done, total, cancelled):
        self.builder = None
        self.build_box.hide()
        self.build_stop.setEnabled(True)
        self.thumbs.failed.clear()  # items that failed earlier may exist on disk now
        for w in WINDOWS:
            for p in w.panes():
                p.view().viewport().update()
        what = "Stopped" if cancelled else "Finished"
        self.statusBar().showMessage(f"{what} building previews: {done:,} of {total:,} items", 8000)

    def _thumb_progress(self, done, total):
        # small batches (a few visible items) finish too fast to be worth showing
        if total < 4 or done >= total:
            self.thumb_progress.hide()
            return
        self.thumb_progress.setMaximum(total)
        self.thumb_progress.setValue(done)
        self.thumb_progress.show()

    def toggle_hidden(self, on):
        self.show_hidden = on
        self.set_view_value("show_hidden", on)
        for p in self.panes():
            p.apply_hidden()

    def _toggle_panel(self, w, key, on):
        w.setVisible(on)
        self.set_view_value(key, on)
        if on and w is self.info:
            self.update_status()

    def update_status(self):
        p = self.pane()
        if not p:
            return
        if p.is_overview():
            self.status_label.setText("Drives, network locations and bookmarks")
            if self.info.isVisible():
                self.info.show_path(None)
            return
        sel = p.selected_paths()
        total = len(p.all_paths())
        if sel:
            size = 0
            dirs = 0
            for s in sel:
                try:
                    if os.path.isdir(s):
                        dirs += 1
                    else:
                        size += os.lstat(s).st_size
                except OSError:
                    pass
            files = len(sel) - dirs
            parts = []
            if dirs:
                parts.append(f"{dirs} folder{'s' if dirs != 1 else ''}")
            if files:
                parts.append(f"{files} file{'s' if files != 1 else ''} ({util.human_size(size)})")
            text = f"{' and '.join(parts)} selected of {total}"
        else:
            text = f"{total} item{'s' if total != 1 else ''}"
        if p.in_search and p.search_thread and p.search_thread.isRunning():
            text += "  — searching…"
        self.status_label.setText(text)
        if self.info.isVisible():
            self.info.show_path(sel[0] if len(sel) == 1 else (p.path if not sel else None))

    # -- opening
    def open_paths(self, pane, paths, new_tab=False):
        paths = [p for p in paths if p]
        if not paths:
            return
        dirs = [p for p in paths if os.path.isdir(p)]
        files = [p for p in paths if not os.path.isdir(p)]
        if self.chooser is not None:  # a file chooser: folders open as usual, files are the choice
            if dirs:
                pane.set_path(dirs[0])
            elif files:
                self.chooser.activated(files)
            return
        places.add_recent(files)
        if dirs:
            if len(dirs) == 1 and not new_tab and not files:
                pane.set_path(dirs[0])
            else:
                for d in dirs:
                    self.new_tab(d, activate=False)
        archives = [f for f in files if archive.opens_as_archive(f)]
        for f in archives:
            archive_ui.extract_dialog(self, f)  # Kestrel's own extraction, not the system's archive app
        files = [f for f in files if f not in archives]
        images = [f for f in files if util.is_image(f)]
        videos = [f for f in files if util.is_video(f)]
        others = [f for f in files if f not in images and f not in videos]
        # a player would download these again on every open/seek
        fetch = [f for f in videos if util.needs_local_copy(f)]
        videos = [f for f in videos if f not in fetch]
        if fetch:
            fileops.fetch_local(self, fetch, self._open_videos)
        img_choice = self.settings.value("image_opener", "system")
        if images and img_choice == "builtin":
            if len(images) == 1:
                all_imgs = [p for p in pane.all_paths() if util.is_image(p)]
                idx = all_imgs.index(images[0]) if images[0] in all_imgs else 0
                self.open_viewer(pane, all_imgs or images, idx)
            else:
                self.open_viewer(pane, images, 0)
        elif images:
            others += self._open_with_choice(images, img_choice)
        if videos:
            self._open_videos(videos)
        for f in others:
            if not self.open_file(f):
                QMessageBox.warning(self, "Open", f"Could not open {f}")

    def _open_videos(self, videos):
        for f in self._open_with_choice(videos, self.settings.value("video_opener", "system")):
            if not self.open_file(f):
                QMessageBox.warning(self, "Open", f"Could not open {f}")

    def _open_with_choice(self, files, choice):
        """Launch `files` with the app chosen in Preferences; returns files left for the system default."""
        app = util.app_by_id(choice) if choice != "system" else None
        if app is None:
            return files
        try:
            util.launch_app(app, files)
            return []
        except Exception as e:
            QMessageBox.warning(self, "Open", f"Could not open with {app.get_name()}: {e}")
            return files

    def open_file(self, path):
        if path.endswith(".desktop") and shutil.which("gio"):
            import subprocess
            try:
                text = open(path, errors="ignore").read()
            except OSError:
                text = ""
            if "Type=Link" in text:
                for line in text.splitlines():
                    if line.startswith("URL="):
                        target = util.uri_to_path(line[4:].strip()) or line[4:].strip()
                        if os.path.isdir(target):
                            self.navigate(target)
                            return True
                        return util.open_default(target)
            subprocess.Popen(["gio", "launch", path], start_new_session=True)
            return True
        return util.open_default(path)

    def quick_view(self, pane, paths):
        imgs = [p for p in paths if util.is_image(p)]
        if imgs:
            if len(paths) == 1:
                all_imgs = [p for p in pane.all_paths() if util.is_image(p)]
                self.open_viewer(pane, all_imgs, all_imgs.index(imgs[0]) if imgs[0] in all_imgs else 0)
            else:
                self.open_viewer(pane, imgs, 0)
        elif paths:
            self.properties(paths)

    def open_viewer(self, pane, images, idx):
        def on_delete(path):
            try:
                util.trash(path)
                self.sidebar.refresh()
                return True
            except OSError as e:
                QMessageBox.warning(self, "Move to Trash", str(e))
                return False

        def on_close(path):
            if pane in self.panes() and os.path.dirname(path) == pane.path:
                pane.select_paths([path])
            self.viewers = [v for v in self.viewers if v.isVisible()]

        v = ImageViewer(images, idx, self.settings, on_delete=on_delete,
                        on_properties=lambda p: self.properties([p], parent=v),
                        on_close=on_close, on_open_with=lambda p: dialogs.OpenWithDialog(v, [p]).exec())
        self.viewers.append(v)
        if self.settings.value("viewer_fullscreen", False, type=bool):
            v.showFullScreen()
        else:
            v.show()
        v.activateWindow()

    # -- context menu
    def context_menu(self, pane, view, pos):
        idx = view.indexAt(pos)
        if idx.isValid():
            p = idx.siblingAtColumn(0).data(PathRole)
            if p not in pane.selected_paths():
                pane.select_paths([p])
            paths = pane.selected_paths()
        else:
            view.clearSelection()
            paths = []
        m = self.build_menu(pane, paths)
        m.exec(view.viewport().mapToGlobal(pos))

    def build_menu(self, pane, paths):
        m = QMenu(self)
        cur = pane.path
        if not paths and pane.is_virtual():
            m.addAction("Select All", lambda: pane.view().selectAll())
            return m
        if not paths:
            m.addAction(icon("folder-new"), "New Folder…", self.new_folder)
            nd = m.addMenu(icon("document-new"), "New Document")
            nd.addAction("Empty File…", lambda: self.new_file())
            tdir = util.xdg_user_dir("TEMPLATES")
            if os.path.isdir(tdir):
                for t in sorted(os.listdir(tdir), key=util.natural_key):
                    nd.addAction(util.icon_for_path(os.path.join(tdir, t)), util.split_ext(t)[0],
                                 lambda t=t: self.new_file(os.path.join(tdir, t)))
            m.addSeparator()
            pa = m.addAction(icon("edit-paste"), "Paste", lambda: self.paste())
            pa.setEnabled(bool(self.read_clipboard()[1]) or QGuiApplication.clipboard().mimeData().hasImage())
            pl = m.addAction("Paste as Link", lambda: self.paste(as_link=True))
            pl.setEnabled(bool(self.read_clipboard()[1]))
            m.addAction("Select All", lambda: pane.view().selectAll())
            m.addSeparator()
            sm = m.addMenu("Sort By")
            for i, name in enumerate(SORT_COLUMNS):
                sm.addAction(name, lambda i=i: pane.sort_by(i))
            sm.addSeparator()
            sm.addAction("Ascending", lambda: pane.sort_by(pane.tree.header().sortIndicatorSection(), Qt.SortOrder.AscendingOrder))
            sm.addAction("Descending", lambda: pane.sort_by(pane.tree.header().sortIndicatorSection(), Qt.SortOrder.DescendingOrder))
            a = m.addAction("Show Hidden Files", lambda: self.a_hidden.trigger())
            a.setCheckable(True)
            a.setChecked(self.show_hidden)
            m.addSeparator()
            m.addAction(icon("utilities-terminal"), "Open in Terminal", lambda: util.open_terminal(cur))
            m.addAction(icon("bookmark-new"), "Bookmark This Folder", lambda: self.sidebar.add_bookmark(cur))
            m.addAction(icon("view-refresh"), "Generate Previews Recursively", lambda: self.build_previews(cur))
            if util.in_trash(os.path.join(cur, "x")):
                m.addAction(icon("user-trash"), "Empty Trash", self.empty_trash)
            sharing.add_scripts_menu(m, [], cur, self.navigate)
            m.addSeparator()
            m.addAction(icon("document-properties"), "Properties", lambda: self.properties([cur]))
            return m

        single = paths[0] if len(paths) == 1 else None
        is_dir = bool(single and os.path.isdir(single))
        in_trash = util.in_trash(paths[0])
        if in_trash:
            m.addAction(icon("edit-undo"), "Restore", lambda: self.restore(paths))
            m.addAction(icon("edit-delete"), "Delete Permanently", lambda: self.delete_paths(paths))
            m.addSeparator()
            m.addAction(icon("document-properties"), "Properties", lambda: self.properties(paths))
            return m

        m.addAction(icon("document-open"), "Open", lambda: self.open_paths(pane, paths))
        if is_dir or all(os.path.isdir(p) for p in paths):
            m.addAction(icon("tab-new"), "Open in New Tab", lambda: [self.new_tab(p, activate=False) for p in paths])
            m.addAction("Open in New Window", lambda: open_window(paths))
        if is_dir:
            sharing.add_open_folder_menu(m, single, lambda: dialogs.OpenWithDialog(self, paths).exec())
        if any(util.is_image(p) for p in paths):
            imgs = [p for p in paths if util.is_image(p)]
            m.addAction(icon("image-x-generic"), "View Images" if len(imgs) > 1 else "View Image",
                        lambda: self.quick_view(pane, imgs))
        if pane.in_search:
            m.addAction(icon("folder-open"), "Show in Folder", lambda: self.reveal(paths[0]))
        if not is_dir and single:
            ow = m.addMenu("Open With")
            rec, _ = util.apps_for(single)
            for app in rec[:8]:
                ow.addAction(util.gicon_to_qicon(app.get_icon()), app.get_name(),
                             lambda app=app: util.launch_app(app, paths))
            ow.addSeparator()
            ow.addAction("Other Application…", lambda: dialogs.OpenWithDialog(self, paths).exec())
        m.addSeparator()
        m.addAction(icon("edit-cut"), "Cut", lambda: self.clip(True, paths))
        m.addAction(icon("edit-copy"), "Copy", lambda: self.clip(False, paths))
        if is_dir:
            pa = m.addAction(icon("edit-paste"), "Paste Into Folder", lambda: self.paste(target=single))
            pa.setEnabled(bool(self.read_clipboard()[1]))
        m.addAction("Move To…", lambda: self.transfer_to(paths, "move"))
        m.addAction("Copy To…", lambda: self.transfer_to(paths, "copy"))
        m.addAction("Duplicate", lambda: self.duplicate(paths))
        m.addAction(icon("edit-rename"), "Rename…" if single else f"Rename {len(paths)} Items…",
                    lambda: self.rename(paths))
        cp = m.addMenu(icon("edit-copy"), "Copy Path / Name")
        cp.addAction("Copy Full Path", lambda: self.copy_text(paths))
        cp.addAction("Copy Name", lambda: self.copy_text([os.path.basename(p) for p in paths]))
        cp.addAction("Copy URI", lambda: self.copy_text([util.file_uri(p) for p in paths]))

        starred = all(places.is_starred(p) for p in paths)
        m.addAction(icon("non-starred-symbolic", "non-starred") if starred else icon("starred-symbolic", "starred"),
                    "Unstar" if starred else "Star", lambda: places.set_starred(paths, not starred))

        here = pane.dir   # None in Starred / Recent: no "… Here" there
        lm = m.addMenu(icon("emblem-symbolic-link", "insert-link"), "Links && Shortcuts")
        if here:
            lm.addAction("Create Symbolic Link Here", lambda: self.make_links(paths, here, "sym"))
            lm.addAction("Create Relative Symbolic Link Here", lambda: self.make_links(paths, here, "rel"))
        if here and all(os.path.isfile(p) and not os.path.islink(p) for p in paths):
            lm.addAction("Create Hard Link Here", lambda: self.make_links(paths, here, "hard"))
        lm.addAction("Create Symbolic Link In…", lambda: self.make_links(paths, None, "sym"))
        lm.addSeparator()
        desktop = util.xdg_user_dir("DESKTOP")
        lm.addAction("Send Link to Desktop", lambda: self.make_links(paths, desktop, "sym"))
        if here:
            lm.addAction("Create Desktop Shortcut (.desktop) Here", lambda: self.make_links(paths, here, "desktop"))
        lm.addAction("Send Shortcut (.desktop) to Desktop", lambda: self.make_links(paths, desktop, "desktop"))
        if any(os.path.islink(p) for p in paths):
            lm.addSeparator()
            lm.addAction("Open Link Target Location", lambda: self.reveal(os.path.realpath(paths[0])))
        if is_dir:
            lm.addSeparator()
            lm.addAction(icon("bookmark-new"), "Add to Bookmarks", lambda: self.sidebar.add_bookmark(single))

        m.addSeparator()
        if single and not is_dir and archive.can_extract(single):
            missing = archive.missing_extract_tool(single)
            if missing:
                a = m.addAction(icon("archive-extract", "package-x-generic"), f"Extract (install {missing})")
                a.setEnabled(False)
            else:
                m.addAction(icon("archive-extract", "package-x-generic"), "Extract Here",
                            lambda: archive_ui.extract_here(self, single))
                m.addAction(icon("archive-extract", "package-x-generic"), "Extract To…",
                            lambda: archive_ui.extract_dialog(self, single))
        quick = archive_ui.quick_compress_label(self.settings, paths)
        if quick:
            m.addAction(icon("package-x-generic", "archive-insert"), quick,
                        lambda: archive_ui.quick_compress(self, paths))
        m.addAction(icon("package-x-generic", "archive-insert"), "Compress…",
                    lambda: archive_ui.compress_dialog(self, paths))
        if single and (util.is_image(single) or util.is_video(single)) and uwp.editor_open():
            m.addAction(icon("preferences-desktop-wallpaper", "video-display"), "Add to Selected UWP Monitor",
                        lambda: self._uwp_add(single))
        if single and util.is_video(single) and uwp.available():
            m.addAction(icon("preferences-desktop-wallpaper"), uwp.wallpaper_label(), lambda: util.set_wallpaper(single))
        if single and util.is_image(single):
            im = m.addMenu(icon("image-x-generic"), "Image")
            im.addAction(uwp.wallpaper_label(), lambda: util.set_wallpaper(single))
            im.addAction("Use as Folder Cover", lambda: self.thumbs.set_cover(os.path.dirname(single), single))
            im.addAction("Copy Image to Clipboard", lambda: QGuiApplication.clipboard().setImage(
                __import__("PyQt6.QtGui", fromlist=["QImage"]).QImage(single)))
        folders = [p for p in paths if os.path.isdir(p)]
        if folders and len(folders) == len(paths):
            self._folder_style_menu(m, folders)
        if is_dir and single in self.thumbs.covers:
            m.addAction("Reset Folder Cover", lambda: self.thumbs.set_cover(single, None))
        sharing.add_send_to_menu(m, paths)
        if is_dir and sharing.can_share():
            m.addAction(icon("folder-remote", "network-workgroup"), "Network Sharing…",
                        lambda: sharing.share_dialog(self, single))
        sharing.add_scripts_menu(m, paths, here, self.navigate)
        if is_dir:
            m.addAction("Regenerate Preview", lambda: (self.thumbs.invalidate(single),
                                                       atc.announce("folders", paths=[single])))
            m.addAction(icon("view-refresh"), "Generate Previews Recursively",
                        lambda: self.build_previews(single))
            m.addAction(icon("utilities-terminal"), "Open in Terminal", lambda: util.open_terminal(single))
        m.addSeparator()
        m.addAction(icon("user-trash"), "Move to Trash", lambda: self.trash_paths(paths))
        m.addAction(icon("edit-delete"), "Delete Permanently…", lambda: self.delete_paths(paths))
        m.addSeparator()
        m.addAction(icon("document-properties"), "Properties", lambda: self.properties(paths))
        return m

    def _folder_style_menu(self, m, folders):
        """Folder Colour submenu and the Show Image Previews switch for one or more folders."""
        t = self.thumbs
        current = {t.custom_color(f) for f in folders}
        cm = m.addMenu(icon("preferences-color", "applications-graphics"), "Folder Colour")
        for name, color in thumbs.FOLDER_COLORS:
            a = cm.addAction(thumbs.color_swatch(color), name, lambda c=color: t.set_folder_color(folders, c))
            a.setCheckable(True)
            a.setChecked(current == {color})
        cm.addSeparator()
        a = cm.addAction(thumbs.color_swatch(t.folder_color), "Default", lambda: t.set_folder_color(folders, None))
        a.setCheckable(True)
        a.setChecked(current == {None})
        on = all(t.previews_for(f) for f in folders)
        a = m.addAction("Show Image Previews", lambda: t.set_folder_previews(folders, not on))
        a.setCheckable(True)
        a.setChecked(on)
        a.setToolTip("Show a mosaic of the images inside on this folder's icon")

    # -- file actions
    def reveal(self, path):
        pane = self.new_tab(os.path.dirname(path))
        if pane:
            pane.select_later(path)

    def copy_text(self, items):
        QGuiApplication.clipboard().setText("\n".join(items))

    def clip(self, cut, paths=None):
        paths = paths if paths is not None else self.pane().selected_paths()
        if not paths:
            return
        md = QMimeData()
        md.setUrls(util.url_list(paths))
        md.setData("x-special/gnome-copied-files",
                   (("cut" if cut else "copy") + "\n" + "\n".join(util.file_uri(p) for p in paths)).encode())
        md.setData("application/x-kde-cutselection", b"1" if cut else b"0")
        md.setText("\n".join(paths))
        QGuiApplication.clipboard().setMimeData(md)
        self.cut_paths = set(paths) if cut else set()
        for p in self.panes():
            p.view().viewport().update()
        self.statusBar().showMessage(f"{len(paths)} item(s) {'cut' if cut else 'copied'}", 3000)

    def read_clipboard(self):
        md = QGuiApplication.clipboard().mimeData()
        if md is None:
            return "copy", []
        if md.hasFormat("x-special/gnome-copied-files"):
            lines = bytes(md.data("x-special/gnome-copied-files")).decode(errors="ignore").splitlines()
            if lines:
                op = lines[0].strip()
                paths = [util.uri_to_path(u) for u in lines[1:] if u.strip()]
                return ("cut" if op == "cut" else "copy"), [p for p in paths if p]
        if md.hasUrls():
            cut = bytes(md.data("application/x-kde-cutselection")) == b"1"
            return ("cut" if cut else "copy"), [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
        if md.hasText():
            paths = [l.strip() for l in md.text().splitlines() if l.strip().startswith("/")]
            if paths and all(os.path.exists(p) for p in paths):
                return "copy", paths
        return "copy", []

    def paste(self, target=None, as_link=False):
        target = target or self.cur_dir()
        if not target:
            return
        op, paths = self.read_clipboard()
        if not paths:
            md = QGuiApplication.clipboard().mimeData()
            if md is not None and md.hasImage():
                dst = util.unique_path(target, "Pasted image.png", "num")
                if QGuiApplication.clipboard().image().save(dst, "PNG"):
                    undo.record("create", "Paste", [dst])
                self.pane().select_later(dst)
            return
        if as_link:
            self.make_links(paths, target, "sym")
            return
        if op == "cut":
            def done():
                self.cut_paths = set()
                QGuiApplication.clipboard().clear()
            fileops.transfer(self, paths, target, "move", done)
        else:
            fileops.transfer(self, paths, target, "copy")

    def handle_drop(self, paths, target):
        if not paths:
            return
        mods = QGuiApplication.keyboardModifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        if alt:
            m = QMenu(self)
            m.addAction("Move Here", lambda: fileops.transfer(self, paths, target, "move"))
            m.addAction("Copy Here", lambda: fileops.transfer(self, paths, target, "copy"))
            m.addAction("Link Here", lambda: self.make_links(paths, target, "sym"))
            m.exec(self.cursor().pos())
            return
        if ctrl and shift:
            self.make_links(paths, target, "sym")
            return
        if ctrl:
            op = "copy"
        elif shift:
            op = "move"
        else:
            try:
                same = os.lstat(paths[0]).st_dev == os.stat(target).st_dev
            except OSError:
                same = False
            op = "move" if same else "copy"
        fileops.transfer(self, paths, target, op)

    def transfer_to(self, paths, op):
        d = dialogs.choose_dir(self, "Move To" if op == "move" else "Copy To", self.cur_dir() or util.HOME)
        if d:
            fileops.transfer(self, paths, d, op)

    def duplicate(self, paths):
        if paths:
            fileops.start_ops(self, [("copy", p, util.unique_path(os.path.dirname(p), os.path.basename(p)))
                                     for p in paths], "Duplicating", undo_label="Duplicate")

    def new_folder(self):
        cur = self.cur_dir()
        if not cur:
            return
        default = os.path.basename(util.unique_path(cur, "New Folder", "num"))
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:", text=default)
        if ok and name.strip():
            p = os.path.join(cur, name.strip())
            try:
                os.makedirs(p)
                undo.record("create", "New Folder", [p])
                self.pane().select_later(p)
            except PermissionError:
                admin.retry_as_admin(self, "New Folder", f"You don't have permission to create folders in “{cur}”.",
                                     lambda task: admin.session().call(task, "mkdir", path=p),
                                     lambda ok: ok and self.pane().select_later(p))
            except OSError as e:
                QMessageBox.warning(self, "New Folder", str(e))

    def new_file(self, template=None):
        cur = self.cur_dir()
        if not cur:
            return
        base = os.path.basename(template) if template else "Untitled.txt"
        default = os.path.basename(util.unique_path(cur, base, "num"))
        name, ok = QInputDialog.getText(self, "New File", "File name:", text=default)
        if ok and name.strip():
            p = os.path.join(cur, name.strip())
            if os.path.lexists(p):
                QMessageBox.warning(self, "New File", "A file with that name already exists.")
                return
            try:
                if template:
                    shutil.copyfile(template, p)
                else:
                    open(p, "x").close()
                undo.record("create", "New File", [p])
                self.pane().select_later(p)
            except PermissionError:
                admin.retry_as_admin(
                    self, "New File", f"You don't have permission to create files in “{cur}”.",
                    lambda task: admin.session().call(task, "copyfile", src=template, dst=p) if template else
                    admin.session().call(task, "touch", path=p),
                    lambda ok: ok and self.pane().select_later(p))
            except OSError as e:
                QMessageBox.warning(self, "New File", str(e))

    def rename(self, paths):
        if not paths:
            return
        if len(paths) > 1:
            dialogs.BatchRenameDialog(self, paths).exec()
            return
        new = dialogs.ask_rename(self, paths[0])
        if new:
            target = os.path.join(os.path.dirname(paths[0]), new)

            def renamed(ok=True):
                if ok:
                    self.pane().select_later(target)
                    QTimer.singleShot(150, self.pane()._try_select)
            try:
                err = dialogs.do_rename(paths[0], new)
            except PermissionError:
                admin.retry_as_admin(self, "Rename",
                                     f"You don't have permission to rename “{os.path.basename(paths[0])}”.",
                                     lambda task: admin.session().call(task, "rename", src=paths[0], dst=target),
                                     renamed)
                return
            if err:
                QMessageBox.warning(self, "Rename", err)
            else:
                undo.record("rename", "Rename", [(paths[0], target)])
                renamed()

    def trash_paths(self, paths):
        if not paths:
            return
        if util.in_trash(paths[0]):
            self.delete_paths(paths)
            return
        paths = list(paths)
        trashed = []

        def work(task):
            failed = []
            for i, p in enumerate(paths):
                if task.cancelled:
                    break   # items already moved stay in the trash (and can be undone)
                task.report(i, len(paths), f"{i:,} of {len(paths):,} — {os.path.basename(p)}")
                try:
                    util.trash(p)
                    trashed.append(p)
                except OSError as e:
                    failed.append((p, str(e)))
            return None if task.cancelled else failed

        def done(failed):
            undo.record("trash", "Move to Trash", trashed)
            self.sidebar.refresh()
            if failed:
                r = QMessageBox.question(
                    self, "Cannot move to trash",
                    f"{len(failed)} item(s) could not be moved to the trash:\n{failed[0][1]}\n\n"
                    "Delete them permanently?")
                if r == QMessageBox.StandardButton.Yes:
                    fileops.start_ops(self, [("delete", p, None) for p, _ in failed], "Deleting")
            elif failed is not None:
                self.statusBar().showMessage(f"Moved {len(paths):,} item(s) to the trash", 4000)
            else:
                self.statusBar().showMessage("Move to trash cancelled; items already moved stay in the trash", 6000)
        fileops.run_job(self, "Moving to trash", work, done)

    def delete_paths(self, paths):
        if not paths:
            return
        what = f"“{os.path.basename(paths[0])}”" if len(paths) == 1 else f"these {len(paths)} items"
        box = QMessageBox(QMessageBox.Icon.Warning, "Delete Permanently",
                          f"Permanently delete {what}?\n\nThis cannot be undone.", QMessageBox.StandardButton.Cancel, self)
        delete = box.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is delete:
            def done():
                for p in paths:
                    info = util.trash_info_path(p)
                    if info:
                        try:
                            os.unlink(info)
                        except OSError:
                            pass
                self.sidebar.refresh()
            fileops.start_ops(self, [("delete", p, None) for p in paths], "Deleting", done)

    def restore(self, paths):
        paths = list(paths)

        def work(task):
            errors = []
            for i, p in enumerate(paths):
                task.check()
                task.report(i, len(paths), f"{i:,} of {len(paths):,} — {os.path.basename(p)}")
                orig = util.trash_original_path(p)
                if not orig:
                    errors.append(f"{os.path.basename(p)}: original location unknown")
                    continue
                dest = orig if not os.path.lexists(orig) else \
                    util.unique_path(os.path.dirname(orig), os.path.basename(orig), "num")
                try:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    info = util.trash_info_path(p)
                    shutil.move(p, dest)
                    if info and os.path.exists(info):
                        os.unlink(info)
                except OSError as e:
                    errors.append(f"{os.path.basename(p)}: {e}")
            return errors

        def done(errors):
            self.sidebar.refresh()
            if errors:
                QMessageBox.warning(self, "Restore", "\n".join(errors[:20]))
        fileops.run_job(self, "Restoring", work, done)

    def empty_trash(self):
        r = QMessageBox.warning(self, "Empty Trash", "Permanently delete all items in the trash?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Yes:
            jobs = [("delete", e.path, None) for root in util.trash_dirs() for sub in ("files", "info", "expunged")
                    if os.path.isdir(os.path.join(root, sub)) for e in os.scandir(os.path.join(root, sub))]
            fileops.start_ops(self, jobs, "Emptying trash", self.sidebar.refresh)

    def make_links(self, paths, dest, kind):
        if dest is None:
            dest = dialogs.choose_dir(self, "Create Links In", self.cur_dir() or util.HOME)
            if not dest:
                return
        made, errors, denied = [], [], []
        try:
            os.makedirs(dest, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "Create Link", str(e))
            return
        for p in paths:
            plan = fileops.link_plan(kind, p, dest)
            try:
                made.append(fileops.make_link(plan))
            except PermissionError:
                denied.append(plan)
            except OSError as e:
                errors.append(f"{os.path.basename(p)}: {e}")
        if errors:
            QMessageBox.warning(self, "Create Link", "\n".join(errors))

        undo.record("create", "Create Link", made)

        def finish(_ok=True):
            done = made + [fileops.plan_path(pl) for pl in denied if os.path.lexists(fileops.plan_path(pl))]
            if done:
                self.statusBar().showMessage(f"Created {len(done)} link(s) in {dest}", 4000)
                if dest == self.cur_dir():
                    self.pane().select_later(done[0])
        if denied:
            def work(task):
                errs = []
                for pl in denied:
                    try:
                        admin.session().call(task, **pl)
                    except admin.AdminError as e:
                        errs.append(f"{os.path.basename(fileops.plan_path(pl))}: {e}")
                return errs
            admin.retry_as_admin(self, "Create Link", f"You don't have permission to create links in “{dest}”.",
                                 work, finish)
        else:
            finish()

    def _uwp_add(self, path):
        if uwp.add_to_selected(path):
            self.statusBar().showMessage(f"Sent {os.path.basename(path)} to the selected UWP monitor", 4000)
        else:
            QMessageBox.warning(self, "UWP", "Couldn't reach UWP. Is its editor window still open?")

    def properties(self, paths, parent=None):
        if paths:
            dialogs.PropertiesDialog(parent or self, paths).exec()
            self.update_status()

    # -- settings
    def preferences(self):
        # A separate, non-modal window: GNOME attaches modal dialogs to their parent ("attach-modal-dialogs"), so
        # dragging a modal Preferences would drag the whole Kestrel window with it.
        if self._prefs is not None:
            self._prefs.raise_()
            self._prefs.activateWindow()
            return
        d = dialogs.PreferencesDialog(self, self.settings)
        d.setWindowModality(Qt.WindowModality.NonModal)
        d.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        d.accepted.connect(self._preferences_saved)
        d.destroyed.connect(lambda: setattr(self, "_prefs", None))
        self._prefs = d
        d.show()

    def _preferences_saved(self):
        apply_preferences()
        _settings.sync()  # other Kestrels read the file as soon as they hear the report
        atc.announce("settings")

    def clear_cache(self):
        shutil.rmtree(util.APP_CACHE / "folders", ignore_errors=True)
        self.thumbs.clear_memory()
        repaint_all()
        atc.announce("thumbs_cleared")
        self.statusBar().showMessage("Folder preview cache cleared", 3000)

    def purge_thumbnails(self):
        box = QMessageBox(QMessageBox.Icon.Warning, "Delete All Thumbnails",
                          f"Delete every thumbnail and folder preview {util.APP_NAME} has made?\n\n"
                          f"• Folder mosaics in {util.APP_CACHE / 'folders'}\n"
                          f"• Image/video thumbnails it wrote to {util.THUMB_DIR}\n\n"
                          "They will be regenerated as you browse.",
                          QMessageBox.StandardButton.Cancel, self)
        go = box.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
        shared = QCheckBox("Also delete thumbnails made by other apps (GNOME Files, etc.)")
        box.setCheckBox(shared)
        box.exec()
        if box.clickedButton() is not go:
            return
        self.thumbs.cancel_pending()
        include = shared.isChecked()

        def done(res):
            files, size = res
            self.thumbs.clear_memory()
            repaint_all()
            atc.announce("thumbs_cleared")
            self.statusBar().showMessage(f"Deleted {files:,} thumbnails ({util.human_size(size)})", 6000)
        fileops.run_task(self, "Deleting thumbnails", lambda: thumbs.purge_thumbnails(include), done)

    def show_shortcuts(self):
        text = """
<table cellpadding=3>
<tr><td><b>Enter / double-click</b></td><td>Open</td></tr>
<tr><td><b>Space</b></td><td>Quick view selection</td></tr>
<tr><td><b>Backspace, Alt+Left / Alt+Right</b></td><td>Back / Forward</td></tr>
<tr><td><b>Alt+Up</b></td><td>Parent folder</td></tr>
<tr><td><b>Ctrl+L</b></td><td>Type a location</td></tr>
<tr><td><b>Ctrl+F</b></td><td>Search (filter or recursive)</td></tr>
<tr><td><b>Ctrl+T / Ctrl+W</b></td><td>New tab / close tab (middle-click folder: open in tab)</td></tr>
<tr><td><b>Ctrl+1 / Ctrl+2</b></td><td>Grid / list view</td></tr>
<tr><td><b>Ctrl+scroll, Ctrl+= / Ctrl+-</b></td><td>Zoom thumbnails</td></tr>
<tr><td><b>Ctrl+H</b></td><td>Show hidden files</td></tr>
<tr><td><b>F2</b></td><td>Rename (batch rename with multiple selected)</td></tr>
<tr><td><b>Ctrl+X / C / V</b></td><td>Cut / copy / paste (works with GNOME Files)</td></tr>
<tr><td><b>Ctrl+Shift+V</b></td><td>Paste as symbolic link</td></tr>
<tr><td><b>Delete / Shift+Delete</b></td><td>Trash / delete permanently</td></tr>
<tr><td><b>Alt+Enter</b></td><td>Properties</td></tr>
<tr><td><b>F3 / F9</b></td><td>Info panel / sidebar</td></tr>
<tr><td><b>Drag + Ctrl / Shift / Ctrl+Shift / Alt</b></td><td>Copy / move / link / ask</td></tr>
<tr><td colspan=2><br><b>Image viewer</b></td></tr>
<tr><td><b>←/→, scroll wheel</b></td><td>Previous / next</td></tr>
<tr><td><b>Ctrl+scroll, +/-, 0, 1</b></td><td>Zoom, fit, 100%</td></tr>
<tr><td><b>F / double-click</b></td><td>Fullscreen</td></tr>
<tr><td><b>S</b></td><td>Slideshow</td></tr>
<tr><td><b>R / L / H</b></td><td>Rotate right / left, flip</td></tr>
<tr><td><b>I</b></td><td>Toggle info overlay</td></tr>
<tr><td><b>Delete</b></td><td>Move to trash</td></tr>
</table>"""
        QMessageBox.information(self, "Keyboard Shortcuts", text)

    def closeEvent(self, ev):
        tasks = list(self.task_panel.tasks)
        if tasks:
            # The worker threads belong to this window; closing now would kill them mid-operation.
            names = "\n".join(f"• {t.title}" for t in tasks[:5])
            box = QMessageBox(QMessageBox.Icon.Question, "Operations Running",
                              f"These operations are still running:\n\n{names}\n\n"
                              "Stop them and close the window? Items already processed stay processed.",
                              QMessageBox.StandardButton.NoButton, self)
            stop = box.addButton("Stop and Close", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton("Keep Running", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            ev.ignore()
            if box.clickedButton() is stop:
                for t in tasks:
                    t.cancel()
                self.task_panel._refresh()
                self.centralWidget().setEnabled(False)  # stay visible (showing "cancelling…") until stopped
                self._close_when_idle()
            return
        if not self.chooser_mode or not (self.isMaximized() or self.isFullScreen()):  # a chooser opens smaller
            self.set_view_value("geometry", self.saveGeometry())
        self.set_view_value("splitter", self.split.saveState())
        if self.chooser is not None:
            self.chooser.finish(False)  # closed without choosing: cancelled
        for p in self.panes():
            p._stop_search()
        if self.builder:
            self.builder.cancel()
            self.builder.wait(5000)
        for v in list(self.viewers):
            v.close()
        if self in WINDOWS:
            WINDOWS.remove(self)
        report_windows()
        super().closeEvent(ev)

    def _close_when_idle(self):
        """Finish closing once every task has stopped (tasks that can't be cancelled run to the end)."""
        if self.task_panel.tasks:
            QTimer.singleShot(100, self._close_when_idle)
        else:
            self.close()


# ---------------------------------------------------------------- entry

_thumbs = None
_settings = None


_accent_watched = None


def apply_thumb_settings(t, s):
    global _accent_watched
    t.folder_count = int(s.value("folder_count", 4))
    t.folder_order = s.value("folder_order", "name")
    color = s.value("folder_color", "accent")
    t.folder_accent = thumbs.follows_accent(color)
    t.folder_color = util.accent_color().name() if t.folder_accent else color
    t.max_file_mb = int(s.value("thumb_max_mb", 200))
    if _accent_watched is None:  # runs before the windows' own updates, which then draw folders in the new accent
        _accent_watched = t

        def accent_changed():
            if t.folder_accent and t.folder_color != util.accent_color().name():
                t.folder_color = util.accent_color().name()
                repaint_all()
        util.on_palette_change(t, accent_changed)


def repaint_all():
    for w in WINDOWS:
        for p in w.panes():
            p.view().viewport().update()


def apply_preferences():
    apply_thumb_settings(_thumbs, _settings)
    _thumbs.clear_memory()
    for w in WINDOWS:
        for p in w.panes():
            p.animator.clear()
            p._apply_folder_previews()
            p.view().viewport().update()
    undo.signals.changed.emit()   # "Share undo…" may have changed what Ctrl+Z undoes


def on_atc(msg):
    """A change reported by another Kestrel through the tower (see atc.py), or by this one ("own")."""
    kind = msg.get("type")
    if kind == "sidebar":   # its order or collapsed sections; also refreshes this process's other windows
        if not msg.get("own"):
            _settings.sync()
        for w in WINDOWS:
            w.sidebar.refresh()
    if kind == "bookmarks":   # also refreshes this process's other windows
        for w in WINDOWS:
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
            w = open_window(folders[:1])
            open_as_tabs(w, folders[1:], select[1:])
            if w.pane() is not None and select[:1] and select[0]:
                w.pane().select_later(select[0])
    elif kind == "settings":
        _settings.sync()
        apply_preferences()
    elif kind == "starred":
        places.reload_starred()
    elif kind == "folders":
        _thumbs.reload_styles()
        for path in msg.get("paths") or []:
            if isinstance(path, str):
                _thumbs.invalidate(path, disk=False)   # the sender already removed the cached mosaic
    elif kind == "thumbs_cleared":
        _thumbs.clear_memory()
        for w in WINDOWS:
            for p in w.panes():
                p.animator.clear()
        repaint_all()


def location_arg(arg):
    """A command-line argument (as passed by xdg-open, the file chooser or GNOME) as a location for open_location:
    a local path, OVERVIEW, or a network URI to mount. None if it can't be opened."""
    if arg.startswith("file:"):
        return util.uri_to_path(arg) or None
    scheme = arg.split(":", 1)[0].lower() if is_uri(arg) else ""
    if scheme == "trash":
        return str(util.TRASH_DIR / "files")
    if scheme in ("recent", "starred"):
        return places.RECENT if scheme == "recent" else places.STARRED
    if scheme in ("computer", "x-nautilus-desktop", "other-locations"):
        return OVERVIEW
    if is_uri(arg):
        return arg  # smb://, sftp://, … are mounted through gvfs by open_location
    return os.path.abspath(os.path.expanduser(arg))


# ---- Preferences → "Open folders from other apps as tabs in an open Kestrel window"

_last_active = None      # the window used most recently
_last_active_ms = 0


def report_windows():
    """For the tower's Handoff: whether we have windows, and when one was last used."""
    atc.announce("windows", keep=True, count=len(WINDOWS), active=float(_last_active_ms))


def _window_focused(win):
    global _last_active, _last_active_ms
    for w in WINDOWS:
        if win is not None and w.windowHandle() is win:
            _last_active, _last_active_ms = w, QDateTime.currentMSecsSinceEpoch()
            report_windows()


def open_in_tabs():
    # never from a conda environment the user activated: the Kestrel taking over may run in a different one
    return _settings.value("open_in_tabs", False, type=bool) and not env.explicit_conda_env()


def recent_window():
    if _last_active is not None and _last_active in WINDOWS:
        return _last_active
    return WINDOWS[-1] if WINDOWS else None


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
        w = WINDOWS[-1] if WINDOWS else open_window([os.path.dirname(paths[0])])
        w.properties(paths)
        return
    if method == "ShowFolders":
        if tabs_instead(paths, []):
            return
        w = open_window(paths)
    else:
        # ShowItems: each item's folder in a tab, with the item selected, scrolled to and focused (folders too:
        # they're shown in their parent, not opened)
        groups = {}
        for p in paths:
            groups.setdefault(os.path.dirname(p.rstrip("/")) or "/", []).append(p)
        if tabs_instead(list(groups), [items[0] for items in groups.values()]):
            return
        w = open_window([next(iter(groups))])
        for i, (folder, items) in enumerate(groups.items()):
            pane = w.pane() if i == 0 else w.new_tab(folder, activate=False)
            if pane is not None:
                pane.select_later(items[0])
    w.raise_()
    w.activateWindow()
    if w.pane():
        w.tabs.setCurrentIndex(0)
        w.pane().view().setFocus()


def open_chooser(req, done):
    """Not in WINDOWS: a chooser isn't a window other Kestrels hand folders to."""
    start = req.current_folder
    if not os.path.isdir(start):
        start = _settings.value("chooser_folder", "")  # where the last chooser picked something
    if not start or not os.path.isdir(start):
        start = util.HOME
    w = MainWindow([start], _thumbs, _settings, chooser_mode=True)
    w.make_chooser(req, done)
    wid = chooser.x11_parent(req.parent_window)
    if wid and QGuiApplication.platformName() == "xcb":  # the app's window (X11): the chooser is its dialog
        w.winId()  # create the native window, to give it a transient parent before it is shown
        app_window = QWindow.fromWinId(wid)
        if app_window is not None:
            w.windowHandle().setTransientParent(app_window)
            w._app_window = app_window  # kept with the chooser
    w.show()
    w.raise_()
    w.activateWindow()
    return w


def open_window(paths):
    w = MainWindow(paths, _thumbs, _settings)
    WINDOWS.append(w)
    w.show()
    report_windows()
    return w


def main(argv=None):
    global _thumbs, _settings
    # LibRaw (RAW image plugin) uses OpenMP; by default each decode spawns one
    # busy-waiting thread per core, which starves the UI. Must be set before the
    # plugin is loaded.
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
    argv = sys.argv if argv is None else argv
    if "--version" in argv[1:]:
        print(f"{util.APP_NAME} {__version__}")
        return 0
    if "--atc" in argv[1:]:
        return atc.run_tower(argv)   # the tower: no window (see atc.py)
    QApplication.setApplicationName(util.APP_ID)
    QApplication.setApplicationVersion(__version__)
    QApplication.setApplicationDisplayName(util.APP_NAME)
    QApplication.setDesktopFileName(util.APP_ID)
    app = QApplication(argv)
    util.setup_icon_theme()
    util.follow_gtk_theme()  # Qt < 6.5: the GTK theme's colours, following changes
    app.setWindowIcon(util.theme_icon("folder"))
    util.migrate_legacy()
    util.ensure_desktop_entry()
    from PyQt6.QtGui import QImageReader
    QImageReader.setAllocationLimit(2048)
    _settings = QSettings(util.APP_ID, util.APP_ID)
    _thumbs = thumbs.ThumbnailManager()
    apply_thumb_settings(_thumbs, _settings)
    if "--file-chooser" in argv[1:]:
        # started by D-Bus for the system's file chooser (see chooser.py): only chooser windows
        app.setQuitOnLastWindowClosed(False)  # the service quits after a minute without dialogs

        def opener(req, done):
            w = open_chooser(req, done)
            return lambda: not sip.isdeleted(w) and w.close()
        chooser.serve(opener)
        return app.exec()
    paths = [location_arg(a) for a in argv[1:] if not a.startswith("-")]
    service = "--dbus-service" in argv[1:]
    paths = [p for p in paths if p]
    if not service and paths and open_in_tabs() and atc.hand_off(paths):
        return 0   # an open Kestrel window took the folders as tabs
    app.focusWindowChanged.connect(_window_focused)
    atc.radio().heard.connect(on_atc)
    atc.radio().start()
    fm1.start(handle_fm1, on_lost=app.quit if service else None)
    if service:
        # started by D-Bus for a "show in folder" request: no window of our own; quit if none is asked for
        QTimer.singleShot(30_000, lambda: WINDOWS or app.quit())
    else:
        open_window([p for p in paths if p])
    return app.exec()
