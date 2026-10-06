"""File operations: copy, move, merge, replace, delete, cancel, trash, links, unique names, and undoing them."""
import os
import stat
import subprocess
import threading
import time

from common import A, check, finish, home_path as P, setup_app, skip, wait_for
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

from kestrel import archive, fileops, places, undo, util
from kestrel.archive_ui import ExtractDialog

boxes = []   # texts of message boxes that popped up (closed automatically)


def make(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def text_of(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def run_ops(w, jobs, undo_label=None, task_out=None):
    """Run start_ops and wait until it has finished; False if it didn't within the time."""
    state = {"done": False, "gone": False}
    t = fileops.start_ops(w, jobs, "Test", lambda: state.__setitem__("done", True), undo_label)
    t.finished.connect(lambda: state.__setitem__("gone", True))
    if task_out is not None:
        task_out.append(t)
    return wait_for(lambda: state["done"] or state["gone"], 20000) and wait_for(lambda: state["gone"], 5000)


def undo_and_wait(w, result):
    undo.undo(w)
    return wait_for(result, 8000)


app = setup_app()
w = A.open_window([os.path.expanduser("~")])


def close_boxes():   # error boxes are modal: note their text and close them
    m = QApplication.activeModalWidget()
    if isinstance(m, QMessageBox):
        boxes.append(m.text())
        m.done(0)


closer = QTimer(timeout=close_boxes, interval=100)
closer.start()

# ---- copy
os.makedirs(P("dst"), exist_ok=True)
make(P("src/a.txt"), b"alpha")
os.chmod(P("src/a.txt"), 0o640)
os.utime(P("src/a.txt"), (1000000000, 1000000000))
check(run_ops(w, [("copy", P("src/a.txt"), P("dst/a.txt"))]) and not boxes, "a copy finishes without errors")
st = os.stat(P("dst/a.txt"))
check(text_of(P("dst/a.txt")) == "alpha" and os.path.exists(P("src/a.txt")), "copy: same contents, original kept")
check(stat.S_IMODE(st.st_mode) == 0o640 and int(st.st_mtime) == 1000000000, "copy: permissions and modification time kept")

make(P("tree/sub/deep/f.bin"), b"z" * 300000)
make(P("tree/sub/g.txt"), b"g")
os.makedirs(P("tree/empty"))
os.symlink("sub/g.txt", P("tree/rel-link"))
os.symlink("/nonexistent/target", P("tree/dangling"))
make(P("tree/100% %1 файл.txt"), "odd name".encode())
check(run_ops(w, [("copy", P("tree"), P("tree2"))]), "a folder copy finishes")
check(os.path.getsize(P("tree2/sub/deep/f.bin")) == 300000 and text_of(P("tree2/sub/g.txt")) == "g",
      "folder copy: nested files")
check(os.path.isdir(P("tree2/empty")), "folder copy: empty folders")
check(os.path.islink(P("tree2/rel-link")) and os.readlink(P("tree2/rel-link")) == "sub/g.txt",
      "folder copy: relative links stay links to the same target")
check(os.path.islink(P("tree2/dangling")) and os.readlink(P("tree2/dangling")) == "/nonexistent/target",
      "folder copy: broken links are copied as they are")
check(text_of(P("tree2/100% %1 файл.txt")) == "odd name", "folder copy: names with %, %1 and non-ASCII letters")

# ---- move
make(P("m/one.txt"), b"1")
before = os.stat(P("m/one.txt"))
check(run_ops(w, [("move", P("m/one.txt"), P("dst/one.txt"))]), "a move finishes")
check(not os.path.lexists(P("m/one.txt")) and text_of(P("dst/one.txt")) == "1", "move: the file is at the new place only")
check(os.stat(P("dst/one.txt")).st_ino == before.st_ino, "move on the same drive renames (no copy)")

# ---- merge and replace
make(P("A/only-a.txt"), b"a")
make(P("A/both.txt"), b"from A")
make(P("B/only-b.txt"), b"b")
make(P("B/both.txt"), b"from B")
check(run_ops(w, [("merge_copy", P("A"), P("B"))]), "a merge finishes")
check(text_of(P("B/only-a.txt")) == "a" and text_of(P("B/only-b.txt")) == "b", "merge: files from both folders are there")
check(text_of(P("B/both.txt")) == "from A", "merge: a file in both is replaced by the copied one")
make(P("C/only-c.txt"), b"c")
check(run_ops(w, [("copy", P("A"), P("C"))]), "a replace finishes")
check(not os.path.lexists(P("C/only-c.txt")) and os.path.exists(P("C/only-a.txt")),
      "replace: the old folder's contents are gone")

# ---- delete
make(P("ro/inner/f.txt"))
os.chmod(P("ro/inner"), 0o500)
check(run_ops(w, [("delete", P("ro"), None)]), "a delete finishes")
check(not os.path.lexists(P("ro")), "delete: removes read-only folders you own")
os.symlink(P("tree"), P("link-to-tree"))
check(run_ops(w, [("delete", P("link-to-tree"), None)]) and not os.path.lexists(P("link-to-tree"))
      and os.path.exists(P("tree/sub/g.txt")), "delete: a link to a folder removes the link, not the folder")

# ---- cancel
BIG = 6 * 1024 * 1024   # more than one 4 MB chunk, so a file can be cancelled half-way
for i in range(24):
    make(P(f"big/f{i:02d}.bin"), bytes([97 + i]) * BIG)
state = {"gone": False}
t = fileops.start_ops(w, [("copy", P("big"), P("big2"))], "Test")
t.finished.connect(lambda: state.__setitem__("gone", True))
# at once: on a fast disk (the test's home is in /tmp, often in memory) the whole copy can finish before the first
# progress report arrives
t.cancel()
check(wait_for(lambda: state["gone"], 10000), "a cancelled copy stops")
copied = os.listdir(P("big2")) if os.path.isdir(P("big2")) else []
check(len(copied) < 24, f"cancel: stops part-way ({len(copied)} of 24 copied)")
check(all(os.path.getsize(os.path.join(P("big2"), f)) == BIG for f in copied), "cancel: no half-copied file is left behind")

# ---- trash
make(P("t/gone.txt"), b"trash me")
util.trash(P("t/gone.txt"))
trashed = str(util.TRASH_DIR / "files" / "gone.txt")
check(not os.path.lexists(P("t/gone.txt")) and os.path.exists(trashed), "trash: the file moves to the trash")
check(util.trash_original_path(trashed) == P("t/gone.txt"), "trash: its original place is recorded")
undo.record("trash", "Move to Trash", [P("t/gone.txt")])
check(undo_and_wait(w, lambda: os.path.exists(P("t/gone.txt"))) and not os.path.exists(trashed),
      "undo Move to Trash: puts it back")

# ---- undo
make(P("u/file.txt"), b"u")
check(run_ops(w, [("move", P("u/file.txt"), P("dst/file.txt"))], "Move"), "a move with undo finishes")
check(undo.label() == "Move", "the Edit menu offers \"Undo Move\"")
check(undo_and_wait(w, lambda: os.path.exists(P("u/file.txt")) and not os.path.lexists(P("dst/file.txt"))), "undo Move")
check(run_ops(w, [("copy", P("u/file.txt"), P("u/file (copy).txt"))], "Copy"), "a copy with undo finishes")
check(undo_and_wait(w, lambda: not os.path.lexists(P("u/file (copy).txt"))), "undo Copy: removes the copy")
check(os.path.exists(util.TRASH_DIR / "files" / "file (copy).txt"), "...by moving it to the trash, not deleting it")
check(os.path.exists(P("u/file.txt")), "...and leaves the original")
os.rename(P("u/file.txt"), P("u/renamed.txt"))
undo.record("rename", "Rename", [(P("u/file.txt"), P("u/renamed.txt"))])
check(undo_and_wait(w, lambda: os.path.exists(P("u/file.txt")) and not os.path.lexists(P("u/renamed.txt"))), "undo Rename")
undo.record("move", "Move", [(P("u/gone-elsewhere.txt"), P("u/missing.txt"))])
boxes.clear()
undo.undo(w)
check(wait_for(lambda: bool(boxes)) and "no longer at" in boxes[-1], "undo explains what it can't put back")

# ---- unique names and links
make(P("n/a.txt"))
check(util.unique_path(P("n"), "a.txt") == P("n/a (copy).txt"), "a copy's name: \"a (copy).txt\"")
make(P("n/a (copy).txt"))
check(util.unique_path(P("n"), "a.txt") == P("n/a (copy 2).txt"), "the next one: \"a (copy 2).txt\"")
check(util.unique_path(P("n"), "a.txt", "num") == P("n/a (2).txt"), "keep both: \"a (2).txt\"")
sym = fileops.make_link(fileops.link_plan("sym", P("n/a.txt"), P("dst")))
check(os.path.islink(sym) and os.readlink(sym) == P("n/a.txt"), "symbolic link (absolute)")
os.makedirs(P("dst/sub"), exist_ok=True)
rel = fileops.make_link(fileops.link_plan("rel", P("n/a.txt"), P("dst/sub")))
check(os.path.islink(rel) and not os.readlink(rel).startswith("/") and os.path.realpath(rel) == P("n/a.txt"),
      "relative link")
hard = fileops.make_link(fileops.link_plan("hard", P("n/a.txt"), P("dst")))
check(os.stat(hard).st_ino == os.stat(P("n/a.txt")).st_ino, "hard link")
same = fileops.make_link(fileops.link_plan("sym", P("n/a.txt"), P("n")))
check(os.path.basename(same).startswith("Link to a"), "a link in the same folder is named \"Link to …\"")
check(A.location_arg("trash:///") == str(util.TRASH_DIR / "files") and A.location_arg("recent:///") == places.RECENT,
      "trash:/// and recent:/// from other apps open the right places")
check(len(boxes) == 1, f"no error boxes besides that one ({' | '.join(boxes)})")

_size, files, dirs = fileops.dir_stats(P("tree"))
check(files == 5 and dirs == 3, f"folder size counts files and folders ({files} files, {dirs} folders)")

# ---- opening an archive
check(archive.opens_as_archive(P("photos.tar.gz")) and not archive.opens_as_archive(P("app.deb"))
      and not archive.opens_as_archive(P("disk.iso")) and not archive.opens_as_archive(P("game.apk")),
      "a .tar.gz opens as an archive; .deb, .iso and .apk open with their own apps")
make(P("arc/in.txt"))
subprocess.run(["tar", "czf", P("arc.tar.gz"), "-C", P("arc"), "in.txt"])
extract_shown = []


def close_extract():
    """The Extract dialog is modal: note it and close it."""
    d = QApplication.activeModalWidget()
    if isinstance(d, ExtractDialog):
        extract_shown.append(True)
        d.reject()


extract_closer = QTimer(interval=50, timeout=close_extract)
extract_closer.start()
w.open_paths(w.pane(), [P("arc.tar.gz")])
check(wait_for(lambda: bool(extract_shown)), "double-clicking an archive opens Kestrel's Extract dialog")
extract_closer.stop()

# -- running programs: a timeout holds even without pipes, or once the program has closed its output
start = time.monotonic()
try:
    subprocess.run(["sh", "-c", 'echo $$ > "$0"; exec sleep 5', P("sleeper.pid")], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=0.3)
    timed_out = False
except subprocess.TimeoutExpired:
    timed_out = True
with open(P("sleeper.pid")) as f:
    pid = int(f.read().strip() or 0)
try:
    os.kill(pid, 0)
    gone = False
except ProcessLookupError:
    gone = True
check(timed_out and time.monotonic() - start < 2 and pid > 0 and gone,
      "a program's timeout holds without pipes, and it's stopped")
start = time.monotonic()
try:
    subprocess.run(["sh", "-c", "exec >&- 2>&-; sleep 5"], capture_output=True, timeout=0.3)
    timed_out = False
except subprocess.TimeoutExpired:
    timed_out = True
check(timed_out and time.monotonic() - start < 2, "a program's timeout holds after it closes its output")
r = subprocess.run(["sh", "-c", "echo hi"], capture_output=True, timeout=5)
check(r.returncode == 0 and r.stdout == b"hi\n", "a program that finishes in time isn't affected")
if os.path.exists(P("left.pid")):
    os.unlink(P("left.pid"))
tool = archive._start(["sh", "-c", 'echo $$ > "$0"; exec sleep 30', P("left.pid")], stdout=subprocess.DEVNULL)
wait_for(lambda: os.path.exists(P("left.pid")) and open(P("left.pid")).read().strip() != "")
with open(P("left.pid")) as f:
    left = int(f.read().strip() or 0)
del tool  # nothing refers to it any more
try:
    os.kill(left, 0)
    gone = False
except ProcessLookupError:
    gone = True
check(left > 0 and gone, "a tool still running when nothing refers to it any more is stopped")
r = subprocess.run(["true"], input=b"x" * (4 << 20), capture_output=True, timeout=10)
check(r.returncode == 0, "writing to a program that exits without reading its input doesn't kill Kestrel")

# -- archive passwords: never on a tool's command line (where every user can see it), apart from zpaq's
pw = 'Kestrel-pw-7731 "quoted"'
os.makedirs(P("pwsrc"), exist_ok=True)
with open("/dev/urandom", "rb") as rnd, open(P("pwsrc/data.bin"), "wb") as f:
    f.write(rnd.read(30 << 20))  # big enough that the tool runs a while, for the watcher to see it
for t in ("rar", "zip"):
    label = f"{t}: a password-protected archive is made and opened, and the password is never on a command line"
    if not archive.tool(t) or not archive.tool("unrar" if t == "rar" else "7z"):
        skip(f"{label} ({t} isn't installed)")
        continue
    fmt = next(f for f in archive.formats() if f["id"] == t)
    out = P(f"secret.{t}")
    watching, seen, leaked = [True], [0], [0]

    def watch(name=f"secret.{t}".encode(), needle=pw[:15].encode()):
        """Every command line on the system, while the jobs run."""
        while watching[0]:
            for pid in os.listdir("/proc"):
                if pid.isdigit():
                    try:
                        with open(f"/proc/{pid}/cmdline", "rb") as f:
                            line = f.read()
                    except OSError:
                        continue
                    if name in line:
                        seen[0] += 1
                        leaked[0] += needle in line

    watcher = threading.Thread(target=watch)
    watcher.start()
    task = fileops.Task("test", lambda _t: None)
    spec = {"format": fmt, "tool": t, "base": P("pwsrc"), "rels": ["data.bin"], "out": out, "level": 1,
            "password": pw, "total": 30 << 20}
    made = opened = refused = False
    try:
        archive.compress(task, spec)
        made = os.path.exists(out)
        os.makedirs(P(f"pwout-{t}"), exist_ok=True)
        archive.extract(task, out, P(f"pwout-{t}"), pw, "overwrite")
        with open(P(f"pwout-{t}/data.bin"), "rb") as a, open(P("pwsrc/data.bin"), "rb") as b:
            opened = a.read() == b.read()
        os.makedirs(P(f"pwbad-{t}"), exist_ok=True)
        archive.extract(task, out, P(f"pwbad-{t}"), "wrong", "overwrite")
    except archive.WrongPassword:
        refused = True
    except Exception:
        pass
    watching[0] = False
    watcher.join()
    check(made and opened and refused and seen[0] > 0 and leaked[0] == 0, label)
finish()
