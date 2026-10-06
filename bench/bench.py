#!/usr/bin/python3
"""Benchmark Kestrel Explorer (Python and C++) against GNOME Files, or Nemo on Linux Mint. See README.md.

  bench/run.sh                      build, generate the data (first time), run everything, print the tables
  bench/run.sh --runs 5             more runs per measurement (medians are reported)
  bench/run.sh --update-readme      ...and write the results into both projects' README.md
  bench/run.sh --only startup,copy  re-run only some measurements (the rest are kept from the last run)
  bench/run.sh --report             rebuild the tables from the last results without running anything

Every run happens on a headless X server (Xvfb) with a fresh home folder and a private D-Bus session bus on which
only the desktop's settings (dconf) and virtual file system (gvfs) services can start, so caches are empty and no
indexer or other background service runs.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if os.path.exists(os.path.join(ROOT, "kestrel", "app.py")):
    PY_ROOT, CXX_ROOT = ROOT, os.path.join(os.path.dirname(ROOT), "kes-c")
else:
    PY_ROOT, CXX_ROOT = os.path.join(os.path.dirname(ROOT), "kestrel-explorer"), ROOT
PY_ROOT, CXX_ROOT = os.path.realpath(PY_ROOT), os.path.realpath(CXX_ROOT)
DATA = os.environ.get("KESTREL_BENCH_DATA", "/tmp/kestrel-bench-data")
CMAKE = "/usr/bin/cmake" if os.access("/usr/bin/cmake", os.X_OK) else "cmake"
BUILD = os.environ.get("KESTREL_BUILD_DIR", "build")  # build folder name (another machine sharing these folders)
# The file managers Kestrel is compared with; the ones installed are measured (GNOME Files on Ubuntu, Nemo on Linux
# Mint). File operations go through each one's D-Bus file-operations service, as other apps use it: "service" starts
# it, "ops" is (bus name, object path, interface), and "settings" are set in the throwaway home first (gsettings).
# "thumbnails": False skips the FOLDER_TESTS: Nemo makes no thumbnails in the sandbox, so each run would only wait
# out the 2-minute timeout.
OTHERS = {
    "nautilus": {"label": "GNOME Files", "cmd": ["nautilus"], "service": ["nautilus", "--gapplication-service"],
                 "ops": ("org.gnome.Nautilus", "/org/gnome/Nautilus/FileOperations2",
                         "org.gnome.Nautilus.FileOperations2"),
                 "settings": [],
                 "heading": "## Performance: Kestrel vs GNOME Files",
                 "helpers": "separate sandboxed helper processes", "search": " without its file indexer",
                 "whole_folder": True,   # thumbnails the whole folder in the background
                 "thumbnails": True},
    "nemo": {"label": "Nemo", "cmd": ["nemo"], "service": ["nemo", "--no-default-window"],
             "ops": ("org.Nemo", "/org/Nemo", "org.Nemo.FileOperations"),
             "settings": [("org.nemo.preferences", "confirm-trash", "false")],   # Empty Trash would ask first
             "heading": "## Performance: Kestrel vs Nemo",
             "helpers": "separate helper processes", "search": "", "whole_folder": False,
             "thumbnails": False},
}
APPS = ("python", "cxx") + tuple(OTHERS)
# measured the same way for every app
COMMON = ("startup", "open_gallery", "videos", "pdfs", "copy", "move_xdev", "trash", "windows", "idle")
# Kestrel only: its "open folders as tabs" setting, and features the other file managers have no equivalent of
KESTREL_ONLY = ("windows_tabs", "bulk_thumbs", "mosaics", "search", "metadata")
FOLDER_TESTS = ("open_gallery", "videos", "pdfs")   # "open a folder": timed by the thumbnails it makes
FIRST = 12          # "open a folder": time until the first FIRST files (by name) have thumbnails
WINDOWS = 5         # "several windows": folders opened from outside, one after another
IDLE_SECONDS = 30   # "idle": CPU used in this long with a folder open and nothing happening
THUMB_FLAVORS = ("normal", "large", "x-large", "xx-large")


# ---------------------------------------------------------------- measurements (run inside the sandbox)

def home():
    return os.path.expanduser("~")


def app_command(app, folder=None):
    if app in OTHERS:
        cmd = list(OTHERS[app]["cmd"])
    else:
        cmd = {"python": ["/usr/bin/python3", os.path.join(PY_ROOT, "kes")],
               "cxx": [os.path.join(CXX_ROOT, BUILD, "kes")]}[app]
    return cmd + ([folder] if folder else [])


def launch(app, folder):
    return subprocess.Popen(app_command(app, folder), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)


def stop(p):
    try:
        os.killpg(p.pid, signal.SIGTERM)
        p.wait(5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait()


def app_pids(app):
    """Every process of this app in this run (Kestrel: its windows and its tower; the others: their process)."""
    marker = f"HOME={home()}".encode() + b"\0"
    exe = app_command(app)[-1]
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        try:
            with open(f"/proc/{pid}/environ", "rb") as f:
                if marker not in f.read():
                    continue
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                args = f.read().split(b"\0")
        except OSError:
            continue
        if exe.encode() in args[:2] or (app in OTHERS and os.path.basename(args[0]) == exe.encode()):
            out.append(int(pid))
    return out


def peak_mb(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            return next(int(ln.split()[1]) for ln in f if ln.startswith("VmHWM")) / 1024
    except (OSError, StopIteration):
        return None


def private_mb(pids):
    """Private memory: what the processes use beyond the libraries they share with each other and with other apps,
    so what closing them would give back. Unlike proportional memory (PSS), it doesn't depend on which other apps
    happen to be running."""
    total = 0
    for pid in pids:
        try:
            with open(f"/proc/{pid}/smaps_rollup") as f:
                total += sum(int(ln.split()[1]) for ln in f if ln.startswith(("Private_Clean:", "Private_Dirty:")))
        except OSError:
            pass
    return total / 1024


def cpu_ticks(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            fields = f.read().rsplit(")", 1)[1].split()
        return int(fields[11]) + int(fields[12])   # utime + stime
    except (OSError, IndexError):
        return None


def windows_of(pid):
    r = subprocess.run(["xdotool", "search", "--onlyvisible", "--pid", str(pid)], capture_output=True, text=True)
    return len(r.stdout.split())


def wait_for(cond, timeout, what):
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 > timeout:
            raise RuntimeError(what)
        time.sleep(0.01)


def thumb_files():
    base = os.path.join(home(), ".cache", "thumbnails")
    out = set()
    for flavor in THUMB_FLAVORS:
        try:
            out.update(n for n in os.listdir(os.path.join(base, flavor)) if n.endswith(".png"))
        except OSError:
            pass
    return out


def measure_startup(app):
    t0 = time.monotonic()
    p = launch(app, f"{DATA}/small")
    try:
        wait_for(lambda: windows_of(p.pid) or p.poll() is not None, 60, "no window")
        s = time.monotonic() - t0
        time.sleep(1.0)
        return {"s": s, "rss_mb": peak_mb(p.pid)}
    finally:
        stop(p)


def measure_folder(app, folder):
    """Launch on a folder, and time it until the first FIRST files have thumbnails in the shared cache. Then wait
    until no more thumbnails appear, noting how many were made, when the last one appeared, and peak memory."""
    names = sorted(os.listdir(folder))[:FIRST]
    wanted = {hashlib.md5(("file://" + urllib.parse.quote(f"{folder}/{n}")).encode()).hexdigest() + ".png"
              for n in names}
    t0 = time.monotonic()
    p = launch(app, folder)
    try:
        try:
            wait_for(lambda: wanted <= thumb_files() or p.poll() is not None, 120, "thumbnails never appeared")
        except RuntimeError:   # this app made none of them: noted in the table instead of stopping the run
            return {"s": None, "rss_mb": peak_mb(p.pid), "n": len(thumb_files()), "all_s": None, "no_thumbs": True}
        s = time.monotonic() - t0
        count, last = len(thumb_files()), time.monotonic()
        while time.monotonic() - last < 2.0 and time.monotonic() - t0 < 180:
            time.sleep(0.05)
            n = len(thumb_files())
            if n != count:
                count, last = n, time.monotonic()
        return {"s": s, "rss_mb": peak_mb(p.pid), "n": count, "all_s": last - t0}
    finally:
        stop(p)


def tree_size(path):
    files = size = 0
    for root, _dirs, names in os.walk(path):
        files += len(names)
        for n in names:
            size += os.lstat(os.path.join(root, n)).st_size
    return files, size


def count_files(path):
    return sum(len(names) for _root, _dirs, names in os.walk(path))


def listing(path):
    try:
        return os.listdir(path)
    except OSError:
        return []


class Stuck(RuntimeError):
    """A file operation another file manager accepted but didn't finish (for example waiting for a confirmation)."""


class FileOps:
    """Another file manager's file-operations D-Bus service, as other apps use it. Its calls return at once, so each
    operation is timed by watching the files until it has finished (checked every 50 ms). The service's methods and
    their arguments are read from the service itself (Nemo's, a fork of an older GNOME Files, has fewer)."""

    def __init__(self, app):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib, self.app = Gio, GLib, app
        conf = OTHERS[app]
        for schema, key, value in conf["settings"]:
            subprocess.run(["gsettings", "set", schema, key, value], capture_output=True)
        self.ops = conf["ops"]
        self.p = subprocess.Popen(conf["service"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  start_new_session=True)
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        t0 = time.monotonic()
        while True:
            try:
                xml = self.bus.call_sync(self.ops[0], self.ops[1], "org.freedesktop.DBus.Introspectable",
                                         "Introspect", None, None, Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
                iface = Gio.DBusNodeInfo.new_for_xml(xml).lookup_interface(self.ops[2])
                if iface is not None:
                    self.methods = {m.name: [a.signature for a in m.in_args] for m in iface.methods}
                    return
            except GLib.Error:
                pass
            if time.monotonic() - t0 > 30:
                stop(self.p)
                raise RuntimeError(f"{app}: no file-operations service")
            time.sleep(0.05)

    def has(self, method):
        return method in self.methods

    def call(self, method, uris=(), dest=""):
        """The method's arguments in its own order: as = the files, s = the destination folder, a{sv} = no platform
        data, b = no confirmation."""
        values = {"as": list(uris), "s": dest, "a{sv}": {}, "b": False}
        sig = self.methods[method]
        args = tuple(values[t] for t in sig)
        self.bus.call_sync(*self.ops, method, self.GLib.Variant(f"({''.join(sig)})", args) if sig else None, None,
                           self.Gio.DBusCallFlags.NONE, -1, None)

    def wait(self, done, timeout=120):
        t0 = time.monotonic()
        while not done():
            if time.monotonic() - t0 > timeout:
                raise Stuck(f"{self.app}: the operation didn't finish within {timeout} s")
            self.sample()
            time.sleep(0.05)
        self.sample()
        return time.monotonic() - t0

    def sample(self):
        # read while it runs: a service with no window may quit as soon as the operation is done (Nemo does)
        m = peak_mb(self.p.pid)
        if m is not None:
            self.peak_seen = max(self.peak_seen or 0, m)

    peak_seen = None

    def peak(self):
        self.sample()
        return self.peak_seen

    def close(self):
        stop(self.p)


def measure_copy_other(app):
    src = f"{DATA}/copysrc"
    want = tree_size(src)
    dst_parent = os.path.join(home(), "copydst")
    os.makedirs(dst_parent)
    dst = os.path.join(dst_parent, "copysrc")
    n = FileOps(app)
    try:
        if not n.has("CopyURIs"):
            return {"s": None}
        t0 = time.monotonic()
        n.call("CopyURIs", [f"file://{src}"], f"file://{dst_parent}")
        n.wait(lambda: os.path.isdir(dst) and count_files(dst) == want[0] and tree_size(dst) == want)
        return {"s": time.monotonic() - t0, "rss_mb": n.peak()}
    finally:
        n.close()
        shutil.rmtree(dst_parent, ignore_errors=True)


def measure_move_xdev(app):
    """Move 20,000 files + 250 MB from the home folder (/tmp) to another drive (/dev/shm): copy, then delete."""
    src = os.path.join(home(), "movesrc")
    shutil.copytree(f"{DATA}/copysrc", src, symlinks=True)
    want = tree_size(src)
    dst_parent = tempfile.mkdtemp(prefix="kestrel-bench-move.", dir="/dev/shm")
    dst = os.path.join(dst_parent, "movesrc")
    try:
        if app not in OTHERS:
            r = harness(app, "move_xdev", dst)
        else:
            n = FileOps(app)
            try:
                if not n.has("MoveURIs"):   # Nemo's service can only copy
                    return {"s": None}
                t0 = time.monotonic()
                n.call("MoveURIs", [f"file://{src}"], f"file://{dst_parent}")
                n.wait(lambda: not os.path.exists(src) and os.path.isdir(dst) and count_files(dst) == want[0]
                       and tree_size(dst) == want)
                r = {"s": time.monotonic() - t0, "rss_mb": n.peak()}
            finally:
                n.close()
        if tree_size(dst) != want or os.path.exists(src):
            raise RuntimeError(f"{app}: the move didn't finish")
        return r
    finally:
        shutil.rmtree(dst_parent, ignore_errors=True)
        shutil.rmtree(src, ignore_errors=True)


def measure_trash(app):
    """Move 10,000 files (all selected in one folder) to the trash, then empty the trash."""
    victim = os.path.join(home(), "victim")
    shutil.copytree(f"{DATA}/flat", victim)
    total = len(os.listdir(victim))
    trash = os.path.join(home(), ".local", "share", "Trash")
    if app not in OTHERS:
        r = harness(app, "trash")
        if r.get("n") != total or r.get("empty_left"):
            raise RuntimeError(f"{app}: trash {r}")
        return r
    n = FileOps(app)
    try:
        uris = [f"file://{victim}/{name}" for name in sorted(os.listdir(victim))]
        s = None
        if n.has("TrashURIs"):
            t0 = time.monotonic()
            n.call("TrashURIs", uris)
            n.wait(lambda: not listing(victim) and len(listing(f"{trash}/files")) == total
                   and len(listing(f"{trash}/info")) == total)
            s = time.monotonic() - t0
        else:   # Nemo's service can't move to the trash: put them there with gio (not timed), to time emptying it
            subprocess.run(["gio", "trash", "--"] + [f"{victim}/{name}" for name in sorted(os.listdir(victim))],
                           check=True)
        if not n.has("EmptyTrash"):
            return {"s": s, "rss_mb": n.peak()}
        t0 = time.monotonic()
        n.call("EmptyTrash")
        try:
            n.wait(lambda: not listing(f"{trash}/files") and not listing(f"{trash}/info"))
        except Stuck as e:
            return {"s": s, "rss_mb": n.peak(), "stuck_empty": str(e)}
        return {"s": s, "empty_s": time.monotonic() - t0, "rss_mb": n.peak()}
    finally:
        n.close()


def measure_windows(app, tabs=False):
    """Open WINDOWS folders from outside, one after another (as from a browser's "Show in folder" or a terminal),
    then measure the memory of everything the app is running for them."""
    if tabs:
        os.makedirs(os.path.join(home(), ".config", "kestrel-explorer"), exist_ok=True)
        with open(os.path.join(home(), ".config", "kestrel-explorer", "kestrel-explorer.conf"), "w") as f:
            f.write("[General]\nopen_in_tabs=true\n")
    folders = [f"{DATA}/windows/folder{k}" for k in range(WINDOWS)]
    procs = []
    try:
        for i, folder in enumerate(folders):
            p = launch(app, folder)
            procs.append(p)
            # each launch either shows its own window, or hands the folder over and exits
            wait_for(lambda: windows_of(p.pid) or p.poll() is not None, 60, "no window")
            if i == 0:
                time.sleep(1.0)   # the first one also starts the tower (Kestrel) or the other app's service
        first = procs[0].pid
        if app in OTHERS:   # one process: the later launches hand their folder to it
            wait_for(lambda: windows_of(first) >= WINDOWS, 30, "not all windows opened")
        elif not tabs:
            wait_for(lambda: all(windows_of(p.pid) for p in procs), 30, "not all windows opened")
        time.sleep(2.0)
        pids = app_pids(app)
        return {"s": 0, "mem_mb": private_mb(pids), "procs": len(pids)}
    finally:
        for p in procs:
            if p.poll() is None:
                stop(p)


def measure_idle(app):
    """CPU used in IDLE_SECONDS with a folder open and nothing happening (after 5 s to settle)."""
    p = launch(app, f"{DATA}/small")
    try:
        wait_for(lambda: windows_of(p.pid) or p.poll() is not None, 60, "no window")
        time.sleep(5.0)
        before = {pid: cpu_ticks(pid) for pid in app_pids(app)}
        time.sleep(IDLE_SECONDS)
        used = 0
        for pid in app_pids(app):
            now = cpu_ticks(pid)
            if now is not None:
                used += now - (before.get(pid) or 0)
        return {"s": 0, "cpu_ms": used * 1000 / os.sysconf("SC_CLK_TCK")}
    finally:
        stop(p)


def harness(app, test, arg=None):
    """A measurement inside the app (kestrel_py.py / kestrel_cpp)."""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    if app == "python":
        cmd = ["/usr/bin/python3", os.path.join(HERE, "kestrel_py.py"), PY_ROOT, test, DATA]
    else:
        cmd = [os.path.join(HERE, BUILD, "kestrel_cpp"), test, DATA]
    out = subprocess.run(cmd + ([arg] if arg else []), env=env, capture_output=True, text=True, timeout=900)
    line = next((ln for ln in out.stdout.splitlines() if ln.startswith("{")), None)
    if line is None:
        raise RuntimeError(f"{app} {test} failed:\n{out.stderr[-2000:]}")
    return json.loads(line)


def start_desktop_services():
    """On a desktop, gvfs is already running when an app starts. Start it here (the daemon, the volume monitors and
    the metadata store), so no app pays for starting it."""
    from gi.repository import Gio
    Gio.File.new_for_uri("trash:///").query_info("standard::*", Gio.FileQueryInfoFlags.NONE, None)
    Gio.VolumeMonitor.get().get_mounts()
    subprocess.run(["gio", "info", "-a", "metadata::*", home()], capture_output=True)
    time.sleep(0.5)


def measure(app, test):
    start_desktop_services()
    if test == "startup":
        return measure_startup(app)
    if test in FOLDER_TESTS:
        return measure_folder(app, f"{DATA}/{'gallery' if test == 'open_gallery' else test}")
    try:
        if test == "copy" and app in OTHERS:
            return measure_copy_other(app)
        if test == "move_xdev":
            return measure_move_xdev(app)
        if test == "trash":
            return measure_trash(app)
    except Stuck as e:   # noted in the table instead of stopping the run
        return {"s": None, "stuck": str(e)}
    if test in ("windows", "windows_tabs"):
        return measure_windows(app, tabs=test == "windows_tabs")
    if test == "idle":
        return measure_idle(app)
    return harness(app, test)


# ---------------------------------------------------------------- the sandbox

class Sandbox:
    """A headless X server, plus a fresh home folder and private session bus for each run."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="kestrel-bench.")
        services = os.path.join(self.dir, "services")
        os.makedirs(services)
        # settings (dconf) and the virtual file system (gvfs: trash:///, volumes, metadata) work as on a desktop;
        # nothing else can be started (no file indexer, portals, online accounts…)
        system = "/usr/share/dbus-1/services"
        for name in os.listdir(system):
            if name == "ca.desrt.dconf.service" or name.startswith("org.gtk.vfs."):
                shutil.copy(os.path.join(system, name), services)
        with open("/usr/share/dbus-1/session.conf") as f:
            conf = re.sub(r"<standard_session_servicedirs\s*/>", f"<servicedir>{services}</servicedir>", f.read())
        self.conf = os.path.join(self.dir, "session.conf")
        with open(self.conf, "w") as f:
            f.write(conf)
        self.display = next(f":{n}" for n in range(90, 200) if not os.path.exists(f"/tmp/.X11-unix/X{n}"))
        self.xvfb = subprocess.Popen(["Xvfb", self.display, "-screen", "0", "1920x1080x24", "-nolisten", "tcp"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sock = f"/tmp/.X11-unix/X{self.display[1:]}"
        for _ in range(100):
            if os.path.exists(sock):
                break
            time.sleep(0.05)

    def run(self, app, test):
        home_dir = tempfile.mkdtemp(prefix="home.", dir=self.dir)
        env = {k: v for k, v in os.environ.items()
               if k not in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "DBUS_SESSION_BUS_ADDRESS",
                            "WAYLAND_DISPLAY", "XDG_ACTIVATION_TOKEN", "DESKTOP_STARTUP_ID", "CONDA_DEFAULT_ENV")}
        env.update(HOME=home_dir, DISPLAY=self.display, XDG_SESSION_TYPE="x11", QT_QPA_PLATFORM="xcb",
                   GDK_BACKEND="x11", GSK_RENDERER="cairo", GTK_A11Y="none", NO_AT_BRIDGE="1",
                   KESTREL_BENCH_DATA=DATA)
        try:
            out = subprocess.run(["dbus-run-session", f"--config-file={self.conf}", "--", "/usr/bin/python3",
                                  os.path.abspath(__file__), "--measure", app, test], env=env, capture_output=True,
                                 text=True, timeout=1200)
        finally:
            shutil.rmtree(home_dir, ignore_errors=True)
        line = next((ln for ln in out.stdout.splitlines() if ln.startswith("{")), None)
        if line is None:
            raise RuntimeError(f"{app} {test}: {out.stdout[-1500:]}{out.stderr[-1500:]}")
        return json.loads(line)

    def close(self):
        self.xvfb.terminate()
        self.xvfb.wait()
        shutil.rmtree(self.dir, ignore_errors=True)


# ---------------------------------------------------------------- results

def secs(v):
    if v is None:
        return "—"
    return f"{v:.3f} s" if v < 0.1 else f"{v:.2f} s"


def ratio(a, b):
    """How many times faster b is than a, in words."""
    if not a or not b:
        return "—"
    r = a / b
    if r >= 1.05:
        return f"{r:.1f}× faster" if r < 10 else f"about {round(r)}× faster"
    if r <= 0.95:
        return f"{1 / r:.1f}× slower"
    return "about the same"


def mb(v):
    return "—" if v is None else f"{round(v)}"


# (label, test, key) for the rows timed in every app
TIMED = [("Startup (launch to window shown)", "startup", "s"),
         (f"Open a 600-image folder (launch to the first {FIRST} thumbnails)", "open_gallery", "s"),
         (f"Open a folder of 40 videos (launch to the first {FIRST} thumbnails)", "videos", "s"),
         (f"Open a folder of 40 PDFs (launch to the first {FIRST} thumbnails)", "pdfs", "s"),
         ("Copy 20,000 small files + 5 × 50 MB", "copy", "s"),
         ("Move the same to another drive", "move_xdev", "s"),
         ("Move 10,000 files to the trash (all selected in one folder)", "trash", "s"),
         ("Empty the trash (those 10,000 files)", "trash", "empty_s")]


def tables(res, other):
    """The tables comparing Kestrel with `other` (a key of OTHERS), the summary for the README text, and notes on what
    `other` couldn't be timed on."""
    apps = ("python", "cxx", other)
    if not OTHERS[other]["thumbnails"]:   # also drops results saved before its folder tests were skipped
        res = {k: v for k, v in res.items() if not (k[0] == other and k[1] in FOLDER_TESTS)}

    def med(app, test, key="s"):
        vals = [r[key] for r in res.get((app, test), []) if r.get(key) is not None]
        return statistics.median(vals) if vals else None

    t1 = [f"| Test | Kestrel (Python) | Kestrel (C++) | {OTHERS[other]['label']} |", "|---|---|---|---|"]
    for label, test, key in TIMED:
        t1.append(f"| {label} | {' | '.join(secs(med(a, test, key)) for a in apps)} |")

    def mem(app):
        a, b = med(app, "startup", "rss_mb"), med(app, "open_gallery", "rss_mb")
        return "—" if a is None and b is None else f"{mb(a)} / {mb(b)} MB"

    def windows(app):
        sep, tabs = med(app, "windows", "mem_mb"), med(app, "windows_tabs", "mem_mb")
        if sep is None:
            return "—"
        return f"{mb(sep)} MB" + (f" ({mb(tabs)} MB as tabs)" if tabs is not None else "")

    def idle(app):
        v = med(app, "idle", "cpu_ms")
        return "—" if v is None else f"{round(v)} ms ({v / (IDLE_SECONDS * 10):.2f}% of a core)"

    t1.append(f"| Peak memory (startup / 600-image folder open) | {' | '.join(mem(a) for a in apps)} |")
    t1.append(f"| Memory with {WINDOWS} folders opened from other apps | {' | '.join(windows(a) for a in apps)} |")
    t1.append(f"| CPU time used in {IDLE_SECONDS} s with a folder open, idle | {' | '.join(idle(a) for a in apps)} |")

    def row2(label, test):
        a, b = med("python", test), med("cxx", test)
        return f"| {label} | {secs(a)} | {secs(b)} | {ratio(a, b)} |"

    def bg(app):
        vals = [med(app, t, "rss_mb") for t in ("bulk_thumbs", "mosaics", "search", "metadata", "copy")]
        vals = [v for v in vals if v is not None]
        return f"{round(min(vals))}–{round(max(vals))} MB" if vals else "—"

    t2 = ["| Test | Python | C++ | C++ speed-up |", "|---|---|---|---|",
          row2("Thumbnail all 600 images (\"Generate Previews\")", "bulk_thumbs"),
          row2("Build 150 folder mosaics", "mosaics"),
          row2("Recursive search over 50,000 files", "search"),
          row2("Read EXIF / AI metadata for 400 images", "metadata"),
          f"| Peak memory (background jobs) | {bg('python')} | {bg('cxx')} | |"]

    vs = []
    for label, test, key in TIMED:
        short = {"startup": "startup", "open_gallery": "first thumbnails in a 600-image folder",
                 "videos": "first video thumbnails", "pdfs": "first PDF thumbnails", "copy": "copying",
                 "move_xdev": "moving to another drive"}.get(test, "moving to the trash" if key == "s" else
                                                              "emptying the trash")
        if med(other, test, key) is not None:
            vs.append(f"- {short}: {ratio(med(other, test, key), med('cxx', test, key))};")
    if vs:
        vs[-1] = vs[-1][:-1] + "."
    gallery_n, kes_n = med(other, "open_gallery", "n"), med("cxx", "open_gallery", "n")
    ops = {"copy": "copy files", "move_xdev": "move files to another drive", "trash": "move files to the trash"}
    runs_of = lambda test: res.get((other, test), [])   # noqa: E731
    stuck = [phrase for test, phrase in ops.items() if runs_of(test) and all(r.get("stuck") for r in runs_of(test))]
    if runs_of("trash") and all(r.get("stuck_empty") for r in runs_of("trash")):
        stuck.append("empty the trash")
    untimed = [phrase for test, phrase in ops.items()
               if runs_of(test) and med(other, test) is None and phrase not in stuck]
    kinds = {"open_gallery": "images", "videos": "videos", "pdfs": "PDFs"}
    no_thumbs = [kinds[t] for t in kinds if res.get((other, t)) and all(r.get("no_thumbs") for r in res[(other, t)])]
    summary = {"vs": "\n".join(vs), "no_thumbs": no_thumbs,
               "files_n": round(gallery_n) if gallery_n else None,
               "files_all": secs(med(other, "open_gallery", "all_s")),
               "kestrel_n": round(kes_n) if kes_n else None,
               "kestrel_all": secs(med("cxx", "bulk_thumbs")),
               "procs": round(med("cxx", "windows", "procs") or 0),
               "untimed": untimed, "stuck": stuck}
    # idle CPU far above Kestrel's: worth a word of caution (Nemo kept a fifth of a core busy in the Mint VM)
    busy, calm = med(other, "idle", "cpu_ms"), med("cxx", "idle", "cpu_ms")
    summary["busy_idle"] = busy / (IDLE_SECONDS * 10) if busy and busy > 1000 and busy > 10 * (calm or 0) else None
    return t1, t2, summary


def machine(other):
    cpu = "unknown CPU"
    with open("/proc/cpuinfo") as f:
        for ln in f:
            if ln.startswith("model name"):
                cpu = ln.split(":", 1)[1].strip()
                break
    try:
        version = subprocess.run(OTHERS[other]["cmd"] + ["--version"], capture_output=True, text=True).stdout.strip()
    except OSError:   # a report made on another machine
        version = ""
    distro = ""
    try:
        with open("/etc/os-release") as f:
            distro = next((ln.split("=", 1)[1].strip().strip('"') for ln in f if ln.startswith("PRETTY_NAME=")), "")
    except OSError:
        pass
    version = version.replace("GNOME nautilus", "GNOME Files").replace("nemo", "Nemo")
    return cpu, os.cpu_count(), version or OTHERS[other]["label"], distro


def section(t1, t2, summary, runs, py_link, cxx_link, bench_link, other):
    cpu, threads, version, distro = machine(other)
    label = OTHERS[other]["label"]
    owns = label + ("'" if label.endswith("s") else "'s")   # GNOME Files', Nemo's
    if not OTHERS[other]["thumbnails"]:
        thumbs = f"{label} wasn't timed on this (see below)."
    elif "images" in summary["no_thumbs"]:
        thumbs = f"{label} made no thumbnails for these images in this setup."
    elif OTHERS[other]["whole_folder"]:
        thumbs = (f"{label} makes thumbnails for the whole folder in the background. It finished all "
                  f"{summary['files_n']} images {summary['files_all']} after launch.")
    else:
        thumbs = f"{label} made {summary['files_n']} thumbnails in all, the last {summary['files_all']} after launch."
    untimed = ""
    if not OTHERS[other]["thumbnails"]:
        untimed += (f"\n- **Opening a folder not timed:** {label} makes no thumbnails in this setup (a fresh home folder "
                    f"with its default settings), so the folder tests are skipped for it and those rows show —, as does "
                    f"its peak memory with the 600-image folder open.")
    if summary["no_thumbs"]:
        untimed += (f"\n- **No thumbnails:** {label} made none for the {' or '.join(summary['no_thumbs'])} within 2 minutes "
                    f"in this setup (a fresh home folder with its default settings), so those rows show —.")
    if summary["untimed"]:
        untimed += (f"\n- **Not timed:** {owns} D-Bus file-operations service has no way to "
                   f"{' or '.join(summary['untimed'])}, so those rows show —.")
        if "move files to the trash" in summary["untimed"]:
            untimed += " For \"Empty the trash\", the files were put in the trash with `gio trash` first."
    if summary["busy_idle"]:
        headless = " (where it also made no thumbnails)" if not OTHERS[other]["thumbnails"] or summary["no_thumbs"] else ""
        untimed += (f"\n- **CPU while idle:** {label} kept about {summary['busy_idle']:.0f}% of a core busy with nothing "
                    f"happening. That's unusual for a file manager at rest, so it probably comes from this headless "
                    f"setup{headless} rather than from everyday use.")
    if summary["stuck"]:
        untimed += (f"\n- **Didn't finish:** asked through its D-Bus service to {' or '.join(summary['stuck'])}, "
                    f"{label} didn't finish within 2 minutes (it may have been waiting for a confirmation), so those "
                    f"rows show —.")
    return f"""{OTHERS[other]["heading"]}

Kestrel Explorer exists in two versions with the same features: the original [Python/PyQt6 version]({py_link}) and the [C++/Qt 6 port]({cxx_link}). They share settings, bookmarks and caches, so you can switch between them. Both are compared here with {version}, the file manager they replace.

**Test machine:** {cpu} ({threads} threads), {distro}. The test data is on a RAM disk: 600 JPEGs at 1600×1200 with camera EXIF, 40 videos, 40 PDFs, 150 folders of 4 images, a tree of 50,000 files, 20,000 small files plus 250 MB, a folder of 10,000 files, and 200 PNGs with AI-generation metadata.

**How it was measured:** each test ran {runs} times, and the tables show medians. Every run started with a fresh home folder, so the thumbnail cache was empty. All three apps ran on a headless X server with software rendering (Qt's raster engine, GTK's cairo renderer), on a private session bus where only the desktop's settings and virtual file system (gvfs) services could start, so no file indexer ran. The benchmark is in [bench/]({bench_link}) and is run with `bench/run.sh`.

### Compared with {label}

All three apps are measured in the same way:
- **Startup:** timed until the window is on screen.
- **Opening a folder:** timed from launch until the first {FIRST} files' thumbnails are in the shared thumbnail cache.
- **File operations:** Kestrel runs them with its own copy and trash code, the same code its menus use. {label} receives them through its D-Bus file-operations service, as when another app asks it to.
- **"Peak memory":** the app's highest memory use.
- **Several folders opened from other apps:** the {WINDOWS} folders are opened one after another, as from a browser's "Show in folder". The figure is the private memory of everything the app then runs: what closing it would give back, not counting the libraries it shares with other apps. Kestrel normally starts a new process for each window ({summary['procs']} processes here, counting the tower that keeps them in sync); with "open folders as tabs" on, they become tabs in one window.
- **CPU while idle:** the CPU time all the app's processes use in {IDLE_SECONDS} s with a folder open and nothing happening. It's counted in 10 ms steps.

{chr(10).join(t1)}

Kestrel (C++) compared with {label}:
{summary['vs']}

**Limits of this comparison:**
- **Opening a folder:** the two apps don't do the same amount of work.
  - {thumbs}
  - Kestrel makes them only for what's on screen ({summary['kestrel_n']} images here), and the rest as you scroll.
  - To thumbnail a whole folder at once, Kestrel has "Generate Previews": {summary['kestrel_all']} for these 600 images in the C++ version (table below).
- **File operations:** {owns} D-Bus service returns straight away, so its times were measured by watching the files until the operation had finished, to within about 50 ms.{untimed}
- **Memory:** {label} makes thumbnails in {OTHERS[other]['helpers']}, whose memory isn't counted in its figures. Kestrel makes them inside the app.
- **Search:** {owns} search can't be timed from outside{OTHERS[other]['search']}, so it isn't compared.

### Kestrel's own features (Python vs C++)

These are measured inside the app, because {label} has no equivalent ("Generate Previews", folder mosaics, metadata panels) or can't be timed from outside (search).

{chr(10).join(t2)}

Thumbnails and mosaics take about as long in both versions, because both decode images with the same Qt C++ code, which the Python version already runs on several threads. The C++ version is much faster where the Python version does the work in Python itself, such as reading metadata, searching and copying, and it uses about half the memory.
"""


def update_readmes(t1, t2, summary, runs, other):
    """Write the section for `other` into both READMEs: in place of its old one, or after the other Performance
    sections (a run on Linux Mint adds "vs Nemo" and leaves "vs GNOME Files" as it was)."""
    heading = OTHERS[other]["heading"]
    # the Python README is also PyPI's project page, where only absolute links work
    gh = "https://github.com/RegulusArms"
    for root, py_link, cxx_link, bench_link in (
            (CXX_ROOT, "../kestrel-explorer", ".", "bench"),
            (PY_ROOT, f"{gh}/kestrel-explorer", f"{gh}/kes-c", f"{gh}/kestrel-explorer/tree/main/bench")):
        path = os.path.join(root, "README.md")
        with open(path) as f:
            s = f.read()
        text = section(t1, t2, summary, runs, py_link, cxx_link, bench_link, other) + "\n"
        if heading + "\n" in s:
            start = s.index(heading + "\n")
            end = s.index("\n## ", start + 1) + 1
        else:
            last = max(s.rfind("\n" + o["heading"] + "\n") for o in OTHERS.values())
            start = end = s.index("\n## ", last + 1) + 1 if last >= 0 else len(s)
        s = s[:start] + text + s[end:]
        with open(path, "w") as f:
            f.write(s)
        print(f"Updated {path}")


# ---------------------------------------------------------------- main

def build():
    print("Building the C++ version and its benchmark harness…", flush=True)
    log = open(os.path.join(HERE, "build.log"), "w")
    for cmd in ([CMAKE, "-S", CXX_ROOT, "-B", os.path.join(CXX_ROOT, BUILD), "-DCMAKE_BUILD_TYPE=Release"],
                [CMAKE, "--build", os.path.join(CXX_ROOT, BUILD), f"-j{os.cpu_count()}"],
                [CMAKE, "-S", HERE, "-B", os.path.join(HERE, BUILD), f"-DKES_SRC={CXX_ROOT}/src"],
                [CMAKE, "--build", os.path.join(HERE, BUILD), f"-j{os.cpu_count()}"]):
        if subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT).returncode:
            sys.exit(f"Build failed (log: {os.path.join(HERE, 'build.log')})")


def runs_for(test, runs):
    if test == "startup":
        return runs + 2
    if test == "idle":
        return min(runs, 2)   # each takes IDLE_SECONDS
    return runs


def report(res, args):
    runs = max((len(v) for (a, t), v in res.items() if t not in ("startup", "idle")), default=args.runs)
    others = [o for o in OTHERS if any(a == o for a, _t in res)]
    t2 = None
    for other in others or [next(iter(OTHERS))]:
        t1, t2, summary = tables(res, other)
        print()
        print("\n".join(t1))
        if args.update_readme and others:
            update_readmes(t1, t2, summary, runs, other)
    print()
    print("\n".join(t2))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--update-readme", action="store_true")
    ap.add_argument("--report", action="store_true", help="rebuild the tables from results.json without running")
    ap.add_argument("--measure", nargs=2, metavar=("APP", "TEST"), help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.measure:
        print(json.dumps(measure(*args.measure)), flush=True)
        os._exit(0)

    results_file = os.path.join(HERE, "results.json")
    saved = {}
    if os.path.exists(results_file):
        with open(results_file) as f:
            saved = {tuple(k.split(" ", 1)): v for k, v in json.load(f).items()}
    if args.report:
        if not saved:
            sys.exit("No results.json yet: run the benchmark first")
        report(saved, args)
        return

    missing = [t for t in ("Xvfb", "xdotool", "dbus-run-session") if not shutil.which(t)]
    if missing:
        sys.exit(f"Missing: {', '.join(missing)} (sudo apt install xvfb xdotool dbus-daemon)")
    for need in (os.path.join(PY_ROOT, "kes"), os.path.join(CXX_ROOT, "CMakeLists.txt")):
        if not os.path.exists(need):
            sys.exit(f"Both projects are needed side by side; not found: {need}")
    apps = [a for a in APPS if a not in OTHERS or shutil.which(OTHERS[a]["cmd"][0])]
    only = [t for t in args.only.split(",") if t]
    unknown = [t for t in only if t not in COMMON + KESTREL_ONLY]
    if unknown:
        sys.exit(f"Unknown test(s): {', '.join(unknown)} (tests: {', '.join(COMMON + KESTREL_ONLY)})")
    tests = [t for t in COMMON + KESTREL_ONLY if not only or t in only]
    build()
    subprocess.run(["/usr/bin/python3", os.path.join(HERE, "make_data.py"), DATA], check=True)

    box = Sandbox()
    res = dict(saved) if only else {}   # --only: keep the other measurements from the last run
    try:
        for test in tests:
            for app in apps:
                if app in OTHERS and test in KESTREL_ONLY:
                    continue
                if app in OTHERS and test in FOLDER_TESTS and not OTHERS[app]["thumbnails"]:
                    res.pop((app, test), None)   # --only keeps saved results: drop any from before
                    continue
                if test == "videos" and not os.path.isdir(f"{DATA}/videos"):
                    continue
                n = runs_for(test, args.runs)
                res[(app, test)] = []
                for i in range(n):
                    r = box.run(app, test)
                    res[(app, test)].append(r)
                    detail = "".join(f", {k} {r[k]:.0f}" for k in ("rss_mb", "mem_mb", "cpu_ms") if r.get(k))
                    if r.get("empty_s") is not None:
                        detail += f", empty {r['empty_s']:.3f} s"
                    elif r.get("stuck_empty"):
                        detail += ", emptying didn't finish"
                    took = (f"{r['s']:.3f} s" if r["s"] is not None else "didn't finish" if r.get("stuck")
                            else "no thumbnails" if r.get("no_thumbs") else "not offered")
                    print(f"  {test:13} {app:9} run {i + 1}/{n}: {took}{detail}", flush=True)
                with open(results_file, "w") as f:   # saved as it goes, so an interrupted run isn't lost
                    json.dump({f"{a} {t}": v for (a, t), v in res.items()}, f, indent=1)
    finally:
        box.close()
    report(res, args)


if __name__ == "__main__":
    main()
