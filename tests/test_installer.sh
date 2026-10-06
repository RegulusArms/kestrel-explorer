#!/usr/bin/env bash
# Installer test: --default (folders, trash:///, the file chooser, the drop focus extension, PATH), --dock, asking
# about the dock, and --uninstall putting it all back; kes-setup on its own, and its BleachBit cleaner (checked by
# BleachBit itself when it's installed). Runs ../install.sh with a throwaway HOME and a keyfile GSettings backend (never
# your real settings or dock), with the portal definition going to a throwaway folder, and with stub `pgrep`, `sudo`
# and `systemctl` so it can't close a running GNOME Files, install packages or restart your desktop portal. run.sh
# starts it on a private D-Bus session bus.
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
export KESTREL_PORTAL_DIR="$WORK/portals" KESTREL_EXTENSIONS_DIR="$WORK/extensions"
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
exts() { /usr/bin/gsettings get org.gnome.shell enabled-extensions; }
FOCUS_UUID="kestrel-focus@regulusarms.com"
EXTS_BEFORE="['other@example.com']"   # an extension of the user's own, which must stay
EXTS_AFTER="['other@example.com', '$FOCUS_UUID']"
USER_EXT="$WORK/home/.local/share/gnome-shell/extensions/$FOCUS_UUID"
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
    (( HAS_DOCK )) && /usr/bin/gsettings set org.gnome.shell enabled-extensions "$EXTS_BEFORE"
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
check '[[ "$(ls "$HOME"/.local/share/icons/hicolor/*/apps/kestrel-explorer.png | wc -l)" == 8 ]] &&
       grep -qx "Icon=kestrel-explorer" "$HOME/.local/share/applications/kestrel-explorer.desktop"' \
    "the app icon is installed in every size, and the menu entry uses it"
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
dock_check '[[ "$(exts)" == "$EXTS_AFTER" && -f "$USER_EXT/extension.js" && -f "$USER_EXT/metadata.json" ]]' \
    "--default: installs and enables the drop focus extension, keeping the user's others"
"$INSTALL" --default --dock </dev/null >"$WORK/out2" 2>&1
dock_check 'grep -q "already in the dock" "$WORK/out2" && [[ "$(fav)" == "$DOCK_AFTER" ]]' "running it again doesn't pin twice"
check '[[ "$(grep -c "^trash_handler=" "$(STATE_FILE)")" == 1 && "$(grep "^trash_handler=" "$(STATE_FILE)")" == trash_handler=xfce4-file-manager.desktop ]]' \
    "running it again keeps the original trash handler"
check '[[ "$(grep -c "FileChooser=" "$(PORTAL_CONF)")" == 1 ]]' "running it again keeps one file chooser line"
check '[[ "$(grep -c "kestrel-explorer: ~/.local/bin on PATH" "$HOME/.bashrc")" == 1 ]]' "running it again adds PATH once"
dock_check '[[ "$(exts)" == "$EXTS_AFTER" ]]' "running it again enables the drop focus extension once"
"$INSTALL" --uninstall >"$WORK/out3" 2>&1
check '[[ "$(trash_handler)" == xfce4-file-manager.desktop ]]' "--uninstall: the trash handler is put back"
check '[[ "$(folder_handler)" == nemo.desktop ]]' "--uninstall: folders open in the previous file manager again"
dock_check '[[ "$(fav)" == "$DOCK_BEFORE" ]]' "--uninstall: GNOME Files is back in the dock"
check '[[ ! -e "$(STATE_FILE)" && ! -e "$HOME/.local/share/applications/kestrel-explorer.desktop" ]]' \
    "--uninstall: removes its files"
check '! ls "$HOME"/.local/share/icons/hicolor/*/apps/kestrel-explorer.png >/dev/null 2>&1' "--uninstall: removes the app icon"
check '[[ ! -e "$(PORTAL_CONF)" && ! -e "$(CHOOSER_SERVICE)" && ! -e "$WORK/portals/kestrel.portal" ]]' \
    "--uninstall: the file chooser is GNOME's again"
check '! grep -q kestrel-explorer "$HOME/.bashrc" && [[ "$(cat "$HOME/.bashrc")" == "alias ll='"'"'ls -l'"'"'" ]]' \
    "--uninstall: takes its PATH lines out of ~/.bashrc and keeps the user's own"
dock_check '[[ "$(exts)" == "$EXTS_BEFORE" && ! -e "$USER_EXT" ]]' \
    "--uninstall: disables and removes the drop focus extension, keeping the user's others"

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

# kes-setup on its own, as after installing the .deb: kes in /usr/bin, and the package's menu entry and portal definition
SETUP="$HERE/../kes-setup"
mkdir -p "$WORK/share/applications"
printf '[Desktop Entry]\nType=Application\nName=Kestrel Explorer\nExec=kes %%U\nMimeType=inode/directory;x-directory/normal;x-scheme-handler/trash;\n' \
    > "$WORK/share/applications/kestrel-explorer.desktop"
export XDG_DATA_DIRS="$WORK/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
# and the package's kes on PATH: GIO ignores a menu entry whose program it can't find
mkdir -p "$WORK/bin"
printf '#!/bin/sh\nexit 0\n' > "$WORK/bin/kes"
chmod +x "$WORK/bin/kes"
export PATH="$WORK/bin:$PATH"
fresh
printf '[portal]\nDBusName=org.freedesktop.impl.portal.desktop.kestrel\nInterfaces=org.freedesktop.impl.portal.FileChooser;\n' \
    > "$WORK/portals/kestrel.portal"
mkdir -p "$WORK/extensions/$FOCUS_UUID"   # the package's drop focus extension
"$SETUP" --kes /usr/bin/kes --default </dev/null >"$WORK/out7" 2>&1
check '[[ "$(folder_handler)" == kestrel-explorer.desktop && "$(trash_handler)" == kestrel-explorer.desktop ]]' \
    "kes-setup --default: folders and trash:/// open in Kestrel"
check 'grep -qx "Exec=/usr/bin/kes --dbus-service" "$HOME/.local/share/dbus-1/services/org.freedesktop.FileManager1.service" &&
       grep -qx "Exec=/usr/bin/kes --file-chooser" "$(CHOOSER_SERVICE)"' \
    "kes-setup --kes: the D-Bus services start that kes"
check 'grep -q "^org.freedesktop.impl.portal.FileChooser=kestrel;" "$(PORTAL_CONF)" && ! grep -q "needs sudo" "$WORK/out7"' \
    "kes-setup --default: picks Kestrel for the file chooser without sudo when the portal definition is installed"
check '[[ ! -e "$HOME/.local/share/applications/kestrel-explorer.desktop" && "$(cat "$HOME/.bashrc")" == "alias ll='"'"'ls -l'"'"'" ]]' \
    "kes-setup --default: leaves the app menu entry and PATH alone"
dock_check '[[ "$(exts)" == "$EXTS_AFTER" && ! -e "$USER_EXT" ]]' \
    "kes-setup --default: enables the package's drop focus extension without copying it"
"$SETUP" --undo >/dev/null 2>&1
check '[[ "$(folder_handler)" == nemo.desktop && "$(trash_handler)" == xfce4-file-manager.desktop ]]' \
    "kes-setup --undo: folders and trash:/// go back to the previous apps"
check '[[ ! -e "$(STATE_FILE)" && ! -e "$(PORTAL_CONF)" && ! -e "$(CHOOSER_SERVICE)" &&
       ! -e "$HOME/.local/share/dbus-1/services/org.freedesktop.FileManager1.service" ]]' \
    "kes-setup --undo: removes its files"
check '[[ -f "$WORK/portals/kestrel.portal" ]]' "kes-setup --undo: leaves the package's portal definition"
dock_check '[[ "$(exts)" == "$EXTS_BEFORE" && -d "$WORK/extensions/$FOCUS_UUID" ]]' \
    "kes-setup --undo: disables the drop focus extension and leaves the package's copy"

# kes-setup --bleachbit: Kestrel's cleaner for BleachBit. Checked as text (it may only touch Kestrel's own files), against
# BleachBit's schema, and by BleachBit itself in this throwaway home: a dry run (--preview) must list exactly Kestrel's
# files and change nothing, then a real clean must leave everything else (other apps' caches, GNOME's thumbnails, a
# folder linked from the cache, the settings it doesn't name) as it was.
fresh
CLEANER="$HOME/.config/bleachbit/cleaners/kestrel-explorer.xml"
"$SETUP" --bleachbit </dev/null >"$WORK/out8" 2>&1
check '[[ -f "$CLEANER" ]] && python3 -c "import sys, xml.dom.minidom; xml.dom.minidom.parse(sys.argv[1])" "$CLEANER"' \
    "kes-setup --bleachbit: writes BleachBit's cleaner, as well-formed XML"
cleaner_actions() {   # each action as "command path [parameter]", sorted
    grep -o '<action [^>]*>' "$CLEANER" | sed -E 's/.*command="([^"]*)".*path="([^"]*)"( section="[^"]*" parameter="([^"]*)")?.*/\1 \2 \4/' |
        sed 's/ *$//' | LC_ALL=C sort | tr '\n' ';'
}
EXPECTED_ACTIONS='delete $XDG_CACHE_HOME/kestrel-explorer;delete $XDG_CONFIG_HOME/kestrel-explorer/covers.json;'
EXPECTED_ACTIONS+='delete $XDG_CONFIG_HOME/kestrel-explorer/folder_styles.json;delete $XDG_CONFIG_HOME/kestrel-explorer/starred.json;'
EXPECTED_ACTIONS+='ini $XDG_CONFIG_HOME/kestrel-explorer/kestrel-explorer.conf chooser_folder;'
EXPECTED_ACTIONS+='ini $XDG_CONFIG_HOME/kestrel-explorer/kestrel-explorer.conf recent_servers;'
check '[[ "$(cleaner_actions)" == "$EXPECTED_ACTIONS" && "$(grep -c "<action " "$CLEANER")" == 6 ]]' \
    "the cleaner only deletes Kestrel's cache and lists, and only the history keys from its settings"
BLEACHBIT_XSD=/usr/share/doc/bleachbit/examples/cleaner_markup_language.xsd
if command -v xmllint >/dev/null && [[ -f "$BLEACHBIT_XSD" ]]; then
    check 'xmllint --noout --schema "$BLEACHBIT_XSD" "$CLEANER" 2>/dev/null' "the cleaner follows BleachBit's CleanerML schema"
else
    echo "SKIP the cleaner follows BleachBit's CleanerML schema (needs xmllint and BleachBit's schema)"
fi
BB_OPTIONS=(kestrel_explorer.cache kestrel_explorer.history kestrel_explorer.starred kestrel_explorer.folder_looks)
# BleachBit's command line, started without its launcher: BleachBit 4.6's (Ubuntu 24.04) asks loginctl about the
# login session first, and fails when there is none (CI, containers)
bleachbit_cli() {
    LC_ALL=C LANG=C /usr/bin/python3 -c 'import sys; sys.path.insert(0, "/usr/share/bleachbit"); sys.argv[0] = "bleachbit"
import bleachbit.CLI; bleachbit.CLI.process_cmd_line()' "$@"
}
if [[ -f /usr/share/bleachbit/bleachbit/CLI.py ]]; then
    # what Kestrel writes, and what must stay: GNOME's thumbnails, another app's cache, a folder a link in the cache
    # points to, Kestrel's other settings and its install state
    KC="$HOME/.cache/kestrel-explorer" KCONF="$HOME/.config/kestrel-explorer"
    mkdir -p "$KC/folders" "$KC/animated" "$KC/device-files/phone" "$HOME/.cache/thumbnails/normal" \
             "$HOME/.cache/other-app" "$HOME/Documents" "$KCONF"
    for f in "$KC/folders/1a2b-256.png" "$KC/animated/clip.webm" "$KC/device-files/phone/IMG_0001.MOV" \
             "$KC/exiftool-tagdb-13.50.json" "$HOME/.cache/thumbnails/normal/3c4d.png" "$HOME/.cache/other-app/data"; do
        echo data > "$f"
    done
    echo "my own file" > "$HOME/Documents/notes.txt"
    ln -s "$HOME/Documents" "$KC/folders/linked"
    printf '[General]\nchooser_folder=/home/someone/Private\ngeometry=@ByteArray(\\x1\\xd9\\xd0\\xcb)\nhomepage=overview\n' \
        > "$KCONF/kestrel-explorer.conf"
    printf 'recent_servers=smb://nas/photos, sftp://host/home\nsidebar_sections=places, devices\n\n[archive]\nformat=7z\n' \
        >> "$KCONF/kestrel-explorer.conf"
    for f in starred.json covers.json folder_styles.json; do echo '{}' > "$KCONF/$f"; done
    echo "chooser_conf=created" > "$KCONF/install-state"
    cp "$KCONF/kestrel-explorer.conf" "$WORK/conf.before"
    snapshot() { (cd "$HOME" && find . -path ./.config/bleachbit -prune -o -print0 | LC_ALL=C sort -z | xargs -0 ls -ld --time-style=+%s.%N | md5sum); }
    before="$(snapshot)"
    bleachbit_cli --preview "${BB_OPTIONS[@]}" >"$WORK/bb-preview" 2>&1
    listed() { sed -nE 's/^(Delete|Clean file) [^ ]+ //p' "$WORK/bb-preview" | sed "s#^$HOME/##" | LC_ALL=C sort -u | tr '\n' ' '; }
    EXPECTED_LISTED=".cache/kestrel-explorer/animated .cache/kestrel-explorer/animated/clip.webm"
    EXPECTED_LISTED+=" .cache/kestrel-explorer/device-files .cache/kestrel-explorer/device-files/phone"
    EXPECTED_LISTED+=" .cache/kestrel-explorer/device-files/phone/IMG_0001.MOV .cache/kestrel-explorer/exiftool-tagdb-13.50.json"
    EXPECTED_LISTED+=" .cache/kestrel-explorer/folders .cache/kestrel-explorer/folders/1a2b-256.png"
    EXPECTED_LISTED+=" .cache/kestrel-explorer/folders/linked .config/kestrel-explorer/covers.json"
    EXPECTED_LISTED+=" .config/kestrel-explorer/folder_styles.json .config/kestrel-explorer/kestrel-explorer.conf"
    EXPECTED_LISTED+=" .config/kestrel-explorer/starred.json "
    check '[[ "$(listed)" == "$EXPECTED_LISTED" ]]' \
        "BleachBit's dry run with Kestrel's cleaner lists exactly Kestrel's cache, lists and settings file$(
            [[ "$(listed)" == "$EXPECTED_LISTED" ]] || echo " (listed: $(listed); output: $(tail -3 "$WORK/bb-preview" | tr '\n' ' '))")"
    check '[[ "$(snapshot)" == "$before" ]]' "BleachBit's dry run changes nothing"
    if /usr/bin/pgrep -x kes >/dev/null; then
        echo "SKIP BleachBit's clean with Kestrel's cleaner (a Kestrel is running, and BleachBit won't clean while it is)"
    else
        bleachbit_cli --clean "${BB_OPTIONS[@]}" >"$WORK/bb-clean" 2>&1
        check '[[ -d "$KC" && -z "$(ls -A "$KC")" && ! -e "$KCONF/starred.json" && ! -e "$KCONF/covers.json" &&
               ! -e "$KCONF/folder_styles.json" ]]' "BleachBit's clean empties Kestrel's cache and deletes its lists"
        check '[[ "$(cat "$HOME/Documents/notes.txt")" == "my own file" && -f "$HOME/.cache/thumbnails/normal/3c4d.png" &&
               -f "$HOME/.cache/other-app/data" && -f "$KCONF/install-state" && -f "$CLEANER" ]]' \
            "...and leaves everything else (a folder linked from the cache, GNOME's thumbnails, other apps' caches)"
        settings() { sed -E 's/^([^=[]*[^ ]) = /\1=/' "$1" | grep -v '^$'; }
        check '! grep -qE "^(recent_servers|chooser_folder) ?=" "$KCONF/kestrel-explorer.conf" &&
               [[ "$(settings "$KCONF/kestrel-explorer.conf")" == "$(settings "$WORK/conf.before" | grep -vE "^(recent_servers|chooser_folder)=")" ]]' \
            "...and removes the history from Kestrel's settings, keeping every other setting as it was"
    fi
else
    echo "SKIP BleachBit's dry run and clean with Kestrel's cleaner (BleachBit isn't installed)"
fi
printf '<?xml version="1.0"?>\n<cleaner id="mine"><label>Mine</label></cleaner>\n' > "$(dirname "$CLEANER")/mine.xml"
"$SETUP" --undo >/dev/null 2>&1
check '[[ ! -e "$CLEANER" && -f "$(dirname "$CLEANER")/mine.xml" ]]' \
    "kes-setup --undo: removes Kestrel's cleaner from BleachBit and leaves the user's own"

if (( fails )); then echo "FAILED ($fails failed)"; exit 1; fi
echo "ALL PASSED (0 failed)"
