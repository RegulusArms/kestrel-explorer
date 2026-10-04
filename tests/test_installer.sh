#!/usr/bin/env bash
# Installer test: --default (folders, trash:///), --dock, asking about the dock, and --uninstall putting it all back.
# Runs ../install.sh with a throwaway HOME and a keyfile GSettings backend (never your real settings or dock), and with
# stub `pgrep` and `sudo` so it can't close a running GNOME Files or install packages. run.sh starts it on a private
# D-Bus session bus.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL="$HERE/../install.sh"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/kestrel-installer-test.XXXXXX")"
trap 'case "$WORK" in */kestrel-installer-test.*) rm -rf "$WORK" ;; esac' EXIT
mkdir -p "$WORK/stubs"
printf '#!/bin/sh\nexit 1\n' > "$WORK/stubs/pgrep"
printf '#!/bin/sh\necho "sudo is disabled in the installer test" >&2\nexit 1\n' > "$WORK/stubs/sudo"
chmod +x "$WORK/stubs/pgrep" "$WORK/stubs/sudo"
export GSETTINGS_BACKEND=keyfile PATH="$WORK/stubs:$PATH"
unset XDG_CONFIG_HOME XDG_DATA_HOME

fails=0
check() { if eval "$1"; then echo "PASS $2"; else echo "FAIL $2"; fails=$((fails + 1)); fi; }
trash_handler() { /usr/bin/gio mime x-scheme-handler/trash 2>/dev/null | sed -n '1s/^Default application for .*: //p'; }
fav() { /usr/bin/gsettings get org.gnome.shell favorite-apps; }
DOCK_BEFORE="['firefox.desktop', 'org.gnome.Nautilus.desktop', 'code.desktop']"
DOCK_AFTER="['firefox.desktop', 'kestrel-explorer.desktop', 'code.desktop']"
fresh() {   # a new HOME where another file manager (here a stand-in for Thunar) handles trash:///
    export HOME="$WORK/home"
    case "$HOME" in "$WORK"/home) rm -rf "$HOME" ;; esac
    mkdir -p "$HOME/.local/share/applications" "$HOME/.config"
    printf '[Desktop Entry]\nType=Application\nName=Thunar\nExec=true\nMimeType=x-scheme-handler/trash;\n' \
        > "$HOME/.local/share/applications/xfce4-file-manager.desktop"
    xdg-mime default xfce4-file-manager.desktop x-scheme-handler/trash
    /usr/bin/gsettings set org.gnome.shell favorite-apps "$DOCK_BEFORE"
}
STATE_FILE() { echo "$HOME/.config/kestrel-explorer/install-state"; }

fresh
check '[[ "$(trash_handler)" == xfce4-file-manager.desktop ]]' "setup: another app handles trash:///"
"$INSTALL" --default --dock </dev/null >"$WORK/out1" 2>&1
check '[[ "$(xdg-mime query default inode/directory)" == kestrel-explorer.desktop ]]' "--default: folders open in Kestrel"
check '[[ "$(trash_handler)" == kestrel-explorer.desktop ]]' "--default: trash:/// opens in Kestrel"
check 'grep -q "replacing xfce4-file-manager.desktop" "$WORK/out1"' "--default: says what it replaced"
check 'grep -q "^MimeType=.*x-scheme-handler/trash" "$HOME/.local/share/applications/kestrel-explorer.desktop"' \
    "the desktop file lists trash:///"
check '[[ -f "$HOME/.local/share/dbus-1/services/org.freedesktop.FileManager1.service" ]]' \
    "--default: installs the Show-in-folder service"
check '[[ "$(fav)" == "$DOCK_AFTER" ]]' "--dock: Kestrel takes GNOME Files' place in the dock"
"$INSTALL" --default --dock </dev/null >"$WORK/out2" 2>&1
check 'grep -q "already in the dock" "$WORK/out2" && [[ "$(fav)" == "$DOCK_AFTER" ]]' "running it again doesn't pin twice"
check '[[ "$(grep -c "^trash_handler=" "$(STATE_FILE)")" == 1 && "$(grep "^trash_handler=" "$(STATE_FILE)")" == trash_handler=xfce4-file-manager.desktop ]]' \
    "running it again keeps the original trash handler"
"$INSTALL" --uninstall >"$WORK/out3" 2>&1
check '[[ "$(trash_handler)" == xfce4-file-manager.desktop ]]' "--uninstall: the trash handler is put back"
check '[[ "$(fav)" == "$DOCK_BEFORE" ]]' "--uninstall: GNOME Files is back in the dock"
check '[[ ! -e "$(STATE_FILE)" && ! -e "$HOME/.local/share/applications/kestrel-explorer.desktop" ]]' \
    "--uninstall: removes its files"

fresh
"$INSTALL" --default </dev/null >"$WORK/out4" 2>&1
check '[[ "$(fav)" == "$DOCK_BEFORE" ]]' "--default without a terminal doesn't touch the dock"
fresh
printf 'y\n' | script -qec "$INSTALL --default" /dev/null >"$WORK/out5" 2>&1
check 'grep -q "Replace GNOME Files in the dock" "$WORK/out5" && [[ "$(fav)" == "$DOCK_AFTER" ]]' \
    "--default in a terminal asks about the dock, and y swaps"
fresh
printf 'n\n' | script -qec "$INSTALL --default" /dev/null >"$WORK/out6" 2>&1
check '[[ "$(fav)" == "$DOCK_BEFORE" ]]' "...and n leaves the dock alone"
"$INSTALL" --uninstall >/dev/null 2>&1
check '[[ "$(fav)" == "$DOCK_BEFORE" ]]' "--uninstall doesn't touch a dock it didn't change"

if (( fails )); then echo "FAILED ($fails failed)"; exit 1; fi
echo "ALL PASSED (0 failed)"
