"""The system's file chooser (chooser.py): the portal backend answers Open and Save requests with Kestrel windows."""
import os

from common import A, check, finish, home_path, setup_app, spin, wait_for
from gi.repository import Gio, GLib
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton, QWidget

from kestrel import chooser, util

NAME = "org.freedesktop.impl.portal.desktop.kestrel"
_client = None


def client():
    """A second connection: the portal calling the backend."""
    global _client
    if _client is None:
        addr = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
        _client = Gio.DBusConnection.new_for_address_sync(
            addr, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None)
    return _client


def call(method, options, handle="/org/freedesktop/portal/desktop/request/1_1/t"):
    r = {"got": False, "response": 99, "uris": [], "filter": ""}

    def done(conn, res):
        r["got"] = True
        try:
            response, results = conn.call_finish(res).unpack()
        except GLib.Error:
            return
        r["response"] = response
        r["uris"] = list(results.get("uris", []))
        r["filter"] = results["current_filter"][0] if "current_filter" in results else ""
    client().call(NAME, "/org/freedesktop/portal/desktop", "org.freedesktop.impl.portal.FileChooser", method,
                  GLib.Variant("(osssa{sv})", (handle, "com.example.App", "", "Test Dialog", options)),
                  GLib.VariantType("(ua{sv})"), Gio.DBusCallFlags.NONE, -1, None, done)
    return r


def folder(p):
    return GLib.Variant.new_bytestring(os.fsencode(p))


PNG_FILTER = GLib.Variant("a(sa(us))", [("Images", [(1, "image/png")]), ("Everything", [(0, "*")])])


def chooser_window():
    """The open chooser window, if any."""
    for w in QApplication.topLevelWidgets():
        if isinstance(w, A.MainWindow) and w.chooser is not None and w.isVisible():
            return w
    return None


def wait_window():
    found = []
    wait_for(lambda: found.append(chooser_window()) or found[-1] is not None)
    return found[-1]


answer = {"button": "Cancel", "seen": False}  # how to answer a question box (Replace / Cancel)


def answer_boxes():
    m = QApplication.activeModalWidget()
    if isinstance(m, QMessageBox):
        answer["seen"] = True
        for b in m.buttons():
            if b.text() == answer["button"]:
                b.click()


app = setup_app()
app.setQuitOnLastWindowClosed(False)
d = home_path("pics")
os.makedirs(os.path.join(d, "sub"), exist_ok=True)
for n in ("a.png", "b.png", "c.txt"):
    with open(os.path.join(d, n), "w") as f:
        f.write("x")


def opener(req, done):
    w = A.open_chooser(req, done)
    return lambda: chooser_window() is w and w.close()


chooser.serve(opener)
boxes = QTimer(interval=50, timeout=answer_boxes)  # question boxes are modal: answer them
boxes.start()
spin(500)  # own the name
J = os.path.join

# -- the request
req = chooser.parse("OpenFile", "t", GLib.Variant("a{sv}", {"filters": PNG_FILTER}).unpack())
check(len(req.filters) == 2 and "*.png" in req.filters[0].globs, "a MIME type filter becomes its file patterns")
anyimg = chooser.parse("OpenFile", "t", GLib.Variant("a{sv}", {
    "filters": GLib.Variant("a(sa(us))", [("Pictures", [(1, "image/*")])])}).unpack())
check("*.jpg" in anyimg.filters[0].globs and "*.png" in anyimg.filters[0].globs, "image/* stands for every image type")
req.accept_label = "_Save"
check(chooser.button_text(req) == "Save", "a GTK mnemonic is dropped from the button label")
check(chooser.x11_parent("x11:1a2b") == 0x1a2b and chooser.x11_parent("wayland:abc") == 0
      and chooser.x11_parent("") == 0 and chooser.x11_parent("x11:zz") == 0,
      "an X11 app's window id is read from the portal's handle (the chooser becomes its dialog)")

# -- saving
r = call("SaveFile", {"current_folder": folder(d), "current_name": GLib.Variant("s", "new.png")})
w = wait_window()
check(w is not None and w.cur_dir() == d and w.chooser.name.text() == "new.png",
      "Save opens a Kestrel window in the requested folder with the suggested name")
check(w is not None and w.windowTitle() == "Test Dialog", "the window has the app's title")
if w:
    w.chooser.accept()
check(wait_for(lambda: r["got"]) and r["response"] == 0 and r["uris"] == [util.file_uri(J(d, "new.png"))],
      "the chosen file goes back to the app as a file:// URI")
check(wait_for(lambda: chooser_window() is None), "the window closes after choosing")

r = call("SaveFile", {"current_folder": folder(d), "current_name": GLib.Variant("s", "a.png")})
w = wait_window()
answer.update(button="Cancel", seen=False)
if w:
    w.chooser.accept()
spin(300)
check(answer["seen"] and not r["got"] and chooser_window() is not None,
      "saving over a file asks first, and Cancel keeps the dialog open")
answer["button"] = "Replace"
if w:
    w.chooser.accept()
check(wait_for(lambda: r["got"]) and r["response"] == 0 and r["uris"] == [util.file_uri(J(d, "a.png"))],
      "...and Replace saves over it")

r = call("SaveFile", {"current_folder": folder(d)})
w = wait_window()
if w:
    w.open_paths(w.pane(), [J(d, "sub")])
    spin(200)
check(w is not None and w.cur_dir() == J(d, "sub"), "double-clicking a folder goes into it")
if w:
    w.chooser.name.setText("x.png")
    w.chooser.accept()
check(wait_for(lambda: r["got"]) and r["uris"] == [util.file_uri(J(d, "sub/x.png"))], "...and the file is saved there")
check(A._settings.value("chooser_folder") == J(d, "sub"), "the next chooser starts where the last one saved")

# -- opening
r = call("OpenFile", {"current_folder": folder(d), "filters": PNG_FILTER})
w = wait_window()
shown = []
wait_for(lambda: w is not None and len(shown.__setitem__(slice(None), w.pane().all_paths()) or shown) == 3)
check(sorted(shown) == [J(d, "a.png"), J(d, "b.png"), J(d, "sub")], "only files of the chosen type are shown, and folders stay")
if w:
    w.open_paths(w.pane(), [J(d, "b.png")])
check(wait_for(lambda: r["got"]) and r["response"] == 0 and r["uris"] == [util.file_uri(J(d, "b.png"))]
      and r["filter"] == "Images",
      "double-clicking a file chooses it, and the chosen type goes back too")

r = call("OpenFile", {"current_folder": folder(d), "multiple": GLib.Variant("b", True)})
w = wait_window()
wait_for(lambda: w is not None and len(w.pane().all_paths()) == 4)
if w:
    w.pane().select_paths([J(d, "a.png"), J(d, "c.txt")])
    w.chooser.accept()
check(wait_for(lambda: r["got"]) and r["response"] == 0
      and sorted(r["uris"]) == [util.file_uri(J(d, "a.png")), util.file_uri(J(d, "c.txt"))],
      "with multiple, every selected file is chosen")

r = call("OpenFile", {"current_folder": folder(d), "directory": GLib.Variant("b", True)})
w = wait_window()
check(w is not None and any(b.text() == "Select" for b in w.chooser.findChildren(QPushButton)),
      "a folder chooser's button says Select")
if w:
    w.chooser.accept()
check(wait_for(lambda: r["got"]) and r["uris"] == [util.file_uri(d)], "choosing a folder: the current folder")

r = call("SaveFiles", {"current_folder": folder(d),
                       "files": GLib.Variant("aay", [b"one.txt\0", b"two.txt\0"])})
w = wait_window()
if w:
    w.chooser.accept()
check(wait_for(lambda: r["got"]) and r["uris"] == [util.file_uri(J(d, "one.txt")), util.file_uri(J(d, "two.txt"))],
      "saving several files: each goes into the chosen folder")

# -- its own settings
big = QWidget()  # a main window's saved size
big.resize(1500, 1000)
A._settings.setValue("geometry", big.saveGeometry())
A._settings.setValue("grid_size", 200)
r = call("OpenFile", {"current_folder": folder(d)})
w = wait_window()
check(w is not None and w.width() < 1500 and w.pane() is not None and w.pane().grid_size == 96,
      "a chooser opens smaller than a main window, with smaller icons")
if w:
    w.pane().zoom(absolute=120)
    w.toggle_hidden(True)
    w.close()
wait_for(lambda: r["got"])
check(int(A._settings.value("grid_size")) == 200 and not A._settings.value("show_hidden", False, type=bool)
      and int(A._settings.value("chooser/grid_size")) == 120,
      "zooming and showing hidden files in a chooser leave the main windows' settings alone")
r = call("OpenFile", {"current_folder": folder(d)})
w = wait_window()
check(w is not None and w.pane() is not None and w.pane().grid_size == 120 and w.show_hidden,
      "...and the next chooser starts from its own settings")
if w:
    w.close()
wait_for(lambda: r["got"])

# -- cancelling
r = call("OpenFile", {"current_folder": folder(d)})
w = wait_window()
if w:
    w.chooser.finish(False)
check(wait_for(lambda: r["got"]) and r["response"] == 1 and not r["uris"], "Cancel answers “cancelled”")
r = call("OpenFile", {"current_folder": folder(d)})
w = wait_window()
if w:
    w.close()
check(wait_for(lambda: r["got"]) and r["response"] == 1, "closing the window answers “cancelled”")
handle = "/org/freedesktop/portal/desktop/request/1_1/closeme"
r = call("OpenFile", {"current_folder": folder(d)}, handle)
wait_window()
client().call(NAME, handle, "org.freedesktop.impl.portal.Request", "Close", None, None,
              Gio.DBusCallFlags.NONE, 3000, None, None)  # not call_sync: this thread answers it
check(wait_for(lambda: r["got"]) and r["response"] == 1 and wait_for(lambda: chooser_window() is None),
      "the app can close the dialog (Request.Close)")

finish()
