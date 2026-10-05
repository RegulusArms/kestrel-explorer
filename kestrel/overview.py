"""Overview page: drives with usage, phones and cameras, network locations, connect-to-server, bookmarks.

Modelled on the old "Other Locations" page in GNOME Files. Volume discovery uses
Gio's VolumeMonitor (same source as Nautilus, so unmounted and encrypted drives can
be mounted/unlocked) merged with QStorageInfo for everything else that is mounted
(ZFS datasets, fstab mounts, network filesystems).
"""
import os
import re
import shutil
import subprocess

from PyQt6.QtCore import QFileInfo, QPoint, QRect, QSize, QStorageInfo, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QPalette
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout,
                             QLabel, QLayout, QLineEdit, QMenu, QMessageBox, QProgressBar, QPushButton,
                             QScrollArea, QToolButton, QVBoxLayout, QWidget)

from . import fileops, util

OVERVIEW = "overview://"
OVERVIEW_TITLE = "Overview"

LOCAL_FS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "vfat", "exfat", "ntfs", "ntfs3", "fuseblk", "f2fs",
            "iso9660", "udf", "hfsplus", "apfs", "bcachefs", "jfs", "reiserfs", "msdos"}
NET_FS = {"cifs", "smb3", "smbfs", "nfs", "nfs4", "fuse.sshfs", "sshfs", "davfs", "fuse.rclone", "9p",
          "fuse.davfs2", "afs", "ceph", "glusterfs"}
HIDDEN_ROOTS = ("/boot", "/efi", "/snap", "/var/snap", "/var/lib/docker", "/var/lib/containers", "/run/", "/sys",
                "/proc", "/dev", "/tmp")
URI_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


def is_uri(target):
    """A network location to mount (smb://, sftp://, …), not a local path, the Overview, Starred or Recent."""
    from .places import VIRTUAL
    return bool(URI_RE.match(target or "")) and not target.startswith("file://") and target != OVERVIEW \
        and target not in VIRTUAL


# ---------------------------------------------------------------- data gathering

def scan_filesystems():
    """Mounted filesystems with usage. Runs in a worker thread (statfs can block)."""
    out = []
    zfs_pools = {}
    if shutil.which("zfs"):
        try:
            res = subprocess.run(["zfs", "list", "-Hp", "-o", "name,used,avail", "-d", "0"],
                                 capture_output=True, text=True, timeout=10).stdout
            for line in res.splitlines():
                name, used, avail = line.split("\t")
                zfs_pools[name] = (int(used) + int(avail), int(avail))
        except Exception:
            pass
    by_dev = {}
    for v in QStorageInfo.mountedVolumes():
        if not v.isValid() or not v.isReady():
            continue
        root = v.rootPath()
        fs = bytes(v.fileSystemType()).decode(errors="ignore")
        dev = bytes(v.device()).decode(errors="ignore")
        if root != "/" and (root.startswith(HIDDEN_ROOTS) and not root.startswith("/run/media/")):
            continue
        if dev.startswith("/dev/loop"):
            continue
        net = fs in NET_FS
        if not net and fs not in LOCAL_FS:
            continue
        total, free = v.bytesTotal(), v.bytesAvailable()
        key = dev
        name = v.displayName() if v.displayName() != root else os.path.basename(root) or root
        if fs == "zfs":
            pool = dev.split("/")[0]
            key = "zfs:" + pool
            if pool in zfs_pools:
                total, free = zfs_pools[pool]
            name = pool
        entry = {"name": name, "root": root, "fs": fs, "device": dev, "total": total, "free": free,
                 "kind": "network" if net else ("system" if root == "/" else "local")}
        # one card per device/pool: keep the shallowest mount (skips bind mounts, subvolumes, nested datasets)
        prev = by_dev.get(key)
        if prev is None or len(root) < len(prev["root"]):
            if prev is not None and fs == "zfs":
                entry["name"] = prev["name"]
            by_dev[key] = entry
    out = sorted(by_dev.values(), key=lambda e: (e["kind"] != "system", e["kind"] == "network", e["root"]))
    for e in out:
        if e["root"] == "/":
            e["name"] = "Computer"
    return out


# partitions that belong to a pool/array/LVM: Gio offers to "mount" them, but they can't be
NON_MOUNTABLE_FS = {"zfs_member", "linux_raid_member", "LVM2_member", "swap", "ddf_raid_member",
                    "isw_raid_member", "bcache", "ceph", "drbd", "btrfs_member"}


def _udev_fstype(dev):
    """Filesystem type from the udev database (no root, no subprocess)."""
    try:
        rdev = os.stat(dev).st_rdev
        with open(f"/run/udev/data/b{os.major(rdev)}:{os.minor(rdev)}") as f:
            for line in f:
                if line.startswith("E:ID_FS_TYPE="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _sysfs_size(dev):
    try:
        name = os.path.basename(os.path.realpath(dev))
        with open(f"/sys/class/block/{name}/size") as f:
            return int(f.read().strip()) * 512
    except (OSError, ValueError):
        return 0


# ---------------------------------------------------------------- Gio mount helpers

_live_ops = set()


def make_mount_operation(parent):
    """Gio.MountOperation that asks for passwords/questions with Qt dialogs."""
    Gio = util.Gio
    op = Gio.MountOperation()

    def ask_password(op_, message, default_user, default_domain, flags):
        dlg = QDialog(parent)
        dlg.setWindowTitle("Authentication Required")
        form = QFormLayout(dlg)
        msg = QLabel(message)
        msg.setWordWrap(True)
        form.addRow(msg)
        anon = None
        if flags & Gio.AskPasswordFlags.ANONYMOUS_SUPPORTED:
            anon = QCheckBox("Connect anonymously")
            form.addRow(anon)
        user = domain = pw = None
        if flags & Gio.AskPasswordFlags.NEED_USERNAME:
            user = QLineEdit(default_user or os.environ.get("USER", ""))
            form.addRow("Username:", user)
        if flags & Gio.AskPasswordFlags.NEED_DOMAIN:
            domain = QLineEdit(default_domain or "WORKGROUP")
            form.addRow("Domain:", domain)
        if flags & Gio.AskPasswordFlags.NEED_PASSWORD:
            pw = QLineEdit()
            pw.setEchoMode(QLineEdit.EchoMode.Password)
            form.addRow("Password:", pw)
        save = None
        if flags & Gio.AskPasswordFlags.SAVING_SUPPORTED:
            save = QComboBox()
            save.addItems(["Forget password immediately", "Remember until you log out", "Remember forever"])
            save.setCurrentIndex(1)
            form.addRow("", save)
        if anon is not None:
            anon.toggled.connect(lambda on: [w.setEnabled(not on) for w in (user, domain, pw, save) if w])
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Connect")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        (pw or user or bb).setFocus()
        if not dlg.exec():
            op_.reply(Gio.MountOperationResult.ABORTED)
            return
        if anon is not None and anon.isChecked():
            op_.set_anonymous(True)
        else:
            if user:
                op_.set_username(user.text())
            if domain:
                op_.set_domain(domain.text())
            if pw:
                op_.set_password(pw.text())
            if save:
                op_.set_password_save([Gio.PasswordSave.NEVER, Gio.PasswordSave.FOR_SESSION,
                                       Gio.PasswordSave.PERMANENTLY][save.currentIndex()])
        op_.reply(Gio.MountOperationResult.HANDLED)

    def ask_question(op_, message, choices):
        box = QMessageBox(QMessageBox.Icon.Question, "Question", message, parent=parent)
        buttons = [box.addButton(c, QMessageBox.ButtonRole.AcceptRole) for c in choices]
        box.exec()
        clicked = box.clickedButton()
        if clicked in buttons:
            op_.set_choice(buttons.index(clicked))
            op_.reply(Gio.MountOperationResult.HANDLED)
        else:
            op_.reply(Gio.MountOperationResult.ABORTED)

    op.connect("ask-password", ask_password)
    op.connect("ask-question", ask_question)
    _live_ops.add(op)
    return op


def mount_uri(parent, uri, on_done):
    """Mount a network location (smb://, sftp://, ...) and call on_done(local_path, error)."""
    Gio, GLib = util.Gio, util.GLib
    if not Gio:
        on_done(None, "python3-gi is required for network locations")
        return
    f = Gio.File.new_for_uri(uri)
    try:
        f.find_enclosing_mount(None)
        on_done(f.get_path(), None if f.get_path() else "Mounted, but no local (FUSE) path is available")
        return
    except GLib.Error:
        pass
    op = make_mount_operation(parent)

    def finished(src, res):
        _live_ops.discard(op)
        try:
            src.mount_enclosing_volume_finish(res)
        except GLib.Error as e:
            if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.ALREADY_MOUNTED):
                on_done(None, None if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.FAILED_HANDLED)
                        else e.message)
                return
        path = src.get_path()
        on_done(path, None if path else "Mounted, but no local (FUSE) path is available")

    f.mount_enclosing_volume(Gio.MountMountFlags.NONE, op, None, finished)


def mount_volume(parent, volume, on_done):
    Gio, GLib = util.Gio, util.GLib
    op = make_mount_operation(parent)

    def finished(src, res):
        _live_ops.discard(op)
        try:
            src.mount_finish(res)
        except GLib.Error as e:
            on_done(None, None if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.FAILED_HANDLED) else e.message)
            return
        m = src.get_mount()
        on_done(m.get_root().get_path() if m else None, None)

    volume.mount(Gio.MountMountFlags.NONE, op, None, finished)


def unmount(parent, mount, on_done):
    Gio, GLib = util.Gio, util.GLib
    op = make_mount_operation(parent)
    eject = mount.can_eject()

    def finished(src, res):
        _live_ops.discard(op)
        try:
            (src.eject_with_operation_finish if eject else src.unmount_with_operation_finish)(res)
            on_done(None)
        except GLib.Error as e:
            on_done(e.message)

    if eject:
        mount.eject_with_operation(Gio.MountUnmountFlags.NONE, op, None, finished)
    else:
        mount.unmount_with_operation(Gio.MountUnmountFlags.NONE, op, None, finished)


# ---------------------------------------------------------------- phones and cameras

NO_PHONES_HINT = ("No phones or cameras connected. Plug one in and unlock it: on an iPhone, tap “Trust”; "
                  "on Android, choose “File transfer” in the USB notification.")


def is_phone_scheme(scheme):
    """Phones and cameras: gvfs's afc (Apple devices), gphoto2 (cameras, and an iPhone's photos) and mtp (Android)."""
    return scheme in ("afc", "gphoto2", "mtp")


def phone_kind(scheme):
    """What a phone/camera mount holds (an iPhone has two: its photos via gphoto2, its apps' files via afc)."""
    return "Photos and videos" if scheme == "gphoto2" else "Files"


def phone_hint(scheme, name):
    """What to do on the device when it won't mount."""
    low = (name or "").lower()
    if scheme == "afc" or any(w in low for w in ("iphone", "ipad", "ipod", "apple")):
        return "Unlock the iPhone or iPad and tap “Trust” if it asks whether to trust this computer, then try again."
    if scheme == "mtp":
        return "Unlock the phone and choose “File transfer” in its USB notification, then try again."
    return "Make sure the camera is switched on and set to photo transfer (PTP) mode, then try again."


def mount_group(scheme, path, shadowed, known_root):
    """Where the Overview lists a Gio mount: "skip", "local", "phone" or "network". known_root: its path is one of
    the scanned filesystems."""
    if shadowed:
        return "skip"  # hidden behind its volume's own mount (gvfs lists both)
    if path and known_root:
        return "local"
    if is_phone_scheme(scheme):
        return "phone"
    if scheme != "file" or (path and "/gvfs/" in path):
        return "network"  # gvfs network mount (smb, sftp, ...)
    return "skip"


def phone_infos(monitor):
    """Connected phones (mounted, with their FUSE path) and ones that can be mounted (uri = where to mount them)."""
    out = []
    if monitor is None:
        return out
    for m in monitor.get_mounts():
        root = m.get_root()
        scheme = root.get_uri_scheme()
        if not m.is_shadowed() and is_phone_scheme(scheme):
            out.append({"name": m.get_name(), "root": root.get_path(), "uri": root.get_uri(), "fs": phone_kind(scheme),
                        "icon": util.gicon_to_qicon(m.get_icon()), "mount": m, "status": "Connected",
                        "kind": "phone"})
    for v in monitor.get_volumes():
        if v.get_mount() is not None:
            continue
        act = v.get_activation_root()
        if act is None:
            continue
        scheme = act.get_uri_scheme()
        if not is_phone_scheme(scheme) or not v.can_mount():
            continue
        out.append({"name": v.get_name(), "root": "", "uri": act.get_uri(), "fs": phone_kind(scheme), "total": 0,
                    "free": None, "mounted": False, "icon": util.gicon_to_qicon(v.get_icon()), "volume": v,
                    "kind": "phone"})
    return out


# ---------------------------------------------------------------- widgets

class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing=12):
        super().__init__(parent)
        self.items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, i):
        return self.items[i] if 0 <= i < len(self.items) else None

    def takeAt(self, i):
        return self.items.pop(i) if 0 <= i < len(self.items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self.items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _do(self, rect, test):
        x, y, line_h = rect.x(), rect.y(), 0
        sp = self.spacing()
        for it in self.items:
            hint = it.sizeHint()
            if x + hint.width() > rect.right() + 1 and line_h > 0:
                x = rect.x()
                y += line_h + sp
                line_h = 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + sp
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


class Card(QFrame):
    clicked = pyqtSignal()
    middle_clicked = pyqtSignal()
    menu_requested = pyqtSignal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # colours from the theme (the page rebuilds its cards when the desktop's colours change)
        self.setStyleSheet(f"""
            QFrame#card {{ background: {util.card_color().name()}; border: 1px solid {util.card_border().name()};
                           border-radius: 10px; }}
            QFrame#card:hover {{ border: 1px solid palette(highlight); }}
        """)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton and self.rect().contains(ev.position().toPoint()):
            self.clicked.emit()
        elif ev.button() == Qt.MouseButton.MiddleButton:
            self.middle_clicked.emit()

    def contextMenuEvent(self, ev):
        self.menu_requested.emit(ev.globalPos())


def _small(label, dim=True):
    f = QFont(label.font())
    f.setPointSizeF(f.pointSizeF() * 0.9)
    label.setFont(f)
    if dim:
        label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
    return label


class DriveCard(Card):
    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info = info
        self.setFixedSize(340, 96)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        icon = QLabel()
        icon.setPixmap(info["icon"].pixmap(48, 48))
        icon.setFixedSize(52, 52)
        lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(3)
        top = QHBoxLayout()
        name = QLabel(info["name"])
        f = QFont(name.font())
        f.setBold(True)
        name.setFont(f)
        top.addWidget(name, 1)
        self.action_btn = None
        if info.get("action"):
            b = QToolButton()
            b.setAutoRaise(True)
            b.setIcon(util.theme_icon(*info["action_icon"]))
            b.setToolTip(info["action"])
            b.clicked.connect(lambda: self.info["on_action"]())
            top.addWidget(b)
            self.action_btn = b
        col.addLayout(top)
        where = "" if info.get("kind") == "phone" else info.get("root") or info.get("device") or ""
        sub = [where, info.get("fs") or ""]
        col.addWidget(_small(QLabel("  ·  ".join(s for s in sub if s))))
        total, free = info.get("total") or 0, info.get("free")
        if info.get("mounted", True) and total:
            used = total - (free or 0)
            pct = used * 100 / total
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setValue(int(pct * 10))
            bar.setTextVisible(False)
            bar.setFixedHeight(8)
            color = "#c01c28" if pct >= 90 else ("#e5a50a" if pct >= 75 else "palette(highlight)")
            bar.setStyleSheet(f"QProgressBar {{ border: none; border-radius: 4px; background: {util.card_border().name()}; }}"
                              f"QProgressBar::chunk {{ border-radius: 4px; background: {color}; }}")
            col.addWidget(bar)
            col.addWidget(_small(QLabel(f"{util.human_size(free)} free of {util.human_size(total)}  ({pct:.0f}% used)")))
        else:
            state = info.get("status") or ("Not mounted — click to mount" if not info.get("mounted", True) else "")
            if total:
                state = f"{util.human_size(total)}  ·  {state}"
            col.addWidget(_small(QLabel(state)))
        col.addStretch(1)
        lay.addLayout(col, 1)


class BookmarkCard(Card):
    def __init__(self, target, label, thumbs, parent=None):
        super().__init__(parent)
        self.target, self.thumbs = target, thumbs
        self.setFixedSize(168, 196)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 8)
        lay.setSpacing(4)
        self.pic = QLabel()
        self.pic.setFixedSize(148, 128)
        self.pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.pic)
        name = QLabel(label)
        f = QFont(name.font())
        f.setBold(True)
        name.setFont(f)
        name.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        name.setText(name.fontMetrics().elidedText(label, Qt.TextElideMode.ElideMiddle, 148))
        lay.addWidget(name)
        where = _small(QLabel())
        where.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        where.setText(where.fontMetrics().elidedText(target, Qt.TextElideMode.ElideLeft, 148))
        where.setToolTip(target)
        lay.addWidget(where)
        self.local = not is_uri(target)
        self.update_pic()

    def update_pic(self):
        icon = "folder-remote"
        if self.local:
            if os.path.isdir(self.target):
                fi = QFileInfo(self.target)
                pm = self.thumbs.folder_pixmap(self.target, fi.lastModified().toSecsSinceEpoch(), 128)
                if pm is not None:
                    self.pic.setPixmap(self.thumbs.scaled(pm, 128))
                    return
                icon = util.SPECIAL_DIR_ICONS.get(self.target, "folder")
            else:
                icon = "folder-remote" if self.target.startswith(("/run/user", "/media", "/mnt")) else "dialog-question"
        self.pic.setPixmap(util.theme_icon(icon, "folder").pixmap(96, 96))


class OverviewPage(QScrollArea):
    """Shows drives, network locations and bookmarks. `win` is the MainWindow."""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self.thumbs = win.thumbs
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.body = QWidget()
        self.body.setAutoFillBackground(True)
        self.setWidget(self.body)
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(24, 18, 24, 24)
        self.lay.setSpacing(10)
        self.bookmark_cards = {}
        self._fs_task = None
        self._fs = None
        self.refresh_timer = QTimer(self, singleShot=True, interval=300, timeout=self.refresh)
        self.usage_timer = QTimer(self, interval=15000, timeout=self.refresh)
        self.thumbs.updated.connect(self._thumb_ready)
        util.on_palette_change(self, self._rebuild)  # a light/dark switch: cards and icons in the new colours
        if util.Gio:
            self.monitor = util.Gio.VolumeMonitor.get()
            for sig in ("volume-added", "volume-removed", "volume-changed", "mount-added", "mount-removed",
                        "mount-changed"):
                self.monitor.connect(sig, lambda *a: self.isVisible() and self.refresh_timer.start())
        else:
            self.monitor = None

    # -- lifecycle
    def showEvent(self, ev):
        super().showEvent(ev)
        self.refresh()
        self.usage_timer.start()

    def hideEvent(self, ev):
        super().hideEvent(ev)
        self.usage_timer.stop()

    def refresh(self):
        """Re-scan filesystems in the background, then rebuild."""
        if self._fs_task is not None:
            return
        if self._fs is None:
            self._rebuild()  # show bookmarks / gio volumes immediately

        def done(res):
            self._fs_task = None
            self._fs = res
            self._rebuild()

        def failed():
            self._fs_task = None

        self._fs_task = fileops.run_task(self, "", scan_filesystems, done, quiet=True)
        self._fs_task.error.connect(lambda _: failed())

    # -- building
    def _clear(self):
        while self.lay.count():
            it = self.lay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
            elif it.layout():
                self._clear_layout(it.layout())

    def _clear_layout(self, lay):
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                self._clear_layout(it.layout())

    def _section(self, title):
        lab = QLabel(title)
        f = QFont(lab.font())
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() * 1.15)
        lab.setFont(f)
        self.lay.addSpacing(6)
        self.lay.addWidget(lab)
        box = QWidget()
        flow = FlowLayout(box)
        self.lay.addWidget(box)
        return flow

    def _rebuild(self):
        scroll = self.verticalScrollBar().value()
        self._clear()
        self.bookmark_cards = {}
        local, phones, network = self._drive_infos()
        flow = self._section("Drives")
        for info in local:
            flow.addWidget(self._drive_card(info))
        if not local:
            flow.addWidget(_small(QLabel("Scanning…")))
        pflow = self._section("Phones & Cameras")
        for info in phones:
            pflow.addWidget(self._drive_card(info))
        if not phones:
            hint = _small(QLabel(NO_PHONES_HINT))
            hint.setWordWrap(True)
            self.lay.addWidget(hint)
        nflow = self._section("Network")
        for info in network:
            nflow.addWidget(self._drive_card(info))
        if not network:
            nflow.addWidget(_small(QLabel("No network locations connected.")))
        self.lay.addLayout(self._connect_row())
        recents = self.win.settings.value("recent_servers", [], type=list) or []
        if recents:
            row = QHBoxLayout()
            row.addWidget(_small(QLabel("Recent:")))
            for uri in recents[:6]:
                b = QPushButton(uri)
                b.setFlat(True)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(lambda _=False, u=uri: self.win.navigate(u))
                row.addWidget(b)
            row.addStretch(1)
            self.lay.addLayout(row)
        bflow = self._section("Bookmarks")
        bms = util.read_bookmarks()
        for target, label in bms:
            card = BookmarkCard(target, label, self.thumbs)
            card.clicked.connect(lambda t=target: self.win.navigate(t))
            card.middle_clicked.connect(lambda t=target: self.win.open_location(t, new_tab=True))
            card.menu_requested.connect(lambda pos, t=target: self._bookmark_menu(t, pos))
            bflow.addWidget(card)
            self.bookmark_cards[target] = card
        if not bms:
            self.lay.addWidget(_small(QLabel("No bookmarks yet — press Ctrl+D in a folder, or right-click a folder "
                                             "and choose Links & Shortcuts → Add to Bookmarks.")))
        self.lay.addStretch(1)
        QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(scroll))

    def _drive_infos(self):
        fs = list(self._fs or [])
        roots = {e["root"]: e for e in fs}
        local, network = [], []
        gio_by_root = {}
        if self.monitor:
            for m in self.monitor.get_mounts():
                root = m.get_root()
                path = root.get_path()
                scheme = root.get_uri_scheme()
                group = mount_group(scheme, path, m.is_shadowed(), path in roots)
                if group == "local":
                    gio_by_root[path] = m
                elif group == "network":
                    network.append({"name": m.get_name(), "root": path, "uri": root.get_uri(), "fs": scheme,
                                    "icon": util.gicon_to_qicon(m.get_icon()), "mount": m, "status": "Connected",
                                    "kind": "network"})
        for e in fs:
            m = gio_by_root.get(e["root"])
            icon = "drive-harddisk-system" if e["kind"] == "system" else (
                "folder-remote" if e["kind"] == "network" else
                "drive-removable-media" if e["root"].startswith(("/media/", "/run/media/")) else "drive-harddisk")
            info = dict(e, icon=util.theme_icon(icon, "drive-harddisk"), mounted=True)
            if m is not None:
                info["name"] = m.get_name() or info["name"]
                gi = util.gicon_to_qicon(m.get_icon())
                if not gi.isNull():
                    info["icon"] = gi
                if m.can_unmount():
                    info["mount"] = m
            (network if e["kind"] == "network" else local).append(info)
        # unmounted volumes (Gio)
        if self.monitor:
            for v in self.monitor.get_volumes():
                if v.get_mount() is not None or not v.can_mount():
                    continue
                act = v.get_activation_root()
                if act is not None and is_phone_scheme(act.get_uri_scheme()):
                    continue  # phone_infos
                dev = v.get_identifier("unix-device") or ""
                fstype = _udev_fstype(dev) if dev else ""
                if fstype in NON_MOUNTABLE_FS:
                    continue
                local.append({"name": v.get_name(), "root": "", "device": dev,
                              "fs": "encrypted" if fstype == "crypto_LUKS" else fstype,
                              "total": _sysfs_size(dev) if dev else 0, "free": None, "mounted": False,
                              "icon": util.gicon_to_qicon(v.get_icon()), "volume": v, "kind": "unmounted"})
        return local, phone_infos(self.monitor), network

    def _drive_card(self, info):
        if info.get("mount") is not None:
            m = info["mount"]
            info["action"] = "Eject" if m.can_eject() else "Unmount"
            info["action_icon"] = ("media-eject-symbolic", "media-eject")
            info["on_action"] = lambda: self._unmount(m)
        card = DriveCard(info)
        if info.get("mounted", True):
            target = info.get("root") or info.get("uri")
            card.clicked.connect(lambda: target and self.win.navigate(target))
            card.middle_clicked.connect(lambda: target and self.win.open_location(target, new_tab=True))
            card.menu_requested.connect(lambda pos: self._drive_menu(info, pos))
        else:
            card.clicked.connect(lambda: self._mount(info["volume"]))
        return card

    def _connect_row(self):
        row = QHBoxLayout()
        row.addWidget(QLabel("Connect to Server:"))
        edit = QLineEdit()
        edit.setPlaceholderText("smb://server/share   sftp://user@host/path   nfs://server/export   ftp://…")
        go = QPushButton(util.theme_icon("network-server", "network-wired"), "Connect")

        def connect():
            uri = edit.text().strip()
            if uri:
                if not is_uri(uri):
                    uri = "smb://" + uri.lstrip("/\\").replace("\\", "/")
                self.win.navigate(uri)
        edit.returnPressed.connect(connect)
        go.clicked.connect(connect)
        row.addWidget(edit, 1)
        row.addWidget(go)
        return row

    # -- actions
    def _mount(self, volume):
        act = volume.get_activation_root()
        scheme = act.get_uri_scheme() if act is not None else ""
        hint = phone_hint(scheme, volume.get_name()) if is_phone_scheme(scheme) else ""

        def done(path, err):
            if err:
                QMessageBox.warning(self, "Mount", f"{err}\n\n{hint}" if hint else err)
            self.win.sidebar.refresh()
            self.refresh()
            if path:
                self.win.navigate(path)
        mount_volume(self, volume, done)

    def _unmount(self, mount):
        def done(err):
            if err:
                QMessageBox.warning(self, "Unmount", err)
            self.win.sidebar.refresh()
            self.refresh()
        unmount(self, mount, done)

    def _drive_menu(self, info, pos):
        target = info.get("root")
        m = QMenu(self)
        if target:
            m.addAction("Open", lambda: self.win.navigate(target))
            m.addAction("Open in New Tab", lambda: self.win.open_location(target, new_tab=True))
            m.addAction("Open in Terminal", lambda: util.open_terminal(target))
            m.addAction("Add to Bookmarks", lambda: (self.win.sidebar.add_bookmark(target), self.refresh()))
            m.addAction("Properties", lambda: self.win.properties([target]))
        if info.get("mount") is not None:
            m.addSeparator()
            m.addAction(info["action"], lambda: self._unmount(info["mount"]))
        m.exec(pos)

    def _bookmark_menu(self, target, pos):
        m = QMenu(self)
        m.addAction("Open", lambda: self.win.navigate(target))
        m.addAction("Open in New Tab", lambda: self.win.open_location(target, new_tab=True))

        def remove():
            util.write_bookmarks([b for b in util.read_bookmarks() if b[0] != target])
            self.win.sidebar.refresh()
            self._rebuild()
        def edit():
            from .dialogs import edit_bookmark
            if edit_bookmark(self, target):
                self.win.sidebar.refresh()
                self._rebuild()
        m.addSeparator()
        m.addAction("Edit Bookmark…", edit)
        m.addAction("Remove Bookmark", remove)
        m.exec(pos)

    def _thumb_ready(self, path):
        card = self.bookmark_cards.get(path)
        if card is not None:
            card.update_pic()
