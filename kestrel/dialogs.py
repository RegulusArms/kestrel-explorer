"""Dialogs: properties, open-with, rename, batch rename, compress, preferences."""
import grp
import hashlib
import html
import os
import pwd
import re
import shutil
import stat
import time

from PyQt6.QtCore import QStorageInfo, Qt, QTimer
from PyQt6.QtGui import QColor, QGuiApplication, QPalette, QPixmap, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QColorDialog, QComboBox, QCompleter, QDialog,
                             QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
                             QPlainTextEdit, QPushButton, QRadioButton, QSpinBox, QTableWidget,
                             QTableWidgetItem, QTabWidget, QTreeView, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                             QWidget)

from . import fileops, metadata, thumbs, util, uwp


def _sel_label(text=""):
    lab = QLabel(text)
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lab.setWordWrap(True)
    return lab


def _fmt_time(ts):
    if ts is None:
        return "—"
    return time.strftime("%a %d %b %Y, %H:%M:%S", time.localtime(ts))


class TextDialog(QDialog):
    def __init__(self, parent, title, text):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(700, 500)
        lay = QVBoxLayout(self)
        ed = QPlainTextEdit(text)
        ed.setReadOnly(True)
        lay.addWidget(ed)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        copy = bb.addButton("Copy", QDialogButtonBox.ButtonRole.ActionRole)
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(text))
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class MetaTagDialog(QDialog):
    """Edit one metadata tag, or (with an empty key) pick a tag to add from those writable for `path`.
    List-type tags are edited one item per line. `existing` maps "Group:Tag" -> value for tags already in the file."""

    def __init__(self, parent, key, value, path=None, existing=None):
        super().__init__(parent)
        self.adding = not key
        self.items = metadata.as_list(value)
        self.existing = {metadata.tag_identity(k): v for k, v in (existing or {}).items()}
        self.kinds = {}  # tag_identity -> (kind, allowed values), filled from the tag database
        self.setWindowTitle("Add Tag" if self.adding else "Edit Tag")
        self.resize(600, 400)
        form = QFormLayout(self)
        if self.adding:
            self.key = QComboBox()
            self.key.setEditable(True)
            self.key.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            self.key.setMaxVisibleItems(20)
            self.key.lineEdit().setPlaceholderText("Choose or type a tag, e.g. Artist or XMP-dc:Description")
            self.help = {}
            self._fill(metadata.tag_choices(path, metadata._tag_db))
            if metadata._tag_db is None:  # full list takes a few seconds the first time; add it when ready
                def loaded(tags):
                    try:
                        self._fill(metadata.tag_choices(path, tags))
                    except RuntimeError:  # dialog already closed
                        pass
                fileops.run_task(parent, "Loading tags…", metadata.tag_db, loaded, quiet=True)
            self.key.currentTextChanged.connect(self._key_changed)
            form.addRow("Tag:", self.key)
        else:
            self.key = QLineEdit(key)
            self.key.setReadOnly(True)
            form.addRow("Tag:", self.key)
            if path and metadata._tag_db is not None:
                for _, items in metadata.tag_choices(path, metadata._tag_db):
                    for k, _, kind, values in items:
                        self.kinds.setdefault(metadata.tag_identity(k), (kind, values))
        self.blurb = QLabel()
        self.blurb.setWordWrap(True)
        self.blurb.setTextFormat(Qt.TextFormat.PlainText)
        form.addRow("", self.blurb)
        self.value = QPlainTextEdit("\n".join(self.items) if self.items is not None else value)
        form.addRow("Value:", self.value)
        self.note = QLabel()
        self.note.setWordWrap(True)
        form.addRow("", self.note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        if self.adding:
            self.key.setCurrentIndex(-1)
            self.key.setEditText("")
            self.key.setFocus()
        else:
            self.blurb.setText(metadata.TAG_HELP.get(self._catalog_key(key), ""))
            self.blurb.setVisible(bool(self.blurb.text()))
            self._update_note(key)
            self.value.setFocus()

    _COLUMNS = ("Tag", "What it's for", "Accepts")

    def _fill(self, sections):
        """(Re)build the tag dropdown as a 3-column list (tag, description, value type) with a heading per section.
        Typing filters a matching flat list of tags without the headings."""
        text = self.key.currentText()
        self.key.blockSignals(True)
        model, flat = QStandardItemModel(0, 3, self), QStandardItemModel(0, 3, self)
        for m in (model, flat):
            m.setHorizontalHeaderLabels(self._COLUMNS)
        headings = []
        for heading, items in sections:
            h = QStandardItem(heading)
            h.setEnabled(False)
            f = h.font()
            f.setBold(True)
            h.setFont(f)
            headings.append(model.rowCount())
            model.appendRow([h, QStandardItem(), QStandardItem()])
            for k, blurb, kind, values in items:
                self.help[k] = blurb
                self.kinds.setdefault(metadata.tag_identity(k), (kind, values))
                for m in (model, flat):
                    row = [QStandardItem(k), QStandardItem(blurb), QStandardItem(kind)]
                    for c in row:
                        c.setEditable(False)
                        c.setToolTip(f"{k}\n{blurb}\nAccepts: {kind}")
                    m.appendRow(row)
        view = self._tag_view()
        self.key.setView(view)
        self.key.setModel(model)
        for r in headings:
            view.setFirstColumnSpanned(r, model.index(-1, -1), True)
        self._size_columns(view)
        comp = QCompleter(flat, self.key)
        comp.setCompletionColumn(0)
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        comp.setFilterMode(Qt.MatchFlag.MatchContains)
        comp.setMaxVisibleItems(15)
        popup = self._tag_view()
        comp.setPopup(popup)
        self._size_columns(popup)
        self.key.setCompleter(comp)
        self.key.setCurrentIndex(-1)
        self.key.setEditText(text)
        self.key.blockSignals(False)

    def _tag_view(self):
        v = QTreeView()
        v.setRootIsDecorated(False)
        v.setUniformRowHeights(True)
        v.setAlternatingRowColors(True)
        v.setTextElideMode(Qt.TextElideMode.ElideRight)
        v.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        v.setMinimumWidth(860)
        return v

    def _size_columns(self, view):
        h = view.header()
        h.setStretchLastSection(False)
        h.resizeSection(0, 260)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.resizeSection(2, 110)

    def _catalog_key(self, key):
        """The catalog entry for `key`, matching e.g. IFD0:Artist or plain "Artist" to EXIF:Artist."""
        if key in metadata.TAG_HELP:
            return key
        ident = metadata.tag_identity(key)
        for k in metadata.TAG_HELP:
            kid = metadata.tag_identity(k)
            if kid == ident or (":" not in key and kid[1] == ident[1]):
                return k
        return key

    def _key_changed(self, key):
        key = key.strip()
        blurb = self.help.get(key) or metadata.TAG_HELP.get(self._catalog_key(key), "")
        group = key.split(":")[0] if ":" in key else ""
        if group and not metadata.is_editable(group, "", key.split(":")[1]):
            blurb = f"{group} tags describe the file itself and can't be edited."
        elif not blurb and key:
            blurb = ("No description available for this tag." if key in self.help else
                     "Custom tag — written exactly as typed. Prefix a group (e.g. XMP-dc:) to choose where it goes.")
        self.blurb.setText(blurb)
        self.blurb.setVisible(bool(blurb))
        old = self.existing.get(metadata.tag_identity(key)) if key else None
        if old is not None and not self.value.toPlainText().strip():
            items = metadata.as_list(old)
            self.value.setPlainText("\n".join(items) if items is not None else old)
        self._update_note(key)

    def _is_list(self, key):
        return self.items is not None or self._catalog_key(key) in metadata.LIST_TAGS

    def _update_note(self, key):
        notes = []
        kind, values = self.kinds.get(metadata.tag_identity(key), (None, [])) if key else (None, [])
        if kind:
            notes.append(f"<b>Accepts:</b> {kind}.")
        if values:
            notes.append("<b>Allowed values:</b> " + html.escape(", ".join(values)) + ".")
        if self._is_list(key):
            notes.append("List tag — one item per line.")
        if self.adding and key and metadata.tag_identity(key) in self.existing:
            notes.append("This tag is already set; saving replaces its current value.")
        self.note.setText("<small>" + " ".join(notes) + "</small>" if notes else "")
        self.note.setVisible(bool(notes))

    def tag(self):
        return (self.key.currentText() if self.adding else self.key.text()).strip()

    def _ok(self):
        key = self.tag()
        if not key or not re.fullmatch(r"[\w-]+(:[\w-]+)?", key):
            QMessageBox.warning(self, self.windowTitle(),
                                "Choose a tag, or type one like “Artist” or “XMP-dc:Description”.")
            return
        group = key.split(":")[0] if ":" in key else ""
        if group and not metadata.is_editable(group, "", key.split(":")[1]):
            QMessageBox.warning(self, self.windowTitle(), f"{group} tags can't be edited.")
            return
        self.accept()

    def result_value(self):
        text = self.value.toPlainText()
        if self._is_list(self.tag()):
            return [ln for ln in text.splitlines() if ln.strip()]
        return text

    @classmethod
    def ask(cls, parent, key, value, path=None, existing=None):
        d = cls(parent, key, value, path, existing)
        if d.exec() != QDialog.DialogCode.Accepted:
            return None
        return d.tag(), d.result_value()


# ---------------------------------------------------------------- properties

class PropertiesDialog(QDialog):
    def __init__(self, parent, paths):
        super().__init__(parent)
        self.paths = paths
        self.single = len(paths) == 1
        self.path = paths[0]
        self.threads = []
        name = os.path.basename(self.path.rstrip("/")) or self.path
        self.setWindowTitle(f"{name} — Properties" if self.single else f"{len(paths)} items — Properties")
        self.resize(560, 600)
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)
        self.tabs.addTab(self._general_tab(), "General")
        if self.single:
            self.tabs.addTab(self._perm_tab(), "Permissions")
            if util.is_image(self.path):
                self.tabs.addTab(self._image_tab(), "Image")
            if os.path.isfile(self.path):
                self.meta_tab = self._meta_tab()
                self.tabs.addTab(self.meta_tab, "Metadata")
                self.tabs.addTab(self._checksum_tab(), "Checksums")
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._apply)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _run(self, fn, cb, title=None):
        """Background job; shown in the main window's status bar only when given a title."""
        t = fileops.run_task(self, title or "", fn, cb, quiet=title is None)
        self.threads.append(t)

    def _general_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        icon = QLabel()
        icon.setFixedSize(128, 128)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setPixmap(util.icon_for_path(self.path).pixmap(96, 96))
        form.addRow(icon)
        if self.single and os.path.isdir(self.path) and thumbs.manager() is not None:
            t = thumbs.manager()
            pm = t.folder_pixmap(self.path, os.stat(self.path).st_mtime, 128)
            if pm is not None:
                icon.setPixmap(t.scaled(pm, 128))
        if self.single and not os.path.isdir(self.path) and thumbs.can_thumbnail(self.path):
            st = os.stat(self.path)
            self._run(lambda: thumbs.file_thumb(self.path, st.st_mtime, 128),
                      lambda img: img is not None and icon.setPixmap(QPixmap.fromImage(img)))
        if self.single:
            st = os.lstat(self.path)
            is_dir = os.path.isdir(self.path)
            self.name_edit = QLineEdit(os.path.basename(self.path.rstrip("/")))
            form.addRow("Name:", self.name_edit)
            mime = util.mime_for(self.path)
            if os.path.isfile(self.path):
                mime = util.mime_for_content(self.path)
            form.addRow("Type:", _sel_label(f"{mime.comment()} ({mime.name()})"))
            form.addRow("Location:", _sel_label(os.path.dirname(os.path.abspath(self.path))))
            if stat.S_ISLNK(st.st_mode):
                target = os.readlink(self.path)
                broken = "" if os.path.exists(self.path) else "  (broken link)"
                form.addRow("Link target:", _sel_label(target + broken))
            self.size_label = _sel_label("Calculating…" if is_dir else
                                         f"{util.human_size(st.st_size)} ({st.st_size:,} bytes)")
            form.addRow("Size:", self.size_label)
            if is_dir:
                self._run(lambda: fileops.dir_stats(self.path), self._show_dir_size)
            fi_birth = QDateTimeHelper.birth(self.path)
            form.addRow("Created:", _sel_label(fi_birth))
            form.addRow("Modified:", _sel_label(_fmt_time(st.st_mtime)))
            form.addRow("Accessed:", _sel_label(_fmt_time(st.st_atime)))
            form.addRow("Changed:", _sel_label(_fmt_time(st.st_ctime) + "  (metadata)"))
            form.addRow("Inode / links:", _sel_label(f"{st.st_ino} / {st.st_nlink}  (device {st.st_dev})"))
            if is_dir:
                self._folder_style_rows(form)
            if os.path.isfile(self.path):
                app = util.default_app(self.path)
                row = QHBoxLayout()
                self.app_label = QLabel(app.get_name() if app else "—")
                row.addWidget(self.app_label, 1)
                btn = QPushButton("Change…")
                btn.clicked.connect(self._change_app)
                row.addWidget(btn)
                form.addRow("Opens with:", row)
        else:
            form.addRow("Items:", _sel_label(f"{len(self.paths)} selected"))
            form.addRow("Location:", _sel_label(os.path.dirname(self.path)))
            self.size_label = _sel_label("Calculating…")
            form.addRow("Total size:", self.size_label)
            paths = list(self.paths)

            def total():
                size = files = dirs = 0
                for p in paths:
                    if os.path.isdir(p) and not os.path.islink(p):
                        s, f, d = fileops.dir_stats(p)
                        size, files, dirs = size + s, files + f, dirs + d + 1
                    else:
                        size += os.lstat(p).st_size
                        files += 1
                return size, files, dirs
            self._run(total, self._show_dir_size)
        vol = QStorageInfo(self.path)
        if vol.isValid():
            form.addRow("Volume:", _sel_label(
                f"{vol.rootPath()} ({bytes(vol.fileSystemType()).decode()}) — "
                f"{util.human_size(vol.bytesAvailable())} free of {util.human_size(vol.bytesTotal())}"))
        return w

    def _folder_style_rows(self, form):
        """Folder colour and image previews for this folder (also in the folder's right-click menu)."""
        t = thumbs.manager()
        if t is None:
            return
        self.style_color = QComboBox()
        self.style_color.addItem(thumbs.color_swatch(t.folder_color), "Default", "")
        for name, color in thumbs.FOLDER_COLORS:
            self.style_color.addItem(thumbs.color_swatch(color), name, color)
        cur = t.custom_color(self.path) or ""
        if cur and self.style_color.findData(cur) < 0:
            self.style_color.addItem(thumbs.color_swatch(cur), f"Custom ({cur})", cur)
        self.style_color.setCurrentIndex(max(0, self.style_color.findData(cur)))
        form.addRow("Folder colour:", self.style_color)
        self.style_previews = QCheckBox("Show image previews on this folder's icon")
        self.style_previews.setChecked(t.previews_for(self.path))
        form.addRow("", self.style_previews)

    def _apply_folder_style(self):
        t = thumbs.manager()
        if t is None or getattr(self, "style_color", None) is None:
            return
        color = self.style_color.currentData() or None
        if color != t.custom_color(self.path):
            t.set_folder_color([self.path], color)
        on = self.style_previews.isChecked()
        if on != t.previews_for(self.path):
            t.set_folder_previews([self.path], on)

    def _show_dir_size(self, res):
        size, files, dirs = res
        self.size_label.setText(f"{util.human_size(size)} ({size:,} bytes)\n{files:,} files, {dirs:,} folders")

    def _change_app(self):
        dlg = OpenWithDialog(self, [self.path], launch=False)
        dlg.default_box.setChecked(True)
        if dlg.exec() and dlg.selected_app():
            self.app_label.setText(dlg.selected_app().get_name())

    def _perm_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        st = os.lstat(self.path)
        self.orig_mode = stat.S_IMODE(st.st_mode)
        form = QFormLayout()
        try:
            owner = pwd.getpwuid(st.st_uid).pw_name
        except KeyError:
            owner = str(st.st_uid)
        try:
            group = grp.getgrgid(st.st_gid).gr_name
        except KeyError:
            group = str(st.st_gid)
        form.addRow("Owner:", QLabel(f"{owner} ({st.st_uid})"))
        form.addRow("Group:", QLabel(f"{group} ({st.st_gid})"))
        lay.addLayout(form)
        grid = QGridLayout()
        is_dir = os.path.isdir(self.path)
        cols = ["Read", "Write", "Access" if is_dir else "Execute"]
        for c, t in enumerate(cols):
            grid.addWidget(QLabel(f"<b>{t}</b>"), 0, c + 1)
        self.perm_boxes = {}
        bits = [[stat.S_IRUSR, stat.S_IWUSR, stat.S_IXUSR], [stat.S_IRGRP, stat.S_IWGRP, stat.S_IXGRP],
                [stat.S_IROTH, stat.S_IWOTH, stat.S_IXOTH]]
        for r, who in enumerate(["Owner", "Group", "Others"]):
            grid.addWidget(QLabel(who), r + 1, 0)
            for c in range(3):
                cb = QCheckBox()
                cb.setChecked(bool(self.orig_mode & bits[r][c]))
                cb.toggled.connect(self._update_octal)
                self.perm_boxes[bits[r][c]] = cb
                grid.addWidget(cb, r + 1, c + 1)
        lay.addLayout(grid)
        special = QHBoxLayout()
        for bit, label in ((stat.S_ISUID, "Set UID"), (stat.S_ISGID, "Set GID"), (stat.S_ISVTX, "Sticky")):
            cb = QCheckBox(label)
            cb.setChecked(bool(self.orig_mode & bit))
            cb.toggled.connect(self._update_octal)
            self.perm_boxes[bit] = cb
            special.addWidget(cb)
        lay.addLayout(special)
        self.octal = QLabel()
        lay.addWidget(self.octal)
        if not is_dir:
            exe = QCheckBox("Allow executing file as program")
            exe.setChecked(bool(self.orig_mode & stat.S_IXUSR))
            exe.toggled.connect(self._toggle_exec)
            lay.addWidget(exe)
        editable = os.lstat(self.path).st_uid == os.getuid() or os.getuid() == 0
        if not editable:
            lay.addWidget(QLabel("<i>You are not the owner, so you cannot change these permissions.</i>"))
            for cb in self.perm_boxes.values():
                cb.setEnabled(False)
        lay.addStretch(1)
        self._update_octal()
        return w

    def _toggle_exec(self, on):
        pairs = ((stat.S_IRUSR, stat.S_IXUSR), (stat.S_IRGRP, stat.S_IXGRP), (stat.S_IROTH, stat.S_IXOTH))
        for r, x in pairs:
            self.perm_boxes[x].setChecked(on and (x == stat.S_IXUSR or self.perm_boxes[r].isChecked()))

    def _mode(self):
        m = 0
        for bit, cb in self.perm_boxes.items():
            if cb.isChecked():
                m |= bit
        return m

    def _update_octal(self):
        m = self._mode()
        self.octal.setText(f"Octal: <b>{m:04o}</b>    Symbolic: <b>{stat.filemode(m | (stat.S_IFDIR if os.path.isdir(self.path) else stat.S_IFREG))}</b>")

    def _image_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        for k, v in metadata.basic_info(self.path):
            form.addRow(k + ":", _sel_label(v))
        ai = metadata.ai_info(self.path)
        for k, v in ai.items():
            if len(v) > 80 or "\n" in v:
                ed = QPlainTextEdit(v)
                ed.setReadOnly(True)
                ed.setMaximumHeight(110)
                form.addRow(k + ":", ed)
            else:
                form.addRow(k + ":", _sel_label(v))
        btn = QPushButton(uwp.wallpaper_label())
        btn.clicked.connect(lambda: util.set_wallpaper(self.path))
        form.addRow(btn)
        return w

    def _meta_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.meta_filter = QLineEdit()
        self.meta_filter.setPlaceholderText("Filter tags…")
        self.meta_filter.textChanged.connect(self._filter_meta)
        lay.addWidget(self.meta_filter)
        self.meta_tree = QTreeWidget()
        self.meta_tree.setHeaderLabels(["Group", "Tag", "Value"])
        self.meta_tree.setRootIsDecorated(False)
        self.meta_tree.setAlternatingRowColors(True)
        self.meta_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.meta_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.meta_tree.itemDoubleClicked.connect(self._meta_edit)
        self.meta_tree.itemSelectionChanged.connect(self._meta_buttons)
        self.meta_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.meta_tree.customContextMenuRequested.connect(self._meta_menu)
        lay.addWidget(self.meta_tree)
        self.meta_editable = metadata.can_edit() and bool(metadata.tag_families(self.path))
        row = QHBoxLayout()
        self.meta_add_btn = QPushButton("Add Tag…")
        self.meta_add_btn.clicked.connect(self._meta_add)
        self.meta_edit_btn = QPushButton("Edit…")
        self.meta_edit_btn.clicked.connect(lambda: self._meta_edit(self.meta_tree.currentItem()))
        self.meta_del_btn = QPushButton("Remove")
        self.meta_del_btn.clicked.connect(self._meta_remove)
        self.meta_clear_btn = QPushButton("Clear All Metadata…")
        self.meta_clear_btn.clicked.connect(self._meta_clear)
        for b in (self.meta_add_btn, self.meta_edit_btn, self.meta_del_btn):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.meta_clear_btn)
        lay.addLayout(row)
        self.meta_add_btn.setEnabled(self.meta_editable)
        self.meta_clear_btn.setEnabled(self.meta_editable)
        self._meta_buttons()
        lay.addWidget(QLabel("<small>Double-click a row to edit it (greyed-out rows are read-only). "
                             "Changes are written to the file immediately.</small>" if self.meta_editable else
                             "<small>Double-click a row to see the full value. " +
                             ("This file type's metadata can't be edited.</small>" if metadata.can_edit() else
                              "Install exiftool (libimage-exiftool-perl) to edit metadata.</small>")))
        self.meta_loaded = False
        self.tabs.currentChanged.connect(self._maybe_load_meta)
        return w

    def _meta_row_editable(self, it):
        return self.meta_editable and it is not None and it.data(0, Qt.ItemDataRole.UserRole) is True

    def _meta_selected(self):
        return [it for it in self.meta_tree.selectedItems() if self._meta_row_editable(it)]

    def _meta_buttons(self):
        sel = self._meta_selected()
        self.meta_edit_btn.setEnabled(self._meta_row_editable(self.meta_tree.currentItem())
                                      and self.meta_tree.currentItem().isSelected())
        self.meta_del_btn.setEnabled(bool(sel))
        self.meta_del_btn.setText(f"Remove {len(sel)} Tags" if len(sel) > 1 else "Remove")

    def _meta_key(self, it):
        return f"{it.text(0)}:{it.text(1)}" if it.text(0) else it.text(1)

    def _meta_write(self, fn):
        """Run a metadata write in the background, then reload the tag list."""
        def done(warnings):
            if warnings:
                QMessageBox.information(self, "Metadata", "\n".join(warnings))
            self._reload_meta()
        self._run(fn, done, "Writing metadata")

    def _reload_meta(self):
        self.meta_loaded = True
        self._run(lambda: metadata.full_metadata(self.path), self._show_meta)

    def _meta_edit(self, it):
        if it is None:
            return
        val = it.data(2, Qt.ItemDataRole.UserRole)
        if not self._meta_row_editable(it):
            TextDialog(self, it.text(1), val).exec()
            return
        res = MetaTagDialog.ask(self, self._meta_key(it), val, self.path)
        if res:
            key, new = res
            self._meta_write(lambda: metadata.set_tags(self.path, [(key, new)]))

    def _meta_add(self):
        existing = {self._meta_key(it): it.data(2, Qt.ItemDataRole.UserRole)
                    for it in map(self.meta_tree.topLevelItem, range(self.meta_tree.topLevelItemCount()))}
        res = MetaTagDialog.ask(self, "", "", self.path, existing)
        if res:
            key, new = res
            self._meta_write(lambda: metadata.set_tags(self.path, [(key, new)]))

    def _meta_remove(self):
        items = self._meta_selected()
        if not items:
            return
        keys = [self._meta_key(it) for it in items]
        what = keys[0] if len(keys) == 1 else f"{len(keys)} tags"
        if QMessageBox.question(self, "Remove Metadata", f"Remove {what} from this file?\n\nThis can't be undone.") \
                != QMessageBox.StandardButton.Yes:
            return
        self._meta_write(lambda: metadata.set_tags(self.path, [(k, None) for k in keys]))

    def _meta_clear(self):
        box = QMessageBox(QMessageBox.Icon.Warning, "Clear All Metadata",
                          f"Remove all metadata (EXIF, XMP, IPTC, GPS, comments, AI generation data…) "
                          f"from “{os.path.basename(self.path)}”?\n\nThis can't be undone.",
                          QMessageBox.StandardButton.Cancel, self)
        box.addButton("Clear All", QMessageBox.ButtonRole.DestructiveRole)
        keep = QCheckBox("Keep orientation and colour profile")
        keep.setChecked(True)
        box.setCheckBox(keep)
        box.exec()
        if box.buttonRole(box.clickedButton()) != QMessageBox.ButtonRole.DestructiveRole:
            return
        k = keep.isChecked()
        self._meta_write(lambda: metadata.clear_all(self.path, keep_basic=k))

    def _maybe_load_meta(self, idx):
        if self.meta_loaded or self.tabs.widget(idx) is not getattr(self, "meta_tab", None):
            return
        self.meta_loaded = True
        if self.meta_editable and metadata._tag_db is None:
            fileops.run_task(self, "Loading tags…", metadata.tag_db, quiet=True)
        loading = QTreeWidgetItem(["", "Loading…", ""])
        self.meta_tree.addTopLevelItem(loading)
        self._run(lambda: metadata.full_metadata(self.path), self._show_meta)

    def _show_meta(self, rows):
        self.meta_tree.clear()
        dim = self.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
        for group, tag, val in rows:
            short = val if len(val) < 300 else val[:300].replace("\n", " ") + " …"
            it = QTreeWidgetItem([group, tag, short.replace("\n", " ")])
            it.setData(2, Qt.ItemDataRole.UserRole, val)
            editable = metadata.is_editable(group, val, tag)
            it.setData(0, Qt.ItemDataRole.UserRole, editable)
            it.setToolTip(2, short)
            if not editable:
                for c in range(3):
                    it.setForeground(c, dim)
            self.meta_tree.addTopLevelItem(it)
        self.meta_tree.resizeColumnToContents(0)
        self.meta_tree.resizeColumnToContents(1)
        self._filter_meta(self.meta_filter.text())
        self._meta_buttons()

    def _filter_meta(self, text):
        t = text.lower()
        for i in range(self.meta_tree.topLevelItemCount()):
            it = self.meta_tree.topLevelItem(i)
            hay = f"{it.text(0)} {it.text(1)} {it.data(2, Qt.ItemDataRole.UserRole) or ''}".lower()
            it.setHidden(bool(t) and t not in hay)

    def _meta_menu(self, pos):
        it = self.meta_tree.itemAt(pos)
        if not it:
            return
        m = QMenu(self)
        m.addAction("Copy Value", lambda: QGuiApplication.clipboard().setText(it.data(2, Qt.ItemDataRole.UserRole)))
        m.addAction("Copy Row", lambda: QGuiApplication.clipboard().setText(
            f"{it.text(0)}:{it.text(1)} = {it.data(2, Qt.ItemDataRole.UserRole)}"))
        m.addAction("View Value…", lambda: TextDialog(self, it.text(1), it.data(2, Qt.ItemDataRole.UserRole)).exec())
        if self.meta_editable:
            m.addSeparator()
            if self._meta_row_editable(it):
                m.addAction("Edit Value…", lambda: self._meta_edit(it))
            sel = self._meta_selected()
            if sel:
                m.addAction(f"Remove {len(sel)} Tags…" if len(sel) > 1 else "Remove Tag…", self._meta_remove)
            m.addAction("Add Tag…", self._meta_add)
        m.exec(self.meta_tree.viewport().mapToGlobal(pos))

    def _checksum_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        outputs = []
        for algo in ("md5", "sha1", "sha256"):
            row = QHBoxLayout()
            out = QLineEdit()
            out.setReadOnly(True)
            outputs.append(out)
            btn = QPushButton("Compute")
            row.addWidget(out, 1)
            row.addWidget(btn)
            form.addRow(algo.upper() + ":", row)

            def compute(_=False, a=algo, o=out, b=btn):
                b.setEnabled(False)
                o.setText("Computing…")
                path = self.path

                def fn():
                    h = hashlib.new(a)
                    with open(path, "rb") as f:
                        for chunk in iter(lambda: f.read(4 << 20), b""):
                            h.update(chunk)
                    return h.hexdigest()
                self._run(fn, o.setText, f"Computing {a.upper()}")
            btn.clicked.connect(compute)
        verify = QLineEdit()
        verify.setPlaceholderText("Paste a checksum to compare…")
        result = QLabel()

        def check(text):
            text = text.strip().lower()
            vals = [o.text().lower() for o in outputs]
            result.setText("" if not text else ("✔ Match" if text in vals else "✘ No match (compute first)"))
        verify.textChanged.connect(check)
        form.addRow("Verify:", verify)
        form.addRow("", result)
        return w

    def _apply(self):
        if self.single:
            self._apply_folder_style()   # before a rename: styles are kept by path
            if hasattr(self, "perm_boxes"):
                m = self._mode()
                if m != self.orig_mode:
                    try:
                        os.chmod(self.path, m)
                    except PermissionError:
                        from . import admin
                        admin.retry_as_admin(
                            self.parent() or self, "Permissions",
                            f"You don't have permission to change the permissions of “{os.path.basename(self.path)}”.",
                            lambda task, p=self.path, mode=m: admin.session().call(task, "chmod", path=p, mode=mode))
                    except OSError as e:
                        QMessageBox.warning(self, "Permissions", str(e))
            new = self.name_edit.text().strip()
            if new and new != os.path.basename(self.path.rstrip("/")):
                try:
                    err = do_rename(self.path, new)
                    if not err:
                        from . import undo
                        undo.record("rename", "Rename", [(self.path, os.path.join(os.path.dirname(self.path), new))])
                except PermissionError:
                    from . import admin
                    target = os.path.join(os.path.dirname(self.path), new)
                    admin.retry_as_admin(
                        self.parent() or self, "Rename",
                        f"You don't have permission to rename “{os.path.basename(self.path)}”.",
                        lambda task, p=self.path: admin.session().call(task, "rename", src=p, dst=target))
                    err = None
                if err:
                    QMessageBox.warning(self, "Rename", err)
                    return
        self.accept()


class QDateTimeHelper:
    @staticmethod
    def birth(path):
        from PyQt6.QtCore import QFileInfo
        fi = QFileInfo(path)
        bt = fi.birthTime()
        if bt.isValid():
            return _fmt_time(bt.toSecsSinceEpoch())
        return "unknown (not supported by filesystem)"


def do_rename(path, new_name):
    if "/" in new_name or new_name in (".", ".."):
        return "Invalid name."
    target = os.path.join(os.path.dirname(path), new_name)
    if os.path.lexists(target) and os.path.realpath(target) != os.path.realpath(path):
        return f"“{new_name}” already exists."
    try:
        os.rename(path, target)
    except PermissionError:
        raise  # callers offer to retry as administrator
    except OSError as e:
        return str(e)
    return None


# ---------------------------------------------------------------- open with

class OpenWithDialog(QDialog):
    def __init__(self, parent, paths, launch=True):
        super().__init__(parent)
        self.paths, self.launch = paths, launch
        self.setWindowTitle("Open With")
        self.resize(420, 520)
        lay = QVBoxLayout(self)
        ct = util.content_type(paths[0])
        lay.addWidget(QLabel(f"Choose an application for “{os.path.basename(paths[0])}”\n<{ct}>"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search applications…")
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _: self._ok())
        lay.addWidget(self.list)
        rec, others = util.apps_for(paths[0])
        for group, apps in (("Recommended", rec), ("Other applications", others)):
            if not apps:
                continue
            h = QListWidgetItem(group)
            h.setFlags(Qt.ItemFlag.NoItemFlags)
            f = h.font()
            f.setBold(True)
            h.setFont(f)
            self.list.addItem(h)
            for app in apps:
                it = QListWidgetItem(util.gicon_to_qicon(app.get_icon()), app.get_name())
                it.setData(Qt.ItemDataRole.UserRole, app)
                self.list.addItem(it)
        if self.list.count() > 1:
            self.list.setCurrentRow(1)
        self.default_box = QCheckBox("Always use for this file type")
        lay.addWidget(self.default_box)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        if not util.Gio:
            lay.addWidget(QLabel("python3-gi is not available; only the default app can be used."))

    def _filter(self, t):
        t = t.lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) is not None:
                it.setHidden(bool(t) and t not in it.text().lower())

    def selected_app(self):
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _ok(self):
        app = self.selected_app()
        if not app:
            return
        try:
            if self.default_box.isChecked():
                app.set_as_default_for_type(util.content_type(self.paths[0]))
            if self.launch:
                util.launch_app(app, self.paths)
        except Exception as e:
            QMessageBox.warning(self, "Open With", str(e))
        self.accept()


# ---------------------------------------------------------------- rename

def ask_rename(parent, path):
    dlg = QDialog(parent)
    is_dir = os.path.isdir(path)
    dlg.setWindowTitle("Rename Folder" if is_dir else "Rename File")
    lay = QVBoxLayout(dlg)
    old = os.path.basename(path)
    edit = QLineEdit(old)
    edit.setMinimumWidth(380)
    lay.addWidget(edit)
    err = QLabel()
    err.setStyleSheet(f"color: {util.error_color().name()}")
    lay.addWidget(err)
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    bb.button(QDialogButtonBox.StandardButton.Ok).setText("Rename")
    lay.addWidget(bb)
    stem = old if is_dir else util.split_ext(old)[0]
    QTimer.singleShot(0, lambda: edit.setSelection(0, len(stem)))

    def validate(t):
        t = t.strip()
        msg = ""
        if not t:
            msg = " "
        elif "/" in t:
            msg = "Names cannot contain “/”."
        elif t != old and os.path.lexists(os.path.join(os.path.dirname(path), t)):
            msg = "An item with that name already exists."
        err.setText(msg.strip())
        bb.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not msg)
    edit.textChanged.connect(validate)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    if dlg.exec() and edit.text().strip() != old:
        return edit.text().strip()
    return None


class BatchRenameDialog(QDialog):
    def __init__(self, parent, paths):
        super().__init__(parent)
        self.paths = paths
        self.setWindowTitle(f"Rename {len(paths)} Items")
        self.resize(700, 520)
        lay = QVBoxLayout(self)
        modes = QHBoxLayout()
        self.r_tmpl = QRadioButton("Rename using a template")
        self.r_repl = QRadioButton("Find and replace text")
        self.r_tmpl.setChecked(True)
        grp_ = QButtonGroup(self)
        grp_.addButton(self.r_tmpl)
        grp_.addButton(self.r_repl)
        modes.addWidget(self.r_tmpl)
        modes.addWidget(self.r_repl)
        lay.addLayout(modes)
        form = QFormLayout()
        self.tmpl = QLineEdit("[Name] ###")
        self.tmpl.setToolTip("[Name] = original name, # = number (### pads to 3 digits), [Date] = modified date")
        self.start = QSpinBox()
        self.start.setRange(0, 10 ** 6)
        self.start.setValue(1)
        self.find = QLineEdit()
        self.repl = QLineEdit()
        self.regex = QCheckBox("Regular expression")
        self.keep_ext = QCheckBox("Keep file extension")
        self.keep_ext.setChecked(True)
        form.addRow("Template:", self.tmpl)
        form.addRow("Start number:", self.start)
        form.addRow("Find:", self.find)
        form.addRow("Replace with:", self.repl)
        form.addRow("", self.regex)
        form.addRow("", self.keep_ext)
        lay.addLayout(form)
        lay.addWidget(QLabel("<small>Template tokens: <b>[Name]</b> original name, <b>#</b> counter "
                             "(<b>###</b> = 001), <b>[Date]</b> modified date (YYYY-MM-DD)</small>"))
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Original", "New name"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(self.table)
        self.status = QLabel()
        lay.addWidget(self.status)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = bb.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText("Rename")
        bb.accepted.connect(self._apply)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        for w in (self.tmpl, self.find, self.repl):
            w.textChanged.connect(self._preview)
        for w in (self.r_tmpl, self.r_repl, self.regex, self.keep_ext):
            w.toggled.connect(self._preview)
        self.start.valueChanged.connect(self._preview)
        self._preview()

    def _new_names(self):
        out = []
        n = self.start.value()
        for p in self.paths:
            name = os.path.basename(p)
            stem, ext = (name, "") if (os.path.isdir(p) or not self.keep_ext.isChecked()) else util.split_ext(name)
            if self.r_tmpl.isChecked():
                t = self.tmpl.text()
                t = t.replace("[Name]", stem)
                t = t.replace("[Date]", time.strftime("%Y-%m-%d", time.localtime(os.lstat(p).st_mtime)))
                t = re.sub(r"#+", lambda m: str(n).zfill(len(m.group(0))), t)
                new = t + ext
            else:
                f = self.find.text()
                if not f:
                    new = name
                elif self.regex.isChecked():
                    try:
                        new = re.sub(f, self.repl.text(), stem) + ext
                    except re.error:
                        new = name
                else:
                    new = stem.replace(f, self.repl.text()) + ext
            out.append(new)
            n += 1
        return out

    def _preview(self):
        names = self._new_names()
        self.table.setRowCount(len(names))
        problems = 0
        dupes = {n for n in names if names.count(n) > 1}
        for i, (p, new) in enumerate(zip(self.paths, names)):
            self.table.setItem(i, 0, QTableWidgetItem(os.path.basename(p)))
            it = QTableWidgetItem(new)
            bad = (not new.strip() or "/" in new or new in dupes or
                   (new != os.path.basename(p) and os.path.lexists(os.path.join(os.path.dirname(p), new))
                    and os.path.join(os.path.dirname(p), new) not in self.paths))
            if bad:
                it.setForeground(util.error_color())
                problems += 1
            self.table.setItem(i, 1, it)
        self.status.setText(f"{problems} conflicting name(s)" if problems else "")
        self.ok.setEnabled(problems == 0)

    def _apply(self):
        names = self._new_names()
        temps = []
        try:
            for p in self.paths:  # two-phase rename so swaps/overlaps work
                tmp = os.path.join(os.path.dirname(p), f".fe-rename-{os.getpid()}-{len(temps)}")
                os.rename(p, tmp)
                temps.append((tmp, p))
            for (tmp, p), new in zip(temps, names):
                os.rename(tmp, os.path.join(os.path.dirname(p), new))
            from . import undo
            undo.record("rename", f"Rename {len(names)} Items",
                        [(p, os.path.join(os.path.dirname(p), new)) for (_, p), new in zip(temps, names) if
                         os.path.basename(p) != new])
        except OSError as e:
            for tmp, p in temps:
                if os.path.exists(tmp):
                    os.rename(tmp, p)
            QMessageBox.warning(self, "Rename", str(e))
            return
        self.accept()


# ---------------------------------------------------------------- compress

# ---------------------------------------------------------------- preferences

class PreferencesDialog(QDialog):
    def __init__(self, parent, settings):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.s = settings
        form = QFormLayout(self)
        # homepage: overview page, home folder, or any folder / network location
        hp = settings.value("homepage", "overview")
        self.hp_mode = QComboBox()
        self.hp_mode.addItems(["Overview (drives, network & bookmarks)", "Home folder", "Custom location…"])
        self.hp_mode.setCurrentIndex({"overview": 0, "home": 1}.get(hp, 2))
        self.hp_edit = QLineEdit("" if hp in ("overview", "home") else hp)
        self.hp_edit.setPlaceholderText("/path/to/folder  or  smb://server/share, sftp://user@host/path")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_home)
        hp_row = QHBoxLayout()
        hp_row.addWidget(self.hp_edit, 1)
        hp_row.addWidget(browse)
        self.hp_custom = QWidget()
        self.hp_custom.setLayout(hp_row)
        hp_row.setContentsMargins(0, 0, 0, 0)
        self.hp_mode.currentIndexChanged.connect(lambda i: self.hp_custom.setEnabled(i == 2))
        self.hp_custom.setEnabled(self.hp_mode.currentIndex() == 2)
        form.addRow("Homepage:", self.hp_mode)
        form.addRow("", self.hp_custom)
        form.addRow(QLabel("<small>Used when the app starts and for the Home button (Alt+Home). "
                           "Network locations are connected automatically.</small>"))
        self.count = QSpinBox()
        self.count.setRange(1, 4)
        self.count.setValue(int(settings.value("folder_count", 4)))
        self.order = QComboBox()
        self.order.addItems(["First by name", "Newest first"])
        self.order.setCurrentIndex(1 if settings.value("folder_order", "name") == "newest" else 0)
        color_setting = settings.value("folder_color", "accent")
        self.color_accent = QCheckBox("Use the desktop's accent colour")
        self.color_accent.setChecked(thumbs.follows_accent(color_setting))
        self.color = util.accent_color() if self.color_accent.isChecked() else QColor(color_setting)
        self.color_btn = QPushButton()
        self._paint_color()
        self.color_btn.clicked.connect(self._pick_color)

        def accent_toggled(on):
            if on:
                self.color = util.accent_color()
            self._paint_color()
        self.color_accent.toggled.connect(accent_toggled)
        color_row = QHBoxLayout()
        color_row.addWidget(self.color_btn)
        color_row.addWidget(self.color_accent, 1)
        self.max_mb = QSpinBox()
        self.max_mb.setRange(1, 10000)
        self.max_mb.setSuffix(" MB")
        self.max_mb.setValue(int(settings.value("thumb_max_mb", 200)))
        self.img_opener = self._opener_combo("image/jpeg", settings.value("image_opener", "system"), builtin=True)
        self.vid_opener = self._opener_combo("video/mp4", settings.value("video_opener", "system"))
        self.single = QCheckBox("Single-click to open items")
        self.single.setChecked(settings.value("single_click", False, type=bool))
        self.dirs_first = QCheckBox("Previews for folders in list view")
        self.dirs_first.setChecked(settings.value("list_folder_previews", False, type=bool))
        self.slide = QSpinBox()
        self.slide.setRange(1, 120)
        self.slide.setSuffix(" s")
        self.slide.setValue(int(settings.value("slideshow_secs", 4)))
        self.play_gifs = QCheckBox("Play animated GIFs in the file view")
        self.play_gifs.setChecked(settings.value("play_gifs", False, type=bool))
        self.play_webm = QCheckBox("Play WebM videos in the file view (silent looping previews)")
        self.play_webm.setChecked(settings.value("play_webm", False, type=bool))
        if not shutil.which("ffmpeg"):
            self.play_webm.setEnabled(False)
            self.play_webm.setToolTip("Needs ffmpeg:  sudo apt install ffmpeg")
        self.shared_undo = QCheckBox("Share undo between all Kestrel windows")
        self.shared_undo.setChecked(settings.value("shared_undo", False, type=bool))
        self.shared_undo.setToolTip("On: Ctrl+Z in any Kestrel window undoes the newest action from any of them.\nOff: each Kestrel undoes only what was done in it.")
        self.open_in_tabs = QCheckBox("Open folders from other apps as tabs in an open Kestrel window")
        self.open_in_tabs.setChecked(settings.value("open_in_tabs", False, type=bool))
        self.open_in_tabs.setToolTip("On: a folder opened from another app (or with “Show in folder”) becomes a tab in "
                                     "the Kestrel window you used last.\nOff: it opens in a new window.")
        form.addRow("Images in folder previews:", self.count)
        form.addRow("Folder preview picks:", self.order)
        form.addRow("Folder colour:", color_row)
        form.addRow("Don't thumbnail files larger than:", self.max_mb)
        form.addRow("Slideshow interval:", self.slide)
        form.addRow("Open images with:", self.img_opener)
        form.addRow("Open videos with:", self.vid_opener)
        form.addRow(self.single)
        form.addRow(self.dirs_first)
        form.addRow(self.play_gifs)
        form.addRow(self.play_webm)
        form.addRow(self.shared_undo)
        form.addRow(self.open_in_tabs)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _opener_combo(self, ct, current, builtin=False):
        """System default / built-in viewer / any installed app that handles `ct`; item data is the setting value."""
        cb = QComboBox()
        default = util.Gio.AppInfo.get_default_for_type(ct, False) if util.Gio else None
        cb.addItem(f"System default ({default.get_name()})" if default else "System default", "system")
        if builtin:
            cb.addItem(QGuiApplication.windowIcon(), "Kestrel image viewer", "builtin")
        apps = util.apps_for_type(ct)
        if current not in ("system", "builtin") and current not in [a.get_id() for a in apps]:
            app = util.app_by_id(current)
            if app:
                apps.append(app)
        for app in apps:
            cb.addItem(util.gicon_to_qicon(app.get_icon()), app.get_name(), app.get_id())
        i = cb.findData(current)
        cb.setCurrentIndex(i if i >= 0 else 0)
        return cb

    def _browse_home(self):
        start = self.hp_edit.text() if self.hp_edit.text().startswith("/") else util.HOME
        d = QFileDialog.getExistingDirectory(self, "Choose Homepage Folder", start)
        if d:
            self.hp_edit.setText(d)

    def _paint_color(self):
        self.color_btn.setStyleSheet(f"background:{self.color.name()}; min-width:60px; min-height:20px")
        self.color_btn.setEnabled(not self.color_accent.isChecked())
        self.color_btn.setToolTip("Follows the desktop's accent colour" if self.color_accent.isChecked()
                                  else "Choose the colour")

    def _pick_color(self):
        c = QColorDialog.getColor(self.color, self, "Folder colour")
        if c.isValid():
            self.color = c
            self._paint_color()

    def _save(self):
        s = self.s
        mode = self.hp_mode.currentIndex()
        custom = self.hp_edit.text().strip()
        if mode == 2:
            if not custom:
                QMessageBox.warning(self, "Homepage", "Enter a folder or network location for the homepage.")
                return
            if custom.startswith("~"):
                custom = os.path.expanduser(custom)
            if custom.startswith("/") and not os.path.isdir(custom):
                QMessageBox.warning(self, "Homepage", f"“{custom}” is not a folder.")
                return
            s.setValue("homepage", custom)
        else:
            s.setValue("homepage", "overview" if mode == 0 else "home")
        s.setValue("folder_count", self.count.value())
        s.setValue("folder_order", "newest" if self.order.currentIndex() == 1 else "name")
        s.setValue("folder_color", "accent" if self.color_accent.isChecked() else self.color.name())
        s.setValue("thumb_max_mb", self.max_mb.value())
        s.setValue("image_opener", self.img_opener.currentData())
        s.setValue("video_opener", self.vid_opener.currentData())
        s.setValue("single_click", self.single.isChecked())
        s.setValue("list_folder_previews", self.dirs_first.isChecked())
        s.setValue("slideshow_secs", self.slide.value())
        s.setValue("play_gifs", self.play_gifs.isChecked())
        s.setValue("play_webm", self.play_webm.isChecked())
        s.setValue("shared_undo", self.shared_undo.isChecked())
        s.setValue("open_in_tabs", self.open_in_tabs.isChecked())
        self.accept()


def choose_dir(parent, title, start):
    return QFileDialog.getExistingDirectory(parent, title, start)


def edit_bookmark(parent, target):
    """Edit the name and location of the bookmark pointing at `target`. Returns True if saved."""
    bms = util.read_bookmarks()
    idx = next((i for i, b in enumerate(bms) if b[0] == target), None)
    if idx is None:
        return False
    d = QDialog(parent)
    d.setWindowTitle("Edit Bookmark")
    d.setMinimumWidth(460)
    form = QFormLayout(d)
    name = QLineEdit(bms[idx][1])
    path = QLineEdit(bms[idx][0])
    browse = QPushButton("Browse…")

    def pick():
        start = path.text().strip()
        p = choose_dir(d, "Bookmark Location", start if os.path.isdir(start) else os.path.expanduser("~"))
        if p:
            path.setText(p)
    browse.clicked.connect(pick)
    row = QHBoxLayout()
    row.addWidget(path, 1)
    row.addWidget(browse)
    form.addRow("Name:", name)
    form.addRow("Location:", row)
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
    form.addRow(bb)
    bb.rejected.connect(d.reject)

    def save():
        p = path.text().strip()
        if p.startswith("~"):
            p = os.path.expanduser(p)
        if not p:
            QMessageBox.warning(d, "Edit Bookmark", "Location can't be empty.")
            return
        if "://" not in p and not os.path.isdir(p):
            if QMessageBox.question(d, "Edit Bookmark", f"“{p}” is not an existing folder. Save anyway?") \
                    != QMessageBox.StandardButton.Yes:
                return
        d.accept()
    bb.accepted.connect(save)
    name.selectAll()
    if d.exec() != QDialog.DialogCode.Accepted:
        return False
    p = path.text().strip()
    p = os.path.expanduser(p) if p.startswith("~") else p
    if "://" not in p:
        p = os.path.normpath(os.path.abspath(p))
    label = name.text().strip() or os.path.basename(p.rstrip("/")) or p
    bms[idx] = (p, label)
    util.write_bookmarks(bms)
    return True
