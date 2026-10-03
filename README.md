# Kestrel Explorer

**Version 0.1.2-alpha.** This is an early alpha release, so expect rough edges.

A lightweight file manager for Ubuntu, written in Python and PyQt6. It is built mainly for browsing image galleries. Folder icons show a mosaic of the images inside them. It also covers the everyday jobs you'd do in GNOME Files.

## Install / run

```bash
./install.sh             # adds the `kes` command to ~/.local/bin and the app to the app grid
./install.sh --default   # ...and makes it the default app for opening folders
./install.sh --install-recommended   # ...and installs the recommended packages (RAW/HEIC previews etc.)
./install.sh --uninstall

./kes ~/Pictures                 # or run it in place without installing
./kes --version                  # print the version
kes ~/Pictures                   # once installed
```

It uses the system Python (`/usr/bin/python3`), not a virtualenv or conda Python. `install.sh` installs the required packages and lists any recommended ones that are missing; add `--install-recommended` to install those too. The options can be combined, for example `./install.sh --install-recommended --default`.

### Updating and reinstalling

`kes` is a link to the code in this folder, so code changes (for example after a `git pull`) take effect the next time you start the app. You don't need to reinstall.

To reinstall, run `./install.sh` again. It overwrites the previous install: it re-checks the required packages, recreates the `kes` link, and rewrites the app-grid entry. Reinstall when:
- you moved or renamed this folder (the `kes` link breaks);
- `install.sh` itself changed;
- the app-grid entry is missing or broken.

A reinstall doesn't change these:
- **Default folder app:** if you set it with `--default`, it stays set. Run `./install.sh --default` to set it again.
- **Your settings, bookmarks and thumbnail cache:** they're kept (`~/.config/kestrel-explorer`, `~/.config/gtk-3.0/bookmarks`, `~/.cache`).

For a completely clean reinstall:

```bash
./install.sh --uninstall && ./install.sh
```

This still keeps your settings, bookmarks and thumbnails. To reset everything, delete `~/.config/kestrel-explorer` and `~/.cache/kestrel-explorer` after uninstalling. Bookmarks are shared with GNOME Files, so leave those alone.

### PyPI (not available yet)

The packaging setup for PyPI is in place (`pyproject.toml`), but **Kestrel is not published yet and isn't ready to be**. `pip install kestrel-explorer` won't work for now; use `install.sh` instead. See [PACKAGING.md](PACKAGING.md) for what's set up and what's left before a first release.

## Dependencies

Package names are for Ubuntu (tested on 26.04). Everything else Kestrel uses is in the Python standard library.

**Required**

| Package | Used for |
|---|---|
| `python3` | Runs the app (the system `/usr/bin/python3`) |
| `python3-pyqt6` | The whole user interface (Qt 6) |
| `python3-pil` | Image sizes, EXIF summary, AI-prompt metadata, and a fallback image decoder |
| `python3-gi` + `gir1.2-glib-2.0` | GIO: default and "Open With" apps, file type detection, drive and network mounting |

**Recommended**: run `./install.sh --install-recommended` to install any of these you're missing. Ubuntu already includes most of them. The ones you'll usually need to add are `kimageformat6-plugins` (RAW and HEIC previews) and some of the archive tools, such as `unrar`, `pigz`, `zpaq` and `lzip`.

| Package | Used for |
|---|---|
| `libglib2.0-bin` | `gio` (launching `.desktop` shortcuts, unmounting), `gsettings` (icon theme, set as wallpaper) and `gdbus` (talking to UWP when GIO isn't available) |
| `xdg-utils` | `xdg-open` (opening files when GIO isn't available) and `xdg-mime` (making Kestrel the default folder app) |
| `gvfs`, `gvfs-backends` | Network locations (`smb://`, `sftp://`, `nfs://`, `ftp://`…) and the drive list on the Overview page |
| `udisks2` | Mounting, unlocking and ejecting drives from the Overview page |
| `qt6-image-formats-plugins` | WebP, TIFF, TGA, ICNS and MNG images |
| `qt6-svg-plugins` | SVG images |
| `kimageformat6-plugins` | Camera RAW (`.raf`, `.cr2`, `.cr3`, `.nef`, `.arw`, `.dng`…), HEIC, AVIF, JPEG XL and PSD. Without it these files get no preview and don't open in the viewer |
| `adwaita-icon-theme` | Fallback icons when your icon theme is missing one |
| `7zip` (or `p7zip-full` on older releases) | Creating and extracting 7z and zip (with AES-256 passwords and split volumes), and extracting iso, cab, deb, rpm and other formats |
| `unrar` (in Ubuntu's multiverse section) | Extracting `.rar`, including encrypted and multi-volume archives. 7-Zip on Ubuntu can't decompress RAR |
| `zip`, `unzip` | zip archives without 7-Zip |
| `pigz` | Fast multi-core `.gz` / `.tar.gz` (falls back to `gzip`) |
| `zpaq` | `.zpaq` journaling archives, with very high compression and passwords |
| `zstd`, `xz-utils`, `bzip2`, `lzip` | `.tar.zst`, `.tar.xz`, `.tar.bz2`, `.tar.lz` and single-file `.zst`/`.xz`/`.bz2`/`.lz` |
| A terminal (`ptyxis`, `gnome-terminal`, `kgx`, `konsole` or `xfce4-terminal`) | "Open in Terminal" (not installed by `install.sh`; any one of these works) |

**Optional** (features are hidden or fall back when missing)

| Package | Used for |
|---|---|
| `libimage-exiftool-perl` | The full Metadata tab, plus editing, adding and clearing metadata |
| `ffmpeg` | Video thumbnails and video stills in folder previews (`ffmpeg` and `ffprobe`) |
| `zfsutils-linux` | Showing a ZFS pool as one card with pool-level usage on the Overview page |
| `pkexec` | The **admin session**: retrying operations that fail with "permission denied" as administrator (usually already installed) |
| `rar` (non-free, multiverse; a trial version of WinRAR's command-line tool) | Creating `.rar` archives (passwords, volumes, recovery records) |
| `pbzip2` or `lbzip2`, `plzip`, `lz4` | Multi-core `.bz2` and `.lz`, and `.lz4` archives. Kestrel offers whichever are installed |
| [UWP](https://github.com/RegulusArms/UWP) (not an apt package; install with its `install.sh`) | "Set as Wallpaper" through UWP profiles, and "Add to Selected UWP Monitor". Found as `uwp` on your PATH or in `~/.local/bin` |

```bash
# everything at once
sudo apt install python3-pyqt6 python3-pil python3-gi gir1.2-glib-2.0 libglib2.0-bin xdg-utils \
    gvfs gvfs-backends udisks2 qt6-image-formats-plugins qt6-svg-plugins adwaita-icon-theme \
    kimageformat6-plugins 7zip unrar zip unzip pigz zpaq zstd xz-utils bzip2 lzip \
    libimage-exiftool-perl ffmpeg
```

## Features

**Galleries**
- Folder icons show a 1–4 image mosaic. If a folder has no images of its own, the mosaic is taken from its subfolders.
- A folder with only videos uses a still from the middle of each video, marked with a ▶ badge.
- Right-click a folder → **Generate Previews Recursively** pre-builds every thumbnail and folder preview below it in the background. Progress shows in the status bar, and ✕ stops it.
- The **Folder previews** checkbox in the toolbar (Ctrl+Shift+P) turns the mosaics off for speed in large or slow folders. Image file thumbnails stay on.
- If a folder contains an image named `cover`, `folder`, `front` or `poster` (any extension, also hidden as `.cover` or `.folder`), that image is used as the cover. You can also right-click an image and choose "Use as Folder Cover", and "Reset Folder Cover" to undo it.
- Right-click a folder → **Regenerate Preview** rebuilds its mosaic. The ☰ menu has **Clear Folder Preview Cache** and **Delete All Thumbnails…** to free disk space or start fresh.
- Thumbnails are generated in background threads and support JPEG, PNG, GIF, WebP, TIFF, SVG and video (with `ffmpeg`). AVIF, HEIC, JPEG XL, PSD and camera RAW also work when `kimageformat6-plugins` is installed (see [Dependencies](#dependencies)).
- Thumbnails are shared with GNOME Files through the freedesktop cache (`~/.cache/thumbnails`).
- Zoom with Ctrl+scroll or the slider, from 48 to 320 px.
- Built-in viewer: arrow keys or the scroll wheel to move between images, zoom and pan, fullscreen (F), slideshow (S), rotate (R/L) and flip (H), an info overlay (I), copy the image (Ctrl+C), trash (Delete), and animated GIF/WebP.
- Images and videos open in your system's default app unless you choose otherwise. In Preferences you can pick **Open images with** (system default, Kestrel's built-in viewer, or any installed image app) and **Open videos with** (system default or any installed video app). "View Image" in the right-click menu always uses the built-in viewer.
- The info panel (F3) shows EXIF details: camera, lens, exposure and GPS.
- It also shows Stable Diffusion / ComfyUI prompts and settings embedded in PNG, WebP and JPEG files.

**Overview (default homepage)**
- Like the old GNOME Files "Other Locations" page:
  - **Drives:** every drive with a usage bar (amber at 75%, red at 90%) and eject/unmount buttons. Unmounted drives can be mounted with a click, and encrypted drives ask for their passphrase. A ZFS pool appears as one card with pool-level numbers.
  - **Network:** connected shares, plus a "Connect to Server" box for `smb://`, `sftp://`, `nfs://`, `ftp://` and similar addresses. Recent servers are remembered.
  - **Bookmarks:** your bookmarks as cards with folder previews. Right-click a card to edit or remove it.
- Preferences → Homepage can be the Overview, your home folder, or any folder or network address. The homepage opens at launch and with the Home button (Alt+Home).

**File management**
- Tabs, back/forward history, a clickable path bar (Ctrl+L to type a path), and a sidebar.
- The sidebar shows standard places, your GTK bookmarks (shared with GNOME Files) and mounted drives, with an unmount option.
- Right-click a bookmark → **Edit Bookmark…** to change its name and location (with a folder picker), or remove it or move it up and down.
- Grid and list views, sorting, hidden files (Ctrl+H).
- Search the current folder by typing to filter, or search subfolders recursively. Wildcards are supported.
- Cut, copy and paste work with GNOME Files' clipboard. Paste as link and pasting image data are supported.
- Drag and drop:
  - Ctrl copies, Shift moves, Ctrl+Shift creates a link, and Alt asks what to do.
  - With no key held, a drop moves files on the same drive and copies them to another drive.
- File operations run on background threads, so the window stays responsive even with tens of thousands of files. Copy, move, duplicate, move to trash, permanent delete, restore, empty trash, compress and extract show their status and a progress bar in the status bar at the bottom of the window, with ✕ to cancel. When several run at once, the bar shows the oldest with "+N more" (hover to see them all). Closing a window while operations are running asks whether to stop them or keep going.
- If a name already exists when copying or moving, you can replace, merge, skip or keep both.
- Trash, permanent delete, and restoring or emptying the trash. The Trash shows everything you've deleted on every drive in one list: your home trash plus the trash folder each drive keeps for files deleted on it (`.Trash-<uid>`, the same as GNOME Files). The Location column shows where each item came from, and Restore, Delete Permanently and Empty Trash work across all of them.
- Deleting handles read-only folders you own (common in extracted Windows archives): they're made writable and deleted.
- Links and shortcuts:
  - Symbolic links (absolute or relative) and hard links.
  - Link to the Desktop.
  - `.desktop` shortcuts, marked as trusted so they can be launched.
- Rename (F2). Batch rename works with templates (`[Name] ###`, `[Date]`) or find and replace with regex.
- Duplicate, Move To…, Copy To…, and Compress / Extract (see [Archives](#archives)).
- Open With: shows recommended apps and lets you set the default app. You can also open a terminal in the current folder or set an image as wallpaper.
- **UWP wallpapers** (when [UWP](https://github.com/RegulusArms/UWP) is installed):
  - **Set as Wallpaper (UWP)** on an image or video adds it to UWP's library and shows it on every monitor as a new UWP profile named after the file. It starts UWP if needed and opens its editor. Without UWP, "Set as Wallpaper" sets the GNOME background instead.
  - **Add to Selected UWP Monitor** appears while UWP's editor window is open. It adds the file to UWP's library and puts it on the monitors selected in the editor (or adds it to that monitor's slideshow). Press OK in UWP to keep it, just as when you pick from UWP's own library.
- Properties window:
  - **General:** size (recursive for folders), dates, inode, the free space on the drive, and the default app.
  - **Permissions:** an editable rwx grid plus setuid, setgid and sticky bits.
  - **Image:** image details and a Set as Wallpaper button.
  - **Metadata:** every tag exiftool can read, with a filter. With `exiftool` installed you can also edit the file's metadata, and changes are written straight to the file:
    - **Edit:** double-click a tag to change its value. List tags such as keywords are edited one item per line.
    - **Add Tag…:** a searchable list of the tags this file type supports, with columns for the tag, what it's for, and what it accepts (text, numbers only, date/time, a fixed choice, or true/false). The allowed values are shown for choice tags.
    - **Remove:** delete one or more selected tags.
    - **Clear All Metadata…:** strip everything, optionally keeping the orientation and colour profile so the image looks the same.
    - Greyed-out rows (file system info and values exiftool calculates) are read-only.
    - The tag descriptions and "Accepts" types are AI-generated and may not be completely accurate. Descriptions outside the common tags are built from exiftool's tag names and categories.
  - **Checksums:** MD5, SHA1 and SHA256, with a field to check against a known checksum.

**Archives** (details in [Archives](#archives))
- **Extract Here** and **Extract To…** for 7z, zip, rar (including split `.part1.rar` sets), zpaq, tar with any compression, single `.gz`/`.xz`/`.zst`/… files, and iso, cab, deb, rpm and more.
- **Encrypted archives**, including RAR and 7z with hidden file names: Kestrel asks for the password, and asks again if it's wrong.
- **Compress…** on any files or folders: 7z, zip, rar, zpaq, tar, tar.gz/bz2/xz/zst/lz/lz4 or a single compressed file, using pigz, gzip, zpaq, zstd, xz, bzip2, lzip, 7-Zip, rar and the other tools you have installed.
- Every option the format supports: program, level, method, CPU threads, password (AES-256), encrypted file names, split volumes, solid archives, rar recovery records, and extra options for the program, with a live preview of the exact command.
- **Compress to “name.ext”** repeats your last settings in one click.
- Progress, elapsed time and Cancel in the status bar; cancelling removes partial output.

**Admin session** (details in [Admin session](#admin-session))
- When something fails because you don't have permission (copying into `/opt`, deleting files owned by root, renaming in a system folder…), Kestrel offers **Retry as Administrator**.
- You enter your password once. After that, a 🛡 **Admin** indicator shows in the status bar, and further operations that need admin rights run without asking again, until you end the session or it's unused for 15 minutes.
- Kestrel itself never runs as root.

Press F1 in the app for all keyboard shortcuts. Settings are in the ☰ menu under Preferences: homepage, how many images a mosaic uses, whether it picks images by name or newest first, folder colour, the thumbnail size limit, which apps open images and videos, single-click, folder previews in list view, and slideshow speed.

## Archives

Kestrel doesn't contain its own compression code. It drives the command-line tools installed on your system, and offers the formats those tools support. Anything whose tool is missing is shown greyed out with the package to install. `./install.sh --install-recommended` installs the common ones (see [Dependencies](#dependencies)).

### Extracting

Right-click an archive:

- **Extract Here** extracts next to the archive into a new folder named after it (`photos.tar.gz` → `photos/`). If everything in the archive is already inside a single folder, that folder is used instead, so you don't get `photos/photos/`. Name clashes get a number: `photos (2)`.
- **Extract To…** lets you choose:
  - the destination folder (created if needed);
  - whether to use a new folder named after the archive;
  - what to do with files that already exist: keep both (rename), replace or skip;
  - whether to move the archive to the trash afterwards, and whether to open the extracted folder.

  These choices, except the destination, are remembered.

**Encrypted archives.** Kestrel checks every archive before extracting. If it's encrypted (including RAR and 7z archives whose file names are hidden) it asks for the password. A wrong password asks again, and nothing half-extracted is left behind.

**Split archives.** Right-clicking any part of a split set (`x.part3.rar`, `x.7z.003`, `x.zip.002`, old-style `x.r05`) extracts the whole set, starting from the first part. All parts must be in the same folder.

### Compressing

Select files and/or folders, right-click and choose **Compress…**. The dialog shows only the options the chosen format supports:

| Option | What it does |
|---|---|
| Archive name, Location | Where the archive is saved. Defaults to the selection's folder and name. |
| Format | See the table below. Formats whose tool isn't installed are greyed out; single-file formats need exactly one file selected. |
| Program | Which tool to use when there's a choice: pigz or gzip, pbzip2, lbzip2 or bzip2, plzip or lzip, 7-Zip or zip. |
| Compression level | The tool's own scale, e.g. 0–9 for 7z (Store … Ultra), 1–22 for zstd (20+ is "ultra"), 1–5 for zpaq. |
| Method | 7z: LZMA2, LZMA, PPMd, BZip2, Deflate or Copy. zip with 7-Zip: Deflate, Deflate64, BZip2, LZMA, PPMd or Copy. |
| CPU threads | Auto uses all cores. Shown for tools that support threads. |
| Encrypt with a password | 7z (AES-256), zip (AES-256 or the weaker but universally readable ZipCrypto), rar and zpaq. **Also encrypt file names** (7z, rar) hides even the list of files. |
| Split into volumes | Parts of a chosen size in MB or GB: `.7z.001`, `.zip.001`, `.part1.rar`, …. |
| Solid archive | 7z and rar: compress files together for a smaller archive, at the cost of slower single-file extraction. |
| Recovery record | rar: extra data (1–10%) that lets `rar` repair a damaged archive. |
| Extra options | Anything else, passed straight to the program, e.g. `-mfb=273` for 7z or `--long=27` for zstd. |
| Move the original files to the trash afterwards | Only after the archive was created successfully. |

The **Command** box at the bottom shows exactly what will run (with the password masked). Your choices are remembered per format, but the password never is. Afterwards, the right-click menu also offers **Compress to “name.ext”**, which uses your last format and options (without a password) in one click.

### Formats

| Format | Create with | Extract with | Password | Split | Notes |
|---|---|---|---|---|---|
| 7z | `7z` | `7z` | AES-256, optional hidden names | yes | Solid; choice of method |
| zip | `7z` or `zip` | `7z` (or `unzip`) | AES-256 or ZipCrypto (`zip`: ZipCrypto only) | with 7-Zip | Split zips are `.zip.001` parts, which 7-Zip joins |
| rar | `rar` (non-free) | `unrar` | yes, optional hidden names | yes | Solid; recovery record. Ubuntu's 7-Zip can't decompress RAR, so extracting needs `unrar` |
| zpaq | `zpaq` | `zpaq` | yes | no | Journaling, very high compression at levels 4–5 (and very slow) |
| tar | `tar` | `tar` | no | no | |
| tar.gz | `pigz` or `gzip` | same | no | no | pigz uses all cores |
| tar.bz2 | `pbzip2`, `lbzip2` or `bzip2` | same | no | no | |
| tar.xz | `xz` | `xz` | no | no | Multi-threaded |
| tar.zst | `zstd` | `zstd` | no | no | Very fast; levels up to 22 |
| tar.lz | `plzip` or `lzip` | same | no | no | |
| tar.lz4 | `lz4` | `lz4` | no | no | Fastest, lowest ratio |
| gz, bz2, xz, zst, lz, lz4 | as above | as above | no | no | One file only, no folder structure |
| iso, cab, deb, rpm, wim, cpio, msi, … | — | `7z` | — | — | Extract only |

Symbolic links are stored as links (zpaq is the exception; see below). File names in any language are stored correctly.

### Progress and cancelling

Archive jobs run in the background and show in the status bar with a progress bar, ✕ to cancel, and the elapsed time for anything over 5 seconds. Cancelling stops the tool immediately and removes the partial archive, or the half-extracted folder.

Not every tool can report real progress, so the bar shows what each one can:

- **7z, rar, unrar:** a real percentage for the whole job.
- **tar formats and single-file formats:** Kestrel feeds the data to the compressor itself and counts the bytes. Multi-threaded xz takes in its input faster than it compresses, so after the input is handed over the bar shows "xz is compressing the last data" with a clock.
- **zip:** the number of files done, so a single huge file shows no movement while it's compressed.
- **zpaq:** its percentage only counts files being *read*, which takes seconds. The real compression that follows prints nothing, so the bar switches to "compressing (zpaq reports no progress for this step)" with a clock. At levels 4–5 this can take several minutes per few hundred MB and about 500 MB of RAM per thread. zpaq extraction reports progress only occasionally, so it mostly shows the clock.

### Passwords and security

- **7-Zip** (7z and zip) receives the password privately, on its standard input.
- **`unrar`, `rar`, `zpaq` and `zip`** only accept a password on their command line. While the job runs, another user logged in to the same computer could see it in the process list. The dialogs say so when this applies.
- **ZipCrypto** is weak encryption; use AES-256 (the default) unless the archive must open in very old tools.
- **Links that point outside the extraction folder** (for example to `/etc/...`) aren't recreated as-is: 7-Zip re-roots them inside the folder and `unrar` skips them. This protects you from malicious archives.

### Known limitations

- **zpaq can't store symbolic links;** any in the selection are skipped. The dialog says so.
- **Creating .rar needs `rar`,** which is non-free (a trial of WinRAR's command-line tool) and not installed by default.
- **zip volumes** made with 7-Zip are plain `.zip.001` parts, not the "spanned" zip format some Windows tools expect.
- **Tools only report progress as described above.** A single large file in zip, or zpaq's compression phase, shows the clock rather than a percentage.

## Admin session

Running a whole file manager as root (`sudo kes`) is risky and mostly doesn't work on a modern desktop. Kestrel instead keeps running as you, and does only the operations that need admin rights as root.

### How it works

1. An operation fails with "permission denied". Kestrel lists what failed and offers **Retry as Administrator**.
2. The first time, the normal system password prompt appears. It says something like *"Authentication is needed to run `/usr/bin/python3` as the super user"*: that's Kestrel's small helper (`kestrel/admin_helper.py`), started through `pkexec`.
3. The helper keeps running as root in the background, and the status bar shows 🛡 **Admin**. From then on, operations that need admin rights are handed to it straight away, with no password and no extra question. Progress and ✕ Cancel work as usual.
4. The session ends when you click 🛡 Admin → **End Admin Session**, after 15 minutes without admin actions, or when Kestrel quits. The next admin operation asks for your password again.

☰ → **Start Admin Session…** opens a session ahead of time, for example before a batch of work in system folders.

### What it covers

| Covered | Not yet |
|---|---|
| Copy, move, paste, drag and drop, duplicate, Move To / Copy To | Batch rename |
| Delete permanently and empty trash (including files owned by root in the trash) | Extracting archives into protected folders |
| Rename (F2 and the Properties name field) | Editing metadata |
| New folder, new empty file | |
| Symbolic links, hard links and `.desktop` shortcuts | |
| Changing permissions in Properties | |

Move to Trash itself doesn't run as administrator: if an item can't be trashed, Kestrel offers to delete it permanently instead, and that can use the session.

### Safety

- **Only the Kestrel window that started the helper can talk to it.** It reads requests from a private pipe and exits as soon as that pipe closes, including if Kestrel crashes.
- **It only accepts a fixed set of file operations,** on absolute paths. It refuses to delete or replace `/`, top-level folders (`/usr`, `/etc`, `/home`, `/var`, and any other folder directly under `/`) and home folders themselves (`/home/name`).
- **The trade-off:** the helper runs from the Kestrel folder, which your account can edit. Anything running as you could change that file before your next admin session. That's the same level of trust as typing `sudo` in your own terminal, which is fine on a personal computer.

## Layout

| File | Purpose |
|---|---|
| `kestrel/app.py` | Main window, tabs, browser pane (including the combined trash view), actions, context menus |
| `kestrel/widgets.py` | File-system model, grid delegate, path bar, sidebar, info panel, search |
| `kestrel/thumbs.py` | Background thumbnail and folder-mosaic generation and caching |
| `kestrel/viewer.py` | Image viewer |
| `kestrel/fileops.py` | Background tasks and the status-bar task panel; copy, move and delete (retrying denied ones as administrator); conflict handling; links |
| `kestrel/admin.py` | The admin session: starts the root helper once, sends it operations, the 🛡 status-bar indicator, and "Retry as Administrator" |
| `kestrel/admin_helper.py` | The small root helper (standard library only) that performs admin-session operations |
| `kestrel/dialogs.py` | Properties (including the metadata editor and Add Tag picker), Open With, rename, batch rename, Edit Bookmark, preferences |
| `kestrel/metadata.py` | EXIF, AI-generation metadata, exiftool reading/editing, and the tag catalog for the Add Tag picker (the tag descriptions and "Accepts" types are AI-generated and may not be completely accurate) |
| `kestrel/overview.py` | Overview page: drives, network locations, bookmarks |
| `kestrel/util.py` | Shared helpers: paths, file types, icons, desktop integration (default apps, wallpaper, terminal), trash on every drive, GTK bookmarks |
| `kestrel/archive.py` | Archive engine: tool detection, the commands for every format, progress, cancel, password handling |
| `kestrel/archive_ui.py` | Compress and Extract dialogs, password prompts, and the archive job flows |
| `kestrel/uwp.py` | Integration with the UWP wallpaper manager (over D-Bus and the `uwp` command) |
| `pyproject.toml` | PyPI packaging metadata (not ready for release yet — see [PACKAGING.md](PACKAGING.md)) |

## Performance: Python vs C++

Kestrel Explorer exists in two versions with the same features: the original [Python/PyQt6 version](.) and the [C++/Qt 6 port](../kes-c). They share settings, bookmarks and caches, so you can switch between them.

Both were tested on the same generated data: 600 JPEGs at 1600×1200, 150 folders of 4 images, a tree of 50,000 files, and 20,000 small files plus 250 MB. Each test ran 3 times with empty caches, on a 32-thread machine with the data on a RAM disk. Times are medians.

| Test | Python | C++ | C++ speed-up |
|---|---|---|---|
| Startup (launch to window shown) | 0.38 s | 0.18 s | 2.1× faster |
| Open a 600-image folder (visible thumbnails ready) | 0.29 s | 0.23 s | 1.3× faster |
| Thumbnail all 600 images ("Generate Previews") | 2.29 s | 2.13 s | 1.1× faster |
| Build 150 folder mosaics | 3.06 s | 2.78 s | 1.1× faster |
| Recursive search over 50,000 files | 0.21 s | 0.15 s | 1.4× faster |
| Copy 20,000 small files + 5 × 50 MB | 1.75 s | 0.98 s | 1.8× faster |
| Read EXIF / AI metadata for 400 images | 0.95 s | 0.024 s | about 40× faster |
| Peak memory (startup / folder open) | 105 / 119 MB | 65 / 79 MB | about 40 MB less |
| Peak memory (background jobs) | 73–81 MB | 35–39 MB | about half |

The largest gains are in work the Python version does in Python itself: reading metadata, copying files and starting up. Thumbnails are only about 10% faster, because both versions decode images with the same Qt C++ code, which the Python version already runs in parallel.

## License

MIT. See [LICENSE](LICENSE).
