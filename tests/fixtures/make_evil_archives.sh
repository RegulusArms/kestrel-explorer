#!/usr/bin/env bash
# Builds the crafted archives the fileops test extracts (R14 in the roadmap): each holds entries that try to write
# outside the folder it's extracted into. Kept in the repo (the same files in both projects); run this only to rebuild
# them (needs python3, 7z and rar).
#   ../escape.txt               a path that climbs out of the destination
#   /tmp/kestrel-evil-abs.txt   an absolute path
#   lnk -> ../outside           a symlink out of the destination, then
#   lnk/payload.txt             a file written through it
set -euo pipefail
OUT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
W="$(mktemp -d)"
trap 'rm -rf "$W"' EXIT
python3 - "$OUT" <<'PY'
import io, sys, tarfile, zipfile
out = sys.argv[1]
def add_file(tf, name, data):
    ti = tarfile.TarInfo(name)
    ti.size = len(data)
    tf.addfile(ti, io.BytesIO(data))
with tarfile.open(f"{out}/evil.tar.gz", "w:gz", format=tarfile.GNU_FORMAT) as tf:
    add_file(tf, "../escape.txt", b"escaped\n")
    add_file(tf, "/tmp/kestrel-evil-abs.txt", b"absolute\n")
    ti = tarfile.TarInfo("lnk")
    ti.type, ti.linkname = tarfile.SYMTYPE, "../outside"
    tf.addfile(ti)
    add_file(tf, "lnk/payload.txt", b"payload\n")
with zipfile.ZipFile(f"{out}/evil.zip", "w") as zf:
    zf.writestr("../escape.txt", "escaped\n")
    zf.writestr("/tmp/kestrel-evil-abs.txt", "absolute\n")
    zi = zipfile.ZipInfo("lnk")
    zi.create_system = 3                       # Unix, so the mode below counts
    zi.external_attr = (0o120777 << 16)        # a symlink; its data is the target
    zf.writestr(zi, "../outside")
    zf.writestr("lnk/payload.txt", "payload\n")
PY
# 7z and rar don't store "../" names; they get the absolute path (7z) and the symlink-then-file pair
mkdir -p "$W/a" "$W/b/lnk"
ln -s ../outside "$W/a/lnk"
echo payload > "$W/b/lnk/payload.txt"
echo absolute > /tmp/kestrel-evil-abs.txt
rm -f "$OUT/evil.7z" "$OUT/evil.rar"
(cd "$W/a" && 7z a -snl "$OUT/evil.7z" lnk >/dev/null && rar a -idq -ol "$OUT/evil.rar" lnk)
(cd "$W/b" && 7z a "$OUT/evil.7z" lnk/payload.txt >/dev/null && rar a -idq "$OUT/evil.rar" lnk/payload.txt)
7z a -spf "$OUT/evil.7z" /tmp/kestrel-evil-abs.txt >/dev/null
rm -f /tmp/kestrel-evil-abs.txt
ls -l "$OUT"/evil.*
