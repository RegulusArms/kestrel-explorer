"""Generate the benchmark data set (see README.md). Usage: make_data.py DIR

  gallery/   600 JPEGs, 1600×1200, with camera EXIF (Make, Model, exposure, ISO, dates)
  albums/    150 folders of 4 images each (hard links to gallery images, so each has its own path)
  small/     a small folder to start in: 20 text files and 5 small images
  tree/      50,000 empty files in 500 folders (2 levels), 500 of them named *_7.jpg
  copysrc/   20,000 files of 1–4 KB in 200 folders, plus 5 × 50 MB
  meta/      200 PNGs with Stable Diffusion "parameters" text
  flat/      10,000 files of 1–2 KB in one folder (moving to the trash, emptying it)
  videos/    40 MP4 videos, 5 s at 1280×720 (needs ffmpeg)
  pdfs/      40 PDFs of 3 pages
  windows/   5 folders of 100 small files (opening several windows)

Each part is made once; a part whose version changes below is made again.
"""
import os
import random
import shutil
import struct
import subprocess
import sys

from PyQt6.QtCore import QMarginsF, QPointF, QRectF
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPageSize, QPainter, QPdfWriter


def _ifd(entries, base):
    """A TIFF IFD (little-endian) at offset `base`: entries are (tag, type, count, value bytes)."""
    head = 2 + len(entries) * 12 + 4
    out, data = struct.pack("<H", len(entries)), b""
    for tag, typ, count, val in sorted(entries):
        if len(val) <= 4:
            out += struct.pack("<HHI", tag, typ, count) + val.ljust(4, b"\0")
        else:
            out += struct.pack("<HHII", tag, typ, count, base + head + len(data))
            data += val + (b"\0" if len(val) % 2 else b"")
    return out + struct.pack("<I", 0) + data


def _ascii(tag, s):
    v = s.encode() + b"\0"
    return (tag, 2, len(v), v)


def exif_segment(i):
    """An APP1 Exif segment like a camera's."""
    dt = f"2024:05:{1 + i % 28:02d} 10:{i % 60:02d}:00"
    exif = [(0x829A, 5, 1, struct.pack("<II", 1, 200)),     # ExposureTime 1/200
            (0x829D, 5, 1, struct.pack("<II", 28, 10)),     # FNumber 2.8
            (0x8827, 3, 1, struct.pack("<H", 200)),         # ISO
            _ascii(0x9003, dt),                             # DateTimeOriginal
            (0x920A, 5, 1, struct.pack("<II", 50, 1))]      # FocalLength 50 mm
    ifd0 = [_ascii(0x010F, "Kestrel"), _ascii(0x0110, "Bench Camera"), _ascii(0x0132, dt),
            (0x8769, 4, 1, struct.pack("<I", 0))]
    size0 = len(_ifd(ifd0, 8))
    ifd0[-1] = (0x8769, 4, 1, struct.pack("<I", 8 + size0))  # pointer to the Exif IFD that follows
    tiff = b"II*\0" + struct.pack("<I", 8) + _ifd(ifd0, 8) + _ifd(exif, 8 + size0)
    payload = b"Exif\0\0" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def picture(w, h, seed):
    rnd = random.Random(seed)
    img = QImage(w, h, QImage.Format.Format_RGB32)
    p = QPainter(img)
    g = QLinearGradient(QPointF(0, 0), QPointF(w, h))
    g.setColorAt(0, QColor(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    g.setColorAt(1, QColor(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    p.fillRect(img.rect(), g)
    for _ in range(250):
        p.setBrush(QColor(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256), rnd.randrange(80, 256)))
        p.drawEllipse(rnd.randrange(w), rnd.randrange(h), rnd.randrange(10, w // 5), rnd.randrange(10, h // 5))
    p.end()
    return img


def make_gallery(root):
    os.makedirs(f"{root}/gallery", exist_ok=True)
    for i in range(600):
        path = f"{root}/gallery/img{i:04d}.jpg"
        picture(1600, 1200, i).save(path, "JPEG", 88)
        with open(path, "rb") as f:
            data = f.read()
        with open(path, "wb") as f:
            f.write(data[:2] + exif_segment(i) + data[2:])


def make_albums(root):
    for a in range(150):
        d = f"{root}/albums/album{a:03d}"
        os.makedirs(d, exist_ok=True)
        for k in range(4):
            dst = f"{d}/photo{k}.jpg"
            if not os.path.exists(dst):
                os.link(f"{root}/gallery/img{a * 4 + k:04d}.jpg", dst)


def make_small(root):
    os.makedirs(f"{root}/small", exist_ok=True)
    for i in range(20):
        with open(f"{root}/small/note{i:02d}.txt", "w") as f:
            f.write("note\n" * 20)
    for i in range(5):
        picture(320, 240, 1000 + i).save(f"{root}/small/pic{i}.jpg", "JPEG", 85)


def make_tree(root):
    for a in range(50):
        for b in range(10):
            d = f"{root}/tree/a{a:02d}/b{b}"
            os.makedirs(d, exist_ok=True)
            for k in range(100):   # img_7.jpg: one per folder
                open(f"{d}/img_{k}.jpg" if k % 2 else f"{d}/doc_{k}.txt", "w").close()


def make_copysrc(root):
    rnd = random.Random(1)
    for a in range(200):
        d = f"{root}/copysrc/dir{a:03d}"
        os.makedirs(d, exist_ok=True)
        for k in range(100):
            with open(f"{d}/file{k:03d}.dat", "wb") as f:
                f.write(rnd.randbytes(rnd.randrange(1000, 4000)))
    for k in range(5):
        with open(f"{root}/copysrc/big{k}.bin", "wb") as f:
            f.write(os.urandom(50 * 1024 * 1024))


def make_meta(root):
    os.makedirs(f"{root}/meta", exist_ok=True)
    for i in range(200):
        img = picture(512, 512, 2000 + i)
        img.setText("parameters", f"a kestrel over a valley, golden hour, highly detailed {i}\n"
                                  "Negative prompt: blurry, low quality\n"
                                  f"Steps: 30, Sampler: DPM++ 2M Karras, CFG scale: 7, Seed: {1000 + i}, "
                                  "Size: 512x512, Model: sd_xl_base_1.0")
        img.save(f"{root}/meta/gen{i:03d}.png", "PNG")


def make_flat(root):
    rnd = random.Random(2)
    os.makedirs(f"{root}/flat", exist_ok=True)
    for k in range(10000):
        with open(f"{root}/flat/item{k:05d}.txt", "wb") as f:
            f.write(rnd.randbytes(rnd.randrange(1000, 2000)))


def make_videos(root):
    if not shutil.which("ffmpeg"):
        print("  (skipping videos: ffmpeg isn't installed)")
        return False
    os.makedirs(f"{root}/videos", exist_ok=True)
    for i in range(40):
        hue = (i * 37) % 360
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"testsrc2=size=1280x720:rate=30:duration=5,hue=h={hue}", "-c:v", "libx264", "-preset",
                        "veryfast", "-pix_fmt", "yuv420p", f"{root}/videos/clip{i:02d}.mp4"], check=True)
    return True


def make_pdfs(root):
    os.makedirs(f"{root}/pdfs", exist_ok=True)
    for i in range(40):
        pdf = QPdfWriter(f"{root}/pdfs/doc{i:02d}.pdf")
        pdf.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        pdf.setPageMargins(QMarginsF(15, 15, 15, 15))
        p = QPainter(pdf)
        w, h = pdf.width(), pdf.height()
        rnd = random.Random(3000 + i)
        for page in range(3):
            if page:
                pdf.newPage()
            p.setFont(QFont("Sans", 24))
            p.drawText(QRectF(0, 0, w, h * 0.1), f"Document {i}, page {page + 1}")
            p.setFont(QFont("Sans", 10))
            p.drawText(QRectF(0, h * 0.12, w, h * 0.4), 0x1000, ("Lorem ipsum dolor sit amet, consectetur adipiscing "
                                                              "elit. " * 40))
            for _ in range(12):
                p.setBrush(QColor(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
                p.drawRect(QRectF(rnd.random() * w * 0.8, h * 0.55 + rnd.random() * h * 0.35, w * 0.15, h * 0.08))
        p.end()


def make_windows(root):
    for k in range(5):
        d = f"{root}/windows/folder{k}"
        os.makedirs(d, exist_ok=True)
        for n in range(100):
            with open(f"{d}/notes{n:03d}.txt", "w") as f:
                f.write("text\n" * 10)


# name -> (version, maker); bump a version to make that part again
PARTS = {
    "gallery": ("1", make_gallery), "albums": ("1", make_albums), "small": ("1", make_small),
    "tree": ("1", make_tree), "copysrc": ("1", make_copysrc), "meta": ("1", make_meta),
    "flat": ("1", make_flat), "videos": ("1", make_videos), "pdfs": ("1", make_pdfs),
    "windows": ("1", make_windows),
}
FIRST_SET = ("gallery", "albums", "small", "tree", "copysrc", "meta")   # made together by the first data version


def main(root):
    os.makedirs(root, exist_ok=True)
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1] + ["-platform", "offscreen"])   # for QPdfWriter
    old = os.path.join(root, ".bench-data")   # the first data version marked the whole set at once
    if os.path.exists(old) and open(old).read().strip() == "1":
        for name in FIRST_SET:
            with open(os.path.join(root, f".part-{name}"), "w") as f:
                f.write("1")
        os.unlink(old)
    todo = []
    for name, (version, _maker) in PARTS.items():
        marker = os.path.join(root, f".part-{name}")
        if not (os.path.exists(marker) and open(marker).read().strip() == version):
            todo.append(name)
    if not todo:
        print(f"Benchmark data already in {root}")
        return
    print(f"Generating benchmark data in {root}: {', '.join(todo)}…", flush=True)
    for name in todo:
        version, maker = PARTS[name]
        shutil.rmtree(os.path.join(root, name), ignore_errors=True)
        if maker(root) is not False:
            with open(os.path.join(root, f".part-{name}"), "w") as f:
                f.write(version)
    del app
    print("Done.")


if __name__ == "__main__":
    main(sys.argv[1])
