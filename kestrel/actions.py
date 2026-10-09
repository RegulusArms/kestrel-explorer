"""MainWindow's file actions: the clipboard (cut, copy, paste), drops, Move To / Copy To, duplicate, new folders and
files, rename, Move to Trash, delete, restore from the trash, empty the trash, and links. MainWindow (app.py) inherits
them; the window itself (tabs, menus, opening files) is there."""
import errno
import os

from PyQt6.QtCore import QBuffer, QIODevice, QMimeData, Qt, QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QInputDialog, QMenu, QMessageBox

from . import admin, dialogs, fileops, undo, util


class FileActions:
    """Mixed into MainWindow: uses its pane(), cur_dir(), statusBar() and the rest."""

    def reveal(self, path):
        pane = self.new_tab(os.path.dirname(path))
        if pane:
            pane.select_later(path)

    def copy_text(self, items):
        QGuiApplication.clipboard().setText("\n".join(items))

    def clip(self, cut, paths=None):
        paths = paths if paths is not None else self.pane().selected_paths()
        if not paths:
            return
        md = QMimeData()
        md.setUrls(util.url_list(paths))
        md.setData("x-special/gnome-copied-files",
                   (("cut" if cut else "copy") + "\n" + "\n".join(util.file_uri(p) for p in paths)).encode())
        md.setData("application/x-kde-cutselection", b"1" if cut else b"0")
        md.setText("\n".join(paths))
        QGuiApplication.clipboard().setMimeData(md)
        self.cut_paths = set(paths) if cut else set()
        for p in self.panes():
            p.view().viewport().update()
        self.statusBar().showMessage(f"{len(paths)} item(s) {'cut' if cut else 'copied'}", 3000)

    def read_clipboard(self):
        md = QGuiApplication.clipboard().mimeData()
        if md is None:
            return "copy", []
        if md.hasFormat("x-special/gnome-copied-files"):
            lines = bytes(md.data("x-special/gnome-copied-files")).decode(errors="ignore").splitlines()
            if lines:
                op = lines[0].strip()
                paths = [util.uri_to_path(u) for u in lines[1:] if u.strip()]
                return ("cut" if op == "cut" else "copy"), [p for p in paths if p]
        if md.hasUrls():
            cut = bytes(md.data("application/x-kde-cutselection")) == b"1"
            return ("cut" if cut else "copy"), [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
        if md.hasText():
            paths = [l.strip() for l in md.text().splitlines() if l.strip().startswith("/")]
            if paths and all(os.path.exists(p) for p in paths):
                return "copy", paths
        return "copy", []

    def paste(self, target=None, as_link=False):
        target = target or self.cur_dir()
        if not target:
            return
        op, paths = self.read_clipboard()
        if not paths:
            md = QGuiApplication.clipboard().mimeData()
            if md is not None and md.hasImage():
                dst = util.unique_path(target, "Pasted image.png", "num")
                buf = QBuffer()
                buf.open(QIODevice.OpenModeFlag.WriteOnly)
                try:   # made whole under a hidden name, then given the new name (write_parts)
                    if not QGuiApplication.clipboard().image().save(buf, "PNG"):
                        raise OSError(errno.EIO, "Couldn't save the pasted image")
                    util.write_new(dst, bytes(buf.data()))
                    undo.record("create", "Paste", [dst])
                    self.pane().select_later(dst)
                except OSError as e:
                    QMessageBox.warning(self, "Paste", e.strerror or str(e))
            return
        if as_link:
            self.make_links(paths, target, "sym")
            return
        if op == "cut":
            def done():
                self.cut_paths = set()
                QGuiApplication.clipboard().clear()
            fileops.transfer(self, paths, target, "move", done)
        else:
            fileops.transfer(self, paths, target, "copy")

    def handle_drop(self, paths, target):
        if not paths:
            return
        mods = QGuiApplication.keyboardModifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        if alt:
            m = QMenu(self)
            m.addAction("Move Here", lambda: fileops.transfer(self, paths, target, "move"))
            m.addAction("Copy Here", lambda: fileops.transfer(self, paths, target, "copy"))
            m.addAction("Link Here", lambda: self.make_links(paths, target, "sym"))
            m.exec(self.cursor().pos())
            return
        if ctrl and shift:
            self.make_links(paths, target, "sym")
            return
        if ctrl:
            op = "copy"
        elif shift:
            op = "move"
        else:
            try:
                same = os.lstat(paths[0]).st_dev == os.stat(target).st_dev
            except OSError:
                same = False
            op = "move" if same else "copy"
        fileops.transfer(self, paths, target, op)

    def transfer_to(self, paths, op):
        d = dialogs.choose_dir(self, "Move To" if op == "move" else "Copy To", self.cur_dir() or util.HOME)
        if d:
            fileops.transfer(self, paths, d, op)

    def duplicate(self, paths):
        if paths:
            fileops.start_ops(self, [("copy", p, util.unique_path(os.path.dirname(p), os.path.basename(p)))
                                     for p in paths], "Duplicating", undo_label="Duplicate")

    def new_folder(self):
        cur = self.cur_dir()
        if not cur:
            return
        default = os.path.basename(util.unique_path(cur, "New Folder", "num"))
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:", text=default)
        if ok and name.strip():
            p = os.path.join(cur, name.strip())
            try:
                os.makedirs(p)
                undo.record("create", "New Folder", [p])
                self.pane().select_later(p)
            except PermissionError:
                admin.retry_as_admin(self, "New Folder", f"You don't have permission to create folders in “{cur}”.",
                                     lambda task: admin.session().call(task, "mkdir", path=p),
                                     lambda ok: ok and self.pane().select_later(p))
            except OSError as e:
                QMessageBox.warning(self, "New Folder", str(e))

    def new_file(self, template=None):
        cur = self.cur_dir()
        if not cur:
            return
        base = os.path.basename(template) if template else "Untitled.txt"
        default = os.path.basename(util.unique_path(cur, base, "num"))
        name, ok = QInputDialog.getText(self, "New File", "File name:", text=default)
        if ok and name.strip():
            p = os.path.join(cur, name.strip())
            if os.path.lexists(p):
                QMessageBox.warning(self, "New File", "A file with that name already exists.")
                return
            try:
                if template:
                    util.copyfile(template, p, new_only=True)
                else:
                    util.write_new(p, b"")
                undo.record("create", "New File", [p])
                self.pane().select_later(p)
            except PermissionError:
                admin.retry_as_admin(
                    self, "New File", f"You don't have permission to create files in “{cur}”.",
                    lambda task: admin.session().call(task, "copyfile", src=template, dst=p) if template else
                    admin.session().call(task, "touch", path=p),
                    lambda ok: ok and self.pane().select_later(p))
            except OSError as e:
                QMessageBox.warning(self, "New File", str(e))

    def rename(self, paths):
        if not paths:
            return
        if len(paths) > 1:
            dialogs.BatchRenameDialog(self, paths).exec()
            return
        new = dialogs.ask_rename(self, paths[0])
        if new:
            target = os.path.join(os.path.dirname(paths[0]), new)

            def renamed(ok=True):
                if ok:
                    self.pane().select_later(target)
                    QTimer.singleShot(150, self.pane()._try_select)
            try:
                err = dialogs.do_rename(paths[0], new)
            except PermissionError:
                admin.retry_as_admin(self, "Rename",
                                     f"You don't have permission to rename “{os.path.basename(paths[0])}”.",
                                     lambda task: admin.session().call(task, "rename", src=paths[0], dst=target),
                                     renamed)
                return
            if err:
                QMessageBox.warning(self, "Rename", err)
            else:
                undo.record("rename", "Rename", [(paths[0], target)])
                renamed()

    def trash_paths(self, paths):
        if not paths:
            return
        if util.in_trash(paths[0]):
            self.delete_paths(paths)
            return
        paths = list(paths)
        trashed = []

        def work(task):
            failed = []
            for i, p in enumerate(paths):
                if task.cancelled:
                    break   # items already moved stay in the trash (and can be undone)
                task.report(i, len(paths), f"{i:,} of {len(paths):,} — {os.path.basename(p)}")
                try:
                    util.trash(p)
                    trashed.append(p)
                except OSError as e:
                    failed.append((p, str(e)))
            return None if task.cancelled else failed

        def done(failed):
            undo.record("trash", "Move to Trash", trashed)
            self.sidebar.refresh()
            if failed:
                r = QMessageBox.question(
                    self, "Cannot move to trash",
                    f"{len(failed)} item(s) could not be moved to the trash:\n{failed[0][1]}\n\n"
                    "Delete them permanently?")
                if r == QMessageBox.StandardButton.Yes:
                    fileops.start_ops(self, [("delete", p, None) for p, _ in failed], "Deleting")
            elif failed is not None:
                self.statusBar().showMessage(f"Moved {len(paths):,} item(s) to the trash", 4000)
            else:
                self.statusBar().showMessage("Move to trash cancelled; items already moved stay in the trash", 6000)
        fileops.run_job(self, "Moving to trash", work, done)

    def delete_paths(self, paths):
        if not paths:
            return
        what = f"“{os.path.basename(paths[0])}”" if len(paths) == 1 else f"these {len(paths)} items"
        box = QMessageBox(QMessageBox.Icon.Warning, "Delete Permanently",
                          f"Permanently delete {what}?\n\nThis cannot be undone.", QMessageBox.StandardButton.Cancel, self)
        delete = box.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is delete:
            def done():
                for p in paths:
                    info = util.trash_info_path(p)
                    if info:
                        try:
                            os.unlink(info)
                        except OSError:
                            pass
                self.sidebar.refresh()
            fileops.start_ops(self, [("delete", p, None) for p in paths], "Deleting", done)

    def restore(self, paths):
        paths = list(paths)

        def work(task):
            errors = []
            for i, p in enumerate(paths):
                task.check()
                task.report(i, len(paths), f"{i:,} of {len(paths):,} — {os.path.basename(p)}")
                orig = util.trash_original_path(p)
                if not orig:
                    errors.append(f"{os.path.basename(p)}: original location unknown")
                    continue
                dest = orig if not os.path.lexists(orig) else \
                    util.unique_path(os.path.dirname(orig), os.path.basename(orig), "num")
                try:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    info = util.trash_info_path(p)
                    util.move(p, dest)
                    if info and os.path.exists(info):
                        os.unlink(info)
                except OSError as e:
                    errors.append(f"{os.path.basename(p)}: {e}")
            return errors

        def done(errors):
            self.sidebar.refresh()
            if errors:
                QMessageBox.warning(self, "Restore", "\n".join(errors[:20]))
        fileops.run_job(self, "Restoring", work, done)

    def empty_trash(self):
        r = QMessageBox.warning(self, "Empty Trash", "Permanently delete all items in the trash?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Yes:
            jobs = [("delete", p, None) for p in fileops.trash_contents()]
            fileops.start_ops(self, jobs, "Emptying trash", self.sidebar.refresh)

    # -- shredding with BleachBit (only offered when it's installed: fileops.can_shred)

    SHRED_NOTE = ("BleachBit overwrites them and then deletes them, so they can't be recovered, not even from the trash. "
                  "On SSDs and some file systems, overwriting can't guarantee that every old copy of the data is gone.")

    def _confirm_shred(self, title, question, button):
        box = QMessageBox(QMessageBox.Icon.Warning, title, question, QMessageBox.StandardButton.Cancel, self)
        box.setInformativeText(self.SHRED_NOTE)
        yes = box.addButton(button, QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is yes

    def shred_paths(self, paths):
        """Shred with BleachBit."""
        if not paths:
            return
        what = f"“{os.path.basename(paths[0])}”" if len(paths) == 1 else f"{len(paths)} items"
        if not self._confirm_shred("Shred with BleachBit", f"Shred {what}?", "Shred"):
            return
        targets = list(paths)
        for p in paths:  # an item in the trash: its record of where it came from too
            info = util.trash_info_path(p)
            if info and os.path.lexists(info):
                targets.append(info)
        self._run_shred(targets, "Shredding with BleachBit")

    def empty_trash_with_bleachbit(self):
        items = fileops.trash_contents()
        if not items:
            QMessageBox.information(self, "Empty Trash with BleachBit", "The trash is empty.")
            return
        if not self._confirm_shred("Empty Trash with BleachBit",
                                   "Shred everything in the trash with BleachBit, on every drive?", "Empty Trash"):
            return
        self._run_shred(items, "Emptying trash with BleachBit")

    def _run_shred(self, targets, title):
        def done(left):
            self.sidebar.refresh()
            names = [p for p in left if not p.endswith(".trashinfo")]
            if names:
                listed = "\n".join(names[:10]) + (f"\n… and {len(names) - 10} more" if len(names) > 10 else "")
                QMessageBox.warning(self, "Shred with BleachBit",
                                    "BleachBit couldn't shred these, so they're still there (you may not have permission "
                                    "to change them):\n\n" + listed)
        fileops.shred(self, targets, title, done)

    def make_links(self, paths, dest, kind):
        if dest is None:
            dest = dialogs.choose_dir(self, "Create Links In", self.cur_dir() or util.HOME)
            if not dest:
                return
        made, errors, denied = [], [], []
        try:
            os.makedirs(dest, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "Create Link", str(e))
            return
        for p in paths:
            plan = None
            try:
                plan = fileops.link_plan(kind, p, dest)
                made.append(fileops.make_link(plan))
            except PermissionError:
                denied.append(plan)
            except OSError as e:
                errors.append(f"{os.path.basename(p)}: {e}")
            except RuntimeError as e:   # a name a shortcut can't hold
                errors.append(str(e))
        if errors:
            QMessageBox.warning(self, "Create Link", "\n".join(errors))

        undo.record("create", "Create Link", made)

        def finish(_ok=True):
            done = made + [fileops.plan_path(pl) for pl in denied if os.path.lexists(fileops.plan_path(pl))]
            if done:
                self.statusBar().showMessage(f"Created {len(done)} link(s) in {dest}", 4000)
                if dest == self.cur_dir():
                    self.pane().select_later(done[0])
        if denied:
            def work(task):
                errs = []
                for pl in denied:
                    try:
                        admin.session().call(task, **pl)
                    except admin.AdminError as e:
                        errs.append(f"{os.path.basename(fileops.plan_path(pl))}: {e}")
                return errs
            admin.retry_as_admin(self, "Create Link", f"You don't have permission to create links in “{dest}”.",
                                 work, finish)
        else:
            finish()
