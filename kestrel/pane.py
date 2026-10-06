"""A browser pane: one tab's view of a folder (grid or list), the overview page, the combined trash, Starred and Recent,
search, history and selection. The window around it is in app.py."""

import os

from PyQt6.QtCore import (QDir, QEvent, QFileSystemWatcher, QItemSelectionModel, QModelIndex, QPersistentModelIndex,
                          QRectF, QSize, Qt, QTimer, pyqtSignal)
from PyQt6.QtGui import QCursor, QDrag, QGuiApplication, QPainter, QPalette, QPen, QPixmap
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListView,
                             QMessageBox, QStackedWidget, QStyle, QStyleOptionViewItem, QToolButton, QTreeView,
                             QVBoxLayout, QWidget)

from . import animate, fileops, focus, places, stats, util
from .overview import OVERVIEW, OVERVIEW_TITLE, OverviewPage
from .widgets import FSModel, GridDelegate, PathRole, SearchModel, SearchThread, can_search_contents

SEL = QItemSelectionModel.SelectionFlag

# zoom limits: icon size in the grid, row height in the list
GRID_MIN, GRID_MAX = 48, 320
LIST_MIN, LIST_MAX = 16, 128


def icon(*names):
    return util.theme_icon(*names)


class FileViewDrag:
    """The file views. Their drags are Qt's, apart from giving the focus to the app the files are dropped into
    (focus.py) and highlighting the folder a drag is over (where the files will go)."""

    _drop_target = None  # the folder highlighted under a drag (a QPersistentModelIndex)

    def dragMoveEvent(self, ev):
        super().dragMoveEvent(ev)
        i = self.indexAt(ev.position().toPoint())
        if i.isValid():
            i = i.siblingAtColumn(0)
        # a folder that takes the drop, and isn't one of the items being dragged
        dragged = ev.source() is self and self.selectionModel().isSelected(i)
        folder = i.isValid() and bool(self.model().flags(i) & Qt.ItemFlag.ItemIsDropEnabled)
        self._set_drop_target(i if ev.isAccepted() and folder and not dragged else None)

    def dragLeaveEvent(self, ev):
        self._set_drop_target(None)
        super().dragLeaveEvent(ev)

    def dropEvent(self, ev):
        self._set_drop_target(None)
        super().dropEvent(ev)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if self._drop_target is None or not self._drop_target.isValid():
            return
        r = QRectF(self.visualRect(QModelIndex(self._drop_target)))
        if isinstance(self, QTreeView):
            r, radius = QRectF(0, r.top(), self.viewport().width(), r.height()), 4  # the whole row
        else:
            r, radius = r.adjusted(3, 3, -3, -3), 8  # as the grid's selection
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = self.palette().color(QPalette.ColorRole.Highlight)
        p.setPen(QPen(c, 2))
        c.setAlpha(70)
        p.setBrush(c)
        p.drawRoundedRect(r.adjusted(1, 1, -1, -1), radius, radius)
        p.end()

    def _set_drop_target(self, i):
        old = self._drop_target if self._drop_target is not None and self._drop_target.isValid() else None
        if (old is None and i is None) or (old is not None and i is not None and QModelIndex(old) == i):
            return
        self._drop_target = QPersistentModelIndex(i) if i is not None else None
        self.viewport().update()
        self.setProperty("drop_target", i.data(PathRole) if i is not None else "")  # for the tests

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
        self._listing_since = None   # KESTREL_STATS: when the folder now shown started loading
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
        self.search_edit.setAccessibleName("Search")
        self.search_edit.setClearButtonEnabled(True)
        self.search_sub = QCheckBox("Include subfolders")
        self.search_sub.setChecked(win.view_value("search_recursive", False, type=bool))
        close = QToolButton()
        close.setIcon(icon("window-close-symbolic", "window-close"))
        close.setToolTip("Close the search (Esc)")
        close.setAccessibleName("Close the search")
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
        v.setAccessibleName("Files")
        v.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        v.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        v.setDragEnabled(True)
        v.setAcceptDrops(True)
        v.setDropIndicatorShown(False)  # FileViewDrag highlights the folder instead (Qt's also marks between rows)
        v.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        v.setDefaultDropAction(Qt.DropAction.MoveAction)
        v.viewport().setAcceptDrops(True)  # the grid's Static movement turns drops off there (QListView.setMovement)
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
        t.header().setAccessibleName("Columns")
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
        self._listing_since = stats.now_ms()
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
            if self._listing_since is not None:
                stats.sample("folder listing (ms)", float(stats.now_ms() - self._listing_since))
            self._listing_since = None
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
