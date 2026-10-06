"""Kestrel's admin helper: runs as root (started once through pkexec) for an "admin session".

Kestrel writes one JSON request per line to this process's stdin and reads JSON replies from its stdout.
Nothing else can talk to it: the pipe belongs to the Kestrel process that started it. The helper exits as
soon as stdin closes (the session ends, Kestrel quits or crashes). Standard library only, no Qt.

Requests:  {"id": n, "op": "...", ...}   or   {"op": "cancel", "id": n}
Replies:   {"id": n, "progress": [done, total, text]}   then   {"id": n, "ok": true} / {"id": n, "ok": false,
           "error": "..."}

Ops: delete(path) · copy/move(src, dst, merge) · rename(src, dst) · mkdir(path) · copyfile(src, dst) ·
     touch(path) · symlink(target, link) · hardlink(target, link) · write(path, text, mode) · chmod(path, mode)

Safety: the files being worked on can change while root works on them (another user, or a program, swapping a
folder for a symlink). So the helper never trusts a path string:
- Every path is walked one folder at a time from "/", each opened without following symlinks (walk()). A symlink on
  the way is followed only if root controls it: owned by root, in a folder only root can write to (system links such
  as /lib → usr/lib). Any other symlink is refused. Kestrel resolves the user's own symlinked folders before sending a
  request (admin.py), so those reach the helper as real paths.
- The operation then works relative to the opened folder (dir_fd), on the name itself: a symlink there is the link,
  never what it points to.
- Recursive copy and delete go folder by folder through open descriptors, and stop if anything changed under them.
- One policy, POLICY below, says which paths an operation may delete, replace or change: never the protected folders,
  top-level folders (/data) or home folders themselves (/home/name). It's checked on the real path walked.
- hardlink links only the user's own files: a second name for, say, /etc/shadow in their folder would let a later
  chmod or write there change the real file.
- A copy root makes is root's, so it drops set-user-ID and set-group-ID from a file that wasn't root's (else a user's
  program would become a set-user-ID root program). A move between drives keeps the owner instead.
- A mount point is never deleted (that would empty the drive mounted there), nor is another drive inside a tree.
"""
import ctypes
import errno
import json
import os
import queue
import stat
import sys
import threading
import time

# Never delete or overwrite these, whatever Kestrel asks (a guard against a slip turning into a disaster).
PROTECTED = {"/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib32", "/lib64", "/libx32", "/media",
             "/mnt", "/opt", "/proc", "/root", "/run", "/sbin", "/srv", "/sys", "/tmp", "/usr", "/var", "/snap",
             "/usr/bin", "/usr/lib", "/usr/sbin", "/usr/share", "/usr/local", "/var/lib", "/etc/ssh"}

# What each operation does to each of its paths. "delete", "replace" and "change" are refused for protected,
# top-level and home folders; "create" makes a new name only (it fails if the name exists), so it's allowed anywhere;
# "read" only reads. Every path argument of every op is listed here, and handle() walks them all through check().
READ, CREATE, DELETE, REPLACE, CHANGE = range(5)
POLICY = {
    "delete": [("path", DELETE)],
    "copy": [("src", READ), ("dst", REPLACE)],
    "move": [("src", DELETE), ("dst", REPLACE)],
    "rename": [("src", DELETE), ("dst", CREATE)],
    "mkdir": [("path", CREATE)],
    "touch": [("path", CREATE)],
    "copyfile": [("src", READ), ("dst", CREATE)],
    "symlink": [("link", CREATE)],
    "hardlink": [("target", READ), ("link", CREATE)],
    "write": [("path", CREATE)],
    "chmod": [("path", CHANGE)],
}

AT_FDCWD, RENAME_NOREPLACE = -100, 1
_libc = ctypes.CDLL(None, use_errno=True)

_out_lock = threading.Lock()
_cancelled = set()


class Cancelled(Exception):
    pass


class Failure(Exception):
    pass


def send(obj):
    with _out_lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def _components(p):
    """Normalised: no "", "." or ".."."""
    out = []
    for c in p.split("/"):
        if c == "..":
            if out:
                out.pop()
        elif c and c != ".":
            out.append(c)
    return out


def _stat_at(dirfd, name):
    """lstat, relative to dirfd; None if it doesn't exist."""
    try:
        return os.stat(name, dir_fd=dirfd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _same_file(a, b):
    return a.st_dev == b.st_dev and a.st_ino == b.st_ino


def _open_dir_at(dirfd, name, st, flags=os.O_RDONLY):
    """Open the folder `name` in dirfd, which must still be the folder `st` described (not swapped for a symlink or
    another folder since): O_NOFOLLOW refuses a symlink, and the inode is compared."""
    fd = os.open(name, flags | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dirfd)
    if not _same_file(os.fstat(fd), st):
        os.close(fd)
        raise Failure(f"{name} changed while it was being worked on")
    return fd


class Walk:
    """The folder a path's last part is in, opened safely (see the top of this file), and that last part."""

    def __init__(self, dirfd, name, path):
        self.dir, self.name, self.path = dirfd, name, path  # name: "" for "/"; path: the real path walked

    def close(self):
        os.close(self.dir)


def _trusted_link(dirfd, link):
    """A symlink only root can have made or changed."""
    d = os.fstat(dirfd)
    return link.st_uid == 0 and d.st_uid == 0 and not d.st_mode & (stat.S_IWGRP | stat.S_IWOTH)


def walk(path, make_parents=False):
    for _links in range(41):
        parts = _components(path)
        dirfd = os.open("/", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
        real = ""
        restarted = False
        try:
            for i, c in enumerate(parts[:-1]):
                st = _stat_at(dirfd, c)
                if st is None:
                    if not make_parents:
                        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT))
                    try:
                        os.mkdir(c, 0o777, dir_fd=dirfd)
                    except FileExistsError:
                        pass
                    st = os.stat(c, dir_fd=dirfd, follow_symlinks=False)
                if stat.S_ISLNK(st.st_mode):
                    if not _trusted_link(dirfd, st):
                        raise Failure(f"refusing to follow the symlink {real}/{c} (it isn't owned by root)")
                    target = os.readlink(c, dir_fd=dirfd)
                    path = "/".join([target if target.startswith("/") else f"{real}/{target}"] + parts[i + 1:])
                    restarted = True
                    break
                if not stat.S_ISDIR(st.st_mode):
                    raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR))
                nfd = _open_dir_at(dirfd, c, st, os.O_PATH)
                os.close(dirfd)
                dirfd = nfd
                real += "/" + c
        except BaseException:
            os.close(dirfd)
            raise
        if restarted:
            os.close(dirfd)
            continue
        name = parts[-1] if parts else ""
        return Walk(dirfd, name, f"{real}/{name}" if parts else "/")
    raise OSError(errno.ELOOP, os.strerror(errno.ELOOP))


def _session_uid():
    """The user the session is for: pkexec says who started it (as root, getuid() is 0); run directly, the caller."""
    v = os.environ.get("PKEXEC_UID", "")
    return int(v) if v.isdigit() else os.getuid()


def _path_arg(req, key):
    p = req.get(key)
    if not isinstance(p, str) or not p.startswith("/"):
        raise ValueError(f"not an absolute path: {p!r}")
    return p


def check(w, kind):
    """The policy (see POLICY): top-level folders (/usr, /data, …) and home folders themselves (/home/name) are never
    deleted, replaced or changed."""
    if not w.name:
        raise PermissionError("refusing to work on /")
    if kind in (READ, CREATE):
        return
    if w.path in PROTECTED or os.path.dirname(w.path) in ("/", "/home"):
        raise PermissionError(f"refusing to delete, replace or change {w.path}")


def _copy_data(src, dst):
    while True:
        chunk = os.read(src, 1 << 20)
        if not chunk:
            return
        view = memoryview(chunk)
        while view:
            n = os.write(dst, view)
            view = view[n:]


def _copy_stat_fd(fd, st, keep_owner):
    """The permissions and times of an open file or folder (on a descriptor: never through a symlink); keep_owner
    (a move): its owner too."""
    mode = stat.S_IMODE(st.st_mode)
    try:
        os.utime(fd, ns=(st.st_atime_ns, st.st_mtime_ns))
        if keep_owner:
            os.chown(fd, st.st_uid, st.st_gid)  # first: chown clears set-user-ID, which chmod then puts back
        elif stat.S_ISREG(st.st_mode) and st.st_uid != os.geteuid():
            mode &= ~(stat.S_ISUID | stat.S_ISGID)  # a copy of someone else's set-user-ID program mustn't run as us
    except OSError:
        pass
    try:
        os.chmod(fd, mode)
    except OSError:
        pass


def _open_new_file(w):
    return os.open(w.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o666, dir_fd=w.dir)


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

    def count(self, dirfd, name):
        """How many items a tree has, for progress (only reads; never follows a symlink)."""
        st = _stat_at(dirfd, name)
        n = 1
        if st is not None and stat.S_ISDIR(st.st_mode):
            self.check()
            try:
                d = _open_dir_at(dirfd, name, st)
            except (OSError, Failure):
                return n
            try:
                for e in os.listdir(d):
                    n += self.count(d, e)
            finally:
                os.close(d)
        return n

    def remove(self, dirfd, name):
        """Delete `name` in dirfd, and everything in it, without leaving the filesystem it's on."""
        st = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
        if stat.S_ISDIR(st.st_mode) and os.fstat(dirfd).st_dev != st.st_dev:
            raise Failure(f"{name} is a mount point (another drive); not deleting it")
        self._remove_tree(dirfd, name, st, st.st_dev)
        self.done += 1

    def _remove_tree(self, dirfd, name, st, dev):
        if stat.S_ISDIR(st.st_mode):
            if st.st_dev != dev:
                raise Failure(f"{name} is on another drive (a mount point); not deleting it")
            d = _open_dir_at(dirfd, name, st)
            try:
                for e in os.listdir(d):
                    self.check()
                    self._remove_tree(d, e, os.stat(e, dir_fd=d, follow_symlinks=False), dev)
                    self.done += 1
                    self.report(e)
            finally:
                os.close(d)
            os.rmdir(name, dir_fd=dirfd)
        else:  # a file or a symlink: the name itself
            os.unlink(name, dir_fd=dirfd)

    def copy(self, src_dir, src, dst_dir, dst, merge, keep_owner=False):
        st = os.stat(src, dir_fd=src_dir, follow_symlinks=False)
        dst_st = _stat_at(dst_dir, dst)
        if dst_st is not None and stat.S_ISDIR(dst_st.st_mode) and not stat.S_ISDIR(st.st_mode):
            raise Failure(f"{dst} is a folder")  # a file or link never replaces a whole folder
        if stat.S_ISLNK(st.st_mode):
            target = os.readlink(src, dir_fd=src_dir)
            if dst_st is not None:
                self.remove(dst_dir, dst)
            os.symlink(target, dst, dir_fd=dst_dir)
            try:
                os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns), dir_fd=dst_dir, follow_symlinks=False)
                if keep_owner:
                    os.chown(dst, st.st_uid, st.st_gid, dir_fd=dst_dir, follow_symlinks=False)
            except OSError:
                pass
        elif stat.S_ISDIR(st.st_mode):
            src_fd = _open_dir_at(src_dir, src, st)
            try:
                if dst_st is not None and (not merge or not stat.S_ISDIR(dst_st.st_mode)):
                    if merge:
                        raise Failure(f"{dst} exists and isn't a folder")
                    self.remove(dst_dir, dst)
                    dst_st = None
                if dst_st is None:
                    os.mkdir(dst, 0o700, dir_fd=dst_dir)
                dst_fd = _open_dir_at(dst_dir, dst, os.stat(dst, dir_fd=dst_dir, follow_symlinks=False))
                try:
                    for e in os.listdir(src_fd):
                        self.copy(src_fd, e, dst_fd, e, merge, keep_owner)
                    _copy_stat_fd(dst_fd, st, keep_owner)
                finally:
                    os.close(dst_fd)
            finally:
                os.close(src_fd)
        elif stat.S_ISREG(st.st_mode):
            self.check()
            in_fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=src_dir)
            try:
                if not _same_file(os.fstat(in_fd), st):
                    raise Failure(f"{src} changed while it was being worked on")
                if dst_st is not None:
                    self.remove(dst_dir, dst)  # a new file: never writes through a link to another one
                out_fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                                 dir_fd=dst_dir)
                try:
                    _copy_data(in_fd, out_fd)
                    _copy_stat_fd(out_fd, st, keep_owner)
                finally:
                    os.close(out_fd)
            finally:
                os.close(in_fd)
        else:
            raise Failure(f"{src} isn't a regular file, folder or link")
        self.done += 1
        self.report(src)

    def move(self, src, dst, merge):
        st = _stat_at(src.dir, src.name)
        if not merge and st is not None and st.st_dev == os.fstat(dst.dir).st_dev:
            if _stat_at(dst.dir, dst.name) is not None:
                self.remove(dst.dir, dst.name)
            # no-replace: something that appeared there since isn't silently replaced
            r = _libc.renameat2(src.dir, os.fsencode(src.name), dst.dir, os.fsencode(dst.name), RENAME_NOREPLACE)
            e = ctypes.get_errno() if r != 0 else 0
            if r != 0 and e == errno.EINVAL:  # a filesystem without RENAME_NOREPLACE
                try:
                    os.rename(src.name, dst.name, src_dir_fd=src.dir, dst_dir_fd=dst.dir)
                    r = 0
                except OSError:
                    pass
            if r == 0:
                self.done = self.total
                return
            if e == errno.EEXIST:
                raise Failure(f"{dst.name} appeared while it was being replaced")
        self.copy(src.dir, src.name, dst.dir, dst.name, merge, keep_owner=True)  # a move keeps the owner
        self.remove(src.dir, src.name)


def handle(req):
    op = req["op"]
    if op not in POLICY:
        raise ValueError(f"unknown operation {op!r}")
    # every path argument, walked and checked against the policy before anything is done
    w = {}
    try:
        for key, kind in POLICY[op]:
            w[key] = walk(_path_arg(req, key), make_parents=op == "mkdir")
            check(w[key], kind)
        _run(op, req, w)
    finally:
        for walked in w.values():
            walked.close()


def _run(op, req, w):
    job = Job(req["id"])
    if op == "delete":
        p = w["path"]
        job.total = job.count(p.dir, p.name)
        job.remove(p.dir, p.name)
    elif op in ("copy", "move"):
        src, dst = w["src"], w["dst"]
        job.total = job.count(src.dir, src.name) * (2 if op == "move" else 1)
        if op == "copy":
            job.copy(src.dir, src.name, dst.dir, dst.name, bool(req.get("merge")))
        else:
            job.move(src, dst, bool(req.get("merge")))
    elif op == "rename":
        src, dst = w["src"], w["dst"]
        if _libc.renameat2(src.dir, os.fsencode(src.name), dst.dir, os.fsencode(dst.name), RENAME_NOREPLACE) != 0:
            e = ctypes.get_errno()
            if e == errno.EEXIST:
                raise FileExistsError(f"{dst.name} already exists")
            if e != errno.EINVAL:  # a filesystem without RENAME_NOREPLACE: check, then rename
                raise OSError(e, os.strerror(e))
            if _stat_at(dst.dir, dst.name) is not None:
                raise FileExistsError(f"{dst.name} already exists")
            os.rename(src.name, dst.name, src_dir_fd=src.dir, dst_dir_fd=dst.dir)
    elif op == "mkdir":  # the parents were made by walk(); the last one must be new
        os.mkdir(w["path"].name, 0o777, dir_fd=w["path"].dir)
    elif op == "touch":
        os.close(_open_new_file(w["path"]))
    elif op == "copyfile":
        src, dst = w["src"], w["dst"]
        if _stat_at(dst.dir, dst.name) is not None:
            raise FileExistsError(f"{dst.name} already exists")
        in_fd = os.open(src.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=src.dir)
        try:
            out_fd = _open_new_file(dst)
            try:
                _copy_data(in_fd, out_fd)
            finally:
                os.close(out_fd)
        finally:
            os.close(in_fd)
    elif op == "symlink":
        os.symlink(req["target"], w["link"].name, dir_fd=w["link"].dir)
    elif op == "hardlink":
        # the file itself, opened without following a symlink: its owner is checked, and that same file is linked
        # (through /proc/self/fd), so it can't be swapped for another between the check and the link
        t, link = w["target"], w["link"]
        fd = os.open(t.name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=t.dir)
        try:
            if os.fstat(fd).st_uid != _session_uid():
                raise PermissionError(f"refusing to hard-link a file that isn't yours: {t.path}")
            os.link(f"/proc/self/fd/{fd}", link.name, dst_dir_fd=link.dir, follow_symlinks=True)
        finally:
            os.close(fd)
    elif op == "write":
        fd = _open_new_file(w["path"])
        try:
            data = req["text"].encode()
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fchmod(fd, stat.S_IMODE(int(req.get("mode", 0o644))))
        finally:
            os.close(fd)
    elif op == "chmod":
        # the file itself, opened without following a symlink; chmod through /proc/self/fd changes that inode
        p = w["path"]
        fd = os.open(p.name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=p.dir)
        try:
            if stat.S_ISLNK(os.fstat(fd).st_mode):
                raise PermissionError(f"refusing to change the permissions of a symlink: {p.path}")
            os.chmod(f"/proc/self/fd/{fd}", stat.S_IMODE(int(req["mode"])))
        finally:
            os.close(fd)


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
