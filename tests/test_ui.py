"""The window's actions, as a list: every menu entry, submenu and separator, with its shortcuts, in the main menu, the
toolbar's menus and the context menus. Compared with tests/ui_actions.txt, which is the same in the C++ version, so a
menu entry or shortcut lost when code moves (or added in one version only) shows up as a difference.
KESTREL_UI_DUMP=file writes the list there instead of comparing (to update ui_actions.txt after a deliberate change)."""
import os

from common import A, check, finish, home_path as P, setup_app, spin, wait_for
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractSlider, QComboBox, QHeaderView, QLineEdit,
                             QToolButton, QWidget)

from kestrel import util


def depends_on_computer(text):
    """What the computer has installed decides whether these appear (an email client or Bluetooth; Samba; code
    editors, "Open in Zed" and the like): not listed."""
    always = ("Open in Terminal", "Open in New Tab", "Open in New Window")
    return text in ("Send To", "Network Sharing…") or (text.startswith("Open in ") and text not in always)


def label(a):
    """The text without its mnemonic ("&&" is a real "&")."""
    return a.text().replace("&&", "\x01").replace("&", "").replace("\x01", "&")


def keys(a):
    out = [k.toString(QKeySequence.SequenceFormat.PortableText) for k in a.shortcuts()]
    return f" [{', '.join(out)}]" if out else ""


def walk(m, where, out):
    for a in m.actions():
        if a.isSeparator():
            out.append(f"{where} > ---")
            continue
        text = label(a)
        if depends_on_computer(text):
            continue
        out.append(f"{where} > {text}{keys(a)}{' (toggle)' if a.isCheckable() else ''}")
        # the apps offered depend on the computer: only the submenu itself is listed
        if a.menu() is not None and text != "Open With":
            walk(a.menu(), f"{where} > {text}", out)


app = setup_app()
home = util.HOME
os.makedirs(P("folder"), exist_ok=True)
for name, data in (("file.txt", "x"), ("other.txt", "y")):
    with open(P(name), "w") as f:
        f.write(data)
w = A.open_window([home])
wait_for(lambda: w.pane() is not None and w.pane().path == home)

out = []
for b in w.findChildren(QToolButton):
    if b.menu() is None:
        continue
    # named by its tooltip's first line (without a shortcut hint), else its text
    name = b.toolTip().split("\n")[0].split(" (")[0].strip() or b.text() or "menu"
    walk(b.menu(), f'button "{name}"', out)
out += sorted(f"shortcut > {label(a)}{keys(a)}" for a in w.actions() if a.shortcuts())
cases = [("empty space", []), ("a file", [P("file.txt")]), ("a folder", [P("folder")]),
         ("two files", [P("file.txt"), P("other.txt")])]
for name, paths in cases:
    m = w.build_menu(w.pane(), paths)
    walk(m, f"context menu, {name}", out)
    m.deleteLater()

# -- accessibility: every control has a name a screen reader can say, as the window opens and with the search bar,
# the list view and the info panel open. PyQt6 has no QAccessible, so this is Qt's rule for these controls: the
# accessible name, or a button's own text (a toolbar button's is its action's)
CONTROLS = (QAbstractButton, QLineEdit, QAbstractSlider, QComboBox, QAbstractItemView, QHeaderView)


def unnamed():
    out = []
    for wd in w.findChildren(QWidget):
        if not isinstance(wd, CONTROLS) or not wd.isVisibleTo(w):
            continue
        name = wd.accessibleName().strip() or (wd.text().replace("&", "").strip() if isinstance(wd, QAbstractButton) else "")
        if not name:
            out.append(f'{type(wd).__name__} "{wd.toolTip().split(chr(10))[0]}"')
    return out


missing = unnamed()
w.pane().start_search()
w.set_view("list")
for a in w.actions():
    if a.text() == "Info Panel" and not a.isChecked():
        a.trigger()
spin(200)
missing = list(dict.fromkeys(missing + unnamed()))
check(not missing, "every control in the window has a name a screen reader can say (also with the search bar, "
                   "the list view and the info panel open)" + (f" (no name: {', '.join(missing)})" if missing else ""))

dump = os.environ.get("KESTREL_UI_DUMP")
if dump:
    with open(dump, "w") as f:
        f.write("\n".join(out) + "\n")
    print(f"wrote {len(out)} lines to {dump}")
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_actions.txt")) as f:
    expected = [x for x in f.read().split("\n") if x]
# the window's own menus and shortcuts, then the context menus: each compared in order, naming what differs
for context in (False, True):
    want = [x for x in expected if x.startswith("context menu") == context]
    got = [x for x in out if x.startswith("context menu") == context]
    missing, extra = [x for x in want if x not in got], [x for x in got if x not in want]
    what = ("the context menus are as listed in tests/ui_actions.txt" if context
            else "the menus, toolbar menus and shortcuts are as listed in tests/ui_actions.txt")
    if got != want:
        what += (f" (missing: {' | '.join(missing[:3])}; extra: {' | '.join(extra[:3])}"
                 f"{'; the order differs' if not missing and not extra else ''})")
    check(bool(want) and got == want, what)
finish()
