"""Undo for file operations (Ctrl+Z), shared by every window.

Recorded: moves (drag and drop, cut/paste, Move To), renames (single and batch), Move to Trash, and items created by
copy, paste, duplicate, New Folder / New File and links. Undoing a creation moves the new items to the trash, like
GNOME Files, so nothing is lost by undoing. Not undoable: permanent deletes, merges into an existing folder, archive
jobs and admin-session operations.
"""
import os

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QMessageBox

from . import fileops, util

MAX = 50


class _Signals(QObject):
    changed = pyqtSignal()


signals = _Signals()
_stack = []   # [{"kind", "label", "items"}], newest last


def record(kind, label, items):
    """kind "move": items [(src, dst)] · "rename": [(old, new)] · "trash": [original path] · "create": [path]."""
    items = list(items)
    if not items:
        return
    _stack.append({"kind": kind, "label": label, "items": items})
    del _stack[:-MAX]
    signals.changed.emit()


def label():
    """What Ctrl+Z would undo, e.g. "Move", or None."""
    return _stack[-1]["label"] if _stack else None


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
    if not _stack:
        return
    op = _stack.pop()
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
