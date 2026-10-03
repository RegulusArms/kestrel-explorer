"""Admin session: enter your password once, then run privileged file operations without asking again.

The first privileged operation starts kestrel/admin_helper.py as root through pkexec (the system password
prompt). Kestrel itself keeps running as you; it only sends the helper individual operations over a private
pipe. The session ends when you click the 🛡 Admin indicator in the status bar and choose End, after
IDLE_MINUTES without admin activity, or when Kestrel quits.
"""
import errno
import json
import os
import queue
import shutil
import subprocess
import threading
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QMenu, QMessageBox, QToolButton

from . import fileops

IDLE_MINUTES = 15
HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin_helper.py")


class AdminError(Exception):
    pass


def is_permission_error(e):
    return isinstance(e, PermissionError) or getattr(e, "errno", None) in (errno.EACCES, errno.EPERM)


class AdminSession(QObject):
    changed = pyqtSignal(bool)   # session started / ended (emitted from any thread; receivers are queued)

    def __init__(self):
        super().__init__()
        self.proc = None
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._pending = {}
        self._next = 1
        self._busy = 0
        self.last_used = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._idle_check)
        self._timer.start(30_000)

    @staticmethod
    def available():
        return shutil.which("pkexec") is not None and os.path.exists("/usr/bin/python3")

    def active(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        """Start the root helper (blocks while the password prompt is up; call from a worker thread)."""
        with self._start_lock:
            if self.active():
                return
            pkexec = shutil.which("pkexec")
            if not pkexec:
                raise AdminError("Administrator actions need pkexec. Install it with:  sudo apt install pkexec")
            p = subprocess.Popen([pkexec, "/usr/bin/python3", HELPER], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
            try:
                hello = json.loads(p.stdout.readline() or "{}")
            except ValueError:
                hello = {}
            if not hello.get("hello"):
                rc = p.wait()
                if rc in (126, 127):
                    raise AdminError("Not authorized: the password prompt was dismissed or the password was wrong.")
                raise AdminError(p.stderr.read().strip() or f"The admin helper failed to start (exit code {rc}).")
            self.proc, self.last_used = p, time.monotonic()
            threading.Thread(target=self._reader, args=(p,), daemon=True).start()
        self.changed.emit(True)

    def _reader(self, p):
        for line in p.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            q = self._pending.get(msg.get("id"))
            if q:
                q.put(msg)
        with self._lock:
            if self.proc is p:
                self.proc = None
            waiting = list(self._pending.values())
        for q in waiting:
            q.put({"ok": False, "error": "The admin session ended."})
        self.changed.emit(False)

    def _send(self, obj):
        with self._lock:
            if not self.active():
                raise AdminError("The admin session has ended.")
            try:
                self.proc.stdin.write(json.dumps(obj) + "\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, ValueError):
                raise AdminError("The admin session has ended.") from None

    def call(self, task, op, **args):
        """Run one operation as root (call from a worker thread). Progress goes to task; Cancel is passed on.
        Raises AdminError, or fileops.Cancelled."""
        self.start()
        with self._lock:
            rid, self._next = self._next, self._next + 1
            q = self._pending[rid] = queue.Queue()
            self._busy += 1
        try:
            self._send({"id": rid, "op": op, **args})
            cancel_sent = False
            while True:
                # checked on every message: progress arrives ~10×/s, so waiting for a quiet moment would never cancel
                if task is not None and task.cancelled and not cancel_sent:
                    self._send({"op": "cancel", "id": rid})
                    cancel_sent = True
                try:
                    msg = q.get(timeout=0.2)
                except queue.Empty:
                    continue
                if "progress" in msg:
                    if task is not None:
                        done, total, text = msg["progress"]
                        task.report(done, total, text)
                    continue
                if msg.get("ok"):
                    return
                if msg.get("cancelled"):
                    raise fileops.Cancelled()
                raise AdminError(msg.get("error") or "failed")
        finally:
            with self._lock:
                self._pending.pop(rid, None)
                self._busy -= 1
                self.last_used = time.monotonic()

    def end(self):
        """Close the session: the helper exits as soon as its input closes."""
        p = self.proc
        if p and p.poll() is None:
            try:
                p.stdin.close()
            except OSError:
                pass

    def _idle_check(self):
        if self.active() and not self._busy and time.monotonic() - self.last_used > IDLE_MINUTES * 60:
            self.end()


_session = None


def session():
    """The app-wide admin session (create from the UI thread)."""
    global _session
    if _session is None:
        _session = AdminSession()
    return _session


# ---------------------------------------------------------------- UI

class Indicator(QToolButton):
    """🛡 Admin in the status bar while a session is open; click to end it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setText("🛡 Admin")
        self.setAutoRaise(True)
        self.setToolTip(f"Admin session open: administrator actions don't ask for your password again.\n"
                        f"Ends after {IDLE_MINUTES} minutes without admin actions, or when Kestrel quits.")
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QMenu(self)
        m.addAction("End Admin Session", lambda: session().end())
        self.setMenu(m)
        self.setStyleSheet("QToolButton { color: #c01c28; font-weight: bold; }")
        session().changed.connect(self.setVisible)
        self.setVisible(session().active())


def start_session(parent):
    """Open a session ahead of time (from the menu)."""
    if not session().available():
        QMessageBox.warning(parent, "Admin Session", "Administrator actions need pkexec. Install it with:\n\n"
                            "sudo apt install pkexec")
        return

    def work(_task):
        try:
            session().start()
        except AdminError as e:
            return str(e)

    def done(err):
        if err:
            QMessageBox.warning(parent, "Admin Session", err)
    fileops.run_job(parent, "Starting admin session", work, done, cancellable=False)


def retry_as_admin(parent, title, message, work, on_done=None):
    """After a permission error: run work(task) as administrator. With a session open it just runs; otherwise
    the user is asked first (and then enters their password once). work() uses session().call(); it may return
    a list of per-item error strings. on_done(ok) runs afterwards."""
    s = session()
    if not s.available():
        QMessageBox.warning(parent, title, f"{message}\n\nInstall pkexec to retry as administrator.")
        if on_done:
            on_done(False)
        return
    if not s.active():
        box = QMessageBox(QMessageBox.Icon.Warning, title,
                          f"{message}\n\nRetry as administrator? You'll be asked for your password once. The admin "
                          f"session then stays open (🛡 Admin in the status bar) until you end it or it's unused "
                          f"for {IDLE_MINUTES} minutes.", QMessageBox.StandardButton.Cancel, parent)
        retry = box.addButton("Retry as Administrator", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(retry)
        box.exec()
        if box.clickedButton() is not retry:
            if on_done:
                on_done(False)
            return

    def job(task):
        try:
            return {"errors": work(task) or []}
        except AdminError as e:
            return {"errors": [str(e)]}

    def done(res):
        errors = (res or {}).get("errors", []) if res is not None else []
        if errors:
            QMessageBox.warning(parent, title, "\n".join(errors[:20]))
        if on_done:
            on_done(res is not None and not errors)
    fileops.run_job(parent, f"{title} (as administrator)", job, done)
