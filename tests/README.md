# Tests

Automated tests for the Python version of Kestrel Explorer. The [C++ version](../../kes-c/tests) has the same tests with the same checks, so the two can be compared line by line.

```bash
tests/run.sh                     # run every test
tests/run.sh fileops atc_undo    # only some of them
```

`run.sh` runs each test with the system Python (`/usr/bin/python3`) and prints its `PASS` / `FAIL` / `SKIP` lines and a summary. It exits with 0 only if every test passed. A full run takes about half a minute.

## Your own setup is never touched

Each test runs:

- with a **throwaway home folder** (`mktemp -d`, deleted afterwards), so your files, settings, bookmarks, trash, thumbnails and `mimeapps.list` are left alone;
- on a **private D-Bus session bus** (`dbus-run-session`), so your running Kestrel windows, GNOME Files and the real tower never see the test;
- **offscreen** (`QT_QPA_PLATFORM=offscreen`), so no windows appear.

The installer test also uses a keyfile GSettings backend, so your real dock isn't changed. It replaces `pgrep` and `sudo` with stubs, so it can't close a running GNOME Files or install packages.

## What's tested

| Test | Checks | What it covers |
|---|---|---|
| `fileops` | 45 | **File operations.** Copy (contents, permissions, modification time, relative and broken links, empty folders, names with `%`, `%1` and non-ASCII letters); move (a rename on the same drive); merge and replace; deleting read-only folders and links to folders; cancelling a copy part-way without leaving half-copied files; Move to Trash and where the item came from; undo of trash, move, copy (into the trash, not deleted) and rename; undo explaining what it can't put back; unique names (`a (copy).txt`, `a (copy 2).txt`, `a (2).txt`); symbolic, relative and hard links; `trash:///` and `recent:///` from other apps; folder sizes. |
| `atc_sync` | 17 | **The tower.** The first Kestrel starts it and checks in. Stars, folder colours, covers, bookmarks, saved Preferences and cleared caches are reported to other Kestrels, and changes from other Kestrels are picked up (including bookmarks appearing in the sidebar). Saving keeps another Kestrel's change. A crashed tower is replaced and everyone checks in again. |
| `atc_tasks` | 15 | **The shared task list.** Another Kestrel's running job shows in a new window's status bar (🛡 for admin jobs). ✕ asks the owning Kestrel to cancel it. Jobs here are reported, throttled to about two progress reports a second. A second window sees the first window's job. A cancel from another Kestrel stops a job here. A Kestrel that leaves takes its jobs with it. A restarted tower gets the running jobs again. |
| `atc_undo` | 20 | **Undo between Kestrels.** Off (the default): each Kestrel undoes only its own actions. On: Ctrl+Z undoes the newest action from any Kestrel, each action is handed out once, and the format matches the C++ version's. With no tower, undo falls back to the Kestrel's own history. |
| `atc_tabs` | 17 | **Open folders as tabs.** Real `kes` programs are launched against an open window. Off: they keep their own windows. On: they hand their folder over and exit, and it becomes the current tab. Exceptions: a Kestrel started from an activated conda environment, or without a folder, keeps its own window. A handoff carrying a selection opens with the item selected, and "Show in folder" opens a tab. |
| `devices` | 22 | **Phones and cameras.** Paths on an iPhone (afc), a camera or an iPhone's photos (gphoto2) and an Android phone (mtp) are recognised; network shares and local folders aren't. Videos from gphoto2, which sends the whole file on every open, are copied before they play; the others play in place. In the Overview, the duplicate (shadowed) mounts gvfs lists are skipped, phones go under Phones & Cameras rather than Network, and the hints say what to do on the device (tap Trust, choose File transfer, PTP mode). On a phone, folders get no preview mosaic, photos are queued for a thumbnail, and one that can't be read fails without hanging. Local photos still get thumbnails. No real device is needed: nothing reaches a gvfs mount. |
| `installer` | 16 | **`install.sh`.** `--default` makes Kestrel the app for folders and `trash:///`, and installs the Show-in-folder service. `--dock` swaps GNOME Files for Kestrel in the dock, in the same place. In a terminal, `--default` asks about the dock. Running it twice changes nothing. `--uninstall` puts back the previous trash handler and the dock, and doesn't touch a dock it didn't change. |

### Checks shared with the C++ version

When the C++ version is built next to this project (`../kes-c/build/kes`), `atc_tabs` also launches it and checks that it hands its folder to this window. Point `KES_CXX` at another build if it lives elsewhere. Without it, that check is skipped (`SKIP`).

The undo test checks that this version sends history entries in the format the C++ version reads. The C++ suite checks the reverse.

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
