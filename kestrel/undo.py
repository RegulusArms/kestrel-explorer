"""Undo for file operations (Ctrl+Z), shared by this Kestrel's windows (or every Kestrel's, with Preferences → Share
undo between all Kestrel windows).

Recorded: moves (drag and drop, cut/paste, Move To), renames (single and batch), Move to Trash, and items created by
copy, paste, duplicate, New Folder / New File and links. Undoing a creation moves the new items to the trash, like
GNOME Files, so nothing is lost by undoing. Not undoable: permanent deletes, merges into an existing folder, archive
jobs and admin-session operations.
"""
import json
import os

from PyQt6.QtCore import QObject, QSettings, pyqtSignal
from PyQt6.QtWidgets import QMessageBox

from . import atc, fileops, util

MAX = 50


class _Signals(QObject):
    changed = pyqtSignal()


signals = _Signals()
_stack = []   # this Kestrel's own history: [{"kind", "label", "items"}], newest last
_shared_label = ""   # what the tower's shared history would undo


def _shared():
    """Preferences → "Share undo between all Kestrel windows": the history is kept by the tower (atc.py), so Ctrl+Z
    in any Kestrel undoes the newest action from any of them. Off (the default), or while there is no tower, each
    Kestrel undoes only what was done in it."""
    return QSettings(util.APP_ID, util.APP_ID).value("shared_undo", False, type=bool) and atc.radio().tower_up()


def _to_json(op):
    # the tower's format: {"kind", "label", "items": [[a, b], …]} (b is "" for trash and create)
    pairs = op["items"] if op["kind"] in ("move", "rename") else [(p, "") for p in op["items"]]
    return json.dumps({"kind": op["kind"], "label": op["label"], "items": [list(p) for p in pairs]},
                      separators=(",", ":"), ensure_ascii=False)


def _from_json(text):
    o = json.loads(text)
    pairs = [(str(p[0]), str(p[1])) for p in o.get("items", []) if isinstance(p, list) and len(p) == 2]
    kind = str(o.get("kind", ""))
    return {"kind": kind, "label": str(o.get("label", "")),
            "items": pairs if kind in ("move", "rename") else [a for a, _ in pairs]}


def _heard(msg):
    global _shared_label
    if msg.get("type") == "undo_changed":
        _shared_label = str(msg.get("label") or "")
        signals.changed.emit()


def _reset():
    global _shared_label
    _shared_label = ""   # a new tower starts with an empty history
    signals.changed.emit()


atc.radio().heard.connect(_heard)
atc.radio().reset.connect(_reset)


def record(kind, label, items):
    """kind "move": items [(src, dst)] · "rename": [(old, new)] · "trash": [original path] · "create": [path]."""
    items = list(items)
    if not items:
        return
    op = {"kind": kind, "label": label, "items": items}
    if _shared() and atc.radio().request("UndoPush", _to_json(op)) is not None:
        return   # the tower tells every window (undo_changed)
    _stack.append(op)
    del _stack[:-MAX]
    signals.changed.emit()


def label():
    """What Ctrl+Z would undo, e.g. "Move", or None."""
    if _shared() and _shared_label:
        return _shared_label
    return _stack[-1]["label"] if _stack else None   # also what was done here before sharing was on


def _trashed_index():
    """{original path: the most recently trashed copy of it in a trash files/ folder}."""
    newest = {}
    for path, orig in util.trashed_items():
        if not orig:
            continue
        info = util.trash_info_path(path)
        try:
            date = next((ln[13:].strip() for ln in open(info) if ln.startswith("DeletionDate=")), "")
        except OSError:
            date = ""
        if orig not in newest or date >= newest[orig][0]:
            newest[orig] = (date, path)
    return {orig: path for orig, (_, path) in newest.items()}


def undo(win):
    """Undo the newest recorded operation; progress and errors show in win's status bar."""
    got = atc.radio().request("UndoPop") if _shared() else None
    # replayed only if it's a valid entry (any program on the session bus can talk to the tower)
    if got and atc.valid_undo(atc.parse(got)):
        op = _from_json(got)
    elif _stack:
        op = _stack.pop()
    else:
        return
    signals.changed.emit()
    kind, items, title = op["kind"], op["items"], f"Undo {op['label']}"

    def done(errors):
        win.sidebar.refresh()
        if errors:
            QMessageBox.warning(win, title, "Some items couldn't be put back:\n\n" + "\n".join(errors[:20]))
        elif errors is not None:
            win.statusBar().showMessage(f"Undid: {op['label']}", 4000)

    if kind == "move":   # move each item back where it came from
        errors = [f"{os.path.basename(dst)}: no longer at {os.path.dirname(dst)}" for _, dst in items
                  if not os.path.lexists(dst)]
        errors += [f"{os.path.basename(src)}: something new is already at {src}" for src, dst in items
                   if os.path.lexists(dst) and os.path.lexists(src)]
        jobs = [("move", dst, src) for src, dst in items if os.path.lexists(dst) and not os.path.lexists(src)]
        if errors:
            QMessageBox.warning(win, title, "Some items couldn't be put back:\n\n" + "\n".join(errors[:20]))
        if jobs:
            fileops.start_ops(win, jobs, title, lambda: done([]))
        return

    def work(task):
        errors = []
        if kind == "rename":
            for old, new in reversed(items):
                task.check()
                if not os.path.lexists(new):
                    errors.append(f"{os.path.basename(new)}: no longer exists")
                elif os.path.lexists(old):
                    errors.append(f"{os.path.basename(old)}: that name is taken again")
                else:
                    try:
                        os.rename(new, old)
                    except OSError as e:
                        errors.append(f"{os.path.basename(new)}: {e}")
        elif kind == "trash":   # restore from the trash
            index = _trashed_index()
            for i, orig in enumerate(items):
                task.check()
                task.report(i, len(items), os.path.basename(orig))
                trashed = index.get(orig)
                if not trashed:
                    errors.append(f"{os.path.basename(orig)}: not in the trash any more")
                    continue
                if os.path.lexists(orig):
                    errors.append(f"{os.path.basename(orig)}: something new is already at {orig}")
                    continue
                try:
                    os.makedirs(os.path.dirname(orig), exist_ok=True)
                    info = util.trash_info_path(trashed)
                    import shutil
                    shutil.move(trashed, orig)
                    if info and os.path.exists(info):
                        os.unlink(info)
                except OSError as e:
                    errors.append(f"{os.path.basename(orig)}: {e}")
        elif kind == "create":   # move what was created to the trash
            for i, p in enumerate(items):
                task.check()
                task.report(i, len(items), os.path.basename(p))
                if not os.path.lexists(p):
                    continue
                try:
                    util.trash(p)
                except OSError as e:
                    errors.append(f"{os.path.basename(p)}: {e}")
        return errors
    fileops.run_job(win, title, work, done)
