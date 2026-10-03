"""Kestrel's admin helper: runs as root (started once through pkexec) for an "admin session".

Kestrel writes one JSON request per line to this process's stdin and reads JSON replies from its stdout.
Nothing else can talk to it: the pipe belongs to the Kestrel process that started it. The helper exits as
soon as stdin closes (the session ends, Kestrel quits or crashes). Standard library only, no Qt.

Requests:  {"id": n, "op": "...", ...}   or   {"op": "cancel", "id": n}
Replies:   {"id": n, "progress": [done, total, text]}   then   {"id": n, "ok": true} / {"id": n, "ok": false,
           "error": "..."}

Ops: delete(path) · copy/move(src, dst, merge) · rename(src, dst) · mkdir(path) · copyfile(src, dst) ·
     touch(path) · symlink(target, link) · hardlink(target, link) · write(path, text, mode) · chmod(path, mode)
"""
import json
import os
import queue
import shutil
import stat
import sys
import threading
import time

# Never delete or overwrite these, whatever Kestrel asks (a guard against a slip turning into a disaster).
PROTECTED = {"/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib32", "/lib64", "/libx32", "/media",
             "/mnt", "/opt", "/proc", "/root", "/run", "/sbin", "/srv", "/sys", "/tmp", "/usr", "/var", "/snap",
             "/usr/bin", "/usr/lib", "/usr/sbin", "/usr/share", "/usr/local", "/var/lib", "/etc/ssh"}

_out_lock = threading.Lock()
_cancelled = set()


class Cancelled(Exception):
    pass


def send(obj):
    with _out_lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def _path(p):
    if not isinstance(p, str) or not os.path.isabs(p):
        raise ValueError(f"not an absolute path: {p!r}")
    return os.path.normpath(p)


def _guard(p):
    """Top-level folders (/usr, /data, …) and home folders themselves (/home/name) are never deleted or replaced."""
    if p in PROTECTED or os.path.dirname(p) in ("/", "/home"):
        raise PermissionError(f"refusing to delete or replace {p}")


class Job:
    def __init__(self, rid):
        self.id, self.done, self.total, self._last = rid, 0, 0, 0.0

    def check(self):
        if self.id in _cancelled:
            raise Cancelled()

    def report(self, text, force=False):
        now = time.monotonic()
        if force or now - self._last >= 0.1:
            self._last = now
            send({"id": self.id, "progress": [self.done, self.total, text]})

    def count(self, path):
        n = 1
        if os.path.isdir(path) and not os.path.islink(path):
            for _root, dirs, files in os.walk(path):
                self.check()
                n += len(dirs) + len(files)
        return n

    def remove(self, path):
        _guard(path)
        if os.path.isdir(path) and not os.path.islink(path):
            for root, dirs, files in os.walk(path, topdown=False):
                for name in files + dirs:
                    self.check()
                    p = os.path.join(root, name)
                    if name in dirs and not os.path.islink(p):
                        os.rmdir(p)
                    else:
                        os.unlink(p)
                    self.done += 1
                    self.report(name)
            os.rmdir(path)
        else:
            os.unlink(path)
        self.done += 1

    def copy(self, src, dst, merge):
        if os.path.islink(src):
            if os.path.lexists(dst):
                os.unlink(dst)
            os.symlink(os.readlink(src), dst)
        elif os.path.isdir(src):
            if os.path.lexists(dst) and not merge:
                self.remove(dst)
            os.makedirs(dst, exist_ok=True)
            for entry in os.scandir(src):
                self.copy(entry.path, os.path.join(dst, entry.name), merge)
            shutil.copystat(src, dst, follow_symlinks=False)
        else:
            self.check()
            shutil.copy2(src, dst, follow_symlinks=False)
        self.done += 1
        self.report(os.path.basename(src))

    def move(self, src, dst, merge):
        try:
            if not merge and os.lstat(src).st_dev == os.stat(os.path.dirname(dst)).st_dev:
                if os.path.lexists(dst):
                    self.remove(dst)
                os.rename(src, dst)
                self.done = self.total
                return
        except OSError:
            pass
        self.copy(src, dst, merge)
        self.remove(src)


def handle(req):
    op, job = req["op"], Job(req["id"])
    if op == "delete":
        p = _path(req["path"])
        _guard(p)
        job.total = job.count(p)
        job.remove(p)
    elif op in ("copy", "move"):
        src, dst = _path(req["src"]), _path(req["dst"])
        _guard(dst)
        job.total = job.count(src) * (2 if op == "move" else 1)
        (job.copy if op == "copy" else job.move)(src, dst, bool(req.get("merge")))
    elif op == "rename":
        src, dst = _path(req["src"]), _path(req["dst"])
        _guard(src)
        if os.path.lexists(dst):
            raise FileExistsError(f"{os.path.basename(dst)} already exists")
        os.rename(src, dst)
    elif op == "mkdir":
        os.makedirs(_path(req["path"]))
    elif op == "touch":
        open(_path(req["path"]), "x").close()
    elif op == "copyfile":
        dst = _path(req["dst"])
        if os.path.lexists(dst):
            raise FileExistsError(f"{os.path.basename(dst)} already exists")
        shutil.copyfile(_path(req["src"]), dst)
    elif op == "symlink":
        os.symlink(req["target"], _path(req["link"]))
    elif op == "hardlink":
        os.link(_path(req["target"]), _path(req["link"]))
    elif op == "write":
        p = _path(req["path"])
        with open(p, "x") as f:
            f.write(req["text"])
        os.chmod(p, int(req.get("mode", 0o644)))
    elif op == "chmod":
        p = _path(req["path"])
        _guard(p)
        os.chmod(p, stat.S_IMODE(int(req["mode"])))
    else:
        raise ValueError(f"unknown operation {op!r}")


def worker(jobs):
    while True:
        req = jobs.get()
        if req is None:
            return
        try:
            handle(req)
            send({"id": req["id"], "ok": True})
        except Cancelled:
            send({"id": req["id"], "ok": False, "error": "cancelled", "cancelled": True})
        except OSError as e:
            send({"id": req["id"], "ok": False, "error": e.strerror or str(e)})
        except Exception as e:
            send({"id": req["id"], "ok": False, "error": str(e)})
        finally:
            _cancelled.discard(req["id"])


def main():
    os.umask(0o022)
    jobs = queue.Queue()
    threading.Thread(target=worker, args=(jobs,), daemon=True).start()
    send({"hello": True, "uid": os.getuid(), "pid": os.getpid()})
    for line in sys.stdin:
        try:
            req = json.loads(line)
        except ValueError:
            continue
        if req.get("op") == "cancel":
            _cancelled.add(req.get("id"))
        elif "id" in req and "op" in req:
            jobs.put(req)
    os._exit(0)  # stdin closed: the session is over; stop whatever is running


if __name__ == "__main__":
    main()
