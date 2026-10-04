"""What GNOME Files offers through extensions: open folders in code editors, Nautilus scripts, Send To (email,
Bluetooth), and sharing folders on the network (Samba usershares)."""
import os
import shutil
import subprocess

from PyQt6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox

from . import util

# code editors that open a folder as a project: (command, name, icon)
EDITORS = [("code", "Visual Studio Code", "vscode"), ("codium", "VSCodium", "vscodium"),
           ("cursor", "Cursor", "cursor"), ("zed", "Zed", "zed"), ("subl", "Sublime Text", "sublime-text")]
SCRIPTS_DIR = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.join(util.HOME, ".local/share")),
                           "nautilus", "scripts")
_FILE_MANAGERS = ("kestrel-explorer.desktop", "folder-explorer.desktop")


def _start(argv, cwd=None, env=None):
    try:
        subprocess.Popen(argv, cwd=cwd, env=env, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- open folders in other apps

def editors():
    return [(cmd, name, icon) for cmd, name, icon in EDITORS if shutil.which(cmd)]


def folder_apps(folder):
    """Apps that open folders (e.g. Disk Usage Analyzer), without other file managers."""
    rec, _ = util.apps_for(folder)
    out = []
    for app in rec:
        cats = app.get_categories() if hasattr(app, "get_categories") else ""
        if app.get_id() in _FILE_MANAGERS or "FileManager" in (cats or "").split(";"):
            continue
        out.append(app)
    return out


def add_open_folder_menu(menu, folder, on_other):
    """Open in <editor> items and an Open With submenu for a folder."""
    for cmd, name, icon in editors():
        menu.addAction(util.theme_icon(icon, "text-editor", "accessories-text-editor"), f"Open in {name}",
                       lambda c=cmd: _start([c, folder]))
    ow = menu.addMenu("Open With")
    for app in folder_apps(folder):
        ow.addAction(util.gicon_to_qicon(app.get_icon()), app.get_name(),
                     lambda a=app: util.launch_app(a, [folder]))
    ow.addSeparator()
    ow.addAction("Other Application…", on_other)


# ---------------------------------------------------------------- Nautilus scripts

def _scripts(folder):
    """[(name, path, sub-entries or None)] for the executables (and sub-folders) in a scripts folder."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    out = []
    for n in sorted(names, key=util.natural_key):
        p = os.path.join(folder, n)
        if n.startswith("."):
            continue
        if os.path.isdir(p):
            sub = _scripts(p)
            if sub:
                out.append((n, p, sub))
        elif os.access(p, os.X_OK):
            out.append((n, p, None))
    return out


def run_script(script, paths, cur_dir):
    """Run a Nautilus script the way GNOME Files does: in the current folder, with the selected files as arguments
    and NAUTILUS_SCRIPT_* variables."""
    env = dict(os.environ)
    env["NAUTILUS_SCRIPT_SELECTED_FILE_PATHS"] = "".join(p + "\n" for p in paths)
    env["NAUTILUS_SCRIPT_SELECTED_URIS"] = "".join(util.file_uri(p) + "\n" for p in paths)
    env["NAUTILUS_SCRIPT_CURRENT_URI"] = util.file_uri(cur_dir) if cur_dir else ""
    env["NAUTILUS_SCRIPT_WINDOW_GEOMETRY"] = ""
    args = [os.path.basename(p) if cur_dir and os.path.dirname(p) == cur_dir else p for p in paths]
    return _start([script] + args, cwd=cur_dir or util.HOME, env=env)


def add_scripts_menu(menu, paths, cur_dir, open_folder):
    """A Scripts submenu, when ~/.local/share/nautilus/scripts has any."""
    entries = _scripts(SCRIPTS_DIR)
    if not entries:
        return

    def fill(m, items):
        for name, path, sub in items:
            if sub is not None:
                fill(m.addMenu(name), sub)
            else:
                m.addAction(name, lambda p=path: run_script(p, paths, cur_dir))
    sm = menu.addMenu(util.theme_icon("text-x-script", "application-x-executable"), "Scripts")
    fill(sm, entries)
    sm.addSeparator()
    sm.addAction("Open Scripts Folder", lambda: open_folder(SCRIPTS_DIR))


# ---------------------------------------------------------------- Send To

def add_send_to_menu(menu, paths):
    """Send To → Email / Bluetooth, for files (not folders)."""
    if not paths or any(os.path.isdir(p) for p in paths):
        return
    targets = []
    if shutil.which("xdg-email"):
        argv = ["xdg-email"]
        for p in paths:
            argv += ["--attach", p]
        targets.append(("mail-send", "Email…", argv))
    if shutil.which("bluetooth-sendto"):
        targets.append(("bluetooth", "Bluetooth Device…", ["bluetooth-sendto"] + paths))
    if not targets:
        return
    sm = menu.addMenu(util.theme_icon("document-send", "mail-send"), "Send To")
    for icon, label, argv in targets:
        sm.addAction(util.theme_icon(icon, "document-send"), label, lambda a=argv: _start(a))


# ---------------------------------------------------------------- network sharing (Samba usershares)

def can_share():
    return shutil.which("net") is not None


def usershares():
    """{folder path: {"name", "comment", "usershare_acl", "guest_ok"}} for this user's network shares."""
    try:
        out = subprocess.run(["net", "usershare", "info"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    shares, cur = {}, None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            cur = {"name": line[1:-1]}
        elif cur is not None and "=" in line:
            k, _, v = line.partition("=")
            cur[k] = v
            if k == "path":
                shares[v] = cur
    return shares


def share_dialog(parent, folder):
    """Share a folder on the local network (like GNOME Files' Sharing Options)."""
    existing = usershares().get(folder)
    d = QDialog(parent)
    d.setWindowTitle("Network Sharing")
    form = QFormLayout(d)
    on = QCheckBox("Share this folder")
    on.setChecked(existing is not None)
    form.addRow(on)
    name = QLineEdit((existing or {}).get("name") or os.path.basename(folder.rstrip("/")) or "share")
    comment = QLineEdit((existing or {}).get("comment", ""))
    write = QCheckBox("Allow others to create and delete files in this folder")
    write.setChecked(":F" in (existing or {}).get("usershare_acl", ""))
    guest = QCheckBox("Guest access (for people without a user account)")
    guest.setChecked((existing or {}).get("guest_ok") == "y")
    form.addRow("Share name:", name)
    form.addRow("Comment:", comment)
    form.addRow(write)
    form.addRow(guest)
    note = QLabel("<small>Others on your network can then open it as smb://this-computer/share-name. "
                  "Needs the Samba server (sudo apt install samba).</small>")
    note.setWordWrap(True)
    form.addRow(note)
    for w in (name, comment, write, guest):
        w.setEnabled(on.isChecked())
        on.toggled.connect(w.setEnabled)
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    bb.accepted.connect(d.accept)
    bb.rejected.connect(d.reject)
    form.addRow(bb)
    d.resize(460, d.sizeHint().height())
    if not d.exec():
        return
    if existing and (not on.isChecked() or existing["name"] != name.text().strip()):
        subprocess.run(["net", "usershare", "delete", existing["name"]], capture_output=True, timeout=10)
    if not on.isChecked():
        return
    acl = "Everyone:F" if write.isChecked() else "Everyone:R"
    r = subprocess.run(["net", "usershare", "add", name.text().strip(), folder, comment.text(), acl,
                        f"guest_ok={'y' if guest.isChecked() else 'n'}"], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        QMessageBox.warning(parent, "Network Sharing", (r.stderr or r.stdout).strip() or "Sharing failed.")
    elif write.isChecked() and not os.stat(folder).st_mode & 0o002:
        QMessageBox.information(parent, "Network Sharing",
                                "The folder is shared. For others to create files in it, it must also be "
                                "writable by others (Properties → Permissions).")

