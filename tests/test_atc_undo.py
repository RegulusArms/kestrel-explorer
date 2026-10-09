"""Undo per Kestrel (the default) and shared undo between all Kestrels (Preferences → Share undo…)."""
import json
import os
import signal

from common import A, FakeFlight, check, finish, home_path as P, setup_app, spin, start_radio, wait_for

from kestrel import atc, undo

app = setup_app()
start_radio()
w = A.open_window([os.path.expanduser("~")])
fake = FakeFlight()
check(wait_for(lambda: atc.radio().tower_up(), 8000), "tower up")
fake.check_in()


def push(kind, label, items):
    return fake.call("UndoPush", json.dumps({"kind": kind, "label": label, "items": items}))


def touch(name, text="x"):
    with open(P(name), "w") as f:
        f.write(text)


touch("a")
touch("d")

# ---- default: off. Each Kestrel undoes only its own
check(not A._settings.value("shared_undo", False, type=bool), "Share undo is off by default")
os.rename(P("a"), P("b"))
undo.record("rename", "Rename", [(P("a"), P("b"))])
push("rename", "Their Rename", [[P("c"), P("d")]])
spin(300)
check(undo.label() == "Rename", f"off: Ctrl+Z offers our own action, not theirs ({undo.label()})")
undo.undo(w)
check(wait_for(lambda: os.path.exists(P("a")) and not os.path.exists(P("b"))), "off: Ctrl+Z undoes our rename")
check(os.path.exists(P("d")), "off: their action is left alone")
check(not undo.label(), "off: nothing left to undo here")

# ---- on: one history for every Kestrel
A._settings.setValue("shared_undo", True)
A._settings.sync()
fake.report({"type": "settings"})   # as if another window's Preferences turned it on
spin(300)
check(undo.label() == "Their Rename", f"on: Ctrl+Z offers the newest action from any Kestrel ({undo.label()})")
undo.undo(w)
check(wait_for(lambda: os.path.exists(P("c")) and not os.path.exists(P("d"))), "on: Ctrl+Z here undoes their rename")
check(wait_for(lambda: "label" in fake.last_of("undo_changed") and fake.last_of("undo_changed")["label"] == ""),
      "the tower tells everyone the history is empty")
check(fake.call("UndoPop") == "", "an undone action is handed out only once")

os.makedirs(P("m1"))
touch("x")
os.rename(P("x"), P("m1/x"))
undo.record("move", "Move", [(P("x"), P("m1/x"))])
check(wait_for(lambda: fake.last_of("undo_changed").get("label") == "Move"),
      "on: our action goes to the shared history and every Kestrel hears about it")
op = fake.call("UndoPop")
o = json.loads(op)
check(o["kind"] == "move" and o["items"] == [[P("x"), P("m1/x")]], "...in the shared format")
fake.call("UndoPush", op)   # put it back
spin(200)
undo.undo(w)
check(wait_for(lambda: os.path.exists(P("x")) and not os.path.exists(P("m1/x"))), "on: and Ctrl+Z undoes it")
touch("t")
undo.record("trash", "Move to Trash", [P("t")])
check(json.loads(fake.call("UndoPop"))["items"] == [[P("t"), ""]],
      "trash/create items are sent as [path, \"\"]")
os.makedirs(P("newdir"))
push("create", "New Folder", [[P("newdir"), ""]])
spin(300)
undo.undo(w)
check(wait_for(lambda: not os.path.exists(P("newdir"))), "on: undoing another Kestrel's New Folder moves it to the trash")

# ---- no tower: falls back to this Kestrel's own history
push("rename", "Stale", [[P("q"), P("r")]])
spin(200)
os.kill(fake.tower_pid(), signal.SIGKILL)
check(wait_for(lambda: not atc.radio().tower_up(), 3000), "tower down")
touch("e")
os.rename(P("e"), P("f"))
undo.record("rename", "Rename", [(P("e"), P("f"))])
check(undo.label() == "Rename", "no tower: the action is kept here")
check(wait_for(lambda: atc.radio().tower_up(), 8000), "a new tower is started")
spin(300)
check(undo.label() == "Rename", f"the new tower's empty history doesn't hide ours ({undo.label()})")
undo.undo(w)
check(wait_for(lambda: os.path.exists(P("e"))), "...and Ctrl+Z still undoes it")
finish()
