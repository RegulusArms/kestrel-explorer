"""In-process measurements of the Python version, for work GNOME Files has no equivalent of (see README.md).
Usage: kestrel_py.py PROJECT_DIR TEST DATA_DIR [ARG]. Prints {"s": seconds, "n": items, "rss_mb": peak memory}.

  trash      move every file in ~/victim to the trash with the window's Move to Trash, then Empty Trash ("empty_s")
  move_xdev  move ~/movesrc to ARG (on another drive)
"""
import json
import os
import shutil
import sys
import time

project, test, D = sys.argv[1:4]
ARG = sys.argv[4] if len(sys.argv) > 4 else None
sys.path.insert(0, project)

from PyQt6.QtCore import QEventLoop, QSettings, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from kestrel import app as A, fileops, metadata, thumbs, util, widgets  # noqa: E402

QApplication.setApplicationName(util.APP_ID)
qapp = QApplication(sys.argv[:1])
A._settings = QSettings(util.APP_ID, util.APP_ID)
A._thumbs = thumbs.ThumbnailManager()
A.apply_thumb_settings(A._thumbs, A._settings)


def wait(cond, timeout=600):
    loop = QEventLoop()
    t = QTimer(timeout=lambda: cond() and loop.quit(), interval=5)
    t.start()
    QTimer.singleShot(int(timeout * 1000), loop.quit)
    loop.exec()


def peak_mb():
    with open("/proc/self/status") as f:
        return next(int(ln.split()[1]) for ln in f if ln.startswith("VmHWM")) / 1024


res = {}
t = time.monotonic()
if test in ("bulk_thumbs", "mosaics"):
    out = []
    b = thumbs.RecursiveBuilder(f"{D}/gallery" if test == "bulk_thumbs" else f"{D}/albums", 160, A._thumbs)
    b.finished_build.connect(lambda *a: out.append(a))
    b.start()
    wait(lambda: bool(out))
    b.wait()
    res["n"] = out[0][0]
elif test == "search":
    found = []
    s = widgets.SearchThread(f"{D}/tree", "*_7.jpg", False)
    s.found.connect(found.extend)
    s.start()
    wait(s.isFinished)
    qapp.processEvents()
    res["n"] = len(found)
elif test == "copy":
    dst = os.path.join(os.path.expanduser("~"), "copydst")
    done = []
    fileops.start_ops(None, [("copy", f"{D}/copysrc", dst)], "Copying", lambda: done.append(1))
    wait(lambda: bool(done))
elif test == "trash":
    home = os.path.expanduser("~")
    w = A.open_window([home])
    victim = os.path.join(home, "victim")
    paths = sorted(os.path.join(victim, n) for n in os.listdir(victim))
    t = time.monotonic()
    w.trash_paths(paths)
    wait(lambda: not w.task_panel.tasks)
    res["s"] = time.monotonic() - t
    res["n"] = len(paths) - len(os.listdir(victim))
    clicked = []

    def answer_yes():   # Empty Trash asks first: answer like a user would, and start the clock there
        m = QApplication.activeModalWidget()
        if isinstance(m, QMessageBox) and not clicked:
            clicked.append(time.monotonic())
            m.button(QMessageBox.StandardButton.Yes).click()
    closer = QTimer(timeout=answer_yes, interval=5)
    closer.start()
    w.empty_trash()
    wait(lambda: bool(clicked) and not w.task_panel.tasks)
    res["empty_s"] = time.monotonic() - clicked[0]
    trash_files = os.path.join(home, ".local/share/Trash/files")
    res["empty_left"] = len(os.listdir(trash_files)) if os.path.isdir(trash_files) else 0
elif test == "move_xdev":
    done = []
    fileops.start_ops(None, [("move", os.path.join(os.path.expanduser("~"), "movesrc"), ARG)], "Moving",
                      lambda: done.append(1))
    wait(lambda: bool(done))
elif test == "metadata":
    for f in sorted(os.listdir(f"{D}/meta")):
        metadata.basic_info(f"{D}/meta/{f}")
        metadata.ai_info(f"{D}/meta/{f}")
    for i in range(200):
        metadata.basic_info(f"{D}/gallery/img{i:04d}.jpg")
    res["n"] = 400
res.setdefault("s", time.monotonic() - t)
res["rss_mb"] = peak_mb()
if test == "copy":
    shutil.rmtree(dst)
print(json.dumps(res), flush=True)
os._exit(0)
