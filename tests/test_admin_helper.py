"""The admin helper (admin_helper.py), the part of Kestrel that runs as root: its operations, the folders it must never
delete or replace, and symlinks. It runs here as the normal user (no pkexec) and gets its requests over its pipe, as
Kestrel sends them. Paths it must refuse are ones that don't exist (/kestrel-test-none), so a slip can't touch anything
real. A "victim" folder stands for a system folder: requests that reach it through a symlink must be refused, and it
must stay unchanged."""
import ctypes
import json
import os
import select
import shutil
import stat
import subprocess
import threading
import time

from common import ROOT, check, finish

from kestrel import admin

J = os.path.join
AT_FDCWD, RENAME_EXCHANGE = -100, 2
_libc = ctypes.CDLL(None, use_errno=True)


def renameat2_exchange(a, b):
    return _libc.renameat2(AT_FDCWD, os.fsencode(a), AT_FDCWD, os.fsencode(b), RENAME_EXCHANGE) == 0


class Helper:
    def __init__(self):
        self.p = subprocess.Popen(["/usr/bin/python3", J(ROOT, "kestrel", "admin_helper.py")],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE)
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

    def stop(self):
        self.p.stdin.close()
        self.p.wait(5)

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
write_file(J(W, "r1"))
check(ok(h.call({"op": "rename", "src": J(W, "r1"), "dst": J(W, "r2")})) and os.path.exists(J(W, "r2"))
      and not ok(h.call({"op": "rename", "src": J(W, "r2"), "dst": J(W, "c.txt")}))
      and read_file(J(W, "c.txt")) == b"hello",
      "rename renames, and won't replace an existing name")
check(ok(h.call({"op": "symlink", "target": "w.txt", "link": J(W, "sl")})) and os.readlink(J(W, "sl")) == "w.txt"
      and ok(h.call({"op": "hardlink", "target": J(W, "w.txt"), "link": J(W, "hl")}))
      and read_file(J(W, "hl")) == b"hello",
      "symlink and hardlink make links")
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
