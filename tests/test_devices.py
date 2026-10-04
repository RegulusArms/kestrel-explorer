"""Phones and cameras: recognising their paths, where the Overview lists them, the hints, and thumbnails on them."""
import os

from common import A, check, finish, home_path, setup_app, wait_for
from PyQt6.QtCore import QFileInfo, Qt
from PyQt6.QtGui import QImage

from kestrel import overview, thumbs, util
from kestrel.util import is_device_path

app = setup_app()

# a uid that has no /run/user folder, so nothing here reaches a real gvfs mount
gvfs = "/run/user/99999/gvfs/"

# -- paths on a phone
check(is_device_path(gvfs + "afc:host=00008140-000201913640801C,port=3/Documents/a.pdf"),
      "a file on an iPhone (afc) is on a device")
check(is_device_path(gvfs + "gphoto2:host=Apple_Inc._iPhone_00008140000201913640801C/DCIM/100APPLE/IMG_0001.HEIC"),
      "a photo on a camera or iPhone (gphoto2) is on a device")
check(is_device_path(gvfs + "mtp:host=Google_Pixel_8_1A2B3C/Internal shared storage/DCIM"),
      "a file on an Android phone (mtp) is on a device")
check(not is_device_path(gvfs + "smb-share:server=nas,share=media/film.mkv"), "a network share (smb) is not a device")
check(not is_device_path(util.HOME) and not is_device_path("/run/user/99999/gvfs-not/afc:host=x/a"),
      "a local folder is not a device")
check(util.needs_local_copy(gvfs + "gphoto2:host=Apple_Inc._iPhone_00008140000201913640801C/202608_a/IMG_2183.MOV"),
      "a video from an iPhone's photos (gphoto2) is copied before it plays")
check(not util.needs_local_copy(gvfs + "afc:host=00008140-000201913640801C,port=3/VLC/clip.mov")
      and not util.needs_local_copy(gvfs + "mtp:host=Google_Pixel_8_1A2B3C/DCIM/Camera/clip.mp4")
      and not util.needs_local_copy(util.HOME),
      "videos from an iPhone's app files (afc), Android (mtp) or a local folder play in place")

# -- where the Overview lists mounts
check(overview.is_phone_scheme("afc") and overview.is_phone_scheme("gphoto2") and overview.is_phone_scheme("mtp")
      and not overview.is_phone_scheme("smb") and not overview.is_phone_scheme("file"),
      "afc, gphoto2 and mtp are phones; smb and file aren't")
check(overview.phone_kind("gphoto2") == "Photos and videos" and overview.phone_kind("afc") == "Files",
      "an iPhone's photos are labelled Photos and videos, its apps' files Files")
check(overview.mount_group("gphoto2", gvfs + "gphoto2:host=X", True, False) == "skip",
      "a shadowed mount is skipped (no duplicate cards)")
check(overview.mount_group("afc", gvfs + "afc:host=X,port=3", False, False) == "phone"
      and overview.mount_group("mtp", gvfs + "mtp:host=X", False, False) == "phone",
      "a phone goes under Phones & Cameras, not Network")
check(overview.mount_group("smb", gvfs + "smb-share:server=nas,share=media", False, False) == "network",
      "an smb share goes under Network")
check(overview.mount_group("file", "/media/usb", False, True) == "local",
      "a scanned local drive stays under Drives")

# -- hints
check("Trust" in overview.phone_hint("afc", "Documents on Sam’s iPhone")
      and "Trust" in overview.phone_hint("gphoto2", "iPhone"),
      "an iPhone that won't mount asks to tap Trust")
check("File transfer" in overview.phone_hint("mtp", "Pixel 8"), "an Android phone asks for File transfer")
check("PTP" in overview.phone_hint("gphoto2", "Canon EOS R6"), "a camera asks for PTP mode")
check("Trust" in overview.NO_PHONES_HINT and "File transfer" in overview.NO_PHONES_HINT,
      "the Overview explains how to connect a phone when none is connected")

# -- thumbnails on a phone
t = A._thumbs
folder = gvfs + "gphoto2:host=Test/DCIM/100APPLE"
before = len(t.failed)
check(t.folder_pixmap(folder, 1, 128) is None and len(t.failed) == before + 1,
      "no folder previews on a phone (they would download every file)")
check(util.device_uri(folder + "/IMG_0001.JPG") is None and thumbs.device_preview(None, 128) is None,
      "a file outside any mount has no device preview")
before = len(t.failed)
photo = folder + "/IMG_0001.JPG"
check(t.get(photo, 1, False, 128, 2000000) is None and len(t.failed) == before,
      "a photo on a phone is queued for a thumbnail")
check(wait_for(lambda: len(t.failed) == before + 1, 10000),
      "a photo that can't be read from the phone fails without hanging")
local = home_path("local.jpg")
img = QImage(64, 48, QImage.Format.Format_RGB32)
img.fill(Qt.GlobalColor.darkCyan)
img.save(local, "JPG")
mtime = QFileInfo(local).lastModified().toSecsSinceEpoch() - 60  # not "still being written"
t.get(local, mtime, False, 128, os.path.getsize(local))
check(wait_for(lambda: t.get(local, mtime, False, 128, os.path.getsize(local)) is not None, 10000),
      "local photos still get thumbnails")

finish()
