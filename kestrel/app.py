"""The main window (its file actions: actions.py; opening files: opening.py). The browser pane: pane.py."""
import os
import shutil
import sys

from PyQt6 import sip
from PyQt6.QtCore import QItemSelectionModel, QSettings, QSize, QStorageInfo, Qt, QTimer
from PyQt6.QtGui import QAction, QGuiApplication, QKeySequence, QWindow
from PyQt6.QtWidgets import (QApplication, QCheckBox, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox,
                             QProgressBar, QSlider, QSplitter, QTabWidget, QToolBar, QToolButton, QVBoxLayout, QWidget)

from . import (__version__, admin, archive, archive_ui, atc, chooser, dialogs, fileops, fm1, hashcheck, places,
               sharing, stats, thumbs, undo, util, uwp)
from .actions import FileActions
from .chooser import ChooserBar
from .incoming import handle_fm1, on_atc, open_in_tabs, report_windows, window_focused  # noqa: F401 (tests use A.on_atc)
from .opening import Opening
from .overview import OVERVIEW, is_phone_scheme, is_uri, mount_uri, phone_hint
from .pane import GRID_MAX, GRID_MIN, LIST_MAX, LIST_MIN, Pane
from .widgets import InfoPanel, PathBar, PathRole, Sidebar

SEL = QItemSelectionModel.SelectionFlag

GRID_DEFAULT, LIST_DEFAULT = 160, 28
CHOOSER_GRID, CHOOSER_LIST = 96, 24  # a chooser window starts with smaller icons
SORT_COLUMNS = ["Name", "Size", "Type", "Modified"]
WINDOWS = []


def icon(*names):
    return util.theme_icon(*names)


class MainWindow(FileActions, Opening, QMainWindow):
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
        self.setWindowIcon(util.app_icon())
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
        self.zoom_slider.setAccessibleName("Zoom")
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
        self.view_btn.setAutoRaise(True)  # its name and tooltip follow the view (_sync_view_btn)
        self.view_btn.clicked.connect(self.toggle_view)
        tb.addWidget(self.view_btn)
        self.sort_btn = QToolButton()
        self.sort_btn.setAutoRaise(True)
        self.sort_btn.setIcon(icon("view-sort-ascending-symbolic", "view-sort-ascending"))
        self.sort_btn.setToolTip("Sort")
        self.sort_btn.setAccessibleName("Sort")
        self.sort_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.sort_menu = QMenu(self)
        self.sort_menu.aboutToShow.connect(self._fill_sort_menu)
        self.sort_btn.setMenu(self.sort_menu)
        tb.addWidget(self.sort_btn)
        self.menu_btn = QToolButton()
        self.menu_btn.setAutoRaise(True)
        self.menu_btn.setIcon(icon("open-menu-symbolic", "application-menu", "preferences-system"))
        self.menu_btn.setToolTip("Menu")
        self.menu_btn.setAccessibleName("Menu")
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
        self.view_btn.setAccessibleName("Switch to list view" if grid else "Switch to grid view")

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

    # -- opening files (activation, Quick View, the image viewer): opening.py

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
            md = QGuiApplication.clipboard().mimeData()  # None when the clipboard is empty
            pa.setEnabled(bool(self.read_clipboard()[1]) or (md is not None and md.hasImage()))
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
                if fileops.can_shred():
                    m.addAction(icon("edit-shred", "edit-delete"), "Empty Trash with BleachBit…",
                                self.empty_trash_with_bleachbit)
            sharing.add_scripts_menu(m, [], cur, self.navigate)
            m.addSeparator()
            m.addAction(icon("document-properties"), "Properties", lambda: self.properties([cur]))
            return m

        single = paths[0] if len(paths) == 1 else None
        is_dir = bool(single and os.path.isdir(single))
        # only a selection all in the trash gets the trash's menu; a mixed one (a search, Recent, Starred) gets the
        # usual menu, whose Move to Trash trashes the rest and asks before deleting what's already in the trash
        if all(util.in_trash(p) for p in paths):
            m.addAction(icon("edit-undo"), "Restore", lambda: self.restore(paths))
            m.addAction(icon("edit-delete"), "Delete Permanently", lambda: self.delete_paths(paths))
            if fileops.can_shred():
                m.addAction(icon("edit-shred", "edit-delete"), "Shred with BleachBit…", lambda: self.shred_paths(paths))
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
        m.addAction(icon("security-high", "document-properties"), "Create Checksum File…",
                    lambda: hashcheck.create_dialog(self, paths))
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
        if fileops.can_shred():
            m.addAction(icon("edit-shred", "edit-delete"), "Shred with BleachBit…", lambda: self.shred_paths(paths))
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

    # -- file actions (the clipboard, new files, rename, trash, links…): actions.py

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


# The app-wide objects, and their order. Each is created once and lives until the process exits:
#   _settings        here, right after the QApplication, before any thread starts. Used from the UI thread only.
#                    Other Kestrels change the same file: on_atc("settings") re-reads it.
#   _thumbs          here, after _settings (apply_thumb_settings reads it). Its thread pool runs for the process.
#   TaskBoard        fileops.py, on first use (the first task, or the first window's task panel).
#   atc.radio()      atc.py, on first use; start() here, after focus tracking and on_atc are connected, so the first
#                    messages from other Kestrels find them. The tower is a separate process.
#   admin.session()  admin.py, on first use (a "Retry as Administrator" or the menu); the helper process it starts
#                    ends when its pipe closes, at the latest when this process exits.
#   WINDOWS          app.py; a window is added when opened and removed when it closes (choosers aren't in it).
#   last used window incoming.py (window_focused); checked against WINDOWS before use.
# A file-chooser process (--file-chooser) makes _settings and _thumbs, then only chooser windows.
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
    stats.print_at_quit(app)  # KESTREL_STATS=1
    util.setup_icon_theme()
    util.follow_gtk_theme()  # Qt < 6.5: the GTK theme's colours, following changes
    app.setWindowIcon(util.app_icon())
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
    app.focusWindowChanged.connect(window_focused)
    atc.radio().heard.connect(on_atc)
    atc.radio().start()
    fm1.start(handle_fm1, on_lost=app.quit if service else None)
    if service:
        # started by D-Bus for a "show in folder" request: no window of our own; quit if none is asked for
        QTimer.singleShot(30_000, lambda: WINDOWS or app.quit())
    else:
        open_window([p for p in paths if p])
    return app.exec()
