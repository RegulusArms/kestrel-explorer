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
import secrets
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

AT_FDCWD, RENAME_NOREPLACE, RENAME_EXCHANGE = -100, 1, 2
_libc = ctypes.CDLL(None, use_errno=True)

_out_lock = threading.Lock()
_cancelled = set()
_closing = threading.Event()   # the session has ended: everything counts as cancelled
_busy = threading.Event()      # a job is running


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


class Owner:
    """Who owns what the helper makes. A move keeps each item's owner. Anything new (a copy, a new folder, file or
    link) gets the owner of the folder it's made in: the user's in their home, root's in /etc. Made as root, it would
    otherwise be root's, and often of no use to the user who asked for it."""

    def __init__(self, keep=False, uid=0, gid=0):
        self.keep = keep  # each item's own (a move)
        self.uid, self.gid = uid, gid


def _owner_of(dirfd):
    """The owner for something new in this folder."""
    try:
        st = os.fstat(dirfd)
        return Owner(uid=st.st_uid, gid=st.st_gid)
    except OSError:
        return Owner(uid=os.geteuid(), gid=os.getegid())


def _own_new(dirfd, name):
    """Give the new `name` in dirfd (never followed if it's a symlink) the owner for that folder."""
    o = _owner_of(dirfd)
    try:
        os.chown(name, o.uid, o.gid, dir_fd=dirfd, follow_symlinks=False)
    except OSError:
        pass


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
                        _own_new(dirfd, c)
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


def _part_name():
    """A hidden name for building something beside the name it will replace (see Job.install)."""
    return f".kes-{secrets.randbits(32):08x}.part"


def _open_part(dirfd):
    """A new, empty file under a fresh _part_name() in dirfd (created exclusively, never through a symlink).
    Returns (fd, name)."""
    while True:
        name = _part_name()
        try:
            return os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                           dir_fd=dirfd), name
        except FileExistsError:
            continue


def _renameat2(src_dir, src, dst_dir, dst, flags):
    """renameat2(); 0 or the errno."""
    if _libc.renameat2(src_dir, os.fsencode(src), dst_dir, os.fsencode(dst), flags) == 0:
        return 0
    return ctypes.get_errno()


def _put_new(w, part):
    """A finished part file (in w's folder) given w's name, which must still be free: never replaces anything."""
    e = _renameat2(w.dir, part, w.dir, w.name, RENAME_NOREPLACE)
    if e == errno.EEXIST:
        raise FileExistsError(f"{w.name} already exists")
    if e == errno.EINVAL:   # a filesystem without RENAME_NOREPLACE: a hard link fails if the name is taken
        os.link(part, w.name, src_dir_fd=w.dir, dst_dir_fd=w.dir)
        os.unlink(part, dir_fd=w.dir)
    elif e:
        raise OSError(e, os.strerror(e))


def _copy_stat_fd(fd, st, owner):
    """The permissions, times and owner of an open file or folder (on a descriptor: never through a symlink)."""
    mode = stat.S_IMODE(st.st_mode)
    uid = st.st_uid if owner.keep else owner.uid
    gid = st.st_gid if owner.keep else owner.gid
    try:
        os.utime(fd, ns=(st.st_atime_ns, st.st_mtime_ns))
    except OSError:
        pass
    try:
        os.chown(fd, uid, gid)  # first: chown clears set-user-ID, which chmod then puts back
    except OSError:
        pass
    if stat.S_ISREG(st.st_mode):  # a copy of someone else's set-user/group-ID program mustn't run as its new owner
        if st.st_uid != uid:
            mode &= ~stat.S_ISUID
        if st.st_gid != gid:
            mode &= ~stat.S_ISGID
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
        if _closing.is_set() or self.id in _cancelled:
            raise Cancelled()

    def report(self, text, force=False):
        now = time.monotonic()
        if force or now - self._last >= 0.1:
            self._last = now
            send({"id": self.id, "progress": [self.done, self.total, text]})

    def count(self, dirfd, name, nbytes=False):
        """How many items a tree has, for progress, and with nbytes its files' sizes too (only reads; never follows
        a symlink)."""
        st = _stat_at(dirfd, name)
        n = 1 + (st.st_size if nbytes and st is not None and stat.S_ISREG(st.st_mode) else 0)
        if st is not None and stat.S_ISDIR(st.st_mode):
            self.check()
            try:
                d = _open_dir_at(dirfd, name, st)
            except (OSError, Failure):
                return n
            try:
                for e in os.listdir(d):
                    n += self.count(d, e, nbytes)
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

    def _remove_tree(self, dirfd, name, st, dev, counted=True):
        if stat.S_ISDIR(st.st_mode):
            if st.st_dev != dev:
                raise Failure(f"{name} is on another drive (a mount point); not deleting it")
            d = _open_dir_at(dirfd, name, st)
            try:
                for e in os.listdir(d):
                    if counted:
                        self.check()
                    self._remove_tree(d, e, os.stat(e, dir_fd=d, follow_symlinks=False), dev, counted)
                    if counted:
                        self.done += 1
                        self.report(e)
            finally:
                os.close(d)
            os.rmdir(name, dir_fd=dirfd)
        else:  # a file or a symlink: the name itself
            os.unlink(name, dir_fd=dirfd)

    def copy(self, src_dir, src, dst_dir, dst, merge, owner, staged=False):
        """Copy src (in src_dir) to dst (in dst_dir). Whatever is made is built under a hidden _part_name() beside
        dst and put in its place only when complete (install), so a failure, a cancel or the session ending leaves
        dst as it was and no half-made copy. Only a merge writes into an existing folder (each file in it still
        replaced whole). staged: dst is inside a folder being built, where nothing exists yet."""
        st = os.stat(src, dir_fd=src_dir, follow_symlinks=False)
        dst_st = None if staged else _stat_at(dst_dir, dst)
        if dst_st is not None and stat.S_ISDIR(dst_st.st_mode) and not stat.S_ISDIR(st.st_mode):
            raise Failure(f"{dst} is a folder")  # a file or link never replaces a whole folder
        if stat.S_ISDIR(st.st_mode) and merge and dst_st is not None:
            if not stat.S_ISDIR(dst_st.st_mode):
                raise Failure(f"{dst} exists and isn't a folder")
            src_fd = _open_dir_at(src_dir, src, st)
            try:
                dst_fd = _open_dir_at(dst_dir, dst, dst_st)
                try:
                    for e in os.listdir(src_fd):
                        self.copy(src_fd, e, dst_fd, e, merge, owner)
                    # a copy leaves the folder's owner as it was
                    _copy_stat_fd(dst_fd, st, Owner(owner.keep, dst_st.st_uid, dst_st.st_gid))
                finally:
                    os.close(dst_fd)
            finally:
                os.close(src_fd)
            self.done += 1
            self.report(src)
            return
        part, made = (dst if staged else _part_name()), False
        try:
            if stat.S_ISLNK(st.st_mode):
                target = os.readlink(src, dir_fd=src_dir)
                os.symlink(target, part, dir_fd=dst_dir)
                made = True
                try:
                    os.utime(part, ns=(st.st_atime_ns, st.st_mtime_ns), dir_fd=dst_dir, follow_symlinks=False)
                    os.chown(part, st.st_uid if owner.keep else owner.uid, st.st_gid if owner.keep else owner.gid,
                             dir_fd=dst_dir, follow_symlinks=False)
                except OSError:
                    pass
            elif stat.S_ISDIR(st.st_mode):
                src_fd = _open_dir_at(src_dir, src, st)
                try:
                    os.mkdir(part, 0o700, dir_fd=dst_dir)
                    made = True
                    dst_fd = _open_dir_at(dst_dir, part, os.stat(part, dir_fd=dst_dir, follow_symlinks=False))
                    try:
                        for e in os.listdir(src_fd):
                            self.copy(src_fd, e, dst_fd, e, False, owner, staged=True)
                        _copy_stat_fd(dst_fd, st, owner)
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
                    if staged:
                        out_fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                         0o600, dir_fd=dst_dir)
                    else:
                        out_fd, part = _open_part(dst_dir)
                    made = True
                    try:
                        self.copy_bytes(in_fd, out_fd, src)
                        _copy_stat_fd(out_fd, st, owner)
                        if dst_st is not None:   # replacing something: on disk before the rename
                            os.fsync(out_fd)
                    finally:
                        os.close(out_fd)
                finally:
                    os.close(in_fd)
            else:
                raise Failure(f"{src} isn't a regular file, folder or link")
            if not staged:
                self.install(dst_dir, part, dst)
        except BaseException:
            if made and not staged:
                self.discard(dst_dir, part)
            raise
        self.done += 1
        self.report(src)

    def copy_bytes(self, in_fd, out_fd, label):
        """in_fd → out_fd, checking for a cancel and reporting the bytes as it goes."""
        while True:
            self.check()
            chunk = os.read(in_fd, 1 << 20)
            if not chunk:
                return
            view = memoryview(chunk)
            while view:
                view = view[os.write(out_fd, view):]
            self.done += len(chunk)
            self.report(label)

    def install(self, dirfd, part, name):
        """Put the finished `part` in place of `name` (both in dirfd). A non-folder over a non-folder (or nothing)
        is one rename. Where a folder is involved, the two are swapped in one step (RENAME_EXCHANGE; on a filesystem
        without it, the old one is renamed aside first and put back if the second rename fails), and then the old
        one, now under the part name, is deleted."""
        self.swap_in(dirfd, part, dirfd, name, lambda d, old: self.remove_old(d, old, name))

    def swap_in(self, src_dir, src, dst_dir, dst, dispose):
        """src (in src_dir) to dst (in dst_dir) on the same filesystem, replacing what's at dst as install() does;
        the old one is passed to dispose (as a name in src_dir) once it's out of the way."""
        old = _stat_at(dst_dir, dst)
        new = os.stat(src, dir_fd=src_dir, follow_symlinks=False)
        if old is None or (not stat.S_ISDIR(old.st_mode) and not stat.S_ISDIR(new.st_mode)):
            os.rename(src, dst, src_dir_fd=src_dir, dst_dir_fd=dst_dir)
            return
        e = _renameat2(src_dir, src, dst_dir, dst, RENAME_EXCHANGE)
        if e == 0:
            dispose(src_dir, src)   # the old one, now where the new one was
            return
        if e != errno.EINVAL:
            raise OSError(e, os.strerror(e))
        aside = _part_name()
        os.rename(dst, aside, src_dir_fd=dst_dir, dst_dir_fd=src_dir)
        try:
            os.rename(src, dst, src_dir_fd=src_dir, dst_dir_fd=dst_dir)
        except OSError:
            try:
                os.rename(aside, dst, src_dir_fd=src_dir, dst_dir_fd=dst_dir)
            except OSError:
                pass
            raise
        dispose(src_dir, aside)

    def remove_old(self, dirfd, old, shown):
        """The replaced one, once the new one is in place: deleted whole (a cancel now would only leave it
        half-deleted)."""
        st = _stat_at(dirfd, old)
        if st is None:
            return
        try:
            self._remove_tree(dirfd, old, st, st.st_dev, counted=False)
        except (OSError, Failure) as e:
            raise Failure(f"{shown} was replaced, but the old one couldn't be deleted (it's left as {old}): "
                          f"{getattr(e, 'strerror', None) or e}") from None

    def discard(self, dirfd, part):
        """A half-made part after a failure or cancel: deleted, without letting a second failure hide the first."""
        try:
            st = _stat_at(dirfd, part)
            if st is not None:
                self._remove_tree(dirfd, part, st, st.st_dev, counted=False)
        except (OSError, Failure):
            pass

    def move(self, src, dst, merge):
        st = _stat_at(src.dir, src.name)
        if not merge and st is not None and st.st_dev == os.fstat(dst.dir).st_dev:
            existing = _stat_at(dst.dir, dst.name)
            if existing is not None:
                # replaced in one step (see swap_in); the old one is deleted once the moved one is in place
                if stat.S_ISDIR(existing.st_mode) and not stat.S_ISDIR(st.st_mode):
                    raise Failure(f"{dst.name} is a folder")
                self.swap_in(src.dir, src.name, dst.dir, dst.name, lambda d, old: self.remove_old(d, old, dst.name))
                self.done = self.total
                return
            # no-replace: something that appeared there since isn't silently replaced
            e = _renameat2(src.dir, src.name, dst.dir, dst.name, RENAME_NOREPLACE)
            if e == errno.EINVAL:  # a filesystem without RENAME_NOREPLACE
                try:
                    os.rename(src.name, dst.name, src_dir_fd=src.dir, dst_dir_fd=dst.dir)
                    e = 0
                except OSError:
                    pass
            if e == 0:
                self.done = self.total
                return
            if e == errno.EEXIST:
                raise Failure(f"{dst.name} appeared while it was being replaced")
        self.copy(src.dir, src.name, dst.dir, dst.name, merge, Owner(keep=True))
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
        merge = bool(req.get("merge"))
        # The destination may be the source itself: the same path, a hard link to it, or the same name in another
        # case on a drive that ignores case. Replacing it first would delete the source, so: moving a path onto
        # itself does nothing, moving onto another name for it just renames, and anything else is refused.
        src_st, dst_st = _stat_at(src.dir, src.name), _stat_at(dst.dir, dst.name)
        if src_st is not None and dst_st is not None and _same_file(src_st, dst_st):
            if op == "copy" or merge:
                raise Failure(f"can't {op} {src.path} onto itself")
            if src.path != dst.path:
                os.rename(src.name, dst.name, src_dir_fd=src.dir, dst_dir_fd=dst.dir)
            return
        if src_st is not None and stat.S_ISDIR(src_st.st_mode) and dst.path.startswith(src.path + "/"):
            raise Failure(f"can't {op} {src.path} into itself")
        # progress: every item, and the bytes copied; a move then deletes the items
        job.total = job.count(src.dir, src.name, True) + (job.count(src.dir, src.name) if op == "move" else 0)
        if op == "copy":
            job.copy(src.dir, src.name, dst.dir, dst.name, merge, _owner_of(dst.dir))
        else:
            job.move(src, dst, merge)
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
        _own_new(w["path"].dir, w["path"].name)
    elif op == "touch":
        os.close(_open_new_file(w["path"]))
        _own_new(w["path"].dir, w["path"].name)
    elif op == "copyfile":
        src, dst = w["src"], w["dst"]
        if _stat_at(dst.dir, dst.name) is not None:
            raise FileExistsError(f"{dst.name} already exists")
        in_fd = os.open(src.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=src.dir)
        try:
            out_fd, part = _open_part(dst.dir)
            try:
                try:
                    job.copy_bytes(in_fd, out_fd, src.path)
                    o = _owner_of(dst.dir)
                    try:
                        os.fchown(out_fd, o.uid, o.gid)
                    except OSError:
                        pass
                    os.fchmod(out_fd, 0o644)
                finally:
                    os.close(out_fd)
                _put_new(dst, part)
            except BaseException:
                os.unlink(part, dir_fd=dst.dir)
                raise
        finally:
            os.close(in_fd)
    elif op == "symlink":
        os.symlink(req["target"], w["link"].name, dir_fd=w["link"].dir)
        _own_new(w["link"].dir, w["link"].name)
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
        p = w["path"]
        if _stat_at(p.dir, p.name) is not None:
            raise FileExistsError(f"{p.name} already exists")
        fd, part = _open_part(p.dir)
        try:
            try:
                o = _owner_of(p.dir)
                try:
                    os.fchown(fd, o.uid, o.gid)  # before the chmod: chown clears set-user-ID
                except OSError:
                    pass
                view = memoryview(req["text"].encode())
                while view:
                    view = view[os.write(fd, view):]
                os.fchmod(fd, stat.S_IMODE(int(req.get("mode", 0o644))))
            finally:
                os.close(fd)
            _put_new(p, part)
        except BaseException:
            os.unlink(part, dir_fd=p.dir)
            raise
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
        if _closing.is_set():
            continue
        _busy.set()
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
            _busy.clear()


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
    # stdin closed: the session is over. A running job is cancelled, and given time to remove what it had half-made
    # (its part files) before the helper exits.
    _closing.set()
    for _ in range(300):
        if not _busy.is_set():
            break
        time.sleep(0.1)
    os._exit(0)


if __name__ == "__main__":
    main()
