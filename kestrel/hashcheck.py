"""Checksum files (.sfv, .md5, .sha256, SHA256SUMS…): reading them, checking the files they list, and the Verify
Checksums dialog that opening one shows; making them (Create Checksum File…).

Formats: GNU coreutils' "hash  name", BSD tags "SHA256 (name) = hash", SFV's "name crc32", or a lone hash."""
import hashlib
import os
import re
import zlib
from collections import namedtuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
                             QRadioButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

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


def hash_files(path, algos, progress=None, check=None):
    """The file's hashes for several algorithms (lowercase hex, in `algos`' order), in one read of the file. On a
    thread: progress(bytes done, size) after each chunk, check() between them (it may raise to stop). Raises OSError
    if it can't be read."""
    hashes = [None if a == "crc32" else hashlib.new(a) for a in algos]
    crc, done = 0, 0
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        while True:
            if check:
                check()
            chunk = f.read(4 << 20)
            if not chunk:
                break
            for h in hashes:
                if h is None:
                    crc = zlib.crc32(chunk, crc)
                else:
                    h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, size)
    return [f"{crc:08x}" if h is None else h.hexdigest() for h in hashes]


def hash_file(path, algo, progress=None, check=None):
    """The file's hash (lowercase hex); see hash_files."""
    return hash_files(path, [algo], progress, check)[0]


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

        def job(t):
            try:
                return tuple(verify(e, t))
            except fileops.Cancelled:
                raise
            except Exception as err:   # as a result: run_job connects on_done before the thread starts
                return ("error", "", str(err))

        # no parent: closing the dialog mustn't destroy a thread that is still reading. The result comes through
        # on_done, not a connection made afterwards, which a quick file could finish before.
        self.task = fileops.run_job(None, f"Verifying {e.name}", job,
                                    lambda res: g == self.gen and self._on_result(res), cancellable=True, quiet=True)
        self.task.progress.connect(lambda f, _text: g == self.gen and self._progress(i, f))

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


# ---------------------------------------------------------------- making checksum files

CREATE_ALGORITHMS = ("crc32", "md5", "sha1", "sha256", "sha512", "blake2b")   # offered, in order
_OUT_EXT = {"crc32": ".sfv", "md5": ".md5", "sha1": ".sha1", "sha256": ".sha256", "sha512": ".sha512",
            "blake2b": ".b2"}


def _write_replacing(path, data):
    """Write `data` to a new file beside `path`, then rename it over `path` (never through a symlink there)."""
    fd, part = util.open_part(os.path.dirname(path).rstrip("/") + "/")
    try:
        with open(fd, "wb") as f:
            f.write(data)
        os.rename(part, path)
    except BaseException:
        os.unlink(part)
        raise


def files_in(paths, check=None):
    """The files to checksum: files as they are, folders' files inside them (not through symlinked folders), in
    order."""
    out = []
    for p in paths:
        if not os.path.isdir(p):
            out.append(p)
            continue
        for root, dirs, files in os.walk(p):
            if check:
                check()
            dirs.sort()
            out += [os.path.join(root, f) for f in sorted(files)]
    return out


def output_paths(dir_, stem, algos, one_file):
    """The checksum files a job writes in `dir_`: one per algorithm (stem.sha256, stem.md5, stem.sfv for CRC32…), or
    with one_file a single stem-CHECKSUM with every algorithm as BSD tags."""
    if one_file:
        return [os.path.join(dir_, stem + "-CHECKSUM")]
    return [os.path.join(dir_, stem + _OUT_EXT[a]) for a in algos]


def format_line(name, algo, hex_, style):
    """One line of a checksum file: "hash  name" (md5sum's), "SHA256 (name) = hash" (bsd) or "name CRC32" (sfv); names
    with a backslash or line break are escaped as md5sum does."""
    if style == "sfv":
        return f"{name} {hex_.upper()}"
    escape = "\\" in name or "\n" in name or "\r" in name
    n = name.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r") if escape else name
    prefix = "\\" if escape else ""
    if style == "bsd":
        return f"{prefix}{algo_label(algo)} ({n}) = {hex_}"
    return f"{prefix}{hex_}  {n}"


def create(task, paths, dir_, stem, algos, one_file, errors=None):
    """Hash every file once with all the algorithms and write output_paths(); names are relative to `dir_`. Files that
    can't be read are left out and named in `errors` (a list). Returns the files written. On a thread (task may be
    None)."""
    check = task.check if task else None
    if task:
        task.report(0, 0, "Listing files…")
    outputs = output_paths(dir_, stem, algos, one_file)
    skip = {os.path.normpath(o) for o in outputs}
    files = [f for f in files_in(paths, check) if os.path.normpath(f) not in skip]  # an older copy of an output
    if not files:
        raise RuntimeError("There are no files to make checksums of.")
    sizes = []
    for f in files:
        try:
            sizes.append(os.stat(f).st_size)
        except OSError:
            sizes.append(0)
    total, before = sum(sizes), 0
    hashed = []   # (relative name, hashes in algos' order)
    for f, size in zip(files, sizes):
        name = os.path.relpath(f, dir_)

        def progress(done, _size, f=f, before=before):
            if task:
                task.report(before + done, max(total, 1), "Hashing " + os.path.basename(f))
        try:
            hashed.append((name, hash_files(f, algos, progress, check)))
        except OSError as e:
            if errors is not None:
                errors.append(f"{name}: {e.strerror or e}")
        before += size
    if not hashed:
        raise RuntimeError("None of the files could be read.")
    if check:
        check()
    if one_file:
        text = "".join(format_line(name, a, h[i], "bsd") + "\n" for name, h in hashed for i, a in enumerate(algos))
        _write_replacing(outputs[0], text.encode())
    else:
        for i, a in enumerate(algos):
            style = "sfv" if a == "crc32" else "gnu"
            text = "; Made by Kestrel Explorer\n" if style == "sfv" else ""
            text += "".join(format_line(name, a, h[i], style) + "\n" for name, h in hashed)
            _write_replacing(outputs[i], text.encode())
    return outputs


class CreateDialog(QDialog):
    """Pick the algorithms, one file or one per algorithm, the name and the folder; settings remember the first
    two."""

    def __init__(self, parent, paths, settings):
        super().__init__(parent)
        self.paths, self.settings = paths, settings
        self.setWindowTitle("Create Checksum File")
        lay = QVBoxLayout(self)
        first = paths[0].rstrip("/") or paths[0]
        folders = any(os.path.isdir(p) for p in paths)
        head = QLabel((f"Checksums for “{os.path.basename(first)}”." if len(paths) == 1
                       else f"Checksums for {len(paths)} items.")
                      + (" The files inside folders are included." if folders else ""))
        head.setWordWrap(True)
        lay.addWidget(head)

        algo_box = QGroupBox("Algorithms")
        grid = QGridLayout(algo_box)
        saved = [a for a in str(settings.value("checksum_algorithms", "sha256")).split(",") if a]
        self.boxes = {}
        for i, a in enumerate(CREATE_ALGORITHMS):
            b = QCheckBox("CRC32 (SFV)" if a == "crc32" else algo_label(a))
            b.setChecked(a in saved)
            self.boxes[a] = b
            grid.addWidget(b, i // 3, i % 3)
            b.toggled.connect(self._update)
        lay.addWidget(algo_box)

        files_box = QGroupBox("Checksum files")
        fl = QVBoxLayout(files_box)
        self.each = QRadioButton("One file for each algorithm (as md5sum, sha256sum… write them)")
        self.single = QRadioButton("One file with every algorithm (BSD tags, like Fedora's CHECKSUM files)")
        (self.single if settings.value("checksum_one_file", False, type=bool) else self.each).setChecked(True)
        fl.addWidget(self.each)
        fl.addWidget(self.single)
        self.each.toggled.connect(self._update)
        lay.addWidget(files_box)

        form = QFormLayout()
        self.name = QLineEdit(os.path.basename(first) if len(paths) == 1
                              else os.path.basename(os.path.dirname(first)))
        if not self.name.text():
            self.name.setText("checksums")
        form.addRow("Name:", self.name)
        row = QHBoxLayout()
        self.folder = QLineEdit(os.path.dirname(first))
        browse = QPushButton("Browse…")
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        form.addRow("Save in:", row)
        lay.addLayout(form)
        self.name.textChanged.connect(self._update)
        self.folder.textChanged.connect(self._update)

        def choose():
            d = QFileDialog.getExistingDirectory(self, "Save Checksum File In", self.folder.text())
            if d:
                self.folder.setText(d)
        browse.clicked.connect(choose)

        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.preview)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.create_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        self.create_btn.setText("Create")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._update()

    def algos(self):
        return [a for a in CREATE_ALGORITHMS if self.boxes[a].isChecked()]

    def one_file(self):
        return self.single.isChecked()

    def dir(self):
        return self.folder.text().strip()

    def stem(self):
        return self.name.text().strip()

    def outputs(self):
        return output_paths(self.dir(), self.stem(), self.algos(), self.one_file())

    def _update(self):
        problem = ""
        if not self.algos():
            problem = "Choose at least one algorithm."
        elif not self.stem() or "/" in self.stem():
            problem = "Enter a name (without “/”)."
        elif not os.path.isdir(self.dir()):
            problem = "The folder to save in doesn't exist."
        self.create_btn.setEnabled(not problem)
        if problem:
            self.preview.setText(problem)
            return
        names = [os.path.basename(o) for o in self.outputs()]
        existing = [os.path.basename(o) for o in self.outputs() if os.path.lexists(o)]
        self.preview.setText("Creates: " + ", ".join(names)
                             + ("\nReplaces: " + ", ".join(existing) if existing else ""))

    def _ok(self):
        existing = [os.path.basename(o) for o in self.outputs() if os.path.lexists(o)]
        if existing and QMessageBox.question(self, "Create Checksum File", "Replace " + ", ".join(existing) + "?") \
                != QMessageBox.StandardButton.Yes:
            return
        self.settings.setValue("checksum_algorithms", ",".join(self.algos()))
        self.settings.setValue("checksum_one_file", self.one_file())
        self.accept()


def create_dialog(win, paths):
    """The context menu's Create Checksum File…: the dialog, then the job in the status bar; selects the result."""
    dlg = CreateDialog(win, paths, win.settings)
    if dlg.exec():
        run_create(win, paths, dlg.dir(), dlg.stem(), dlg.algos(), dlg.one_file())


def run_create(win, paths, dir_, stem, algos, one_file):
    def work(task):
        errors = []
        return create(task, paths, dir_, stem, algos, one_file, errors), errors

    def done(res):
        if res is None:  # cancelled
            win.statusBar().showMessage("Creating checksums cancelled", 4000)
            return
        written, errors = res
        win.statusBar().showMessage("Created " + ", ".join(os.path.basename(o) for o in written), 6000)
        if win.pane() and win.pane().dir == dir_ and written:
            win.pane().select_later(written[0])
        if errors:
            shown = errors[:10] + ([f"…and {len(errors) - 10} more"] if len(errors) > 10 else [])
            head = ("1 file couldn't be read and was left out:" if len(errors) == 1
                    else f"{len(errors)} files couldn't be read and were left out:")
            QMessageBox.warning(win, "Create Checksum File", head + "\n\n" + "\n".join(shown))
    fileops.run_job(win, "Creating checksums", work, done)
