"""The sidebar: rearranging entries and sections, collapsing sections, and sharing that with other windows and Kestrels."""
import os

from common import A, FakeFlight, check, finish, home_path as P, setup_app, start_radio, wait_for
from gi.repository import Gio
from PyQt6.QtCore import QSettings

from kestrel import util, widgets
from kestrel.widgets import moved, ordered, section_order

app = setup_app()
for d in ("a", "b", "c"):
    os.makedirs(P(d), exist_ok=True)
util.write_bookmarks([(P("a"), "a"), (P("b"), "b"), (P("c"), "c")])
start_radio()
w = A.open_window([util.HOME])
s = w.sidebar
fake = FakeFlight()
me = Gio.bus_get_sync(Gio.BusType.SESSION, None).get_unique_name()
wait_for(lambda: fake.tower_pid() != 0, 8000)
fake.check_in()
trash = str(util.TRASH_DIR / "files")

# -- the order helpers
check(section_order([]) == ["places", "bookmarks", "devices"]
      and section_order(["devices", "nonsense"]) == ["devices", "places", "bookmarks"],
      "a saved section order ignores unknown sections and adds missing ones")
check(ordered(["a", "b", "c", "d"], ["c", "gone", "a"]) == ["c", "a", "b", "d"],
      "a saved order puts new entries after the saved ones, in their natural order")
check(moved(["a", "b", "c"], "a", 3) == ["b", "c", "a"] and moved(["a", "b", "c"], "c", 0) == ["c", "a", "b"]
      and moved(["a", "b", "c"], "b", 2) == ["a", "b", "c"],
      "moving to an insertion point counts positions before the move")

# -- rearranging
check(s.shown_sections() == ["places", "bookmarks", "devices"], "the sections start as Places, Bookmarks, Devices")
places_before = s.entry_keys("places")
s.move_entry("places", trash, 0)
check(wait_for(lambda: s.entry_keys("places")[:1] == [trash]), "Trash can be moved to the top of Places")
s.refresh()
check(s.entry_keys("places")[:1] == [trash] and widgets._setting_list("sidebar_places_order")[:1] == [trash],
      "the new order is saved and survives a refresh")
s.move_entry("bookmarks", P("c"), 0)
check(wait_for(lambda: s.entry_keys("bookmarks") == [P("c"), P("a"), P("b")])
      and util.read_bookmarks()[0][0] == P("c"),
      "moving a bookmark rewrites the bookmarks file in the new order")
s.move_entry("places", trash, 99)
check(wait_for(lambda: s.entry_keys("places")[-1:] == [trash]), "an entry stays in its own section")
s.move_section("devices", 0)
check(wait_for(lambda: s.shown_sections() == ["devices", "places", "bookmarks"]),
      "Devices can be moved above Places and Bookmarks")
s.move_section("places", 3)
check(wait_for(lambda: s.shown_sections() == ["devices", "bookmarks", "places"]), "a section can be moved to the bottom")

# -- collapsing
s.toggle_section("places")
check(wait_for(lambda: not s.entry_keys("places")) and "places" in s.shown_sections(),
      "a collapsed section shows only its header")
s.toggle_section("places")
check(wait_for(lambda: len(s.entry_keys("places")) == len(places_before)), "it expands again")

# -- shared
w2 = A.open_window([util.HOME])
s.toggle_section("bookmarks")
check(wait_for(lambda: not w2.sidebar.entry_keys("bookmarks") and w2.sidebar.shown_sections() == s.shown_sections()),
      "another window shows the same order and collapsed sections")
check(wait_for(lambda: fake.heard_type("sidebar", me)), "a sidebar change is reported to other Kestrels")
s.toggle_section("devices")  # no waiting: another Kestrel reads the file as soon as it hears the report
with open(A._settings.fileName(), encoding="utf-8") as fh:
    on_disk = any(line.startswith("sidebar_collapsed=") and "devices" in line for line in fh.read().split("\n"))
check(on_disk, "a sidebar change is on disk before it is reported")
other = QSettings(util.APP_ID, util.APP_ID)  # what another Kestrel writes
other.setValue("sidebar_sections", ["bookmarks", "places", "devices"])
other.setValue("sidebar_collapsed", [])
other.sync()
del other
fake.report({"type": "sidebar"})
check(wait_for(lambda: s.shown_sections() == ["bookmarks", "places", "devices"]),
      "a sidebar change from another Kestrel is picked up")

finish()
