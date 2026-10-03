#!/usr/bin/env bash
# Install Kestrel Explorer for the current user (command: kes).
#   ./install.sh                        install launcher + app menu entry
#   ./install.sh --default              also make it the default app for opening folders
#   ./install.sh --install-recommended  also install the recommended packages (RAW/HEIC previews, network, drives…)
#   ./install.sh --uninstall
# --default and --install-recommended can be combined.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin/kes"
DESKTOP="$HOME/.local/share/applications/kestrel-explorer.desktop"
# previous name of this app
LEGACY_BIN="$HOME/.local/bin/folder-explorer"
LEGACY_DESKTOP="$HOME/.local/share/applications/folder-explorer.desktop"

# Recommended apt packages (see "Dependencies" in README.md). Most come with a standard Ubuntu desktop;
# kimageformat6-plugins (camera RAW, HEIC, AVIF, JPEG XL, PSD previews) usually doesn't.
RECOMMENDED=(libglib2.0-bin xdg-utils gvfs gvfs-backends udisks2 qt6-image-formats-plugins qt6-svg-plugins
             kimageformat6-plugins adwaita-icon-theme
             7zip unrar zip unzip pigz zpaq zstd xz-utils bzip2 lzip)   # archives (unrar is in multiverse)

DEFAULT=0 WITH_RECOMMENDED=0 UNINSTALL=0
for arg in "$@"; do
    case "$arg" in
        --default) DEFAULT=1 ;;
        --install-recommended) WITH_RECOMMENDED=1 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help) sed -n '2,7s/^# \{0,1\}//p' "$0"; exit 0 ;;
        *) echo "Unknown option: $arg (see ./install.sh --help)" >&2; exit 2 ;;
    esac
done

if (( UNINSTALL )); then
    rm -f "$BIN" "$DESKTOP" "$LEGACY_BIN" "$LEGACY_DESKTOP"
    if [[ "$(xdg-mime query default inode/directory)" =~ ^(kestrel-explorer|folder-explorer)\.desktop$ ]]; then
        xdg-mime default org.gnome.Nautilus.desktop inode/directory
    fi
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
    echo "Uninstalled."
    exit 0
fi

# dependencies (all in Ubuntu's main archive)
missing=()
/usr/bin/python3 -c "import PyQt6.QtWidgets" 2>/dev/null || missing+=(python3-pyqt6)
/usr/bin/python3 -c "import PIL" 2>/dev/null || missing+=(python3-pil)
/usr/bin/python3 -c "import gi" 2>/dev/null || missing+=(python3-gi)
missing_rec=()
for p in "${RECOMMENDED[@]}"; do
    dpkg -s "$p" >/dev/null 2>&1 || missing_rec+=("$p")
done
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
Comment=Browse files and image galleries with folder previews
Exec=$BIN %U
Icon=folder
Terminal=false
Categories=System;FileTools;FileManager;Viewer;
MimeType=inode/directory;
StartupWMClass=kestrel-explorer
Actions=new-window;

[Desktop Action new-window]
Name=New Window
Exec=$BIN
DESK
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

if (( DEFAULT )); then
    xdg-mime default kestrel-explorer.desktop inode/directory
    echo "Set as default folder handler."
fi
echo "Installed. Launch 'Kestrel Explorer' from the app grid or run: kes [path]"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "Note: add ~/.local/bin to your PATH to use 'kes' in a terminal." ;; esac
