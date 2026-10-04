# Benchmark

Compares the two versions of Kestrel Explorer (Python and C++) with GNOME Files on the same data and in the same conditions. The results are the "Performance" section of the main README.

This folder is identical in both projects. It needs both of them side by side (`kes-c` and `kestrel-explorer` in the same parent folder), and it can be run from either.

```bash
bench/run.sh                       # build, generate the data the first time, run everything, print the tables
bench/run.sh --update-readme       # ...and write the tables into both projects' README.md
bench/run.sh --runs 5              # more runs per measurement (default 3; startup gets 2 extra)
bench/run.sh --only startup,copy   # re-run some measurements and keep the rest from the last run
bench/run.sh --report --update-readme   # rebuild the tables from the last results without running anything
```

A full run takes about 12 minutes, plus about a minute and a half the first time to generate the data. Raw results are saved in `bench/results.json`.

## Requirements

- `xvfb` and `xdotool` (`sudo apt install xvfb xdotool`);
- `ffmpeg`, for the video test data;
- `dbus-run-session`;
- the C++ version's build tools, since its app and benchmark harness are built Release;
- the Python version's packages;
- `nautilus`. Without it, the GNOME Files column shows "—".

## How it's measured

### Same conditions for every run

- **Headless display:** all three apps run on an Xvfb X server with software rendering: Qt's raster engine, and GTK with `GSK_RENDERER=cairo`. No windows appear on your screen.
- **Fresh home folder:** each run gets a new one, so the thumbnail cache is empty and settings are defaults.
- **Private session bus:** only the services every GNOME desktop runs can be started on it: settings (dconf) and the virtual file system (gvfs, which provides `trash:///`, the drive list and file metadata). Both are started before each measurement, because on a desktop they are already running when an app starts. No file indexer, portal or online-accounts service runs in the background, and your own running apps never see the benchmark.
- **RAM disk:** the data is in `/tmp/kestrel-bench-data`, so disk speed doesn't count. Set `KESTREL_BENCH_DATA` to use another place.
- **Medians:** each measurement runs several times, and the tables show the median.

### Compared with GNOME Files (measured from outside)

| Measurement | How |
|---|---|
| Startup | Launch the app on a small folder, and time it until its window is on screen (`xdotool`, checked every 10 ms). |
| Open a folder of 600 images, 40 videos or 40 PDFs | Launch the app on the folder, and time it until the first 12 files by name have thumbnails in the shared freedesktop thumbnail cache (`~/.cache/thumbnails`), which all three apps write to. The benchmark then waits until no new thumbnails appear for 2 s and records how many were made and when the last one appeared: Kestrel makes them only for what's on screen, GNOME Files for the whole folder. |
| Copy 20,000 small files + 5 × 50 MB | **Kestrel:** its own copy engine (`fileops.start_ops`, as used by paste and drag and drop), timed to completion. **GNOME Files:** its `org.gnome.Nautilus.FileOperations2.CopyURIs` D-Bus call, which other apps use to ask it to copy. That call returns at once, so the copy is timed by watching the destination, every 50 ms, until every file and byte has arrived. |
| Move the same to another drive | From the home folder (`/tmp`) to `/dev/shm`, a different filesystem, so the files are copied and then deleted, as with a USB stick. **Kestrel:** `start_ops` with a move. **GNOME Files:** `MoveURIs`, timed until everything has arrived and the source is gone. |
| Move 10,000 files to the trash, then empty it | All the files in one folder are selected and trashed. **Kestrel:** the window's own Move to Trash and Empty Trash actions, with the confirmation answered at once (the clock starts at the answer). **GNOME Files:** `TrashURIs`, then `EmptyTrash` without confirmation, each timed until the trash folders have filled or emptied. |
| Peak memory | The app process's peak resident memory (`VmHWM`), one second after startup and once the 600-image folder's thumbnails are done. GNOME Files makes thumbnails in separate sandboxed processes, which aren't counted. |
| Memory with 5 folders opened from other apps | The 5 folders are launched one after another, as from a browser's "Show in folder" or a terminal, and each launch waits for its window. Two seconds later, the private memory of every process the app runs is added up: what closing them would give back, without the libraries they share with each other and with other apps. That doesn't depend on which other apps happen to be running. That covers Kestrel's processes, including its tower, and GNOME Files' one process. Kestrel is measured twice: normally (a process per window), and with "open folders as tabs" on. |
| CPU time while idle | The app is launched on a small folder and left alone for 5 s. Then the CPU time all its processes use in the next 30 s is counted. The kernel counts it in 10 ms steps. This test runs twice, not three times. |

### Kestrel's own features (measured inside the app)

`kestrel_py.py` and `kestrel_cpp.cpp` run the same code the app runs for:
- "Generate Previews" on the 600 images;
- the mosaics for 150 folders;
- a recursive search for `*_7.jpg` among 50,000 files (500 matches);
- reading the EXIF and AI-generation info for 400 images.

Each one prints its time and peak memory. GNOME Files has no equivalent of these. Its search can't be timed from outside without its file indexer.

## Test data

`make_data.py` makes this set once (about 600 MB). Each part is made again only if its version in `make_data.py` changes:

| Folder | Contents |
|---|---|
| `gallery/` | 600 JPEGs at 1600×1200 with camera EXIF (make, model, exposure, ISO, dates) |
| `albums/` | 150 folders of 4 images (hard links to gallery images, each with its own path) |
| `small/` | 20 text files and 5 small images: the folder used to time startup |
| `tree/` | 50,000 empty files in 500 folders, 500 of them named `*_7.jpg` |
| `copysrc/` | 20,000 files of 1–4 KB in 200 folders, plus 5 × 50 MB |
| `meta/` | 200 PNGs with Stable Diffusion "parameters" text |
| `flat/` | 10,000 files of 1–2 KB in one folder: the trash test |
| `videos/` | 40 MP4 videos, 5 s at 1280×720, made with `ffmpeg` (skipped if it isn't installed) |
| `pdfs/` | 40 PDFs of 3 pages |
| `windows/` | 5 folders of 100 small files: the several-windows test |

## Files

| File | Purpose |
|---|---|
| `run.sh` | Entry point |
| `bench.py` | Builds what's needed, runs every measurement in the sandbox, prints the tables, updates the READMEs |
| `make_data.py` | Generates the test data |
| `kestrel_py.py` | In-process measurements of the Python version |
| `kestrel_cpp.cpp`, `CMakeLists.txt` | In-process measurements of the C++ version (built from `kes-c/src` into `bench/build/`) |
