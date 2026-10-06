"""The tower (atc.py): starting it, checking in, and passing shared-state changes between Kestrels."""
import json
import os
import signal
import subprocess

from common import A, FakeFlight, check, finish, home_path as P, setup_app, spin, start_radio, wait_for
from gi.repository import Gio
from PyQt6.QtCore import Qt

from kestrel import atc, dialogs, places, thumbs, util

app = setup_app()
home = os.path.expanduser("~")
for d in ("d1", "d2"):
    os.makedirs(P(d), exist_ok=True)
for f in ("f1", "f2"):
    with open(P(f), "w") as fh:
        fh.write(f)
th = A._thumbs
start_radio()
w = A.open_window([home])
me = Gio.bus_get_sync(Gio.BusType.SESSION, None).get_unique_name()
fake = FakeFlight()

check(wait_for(lambda: fake.tower_pid() != 0, 8000), "the first Kestrel starts a tower")
check(wait_for(lambda: fake.flights_have(os.getpid(), "python")), "Kestrel checked in (impl python)")
fake.check_in()
check(fake.flights_have(1, "fake"), "a second flight is listed")

# ---- this Kestrel reports its changes
f1, f2, d1 = P("f1"), P("f2"), P("d1")
places.set_starred([f1], True)
check(wait_for(lambda: fake.heard_type("starred", me)), "Star is reported")
th.set_folder_color([d1], "#123456")
check(wait_for(lambda: fake.heard_type("folders", me)) and fake.last_of("folders").get("paths") == [d1],
      "Folder colour is reported with its folder")
n = len(fake.heard)
th.set_cover(d1, f1)
check(wait_for(lambda: len(fake.heard) > n) and fake.last_of("folders").get("paths") == [d1], "Folder cover is reported")
util.write_bookmarks([(P("d2"), "Mine")])
check(wait_for(lambda: fake.heard_type("bookmarks", me)), "Bookmarks are reported")
w.preferences()
pd = w.findChild(dialogs.PreferencesDialog)
if pd:
    pd.accept()
check(pd is not None and wait_for(lambda: fake.heard_type("settings", me)), "Saved Preferences are reported")
w.clear_cache()
check(wait_for(lambda: fake.heard_type("thumbs_cleared", me)), "Clearing the preview cache is reported")

# ---- changes from another Kestrel reach this one
sig = []
places.signals.starred_changed.connect(lambda: sig.append(1))
places.STARRED_FILE.write_text(json.dumps([f2]))
fake.report({"type": "starred"})
check(wait_for(lambda: places.is_starred(f2) and not places.is_starred(f1)) and bool(sig),
      "Another Kestrel's stars are picked up")
thumbs.STYLES_FILE.write_text(json.dumps({d1: {"color": "#abcdef"}}))
thumbs.COVERS_FILE.write_text("{}")
fake.report({"type": "folders", "paths": [d1]})
check(wait_for(lambda: th.custom_color(d1) == "#abcdef"), "Another Kestrel's folder colour is picked up")
check(d1 not in th.covers, "...and its cover change")
subprocess.run(["/usr/bin/python3", "-c", "from PyQt6.QtCore import QSettings; "
                "s = QSettings('kestrel-explorer', 'kestrel-explorer'); s.setValue('folder_count', 2); s.sync()"])
fake.report({"type": "settings"})
check(wait_for(lambda: th.folder_count == 2), "Another Kestrel's Preferences are applied")
util.GTK_BOOKMARKS.write_text(f"file://{home}/d1 Theirs\n")
fake.report({"type": "bookmarks"})
check(wait_for(lambda: bool(w.sidebar.findItems("Theirs", Qt.MatchFlag.MatchExactly))),
      "Another Kestrel's bookmarks appear in the sidebar")

# ---- read-modify-write: our next change keeps theirs
thumbs.STYLES_FILE.write_text(json.dumps({d1: {"color": "#abcdef"}, P("d2"): {"color": "#00ff00"}}))
th.set_folder_previews([d1], False)
st = json.loads(thumbs.STYLES_FILE.read_text())
check(st.get(P("d2"), {}).get("color") == "#00ff00", "Saving a folder style keeps another Kestrel's change")

# ---- the protocol: other programs on the session bus can talk to the tower, so messages are checked
before = len(fake.heard)
big = json.dumps({"type": "starred", "pad": "x" * atc.MAX_MESSAGE}, separators=(",", ":"))
for raw in ["not json", "[1,2]", '{"type":"nonsense"}', '{"type":"folders","paths":"/not/a/list"}',
            '{"type":"folders","paths":["relative"]}', '{"type":"tasks","tasks":[{"id":5}]}', '{"type":"left"}', big]:
    fake.call("Report", raw)
fake.report({"type": "folders", "paths": [d1], "unknown_field": 1, "marker": "good"})


def mine_since(start):
    return [m for f, m in fake.heard[start:] if f == fake.name()]


wait_for(lambda: bool(mine_since(before)))
spin(300)
passed = mine_since(before)
check(len(passed) == 1 and passed[0].get("marker") == "good",
      "the tower drops malformed, unknown and oversized messages, and passes on the next good one")
check(atc.valid_message({"type": "tasks", "tasks": [{"id": "1", "fraction": 0.5}]})
      and not atc.valid_message({"type": "cancel", "task": 7})
      and not atc.valid_message({"type": "open", "folders": ["rel"]})
      and not atc.valid_message({"type": "settings", "keep": "yes"})
      and atc.valid_message({"type": "settings", "from_the_future": [1, 2]})
      and atc.valid_undo({"kind": "trash", "label": "Trash", "items": [["/a", ""]]})
      and not atc.valid_undo({"kind": "move", "label": "Move", "items": [["/a", "b"]]}),
      "message fields are checked: wrong types and relative paths are refused, unknown fields are allowed")
fake.call("UndoPush", '{"kind":"wipe","label":"x","items":[["/a","/b"]]}')
fake.call("UndoPush", '{"kind":"move","label":"x","items":[["a","b"]]}')
none = fake.call("UndoPop") == ""
fake.call("UndoPush", '{"kind":"move","label":"Move","items":[["/a","/b"]]}')
check(none and json.loads(fake.call("UndoPop") or "{}").get("label") == "Move",
      "the tower keeps only valid undo entries")
other = FakeFlight()
other.call("CheckIn", '{"pid":2,"impl":"fake","protocol":999}')
other.report({"type": "starred"})
spin(500)
check(not any(f == other.name() for f, _ in fake.heard), "a flight speaking another protocol version isn't passed on")

# ---- the tower goes down: the Kestrel starts a new one and checks in again
old = fake.tower_pid()
os.kill(old, signal.SIGKILL)
check(wait_for(lambda: fake.tower_pid() not in (0, old), 8000), "A crashed tower is replaced")
check(wait_for(lambda: fake.flights_have(os.getpid(), "python")), "...and Kestrel checks in again")
finish()
