"""The admin helper (admin_helper.py), the part of Kestrel that runs as root: its operations, the folders it must never
delete or replace, and symlinks. It runs here as the normal user (no pkexec) and gets its requests over its pipe, as
Kestrel sends them. Paths it must refuse are ones that don't exist (/kestrel-test-none), so a slip can't touch anything
real. A "victim" folder stands for a system folder: requests that reach it through a symlink must be refused, and it
must stay unchanged."""
import ctypes
import json
import os
import resource
import select
import shutil
import signal
import stat
import subprocess
import threading
import time

from common import ROOT, check, finish, skip

from kestrel import admin

J = os.path.join
AT_FDCWD, RENAME_EXCHANGE = -100, 2
_libc = ctypes.CDLL(None, use_errno=True)


def renameat2_exchange(a, b):
    return _libc.renameat2(AT_FDCWD, os.fsencode(a), AT_FDCWD, os.fsencode(b), RENAME_EXCHANGE) == 0


class Helper:
    def __init__(self, preexec_fn=None):
        self.p = subprocess.Popen(["/usr/bin/python3", J(ROOT, "kestrel", "admin_helper.py")],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, preexec_fn=preexec_fn)
        self.buf = b""
        self.next_id = 1

    def started(self):
        return self._next_reply(None, 5).get("hello") is True

    def send(self, req):
        rid = self.next_id
        self.next_id += 1
        self.p.stdin.write(json.dumps({**req, "id": rid}).encode() + b"\n")
        self.p.stdin.flush()
        return rid

    def cancel(self, rid):
        self.p.stdin.write(json.dumps({"op": "cancel", "id": rid}).encode() + b"\n")
        self.p.stdin.flush()

    def reply(self, rid, timeout=20):
        """Its final reply ({"ok": …}), skipping progress."""
        while True:
            r = self._next_reply(rid, timeout)
            if not r or "ok" in r:
                return r

    def call(self, req, timeout=20):
        return self.reply(self.send(req), timeout)

    def next(self, rid, timeout=20):
        """The next message for a request: a progress report ({"progress": [done, total, text]}) or its final
        reply."""
        return self._next_reply(rid, timeout)

    def stop(self, timeout=5):
        """Close its input, as when the session ends; True if it then exits."""
        self.p.stdin.close()
        try:
            self.p.wait(timeout)
            return True
        except subprocess.TimeoutExpired:
            return False

    def _next_reply(self, rid, timeout):
        while True:
            nl = self.buf.find(b"\n")
            if nl >= 0:
                r = json.loads(self.buf[:nl])
                self.buf = self.buf[nl + 1:]
                if rid is None or r.get("id") == rid:
                    return r
                continue
            if not select.select([self.p.stdout], [], [], timeout)[0]:
                return {}
            chunk = os.read(self.p.stdout.fileno(), 65536)
            if not chunk:
                return {}
            self.buf += chunk


def ok(r):
    return r.get("ok") is True


def refused(r):
    return not ok(r) and "refusing" in r.get("error", "")


def write_file(p, data=b"x"):
    with open(p, "wb") as f:
        f.write(data)


def read_file(p):
    try:
        with open(p, "rb") as f:
            return f.read()
    except OSError:
        return b""


def mode_of(p):
    try:
        return stat.S_IMODE(os.lstat(p).st_mode)
    except OSError:
        return -1


def snapshot(d):
    """Every name under a folder, with its permissions: to see that a folder is unchanged."""
    out = []
    for root, dirs, files in os.walk(d):
        for n in dirs + files:
            p = J(root, n)
            out.append(f"{p[len(d):]} {mode_of(p):o}")
    return sorted(out)


def make_tree(root, dirs, files):
    os.makedirs(root, exist_ok=True)
    for d in range(dirs):
        sub = J(root, f"d{d}")
        os.makedirs(sub, exist_ok=True)
        for f in range(files):
            write_file(J(sub, f"f{f}"))


T = J(os.environ["HOME"], "t")
W = J(T, "work")
victim = J(T, "victim")  # stands for a system folder
link = J(T, "link")      # → victim


def reset_victim():
    """As it was, so each check starts from the same victim folder."""
    shutil.rmtree(victim, ignore_errors=True)
    os.makedirs(J(victim, "sub"))
    write_file(J(victim, "keep"), b"keep")
    os.chmod(J(victim, "keep"), 0o644)


os.makedirs(W)
reset_victim()
os.symlink(victim, link)
before = snapshot(victim)


def victim_intact():
    return snapshot(victim) == before and read_file(J(victim, "keep")) == b"keep"


h = Helper()
check(h.started(), "the helper starts and says hello")

# -- the operations
check(ok(h.call({"op": "mkdir", "path": J(W, "a/b/c")})) and os.path.isdir(J(W, "a/b/c")),
      "mkdir makes a folder and its missing parents")
made = (ok(h.call({"op": "touch", "path": J(W, "t.txt")}))
        and ok(h.call({"op": "write", "path": J(W, "w.txt"), "text": "hello", "mode": 0o600})))
check(made and os.path.exists(J(W, "t.txt")) and read_file(J(W, "w.txt")) == b"hello"
      and mode_of(J(W, "w.txt")) == 0o600,
      "touch and write make new files (write sets the mode)")
check(not ok(h.call({"op": "touch", "path": J(W, "w.txt")}))
      and not ok(h.call({"op": "write", "path": J(W, "w.txt"), "text": "other"}))
      and read_file(J(W, "w.txt")) == b"hello",
      "touch and write refuse an existing file")
check(ok(h.call({"op": "copyfile", "src": J(W, "w.txt"), "dst": J(W, "c.txt")}))
      and read_file(J(W, "c.txt")) == b"hello"
      and not ok(h.call({"op": "copyfile", "src": J(W, "t.txt"), "dst": J(W, "c.txt")})),
      "copyfile copies a file and won't overwrite one")

# what the helper makes gets the owner of the folder it's made in (run as root: the user's in their home), not the
# helper's. Seen here through the group: a folder given another of the user's groups
other = next((g for g in reversed(os.getgroups()) if g != os.getegid()), os.getegid())
if other == os.getegid():
    skip("new items get the folder's owner (the user has no other group)")
else:
    own, own_src = J(W, "own"), J(W, "own-src.txt")
    os.makedirs(own, exist_ok=True)
    os.chown(own, -1, other)
    write_file(own_src, b"s")
    os.chmod(own_src, 0o2755)
    make_tree(J(W, "own-tree"), 1, 1)
    done = (ok(h.call({"op": "copy", "src": own_src, "dst": J(own, "copy.txt")}))
            and ok(h.call({"op": "copy", "src": J(W, "own-tree"), "dst": J(own, "tree")}))
            and ok(h.call({"op": "mkdir", "path": J(own, "new/sub")}))
            and ok(h.call({"op": "touch", "path": J(own, "t.txt")}))
            and ok(h.call({"op": "write", "path": J(own, "w.txt"), "text": "w"}))
            and ok(h.call({"op": "copyfile", "src": own_src, "dst": J(own, "cf.txt")}))
            and ok(h.call({"op": "symlink", "target": "t.txt", "link": J(own, "l")})))
    check(done and all(os.lstat(J(own, n)).st_gid == other for n in (
        "copy.txt", "tree", "tree/d0", "tree/d0/f0", "new", "new/sub", "t.txt", "w.txt", "cf.txt", "l")),
        "copies, new folders (and the parents made for them), files and links get the folder's owner")
    check(done and mode_of(J(own, "copy.txt")) == 0o755, "a copy given another group loses its set-group-ID bit")

tree = J(W, "tree")
make_tree(tree, 2, 2)
os.symlink(victim, J(tree, "to-victim"))
copied = ok(h.call({"op": "copy", "src": tree, "dst": J(W, "tree2")}))
check(copied and os.path.exists(J(W, "tree2/d1/f1")) and os.path.islink(J(W, "tree2/to-victim"))
      and os.readlink(J(W, "tree2/to-victim")) == victim and victim_intact(),
      "copy copies a folder tree, and symlinks in it as links")
check(ok(h.call({"op": "move", "src": J(W, "tree2"), "dst": J(W, "tree3")}))
      and not os.path.exists(J(W, "tree2")) and os.path.exists(J(W, "tree3/d0/f0")),
      "move moves a folder")


# the same entry as source and destination: the helper must notice (its "replace the destination" step would delete the
# source), whatever Kestrel sends
def contents(root):
    """Names and file contents."""
    out = []
    for d, dirs, files in os.walk(root):
        for n in dirs + files:
            p = J(d, n)
            out.append(os.path.relpath(p, root) + "=" + (read_file(p).decode() if os.path.isfile(p) else ""))
    return sorted(out)


write_file(J(W, "self.txt"), b"mine")
tree_before = contents(J(W, "tree3"))
h.call({"op": "move", "src": J(W, "self.txt"), "dst": J(W, "self.txt")})
h.call({"op": "copy", "src": J(W, "self.txt"), "dst": J(W, "self.txt")})
h.call({"op": "move", "src": J(W, "tree3"), "dst": J(W, "tree3")})
h.call({"op": "copy", "src": J(W, "tree3"), "dst": J(W, "tree3")})
h.call({"op": "copy", "src": J(W, "tree3"), "dst": J(W, "tree3"), "merge": True})
check(read_file(J(W, "self.txt")) == b"mine" and tree_before and contents(J(W, "tree3")) == tree_before,
      "moving or copying a file or folder onto itself leaves it as it was")
into_move = h.call({"op": "move", "src": J(W, "tree3"), "dst": J(W, "tree3/d0/in")}, 10)
into_copy = h.call({"op": "copy", "src": J(W, "tree3"), "dst": J(W, "tree3/d0/in")}, 10)
check(into_move and not ok(into_move) and into_copy and not ok(into_copy) and contents(J(W, "tree3")) == tree_before,
      "moving or copying a folder into itself is refused, and the folder is left as it was")
write_file(J(W, "hl-a"), b"linked")
os.link(J(W, "hl-a"), J(W, "hl-b"))
h.call({"op": "move", "src": J(W, "hl-a"), "dst": J(W, "hl-b")})
check(read_file(J(W, "hl-b")) == b"linked", "moving a file onto a hard link to it keeps the file")


# -- replacing: what's replaced stays as it was until the new one is complete (a failure, a cancel or the session
# ending leaves it, and no half-made copy); a folder is swapped in whole
def leftovers():
    """Half-made copies (".kes-….part") anywhere in W."""
    return [J(d, n) for d, dirs, files in os.walk(W) for n in dirs + files if n.startswith(".kes-")]


write_file(J(W, "big6"), b"b" * (6 << 20))
write_file(J(W, "conf.txt"), b"original")
os.makedirs(J(W, "newdir"))
write_file(J(W, "newdir/a.txt"), b"a")
write_file(J(W, "newdir/big"), b"n" * (6 << 20))
os.makedirs(J(W, "olddir"))
write_file(J(W, "olddir/keep.txt"), b"keep")
old_before = contents(J(W, "olddir"))


def small_disk():
    """In the helper: writes past 1 MB fail (EFBIG), as on a full disk."""
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    resource.setrlimit(resource.RLIMIT_FSIZE, (1 << 20, resource.getrlimit(resource.RLIMIT_FSIZE)[1]))


small = Helper(small_disk)
started = small.started()
file_r = small.call({"op": "copy", "src": J(W, "big6"), "dst": J(W, "conf.txt")})
dir_r = small.call({"op": "copy", "src": J(W, "newdir"), "dst": J(W, "olddir")})
small.stop()
check(started and file_r and not ok(file_r) and read_file(J(W, "conf.txt")) == b"original" and not leftovers(),
      "replacing a file: a write that fails part-way (a full disk) keeps the old file, and leaves no "
      "half-made copy")
check(dir_r and not ok(dir_r) and contents(J(W, "olddir")) == old_before and not leftovers(),
      "replacing a folder: a failure part-way keeps the old folder as it was, and leaves no half-made copy")
write_file(J(W, "huge"), b"x")
os.truncate(J(W, "huge"), 2 << 30)   # 2 GB (sparse): long enough to stop part-way
rid = h.send({"op": "copy", "src": J(W, "huge"), "dst": J(W, "conf.txt")})
pr = h.next(rid).get("progress") or []
partway = bool(pr) and 0 < pr[0] < pr[1] / 2
clock = time.monotonic()
h.cancel(rid)
r = h.reply(rid)
check(partway and r.get("cancelled") and time.monotonic() - clock < 2 and read_file(J(W, "conf.txt")) == b"original"
      and not leftovers(),
      "a large file's copy reports its bytes as it goes, and a cancel stops it part-way at once, keeping "
      "the file it was replacing and no half-made copy")
ending = Helper()
started = ending.started()
rid = ending.send({"op": "copy", "src": J(W, "huge"), "dst": J(W, "conf.txt")})
partway = bool(ending.next(rid).get("progress"))
exited = ending.stop()
check(started and partway and exited and read_file(J(W, "conf.txt")) == b"original" and not leftovers(),
      "ending the admin session part-way through a copy stops it, keeping the file it was replacing and no "
      "half-made copy")
os.unlink(J(W, "huge"))
os.makedirs(J(W, "mvsrc"))
write_file(J(W, "mvsrc/moved.txt"), b"moved")
os.makedirs(J(W, "mvdst"))
write_file(J(W, "mvdst/old.txt"), b"old")
copied_over = ok(h.call({"op": "copy", "src": J(W, "newdir"), "dst": J(W, "olddir")}))
moved_over = ok(h.call({"op": "move", "src": J(W, "mvsrc"), "dst": J(W, "mvdst")}))
check(copied_over and contents(J(W, "olddir")) == contents(J(W, "newdir")) and moved_over
      and not os.path.exists(J(W, "mvsrc")) and contents(J(W, "mvdst")) == ["moved.txt=moved"] and not leftovers(),
      "copying or moving a folder onto another replaces it whole: the old contents are gone, nothing is left "
      "behind")
write_file(J(W, "r1"))
check(ok(h.call({"op": "rename", "src": J(W, "r1"), "dst": J(W, "r2")})) and os.path.exists(J(W, "r2"))
      and not ok(h.call({"op": "rename", "src": J(W, "r2"), "dst": J(W, "c.txt")}))
      and read_file(J(W, "c.txt")) == b"hello",
      "rename renames, and won't replace an existing name")
check(ok(h.call({"op": "symlink", "target": "w.txt", "link": J(W, "sl")})) and os.readlink(J(W, "sl")) == "w.txt"
      and ok(h.call({"op": "hardlink", "target": J(W, "w.txt"), "link": J(W, "hl")}))
      and read_file(J(W, "hl")) == b"hello",
      "symlink and hardlink make links")
other = h.call({"op": "hardlink", "target": "/etc/hostname", "link": J(W, "not-mine")})
check(not ok(other) and "isn't yours" in other.get("error", "") and not os.path.exists(J(W, "not-mine")),
      "hardlink refuses a file that isn't the user's (the helper's own rule, before the kernel's)")
check(ok(h.call({"op": "chmod", "path": J(W, "c.txt"), "mode": 0o640})) and mode_of(J(W, "c.txt")) == 0o640,
      "chmod changes permissions")
check(ok(h.call({"op": "delete", "path": J(W, "tree3")})) and not os.path.exists(J(W, "tree3")),
      "delete removes a folder tree")
check(ok(h.call({"op": "delete", "path": tree})) and not os.path.exists(tree) and victim_intact(),
      "deleting a tree with a symlink inside removes the link, not what it points to")
os.symlink(victim, J(W, "lone-link"))
check(ok(h.call({"op": "delete", "path": J(W, "lone-link")})) and not os.path.islink(J(W, "lone-link"))
      and victim_intact(),
      "deleting a symlink removes the link only")
make_tree(J(W, "big"), 20, 100)
rid = h.send({"op": "copy", "src": J(W, "big"), "dst": J(W, "big2")})
h.cancel(rid)
check(h.reply(rid).get("cancelled") is True, "a running job can be cancelled")

# -- paths
check(not ok(h.call({"op": "delete", "path": "relative/x"}))
      and "absolute" in h.call({"op": "delete", "path": "relative/x"}).get("error", ""),
      "relative paths are refused")

# -- folders that are never deleted or replaced (paths that don't exist: refused, not "not found")
top = ["/kestrel-test-none", "/home/kestrel-test-none"]
check(all(refused(h.call({"op": "delete", "path": p})) for p in top), "top-level and home folders can't be deleted")
check(all(refused(h.call({"op": "copy", "src": J(W, "c.txt"), "dst": p}))
          and refused(h.call({"op": "move", "src": J(W, "c.txt"), "dst": p})) for p in top)
      and os.path.exists(J(W, "c.txt")),
      "...or replaced by a copy or move")
check(all(refused(h.call({"op": "move", "src": p, "dst": J(W, "moved")})) for p in top), "...or moved away")
check(all(refused(h.call({"op": "rename", "src": p, "dst": J(W, "renamed")})) for p in top), "...or renamed")
check(all(refused(h.call({"op": "chmod", "path": p, "mode": 0o777})) for p in top),
      "...or have their permissions changed")

# -- symlinks in the path: refused, and the victim folder stays as it was
reset_victim()
check(not ok(h.call({"op": "mkdir", "path": J(link, "new/deeper")})) and victim_intact(),
      "mkdir through a symlinked folder is refused")
reset_victim()
check(not ok(h.call({"op": "touch", "path": J(link, "t")}))
      and not ok(h.call({"op": "write", "path": J(link, "w"), "text": "x"}))
      and not ok(h.call({"op": "copyfile", "src": J(W, "c.txt"), "dst": J(link, "c")})) and victim_intact(),
      "new files through a symlinked folder are refused (touch, write, copyfile)")
reset_victim()
check(not ok(h.call({"op": "symlink", "target": "/", "link": J(link, "s")}))
      and not ok(h.call({"op": "hardlink", "target": J(W, "c.txt"), "link": J(link, "h")})) and victim_intact(),
      "links through a symlinked folder are refused (symlink, hardlink)")
reset_victim()
check(not ok(h.call({"op": "delete", "path": J(link, "keep")})) and victim_intact(),
      "delete through a symlinked folder is refused")
reset_victim()
check(not ok(h.call({"op": "copy", "src": J(W, "c.txt"), "dst": J(link, "keep")}))
      and not ok(h.call({"op": "move", "src": J(W, "w.txt"), "dst": J(link, "keep")})) and victim_intact(),
      "copy or move into a symlinked folder is refused")
reset_victim()
check(not ok(h.call({"op": "rename", "src": J(link, "keep"), "dst": J(W, "out")})) and victim_intact(),
      "rename out of a symlinked folder is refused")
reset_victim()
check(not ok(h.call({"op": "chmod", "path": J(link, "keep"), "mode": 0o777})) and victim_intact(),
      "chmod through a symlinked folder is refused")
reset_victim()
os.symlink(J(victim, "keep"), J(W, "to-keep"))
h.call({"op": "chmod", "path": J(W, "to-keep"), "mode": 0o777})
check(victim_intact(), "chmod on a symlink doesn't change what it points to")

# -- Kestrel's side: the user's own symlinked folders are resolved before the helper sees them
args = admin.resolve_paths("delete", {"path": J(link, "keep")})
whole = admin.resolve_paths("chmod", {"path": J(W, "to-keep"), "mode": 0o600})
check(args["path"] == J(victim, "keep")
      and admin.resolve_paths("delete", {"path": J(W, "to-keep")})["path"] == J(W, "to-keep")
      and whole["path"] == J(victim, "keep") and whole["mode"] == 0o600,
      "Kestrel resolves the user's own symlinked folders first (chmod: the whole path; a link itself otherwise)")

# -- a folder swapped for a symlink while a delete runs: renameat2(RENAME_EXCHANGE) trades a folder in the tree for a
# symlink to the victim (kept outside the tree) and back, in one step each, so the path always exists
# (a race: the old helper loses it in some rounds, not all, so rounds run for a few seconds)
safe = True
start = time.monotonic()
rnd = 0
while time.monotonic() - start < 4 and safe:
    reset_victim()
    race, links = J(T, f"race{rnd}"), J(T, f"links{rnd}")
    make_tree(race, 40, 5)
    os.makedirs(links)
    for d in range(40):
        os.symlink(victim, J(links, f"d{d}"))
    stop = threading.Event()

    def swapper():
        while not stop.is_set():
            for d in range(40):
                if stop.is_set():
                    break
                a, b = J(race, f"d{d}"), J(links, f"d{d}")
                if renameat2_exchange(a, b):
                    time.sleep(0.0002)
                    renameat2_exchange(a, b)

    t = threading.Thread(target=swapper)
    t.start()
    h.call({"op": "delete", "path": race})
    stop.set()
    t.join()
    safe = victim_intact()
    rnd += 1
check(safe, "a folder swapped for a symlink during a delete doesn't let it delete outside the tree")

h.stop()
finish()
