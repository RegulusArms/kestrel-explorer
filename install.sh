#!/usr/bin/env bash
# Install Kestrel Explorer for the current user (command: kes).
#   ./install.sh            install launcher + app menu entry
#   ./install.sh --default  also make it the default app for opening folders
#   ./install.sh --uninstall
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin/kes"
DESKTOP="$HOME/.local/share/applications/kestrel-explorer.desktop"
# previous name of this app
LEGACY_BIN="$HOME/.local/bin/folder-explorer"
LEGACY_DESKTOP="$HOME/.local/share/applications/folder-explorer.desktop"

if [[ "${1:-}" == "--uninstall" ]]; then
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
if (( ${#missing[@]} )); then
    echo "Installing: ${missing[*]}"
    sudo apt-get install -y "${missing[@]}"
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

if [[ "${1:-}" == "--default" ]]; then
    xdg-mime default kestrel-explorer.desktop inode/directory
    echo "Set as default folder handler."
fi
echo "Installed. Launch 'Kestrel Explorer' from the app grid or run: kes [path]"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "Note: add ~/.local/bin to your PATH to use 'kes' in a terminal." ;; esac
