"""Compress / Extract dialogs and the flows that run archive jobs (see archive.py) in the status bar."""
import os
import shlex
import shutil

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
                             QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSlider,
                             QSpinBox, QVBoxLayout, QWidget)

from . import archive, fileops, util

_ARGV_NOTE = ("{tool} only accepts the password on its command line, so other users on this computer could "
              "see it in the process list while the job runs.")


def _small(text):
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    f = lbl.font()
    f.setPointSizeF(f.pointSizeF() * 0.9)
    lbl.setFont(f)
    lbl.setStyleSheet("color: palette(placeholder-text)")
    return lbl


def _password_row(edit):
    """Password field with a "Show" toggle."""
    edit.setEchoMode(QLineEdit.EchoMode.Password)
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(edit, 1)
    show = QCheckBox("Show")
    show.toggled.connect(lambda on: edit.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
    row.addWidget(show)
    return row, show


def ask_password(parent, path, wrong=False):
    """Password for an encrypted archive, or None if cancelled."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Password Required")
    lay = QVBoxLayout(dlg)
    msg = (f"<b>Wrong password</b> for “{os.path.basename(path)}”. Try again:" if wrong else
           f"“{os.path.basename(path)}” is encrypted. Enter its password:")
    lbl = QLabel(msg)
    lbl.setTextFormat(Qt.TextFormat.RichText if wrong else Qt.TextFormat.PlainText)
    lbl.setWordWrap(True)
    lay.addWidget(lbl)
    edit = QLineEdit()
    row, _ = _password_row(edit)
    lay.addLayout(row)
    if archive.extract_tool(path) == "zpaq":  # the only one given the password on its command line
        lay.addWidget(_small(_ARGV_NOTE.format(tool=archive.extract_tool(path))))
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    bb.button(QDialogButtonBox.StandardButton.Ok).setText("Extract")
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    lay.addWidget(bb)
    dlg.resize(420, dlg.sizeHint().height())
    edit.setFocus()
    return edit.text() if dlg.exec() and edit.text() else None


# ---------------------------------------------------------------- compress

class CompressDialog(QDialog):
    """Pick a format, tool and every option it supports; spec() describes the job for archive.compress()."""

    def __init__(self, parent, paths, settings):
        super().__init__(parent)
        self.setWindowTitle("Compress")
        self.paths, self.s = paths, settings
        self.base = os.path.commonpath([os.path.dirname(p) for p in paths])
        self.rels = [os.path.relpath(p, self.base) for p in paths]
        self.one_file = len(paths) == 1 and os.path.isfile(paths[0]) and not os.path.islink(paths[0])
        self.fmts = archive.formats()
        lay = QVBoxLayout(self)
        form = QFormLayout()
        lay.addLayout(form)

        default = os.path.basename(paths[0].rstrip("/")) if len(paths) == 1 else \
            (os.path.basename(self.base) or "Archive")
        self.name = QLineEdit(default)
        self.ext = QLabel()
        row = QHBoxLayout()
        row.addWidget(self.name, 1)
        row.addWidget(self.ext)
        form.addRow("Archive name:", row)
        self.dest = QLineEdit(self.base)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.dest, 1)
        row.addWidget(browse)
        form.addRow("Location:", row)

        self.fmt = QComboBox()
        for f in self.fmts:
            usable = bool(f["available"]) and (not f.get("single") or self.one_file)
            text = f["label"] if f["available"] else f"{f['label']} — install {archive.install_hint(f['tools'][0])}"
            self.fmt.addItem(text, f["id"])
            if not usable:
                self.fmt.model().item(self.fmt.count() - 1).setEnabled(False)
        form.addRow("Format:", self.fmt)
        self.tool = QComboBox()
        self.tool_row = self._row(form, "Program:", self.tool)

        self.level = QSlider(Qt.Orientation.Horizontal)
        self.level_lbl = QLabel()
        self.level_lbl.setMinimumWidth(90)
        row = QHBoxLayout()
        row.addWidget(self.level, 1)
        row.addWidget(self.level_lbl)
        self.level_row = self._row(form, "Compression level:", row)
        self.level_hint = _small("")
        form.addRow(self.level_hint)
        self.method = QComboBox()
        self.method_row = self._row(form, "Method:", self.method)
        self.threads = QSpinBox()
        self.threads.setRange(0, archive.cpu_count() * 2)
        self.threads.setSpecialValueText(f"Auto ({archive.cpu_count()} cores)")
        self.threads_row = self._row(form, "CPU threads:", self.threads)

        # encryption
        self.enc = QGroupBox("Encrypt with a password")
        self.enc.setCheckable(True)
        self.enc.setChecked(False)
        ef = QFormLayout(self.enc)
        self.pw, self.pw2 = QLineEdit(), QLineEdit()
        row, show = _password_row(self.pw)
        ef.addRow("Password:", row)
        self.pw2.setEchoMode(QLineEdit.EchoMode.Password)
        show.toggled.connect(lambda on: self.pw2.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        ef.addRow("Confirm:", self.pw2)
        self.enc_names = QCheckBox("Also encrypt file names (nothing can be listed without the password)")
        ef.addRow(self.enc_names)
        self.zip_enc = QComboBox()
        self.zip_enc.addItem("AES-256 (secure; needs 7-Zip, WinRAR or a recent unzip)", "AES256")
        self.zip_enc.addItem("ZipCrypto (weak, but opens everywhere)", "ZipCrypto")
        self.zip_enc_row = QLabel("Zip encryption:")
        ef.addRow(self.zip_enc_row, self.zip_enc)
        self.enc_note = _small("")
        ef.addRow(self.enc_note)
        lay.addWidget(self.enc)
        self.no_enc = _small("")
        lay.addWidget(self.no_enc)

        # splitting and archive structure
        self.split = QGroupBox("Split into volumes")
        self.split.setCheckable(True)
        self.split.setChecked(False)
        sl = QHBoxLayout(self.split)
        self.vol = QSpinBox()
        self.vol.setRange(1, 1_000_000)
        self.vol.setValue(4000)
        self.vol_unit = QComboBox()
        self.vol_unit.addItems(["MB", "GB"])
        sl.addWidget(QLabel("Volume size:"))
        sl.addWidget(self.vol)
        sl.addWidget(self.vol_unit)
        sl.addStretch(1)
        lay.addWidget(self.split)
        opts = QFormLayout()
        lay.addLayout(opts)
        self.solid = QCheckBox("Solid archive (smaller, but slower to extract single files)")
        opts.addRow(self.solid)
        self.recovery = QSpinBox()
        self.recovery.setRange(0, 10)
        self.recovery.setSuffix(" %")
        self.recovery.setSpecialValueText("None")
        self.recovery_row = self._row(opts, "Recovery record:", self.recovery)
        self.extra = QLineEdit()
        self.extra.setPlaceholderText("Extra options passed to the program, e.g. -mfb=273 or --long=27")
        opts.addRow("Extra options:", self.extra)
        self.trash_after = QCheckBox("Move the original files to the trash afterwards")
        opts.addRow(self.trash_after)

        lay.addWidget(QLabel("Command:"))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(64)
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.preview.setFont(mono)
        lay.addWidget(self.preview)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Compress")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        self._loading = True
        last = self.s.value("archive/format", "7z" if archive.tool("7z") else "tar.gz")
        i = self.fmt.findData(last)
        if i < 0 or not self.fmt.model().item(i).isEnabled():
            i = next(j for j in range(self.fmt.count()) if self.fmt.model().item(j).isEnabled())
        self.fmt.setCurrentIndex(i)
        self._format_changed()
        self._loading = False
        for w in (self.name, self.dest, self.pw, self.extra):
            w.textChanged.connect(self._update_preview)
        for w in (self.enc, self.enc_names, self.split, self.solid):
            w.toggled.connect(self._update_preview)
        for w in (self.vol, self.threads, self.recovery):
            w.valueChanged.connect(self._update_preview)
        for w in (self.vol_unit, self.method, self.zip_enc):
            w.currentIndexChanged.connect(self._update_preview)
        self.fmt.currentIndexChanged.connect(self._format_changed)
        self.tool.currentIndexChanged.connect(self._tool_changed)
        self.level.valueChanged.connect(self._level_changed)
        self._update_preview()
        self.resize(640, self.sizeHint().height())

    @staticmethod
    def _row(form, label, field):
        lbl = QLabel(label)
        form.addRow(lbl, field)
        return lbl, field

    @staticmethod
    def _show_row(row, on):
        for w in row:
            if isinstance(w, QWidget):
                w.setVisible(on)
            else:  # a layout
                for i in range(w.count()):
                    if w.itemAt(i).widget():
                        w.itemAt(i).widget().setVisible(on)

    def format(self):
        return next(f for f in self.fmts if f["id"] == self.fmt.currentData())

    def _key(self, name):
        return f"archive/{self.format()['id']}/{name}"

    def _format_changed(self, *_):
        f = self.format()
        self.ext.setText(f["ext"])
        if f.get("single") and self.name.text() == os.path.splitext(os.path.basename(self.paths[0]))[0]:
            self.name.setText(os.path.basename(self.paths[0]))
        self.tool.blockSignals(True)
        self.tool.clear()
        for t in f["available"]:
            self.tool.addItem(t, t)
        saved = self.s.value(self._key("tool"), "")
        if self.tool.findData(saved) >= 0:
            self.tool.setCurrentIndex(self.tool.findData(saved))
        self.tool.blockSignals(False)
        self._show_row(self.tool_row, len(f["available"]) > 1)
        self.enc.setVisible(bool(f.get("password")))
        self.no_enc.setVisible(not f.get("password"))
        self.no_enc.setText(f"{f['label'].split(' ')[0]} can't be encrypted. Use 7z, zip, rar or zpaq for a "
                            "password-protected archive.")
        self.split.setVisible(bool(f.get("volumes")))
        self.solid.setVisible(bool(f.get("solid")))
        self._show_row(self.recovery_row, bool(f.get("recovery")))
        self.enc_names.setVisible(bool(f.get("encrypt_names")))
        # restore this format's last options
        v = self.s.value
        self.solid.setChecked(v(self._key("solid"), True, type=bool))
        self.enc_names.setChecked(v(self._key("encrypt_names"), True, type=bool))
        self.recovery.setValue(int(v(self._key("recovery"), 0)))
        self.extra.setText(v(self._key("extra"), ""))
        self.split.setChecked(v(self._key("split"), False, type=bool))
        self.vol.setValue(int(v(self._key("volume"), 4000)))
        self.vol_unit.setCurrentText(v(self._key("volume_unit"), "MB"))
        self.threads.setValue(int(v(self._key("threads"), 0)))
        self._tool_changed()

    def _tool_changed(self, *_):
        f, t = self.format(), self.tool.currentData()
        if t is None:
            return
        lv = archive.tool_levels(f, t)
        self._show_row(self.level_row, lv is not None)
        if lv:
            self.level.blockSignals(True)
            self.level.setRange(lv[0], lv[1])
            self.level.setValue(int(self.s.value(self._key(f"level_{t}"), lv[2])))
            self.level.blockSignals(False)
        methods = f.get("methods") if t == "7z" else None
        self.method.blockSignals(True)
        self.method.clear()
        for m in methods or []:
            self.method.addItem(m)
        if methods:
            self.method.setCurrentText(self.s.value(self._key("method"), methods[0]))
        self.method.blockSignals(False)
        self._show_row(self.method_row, bool(methods))
        self._show_row(self.threads_row, archive.tool_threads(f, t))
        zip7 = f["id"] == "zip" and t == "7z"
        self.zip_enc.setVisible(zip7)
        self.zip_enc_row.setVisible(zip7)
        self.split.setVisible(bool(f.get("volumes")) and not (f["id"] == "zip" and t == "zip"))
        names = {"7z": "7-Zip", "rar": "rar", "zip": "zip"}
        self.enc_note.setText(_ARGV_NOTE.format(tool=t) if t == "zpaq" else
                              f"The password is passed to {names.get(t, t)} privately (not on its command line).")
        self._level_changed()

    def _level_changed(self, *_):
        f, t, v = self.format(), self.tool.currentData(), self.level.value()
        if t == "7z" or f["id"] == "rar":
            names = archive.LEVEL_NAMES_7Z if t == "7z" else \
                {0: "Store", 1: "Fastest", 2: "Fast", 3: "Normal", 4: "Good", 5: "Best"}
            self.level_lbl.setText(f"{v} — {names.get(v, '')}".rstrip(" —"))
        elif f["id"] == "zpaq":
            self.level_lbl.setText(f"{v} — {['', 'Fast', 'Normal', 'Good', 'Better', 'Best'][v]}")
        elif t == "zstd" and v > 19:
            self.level_lbl.setText(f"{v} (ultra)")
        else:
            self.level_lbl.setText(str(v))
        hint = ""
        if f["id"] == "zpaq":
            hint = "zpaq doesn't store symbolic links; any in the selection are skipped."
            if v >= 4:
                hint = ("Levels 4–5 are very slow (several minutes per few hundred MB, even on many cores) and use "
                        "about 500 MB of RAM per thread. Progress is shown while files are read; after that the "
                        "status bar shows the elapsed time until zpaq finishes. " + hint)
        self.level_hint.setText(hint)
        self.level_hint.setVisible(bool(hint))
        self._update_preview()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Save Archive In", self.dest.text() or self.base)
        if d:
            self.dest.setText(d)

    def spec(self):
        f, t = self.format(), self.tool.currentData()
        lv = archive.tool_levels(f, t)
        try:
            extra = shlex.split(self.extra.text())
        except ValueError:
            extra = []
        vol = self.vol.value() * (1024 if self.vol_unit.currentText() == "GB" else 1)
        name = self.name.text().strip()
        if name.endswith(f["ext"]):
            name = name[:-len(f["ext"])]
        return dict(
            format=f, tool=t, base=self.base, rels=self.rels,
            out=os.path.join(os.path.expanduser(self.dest.text().strip() or self.base), name + f["ext"]),
            level=self.level.value() if lv else None, method=self.method.currentText() or None,
            threads=self.threads.value() if archive.tool_threads(f, t) else 0,
            password=self.pw.text() if f.get("password") and self.enc.isChecked() else "",
            encrypt_names=self.enc_names.isChecked() if f.get("encrypt_names") else False,
            zip_encryption=self.zip_enc.currentData(),
            volume_mb=vol if self.split.isVisible() and self.split.isChecked() else 0,
            solid=self.solid.isChecked() if f.get("solid") else None,
            recovery=self.recovery.value() if f.get("recovery") else 0,
            extra=extra, trash_originals=self.trash_after.isChecked())

    def _update_preview(self, *_):
        if self._loading or self.tool.currentData() is None:
            return
        try:
            self.preview.setPlainText(archive.command_preview(self.spec()))
        except Exception as e:
            self.preview.setPlainText(f"({e})")

    def _ok(self):
        sp = self.spec()
        name = os.path.basename(sp["out"])
        if not self.name.text().strip() or "/" in self.name.text():
            QMessageBox.warning(self, "Compress", "Enter a name for the archive.")
            return
        if not os.path.isdir(os.path.dirname(sp["out"])):
            QMessageBox.warning(self, "Compress", f"“{os.path.dirname(sp['out'])}” is not a folder.")
            return
        if sp["password"] and self.pw.text() != self.pw2.text():
            QMessageBox.warning(self, "Compress", "The passwords don't match.")
            return
        if self.enc.isVisible() and self.enc.isChecked() and not sp["password"]:
            QMessageBox.warning(self, "Compress", "Enter a password, or turn off encryption.")
            return
        if self.extra.text().strip():
            try:
                shlex.split(self.extra.text())
            except ValueError as e:
                QMessageBox.warning(self, "Compress", f"Extra options: {e}")
                return
        if os.path.exists(sp["out"]) and QMessageBox.question(
                self, "Compress", f"“{name}” already exists. Replace it?") != QMessageBox.StandardButton.Yes:
            return
        s, f, t = self.s, sp["format"], sp["tool"]
        s.setValue("archive/format", f["id"])
        s.setValue(self._key("tool"), t)
        if sp["level"] is not None:
            s.setValue(self._key(f"level_{t}"), sp["level"])
        if sp["method"]:
            s.setValue(self._key("method"), sp["method"])
        for k, v in (("threads", self.threads.value()), ("solid", self.solid.isChecked()),
                     ("encrypt_names", self.enc_names.isChecked()), ("recovery", self.recovery.value()),
                     ("extra", self.extra.text()), ("split", self.split.isChecked()),
                     ("volume", self.vol.value()), ("volume_unit", self.vol_unit.currentText())):
            s.setValue(self._key(k), v)
        self.accept()


# ---------------------------------------------------------------- extract

class ExtractDialog(QDialog):
    def __init__(self, parent, path, settings, encrypted):
        super().__init__(parent)
        self.setWindowTitle("Extract")
        self.path, self.s = path, settings
        form = QFormLayout(self)
        form.addRow("Archive:", QLabel(f"{os.path.basename(path)}   (using {archive.extract_tool(path)})"))
        self.dest = QLineEdit(os.path.dirname(path))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.dest, 1)
        row.addWidget(browse)
        form.addRow("Extract to:", row)
        self.subfolder = QCheckBox(f"Into a new folder named “{archive.archive_stem(path)}”")
        self.subfolder.setChecked(settings.value("archive/x_subfolder", True, type=bool))
        form.addRow(self.subfolder)
        form.addRow(_small("If everything in the archive is already inside one folder, that folder is used "
                           "instead of nesting it."))
        self.overwrite = QComboBox()
        self.overwrite.addItem("Keep both (rename)", "rename")
        self.overwrite.addItem("Replace", "overwrite")
        self.overwrite.addItem("Skip", "skip")
        self.overwrite.setCurrentIndex(max(0, self.overwrite.findData(settings.value("archive/x_overwrite", "rename"))))
        form.addRow("Existing files:", self.overwrite)
        self.subfolder.toggled.connect(lambda on: self.overwrite.setEnabled(not on))
        self.overwrite.setEnabled(not self.subfolder.isChecked())
        self.pw = QLineEdit()
        row, _ = _password_row(self.pw)
        form.addRow("Password:" if encrypted else "Password (if any):", row)
        if encrypted:
            form.addRow(_small("This archive is encrypted."))
        if archive.extract_tool(path) == "zpaq":  # the only one given the password on its command line
            form.addRow(_small(_ARGV_NOTE.format(tool=archive.extract_tool(path))))
        self.trash_after = QCheckBox("Move the archive to the trash afterwards")
        self.trash_after.setChecked(settings.value("archive/x_trash", False, type=bool))
        form.addRow(self.trash_after)
        self.open_after = QCheckBox("Open the extracted folder")
        self.open_after.setChecked(settings.value("archive/x_open", False, type=bool))
        form.addRow(self.open_after)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Extract")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.encrypted = encrypted
        (self.pw if encrypted else self.dest).setFocus()
        self.resize(560, self.sizeHint().height())

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Extract To", self.dest.text())
        if d:
            self.dest.setText(d)

    def _ok(self):
        dest = os.path.expanduser(self.dest.text().strip())
        if not os.path.isdir(dest):
            if QMessageBox.question(self, "Extract", f"“{dest}” doesn't exist. Create it?") \
                    != QMessageBox.StandardButton.Yes:
                return
            try:
                os.makedirs(dest)
            except OSError as e:
                QMessageBox.warning(self, "Extract", str(e))
                return
        if self.encrypted and not self.pw.text():
            QMessageBox.warning(self, "Extract", "This archive is encrypted. Enter its password.")
            return
        self.s.setValue("archive/x_subfolder", self.subfolder.isChecked())
        self.s.setValue("archive/x_overwrite", self.overwrite.currentData())
        self.s.setValue("archive/x_trash", self.trash_after.isChecked())
        self.s.setValue("archive/x_open", self.open_after.isChecked())
        self.accept()

    def options(self):
        return dict(dest=os.path.expanduser(self.dest.text().strip()), subfolder=self.subfolder.isChecked(),
                    overwrite=self.overwrite.currentData(), password=self.pw.text() or None,
                    trash_after=self.trash_after.isChecked(), open_after=self.open_after.isChecked())


# ---------------------------------------------------------------- flows (called from the main window)

def _check_tool(win, path):
    missing = archive.missing_extract_tool(path)
    if missing:
        QMessageBox.warning(win, "Extract", f"Kestrel needs another program to open “{os.path.basename(path)}”.\n\n"
                            f"Install it with:  sudo apt install {missing}")
        return False
    return True


def _probe_then(win, path, then):
    """Check the archive (on a thread) and call then(info) with {"encrypted", "error"}."""
    if not _check_tool(win, path):
        return

    def done(info):
        if info and info.get("error"):
            QMessageBox.warning(win, "Extract", info["error"])
        elif info:
            then(info)
    fileops.run_task(win, f"Opening {os.path.basename(path)}", lambda: archive.probe(path), done)


def extract_here(win, path):
    """Extract next to the archive, into a new folder (or its single top-level folder). Asks for a password
    when the archive is encrypted."""
    def go(info):
        pw = None
        if info["encrypted"]:
            pw = ask_password(win, path)
            if pw is None:
                return
        _run_extract(win, path, dict(dest=os.path.dirname(path), subfolder=True, overwrite="rename", password=pw,
                                     trash_after=False, open_after=False))
    _probe_then(win, path, go)


def extract_dialog(win, path):
    def go(info):
        dlg = ExtractDialog(win, path, win.settings, info["encrypted"])
        if dlg.exec():
            _run_extract(win, path, dlg.options())
    _probe_then(win, path, go)


def _collapse(out, dest_parent):
    """If `out` holds exactly one folder, use that folder in its place (no needless nesting)."""
    entries = os.listdir(out)
    if len(entries) == 1 and os.path.isdir(os.path.join(out, entries[0])) and \
            not os.path.islink(os.path.join(out, entries[0])):
        tmp = util.unique_path(dest_parent, f".{entries[0]}.kestrel-tmp", "num")
        os.rename(os.path.join(out, entries[0]), tmp)
        os.rmdir(out)  # free the name first, so "x/x" becomes "x" rather than "x (2)"
        final = util.unique_path(dest_parent, entries[0], "num")
        os.rename(tmp, final)
        return final
    return out


def say_dropped_links(win, name, dropped):
    """Tell the user which of the archive's symlinks were left out for leading outside the folder (an absolute target,
    or one climbing out with ".."), and warn about any that couldn't be removed."""
    removed = [f"{p} → {t}" for p, t, why in dropped if why is None]
    stuck = [f"{p} → {t} ({why})" for p, t, why in dropped if why is not None]
    text = ""
    if removed:
        text += (f"Left out {len(removed)} symbolic link{'s' if len(removed) > 1 else ''} that led outside the folder "
                 "(an absolute target, or one climbing out with “..”):\n\n" + "\n".join(removed[:20]) + "\n\n")
    if stuck:
        text += ("These symbolic links lead outside the folder and couldn't be removed. Opening, copying or deleting "
                 "through them reaches what they point to:\n\n" + "\n".join(stuck[:20]))
    box = QMessageBox.warning if stuck else QMessageBox.information
    box(win, f"Extracting {name}", text.strip())


def _run_extract(win, path, opts):
    name = os.path.basename(path)

    def work(task):
        if opts["subfolder"]:
            out = util.unique_path(opts["dest"], archive.archive_stem(path), "num")
            os.makedirs(out)
        else:
            out = opts["dest"]
        dropped = []
        try:
            archive.extract(task, path, out, opts["password"], opts["overwrite"], dropped_links=dropped)
        except archive.WrongPassword:
            if opts["subfolder"]:
                shutil.rmtree(out, ignore_errors=True)
            return {"wrong_password": True}
        except BaseException:
            if opts["subfolder"]:
                shutil.rmtree(out, ignore_errors=True)
            raise
        result = _collapse(out, opts["dest"]) if opts["subfolder"] else out
        if opts["trash_after"]:
            try:
                util.trash(path)
            except OSError:
                pass
        return {"out": result, "dropped": dropped}

    def done(res):
        if res is None:  # cancelled
            win.statusBar().showMessage(f"Extracting {name} cancelled", 4000)
            return
        if res.get("wrong_password"):
            pw = ask_password(win, path, wrong=True)
            if pw is not None:
                _run_extract(win, path, dict(opts, password=pw))
            return
        out = res["out"]
        win.statusBar().showMessage(f"Extracted {name} to {out}", 6000)
        if res["dropped"]:
            say_dropped_links(win, name, res["dropped"])
        if opts["open_after"]:
            win.navigate(out)
        elif win.pane() and win.pane().dir == os.path.dirname(out):
            win.pane().select_later(out)
    fileops.run_job(win, f"Extracting {name}", work, done)


def compress_dialog(win, paths):
    dlg = CompressDialog(win, paths, win.settings)
    if dlg.exec():
        run_compress(win, dlg.spec())


def run_compress(win, spec):
    name = os.path.basename(spec["out"])
    paths = [os.path.join(spec["base"], r) for r in spec["rels"]]

    def work(task):
        task.report(0, 0, "Measuring…")
        total = 0
        for p in paths:
            task.check()
            if os.path.isdir(p) and not os.path.islink(p):
                total += fileops.dir_stats(p, cancel=lambda: task.cancelled)[0]
            else:
                total += os.path.getsize(p) if os.path.exists(p) else 0
        task.check()
        # an older archive of that name is replaced only once the new one is made
        out = archive.compress(task, dict(spec, total=max(total, 1)))
        if spec.get("trash_originals"):
            for p in paths:
                try:
                    util.trash(p)
                except OSError:
                    pass
        return out

    def done(out):
        if out is None:
            win.statusBar().showMessage(f"Compressing {name} cancelled", 4000)
            return
        win.statusBar().showMessage(f"Created {os.path.basename(out)}", 6000)
        if win.pane() and win.pane().dir == os.path.dirname(out):
            win.pane().select_later(out)
    fileops.run_job(win, f"Compressing {name}", work, done)


def quick_compress_label(settings, paths):
    """Menu text for compressing with the last-used format and options (no password), or None."""
    fid = settings.value("archive/format", "")
    f = next((f for f in archive.formats() if f["id"] == fid and f["available"]), None)
    if not f or f.get("single") and not (len(paths) == 1 and os.path.isfile(paths[0])):
        return None
    base = os.path.basename(paths[0].rstrip("/")) if len(paths) == 1 else \
        (os.path.basename(os.path.commonpath([os.path.dirname(p) for p in paths])) or "Archive")
    return f"Compress to “{base}{f['ext']}”"


def quick_compress(win, paths):
    """Compress with the last-used format and options, next to the files, without a password."""
    dlg = CompressDialog(win, paths, win.settings)  # loads the saved options; never shown
    sp = dlg.spec()
    sp["password"], sp["trash_originals"] = "", False
    if os.path.exists(sp["out"]):
        stem = sp["out"][:-len(sp["format"]["ext"])]
        sp["out"] = util.unique_path(os.path.dirname(stem), os.path.basename(stem) + sp["format"]["ext"], "num")
    run_compress(win, sp)
