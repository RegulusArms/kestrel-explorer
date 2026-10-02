# Kestrel Explorer

**Version 0.1.1-alpha.** This is an early alpha release, so expect rough edges.

A lightweight file manager for Ubuntu, written in Python and PyQt6. It is built mainly for browsing image galleries. Folder icons show a mosaic of the images inside them. It also covers the everyday jobs you'd do in GNOME Files.

## Install / run

```bash
./install.sh             # adds the `kes` command to ~/.local/bin and the app to the app grid
./install.sh --default   # ...and makes it the default app for opening folders
./install.sh --uninstall

./kes ~/Pictures                 # or run it in place without installing
./kes --version                  # print the version
kes ~/Pictures                   # once installed
```

It uses the system Python (`/usr/bin/python3`), not a virtualenv or conda Python. `install.sh` installs the required packages.

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

**Recommended** (included in a standard Ubuntu desktop)

| Package | Used for |
|---|---|
| `libglib2.0-bin` | `gio` (launching `.desktop` shortcuts, unmounting) and `gsettings` (icon theme, set as wallpaper) |
| `xdg-utils` | `xdg-open` (opening files when GIO isn't available) and `xdg-mime` (making Kestrel the default folder app) |
| `gvfs`, `gvfs-backends` | Network locations (`smb://`, `sftp://`, `nfs://`, `ftp://`…) and the drive list on the Overview page |
| `udisks2` | Mounting, unlocking and ejecting drives from the Overview page |
| `qt6-image-formats-plugins` | WebP, TIFF, TGA, ICNS and MNG images |
| `qt6-svg-plugins` | SVG images |
| `adwaita-icon-theme` | Fallback icons when your icon theme is missing one |
| A terminal (`ptyxis`, `gnome-terminal`, `kgx`, `konsole` or `xfce4-terminal`) | "Open in Terminal" |

**Optional** (features are hidden or fall back when missing)

| Package | Used for |
|---|---|
| `libimage-exiftool-perl` | The full Metadata tab, plus editing, adding and clearing metadata |
| `ffmpeg` | Video thumbnails and video stills in folder previews (`ffmpeg` and `ffprobe`) |
| `kimageformat6-plugins` | AVIF, HEIC, JPEG XL, PSD, camera RAW and other formats (which formats are included depends on the distro's build) |
| `7zip` (or `p7zip-full` on older releases) | Creating `.7z` archives and extracting `.7z`, `.rar` and other formats Python can't open |
| `zfsutils-linux` | Showing a ZFS pool as one card with pool-level usage on the Overview page |

```bash
# everything at once
sudo apt install python3-pyqt6 python3-pil python3-gi gir1.2-glib-2.0 libglib2.0-bin xdg-utils \
    gvfs gvfs-backends udisks2 qt6-image-formats-plugins qt6-svg-plugins adwaita-icon-theme \
    libimage-exiftool-perl ffmpeg kimageformat6-plugins 7zip
```

## Features

**Galleries**
- Folder icons show a 1–4 image mosaic. If a folder has no images of its own, the mosaic is taken from its subfolders.
- A folder with only videos uses a still from the middle of each video, marked with a ▶ badge.
- Right-click a folder → **Generate Previews Recursively** pre-builds every thumbnail and folder preview below it in the background. Progress shows in the status bar, and ✕ stops it.
- The **Folder previews** checkbox in the toolbar (Ctrl+Shift+P) turns the mosaics off for speed in large or slow folders. Image file thumbnails stay on.
- If a folder contains an image named `cover.*` or `folder.*`, that image is used as the cover. You can also right-click an image and choose "Use as Folder Cover".
- Thumbnails are generated in background threads and support JPEG, PNG, GIF, WebP, TIFF, SVG and video (with `ffmpeg`). AVIF, HEIC, JPEG XL, PSD and camera RAW also work when `kimageformat6-plugins` is installed (see [Dependencies](#dependencies)).
- Thumbnails are shared with GNOME Files through the freedesktop cache (`~/.cache/thumbnails`).
- Zoom with Ctrl+scroll or the slider, from 48 to 320 px.
- Built-in viewer: arrow keys or the scroll wheel to move between images, zoom and pan, fullscreen, slideshow (S), rotate and flip, trash (Delete), animated GIF/WebP.
- Images and videos open in your system's default app unless you choose otherwise. In Preferences you can pick **Open images with** (system default, Kestrel's built-in viewer, or any installed image app) and **Open videos with** (system default or any installed video app). "View Image" in the right-click menu always uses the built-in viewer.
- The info panel (F3) shows EXIF details: camera, lens, exposure and GPS.
- It also shows Stable Diffusion / ComfyUI prompts and settings embedded in PNG files.

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
- Copies run in the background with progress and a cancel button. If a name already exists you can replace, merge, skip or keep both.
- Trash, permanent delete, and restoring or emptying the trash.
- Links and shortcuts:
  - Symbolic links (absolute or relative) and hard links.
  - Link to the Desktop.
  - `.desktop` shortcuts, marked as trusted so they can be launched.
- Rename (F2). Batch rename works with templates (`[Name] ###`, `[Date]`) or find and replace with regex.
- Duplicate, Move To…, Copy To…, Compress (zip, tar.gz, tar.xz, 7z), Extract Here.
- Open With: shows recommended apps and lets you set the default app. You can also open a terminal in the current folder or set an image as wallpaper.
- Properties window:
  - **General:** size (recursive for folders), dates, inode, the free space on the drive, and the default app.
  - **Permissions:** an editable rwx grid plus setuid, setgid and sticky bits.
  - **Image:** image details.
  - **Metadata:** every tag exiftool can read, with a filter. With `exiftool` installed you can also edit the file's metadata, and changes are written straight to the file:
    - **Edit:** double-click a tag to change its value. List tags such as keywords are edited one item per line.
    - **Add Tag…:** a searchable list of the tags this file type supports, with columns for the tag, what it's for, and what it accepts (text, numbers only, date/time, a fixed choice, or true/false). The allowed values are shown for choice tags.
    - **Remove:** delete one or more selected tags.
    - **Clear All Metadata…:** strip everything, optionally keeping the orientation and colour profile so the image looks the same.
    - Greyed-out rows (file system info and values exiftool calculates) are read-only.
    - The tag descriptions and "Accepts" types are AI-generated and may not be completely accurate. Descriptions outside the common tags are built from exiftool's tag names and categories.
  - **Checksums:** MD5, SHA1 and SHA256, with a field to check against a known checksum.

Press F1 in the app for all keyboard shortcuts. Settings are in the ☰ menu under Preferences: homepage, how many images a mosaic uses, whether it picks images by name or newest first, folder colour, the thumbnail size limit, which apps open images and videos, single-click, folder previews in list view, and slideshow speed.

## Layout

| File | Purpose |
|---|---|
| `kestrel/app.py` | Main window, tabs, browser pane, actions, context menus |
| `kestrel/widgets.py` | File-system model, grid delegate, path bar, sidebar, info panel, search |
| `kestrel/thumbs.py` | Background thumbnail and folder-mosaic generation and caching |
| `kestrel/viewer.py` | Image viewer |
| `kestrel/fileops.py` | Copy, move and delete threads, conflict handling, links, archives |
| `kestrel/dialogs.py` | Properties, Open With, rename, batch rename, compress, preferences |
| `kestrel/metadata.py` | EXIF, AI-generation metadata, exiftool reading/editing, and the tag catalog for the Add Tag picker (the tag descriptions and "Accepts" types are AI-generated and may not be completely accurate) |
| `kestrel/overview.py` | Overview page: drives, network locations, bookmarks |
| `pyproject.toml` | PyPI packaging metadata (not ready for release yet — see [PACKAGING.md](PACKAGING.md)) |

## License

MIT. See [LICENSE](LICENSE).
