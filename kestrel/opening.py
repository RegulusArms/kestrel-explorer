"""MainWindow's opening of files and folders: activating items (folders navigate, archives offer Extract, checksum
files check the files they list, images open in the viewer, videos and everything else in their apps, as Preferences
says), Quick View, and the image viewer.
MainWindow (app.py) inherits it; the window itself (tabs, menus, context menus) is there."""
import os
import shutil

from PyQt6.QtWidgets import QMessageBox

from . import archive, archive_ui, dialogs, fileops, hashcheck, places, util
from .viewer import ImageViewer


class Opening:
    """Mixed into MainWindow: uses its panes, settings, chooser and the rest."""

    def open_paths(self, pane, paths, new_tab=False):
        paths = [p for p in paths if p]
        if not paths:
            return
        dirs = [p for p in paths if os.path.isdir(p)]
        files = [p for p in paths if not os.path.isdir(p)]
        if self.chooser is not None:  # a file chooser: folders open as usual, files are the choice
            if dirs:
                pane.set_path(dirs[0])
            elif files:
                self.chooser.activated(files)
            return
        places.add_recent(files)
        if dirs:
            if len(dirs) == 1 and not new_tab and not files:
                pane.set_path(dirs[0])
            else:
                for d in dirs:
                    self.new_tab(d, activate=False)
        archives = [f for f in files if archive.opens_as_archive(f)]
        for f in archives:
            archive_ui.extract_dialog(self, f)  # Kestrel's own extraction, not the system's archive app
        files = [f for f in files if f not in archives]
        # Verify Checksums (one that lists nothing opens as text)
        files = [f for f in files if not (hashcheck.is_hash_file(f) and hashcheck.open_dialog(self, f))]
        images = [f for f in files if util.is_image(f)]
        videos = [f for f in files if util.is_video(f)]
        others = [f for f in files if f not in images and f not in videos]
        # a player would download these again on every open/seek
        fetch = [f for f in videos if util.needs_local_copy(f)]
        videos = [f for f in videos if f not in fetch]
        if fetch:
            fileops.fetch_local(self, fetch, self._open_videos)
        img_choice = self.settings.value("image_opener", "system")
        if images and img_choice == "builtin":
            if len(images) == 1:
                all_imgs = [p for p in pane.all_paths() if util.is_image(p)]
                idx = all_imgs.index(images[0]) if images[0] in all_imgs else 0
                self.open_viewer(pane, all_imgs or images, idx)
            else:
                self.open_viewer(pane, images, 0)
        elif images:
            others += self._open_with_choice(images, img_choice)
        if videos:
            self._open_videos(videos)
        for f in others:
            if not self.open_file(f):
                QMessageBox.warning(self, "Open", f"Could not open {f}")

    def _open_videos(self, videos):
        for f in self._open_with_choice(videos, self.settings.value("video_opener", "system")):
            if not self.open_file(f):
                QMessageBox.warning(self, "Open", f"Could not open {f}")

    def _open_with_choice(self, files, choice):
        """Launch `files` with the app chosen in Preferences; returns files left for the system default."""
        app = util.app_by_id(choice) if choice != "system" else None
        if app is None:
            return files
        try:
            util.launch_app(app, files)
            return []
        except Exception as e:
            QMessageBox.warning(self, "Open", f"Could not open with {app.get_name()}: {e}")
            return files

    def open_file(self, path):
        if path.endswith(".desktop") and shutil.which("gio"):
            import subprocess
            try:
                text = open(path, errors="ignore").read()
            except OSError:
                text = ""
            if "Type=Link" in text:
                for line in text.splitlines():
                    if line.startswith("URL="):
                        target = util.uri_to_path(line[4:].strip()) or line[4:].strip()
                        if os.path.isdir(target):
                            self.navigate(target)
                            return True
                        return util.open_default(target)
            subprocess.Popen(["gio", "launch", path], start_new_session=True)
            return True
        return util.open_default(path)

    def quick_view(self, pane, paths):
        imgs = [p for p in paths if util.is_image(p)]
        if imgs:
            if len(paths) == 1:
                all_imgs = [p for p in pane.all_paths() if util.is_image(p)]
                self.open_viewer(pane, all_imgs, all_imgs.index(imgs[0]) if imgs[0] in all_imgs else 0)
            else:
                self.open_viewer(pane, imgs, 0)
        elif paths:
            self.properties(paths)

    def open_viewer(self, pane, images, idx):
        def on_delete(path):
            try:
                util.trash(path)
                self.sidebar.refresh()
                return True
            except OSError as e:
                QMessageBox.warning(self, "Move to Trash", str(e))
                return False

        def on_close(path):
            if pane in self.panes() and os.path.dirname(path) == pane.path:
                pane.select_paths([path])
            self.viewers = [v for v in self.viewers if v.isVisible()]

        v = ImageViewer(images, idx, self.settings, on_delete=on_delete,
                        on_properties=lambda p: self.properties([p], parent=v),
                        on_close=on_close, on_open_with=lambda p: dialogs.OpenWithDialog(v, [p]).exec())
        self.viewers.append(v)
        if self.settings.value("viewer_fullscreen", False, type=bool):
            v.showFullScreen()
        else:
            v.show()
        v.activateWindow()
