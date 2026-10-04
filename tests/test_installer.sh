#!/usr/bin/env bash
# Installer test: --default (folders, trash:///, the file chooser, PATH), --dock, asking about the dock, and --uninstall
# putting it all back. Runs ../install.sh with a throwaway HOME and a keyfile GSettings backend (never your real settings
# or dock), with the portal definition going to a throwaway folder, and with stub `pgrep`, `sudo` and `systemctl` so it
# can't close a running GNOME Files, install packages or restart your desktop portal. run.sh starts it on a private
# D-Bus session bus.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL="$HERE/../install.sh"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/kestrel-installer-test.XXXXXX")"
trap 'case "$WORK" in */kestrel-installer-test.*) rm -rf "$WORK" ;; esac' EXIT
mkdir -p "$WORK/stubs"
printf '#!/bin/sh\nexit 1\n' > "$WORK/stubs/pgrep"
printf '#!/bin/sh\necho "sudo is disabled in the installer test" >&2\nexit 1\n' > "$WORK/stubs/sudo"
printf '#!/bin/sh\necho "$@" >> "%s/systemctl.log"\n' "$WORK" > "$WORK/stubs/systemctl"
chmod +x "$WORK/stubs/pgrep" "$WORK/stubs/sudo" "$WORK/stubs/systemctl"
# the desktop's portals.conf (as /usr/share/xdg-desktop-portal/ubuntu-portals.conf would be), and the portal folder
mkdir -p "$WORK/xdg/xdg-desktop-portal" "$WORK/portals"
printf '[preferred]\ndefault=gnome;gtk;\norg.freedesktop.impl.portal.Secret=gnome-keyring;\n' \
    > "$WORK/xdg/xdg-desktop-portal/ubuntu-portals.conf"
export GSETTINGS_BACKEND=keyfile PATH="$WORK/stubs:$PATH" XDG_CONFIG_DIRS="$WORK/xdg" XDG_CURRENT_DESKTOP=ubuntu:GNOME
export KESTREL_PORTAL_DIR="$WORK/portals"
unset XDG_CONFIG_HOME XDG_DATA_HOME

fails=0
check() { if eval "$1"; then echo "PASS $2"; else echo "FAIL $2"; fails=$((fails + 1)); fi; }
# the dock checks need GNOME Shell's settings (not installed on Linux Mint and other desktops)
HAS_DOCK=0
/usr/bin/gsettings list-schemas 2>/dev/null | grep -qx org.gnome.shell && HAS_DOCK=1
dock_check() { if (( HAS_DOCK )); then check "$@"; else echo "SKIP $2 (no GNOME Shell settings)"; fi; }
folder_handler() { /usr/bin/gio mime inode/directory 2>/dev/null | sed -n "1s/^Default application for .*: //p"; }
trash_handler() { /usr/bin/gio mime x-scheme-handler/trash 2>/dev/null | sed -n '1s/^Default application for .*: //p'; }
fav() { /usr/bin/gsettings get org.gnome.shell favorite-apps; }
DOCK_BEFORE="['firefox.desktop', 'org.gnome.Nautilus.desktop', 'code.desktop']"
DOCK_AFTER="['firefox.desktop', 'kestrel-explorer.desktop', 'code.desktop']"
fresh() {   # a new HOME where other file managers handle folders (a stand-in for Nemo) and trash:/// (for Thunar)
    export HOME="$WORK/home"
    case "$HOME" in "$WORK"/home) rm -rf "$HOME" ;; esac
    mkdir -p "$HOME/.local/share/applications" "$HOME/.config"
    printf '[Desktop Entry]\nType=Application\nName=Thunar\nExec=true\nMimeType=x-scheme-handler/trash;\n' \
        > "$HOME/.local/share/applications/xfce4-file-manager.desktop"
    xdg-mime default xfce4-file-manager.desktop x-scheme-handler/trash
    printf '[Desktop Entry]\nType=Application\nName=Nemo\nExec=true\nMimeType=inode/directory;\n' \
        > "$HOME/.local/share/applications/nemo.desktop"
    xdg-mime default nemo.desktop inode/directory
    printf "alias ll='ls -l'\n" > "$HOME/.bashrc"   # the user's own line, which must stay
    (( HAS_DOCK )) && /usr/bin/gsettings set org.gnome.shell favorite-apps "$DOCK_BEFORE"
    return 0
}
# what a new terminal finds as `kes` (bash reading ~/.bashrc)
new_terminal_kes() { env -u BASH_ENV bash --norc --noprofile -c '. "$HOME/.bashrc" >/dev/null 2>&1; command -v kes'; }
STATE_FILE() { echo "$HOME/.config/kestrel-explorer/install-state"; }
PORTAL_CONF() { echo "$HOME/.config/xdg-desktop-portal/ubuntu-portals.conf"; }
CHOOSER_SERVICE() { echo "$HOME/.local/share/dbus-1/services/org.freedesktop.impl.portal.desktop.kestrel.service"; }

fresh
check '[[ "$(trash_handler)" == xfce4-file-manager.desktop && "$(folder_handler)" == nemo.desktop ]]' \
    "setup: other apps handle folders and trash:///"
"$INSTALL" --default --dock </dev/null >"$WORK/out1" 2>&1
check '[[ "$(xdg-mime query default inode/directory)" == kestrel-explorer.desktop ]]' "--default: folders open in Kestrel"
check '[[ "$(trash_handler)" == kestrel-explorer.desktop ]]' "--default: trash:/// opens in Kestrel"
check 'grep -q "replacing xfce4-file-manager.desktop" "$WORK/out1"' "--default: says what it replaced"
check 'grep -q "^MimeType=.*x-scheme-handler/trash" "$HOME/.local/share/applications/kestrel-explorer.desktop"' \
    "the desktop file lists trash:///"
check '[[ -f "$HOME/.local/share/dbus-1/services/org.freedesktop.FileManager1.service" ]]' \
    "--default: installs the Show-in-folder service"
dock_check '[[ "$(fav)" == "$DOCK_AFTER" ]]' "--dock: Kestrel takes GNOME Files' place in the dock"
check '[[ -f "$WORK/portals/kestrel.portal" ]] && grep -q "^Exec=.* --file-chooser$" "$(CHOOSER_SERVICE)"' \
    "--default: installs the file chooser's portal definition and D-Bus service"
check 'grep -q "^org.freedesktop.impl.portal.FileChooser=kestrel;" "$(PORTAL_CONF)" &&
       grep -q "^default=gnome;gtk;$" "$(PORTAL_CONF)" &&
       grep -q "^org.freedesktop.impl.portal.Secret=gnome-keyring;$" "$(PORTAL_CONF)"' \
    "--default: the desktop's portals.conf picks Kestrel for the file chooser and keeps the rest"
check 'grep -q "try-restart xdg-desktop-portal.service" "$WORK/systemctl.log"' "--default: restarts the portal"
check '[[ "$(new_terminal_kes)" == "$HOME/.local/bin/kes" ]]' "--default: a new terminal finds kes (~/.local/bin on PATH)"
"$INSTALL" --default --dock </dev/null >"$WORK/out2" 2>&1
dock_check 'grep -q "already in the dock" "$WORK/out2" && [[ "$(fav)" == "$DOCK_AFTER" ]]' "running it again doesn't pin twice"
check '[[ "$(grep -c "^trash_handler=" "$(STATE_FILE)")" == 1 && "$(grep "^trash_handler=" "$(STATE_FILE)")" == trash_handler=xfce4-file-manager.desktop ]]' \
    "running it again keeps the original trash handler"
check '[[ "$(grep -c "FileChooser=" "$(PORTAL_CONF)")" == 1 ]]' "running it again keeps one file chooser line"
check '[[ "$(grep -c "kestrel-explorer: ~/.local/bin on PATH" "$HOME/.bashrc")" == 1 ]]' "running it again adds PATH once"
"$INSTALL" --uninstall >"$WORK/out3" 2>&1
check '[[ "$(trash_handler)" == xfce4-file-manager.desktop ]]' "--uninstall: the trash handler is put back"
check '[[ "$(folder_handler)" == nemo.desktop ]]' "--uninstall: folders open in the previous file manager again"
dock_check '[[ "$(fav)" == "$DOCK_BEFORE" ]]' "--uninstall: GNOME Files is back in the dock"
check '[[ ! -e "$(STATE_FILE)" && ! -e "$HOME/.local/share/applications/kestrel-explorer.desktop" ]]' \
    "--uninstall: removes its files"
check '[[ ! -e "$(PORTAL_CONF)" && ! -e "$(CHOOSER_SERVICE)" && ! -e "$WORK/portals/kestrel.portal" ]]' \
    "--uninstall: the file chooser is GNOME's again"
check '! grep -q kestrel-explorer "$HOME/.bashrc" && [[ "$(cat "$HOME/.bashrc")" == "alias ll='"'"'ls -l'"'"'" ]]' \
    "--uninstall: takes its PATH lines out of ~/.bashrc and keeps the user's own"

fresh   # the user already has a portals.conf of their own
mkdir -p "$(dirname "$(PORTAL_CONF)")"
printf '[preferred]\ndefault=gtk;\n' > "$(PORTAL_CONF)"
cp "$(PORTAL_CONF)" "$WORK/own-portals.conf"
"$INSTALL" --default </dev/null >/dev/null 2>&1
check 'grep -q "^org.freedesktop.impl.portal.FileChooser=kestrel;" "$(PORTAL_CONF)" && grep -q "^default=gtk;$" "$(PORTAL_CONF)"' \
    "--default adds the file chooser to the user's own portals.conf"
"$INSTALL" --uninstall >/dev/null 2>&1
check 'cmp -s "$(PORTAL_CONF)" "$WORK/own-portals.conf"' "--uninstall puts the user's own portals.conf back"

fresh
"$INSTALL" --default </dev/null >"$WORK/out4" 2>&1
dock_check '[[ "$(fav)" == "$DOCK_BEFORE" ]]' "--default without a terminal doesn't touch the dock"
fresh
printf 'y\n' | script -qec "$INSTALL --default" /dev/null >"$WORK/out5" 2>&1
dock_check 'grep -q "Replace GNOME Files in the dock" "$WORK/out5" && [[ "$(fav)" == "$DOCK_AFTER" ]]' \
    "--default in a terminal asks about the dock, and y swaps"
fresh
printf 'n\n' | script -qec "$INSTALL --default" /dev/null >"$WORK/out6" 2>&1
dock_check '[[ "$(fav)" == "$DOCK_BEFORE" ]]' "...and n leaves the dock alone"
"$INSTALL" --uninstall >/dev/null 2>&1
dock_check '[[ "$(fav)" == "$DOCK_BEFORE" ]]' "--uninstall doesn't touch a dock it didn't change"

if (( fails )); then echo "FAILED ($fails failed)"; exit 1; fi
echo "ALL PASSED (0 failed)"
