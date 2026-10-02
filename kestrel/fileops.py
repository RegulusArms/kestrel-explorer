"""File operations: threaded copy/move/delete with progress, links, shortcuts, archives."""
import os
import shutil
import stat
import subprocess
import tarfile
import time
import zipfile

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox,
                             QProgressBar, QToolButton, QVBoxLayout, QWidget)

from . import util

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

    def __init__(self, title, fn, parent=None, cancellable=True):
        super().__init__(parent)
        self.title, self.fn, self.cancellable = title, fn, cancellable
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


class TaskPanel(QWidget):
    """Status-bar widget: the running tasks' title, status and progress, with a cancel button."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.label = QLabel()
        self.bar = QProgressBar()
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

    def add(self, task):
        self.tasks.append(task)
        task.progress.connect(lambda f, text, t=task: self._progress(t, f, text))
        task.finished.connect(lambda t=task: self._finished(t))
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

    def _refresh(self):
        if not self.tasks:
            self.hide()
            return
        t = self.tasks[0]
        text = t.title + (" — cancelling…" if t.cancelled else f": {t.text}" if t.text else "")
        more = f"  (+{len(self.tasks) - 1} more)" if len(self.tasks) > 1 else ""
        self.label.setText(self.label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, 380) + more)
        self.setToolTip("\n".join(x.title + (f": {x.text}" if x.text else "") for x in self.tasks))
        if t.fraction < 0:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(t.fraction * 1000))
        self.stop.setVisible(t.cancellable)
        self.stop.setEnabled(not t.cancelled)
        self.stop.setToolTip(f"Cancel {t.title.lower()}")
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


class _Ops:
    """Runs a list of (op, src, dst) where op in copy/move/merge_copy/merge_move/delete, for a Task.
    Progress is in bytes, or in files when every job is a delete. Returns the list of error strings."""

    def __init__(self, task, jobs):
        self.task, self.jobs = task, jobs
        self.errors = []
        self.denied = []   # delete jobs blocked by files another user (e.g. root) owns: (path, owner)
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
        for op, src, dst in self.jobs:
            self.task.check()
            try:
                if op == "delete":
                    self._remove(src)
                elif op in ("move", "merge_move"):
                    self._move(src, dst, merge=op == "merge_move")
                else:
                    self._copy(src, dst, merge=op == "merge_copy")
            except Cancelled:
                raise
            except PermissionError as e:
                owner = _foreign_owner(src) if op == "delete" else None
                if owner:
                    self.denied.append((src, owner))
                else:
                    self.errors.append(f"{os.path.basename(src)}: {e.strerror or e}")
            except Exception as e:
                self.errors.append(f"{os.path.basename(src)}: {e}")
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

    def _remove_tree(self, path):
        if os.path.isdir(path) and not os.path.islink(path):
            for root, dirs, files in os.walk(path, topdown=False):
                for name in files + dirs:
                    self.task.check()
                    p = os.path.join(root, name)
                    if name in dirs and not os.path.islink(p):
                        os.rmdir(p)
                    else:
                        os.unlink(p)
                    self._count(name)
            os.rmdir(path)
        else:
            os.unlink(path)
        self._count(os.path.basename(path))

    def _count(self, name):
        if self.by_count:
            self.done += 1
            self._report(name)

    def _copy(self, src, dst, merge=False):
        if os.path.islink(src):
            if os.path.lexists(dst):
                os.unlink(dst)
            os.symlink(os.readlink(src), dst)
        elif os.path.isdir(src):
            if os.path.exists(dst) and not merge:
                self._remove(dst)
            os.makedirs(dst, exist_ok=True)
            for entry in os.scandir(src):
                self._copy(entry.path, os.path.join(dst, entry.name), merge=merge)
            shutil.copystat(src, dst, follow_symlinks=False)
        else:
            self._copy_file(src, dst)

    def _copy_file(self, src, dst):
        name = os.path.basename(src)
        with open(src, "rb") as fi, open(dst, "wb") as fo:
            while True:
                if self.task.cancelled:
                    fo.close()
                    os.unlink(dst)
                    raise Cancelled()
                buf = fi.read(CHUNK)
                if not buf:
                    break
                fo.write(buf)
                self.done += len(buf)
                self._report(name)
        shutil.copystat(src, dst)


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


def _foreign_owner(path):
    """Name of another user owning something in or above `path` that blocks deleting it, else None."""
    uid = os.getuid()
    candidates = [os.path.dirname(path), path]
    if os.path.isdir(path) and not os.path.islink(path):
        for root, dirs, files in os.walk(path):
            candidates += [os.path.join(root, n) for n in dirs + files]
            if len(candidates) > 100000:
                break
    for p in candidates:
        try:
            owner = os.lstat(p).st_uid
        except OSError:
            continue
        if owner != uid:
            try:
                import pwd
                return pwd.getpwuid(owner).pw_name
            except KeyError:
                return f"uid {owner}"
    return None


def delete_as_admin(parent, paths, on_done=None):
    """rm -rf the given paths through pkexec (the system asks for an administrator password)."""
    def work(task):
        task.report(0, 0, f"{len(paths):,} item(s) — waiting for authorization")
        r = subprocess.run(["pkexec", "rm", "-rf", "--", *paths], capture_output=True, text=True)
        if r.returncode in (126, 127):  # dialog dismissed / not authorized
            return "Not authorized: nothing was deleted."
        if r.returncode != 0:
            return r.stderr.strip() or f"rm failed (exit code {r.returncode})"
        return None

    def done(err):
        if err:
            QMessageBox.warning(parent, "Delete as Administrator", err)
        if on_done:
            on_done()
    return run_job(parent, "Deleting as administrator", work, done, cancellable=False)


def start_ops(parent, jobs, title, on_done=None):
    """Copy/move/delete jobs on a thread, with progress and Cancel in the status bar."""
    if not jobs:
        return
    ops = []

    def finished(errors):
        denied = ops[0].denied if ops else []
        if errors:
            QMessageBox.warning(parent, title, "Some items could not be processed:\n\n" + "\n".join(errors[:20]))
        if denied and shutil.which("pkexec"):
            owners = sorted({o for _, o in denied})
            names = "\n".join(f"• {os.path.basename(p)}" for p, _ in denied[:10])
            more = f"\n…and {len(denied) - 10} more" if len(denied) > 10 else ""
            box = QMessageBox(QMessageBox.Icon.Warning, title,
                              f"{len(denied)} item(s) couldn't be deleted because they contain files owned by "
                              f"{', '.join(owners)}:\n\n{names}{more}\n\n"
                              "Delete them as administrator? You'll be asked for your password.",
                              QMessageBox.StandardButton.Cancel, parent)
            admin = box.addButton("Delete as Administrator", QMessageBox.ButtonRole.DestructiveRole)
            box.exec()
            if box.clickedButton() is admin:
                delete_as_admin(parent, [p for p, _ in denied], on_done)
                return
        elif denied:
            QMessageBox.warning(parent, title, "These items contain files owned by another user and couldn't be "
                                "deleted:\n\n" + "\n".join(p for p, _ in denied[:20]))
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
        start_ops(parent, jobs, "Copying" if op == "copy" else "Moving", on_done)


# ---------------------------------------------------------------- links & shortcuts

def make_symlink(target, dest_dir, relative=False, name=None):
    name = name or os.path.basename(target.rstrip("/"))
    link = util.unique_path(dest_dir, name if os.path.dirname(target) != dest_dir else f"Link to {name}", "num")
    src = os.path.relpath(target, dest_dir) if relative else os.path.abspath(target)
    os.symlink(src, link)
    return link


def make_hardlink(target, dest_dir):
    name = os.path.basename(target)
    link = util.unique_path(dest_dir, name if os.path.dirname(target) != dest_dir else f"{name} (hard link)", "num")
    os.link(target, link)
    return link


def make_desktop_shortcut(target, dest_dir):
    """Create a freedesktop .desktop launcher pointing to target."""
    name = os.path.basename(target.rstrip("/")) or target
    is_dir = os.path.isdir(target)
    executable = os.path.isfile(target) and os.access(target, os.X_OK)
    if executable:
        body = (f"[Desktop Entry]\nType=Application\nName={name}\nExec=\"{target}\"\n"
                f"Path={os.path.dirname(target)}\nIcon=application-x-executable\nTerminal=false\n")
    else:
        mime = util.mime_for(target, is_dir)
        icon = "folder" if is_dir else mime.iconName()
        body = f"[Desktop Entry]\nType=Link\nName={name}\nURL={util.file_uri(target)}\nIcon={icon}\n"
    path = util.unique_path(dest_dir, util.split_ext(name)[0] + ".desktop", "num")
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, 0o755)
    util.mark_trusted(path)
    return path


# ---------------------------------------------------------------- archives

def compress(paths, out_path, fmt):
    base = os.path.dirname(paths[0])
    if fmt == "zip":
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            for p in paths:
                if os.path.isdir(p) and not os.path.islink(p):
                    for root, dirs, files in os.walk(p):
                        rel_root = os.path.relpath(root, base)
                        z.write(root, rel_root)
                        for f in files:
                            full = os.path.join(root, f)
                            z.write(full, os.path.relpath(full, base))
                else:
                    z.write(p, os.path.relpath(p, base))
    elif fmt == "7z":
        subprocess.run(["7z", "a", "-y", out_path] + [os.path.relpath(p, base) for p in paths],
                       cwd=base, check=True, capture_output=True)
    else:
        mode = {"tar": "w", "tar.gz": "w:gz", "tar.xz": "w:xz", "tar.bz2": "w:bz2"}[fmt]
        with tarfile.open(out_path, mode) as t:
            for p in paths:
                t.add(p, arcname=os.path.relpath(p, base))
    return out_path


def extract(archive, dest_parent):
    stem = util.split_ext(os.path.basename(archive))[0]
    if stem.endswith(".tar"):
        stem = stem[:-4]
    out = util.unique_path(dest_parent, stem, "num")
    os.makedirs(out)
    low = archive.lower()
    if low.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(out)
    elif ".tar" in low or low.endswith((".tgz", ".tbz2", ".txz")):
        with tarfile.open(archive) as t:
            t.extractall(out, filter="data")
    elif shutil.which("7z"):
        subprocess.run(["7z", "x", "-y", f"-o{out}", archive], check=True, capture_output=True)
    else:
        os.rmdir(out)
        raise RuntimeError("No tool available to extract this archive (install 7zip, or p7zip-full on older releases)")
    # collapse single top-level folder
    entries = os.listdir(out)
    if len(entries) == 1 and os.path.isdir(os.path.join(out, entries[0])):
        inner = os.path.join(out, entries[0])
        final = util.unique_path(dest_parent, entries[0], "num") if entries[0] != os.path.basename(out) else None
        if final:
            os.rename(inner, final)
            os.rmdir(out)
            out = final
    return out


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
