# Tests

Automated tests for the Python version of Kestrel Explorer. The [C++ version](../../kes-c/tests) has the same tests with the same checks, so the two can be compared line by line.

```bash
tests/run.sh                     # run every test
tests/run.sh fileops atc_undo    # only some of them
```

`run.sh` runs each test with the system Python (`/usr/bin/python3`) and prints its `PASS` / `FAIL` / `SKIP` lines and a summary. It exits with 0 only if every test passed. A full run takes about half a minute.

If the C++ version is built next to this project, `atc_tabs` also checks the two together. It looks for `../kes-c/build/kes`, or `../kes-c/$KESTREL_BUILD_DIR/kes` when `KESTREL_BUILD_DIR` is set (a machine that shares these folders with another keeps its own build folder).

## Your own setup is never touched

Each test runs:

- with a **throwaway home folder** (`mktemp -d`, deleted afterwards), so your files, settings, bookmarks, trash, thumbnails and `mimeapps.list` are left alone;
- on a **private D-Bus session bus** (`dbus-run-session`), so your running Kestrel windows, GNOME Files and the real tower never see the test;
- **offscreen** (`QT_QPA_PLATFORM=offscreen`), so no windows appear.

The installer test also uses a keyfile GSettings backend, so your real dock isn't changed. It replaces `pgrep` and `sudo` with stubs, so it can't close a running GNOME Files or install packages.

## What's tested

| Test | Checks | What it covers |
|---|---|---|
| `fileops` | 73 | **File operations.** Copy (contents, permissions, modification time, relative and broken links, empty folders, names with `%`, `%1` and non-ASCII letters); move (a rename on the same drive); merge and replace; deleting read-only folders and links to folders; cancelling a copy part-way without leaving half-copied files; Move to Trash and where the item came from; undo of trash, move, copy (into the trash, not deleted) and rename; undo explaining what it can't put back; unique names (`a (copy).txt`, `a (copy 2).txt`, `a (2).txt`); symbolic, relative and hard links; `trash:///` and `recent:///` from other apps; folder sizes; double-clicking an archive opens the Extract dialog (but not a `.deb`, `.iso` or `.apk`); checksum files: recognised by name (`.sfv`, `.md5`, `.sha256`, `SHA256SUMS`, `…-CHECKSUM`), read in every format (`md5sum`'s with escaped names, BSD tags in a PGP-signed `CHECKSUM`, SFV with comments and Windows line ends, a lone hash for `x.sha256`), checked with each algorithm (matching, changed and missing files), double-clicking one opens Verify Checksums, which checks every file it lists, and Properties' Checksums tab checks a file against a chosen checksum file; a program's timeout holds even without pipes or after it closes its output (the program is stopped); a tool still running when nothing refers to it any more is stopped; symlinks inside a tree are never followed (a merge copy doesn't write through one in the destination, copying onto one replaces the link, and a folder swapped for a symlink during a delete doesn't let it delete outside the tree); crafted tar.gz, zip, 7z and rar archives (`tests/fixtures`: `../` names, absolute paths, a symlink out of the destination and a file through it) can't write outside the folder they're extracted into (skipped where no tool for one is installed); decompressing a single file (`note.txt.gz`) where the destination already has a symlink of that name never writes through it, whether the link dangles (which would create its target) or points to a file (which would be truncated), with Keep both and with Replace, and a failed decompress leaves the file it would have replaced and no temporary file; with `KESTREL_STATS`, folder listings, copy speed, archive jobs and tasks at once are counted; writing to a program that exits without reading its input doesn't kill Kestrel (C++: with SIGPIPE at its default); password-protected rar and zip archives are made and opened (a wrong password is refused), and a watcher on every process's command line never sees the password (skipped where rar, unrar or 7z isn't installed). Shred with BleachBit (a stand-in `bleachbit` that only touches the test's home): it runs `bleachbit --shred` on the chosen files and folders and reports what's still there afterwards; Empty Trash with BleachBit hands it every item in the trash and its record; ✕ stops it. |
| `atc_sync` | 22 | **The tower.** The first Kestrel starts it and checks in. Stars, folder colours, covers, bookmarks, saved Preferences and cleared caches are reported to other Kestrels, and changes from other Kestrels are picked up (including bookmarks appearing in the sidebar). Saving keeps another Kestrel's change. A crashed tower is replaced and everyone checks in again. The protocol is checked: malformed, unknown, oversized and wrongly typed messages (and a flight's own "left") aren't passed on, unknown fields are allowed, only valid undo entries are kept, and a flight speaking another protocol version is ignored. With `KESTREL_STATS`, a report's time to arrive through the tower is counted. |
| `atc_tasks` | 15 | **The shared task list.** Another Kestrel's running job shows in a new window's status bar (🛡 for admin jobs). ✕ asks the owning Kestrel to cancel it. Jobs here are reported, throttled to about two progress reports a second. A second window sees the first window's job. A cancel from another Kestrel stops a job here. A Kestrel that leaves takes its jobs with it. A restarted tower gets the running jobs again. |
| `atc_undo` | 20 | **Undo between Kestrels.** Off (the default): each Kestrel undoes only its own actions. On: Ctrl+Z undoes the newest action from any Kestrel, each action is handed out once, and the format matches the C++ version's. With no tower, undo falls back to the Kestrel's own history. |
| `atc_tabs` | 17 | **Open folders as tabs.** Real `kes` programs are launched against an open window. Off: they keep their own windows. On: they hand their folder over and exit, and it becomes the current tab. Exceptions: a Kestrel started from an activated conda environment, or without a folder, keeps its own window. A handoff carrying a selection opens with the item selected, and "Show in folder" opens a tab. |
| `devices` | 22 | **Phones and cameras.** Paths on an iPhone (afc), a camera or an iPhone's photos (gphoto2) and an Android phone (mtp) are recognised; network shares and local folders aren't. Videos from gphoto2, which sends the whole file on every open, are copied before they play; the others play in place. In the Overview, the duplicate (shadowed) mounts gvfs lists are skipped, phones go under Phones & Cameras rather than Network, and the hints say what to do on the device (tap Trust, choose File transfer, PTP mode). On a phone, folders get no preview mosaic, photos are queued for a thumbnail, and one that can't be read fails without hanging. Local photos still get thumbnails. No real device is needed: nothing reaches a gvfs mount. |
| `sidebar` | 16 | **Rearranging the sidebar.** Entries move within their section (a place to the top, a bookmark, which rewrites the bookmarks file); whole sections move (Devices above Places and Bookmarks, a section to the bottom); a section collapses to its header and expands again. The order survives a refresh, other windows show the same, the change is on disk before it is reported to other Kestrels, and one from another Kestrel is picked up. |
| `theme` | 18 | **Following the desktop's theme and settings.** With Qt before 6.5 (Linux Mint 22), the GTK theme's named colours become the palette; a theme without the basic colours changes nothing. On Cinnamon (Linux Mint), its own settings are used for the wallpaper, file history and icon theme (and only the one wallpaper picture it has); other desktops use GNOME's. Light and dark palettes are told apart; Overview cards stand out from the background and error text stays readable in both. When the palette changes (a light/dark switch, another theme): registered updates run once, stylesheets that use `palette()` take the new colours, and so do the sidebar's headers and the Overview's cards. A deleted widget's update is dropped. The default folder colour (also in folder previews) follows the accent, unless a colour was chosen in Preferences. |
| `chooser` | 25 | **Kestrel as the system's file chooser.** On X11 the chooser becomes the app's dialog (the window id is read from the portal's handle). Requests come over D-Bus as the desktop portal sends them. Save opens a Kestrel window in the requested folder with the suggested name and the app's title; the file goes back as a `file://` URI; saving over a file asks first; double-clicking a folder goes into it; the next dialog starts where the last one saved. Open shows only the chosen file type (folders stay), chooses a file on double-click, returns several with "multiple", and a folder when choosing folders. Saving several files puts them in the chosen folder. A chooser opens smaller than a main window (never at its saved, perhaps maximized, size) and with smaller icons, and keeps its own view settings: zooming or showing hidden files there leaves the main windows alone, and the next chooser starts from them. Cancel, closing the window and the app's Request.Close answer "cancelled". MIME-type filters and GTK button labels are understood. |
| `admin_helper` | 35 | **The admin helper, the part that runs as root** (`admin_helper.py`). It runs as the normal user (no pkexec) and gets requests over its pipe, as Kestrel sends them. Every operation works (mkdir, touch, write, copyfile, copy, move, rename, symlink, hardlink, chmod, delete, cancel); hardlink refuses a file that isn't the user's; moving or copying a file or folder onto itself (with or without merging) leaves it as it was, instead of deleting it as the destination it replaces; moving or copying a folder into itself is refused; moving a file onto a hard link to it keeps the file; symlinks inside a copied or deleted tree are handled as links; relative paths are refused; top-level and home folders can't be deleted, replaced, moved away, renamed or have their permissions changed (checked with paths that don't exist, so nothing real is touched). Nothing reaches a "victim" folder through a symlinked folder, for any operation, and a folder swapped for a symlink while a delete runs (`renameat2` exchanges, for a few seconds) doesn't let the delete escape the tree. Kestrel resolves the user's own symlinked folders before sending a request (for `chmod`, the whole path). |
| `ui` | 4 | **Menus and shortcuts.** Every entry, submenu and separator, with its shortcuts, in the main menu, the toolbar's menus and the context menus (empty space, a file, a folder, two files), compared with `tests/ui_actions.txt`, which is the same file in both versions: an entry or shortcut lost when code moves, or added to one version only, is named in the failure. Entries that depend on what's installed (Open With apps, Send To, Network Sharing, the BleachBit entries, code editors) aren't listed. After a deliberate change, `KESTREL_UI_DUMP=new.txt tests/run.sh ui` writes the new list, for both projects' `ui_actions.txt`. And every control in the window (buttons, the search box, the zoom slider, the file views and their column header, the sidebar) has a name a screen reader can say, also with the search bar, the list view and the info panel open (C++ asks Qt's accessibility layer; PyQt6 has none, so Python applies Qt's rule: the accessible name, or a button's own text). And a file dropped onto a folder goes into it, in the grid and the list view, with the folder highlighted while the file is over it (the drag events go where a real drag's go: the innermost widget under the pointer that accepts drops). |
| `parsing` | 5 | **Parsing that must match the other version.** `shlex_split` (C++) splits the way Python's `shlex.split` does (quotes, backslashes, empty words, which spaces count) and refuses the same broken input; the ordered JSON reader used for exiftool's output and for AI-generation workflows (C++) keeps the writer's key order through escaped quotes, `\u` escapes, braces inside strings and nesting, reads duplicate keys as `json.loads` does, and handles exiftool's `[{…}]`. The expected results are Python's. The `KESTREL_STATS` report reads exactly the same in both versions. |
| `installer` | 52 | **`install.sh` and `kes-setup`.** `--default` makes Kestrel the app for folders and `trash:///` (and `--uninstall` puts back the previous ones, such as Nemo on Linux Mint), puts `~/.local/bin` on new terminals' PATH so `kes` works (added once; `--uninstall` takes it out of `~/.bashrc` and keeps the user's own lines), installs the Show-in-folder service, and makes Kestrel the file chooser (the portal definition, its D-Bus service, and a portals.conf that keeps the desktop's other choices; a portals.conf the user had is changed and put back on `--uninstall`). `--dock` swaps GNOME Files for Kestrel in the dock, in the same place. In a terminal, `--default` asks about the dock. Running it twice changes nothing. `--uninstall` puts back the previous trash handler and the dock, and doesn't touch a dock it didn't change. `kes-setup` on its own, as after installing the .deb (`kes` in `/usr/bin`, the package's menu entry and portal definition): `--default` points the D-Bus services at that `kes`, needs no sudo and leaves the menu entry and PATH alone; `--undo` puts everything back and leaves the package's portal definition. On GNOME, `--default` installs and enables the drop focus extension (only enabling the package's copy after the .deb), keeping the user's other extensions; `--uninstall` and `--undo` take it out again. `kes-setup --bleachbit` writes BleachBit's cleaner: well-formed, following BleachBit's CleanerML schema (needs `xmllint`), and only naming Kestrel's cache, its lists and the two history keys in its settings. With BleachBit installed, BleachBit's dry run in the throwaway home lists exactly Kestrel's files and changes nothing; then a real clean empties the cache and deletes the lists, keeps a folder linked from the cache, GNOME's thumbnails, other apps' caches and every other setting (skipped while a Kestrel is running, since BleachBit won't clean then); `--undo` removes the cleaner and leaves the user's own. `kes-setup --install-optional` (stub `dpkg`, `apt-cache` and `sudo`) asks apt to install exactly the optional packages that are missing and available, names the unavailable ones, and installs nothing when they're all there. |

### Checks shared with the C++ version

When the C++ version is built next to this project (`../kes-c/build/kes`), `atc_tabs` also launches it and checks that it hands its folder to this window. Point `KES_CXX` at another build if it lives elsewhere. Without it, that check is skipped (`SKIP`).

The undo test checks that this version sends history entries in the format the C++ version reads. The C++ suite checks the reverse.

## In CI

GitHub Actions (`.github/workflows/tests.yml`) runs `tests/run.sh` on Ubuntu 24.04, the oldest supported system, on
every push and pull request, with the system's Python and PyQt6. The checks that need `rar`, and the cross-version
checks that need the C++ version built next to this project, are skipped there. The C++ version's CI runs the same
tests, plus sanitizer builds and a check of the .deb.

## Requirements

The packages `install.sh` checks for (`python3-pyqt6`, `python3-pil`, `python3-gi`), plus a few tools a normal Ubuntu desktop already has:

- `dbus-run-session` (`dbus-daemon`);
- `gio` and `gsettings` (`libglib2.0-bin`);
- `xdg-mime` (`xdg-utils`);
- `script` (`util-linux`).

## How the tests work

Each test is one script in `tests/`, `test_<name>.py`:

- **It imports the app** (`kestrel.app` and the other modules) through `common.py`, which also sets up the QApplication, settings and thumbnail manager the way `main()` does.
- **It drives the real code** (`fileops.start_ops`, `undo.undo`, `places.set_starred`, …) and checks the results on disk and in the windows.
- **`FakeFlight`** (`common.py`) plays "another Kestrel": a second connection to the bus that checks in with the tower, reports changes, calls tower methods and records everything the tower broadcasts.
- **The tower is the real one.** When a test's Kestrel finds no tower, it starts `kes --atc`, as the app does.
- **Error boxes don't block the test.** In `fileops`, modal error boxes are closed automatically and their text is recorded, so a failure shows up as a `FAIL` instead of a hang.

## Adding a test

1. Create `tests/test_<name>.py`. Import what you need from `common`, call `setup_app()`, use `check(ok, "what it means")`, and end with `finish()`.
2. Add `<name>` to `ALL` in `tests/run.sh`.
3. Add the same test, with the same check texts, to the C++ version's `tests/test_<name>.cpp`.

## Not covered

These need a real desktop, real hardware or a password, so they are checked by hand:

- whether GNOME on Wayland actually brings a window to the front;
- USB drives, phones and network shares;
- the admin session (`pkexec`);
- drag and drop between apps;
- what the UI looks like.
