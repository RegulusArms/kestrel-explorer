"""Kestrel as the system's file chooser: the Open and Save dialogs other apps get through xdg-desktop-portal (a
browser's "Save image as", Flatpak and Snap apps, GTK 4 and Qt apps that use the portal).

install.sh --default registers it: a D-Bus activation file starts `kes --file-chooser`, which owns
org.freedesktop.impl.portal.desktop.kestrel and serves org.freedesktop.impl.portal.FileChooser, and the user's
portals.conf picks it for FileChooser (GNOME's chooser stays next in line). Each request opens a Kestrel window in
chooser mode: a normal window with a ChooserBar (file name, file type, Cancel and Save/Open) at the bottom.
"""
import os

from PyQt6.QtCore import QMimeDatabase, QTimer
from PyQt6.QtWidgets import QApplication, QComboBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QWidget

from . import util


class Filter:
    def __init__(self, name, patterns):
        self.name = name
        self.patterns = patterns  # as the app sent them: (0, glob) or (1, MIME type)
        globs = []
        for kind, p in patterns:
            globs += [p] if kind == 0 else _mime_globs(p)
        self.globs = list(dict.fromkeys(globs))  # what to show: the globs, and each MIME type's globs


class Request:
    def __init__(self):
        self.method = ""  # OpenFile, SaveFile or SaveFiles
        self.title = self.accept_label = ""
        self.current_name = self.current_folder = ""  # SaveFile: the suggested name; any: the folder to start in
        self.multiple = self.directory = False
        self.filters = []
        self.current_filter = -1
        self.files = []  # SaveFiles: the names to save into the chosen folder
        self.choices = []  # (id, default): returned as given

    def saving(self):
        return self.method != "OpenFile"


class Result:
    def __init__(self, ok=False, paths=None, filter_=-1):
        self.ok, self.paths, self.filter = ok, list(paths or []), filter_


_mime_db = None


def _mime_globs(mime):
    global _mime_db
    _mime_db = _mime_db or QMimeDatabase()
    if mime.endswith("/*"):  # image/* and the like
        top = mime[:-1]
        return [g for t in _mime_db.allMimeTypes() if t.name().startswith(top) for g in t.globPatterns()]
    return list(_mime_db.mimeTypeForName(mime).globPatterns())


def _path(value):
    """Paths come as byte strings (ay, with a trailing NUL)."""
    return os.fsdecode(bytes(value).rstrip(b"\0")) if value else ""


def parse(method, title, options):
    """options: the a{sv} as a dict (GLib.Variant.unpack())."""
    r = Request()
    r.method, r.title = method, title
    r.accept_label = options.get("accept_label", "")
    r.multiple = bool(options.get("multiple", False))
    r.directory = bool(options.get("directory", False))
    r.current_name = options.get("current_name", "")
    r.current_folder = _path(options.get("current_folder"))
    current_file = _path(options.get("current_file"))  # saving over an existing file
    if current_file:
        r.current_folder, r.current_name = os.path.dirname(current_file), os.path.basename(current_file)
    r.filters = [Filter(name, [tuple(p) for p in pats]) for name, pats in options.get("filters", [])]
    if "current_filter" in options:
        name, pats = options["current_filter"]
        cur = Filter(name, [tuple(p) for p in pats])
        r.current_filter = next((i for i, f in enumerate(r.filters) if f.name == cur.name
                                 and f.patterns == cur.patterns), -1)
        if r.current_filter < 0:  # not one of the list: it is the only filter
            r.filters.append(cur)
            r.current_filter = len(r.filters) - 1
    if r.current_filter < 0 and r.filters:
        r.current_filter = 0
    r.choices = [(cid, default) for cid, _label, _opts, default in options.get("choices", [])]
    r.files = [os.path.basename(_path(f)) for f in options.get("files", [])]
    return r


def results(req, res):
    """The a{sv} results, as a dict of GLib.Variant."""
    GLib = util.GLib
    out = {}
    if res.ok:
        out["uris"] = GLib.Variant("as", [util.file_uri(p) for p in res.paths])
        if 0 <= res.filter < len(req.filters):
            f = req.filters[res.filter]
            out["current_filter"] = GLib.Variant("(sa(us))", (f.name, list(f.patterns)))
        if req.choices:
            out["choices"] = GLib.Variant("a(ss)", req.choices)
        if not req.saving():
            out["writable"] = GLib.Variant("b", True)
    return out


def button_text(req):
    """accept_label without its GTK mnemonic, or Open / Save / Select."""
    if req.accept_label:
        return req.accept_label.replace("__", "\x01").replace("_", "").replace("\x01", "_")  # GTK mnemonics: _Save
    if req.method == "SaveFiles" or req.directory:
        return "Select"
    return "Save" if req.saving() else "Open"


# ---------------------------------------------------------------- the D-Bus service

NAME = "org.freedesktop.impl.portal.desktop.kestrel"
PATH = "/org/freedesktop/portal/desktop"
XML = """
<node>
  <interface name="org.freedesktop.impl.portal.FileChooser">
    <method name="OpenFile">
      <arg type="o" name="handle" direction="in"/><arg type="s" name="app_id" direction="in"/>
      <arg type="s" name="parent_window" direction="in"/><arg type="s" name="title" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="u" name="response" direction="out"/><arg type="a{sv}" name="results" direction="out"/>
    </method>
    <method name="SaveFile">
      <arg type="o" name="handle" direction="in"/><arg type="s" name="app_id" direction="in"/>
      <arg type="s" name="parent_window" direction="in"/><arg type="s" name="title" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="u" name="response" direction="out"/><arg type="a{sv}" name="results" direction="out"/>
    </method>
    <method name="SaveFiles">
      <arg type="o" name="handle" direction="in"/><arg type="s" name="app_id" direction="in"/>
      <arg type="s" name="parent_window" direction="in"/><arg type="s" name="title" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="u" name="response" direction="out"/><arg type="a{sv}" name="results" direction="out"/>
    </method>
  </interface>
  <interface name="org.freedesktop.impl.portal.Request">
    <method name="Close"/>
  </interface>
</node>
"""

_service = None


class _Service:
    def __init__(self, opener):
        self.open = opener
        self.node = util.Gio.DBusNodeInfo.new_for_xml(XML)
        self.open_dialogs = 0
        self.idle = QTimer(singleShot=True, interval=60_000, timeout=QApplication.quit)
        self.idle.start()  # started by D-Bus for a request that may never come
        self.pending = set()


def serve(opener):
    """Own the portal backend's name and answer each request with opener(request, done); done(result) may be called
    once. opener returns a function that closes the dialog (the app cancelled). Quits after a minute without dialogs."""
    global _service
    Gio, GLib = util.Gio, util.GLib
    if _service is not None or not Gio:
        return
    s = _service = _Service(opener)

    def on_call(conn, _sender, _path, _iface, method, params, invocation):
        handle, _app_id, _parent, title, options = params.unpack()
        req = parse(method, title, options)
        state = {"answered": False, "close": None, "request_id": 0}
        s.pending.add(id(state))

        def on_close(_c, _s, _p, _i, _m, _params, inv):  # Request.Close: the app gave up waiting
            inv.return_value(None)
            if state["close"]:
                QTimer.singleShot(0, state["close"])
        state["request_id"] = conn.register_object(handle, s.node.interfaces[1], on_close, None, None)
        s.open_dialogs += 1
        s.idle.stop()

        def done(res):
            if state["answered"]:
                return
            state["answered"] = True
            invocation.return_value(GLib.Variant("(ua{sv})", (0 if res.ok else 1, results(req, res))))
            if state["request_id"]:
                conn.unregister_object(state["request_id"])
            s.open_dialogs -= 1
            if s.open_dialogs == 0:
                s.idle.start()
        QTimer.singleShot(0, lambda: state.__setitem__("close", s.open(req, done)))

    def on_bus(conn, _name):
        s.registration = conn.register_object(PATH, s.node.interfaces[0], on_call, None, None)

    def lost(_conn, _name):  # another Kestrel already serves it
        if s.open_dialogs == 0:
            QTimer.singleShot(0, QApplication.quit)
    s.owner = Gio.bus_own_name(Gio.BusType.SESSION, NAME, Gio.BusNameOwnerFlags.DO_NOT_QUEUE, on_bus, None, lost)


# ---------------------------------------------------------------- the chooser window's bar

class ChooserBar(QWidget):
    """The bottom of a chooser window: the file name (saving), the file type, Cancel and Save / Open."""

    def __init__(self, win, req, done):
        super().__init__()
        self.win, self.req, self.done = win, req, done
        self.finished = False
        self.name = self.filter = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        if req.method == "SaveFile":
            lay.addWidget(QLabel("Name:"))
            self.name = QLineEdit(req.current_name)
            self.name.setMinimumWidth(320)
            self.name.returnPressed.connect(self.accept)
            self.name.textChanged.connect(self._update_button)
            lay.addWidget(self.name, 1)
            # select the name without its extension, ready to type over
            dot = req.current_name.rfind(".")
            self.name.setSelection(0, dot if dot > 0 else len(req.current_name))
        elif req.method == "SaveFiles":
            n = len(req.files)
            lay.addWidget(QLabel(f"Choose a folder for {n} file{'' if n == 1 else 's'}"), 1)
        else:
            lay.addStretch(1)
        if req.filters:
            self.filter = QComboBox()
            for f in req.filters:
                self.filter.addItem(f.name)
            self.filter.setCurrentIndex(req.current_filter)
            self.filter.currentIndexChanged.connect(
                lambda _i: [p.set_type_filter(self.type_filter()) for p in self.win.panes()])
            lay.addWidget(self.filter)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(lambda: self.finish(False))
        self.ok_btn = QPushButton(button_text(req))
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self.accept)
        lay.addWidget(cancel)
        lay.addWidget(self.ok_btn)
        self._update_button()

    def type_filter(self):
        """The chosen file type's globs (empty: all files)."""
        i = self.filter.currentIndex() if self.filter else -1
        return self.req.filters[i].globs if 0 <= i < len(self.req.filters) else []

    def _selected(self):
        return self.win.pane().selected_paths() if self.win.pane() else []

    def _update_button(self, *_a):
        in_folder = bool(self.win.cur_dir())
        if self.req.method == "SaveFile":
            self.ok_btn.setEnabled(in_folder and bool(self.name.text().strip()))
        elif self.req.saving() or self.req.directory:
            self.ok_btn.setEnabled(in_folder or bool(self._selected()))
        else:
            self.ok_btn.setEnabled(bool(self._selected()))

    def selection_changed(self):
        # saving: picking a file puts its name in the box (to save over it, or to start from it)
        if self.name is not None:
            sel = self._selected()
            if len(sel) == 1 and not os.path.isdir(sel[0]):
                self.name.setText(os.path.basename(sel[0]))
        self._update_button()

    def activated(self, files):
        """Files double-clicked or opened with Enter."""
        if self.name is not None:
            self.name.setText(os.path.basename(files[0]))
            self.accept()
        elif not self.req.saving() and not self.req.directory:
            self.finish(True, files if self.req.multiple else files[:1])

    def _confirm_replace(self, existing):
        if len(existing) == 1:
            text = f"A file named “{os.path.basename(existing[0])}” already exists. Do you want to replace it?"
        else:
            text = f"{len(existing)} of these files already exist in this folder. Do you want to replace them?"
        box = QMessageBox(QMessageBox.Icon.Question, "Replace File", text, QMessageBox.StandardButton.NoButton, self)
        replace = box.addButton("Replace", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is replace

    def accept(self):
        if not self.ok_btn.isEnabled():
            return
        d = self.win.cur_dir()
        sel = self._selected()
        req = self.req
        if req.method == "SaveFile":
            n = self.name.text().strip()
            target = os.path.expanduser(n) if n.startswith(("/", "~")) else os.path.join(d, n)
            if os.path.isdir(target):  # a folder name: go into it
                self.win.navigate(target)
                self.name.clear()
                return
            if not os.path.isdir(os.path.dirname(target)):
                QMessageBox.warning(self, "Save", f"The folder “{os.path.dirname(target)}” doesn't exist.")
                return
            if os.path.lexists(target) and not self._confirm_replace([target]):
                return
            self.finish(True, [target])
        elif req.method == "SaveFiles":
            folder = sel[0] if len(sel) == 1 and os.path.isdir(sel[0]) else d
            targets = [os.path.join(folder, f) for f in req.files]
            existing = [t for t in targets if os.path.lexists(t)]
            if existing and not self._confirm_replace(existing):
                return
            self.finish(True, targets)
        elif req.directory:
            dirs = [p for p in sel if os.path.isdir(p)] or [d]
            self.finish(True, dirs if req.multiple else dirs[:1])
        else:
            files = [p for p in sel if not os.path.isdir(p)]
            if not files:  # only folders selected: go into the first
                self.win.navigate(sel[0])
                return
            self.finish(True, files if req.multiple else files[:1])

    def finish(self, ok, paths=()):
        """Once; closes the window."""
        if self.finished:
            return
        self.finished = True
        paths = list(paths)
        if ok and paths:
            folder = paths[0] if self.req.directory and not self.req.saving() else os.path.dirname(paths[0])
            self.win.settings.setValue("chooser_folder", folder)  # the next chooser starts here
        self.done(Result(ok, paths, self.filter.currentIndex() if self.filter else -1))
        self.win.close()
