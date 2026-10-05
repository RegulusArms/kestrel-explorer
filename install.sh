#!/usr/bin/env bash
# Install Kestrel Explorer for the current user (command: kes).
#   ./install.sh                        install launcher + app menu entry
#   ./install.sh --default              also make it the default app for opening folders and the trash
#                                       and the system's file chooser for Save/Open dialogs (asks for sudo once)
#                                       and puts ~/.local/bin on your PATH (for the kes command)
#                                       (and offer to put it in the dock in place of GNOME Files)
#   ./install.sh --dock                 put it in the dock in place of GNOME Files (without asking)
#   ./install.sh --install-recommended  also install the recommended packages (RAW/HEIC previews, network, drives…)
#   ./install.sh --uninstall
# --default, --dock and --install-recommended can be combined.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin/kes"
DESKTOP="$HOME/.local/share/applications/kestrel-explorer.desktop"
# --default and --dock (and putting back what they changed) are done by kes-setup, which the .deb installs as well
SETUP="$HERE/kes-setup"
# the file chooser's portal definition (xdg-desktop-portal reads those only from /usr/share): --default installs it
# with sudo, --uninstall removes it
PORTAL_DIR="${KESTREL_PORTAL_DIR:-/usr/share/xdg-desktop-portal/portals}"
PORTAL_FILE="$PORTAL_DIR/kestrel.portal"
# ~/.local/bin (where `kes` goes) on the PATH of new terminals: a marked block in the shells' startup files.
# Ubuntu's ~/.profile only adds it if it existed at login, so a first install wouldn't find `kes` until you log in again.
PATH_BEGIN="# >>> kestrel-explorer: ~/.local/bin on PATH for the kes command (install.sh --default; --uninstall removes this)"
PATH_END="# <<< kestrel-explorer"
FISH_PATH="$HOME/.config/fish/conf.d/kestrel-explorer.fish"
# previous name of this app
LEGACY_BIN="$HOME/.local/bin/folder-explorer"
LEGACY_DESKTOP="$HOME/.local/share/applications/folder-explorer.desktop"

# Recommended apt packages (see "Dependencies" in README.md). Most come with a standard Ubuntu desktop;
# kimageformat6-plugins (camera RAW, HEIC, AVIF, JPEG XL, PSD previews) usually doesn't.
RECOMMENDED=(libglib2.0-bin xdg-utils gvfs gvfs-backends udisks2 qt6-image-formats-plugins 'qt6-svg-plugins|libqt6svg6'
             kimageformat6-plugins adwaita-icon-theme qt6-gtk-platformtheme
             '7zip|p7zip-full' unrar zip unzip pigz zpaq zstd xz-utils bzip2 lzip)   # archives (unrar is in multiverse)
# a|b: the first of these the system has (7zip is p7zip-full on Debian 12 and older Ubuntu releases; the SVG plugins
# come with libqt6svg6 on Ubuntu 24.04 / Linux Mint 22)

DEFAULT=0 DOCK=0 WITH_RECOMMENDED=0 UNINSTALL=0
for arg in "$@"; do
    case "$arg" in
        --default) DEFAULT=1 ;;
        --dock) DOCK=1 ;;
        --install-recommended) WITH_RECOMMENDED=1 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help) sed -n '2,12s/^# \{0,1\}//p' "$0"; exit 0 ;;
        *) echo "Unknown option: $arg (see ./install.sh --help)" >&2; exit 2 ;;
    esac
done

path_files() {   # the shell startup files to put the PATH block in: bash's, and zsh's if it's set up
    echo "$HOME/.bashrc"
    [[ -f "$HOME/.zshrc" ]] && echo "$HOME/.zshrc"
    return 0
}
remove_path_block() {   # remove_path_block FILE: drop our block, keep everything else
    [[ -f "$1" ]] && grep -qF "$PATH_BEGIN" "$1" || return 0
    awk -v begin="$PATH_BEGIN" -v end="$PATH_END" '
        $0 == begin { skip = 1; next }
        skip && $0 == end { skip = 0; next }
        !skip' "$1" > "$1.kestrel-tmp" && mv "$1.kestrel-tmp" "$1"
}
portal_root() {   # run a command on the portal folder: with sudo unless it is writable
    if [[ -w "$PORTAL_DIR" ]]; then "$@"; else sudo "$@"; fi
}

if (( UNINSTALL )); then
    rm -f "$BIN" "$DESKTOP" "$LEGACY_BIN" "$LEGACY_DESKTOP"
    # the portal definition, unless the .deb installed it (apt removes that one)
    if [[ -f "$PORTAL_FILE" ]] && ! dpkg -S "$PORTAL_FILE" >/dev/null 2>&1; then
        portal_root rm -f "$PORTAL_FILE" || echo "Couldn't remove $PORTAL_FILE; delete it with sudo." >&2
    fi
    "$SETUP" --undo
    # ~/.local/bin on PATH
    while read -r f; do remove_path_block "$f"; done < <(path_files)
    rm -f "$FISH_PATH"
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
    echo "Uninstalled."
    exit 0
fi

# dependencies (all in Ubuntu's main archive)
missing=()
/usr/bin/python3 -c "import PyQt6.QtWidgets" 2>/dev/null || missing+=(python3-pyqt6)
/usr/bin/python3 -c "import PIL" 2>/dev/null || missing+=(python3-pil)
/usr/bin/python3 -c "import gi" 2>/dev/null || missing+=(python3-gi)
missing_rec=() unavailable=()
apt_has() { apt-cache policy "$1" 2>/dev/null | grep -q 'Candidate: [^(]'; }   # in the system's package lists
for entry in "${RECOMMENDED[@]}"; do
    IFS='|' read -r -a alts <<< "$entry"
    have=0 pick=""
    for p in "${alts[@]}"; do
        dpkg -s "$p" >/dev/null 2>&1 && have=1
        [[ -z "$pick" ]] && apt_has "$p" && pick="$p"
    done
    (( have )) && continue
    if [[ -n "$pick" ]]; then
        missing_rec+=("$pick")
    else   # e.g. kimageformat6-plugins on Ubuntu 24.04 / Linux Mint 22
        unavailable+=("${alts[0]}")
    fi
done
if (( ${#unavailable[@]} )); then
    echo "Not available on this system (skipped): ${unavailable[*]}"
fi
if (( WITH_RECOMMENDED )); then
    missing+=("${missing_rec[@]}")
fi
if (( ${#missing[@]} )); then
    echo "Installing: ${missing[*]}"
    sudo apt-get install -y "${missing[@]}"
fi
if (( ! WITH_RECOMMENDED && ${#missing_rec[@]} )); then
    echo "Tip: recommended packages not installed: ${missing_rec[*]}"
    echo "     Run './install.sh --install-recommended' to add them (RAW/HEIC previews need kimageformat6-plugins)."
fi
command -v exiftool >/dev/null || echo "Tip: 'sudo apt install libimage-exiftool-perl' for full metadata."
command -v ffmpeg >/dev/null || echo "Tip: 'sudo apt install ffmpeg' for video thumbnails."

mkdir -p "$(dirname "$BIN")" "$(dirname "$DESKTOP")"
rm -f "$LEGACY_BIN" "$LEGACY_DESKTOP"
ln -sf "$HERE/kes" "$BIN"
cat > "$DESKTOP" <<DESK
[Desktop Entry]
Type=Application
Name=Kestrel Explorer
GenericName=File Manager
Comment=Manage files, with archive, admin, permission and metadata tools built in
Exec=$BIN %U
Icon=folder
Terminal=false
Categories=System;FileTools;FileManager;Viewer;
MimeType=inode/directory;x-directory/normal;x-scheme-handler/trash;
StartupWMClass=kestrel-explorer
Actions=new-window;

[Desktop Action new-window]
Name=New Window
Exec=$BIN
DESK
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

if (( DEFAULT || DOCK )); then
    setup_args=(--kes "$BIN")
    (( DEFAULT )) && setup_args+=(--default)
    (( DOCK )) && setup_args+=(--dock)
    "$SETUP" "${setup_args[@]}"
fi
if (( DEFAULT )); then
    # `kes` in terminals: ~/.local/bin on the PATH of new ones
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *)
            added=()
            while read -r f; do
                grep -qsF "$PATH_BEGIN" "$f" && continue
                printf '\n%s\ncase ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac\n%s\n' \
                    "$PATH_BEGIN" "$PATH_END" >> "$f"
                added+=("${f/#$HOME/\~}")
            done < <(path_files)
            if [[ -d "$HOME/.config/fish" && ! -f "$FISH_PATH" ]]; then
                mkdir -p "$(dirname "$FISH_PATH")"
                printf '%s\ncontains ~/.local/bin $PATH; or set -gx PATH ~/.local/bin $PATH\n' "$PATH_BEGIN" > "$FISH_PATH"
                added+=("${FISH_PATH/#$HOME/\~}")
            fi
            (( ${#added[@]} )) && echo "Added ~/.local/bin to your PATH (${added[*]}): open a new terminal to use 'kes'."
            ;;
    esac
fi
echo "Installed. Launch 'Kestrel Explorer' from the app grid or run: kes [path]"
if (( ! DEFAULT )); then
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *) echo "Note: ~/.local/bin isn't on your PATH, so 'kes' won't work in a terminal yet (--default adds it)." ;;
    esac
fi
