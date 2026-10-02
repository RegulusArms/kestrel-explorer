"""File operations: threaded copy/move/delete with progress, links, shortcuts, archives."""
import os
import shutil
import stat
import subprocess
import tarfile
import zipfile

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QLabel, QMessageBox, QProgressDialog,
                             QVBoxLayout)

from . import util

CHUNK = 4 * 1024 * 1024


class Cancelled(Exception):
    pass


class OpThread(QThread):
    """Runs a list of (op, src, dst) where op in copy/move/merge_copy/merge_move/delete."""
    progress = pyqtSignal(object, object, str)   # done bytes, total bytes, current name
    failed = pyqtSignal(list)

    def __init__(self, jobs, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.cancelled = False
        self.errors = []
        self.done_bytes = 0
        self.total = 0

    def cancel(self):
        self.cancelled = True

    def _size(self, path):
        try:
            st = os.lstat(path)
        except OSError:
            return 0
        if stat.S_ISDIR(st.st_mode):
            total = 0
            for root, dirs, files in os.walk(path):
                for f in files:
                    try:
                        total += os.lstat(os.path.join(root, f)).st_size
                    except OSError:
                        pass
            return total
        return st.st_size

    def run(self):
        for op, src, dst in self.jobs:
            if op != "delete" and not (op == "move" and self._same_dev(src, dst)):
                self.total += self._size(src)
        self.total = max(self.total, 1)
        for op, src, dst in self.jobs:
            if self.cancelled:
                break
            try:
                if op == "delete":
                    self.progress.emit(self.done_bytes, self.total, os.path.basename(src))
                    if os.path.isdir(src) and not os.path.islink(src):
                        shutil.rmtree(src)
                    else:
                        os.unlink(src)
                elif op in ("move", "merge_move"):
                    self._move(src, dst, merge=op == "merge_move")
                else:
                    self._copy(src, dst, merge=op == "merge_copy")
            except Cancelled:
                break
            except Exception as e:
                self.errors.append(f"{os.path.basename(src)}: {e}")
        self.failed.emit(self.errors)

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
            return
        self._copy(src, dst, merge=merge)
        if not self.cancelled:
            self._remove(src)

    @staticmethod
    def _remove(path):
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)

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
        tmp_dst = dst
        with open(src, "rb") as fi, open(tmp_dst, "wb") as fo:
            while True:
                if self.cancelled:
                    fo.close()
                    os.unlink(tmp_dst)
                    raise Cancelled()
                buf = fi.read(CHUNK)
                if not buf:
                    break
                fo.write(buf)
                self.done_bytes += len(buf)
                self.progress.emit(self.done_bytes, self.total, name)
        shutil.copystat(src, dst)


class TaskThread(QThread):
    """Run fn() in a thread; emits result or error."""
    result = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.result.emit(self.fn())
        except Exception as e:
            self.error.emit(str(e))


_running = set()


def run_task(parent, title, fn, on_done=None, quiet=False):
    """Run fn in background with an indeterminate progress dialog (none if quiet)."""
    dlg = None
    if not quiet:
        dlg = QProgressDialog(title, None, 0, 0, parent)
        dlg.setWindowTitle(util.APP_NAME)
        dlg.setMinimumDuration(400)
        dlg.setWindowModality(Qt.WindowModality.NonModal)
    t = TaskThread(fn, parent)
    _running.add(t)

    def finish():
        if dlg:
            dlg.close()
        _running.discard(t)
        t.deleteLater()

    def ok(res):
        if on_done:
            on_done(res)

    t.result.connect(ok)
    if not quiet:
        t.error.connect(lambda msg: QMessageBox.warning(parent, title, msg))
    t.finished.connect(finish)
    t.start()
    return t


def start_ops(parent, jobs, title, on_done=None):
    if not jobs:
        return
    dlg = QProgressDialog(title, "Cancel", 0, 1000, parent)
    dlg.setWindowTitle(title)
    dlg.setMinimumDuration(500)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    dlg.setValue(0)
    t = OpThread(jobs, parent)
    _running.add(t)
    dlg.canceled.connect(t.cancel)

    def prog(done, total, name):
        dlg.setLabelText(f"{title}\n{name}\n{util.human_size(done)} of {util.human_size(total)}")
        dlg.setValue(int(done * 1000 / max(total, 1)))

    def finished(errors):
        dlg.close()
        if errors:
            QMessageBox.warning(parent, title, "Some items could not be processed:\n\n" + "\n".join(errors[:20]))
        if on_done:
            on_done()

    t.progress.connect(prog)
    t.failed.connect(finished)
    t.finished.connect(lambda: (_running.discard(t), t.deleteLater()))
    t.start()


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
    """Resolve conflicts interactively; return job list for OpThread (or None if cancelled)."""
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
