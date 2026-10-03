"""Preferences → "Open folders from other apps as tabs": launched Kestrels hand their folders to an open window.

This window is the open one; real `kes` programs are launched against it: this project's launcher, and the C++
version's build/kes when KES_CXX points at it (run.sh sets it if ../kes-c/build/kes exists).
"""
import os
import subprocess
import sys

from common import ROOT, A, check, finish, home_path as P, setup_app, skip, spin, start_radio, wait_for
from PyQt6.QtCore import Qt

from kestrel import atc, util


def launch(cmd, ms=6000, env=None):
    """Launch a real Kestrel; True if it exited successfully (handed off) within ms."""
    p = subprocess.Popen(cmd, env=dict(os.environ, **(env or {})), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t = 0
    while t < ms and p.poll() is None:
        spin(50)
        t += 50
    if p.poll() is None:
        p.kill()
        p.wait()
        return False
    return p.returncode == 0


app = setup_app()
for d in ("d1", "d2", "d3", "d4", "d5", "d6"):
    os.makedirs(P(d), exist_ok=True)
with open(P("d5/pick.txt"), "w") as f:
    f.write("x")
start_radio()
w = A.open_window([P("d1")])
check(wait_for(lambda: atc.radio().tower_up(), 8000), "tower up")
spin(500)


def tabs():
    return [p.path for p in w.panes()]


PY = [sys.executable, os.path.join(ROOT, "kes")]
CXX = os.environ.get("KES_CXX", "")

check(not A._settings.value("open_in_tabs", False, type=bool), "the switch is off by default")
check(not launch(PY + [P("d2")], 2500), "off: a launched Kestrel keeps its own window")
check(w.tabs.count() == 1, "off: no tab added here")
A._settings.setValue("open_in_tabs", True)
A._settings.sync()
check(launch(PY + [P("d2")]), "on: a launched Kestrel hands its folder over and exits")
check(wait_for(lambda: P("d2") in tabs()), "on: ...and it opens here as a tab")
check(w.pane() is not None and w.pane().path == P("d2"), "on: ...which becomes the current tab")
if CXX and os.access(CXX, os.X_OK):
    check(launch([CXX, P("d3")]), "on: a launched C++ Kestrel hands its folder over too")
    check(wait_for(lambda: P("d3") in tabs()), "on: ...and it opens here as a tab")
else:
    skip("the C++ version (set KES_CXX to its build/kes)")
check(not launch(PY + [P("d4")], 2500, {"CONDA_DEFAULT_ENV": "myenv"}), "on: not from an activated conda environment")
check(P("d4") not in tabs(), "on: ...so no tab for that one")
check(not launch(PY, 2500), "on: launching Kestrel without a folder still opens a new window")

check(atc.hand_off([P("d5")], [P("d5/pick.txt")]) == atc.radio().flight(),
      "a handoff with a selection is routed to the Kestrel with a window")
check(wait_for(lambda: P("d5") in tabs()), "...which opens the folder as a tab")
spin(800)
sel = [i.data(Qt.ItemDataRole.DisplayRole) for i in w.pane().view().selectionModel().selectedIndexes()]
check("pick.txt" in sel, "...with the item selected")
n = w.tabs.count()
A.handle_fm1("ShowFolders", [util.file_uri(P("d6"))], "")
check(len(A.WINDOWS) == 1 and w.tabs.count() == n + 1, "on: Show in folder opens a tab in this window")
A._settings.setValue("open_in_tabs", False)
A._settings.sync()
A.handle_fm1("ShowFolders", [util.file_uri(P("d6"))], "")
check(len(A.WINDOWS) == 2, "off: Show in folder opens a new window again")
finish()
