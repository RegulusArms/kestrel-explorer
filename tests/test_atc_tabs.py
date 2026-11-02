"""Preferences → "Open folders from other apps as tabs": launched Kestrels hand their folders to an open window.

This window is the open one; real `kes` programs (this project's launcher) are launched against it.
"""
import os
import subprocess
import sys

from common import ROOT, A, check, finish, home_path as P, setup_app, spin, start_radio, wait_for
from PyQt6.QtCore import Qt

from kestrel import atc, util


def launch(cmd, ms=6000, env=None):
    """What a launched Kestrel did within ms: "handed off" its folder and exited successfully, was still running ("own
    window": it kept its own window), or "failed" (exited with an error or crashed: neither of the others)."""
    p = subprocess.Popen(cmd, env=dict(os.environ, **(env or {})), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t = 0
    while t < ms and p.poll() is None:
        spin(50)
        t += 50
    if p.poll() is None:
        p.kill()
        p.wait()
        return "own window"
    return "handed off" if p.returncode == 0 else "failed"


app = setup_app()
for d in ("d1", "d2", "d3", "d4", "d5", "d6"):
    os.makedirs(P(d), exist_ok=True)
with open(P("d5/pick.txt"), "w") as f:
    f.write("x")
with open(P("d6/item.txt"), "w") as f:
    f.write("x")
start_radio()
w = A.open_window([P("d1")])
check(wait_for(lambda: atc.radio().tower_up(), 8000), "tower up")
spin(500)


def tabs():
    return [p.path for p in w.panes()]


PY = [sys.executable, os.path.join(ROOT, "kes")]

check(not A._settings.value("open_in_tabs", False, type=bool), "the switch is off by default")
check(launch(PY + [P("d2")], 2500) == "own window", "off: a launched Kestrel keeps its own window")
check(w.tabs.count() == 1, "off: no tab added here")
A._settings.setValue("open_in_tabs", True)
A._settings.sync()
check(launch(PY + [P("d2")]) == "handed off", "on: a launched Kestrel hands its folder over and exits")
check(wait_for(lambda: P("d2") in tabs()), "on: ...and it opens here as a tab")
check(w.pane() is not None and w.pane().path == P("d2"), "on: ...which becomes the current tab")
check(launch(PY + [P("d3")]) == "handed off", "on: a second launched Kestrel hands its folder over too")
check(wait_for(lambda: P("d3") in tabs() and P("d2") in tabs()), "on: ...and it opens here as another tab")
check(launch(PY + [P("d4")], 2500, {"CONDA_DEFAULT_ENV": "myenv"}) == "own window", "on: not from an activated conda environment")
check(P("d4") not in tabs(), "on: ...so no tab for that one")
check(launch(PY, 2500) == "own window", "on: launching Kestrel without a folder still opens a new window")



def selected():
    return [i.data(Qt.ItemDataRole.DisplayRole) for i in w.pane().view().selectionModel().selectedIndexes()]


check(atc.hand_off([P("d5")], [P("d5/pick.txt")]) == atc.radio().flight(),
      "a handoff with a selection is routed to the Kestrel with a window")
check(wait_for(lambda: P("d5") in tabs()), "...which opens the folder as a tab")
check(wait_for(lambda: "pick.txt" in selected(), 3000), "...with the item selected")
n = w.tabs.count()
A.handle_fm1("ShowItems", [util.file_uri(P("d6/item.txt"))], "")
check(len(A.WINDOWS) == 1 and w.tabs.count() == n + 1 and w.pane().path == P("d6"),
      "on: Show in folder opens a tab in this window")
check(wait_for(lambda: "item.txt" in selected(), 3000), "on: ...with the item selected")
A._settings.setValue("open_in_tabs", False)
A._settings.sync()
A.handle_fm1("ShowFolders", [util.file_uri(P("d6"))], "")
check(len(A.WINDOWS) == 2, "off: Show in folder opens a new window again")
finish()
