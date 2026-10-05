# Kestrel Explorer

**Version 0.2.1-alpha.** This is an early alpha release, so expect rough edges.

A file manager for Ubuntu and Linux Mint that's easy to pick up and puts many jobs you'd normally do in a terminal into the window. It also adds quality-of-life improvements over GNOME Files. It's written in Python with PyQt6.

**Terminal jobs, in the window:**
- **Archives:** create and extract 7z, zip, rar, zpaq and every tar format, with the options the command-line tools offer (level, method, threads, passwords, split volumes), and a preview of the exact command that will run. Double-click an archive to extract it.
- **Admin rights only when needed:** when something fails with "permission denied", choose **Retry as Administrator** and enter your password once per session, instead of running `sudo` or a file manager as root.
- **Permissions and links:** permissions, including setuid, setgid and the sticky bit; symbolic, relative and hard links; and MD5/SHA checksums with verification.
- **Metadata:** view and edit EXIF, XMP and other metadata with `exiftool`, from a searchable list of tags.
- **Search and rename:** search subfolders with wildcards or inside files, and batch rename with templates or regular expressions.
- **Shortcuts out:** Open in Terminal or VS Code, your Nautilus scripts, and Samba network sharing.

**Improvements over GNOME Files:**
- Undo for moves, renames, Move to Trash and new files.
- One Trash for every drive, and an Overview page of drives with usage bars.
- Folder icons that preview the images inside, plus per-folder colours.
- A built-in image viewer, and GIF/WebM playback in the file view.
- Background copies with progress and Cancel in the status bar.
- Phones and cameras (iPhone, Android, PTP cameras) on the Overview page and in the sidebar, with thumbnails from the phone's own previews.
- A sidebar you can rearrange and collapse.
- Follows the desktop's theme and accent colour, also when you change them while it's open.
- Other apps' Open and Save dialogs, such as a browser's "Save image as", can open in Kestrel.
- Several windows that stay in sync.
- Faster in most measurements (see [Performance: Kestrel vs GNOME Files](#performance-kestrel-vs-gnome-files)).

**Improvements over Nemo** (Linux Mint's file manager):
- Archives created and extracted with every option in the window, instead of handing off to a separate archive app.
- Admin rights for just the operation that needs them, instead of opening a whole Nemo window as root.
- Folder icons that preview the images inside.
- A built-in image viewer, and GIF/WebM playback in the file view.
- Metadata viewing and editing (EXIF, XMP, AI-generation prompts) and checksums in Properties.
- Phones: thumbnails from the phone's own previews, and iPhone videos copied once so they play smoothly.
- Faster file operations in the benchmark: copying about 14× faster and moving to another drive about 4.9× faster (see [Performance: Kestrel vs Nemo](#performance-kestrel-vs-nemo)).

**Installs alongside GNOME Files:** Kestrel installs next to GNOME Files (or Nemo on Linux Mint) instead of replacing it. The two share bookmarks, thumbnails, the clipboard, the trash and Recent files, so you can use either. `./install.sh --default` makes Kestrel open folders and "Show in folder" requests and show other apps' Open/Save dialogs, and `./install.sh --uninstall` hands them back.

## Install / run

Install from a clone of this repo with `install.sh` (below), or with pip from PyPI (see [pip (PyPI)](#pip-pypi)).

```bash
./install.sh             # adds the `kes` command to ~/.local/bin and the app to the app grid
./install.sh --default   # ...and makes it the default app for opening folders and the trash, and puts kes on your PATH
./install.sh --dock      # ...and puts it in the dock in place of GNOME Files
./install.sh --install-recommended   # ...and installs the recommended packages (RAW/HEIC previews etc.)
./install.sh --uninstall

./kes ~/Pictures                 # or run it in place without installing
./kes --version                  # print the version
kes ~/Pictures                   # once installed
```

`--default` also makes Kestrel answer "Show in folder" / "Open containing folder" from browsers and other apps. Those don't use the default folder app: they call the `org.freedesktop.FileManager1` D-Bus service, which GNOME Files normally provides. The installer adds a per-user D-Bus activation file so Kestrel provides it instead, and closes GNOME Files' background service so it lets go. If you open GNOME Files later while no Kestrel window is open, it takes the service back until it quits.

If `~/.local/bin` isn't on your PATH yet (on Ubuntu and Mint it's only added at login, and only if the folder already existed), `--default` adds it for new terminals: a marked block at the end of `~/.bashrc` (and `~/.zshrc`, or a file in `~/.config/fish/conf.d/`, if you use those shells). `--uninstall` takes the block out again and leaves the rest of the file alone.

It also makes Kestrel the system's **file chooser**: the Open and Save dialogs that apps get through the desktop portal (xdg-desktop-portal), such as a browser's "Save image as", Flatpak and Snap apps, and GTK 4 and Qt apps that use the portal. Those dialogs open as a Kestrel window with a bar at the bottom for the file name, the file type, Cancel and Save/Open, so you browse with the sidebar, previews and search as usual. Saving over a file asks first, and the next dialog starts where the last one picked something. The portal chooses its file chooser per desktop, not per app, so this applies to every app that uses it. The installer adds a D-Bus activation file for `kes --file-chooser`, installs the portal definition `/usr/share/xdg-desktop-portal/portals/kestrel.portal` (the portal reads these only from there, so this one file asks for your password), writes `~/.config/xdg-desktop-portal/<desktop>-portals.conf` with your desktop's current choices plus Kestrel for the file chooser (GNOME's stays as the fallback; a portals.conf you already had is kept and changed), and restarts the portal. `--uninstall` puts it all back.

It also installs a small GNOME Shell extension, **Kestrel drop focus**, so that when you drag files from Kestrel into another app (a browser, an editor, a chat window) that app gets the focus, as it would on Windows. On GNOME on Wayland an app can't focus another app's window, so the extension does it when Kestrel asks; GNOME loads a newly installed extension at your next log-in, so log out and back in once. On X11 (Linux Mint, GNOME on Xorg) Kestrel needs no extension. The extension is copied to `~/.local/share/gnome-shell/extensions/` (the .deb installs it for everyone) and added to your enabled extensions; `--uninstall` takes it out again and leaves your other extensions alone.

It also makes Kestrel the app for `trash:///` (the dock's Trash icon, `gio open trash:///`), remembering which app had it so `--uninstall` can put it back. If GNOME Files is pinned in the dock, `--default` asks whether to put Kestrel in its place; `--dock` does that without asking. `--uninstall` puts GNOME Files back if the installer swapped it.

The installer does all of this (everything but the PATH) by running `kes-setup`, which you can also run on its own: `kes-setup --default`, `kes-setup --dock`, and `kes-setup --undo` to put back what they changed. It sets things up for the `kes` next to it, or the one given with `--kes PATH`.

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

### pip (PyPI)

Kestrel is also on [PyPI](https://pypi.org/project/kestrel-explorer/) as `kestrel-explorer`. It's an alpha, and `install.sh` is still the fuller install (see the comparison below).

**Requirements**
- Linux, with **Python 3.11.4 or newer**. On Ubuntu 24.04 / Linux Mint 22 (Python 3.12) and Ubuntu 26.04 that's the system's `/usr/bin/python3`. Anaconda's `base` Python is often older: pip then says *"from versions: none"*. Make the virtual environment with `/usr/bin/python3`, or use a conda environment with a newer Python (see [With conda](#with-conda)).
- pip installs the Python packages (`PyQt6`, `Pillow`) itself. The system programs Kestrel calls can't come from pip; install them with apt:

  ```bash
  sudo apt install 7zip unrar zip unzip pigz zpaq zstd xz-utils bzip2 lzip \
                   libimage-exiftool-perl ffmpeg pkexec gvfs gvfs-backends udisks2 \
                   python3-pyqt6 python3-pil python3-gi kimageformat6-plugins
  ```

  Each one is optional: without it, the feature that needs it is hidden or falls back (see [Dependencies](#dependencies) for what each is for). The last line is only for the recommended install below. On Ubuntu 24.04 / Linux Mint 22, leave out `kimageformat6-plugins` (it doesn't exist there).

**Install** (recommended: a virtual environment that uses the system's PyQt6, Pillow and GIO)

```bash
/usr/bin/python3 -m venv --system-site-packages ~/.local/share/kestrel-venv
~/.local/share/kestrel-venv/bin/pip install kestrel-explorer
~/.local/share/kestrel-venv/bin/kes                 # or link it: ln -s ~/.local/share/kestrel-venv/bin/kes ~/.local/bin/kes
```

With `--system-site-packages`, pip reuses Ubuntu's `python3-pyqt6`, `python3-pil` and `python3-gi` instead of downloading its own, so Kestrel gets the system's Qt image plugins (RAW, HEIC, AVIF, JPEG XL, PSD with `kimageformat6-plugins`) and GIO (Open With, default apps, drive and network mounting). In a plain virtual environment (without `--system-site-packages`), pip installs PyQt6 with its own Qt, which can't load those plugins, and the GIO features stay off unless you add the `[gio]` extra (`pip install "kestrel-explorer[gio]"`, which builds PyGObject and needs `libgirepository-2.0-dev` and `libcairo2-dev`).

The first launch adds Kestrel to the app grid (`~/.local/share/applications/kestrel-explorer.desktop`, pointing at that `kes`).

**Update / uninstall**

```bash
~/.local/share/kestrel-venv/bin/pip install --upgrade kestrel-explorer
rm -rf ~/.local/share/kestrel-venv ~/.local/share/applications/kestrel-explorer.desktop   # uninstall
```

As with `install.sh`, your settings, bookmarks and thumbnails are kept.

**What's different from `install.sh`**
- **Default folder app, file chooser, dock:** `kes-setup` isn't in the pip package. Run it from a clone of this repo: `./kes-setup --kes ~/.local/share/kestrel-venv/bin/kes --default` (give the conda environment's `kes` if you use conda), and `--undo` to put things back.
- **Drop focus extension:** not included, so on GNOME on Wayland, apps you drag files into don't get the focus. Install it with `install.sh` or the .deb if you want it.
- **Admin session:** the same as with `install.sh`: it needs `pkexec` and runs its small helper with the system `/usr/bin/python3`, so the password prompt names `/usr/bin/python3`.
- **App-grid entry:** a basic one, without the "New Window" action.

See [PACKAGING.md](https://github.com/RegulusArms/kestrel-explorer/blob/main/PACKAGING.md) for how the package is built and published.

#### With conda

If you use Anaconda or Miniconda, Kestrel can live in its own conda environment instead. conda provides the Python, and pip installs Kestrel and its PyQt6 (with its own Qt) into the environment:

```bash
conda create -n kestrel python=3.12 pip
conda activate kestrel
pip install kestrel-explorer
conda deactivate
```

The differences from `install.sh` above apply here too. Start it by its full path, without activating the environment, and link it onto your PATH if you like:

```bash
~/anaconda3/envs/kestrel/bin/kes                 # `conda env list` shows the folder (~/miniconda3/envs/… for Miniconda)
ln -s ~/anaconda3/envs/kestrel/bin/kes ~/.local/bin/kes
```

Started this way, Kestrel puts the system's programs and libraries ahead of Anaconda's (gsettings, gio, ffmpeg, xz…), which match the desktop. If you start it with the environment activated, it leaves the environment exactly as it is, so the environment's copies of those programs win. The app-grid entry made on first launch also points at the environment's `kes`.

The system programs still come from apt (see Requirements). Compared with the recommended install, a conda environment can't use the system's PyQt6 or GIO, so it has the same limits as a plain virtual environment: no RAW, HEIC, AVIF, JPEG XL or PSD previews, and no Open With, default apps or drive and network mounting.

To update: `~/anaconda3/envs/kestrel/bin/pip install --upgrade kestrel-explorer`. To uninstall: `conda env remove -n kestrel`, then delete `~/.local/share/applications/kestrel-explorer.desktop` and the `~/.local/bin/kes` link if you made one.

## Dependencies

**Supported systems**
- **Ubuntu 26.04 (GNOME):** the main platform; everything is tested here.
- **Linux Mint 22 (Cinnamon):** supported since 0.2.0. Kestrel builds and runs there, and the [benchmark against Nemo](#performance-kestrel-vs-nemo) was run there.
- **Ubuntu 24.04:** has the same base as Linux Mint 22, so it should work the same, but it isn't tested yet.

Package names below are Ubuntu's; Linux Mint uses the same ones. Everything else Kestrel uses is in the Python standard library.

**What's different on Linux Mint 22 and Ubuntu 24.04.** They're based on an older Ubuntu, with Qt 6.4, the oldest Qt Kestrel works with:
- `kimageformat6-plugins` doesn't exist there, so the installer skips it and RAW and HEIC files get no preview.
- SVG support comes in `libqt6svg6` instead of `qt6-svg-plugins`. The installer picks whichever the system has.
- Qt 6.4 takes no colours from the GTK theme, so Kestrel reads them through GTK itself (with the system's `python3`, `python3-gi` and `gir1.2-gtk-3.0`, which Mint has) and follows theme changes while it runs.
- On Cinnamon, Kestrel uses Cinnamon's own settings for the wallpaper, file history and icon theme. `--default` remembers Nemo and puts it back on `--uninstall`, and the file chooser opens as the app's dialog (X11). `--dock` only works with GNOME's dock for now.

**Anaconda / conda:** if `conda init` put Anaconda first on your PATH, Kestrel still uses the system's programs and libraries (gsettings, gio, ffmpeg, xz…), because Anaconda's copies don't match the desktop (its `gsettings` can't see your real settings, for example). Anaconda's folders are moved to the end of the search paths at startup. If you start Kestrel from an environment you activated yourself (`conda activate myenv`; anything but the auto-activated `base`), the environment is left exactly as it is.

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
| `gvfs`, `gvfs-backends` | Network locations (`smb://`, `sftp://`, `nfs://`, `ftp://`…), the drive list on the Overview page, and phones and cameras (iPhone, Android, PTP cameras; iPhones also use `usbmuxd`, which comes with them) |
| `udisks2` | Mounting, unlocking and ejecting drives from the Overview page |
| `qt6-image-formats-plugins` | WebP, TIFF, TGA, ICNS and MNG images |
| `qt6-svg-plugins` (in `libqt6svg6` on Ubuntu 24.04 / Linux Mint 22) | SVG images |
| `kimageformat6-plugins` | Camera RAW (`.raf`, `.cr2`, `.cr3`, `.nef`, `.arw`, `.dng`…), HEIC, AVIF, JPEG XL and PSD. Without it these files get no preview and don't open in the viewer |
| `adwaita-icon-theme` | Fallback icons when your icon theme is missing one |
| `qt6-gtk-platformtheme` | The desktop's theme on GTK desktops (GNOME, Cinnamon, MATE, Xfce, Budgie): its colours, accent, light or dark mode and fonts. Without it Qt uses its own generic look there. KDE Plasma has its own (`plasma-integration`) |
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

**File management**
- Tabs, back/forward history, a clickable path bar (Ctrl+L to type a path), and a sidebar.
- Follows the desktop's theme: light or dark, accent colour, fonts and icons, including switching while Kestrel is open. On GTK desktops this needs `qt6-gtk-platformtheme` (see [Dependencies](#dependencies)).
- **Other apps' Open and Save dialogs** (a browser's "Save image as", Flatpak and Snap apps, and GTK 4 and Qt apps that use the desktop portal) open as a Kestrel window, with the file name, the file type and Save/Open at the bottom, once you've run `./install.sh --default` (see [Install / run](#install--run)).
- The sidebar shows standard places, your GTK bookmarks (shared with GNOME Files), mounted drives and connected phones, with an unmount option. Drag entries to reorder them within a section, drag a section header to move the whole section, and click a header to collapse it.
- Right-click a bookmark → **Edit Bookmark…** to change its name and location (with a folder picker), or remove it or move it up and down.
- Grid and list views, sorting, hidden files (Ctrl+H).
- Search the current folder by typing to filter, or search subfolders recursively. Wildcards are supported.
- Cut, copy and paste work with GNOME Files' clipboard. Paste as link and pasting image data are supported.
- Drag and drop:
  - Ctrl copies, Shift moves, Ctrl+Shift creates a link, and Alt asks what to do.
  - With no key held, a drop moves files on the same drive and copies them to another drive.
- File operations run on background threads, so the window stays responsive even with tens of thousands of files. Copy, move, duplicate, move to trash, permanent delete, restore, empty trash, compress and extract show their status and a progress bar in the status bar at the bottom of the window, with ✕ to cancel. When several run at once, the bar shows the oldest with "+N more" (hover to see them all). Operations running in your other Kestrel windows are counted too ("+N in other windows"), or shown when this window has none; ✕ on one of those asks its window to cancel it, and 🛡 marks admin-session jobs. Closing a window while operations are running asks whether to stop them or keep going.
- If a name already exists when copying or moving, you can replace, merge, skip or keep both.
- Trash, permanent delete, and restoring or emptying the trash. The Trash shows everything you've deleted on every drive in one list: your home trash plus the trash folder each drive keeps for files deleted on it (`.Trash-<uid>`, the same as GNOME Files). The Location column shows where each item came from, and Restore, Delete Permanently and Empty Trash work across all of them.
- Deleting handles read-only folders you own (common in extracted Windows archives): they're made writable and deleted.
- Links and shortcuts:
  - Symbolic links (absolute or relative) and hard links.
  - Link to the Desktop.
  - `.desktop` shortcuts, marked as trusted so they can be launched.
- Rename (F2). Batch rename works with templates (`[Name] ###`, `[Date]`) or find and replace with regex.
- Duplicate, Move To…, Copy To…, and Compress / Extract (see [Archives](#archives)).
- **Undo (Ctrl+Z)** for moves, renames (also batch), Move to Trash, and anything created by copy, paste, duplicate, new folder/file or links (undoing those moves them to the trash). The Edit menu shows what will be undone. Permanent deletes, merges into existing folders, archives and admin-session operations can't be undone. Each Kestrel undoes only what was done in it (its own windows); turn on Preferences → *Share undo between all Kestrel windows* to have Ctrl+Z in any window undo the newest action from any Kestrel. The shared history is kept by the tower (see below) and is cleared when the last Kestrel closes.
- **Starred and Recent** in the sidebar. Right-click anything → **Star** (starred items show a ★). Recent lists the desktop's recently used files; files you open from Kestrel are added unless "File History" is off in GNOME's Privacy settings.
- **Several Kestrels stay in sync.** Each folder opened from another app may start its own Kestrel, so a crash only closes that one. A small background helper, the tower (`kes --atc`, started automatically and gone about 10 seconds after the last Kestrel closes), passes changes between them: saved Preferences, stars, folder colours, covers and preview switches, bookmarks, and cleared preview caches show up in every open window straight away, and every window's status bar also shows the operations running in the others. A newly opened window sees operations already running, and a Kestrel that closes or crashes drops out of the list. With Preferences → *Open folders from other apps as tabs in an open Kestrel window* (off by default), a folder opened from another app, from the terminal (`kes ~/Pictures`) or with "Show in folder" becomes a tab in the Kestrel window you used last, instead of a new window. Starting Kestrel without a folder (e.g. from the app grid) still opens a new window, and a Kestrel started from a conda environment you activated always opens its own. The Python and C++ versions share one tower. Admin sessions are not shared: each window keeps its own.
- **Search file contents**: the search bar's *File contents* option searches inside files using the desktop's search index (`localsearch`), within the current folder and its subfolders.
- **Thumbnails for other files** (PDFs, fonts, audio covers, comics…) come from the system thumbnailers GNOME Files uses. Images and videos keep Kestrel's own fast thumbnailing; a system thumbnailer is only a fallback when an image can't be decoded.
- **Extras GNOME Files offers through extensions:** *Open in Visual Studio Code* (and VSCodium, Cursor, Zed, Sublime Text) and *Open With* for folders; your **Nautilus scripts** (`~/.local/share/nautilus/scripts`) under *Scripts*, with the same `NAUTILUS_SCRIPT_*` variables; **Send To** → Email or Bluetooth; and **Network Sharing…** for folders (Samba usershares, needs the `samba` package).
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

**Overview (default homepage)**
- Like the old GNOME Files "Other Locations" page:
  - **Drives:** every drive with a usage bar (amber at 75%, red at 90%) and eject/unmount buttons. Unmounted drives can be mounted with a click, and encrypted drives ask for their passphrase. A ZFS pool appears as one card with pool-level numbers.
  - **Phones & Cameras:** connected phones and cameras (see below), or what to do on a phone to connect it.
  - **Network:** connected shares, plus a "Connect to Server" box for `smb://`, `sftp://`, `nfs://`, `ftp://` and similar addresses. Recent servers are remembered.
  - **Bookmarks:** your bookmarks as cards with folder previews. Right-click a card to edit or remove it.
- Preferences → Homepage can be the Overview, your home folder, or any folder or network address. The homepage opens at launch and with the Home button (Alt+Home).

**Phones and cameras**
- iPhones, Android phones and cameras (PTP) appear under **Phones & Cameras** on the Overview page and under Devices in the sidebar, through the desktop's own phone support (gvfs). Click one to connect it; Eject disconnects it. An iPhone has two entries: its photos and videos, and the files its apps share.
- If a phone doesn't show up or won't connect, Kestrel says what to do on it: unlock it and tap "Trust" (iPhone), choose "File transfer" in its USB notification (Android), or switch the camera to photo transfer (PTP).
- Thumbnails come from the phone's own small previews where it has them (an iPhone's photos, Android phones). Otherwise files are read one at a time, images up to 30 MB. Folders on a phone get no preview mosaic, since that would download every file.
- Videos from an iPhone's photos are copied to your computer before they play, because the phone sends the whole file every time it's opened. The status bar shows "Loading from device: … — will launch once ready" with a pulsing bar, and the video opens when it's there. Copies are kept in `~/.cache/kestrel-explorer/device-files`, reused when you open the video again, and deleted after a day.

**Images and galleries**
- Folder icons show a 1–4 image mosaic. If a folder has no images of its own, the mosaic is taken from its subfolders.
- A folder with only videos uses a still from the middle of each video, marked with a ▶ badge.
- Right-click a folder → **Generate Previews Recursively** pre-builds every thumbnail and folder preview below it in the background. Progress shows in the status bar, and ✕ stops it.
- The **Folder previews** checkbox in the toolbar (Ctrl+Shift+P) turns the mosaics off for speed in large or slow folders. Image file thumbnails stay on.
- If a folder contains an image named `cover`, `folder`, `front` or `poster` (any extension, also hidden as `.cover` or `.folder`), that image is used as the cover. You can also right-click an image and choose "Use as Folder Cover", and "Reset Folder Cover" to undo it.
- Right-click a folder → **Regenerate Preview** rebuilds its mosaic. The ☰ menu has **Clear Folder Preview Cache** and **Delete All Thumbnails…** to free disk space or start fresh.
- Right-click one or more folders → **Folder Colour** to give their icons one of 10 colours (or back to Default, which follows the desktop's accent colour unless you pick a fixed one in Preferences). Only the folder behind the image mosaic changes colour; the previews stay. **Show Image Previews** turns the mosaic off for just those folders (they keep their colour). Both settings are also on the folder's Properties → General tab.
- Thumbnails are generated in background threads and support JPEG, PNG, GIF, WebP, TIFF, SVG and video (with `ffmpeg`). AVIF, HEIC, JPEG XL, PSD and camera RAW also work when `kimageformat6-plugins` is installed (see [Dependencies](#dependencies)).
- Thumbnails are shared with GNOME Files through the freedesktop cache (`~/.cache/thumbnails`).
- Zoom with Ctrl+scroll or the slider, from 48 to 320 px.
- **Animated GIFs and WebM videos can play right in the file view** (Preferences → *Play animated GIFs* / *Play WebM videos in the file view*, both off by default). WebM files play as silent looping previews of their first 15 seconds, made once with `ffmpeg` and cached in `~/.cache/kestrel-explorer/animated`. Only items on screen play.
- Built-in viewer: arrow keys or the scroll wheel to move between images, zoom and pan, fullscreen (F), slideshow (S), rotate (R/L) and flip (H), an info overlay (I), copy the image (Ctrl+C), trash (Delete), and animated GIF/WebP.
- Images and videos open in your system's default app unless you choose otherwise. In Preferences you can pick **Open images with** (system default, Kestrel's built-in viewer, or any installed image app) and **Open videos with** (system default or any installed video app). "View Image" in the right-click menu always uses the built-in viewer.
- The info panel (F3) shows EXIF details: camera, lens, exposure and GPS.
- It also shows Stable Diffusion / ComfyUI prompts and settings embedded in PNG, WebP and JPEG files.

Press F1 in the app for all keyboard shortcuts. Settings are in the ☰ menu under Preferences: homepage, how many images a mosaic uses, whether it picks images by name or newest first, folder colour (the desktop's accent colour unless you pick one), the thumbnail size limit, which apps open images and videos, single-click, folder previews in list view, slideshow speed, playing GIFs and WebM videos, sharing undo between windows, and opening folders from other apps as tabs.

## Archives

Double-clicking an archive opens Kestrel's Extract dialog instead of another app (packages, disk images and apps such as `.deb`, `.iso` and `.apk` still open with the system's app). Kestrel doesn't contain its own compression code. It drives the command-line tools installed on your system, and offers the formats those tools support. Anything whose tool is missing is shown greyed out with the package to install. `./install.sh --install-recommended` installs the common ones (see [Dependencies](#dependencies)).

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

## Tests

```bash
tests/run.sh                  # every test
tests/run.sh fileops atc_undo # only some
```

There are 236 checks in 10 tests: file operations (copy, move, merge, delete, cancel, trash, links, undo), the tower that keeps several Kestrels in sync (shared changes, the shared task list, shared undo, opening folders as tabs), phones and cameras, rearranging the sidebar, following the desktop theme, the file chooser, and `install.sh`. Each test runs with a throwaway home folder on a private D-Bus bus, so your files, settings, dock and open windows are never touched. The [C++ version](https://github.com/RegulusArms/kes-c/tree/main/tests) has the same tests, and some checks launch the other version to test the two together. Details: [tests/README.md](https://github.com/RegulusArms/kestrel-explorer/blob/main/tests/README.md).

## Layout

| File | Purpose |
|---|---|
| `kestrel/app.py` | Main window, tabs, browser pane (including the combined trash view), actions, context menus |
| `kestrel/widgets.py` | File-system model, grid delegate, path bar, sidebar, info panel, search |
| `kestrel/thumbs.py` | Background thumbnail and folder-mosaic generation and caching |
| `kestrel/viewer.py` | Image viewer |
| `kestrel/fileops.py` | Background tasks and the status-bar task panel; copy, move and delete (retrying denied ones as administrator); conflict handling; links |
| `kestrel/places.py` | Starred and Recent: places that list files from anywhere |
| `kestrel/undo.py` | Undo for file operations, shared by a Kestrel's windows (or every Kestrel's) |
| `kestrel/sharing.py` | What GNOME Files offers through extensions: code editors, Nautilus scripts, Send To, Samba sharing |
| `kestrel/animate.py` | Animated GIFs and WebM clips playing in the file view |
| `kestrel/fm1.py` | The `org.freedesktop.FileManager1` service ("Show in folder" from browsers and other apps) |
| `kestrel/env.py` | Using the system's programs and libraries instead of Anaconda's (see [Dependencies](#dependencies)) |
| `kestrel/chooser.py` | The system's file chooser: the portal backend (`kes --file-chooser`) and the Save/Open bar of chooser windows |
| `kestrel/focus.py` | Giving the focus to the app files are dropped into: on X11 directly, on GNOME on Wayland through the drop focus extension (`data/gnome-shell/`) |
| `kestrel/admin.py` | The admin session: starts the root helper once, sends it operations, the 🛡 status-bar indicator, and "Retry as Administrator" |
| `kestrel/admin_helper.py` | The small root helper (standard library only) that performs admin-session operations |
| `kestrel/dialogs.py` | Properties (including the metadata editor and Add Tag picker), Open With, rename, batch rename, Edit Bookmark, preferences |
| `kestrel/metadata.py` | EXIF, AI-generation metadata, exiftool reading/editing, and the tag catalog for the Add Tag picker (the tag descriptions and "Accepts" types are AI-generated and may not be completely accurate) |
| `kestrel/overview.py` | Overview page: drives, phones and cameras, network locations, bookmarks |
| `kestrel/util.py` | Shared helpers: paths, file types, icons, desktop integration (default apps, wallpaper, terminal), trash on every drive, GTK bookmarks |
| `kestrel/archive.py` | Archive engine: tool detection, the commands for every format, progress, cancel, password handling |
| `kestrel/archive_ui.py` | Compress and Extract dialogs, password prompts, and the archive job flows |
| `kestrel/uwp.py` | Integration with the UWP wallpaper manager (over D-Bus and the `uwp` command) |
| `kestrel/atc.py` | The tower (`kes --atc`) and each window's link to it, which keep several running Kestrels in sync |
| `tests/` | The test suite (see [Tests](#tests)) |
| `bench/` | The benchmark against GNOME Files and Nemo (see Performance: [vs GNOME Files](#performance-kestrel-vs-gnome-files), [vs Nemo](#performance-kestrel-vs-nemo)) |
| `pyproject.toml` | PyPI packaging metadata (see [PACKAGING.md](https://github.com/RegulusArms/kestrel-explorer/blob/main/PACKAGING.md)) |
| `.github/workflows/pypi.yml` | Publishes to PyPI by hand, with trusted publishing (see [PACKAGING.md](https://github.com/RegulusArms/kestrel-explorer/blob/main/PACKAGING.md)) |

## Performance: Kestrel vs GNOME Files

Kestrel Explorer exists in two versions with the same features: the original [Python/PyQt6 version](https://github.com/RegulusArms/kestrel-explorer) and the [C++/Qt 6 port](https://github.com/RegulusArms/kes-c). They share settings, bookmarks and caches, so you can switch between them. Both are compared here with GNOME Files 50.2.2, the file manager they replace.

**Test machine:** AMD Ryzen Threadripper 2950X 16-Core Processor (32 threads), Ubuntu 26.04.1 LTS. The test data is on a RAM disk: 600 JPEGs at 1600×1200 with camera EXIF, 40 videos, 40 PDFs, 150 folders of 4 images, a tree of 50,000 files, 20,000 small files plus 250 MB, a folder of 10,000 files, and 200 PNGs with AI-generation metadata.

**How it was measured:** each test ran 3 times, and the tables show medians. Every run started with a fresh home folder, so the thumbnail cache was empty. All three apps ran on a headless X server with software rendering (Qt's raster engine, GTK's cairo renderer), on a private session bus where only the desktop's settings and virtual file system (gvfs) services could start, so no file indexer ran. The benchmark is in [bench/](https://github.com/RegulusArms/kestrel-explorer/tree/main/bench) and is run with `bench/run.sh`.

### Compared with GNOME Files

All three apps are measured in the same way:
- **Startup:** timed until the window is on screen.
- **Opening a folder:** timed from launch until the first 12 files' thumbnails are in the shared thumbnail cache.
- **File operations:** Kestrel runs them with its own copy and trash code, the same code its menus use. GNOME Files receives them through its D-Bus file-operations service, as when another app asks it to.
- **"Peak memory":** the app's highest memory use.
- **Several folders opened from other apps:** the 5 folders are opened one after another, as from a browser's "Show in folder". The figure is the private memory of everything the app then runs: what closing it would give back, not counting the libraries it shares with other apps. Kestrel normally starts a new process for each window (6 processes here, counting the tower that keeps them in sync); with "open folders as tabs" on, they become tabs in one window.
- **CPU while idle:** the CPU time all the app's processes use in 30 s with a folder open and nothing happening. It's counted in 10 ms steps.

| Test | Kestrel (Python) | Kestrel (C++) | GNOME Files |
|---|---|---|---|
| Startup (launch to window shown) | 0.51 s | 0.36 s | 0.44 s |
| Open a 600-image folder (launch to the first 12 thumbnails) | 0.60 s | 0.40 s | 1.28 s |
| Open a folder of 40 videos (launch to the first 12 thumbnails) | 2.22 s | 2.05 s | 8.39 s |
| Open a folder of 40 PDFs (launch to the first 12 thumbnails) | 1.06 s | 0.77 s | 0.92 s |
| Copy 20,000 small files + 5 × 50 MB | 1.72 s | 0.94 s | 2.34 s |
| Move the same to another drive | 1.95 s | 1.16 s | 4.54 s |
| Move 10,000 files to the trash (all selected in one folder) | 2.00 s | 1.68 s | 2.00 s |
| Empty the trash (those 10,000 files) | 0.42 s | 0.34 s | 9.05 s |
| Peak memory (startup / 600-image folder open) | 176 / 188 MB | 137 / 148 MB | 196 / 305 MB |
| Memory with 5 folders opened from other apps | 214 MB (89 MB as tabs) | 79 MB (43 MB as tabs) | 96 MB |
| CPU time used in 30 s with a folder open, idle | 15 ms (0.05% of a core) | 20 ms (0.07% of a core) | 10 ms (0.03% of a core) |

Kestrel (C++) compared with GNOME Files:
- startup: 1.2× faster;
- first thumbnails in a 600-image folder: 3.2× faster;
- first video thumbnails: 4.1× faster;
- first PDF thumbnails: 1.2× faster;
- copying: 2.5× faster;
- moving to another drive: 3.9× faster;
- moving to the trash: 1.2× faster;
- emptying the trash: about 27× faster.

**Limits of this comparison:**
- **Opening a folder:** the two apps don't do the same amount of work.
  - GNOME Files makes thumbnails for the whole folder in the background. It finished all 600 images 11.72 s after launch.
  - Kestrel makes them only for what's on screen (40 images here), and the rest as you scroll.
  - To thumbnail a whole folder at once, Kestrel has "Generate Previews": 5.02 s for these 600 images in the C++ version (table below).
- **File operations:** GNOME Files' D-Bus service returns straight away, so its times were measured by watching the files until the operation had finished, to within about 50 ms.
- **Memory:** GNOME Files makes thumbnails in separate sandboxed helper processes, whose memory isn't counted in its figures. Kestrel makes them inside the app.
- **Search:** GNOME Files' search can't be timed from outside without its file indexer, so it isn't compared.

### Kestrel's own features (Python vs C++)

These are measured inside the app, because GNOME Files has no equivalent ("Generate Previews", folder mosaics, metadata panels) or can't be timed from outside (search).

| Test | Python | C++ | C++ speed-up |
|---|---|---|---|
| Thumbnail all 600 images ("Generate Previews") | 5.24 s | 5.02 s | about the same |
| Build 150 folder mosaics | 6.79 s | 6.50 s | about the same |
| Recursive search over 50,000 files | 0.19 s | 0.12 s | 1.6× faster |
| Read EXIF / AI metadata for 400 images | 1.71 s | 0.043 s | about 40× faster |
| Peak memory (background jobs) | 74–82 MB | 37–41 MB | |

Thumbnails and mosaics take about as long in both versions, because both decode images with the same Qt C++ code, which the Python version already runs on several threads. The C++ version is much faster where the Python version does the work in Python itself, such as reading metadata, searching and copying, and it uses about half the memory.

## Performance: Kestrel vs Nemo

Kestrel Explorer exists in two versions with the same features: the original [Python/PyQt6 version](https://github.com/RegulusArms/kestrel-explorer) and the [C++/Qt 6 port](https://github.com/RegulusArms/kes-c). They share settings, bookmarks and caches, so you can switch between them. Both are compared here with Nemo, the file manager they replace.

**Test machine:** a VirtualBox virtual machine running Linux Mint 22 (Cinnamon). The test data is on a RAM disk: 600 JPEGs at 1600×1200 with camera EXIF, 40 videos, 40 PDFs, 150 folders of 4 images, a tree of 50,000 files, 20,000 small files plus 250 MB, a folder of 10,000 files, and 200 PNGs with AI-generation metadata.

**How it was measured:** each test ran 3 times, and the tables show medians. Every run started with a fresh home folder, so the thumbnail cache was empty. All three apps ran on a headless X server with software rendering (Qt's raster engine, GTK's cairo renderer), on a private session bus where only the desktop's settings and virtual file system (gvfs) services could start, so no file indexer ran. The benchmark is in [bench/](https://github.com/RegulusArms/kestrel-explorer/tree/main/bench) and is run with `bench/run.sh`.

### Compared with Nemo

All three apps are measured in the same way:
- **Startup:** timed until the window is on screen.
- **Opening a folder:** timed from launch until the first 12 files' thumbnails are in the shared thumbnail cache.
- **File operations:** Kestrel runs them with its own copy and trash code, the same code its menus use. Nemo receives them through its D-Bus file-operations service, as when another app asks it to.
- **"Peak memory":** the app's highest memory use.
- **Several folders opened from other apps:** the 5 folders are opened one after another, as from a browser's "Show in folder". The figure is the private memory of everything the app then runs: what closing it would give back, not counting the libraries it shares with other apps. Kestrel normally starts a new process for each window (6 processes here, counting the tower that keeps them in sync); with "open folders as tabs" on, they become tabs in one window.
- **CPU while idle:** the CPU time all the app's processes use in 30 s with a folder open and nothing happening. It's counted in 10 ms steps.

| Test | Kestrel (Python) | Kestrel (C++) | Nemo |
|---|---|---|---|
| Startup (launch to window shown) | 0.94 s | 0.61 s | 0.81 s |
| Open a 600-image folder (launch to the first 12 thumbnails) | 1.07 s | 0.74 s | — |
| Open a folder of 40 videos (launch to the first 12 thumbnails) | 3.53 s | 3.36 s | — |
| Open a folder of 40 PDFs (launch to the first 12 thumbnails) | 1.66 s | 1.10 s | — |
| Copy 20,000 small files + 5 × 50 MB | 3.84 s | 1.89 s | 26.85 s |
| Move the same to another drive | 3.37 s | 1.61 s | 7.80 s |
| Move 10,000 files to the trash (all selected in one folder) | 4.33 s | 3.40 s | — |
| Empty the trash (those 10,000 files) | 1.35 s | 0.76 s | — |
| Peak memory (startup / 600-image folder open) | 146 / 157 MB | 114 / 123 MB | 124 / — MB |
| Memory with 5 folders opened from other apps | 159 MB (70 MB as tabs) | 56 MB (35 MB as tabs) | 51 MB |
| CPU time used in 30 s with a folder open, idle | 45 ms (0.15% of a core) | 35 ms (0.12% of a core) | 6375 ms (21.25% of a core) |

Kestrel (C++) compared with Nemo:
- startup: 1.3× faster;
- copying: about 14× faster;
- moving to another drive: 4.9× faster.

**Limits of this comparison:**
- **Opening a folder:** the two apps don't do the same amount of work.
  - Nemo wasn't timed on this (see below).
  - Kestrel makes them only for what's on screen (40 images here), and the rest as you scroll.
  - To thumbnail a whole folder at once, Kestrel has "Generate Previews": 7.54 s for these 600 images in the C++ version (table below).
- **File operations:** Nemo's D-Bus service returns straight away, so its times were measured by watching the files until the operation had finished, to within about 50 ms.
- **Opening a folder not timed:** Nemo makes no thumbnails in this setup (a fresh home folder with its default settings), so the folder tests are skipped for it and those rows show —, as does its peak memory with the 600-image folder open.
- **Not timed:** Nemo's D-Bus file-operations service has no way to move files to the trash, so those rows show —. For "Empty the trash", the files were put in the trash with `gio trash` first.
- **CPU while idle:** Nemo kept about 21% of a core busy with nothing happening. That's unusual for a file manager at rest, so it probably comes from this headless setup (where it also made no thumbnails) rather than from everyday use.
- **Didn't finish:** asked through its D-Bus service to empty the trash, Nemo didn't finish within 2 minutes (it may have been waiting for a confirmation), so those rows show —.
- **Memory:** Nemo makes thumbnails in separate helper processes, whose memory isn't counted in its figures. Kestrel makes them inside the app.
- **Search:** Nemo's search can't be timed from outside, so it isn't compared.

### Kestrel's own features (Python vs C++)

These are measured inside the app, because Nemo has no equivalent ("Generate Previews", folder mosaics, metadata panels) or can't be timed from outside (search).

| Test | Python | C++ | C++ speed-up |
|---|---|---|---|
| Thumbnail all 600 images ("Generate Previews") | 8.09 s | 7.54 s | 1.1× faster |
| Build 150 folder mosaics | 10.79 s | 10.76 s | about the same |
| Recursive search over 50,000 files | 0.25 s | 0.41 s | 1.7× slower |
| Read EXIF / AI metadata for 400 images | 2.40 s | 0.051 s | about 47× faster |
| Peak memory (background jobs) | 57–64 MB | 25–30 MB | |

Thumbnails and mosaics take about as long in both versions, because both decode images with the same Qt C++ code, which the Python version already runs on several threads. The C++ version is much faster where the Python version does the work in Python itself, such as reading metadata, searching and copying, and it uses about half the memory.

## License

MIT. See [LICENSE](https://github.com/RegulusArms/kestrel-explorer/blob/main/LICENSE).
