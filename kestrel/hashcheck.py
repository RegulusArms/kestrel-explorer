"""Checksum files (.sfv, .md5, .sha256, SHA256SUMS…): reading them, checking the files they list, and the Verify
Checksums dialog that opening one shows.

Formats: GNU coreutils' "hash  name", BSD tags "SHA256 (name) = hash", SFV's "name crc32", or a lone hash."""
import hashlib
import os
import re
import zlib
from collections import namedtuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QHeaderView, QLabel, QProgressBar, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout)

from . import fileops, util

# One file a checksum file lists: its path (resolved against the checksum file's folder), the hash it should have
# (lowercase hex) and the algorithm: "crc32", "md5", "sha1", "sha224", "sha256", "sha384", "sha512" or "blake2b".
Entry = namedtuple("Entry", "path name expected algo")
# Each listed file's result: "ok", "failed", "missing" or "error" (with the message).
Result = namedtuple("Result", "status actual error")

# hex digits per algorithm
_DIGEST_LEN = {"crc32": 8, "md5": 32, "sha1": 40, "sha224": 56, "sha256": 64, "sha384": 96, "sha512": 128,
               "blake2b": 128}
# a hash's algorithm from its length, when nothing else says (BLAKE2b needs its name: it's as long as SHA-512)
_BY_LEN = {8: "crc32", 32: "md5", 40: "sha1", 56: "sha224", 64: "sha256", 96: "sha384", 128: "sha512"}
# BSD tags, lower-cased without "-"
_TAGS = {"crc32": "crc32", "md5": "md5", "sha1": "sha1", "sha224": "sha224", "sha256": "sha256", "sha384": "sha384",
         "sha512": "sha512", "blake2b": "blake2b", "blake2b512": "blake2b"}
_EXTS = {".sfv": "crc32", ".md5": "md5", ".md5sum": "md5", ".sha1": "sha1", ".sha1sum": "sha1", ".sha224": "sha224",
         ".sha224sum": "sha224", ".sha256": "sha256", ".sha256sum": "sha256", ".sha384": "sha384",
         ".sha384sum": "sha384", ".sha512": "sha512", ".sha512sum": "sha512", ".b2": "blake2b", ".b2sum": "blake2b"}
_NAMES = {"md5sums": "md5", "sha1sums": "sha1", "sha224sums": "sha224", "sha256sums": "sha256",
          "sha384sums": "sha384", "sha512sums": "sha512", "b2sums": "blake2b"}

_SFV_RE = re.compile(r"^(.*\S)\s+([0-9A-Fa-f]{8})$")
_BSD_RE = re.compile(r"^(\\?)([A-Za-z0-9-]+) ?\((.*)\) ?= ?([0-9A-Fa-f]+)$")
_GNU_RE = re.compile(r"^(\\?)([0-9A-Fa-f]+) [ *]?(.+)$")
_HEX_RE = re.compile(r"^[0-9A-Fa-f]+$")

FILTER = ("Checksum files (*.sfv *.md5 *.sha1 *.sha224 *.sha256 *.sha384 *.sha512 *.b2 *.md5sum *.sha1sum *.sha256sum "
          "*.sha512sum *SUMS *sums *CHECKSUM *CHECKSUMS *checksum *checksums);;All files (*)")


def _hint(path):
    """The algorithm a checksum file's name suggests, "" if none."""
    return _NAMES.get(os.path.basename(path).lower(), _EXTS.get(util.ext_of(path), ""))


def _algo_for(hex_, hint):
    if hint and _DIGEST_LEN[hint] == len(hex_):
        return hint
    return _BY_LEN.get(len(hex_), "")


def _unescape(s):
    """GNU's escaping of names holding a backslash or a line break ("\\" before the hash)."""
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            i += 1
            out.append({"n": "\n", "r": "\r"}.get(s[i], s[i]))
        else:
            out.append(s[i])
        i += 1
    return "".join(out)


def is_hash_file(path):
    """By its name: .sfv, .md5, .sha256, …, SHA256SUMS, …-CHECKSUM."""
    name = os.path.basename(path).lower()
    return bool(_hint(path)) or name in ("checksum", "checksums") or name.endswith(("-checksum", "-checksums"))


def parse_text(text, path):
    """The entries in a checksum file's text, in its order; lines that aren't checksums (comments, a PGP signature)
    are skipped. A file holding only a hash is for the file of the same name without the extension
    (disk.iso.sha256 → disk.iso)."""
    h, base = _hint(path), os.path.dirname(path)
    entries, lines = [], []

    def add(name, hex_, algo):
        entries.append(Entry(os.path.normpath(os.path.join(base, name)), name, hex_.lower(), algo))

    for line in text.split("\n"):
        s = line.strip()
        if not s or s[0] in "#;":
            continue
        lines.append(s)
        if h == "crc32":
            m = _SFV_RE.match(s)
            if m:
                add(m.group(1), m.group(2), "crc32")
            continue
        m = _BSD_RE.match(s)
        if m:
            algo = _TAGS.get(m.group(2).lower().replace("-", ""), "")
            if algo and _DIGEST_LEN[algo] == len(m.group(4)):
                add(_unescape(m.group(3)) if m.group(1) else m.group(3), m.group(4), algo)
            continue
        m = _GNU_RE.match(s)
        if m:
            algo = _algo_for(m.group(2), h)
            if algo:
                add(_unescape(m.group(3)) if m.group(1) else m.group(3), m.group(2), algo)
    # only a hash: for the file named like this one without its extension
    ext = os.path.splitext(path)[1]
    if not entries and len(lines) == 1 and _HEX_RE.match(lines[0]) and ext:
        algo = _algo_for(lines[0], h)
        if algo:
            add(os.path.basename(path[:-len(ext)]), lines[0], algo)
    return entries


def parse(path):
    """The entries in a checksum file (see parse_text); none if it can't be read or is too big to be one."""
    try:
        if os.path.getsize(path) > 16 << 20:
            return []
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return []
    return parse_text(data.decode("utf-8", errors="replace"), path)


def find(entries, file):
    """The entry for `file`: by its path, else by its name; None if it isn't listed."""
    for e in entries:
        if e.path == file:
            return e
    for e in entries:
        if os.path.basename(e.path) == os.path.basename(file):
            return e
    return None


def algo_label(algo):
    """"SHA256", "CRC32", "BLAKE2b"."""
    return "BLAKE2b" if algo == "blake2b" else algo.upper()


def hash_file(path, algo, progress=None, check=None):
    """The file's hash (lowercase hex). On a thread: progress(bytes done, size) after each chunk, check() between them
    (it may raise to stop). Raises OSError if it can't be read."""
    crc, h = 0, None if algo == "crc32" else hashlib.new(algo)
    done = 0
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        while True:
            if check:
                check()
            chunk = f.read(4 << 20)
            if not chunk:
                break
            if h is None:
                crc = zlib.crc32(chunk, crc)
            else:
                h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, size)
    return f"{crc:08x}" if h is None else h.hexdigest()


def verify(e, task=None):
    if not os.path.lexists(e.path):
        return Result("missing", "", "")
    try:
        actual = hash_file(e.path, e.algo,
                           (lambda done, size: task.report(done, size, f"Verifying {e.name}")) if task else None,
                           task.check if task else None)
    except OSError as err:
        return Result("error", "", err.strerror or str(err))
    return Result("ok" if actual == e.expected else "failed", actual, "")


class VerifyDialog(QDialog):
    """Shows the checksum file's entries and checks them one after another, with progress; Verify Again, Stop,
    Close."""

    def __init__(self, parent, path, entries):
        super().__init__(parent)
        self.path, self.entries = path, entries
        self.results = []   # in entries' order; empty status while not checked yet
        self.sizes, self.total, self.before = [], 0, 0   # bytes in all files; in the files already checked
        self.next, self.gen, self.task = 0, 0, None      # gen: which run a task's result belongs to
        self.setWindowTitle("Verify Checksums — " + os.path.basename(path))
        self.resize(640, 400)
        lay = QVBoxLayout(self)
        head = QLabel(f"Checking the files listed in “{os.path.basename(path)}”.")
        head.setWordWrap(True)
        lay.addWidget(head)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File", "Algorithm", "Result"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        for e in entries:
            it = QTreeWidgetItem(self.tree, [e.name, algo_label(e.algo), ""])
            it.setToolTip(0, e.path)
        lay.addWidget(self.tree, 1)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setAccessibleName("Progress")
        lay.addWidget(self.bar)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.status)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.again = bb.addButton("Verify Again", QDialogButtonBox.ButtonRole.ActionRole)
        self.stop_btn = bb.addButton("Stop", QDialogButtonBox.ButtonRole.ActionRole)
        self.again.clicked.connect(self._start)
        self.stop_btn.clicked.connect(lambda: (self._stop(), self._summary()))
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._start()

    def complete(self):
        """Every file checked."""
        return self.next >= len(self.entries) and self.task is None

    def hideEvent(self, ev):
        self._stop()   # closed: nothing more to read
        super().hideEvent(ev)

    def _start(self):
        self._stop()
        self.results = [Result("", "", "")] * len(self.entries)
        self.sizes = []
        for e in self.entries:
            try:
                self.sizes.append(os.stat(e.path).st_size)
            except OSError:
                self.sizes.append(0)
        self.total, self.before = sum(self.sizes), 0
        for i in range(len(self.entries)):
            self._show_row(i)
        self.next = 0
        self.bar.setValue(0)
        self._step()

    def _step(self):
        self.again.setEnabled(False)
        self.stop_btn.setEnabled(True)
        if self.next >= len(self.entries):
            self._summary()
            return
        i, g = self.next, self.gen
        it = self.tree.topLevelItem(i)
        it.setText(2, "Checking…")
        self.tree.scrollToItem(it)
        self.status.setStyleSheet("")
        self.status.setText(f"Checking {i + 1} of {len(self.entries)}: {self.entries[i].name}")
        e = self.entries[i]
        # no parent: closing the dialog mustn't destroy a thread that is still reading
        self.task = fileops.run_job(None, f"Verifying {e.name}", lambda t: tuple(verify(e, t)), None,
                                    cancellable=True, quiet=True)
        self.task.progress.connect(lambda f, _text: g == self.gen and self._progress(i, f))
        self.task.result.connect(lambda res: g == self.gen and self._on_result(res))
        self.task.error.connect(lambda msg: g == self.gen and self._on_result(("error", "", msg)))

    def _progress(self, i, f):
        here = self.before + int(max(f, 0.0) * self.sizes[i])
        self.bar.setValue(1000 * here // self.total if self.total else 1000 * i // len(self.entries))

    def _on_result(self, res):
        self.task = None
        if res is None:   # stopped
            return
        self.results[self.next] = Result(*res)
        self._show_row(self.next)
        self.before += self.sizes[self.next]
        self.next += 1
        self.bar.setValue(1000 * self.before // self.total if self.total else 1000 * self.next // len(self.entries))
        self._step()

    def _stop(self):
        self.gen += 1   # results still on their way are for the old run
        if self.task is not None:
            self.task.cancel()
        self.task = None

    def _show_row(self, i):
        it = self.tree.topLevelItem(i)
        r, e = self.results[i], self.entries[i]
        text = tip = ""
        if r.status == "ok":
            text = "✔ OK"
        elif r.status == "failed":
            text = "✘ Doesn't match"
            tip = f"Expected: {e.expected}\nActual: {r.actual}"
        elif r.status == "missing":
            text = "✘ Missing"
            tip = "Not found: " + e.path
        elif r.status == "error":
            text = "✘ " + r.error
            tip = r.error
        it.setText(2, text)
        it.setToolTip(2, tip)
        brush = QBrush() if not r.status else QBrush(util.ok_color() if r.status == "ok" else util.error_color())
        for c in range(3):
            it.setForeground(c, brush if c == 2 else QBrush())

    def _summary(self):
        self.again.setEnabled(True)
        self.stop_btn.setEnabled(False)
        statuses = [r.status for r in self.results]
        checked = sum(1 for s in statuses if s)
        ok, failed = statuses.count("ok"), statuses.count("failed")
        missing, errors = statuses.count("missing"), statuses.count("error")
        parts = []
        if failed:
            parts.append(f"{failed} doesn't match" if failed == 1 else f"{failed} don't match")
        if missing:
            parts.append(f"{missing} missing")
        if errors:
            parts.append(f"{errors} couldn't be read")
        if ok:
            parts.append(f"{ok} OK")
        n = len(self.entries)
        if checked < n:
            text = f"Stopped after {checked} of {n} files" + (": " + ", ".join(parts) + "." if parts else ".")
        elif ok == n:
            text = "✔ The file is OK." if n == 1 else f"✔ All {ok} files are OK."
        else:
            text = "✘ " + ", ".join(parts) + "."
        good = checked == n and ok == n
        bad = failed or missing or errors
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {util.ok_color().name()}" if good
                                  else f"color: {util.error_color().name()}" if bad else "")


def open_dialog(parent, path):
    """Opening a checksum file: the Verify Checksums dialog, or False if it lists nothing (open it as a text file)."""
    entries = parse(path)
    if not entries:
        return False
    d = VerifyDialog(parent, path, entries)
    d.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    d.show()
    d.raise_()
    d.activateWindow()
    return True
