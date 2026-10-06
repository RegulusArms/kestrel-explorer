"""File operations: threaded copy/move/delete with progress, links, shortcuts, archives."""
import itertools
import math
import os
import shutil
import signal
import stat
import subprocess
import time

from PyQt6.QtCore import QElapsedTimer, QObject, QRectF, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QPainter, QPalette
from PyQt6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox,
                             QProgressBar, QToolButton, QVBoxLayout, QWidget)

from . import atc, stats, util

CHUNK = 4 * 1024 * 1024


class Cancelled(Exception):
    pass


class Task(QThread):
    """A background job shown in its window's status bar (TaskPanel).

    fn(task) runs on the thread. It may call task.report(done, total, text) for progress (total 0 = busy,
    no percentage) and task.check() to stop early when the user cancels. Its return value goes to
    on_done; a Cancelled exception counts as a normal finish with task.was_cancelled set."""
    progress = pyqtSignal(float, str)   # fraction 0..1 or -1 for busy, status text
    result = pyqtSignal(object)
    error = pyqtSignal(str)

    _ids = itertools.count(1)

    def __init__(self, title, fn, parent=None, cancellable=True):
        super().__init__(parent)
        self.title, self.fn, self.cancellable = title, fn, cancellable
        self.id = str(next(Task._ids))   # unique in this process (for the shared task list)
        self.admin = False               # runs in this window's admin session
        self.cancelled = self.was_cancelled = False
        self.fraction, self.text = -1.0, ""
        self._last = 0.0

    def cancel(self):
        self.cancelled = True

    def check(self):
        if self.cancelled:
            raise Cancelled()

    def report(self, done, total=0, text=""):
        # at most ~12 updates a second, so thousands of tiny files can't flood the UI thread
        now = time.monotonic()
        if now - self._last < 0.08:
            return
        self._last = now
        self.progress.emit(min(done / total, 1.0) if total else -1.0, text)

    def run(self):
        try:
            res = self.fn(self)
        except Cancelled:
            self.was_cancelled, res = True, None
        except Exception as e:
            self.error.emit(str(e))
            return
        self.result.emit(res)


class TaskInfo:
    """A running task as the shared task list knows it: one of ours (local), or another Kestrel's."""
    __slots__ = ("flight", "id", "title", "text", "fraction", "cancellable", "cancelling", "admin", "local")

    def __init__(self, flight, id, title, text="", fraction=-1.0, cancellable=False, cancelling=False, admin=False,
                 local=None):
        self.flight, self.id, self.title, self.text, self.fraction = flight, id, title, text, fraction
        self.cancellable, self.cancelling, self.admin, self.local = cancellable, cancelling, admin, local


class TaskBoard(QObject):
    """Every running task: this Kestrel's (all its windows) and, through the tower (atc.py), the other Kestrels'.
    Ours are reported to the others whenever one starts or ends, and at most twice a second while they make
    progress."""
    changed = pyqtSignal()
    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        super().__init__()
        self.local = []
        self.remote = {}   # flight -> [TaskInfo]
        self.timer = QTimer(self, singleShot=True, timeout=self._publish)
        atc.radio().heard.connect(self._heard)
        atc.radio().reset.connect(self._reset)

    def add(self, task):
        self.local.append(task)
        stats.peak("tasks at once", len(self.local))
        task.progress.connect(self._progress)
        task.finished.connect(lambda t=task: self._finished(t))
        self._schedule(0)
        self.changed.emit()

    def _progress(self, *_):
        self._schedule(500)
        self.changed.emit()

    def _finished(self, task):
        if task in self.local:
            self.local.remove(task)
        self._schedule(0)
        self.changed.emit()

    def _reset(self):
        self.remote.clear()
        self.changed.emit()

    def _schedule(self, ms):
        # report soon (a task started, ended or was cancelled) or within ms (progress); never more often than that
        if not self.timer.isActive() or self.timer.remainingTime() > ms:
            self.timer.start(ms)

    def _publish(self):
        atc.announce("tasks", keep=True, tasks=[
            {"id": t.id, "title": t.title, "text": t.text, "fraction": t.fraction, "cancellable": t.cancellable,
             "cancelling": t.cancelled, "admin": t.admin} for t in self.local])

    def others(self, mine):
        """All tasks but `mine`: our other windows', then other Kestrels'."""
        me = atc.radio().flight()
        out = [TaskInfo(me, t.id, t.title, t.text, t.fraction, t.cancellable, t.cancelled, t.admin, t)
               for t in self.local if t not in mine]
        for tasks in self.remote.values():
            out += tasks
        return out

    def cancel(self, info):
        """Cancel a task from another window. Another Kestrel's is cancelled by that Kestrel (its admin session stays
        its own)."""
        if info.local is not None:
            if info.local in self.local:
                info.local.cancel()
            self._schedule(0)
        else:
            atc.announce("cancel", flight=info.flight, task=info.id)
            for t in self.remote.get(info.flight, []):
                if t.id == info.id:
                    t.cancelling = True
        self.changed.emit()

    def _heard(self, msg):
        if msg.get("own"):
            return
        kind, sender = msg.get("type"), msg.get("from")
        if kind == "tasks":
            tasks = []
            for o in msg.get("tasks") or []:
                if isinstance(o, dict):
                    tasks.append(TaskInfo(sender, str(o.get("id", "")), str(o.get("title", "")), str(o.get("text", "")),
                                          float(o.get("fraction", -1.0)), bool(o.get("cancellable")),
                                          bool(o.get("cancelling")), bool(o.get("admin"))))
            if tasks:
                self.remote[sender] = tasks
            else:
                self.remote.pop(sender, None)
            self.changed.emit()
        elif kind == "left":
            if self.remote.pop(sender, None) is not None:
                self.changed.emit()
        elif kind == "cancel" and msg.get("flight") == atc.radio().flight():
            for t in self.local:
                if t.id == msg.get("task") and t.cancellable:
                    t.cancel()
            self._schedule(0)
            self.changed.emit()


class PulseBar(QProgressBar):
    """The task panel's bar. Busy (no percentage): a block glides back and forth, so a long wait doesn't look stuck
    (styles draw busy bars differently, some barely moving)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pulsing = False
        self._clock = QElapsedTimer()
        self._frame = QTimer(self, interval=16, timeout=self.update)

    def set_busy(self, on):
        if on == self.pulsing:
            return
        self.pulsing = on
        if on:
            self._clock.start()
            self._frame.start()
        else:
            self._frame.stop()
        self.update()

    def paintEvent(self, ev):
        if not self.pulsing:
            super().paintEvent(ev)
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(self.palette().color(QPalette.ColorRole.Mid))
        p.setBrush(self.palette().color(QPalette.ColorRole.Base))
        p.drawRoundedRect(r, 3, 3)
        # there and back every 2.4 s, slowing at the ends
        t = (self._clock.elapsed() % 2400) / 2400
        x = 0.5 - 0.5 * math.cos(2 * math.pi * t)
        w = r.width() * 0.3
        block = QRectF(r.x() + 1.5 + x * (r.width() - 3 - w), r.y() + 1.5, w, r.height() - 3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.palette().color(QPalette.ColorRole.Highlight))
        p.drawRoundedRect(block, 2, 2)
        p.end()


class TaskPanel(QWidget):
    """Status-bar widget: the running tasks' title, status and progress, with a cancel button. This window's tasks
    come first; tasks running in other windows (and other Kestrels) are counted after them, or shown when there are
    none here."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.label = QLabel()
        self.bar = PulseBar()
        self.bar.setFixedWidth(200)
        self.bar.setMaximumHeight(16)
        self.bar.setRange(0, 1000)
        self.stop = QToolButton()
        self.stop.setText("✕")
        self.stop.setAutoRaise(True)
        self.stop.clicked.connect(self._cancel)
        for w in (self.label, self.bar, self.stop):
            lay.addWidget(w)
        self.tasks = []
        self.hide()
        TaskBoard.instance().changed.connect(self._refresh)

    def add(self, task):
        self.tasks.append(task)
        task.progress.connect(lambda f, text, t=task: self._progress(t, f, text))
        task.finished.connect(lambda t=task: self._finished(t))
        TaskBoard.instance().add(task)
        self._refresh()

    def _progress(self, task, fraction, text):
        task.fraction, task.text = fraction, text
        if self.tasks and task is self.tasks[0]:
            self._refresh()

    def _finished(self, task):
        if task in self.tasks:
            self.tasks.remove(task)
        self._refresh()

    def _cancel(self):
        if self.tasks:
            self.tasks[0].cancel()
            self._refresh()
            return
        others = TaskBoard.instance().others(self.tasks)
        if others:
            TaskBoard.instance().cancel(others[0])

    def _refresh(self):
        others = TaskBoard.instance().others(self.tasks)
        if not self.tasks and not others:
            self.hide()
            return

        def line(t):
            return ("🛡 " if t.admin else "") + t.title + (f": {t.text}" if t.text else "")
        where = ""
        if self.tasks:
            t = self.tasks[0]
            first = TaskInfo("", t.id, t.title, t.text, t.fraction, t.cancellable, t.cancelled, False, t)
        else:
            first = others.pop(0)
            where = " (in another window)"
        shown = ("🛡 " if first.admin and first.local is None else "") + first.title
        text = shown + where + (" — cancelling…" if first.cancelling else f": {first.text}" if first.text else "")
        more = []
        if len(self.tasks) > 1:
            more.append(f"+{len(self.tasks) - 1} more")
        if others:
            more.append(f"+{len(others)} in other windows")
        self.label.setText(self.label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, 380)
                           + (f"  ({', '.join(more)})" if more else ""))
        tips = [x.title + (f": {x.text}" if x.text else "") for x in self.tasks]
        if where:
            tips.append(line(first) + " — in another window")
        tips += [line(x) + " — in another window" for x in others]
        self.setToolTip("\n".join(tips))
        self.bar.set_busy(first.fraction < 0)
        if first.fraction >= 0:
            self.bar.setValue(int(first.fraction * 1000))
        self.stop.setVisible(first.cancellable)
        self.stop.setEnabled(not first.cancelling)
        self.stop.setToolTip(f"Cancel {first.title.lower()}{where}")
        self.show()


def _panel_for(widget):
    """The TaskPanel of the main window that `widget` belongs to (or the active/first main window's)."""
    w = widget
    while w is not None:
        if getattr(w, "task_panel", None) is not None:
            return w.task_panel
        w = w.parentWidget()
    active = QApplication.activeWindow()
    if getattr(active, "task_panel", None) is not None:
        return active.task_panel
    return next((w.task_panel for w in QApplication.topLevelWidgets() if getattr(w, "task_panel", None)), None)


_running = set()


def run_job(parent, title, fn, on_done=None, cancellable=True, quiet=False):
    """Run fn(task) on a thread, shown in the status bar unless quiet. on_done(result) runs on the UI thread
    (also after a cancel, with result None). Errors are shown in a message box unless quiet."""
    t = Task(title, fn, parent, cancellable)
    _running.add(t)
    if on_done:
        t.result.connect(on_done)
    if not quiet:
        t.error.connect(lambda msg: QMessageBox.warning(parent, title, msg))
        panel = _panel_for(parent)
        if panel is not None:
            panel.add(t)
    t.finished.connect(lambda: (_running.discard(t), t.deleteLater()))
    t.start()
    return t


def run_task(parent, title, fn, on_done=None, quiet=False):
    """Run fn() (no arguments) on a thread, shown as a busy item in the status bar unless quiet."""
    return run_job(parent, title, lambda _task: fn(), on_done, cancellable=False, quiet=quiet)


# Within a tree, copies and deletes work through open folders, by name, never by path again: each folder is opened
# without following a symlink and must still be the folder that was listed, so a folder swapped for a symlink while the
# job runs (in /tmp, a shared folder, a USB drive) stops the job instead of leading it into the symlink's target. The
# path you chose itself, symlinked folders on the way included, is used as it is. A delete doesn't cross into another
# drive mounted inside the tree.
def _call(fn, shown, *args, **kw):
    """fn(*args), its error naming `shown` (the path), not the bare name it was given."""
    try:
        return fn(*args, **kw)
    except OSError as e:
        raise type(e)(e.errno, e.strerror, shown) from None


def _open_parent(path):
    """The folder a path's last part is in."""
    d = os.path.dirname(path) or "/"
    return _call(os.open, d, d, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)


def _lstat_at(dirfd, name, shown, missing_ok=False):
    try:
        return os.stat(name, dir_fd=dirfd, follow_symlinks=False)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise FileNotFoundError(2, "No such file or directory", shown) from None


def _open_dir_at(dirfd, name, st, shown):
    fd = _call(os.open, shown, name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dirfd)
    now = os.fstat(fd)
    if (now.st_dev, now.st_ino) != (st.st_dev, st.st_ino):
        os.close(fd)
        raise RuntimeError(f"{shown} changed while it was being worked on")
    return fd


def _names_in(dirfd, shown):
    """The names in an open folder."""
    return _call(os.listdir, shown, dirfd)


def _copy_times_mode(fd, st):
    """On a descriptor: never through a symlink."""
    try:
        os.utime(fd, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.chmod(fd, stat.S_IMODE(st.st_mode))
    except OSError:
        pass


class _Ops:
    """Runs a list of (op, src, dst) where op in copy/move/merge_copy/merge_move/delete, for a Task.
    Progress is in bytes, or in files when every job is a delete. Returns the list of error strings."""

    def __init__(self, task, jobs):
        self.task, self.jobs = task, jobs
        self.errors = []
        self.denied = []   # jobs that failed for lack of permission, to retry as administrator: (op, src, dst)
        self.completed = []   # jobs that succeeded (for undo)
        self.done = self.total = 0
        self.by_count = all(op == "delete" for op, _, _ in jobs)

    def _report(self, name):
        if self.by_count:
            text = f"{self.done:,} of {self.total:,} — {name}"
        else:
            text = f"{util.human_size(self.done)} of {util.human_size(self.total)} — {name}"
        self.task.report(self.done, self.total, text)

    def _size(self, path):
        """Bytes under path, or file count when counting deletes."""
        try:
            st = os.lstat(path)
        except OSError:
            return 0
        if not stat.S_ISDIR(st.st_mode):
            return 1 if self.by_count else st.st_size
        total = 1 if self.by_count else 0
        for root, dirs, files in os.walk(path):
            self.task.check()
            if self.by_count:
                total += len(files) + len(dirs)
                continue
            for f in files:
                try:
                    total += os.lstat(os.path.join(root, f)).st_size
                except OSError:
                    pass
        return total

    def run(self):
        self.task.report(0, 0, "Counting…")
        for op, src, dst in self.jobs:
            if op == "delete" and not self.by_count:
                continue
            if not (op == "move" and self._same_dev(src, dst)):
                self.total += self._size(src)
        self.total = max(self.total, 1)
        started = stats.now_ms()
        for op, src, dst in self.jobs:
            self.task.check()
            try:
                if op == "delete":
                    self._remove(src)
                elif op in ("move", "merge_move"):
                    self._move(src, dst, merge=op == "merge_move")
                else:
                    self._copy(src, dst, merge=op == "merge_copy")
                self.completed.append((op, src, dst))
            except Cancelled:
                raise
            except PermissionError:
                self.denied.append((op, src, dst))
            except Exception as e:
                self.errors.append(f"{os.path.basename(src)}: {e}")
        ms = stats.now_ms() - started
        if not self.by_count and self.done > 0 and ms > 0:
            stats.sample("copy speed (MB/s)", self.done / 1e6 / (ms / 1000))
        return self.errors

    @staticmethod
    def _same_dev(src, dst):
        try:
            return os.lstat(src).st_dev == os.stat(os.path.dirname(dst)).st_dev
        except OSError:
            return False

    def _move(self, src, dst, merge=False):
        if not merge and self._same_dev(src, dst):
            if os.path.lexists(dst):
                self._remove(dst)
            os.rename(src, dst)
            self._report(os.path.basename(src))
            return
        self._copy(src, dst, merge=merge)
        self._remove(src)

    def _remove(self, path):
        """Delete a file or tree one entry at a time, so it can report progress and be cancelled.
        Read-only folders you own (common in extracted Windows archives) are made writable and retried."""
        try:
            self._remove_tree(path)
        except OSError:
            if not (os.path.isdir(path) and not os.path.islink(path)) or not _make_writable(path):
                raise
            self._remove_tree(path)

    # The tree is worked on through open folders, by name (see "Within a tree" above the class)
    def _remove_tree(self, path):
        st = os.lstat(path)
        if stat.S_ISDIR(st.st_mode):
            parent = _open_parent(path)
            try:
                self._remove_at(parent, os.path.basename(path), st, path, st.st_dev)
            finally:
                os.close(parent)
        else:
            os.unlink(path)
        self._count(os.path.basename(path))

    def _remove_at(self, dirfd, name, st, shown, dev):
        if stat.S_ISDIR(st.st_mode):
            if st.st_dev != dev:
                raise RuntimeError(f"{shown} is on another drive (a mount point); not deleting it")
            d = _open_dir_at(dirfd, name, st, shown)
            try:
                for e in _names_in(d, shown):
                    self.task.check()
                    p = os.path.join(shown, e)
                    self._remove_at(d, e, _lstat_at(d, e, p), p, dev)
                    self._count(e)
            finally:
                os.close(d)
            _call(os.rmdir, shown, name, dir_fd=dirfd)
        else:  # a file or a symlink: the name itself
            _call(os.unlink, shown, name, dir_fd=dirfd)

    def _count(self, name):
        if self.by_count:
            self.done += 1
            self._report(name)

    def _copy(self, src, dst, merge=False):
        st = os.lstat(src)
        sp, dp = _open_parent(src), _open_parent(dst)
        try:
            self._copy_at(sp, os.path.basename(src), st, src, dp, os.path.basename(dst), dst, merge)
        finally:
            os.close(sp)
            os.close(dp)

    def _copy_at(self, sdir, sname, st, src, ddir, dname, dst, merge):
        dst_st = _lstat_at(ddir, dname, dst, missing_ok=True)
        if dst_st is not None and stat.S_ISDIR(dst_st.st_mode) and not stat.S_ISDIR(st.st_mode):
            raise RuntimeError(f"{dst} is a folder")
        if stat.S_ISLNK(st.st_mode):
            target = _call(os.readlink, src, sname, dir_fd=sdir)
            if dst_st is not None:
                _call(os.unlink, dst, dname, dir_fd=ddir)
            _call(os.symlink, dst, target, dname, dir_fd=ddir)
        elif stat.S_ISDIR(st.st_mode):
            in_fd = _open_dir_at(sdir, sname, st, src)
            try:
                if dst_st is not None and not merge:
                    self._remove_at_or_retry(ddir, dname, dst_st, dst)
                    dst_st = None
                elif dst_st is not None and not stat.S_ISDIR(dst_st.st_mode):
                    raise RuntimeError(f"{dst} exists and isn't a folder")
                if dst_st is None:
                    _call(os.mkdir, dst, dname, 0o700, dir_fd=ddir)
                out_fd = _open_dir_at(ddir, dname, _lstat_at(ddir, dname, dst), dst)
                try:
                    for e in _names_in(in_fd, src):
                        self._copy_at(in_fd, e, _lstat_at(in_fd, e, os.path.join(src, e)), os.path.join(src, e),
                                      out_fd, e, os.path.join(dst, e), merge)
                    _copy_times_mode(out_fd, st)
                finally:
                    os.close(out_fd)
            finally:
                os.close(in_fd)
        elif stat.S_ISREG(st.st_mode):
            self._copy_file_at(sdir, sname, st, src, ddir, dname, dst,
                               dst_st is not None and stat.S_ISLNK(dst_st.st_mode))
        else:
            raise RuntimeError(f"{src} isn't a regular file, folder or link")

    def _copy_file_at(self, sdir, sname, st, src, ddir, dname, dst, dst_is_link):
        name = os.path.basename(src)
        in_fd = _call(os.open, src, sname, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=sdir)
        try:
            now = os.fstat(in_fd)
            if (now.st_dev, now.st_ino) != (st.st_dev, st.st_ino):
                raise RuntimeError(f"{src} changed while it was being copied")
            # a symlink in the way is replaced, never written through; an existing file is overwritten in place
            if dst_is_link:
                _call(os.unlink, dst, dname, dir_fd=ddir)
            out_fd = _call(os.open, dst, dname, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC,
                           0o666, dir_fd=ddir)
            try:
                while True:
                    if self.task.cancelled:
                        os.close(out_fd)
                        out_fd = -1
                        os.unlink(dname, dir_fd=ddir)
                        raise Cancelled()
                    buf = os.read(in_fd, CHUNK)
                    if not buf:
                        break
                    view = memoryview(buf)
                    while view:
                        view = view[os.write(out_fd, view):]
                    self.done += len(buf)
                    self._report(name)
                _copy_times_mode(out_fd, st)
            finally:
                if out_fd >= 0:
                    os.close(out_fd)
        finally:
            os.close(in_fd)

    def _remove_at_or_retry(self, dirfd, name, st, shown):
        """A folder in the way of a copy: deleted, with the same retry for read-only folders as _remove()."""
        try:
            self._remove_at(dirfd, name, st, shown, st.st_dev)
        except OSError:
            if not _make_writable(shown):
                raise
            self._remove_at(dirfd, name, st, shown, st.st_dev)


def _make_writable(path):
    """Give the owner rwx on every folder under path that this user owns (so its entries can be deleted).
    Returns True if anything changed."""
    uid, changed = os.getuid(), False
    for root, dirs, _files in os.walk(path):
        for d in [root] + [os.path.join(root, x) for x in dirs]:
            try:
                st = os.lstat(d)
                if st.st_uid == uid and not stat.S_ISLNK(st.st_mode) and (st.st_mode & 0o700) != 0o700:
                    os.chmod(d, st.st_mode | 0o700)
                    changed = True
            except OSError:
                pass
    return changed


_VERBS = {"delete": "deleted", "copy": "copied", "merge_copy": "copied", "move": "moved", "merge_move": "moved"}


def retry_denied_as_admin(parent, title, denied, on_done=None):
    """Offer to redo copy/move/delete jobs that failed with "permission denied" in the admin session."""
    from . import admin
    verbs = sorted({_VERBS[op] for op, _, _ in denied})
    names = "\n".join(f"• {os.path.basename(src)}" for _, src, _ in denied[:10])
    more = f"\n…and {len(denied) - 10} more" if len(denied) > 10 else ""
    message = (f"{len(denied)} item(s) couldn't be {' or '.join(verbs)} because you don't have permission:"
               f"\n\n{names}{more}")

    def work(task):
        errors = []
        for op, src, dst in denied:
            task.check()
            try:
                if op == "delete":
                    admin.session().call(task, "delete", path=src)
                else:
                    admin.session().call(task, "copy" if "copy" in op else "move", src=src, dst=dst,
                                         merge=op.startswith("merge"))
            except admin.AdminError as e:
                errors.append(f"{os.path.basename(src)}: {e}")
        return errors
    admin.retry_as_admin(parent, title, message, work, lambda _ok: on_done and on_done())


def start_ops(parent, jobs, title, on_done=None, undo_label=None):
    """Copy/move/delete jobs on a thread, with progress and Cancel in the status bar. Jobs that fail for lack
    of permission can be retried as administrator. With undo_label, the moves and copies that succeed (also when
    cancelled part-way) can be undone with Ctrl+Z; merges into existing folders can't."""
    if not jobs:
        return
    ops = []

    def finished(errors):
        denied = ops[0].denied if ops else []
        if undo_label and ops:
            from . import undo
            done = ops[0].completed
            moves = [(src, dst) for op, src, dst in done if op == "move"]
            copies = [dst for op, src, dst in done if op == "copy"]
            if moves:
                undo.record("move", undo_label, moves)
            elif copies:
                undo.record("create", undo_label, copies)
        if errors:
            QMessageBox.warning(parent, title, "Some items could not be processed:\n\n" + "\n".join(errors[:20]))
        if denied and errors is not None:
            retry_denied_as_admin(parent, title, denied, on_done)
            return
        if on_done:
            on_done()

    def work(task):
        ops.append(_Ops(task, jobs))
        return ops[0].run()
    return run_job(parent, title, work, finished)


class ConflictDialog(QDialog):
    def __init__(self, parent, dst, is_dir):
        super().__init__(parent)
        self.setWindowTitle("File conflict")
        lay = QVBoxLayout(self)
        kind = "folder" if is_dir else "file"
        lay.addWidget(QLabel(f"A {kind} named “{os.path.basename(dst)}” already exists in\n{os.path.dirname(dst)}"))
        self.all_box = QCheckBox("Apply this action to all conflicts")
        lay.addWidget(self.all_box)
        bb = QDialogButtonBox()
        self.choice = "skip"
        for label, val in ((("Merge" if is_dir else "Replace"), "replace"), ("Keep Both", "both"), ("Skip", "skip")):
            b = bb.addButton(label, QDialogButtonBox.ButtonRole.AcceptRole)
            b.clicked.connect(lambda _=False, v=val: self._pick(v))
        cancel = bb.addButton(QDialogButtonBox.StandardButton.Cancel)
        cancel.clicked.connect(lambda: self._pick("cancel"))
        lay.addWidget(bb)

    def _pick(self, v):
        self.choice = v
        self.accept()


def plan_transfer(parent, sources, dest_dir, op):
    """Resolve conflicts interactively; return job list for start_ops (or None if cancelled)."""
    jobs, apply_all = [], None
    dest_real = os.path.realpath(dest_dir)
    for src in sources:
        name = os.path.basename(src.rstrip("/"))
        src_parent = os.path.dirname(os.path.abspath(src))
        if os.path.isdir(src) and not os.path.islink(src):
            sr = os.path.realpath(src)
            if dest_real == sr or dest_real.startswith(sr + os.sep):
                QMessageBox.warning(parent, "Cannot transfer", f"Cannot {op} “{name}” into itself.")
                continue
        if os.path.realpath(src_parent) == dest_real:
            if op == "move":
                continue
            jobs.append((op, src, util.unique_path(dest_dir, name)))
            continue
        dst = os.path.join(dest_dir, name)
        if os.path.lexists(dst):
            choice = apply_all
            if choice is None:
                dlg = ConflictDialog(parent, dst, os.path.isdir(dst))
                dlg.exec()
                choice = dlg.choice
                if choice == "cancel":
                    return None
                if dlg.all_box.isChecked():
                    apply_all = choice
            if choice == "skip":
                continue
            if choice == "both":
                dst = util.unique_path(dest_dir, name, style="num")
                jobs.append((op, src, dst))
            else:
                merge = os.path.isdir(dst) and os.path.isdir(src)
                jobs.append((("merge_" + op) if merge else op, src, dst))
        else:
            jobs.append((op, src, dst))
    return jobs


def transfer(parent, sources, dest_dir, op, on_done=None):
    jobs = plan_transfer(parent, sources, dest_dir, op)
    if jobs:
        start_ops(parent, jobs, "Copying" if op == "copy" else "Moving", on_done,
                  undo_label="Copy" if op == "copy" else "Move")


# ---------------------------------------------------------------- links & shortcuts

def link_plan(kind, target, dest_dir):
    """What creating a link of `kind` ("sym", "rel", "hard" or "desktop") to target in dest_dir means, as an
    admin-helper request: {"op": "symlink" | "hardlink" | "write", ...}. The name is made unique."""
    name = os.path.basename(target.rstrip("/")) or target
    same_dir = os.path.dirname(target) == dest_dir
    if kind in ("sym", "rel"):
        link = util.unique_path(dest_dir, f"Link to {name}" if same_dir else name, "num")
        src = os.path.relpath(target, dest_dir) if kind == "rel" else os.path.abspath(target)
        return {"op": "symlink", "target": src, "link": link}
    if kind == "hard":
        link = util.unique_path(dest_dir, f"{name} (hard link)" if same_dir else name, "num")
        return {"op": "hardlink", "target": os.path.abspath(target), "link": link}
    # a freedesktop .desktop launcher pointing to target
    is_dir = os.path.isdir(target)
    if os.path.isfile(target) and os.access(target, os.X_OK):
        body = (f"[Desktop Entry]\nType=Application\nName={name}\nExec=\"{target}\"\n"
                f"Path={os.path.dirname(target)}\nIcon=application-x-executable\nTerminal=false\n")
    else:
        icon = "folder" if is_dir else util.mime_for(target, is_dir).iconName()
        body = f"[Desktop Entry]\nType=Link\nName={name}\nURL={util.file_uri(target)}\nIcon={icon}\n"
    path = util.unique_path(dest_dir, util.split_ext(name)[0] + ".desktop", "num")
    return {"op": "write", "path": path, "text": body, "mode": 0o755}


def plan_path(plan):
    return plan.get("link") or plan.get("path")


def make_link(plan):
    """Carry out a link_plan() as the current user. Returns the created path."""
    if plan["op"] == "symlink":
        os.symlink(plan["target"], plan["link"])
    elif plan["op"] == "hardlink":
        os.link(plan["target"], plan["link"])
    else:
        with open(plan["path"], "x") as f:
            f.write(plan["text"])
        os.chmod(plan["path"], plan["mode"])
        util.mark_trusted(plan["path"])
    return plan_path(plan)


# ---------------------------------------------------------------- misc

def trash_contents():
    """What emptying the trash removes: every entry in the trash folders on every drive (files, info and expunged)."""
    out = []
    for root in util.trash_dirs():
        for sub in ("files", "info", "expunged"):
            d = os.path.join(root, sub)
            if os.path.isdir(d):
                try:
                    out += [os.path.join(d, n) for n in os.listdir(d)]
                except OSError:
                    pass
    return out


def can_shred():
    """BleachBit is installed (shred)."""
    return shutil.which("bleachbit") is not None


def _still_there(paths):
    return [p for p in paths if os.path.lexists(p)]


def shred(parent, paths, title, on_done=None):
    """Shred with BleachBit (`bleachbit --shred`): files and folders are overwritten, then deleted, so they can't be
    recovered. A background task; BleachBit reports success either way, so on_done(left) gets the paths still there
    afterwards (none: all shredded). Not called after a cancel."""
    paths = list(paths)

    def work(task):
        total = len(paths)
        task.report(0, total, "Starting BleachBit…")
        # it lists every file; what's left afterwards is checked instead
        p = subprocess.Popen(["bleachbit", "--shred", "--"] + paths, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            while True:
                try:
                    p.wait(0.25)
                    break
                except subprocess.TimeoutExpired:
                    task.check()
                    n = total - len(_still_there(paths))
                    task.report(n, total, f"{n:,} of {total:,} shredded")
        except Cancelled:
            try:
                os.killpg(p.pid, signal.SIGKILL)   # with anything it started
            except ProcessLookupError:
                pass
            p.wait()
            raise
        return _still_there(paths)

    def done(left):
        if left is not None and on_done:
            on_done(left)
    return run_job(parent, title, work, done)


def dir_stats(path, cancel=lambda: False):
    """(total bytes, file count, dir count) recursively, not following symlinks."""
    size = files = dirs = 0
    stack = [path]
    while stack:
        if cancel():
            break
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            dirs += 1
                            stack.append(e.path)
                        else:
                            files += 1
                            size += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return size, files, dirs


# ---------------------------------------------------------------- local copies of device files

def fetch_local(parent, paths, on_done):
    """Copy files from a device (util.needs_local_copy) into ~/.cache/kestrel-explorer/device-files, as a busy task
    ("will launch once ready") with Cancel, then call on_done(local paths) on the UI thread (not after a cancel or an error). A copy is
    reused while its size matches the file's; copies not opened for a day are deleted."""
    import threading
    Gio, GLib = util.Gio, util.GLib
    root = util.APP_CACHE / "device-files"
    items = []
    for p in paths:
        uri = util.device_uri(p)  # main thread (volume monitor); the copy then talks to gvfs directly
        items.append((p, uri, str(root / util.md5(uri or p) / os.path.basename(p))))

    def fn(task):
        keep = {os.path.dirname(dst) for _p, _u, dst in items}
        # drop the copies nobody has opened for a day
        try:
            for n in os.listdir(root):
                d = str(root / n)
                if d not in keep and time.time() - os.stat(d).st_mtime > 86400:
                    shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass
        srcs, sizes = [], []
        for p, uri, _dst in items:
            f = Gio.File.new_for_uri(uri) if uri else Gio.File.new_for_path(p)
            try:
                size = f.query_info(Gio.FILE_ATTRIBUTE_STANDARD_SIZE, Gio.FileQueryInfoFlags.NONE, None).get_size()
            except GLib.Error:
                size = -1
            srcs.append(f)
            sizes.append(size)
        out = []
        for (p, _uri, dst), src, size in zip(items, srcs, sizes):
            name = os.path.basename(p)
            try:
                if os.stat(dst).st_size == size:  # copied before
                    os.utime(os.path.dirname(dst))
                    out.append(dst)
                    continue
            except OSError:
                pass
            task.check()
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            part = dst + ".part"
            # the device sends the whole file before the copy starts, so a cancel has to interrupt the wait
            cancel = Gio.Cancellable()
            finished = threading.Event()

            def watch():
                while not finished.wait(0.1):
                    if task.cancelled:
                        cancel.cancel()
                        return
            watcher = threading.Thread(target=watch, daemon=True)
            watcher.start()
            # busy, not a percentage: the device sends nothing until it has the whole file, then it arrives at once
            task.report(0, 0, f"{name} — will launch once ready")
            try:
                src.copy(Gio.File.new_for_path(part), Gio.FileCopyFlags.OVERWRITE, cancel, None)
            except GLib.Error as e:
                cancelled = e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED)
                try:
                    os.unlink(part)
                except OSError:
                    pass
                if cancelled or task.cancelled:
                    raise Cancelled()
                raise OSError(f"Could not copy {name} from the device: {e.message}")
            finally:
                finished.set()
                watcher.join()
            os.rename(part, dst)
            out.append(dst)
        return out

    run_job(parent, "Loading from device", fn, lambda res: res is not None and on_done(res))
