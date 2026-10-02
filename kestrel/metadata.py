"""Image metadata: EXIF summary, AI-generation parameters, full exiftool dump."""
import json
import os
import re
import shutil
import subprocess

try:
    from PIL import ExifTags, Image
except Exception:  # pragma: no cover
    Image = None


_TAG_IDS = {v: k for k, v in ExifTags.TAGS.items()} if Image else {}


def _ratio(v):
    try:
        return float(v)
    except Exception:
        return None


def basic_info(path):
    """Fast summary for the info panel. Returns list of (label, value)."""
    out = []
    if Image is None:
        return out
    try:
        with Image.open(path) as im:
            out.append(("Dimensions", f"{im.width} × {im.height}"))
            out.append(("Format", f"{im.format} ({im.mode})"))
            n = getattr(im, "n_frames", 1)
            if n > 1:
                out.append(("Frames", str(n)))
            exif = im.getexif()
            sub = exif.get_ifd(0x8769) if exif else {}
            def g(tag):
                tid = _TAG_IDS.get(tag)
                return sub.get(tid) if sub.get(tid) is not None else exif.get(tid)
            make, model = g("Make"), g("Model")
            if model:
                out.append(("Camera", f"{make or ''} {model}".strip()))
            if g("LensModel"):
                out.append(("Lens", str(g("LensModel"))))
            if g("DateTimeOriginal") or g("DateTime"):
                out.append(("Taken", str(g("DateTimeOriginal") or g("DateTime"))))
            exp = _ratio(g("ExposureTime"))
            if exp:
                out.append(("Exposure", f"1/{round(1 / exp)} s" if exp < 1 else f"{exp:g} s"))
            fn = _ratio(g("FNumber"))
            if fn:
                out.append(("Aperture", f"f/{fn:g}"))
            if g("ISOSpeedRatings"):
                out.append(("ISO", str(g("ISOSpeedRatings"))))
            fl = _ratio(g("FocalLength"))
            if fl:
                out.append(("Focal length", f"{fl:g} mm"))
            if exif and exif.get_ifd(0x8825):
                out.append(("GPS", "yes"))
    except Exception:
        pass
    return out


def ai_info(path):
    """Extract Stable Diffusion / ComfyUI generation info from PNG/WebP/JPEG text metadata."""
    if Image is None:
        return {}
    try:
        with Image.open(path) as im:
            info = dict(im.info)
            try:
                exif = im.getexif()
                uc = exif.get_ifd(0x8769).get(0x9286)  # UserComment
                if uc and "parameters" not in info:
                    if isinstance(uc, bytes):
                        uc = uc[8:].decode("utf-16-be" if uc.startswith(b"UNICODE") else "utf-8", "ignore")
                    info["parameters"] = uc
            except Exception:
                pass
    except Exception:
        return {}
    res = {}
    params = info.get("parameters")
    if isinstance(params, str) and params.strip():
        text = params.strip()
        prompt, neg, settings = text, "", ""
        if "\nSteps:" in text or text.startswith("Steps:"):
            idx = text.rfind("Steps:")
            prompt, settings = text[:idx].strip(), text[idx:].strip()
        if "Negative prompt:" in prompt:
            prompt, neg = prompt.split("Negative prompt:", 1)
        res["Prompt"] = prompt.strip()
        if neg.strip():
            res["Negative prompt"] = neg.strip()
        if settings:
            res["Settings"] = settings
    prompt_json = info.get("prompt")
    if isinstance(prompt_json, str):
        try:
            graph = json.loads(prompt_json)
            texts, settings = [], []
            for node in graph.values():
                ct = node.get("class_type", "")
                inputs = node.get("inputs", {})
                if "TextEncode" in ct or ct in ("CLIPTextEncode", "PrimitiveStringMultiline", "String Literal"):
                    for k in ("text", "text_g", "text_l", "value", "string"):
                        if isinstance(inputs.get(k), str) and inputs[k].strip():
                            texts.append(inputs[k].strip())
                if ct.startswith("KSampler") or "Sampler" in ct:
                    parts = [f"{k}: {inputs[k]}" for k in ("seed", "noise_seed", "steps", "cfg", "sampler_name",
                                                            "scheduler", "denoise") if k in inputs
                             and not isinstance(inputs[k], list)]
                    if parts:
                        settings.append(", ".join(parts))
                if "CheckpointLoader" in ct or ct in ("UNETLoader", "LoraLoader"):
                    for k in ("ckpt_name", "unet_name", "lora_name"):
                        if isinstance(inputs.get(k), str):
                            settings.append(f"{k}: {inputs[k]}")
            if texts:
                res.setdefault("Prompt", texts[0])
                if len(texts) > 1:
                    res.setdefault("Other prompts", "\n---\n".join(texts[1:]))
            if settings:
                res.setdefault("Settings", "\n".join(settings))
        except Exception:
            pass
    if "workflow" in info:
        res["ComfyUI workflow"] = "embedded (see Metadata tab)"
    for k in ("Description", "Comment", "Software"):
        v = info.get(k) or info.get(k.lower())
        if isinstance(v, str) and v.strip() and k not in res:
            res[k] = v.strip()
    return res


def full_metadata(path):
    """List of (group, tag, value) with everything we can find."""
    rows = []
    if shutil.which("exiftool"):
        try:
            out = subprocess.run(["exiftool", "-j", "-G1", "-a", "-charset", "filename=utf8", path],
                                 capture_output=True, text=True, timeout=30).stdout
            data = json.loads(out)[0]
            for key, val in data.items():
                if key == "SourceFile":
                    continue
                group, _, tag = key.partition(":")
                if not tag:
                    group, tag = "", group
                rows.append((group, tag, val if isinstance(val, str) else json.dumps(val)))
            return rows
        except Exception:
            rows = []
    if Image is None:
        return rows
    try:
        with Image.open(path) as im:
            rows += [("Image", "Format", str(im.format)), ("Image", "Mode", im.mode),
                     ("Image", "Size", f"{im.width} × {im.height}")]
            for k, v in im.info.items():
                if isinstance(v, (bytes, bytearray)):
                    v = f"(binary, {len(v)} bytes)"
                rows.append(("Info", str(k), str(v)))
            exif = im.getexif()
            for tid, v in exif.items():
                rows.append(("EXIF", ExifTags.TAGS.get(tid, hex(tid)), str(v)))
            for ifd, group, names in ((0x8769, "EXIF", ExifTags.TAGS), (0x8825, "GPS", ExifTags.GPSTAGS)):
                for tid, v in exif.get_ifd(ifd).items():
                    if isinstance(v, bytes):
                        v = f"(binary, {len(v)} bytes)"
                    rows.append((group, names.get(tid, hex(tid)), str(v)))
    except Exception as e:
        rows.append(("Error", "", str(e)))
    return rows


# ---------------------------------------------------------------- editing (exiftool)

# exiftool groups that describe the file itself or are computed; writing File:FileName etc. would rename/move it
READONLY_GROUPS = {"", "ExifTool", "System", "File", "Composite"}


def can_edit():
    return shutil.which("exiftool") is not None


def is_editable(group, value="", tag=""):
    if group == "File" and tag == "Comment":  # JPEG comment segment
        return True
    return group not in READONLY_GROUPS and not value.startswith("(Binary data")


def as_list(value):
    """The items of a list-type value as shown by full_metadata(), or None if it isn't a list."""
    if value.startswith("["):
        try:
            items = json.loads(value)
            if isinstance(items, list):
                return [i if isinstance(i, str) else json.dumps(i) for i in items]
        except ValueError:
            pass
    return None


def _exiftool_write(path, args):
    """Run an exiftool write in place; raises RuntimeError on failure, returns a list of warnings."""
    r = subprocess.run(["exiftool", "-overwrite_original", "-charset", "filename=utf8", *args, os.path.abspath(path)],
                       capture_output=True, text=True, timeout=300)
    out = r.stdout + r.stderr
    msgs = [ln.strip() for ln in out.splitlines() if ln.strip().startswith(("Warning:", "Error:"))]
    if r.returncode != 0 or "0 image files updated" in out or "Nothing to do" in out:
        raise RuntimeError("\n".join(msgs) or out.strip() or "exiftool failed")
    return msgs


def set_tags(path, changes):
    """changes: list of (key, value) where key is "Group:Tag" or "Tag"; value is a str,
    a list of str (list-type tags), or None to remove the tag."""
    args = []
    for key, value in changes:
        if value is None:
            args.append(f"-{key}=")
        elif isinstance(value, list):
            args += [f"-{key}={v}" for v in value] or [f"-{key}="]
        else:
            args.append(f"-{key}={value}")
    return _exiftool_write(path, args)


def clear_all(path, keep_basic=True):
    """Strip all writable metadata. keep_basic keeps orientation and the colour profile so the image looks the same."""
    args = ["-all="]
    if keep_basic:
        args += ["-tagsfromfile", "@", "-icc_profile", "-orientation"]
    return [m for m in _exiftool_write(path, args) if "No writable tags set" not in m]


# ---------------------------------------------------------------- tag catalog for "Add Tag"

# Metadata standards ("families") exiftool can write into each file type
_FAMILIES_BY_EXT = {}
for _exts, _fams in (
        ("jpg jpeg jpe mpo", ("EXIF", "XMP", "IPTC", "File")),
        ("tif tiff dng cr2 cr3 nef nrw arw sr2 orf rw2 raf pef srw erf mrw x3f iiq 3fr psd psb",
         ("EXIF", "XMP", "IPTC")),
        ("png apng", ("PNG", "EXIF", "XMP")),
        ("webp heic heif hif avif jxl jp2 j2k jpx", ("EXIF", "XMP")),
        ("gif", ("GIF", "XMP")),
        ("mp4 mov m4v qt 3gp 3g2 lrv insv", ("QuickTime", "XMP")),
        ("m4a m4b aax", ("QuickTime",)),
        ("pdf ai", ("PDF", "XMP"))):
    for _e in _exts.split():
        _FAMILIES_BY_EXT[_e] = _fams

# exiftool groups listed under "All … tags" for each family
_FAMILY_GROUPS = {
    "EXIF": ["EXIF"], "IPTC": ["IPTC"], "PNG": ["PNG"], "QuickTime": ["QuickTime"], "PDF": ["PDF"], "GIF": ["GIF"],
    "XMP": ["XMP-dc", "XMP-xmp", "XMP-photoshop", "XMP-iptcCore", "XMP-iptcExt", "XMP-xmpRights", "XMP-lr"],
}

_DATE = " Format: YYYY:MM:DD HH:MM:SS."

# (family, "Group:Tag", what it's for) — the common tags offered first
TAG_CATALOG = [
    ("EXIF", "EXIF:ImageDescription", "Title or caption describing the image."),
    ("EXIF", "EXIF:Artist", "Name of the photographer or creator."),
    ("EXIF", "EXIF:Copyright", "Copyright notice, e.g. “© 2026 Jane Doe. All rights reserved.”"),
    ("EXIF", "EXIF:UserComment", "Free-form comment. Stable Diffusion tools store generation parameters here in JPEGs."),
    ("EXIF", "EXIF:DateTimeOriginal", "When the photo was taken." + _DATE),
    ("EXIF", "EXIF:CreateDate", "When the image was digitised (normally the same as when it was taken)." + _DATE),
    ("EXIF", "EXIF:ModifyDate", "When the image was last edited." + _DATE),
    ("EXIF", "EXIF:OffsetTimeOriginal", "Time-zone offset for DateTimeOriginal, e.g. +02:00."),
    ("EXIF", "EXIF:Orientation", "How viewers should rotate or flip the image when showing it."),
    ("EXIF", "EXIF:Make", "Manufacturer of the camera or phone."),
    ("EXIF", "EXIF:Model", "Model name of the camera or phone."),
    ("EXIF", "EXIF:LensMake", "Manufacturer of the lens."),
    ("EXIF", "EXIF:LensModel", "Lens used to take the photo."),
    ("EXIF", "EXIF:SerialNumber", "Serial number of the camera body."),
    ("EXIF", "EXIF:Software", "Program used to create or last edit the image."),
    ("EXIF", "EXIF:ExposureTime", "Shutter speed in seconds, e.g. 1/250."),
    ("EXIF", "EXIF:FNumber", "Aperture f-number, e.g. 2.8."),
    ("EXIF", "EXIF:ISO", "Sensor sensitivity (ISO speed), e.g. 400."),
    ("EXIF", "EXIF:FocalLength", "Lens focal length in mm, e.g. 50."),
    ("EXIF", "EXIF:GPSLatitude", "Latitude in degrees, e.g. 48.8584. Also set GPSLatitudeRef (North/South)."),
    ("EXIF", "EXIF:GPSLatitudeRef", "Hemisphere for GPSLatitude: N (north) or S (south)."),
    ("EXIF", "EXIF:GPSLongitude", "Longitude in degrees, e.g. 2.2945. Also set GPSLongitudeRef (East/West)."),
    ("EXIF", "EXIF:GPSLongitudeRef", "Hemisphere for GPSLongitude: E (east) or W (west)."),
    ("EXIF", "EXIF:GPSAltitude", "Altitude in metres above sea level."),
    ("EXIF", "EXIF:XPTitle", "Title shown in Windows Explorer's Details tab."),
    ("EXIF", "EXIF:XPComment", "Comments shown in Windows Explorer's Details tab."),
    ("EXIF", "EXIF:XPKeywords", "Tags shown in Windows Explorer, separated by semicolons."),
    ("EXIF", "EXIF:XPAuthor", "Authors shown in Windows Explorer, separated by semicolons."),
    ("EXIF", "EXIF:XPSubject", "Subject shown in Windows Explorer's Details tab."),
    ("File", "File:Comment", "JPEG comment segment — a plain-text note many tools display."),
    ("XMP", "XMP-dc:Title", "Title of the work (Lightroom, Bridge, digiKam, darktable…)."),
    ("XMP", "XMP-dc:Description", "Caption or description of the content."),
    ("XMP", "XMP-dc:Creator", "Creator(s) or author(s); one name per line."),
    ("XMP", "XMP-dc:Subject", "Keywords; one per line. The standard keyword field for photo managers."),
    ("XMP", "XMP-dc:Rights", "Copyright / usage rights statement."),
    ("XMP", "XMP-lr:HierarchicalSubject", "Nested keywords, e.g. “Places|France|Paris”; one per line."),
    ("XMP", "XMP-xmp:Rating", "Star rating 0–5 (−1 = rejected), used by Lightroom, digiKam, Windows and others."),
    ("XMP", "XMP-xmp:Label", "Colour label, e.g. Red, Green, Blue."),
    ("XMP", "XMP-xmp:CreateDate", "When the resource was created." + _DATE),
    ("XMP", "XMP-xmp:ModifyDate", "When the resource was last modified." + _DATE),
    ("XMP", "XMP-xmp:CreatorTool", "Application that created the file."),
    ("XMP", "XMP-photoshop:Headline", "Short headline summarising the content."),
    ("XMP", "XMP-photoshop:DateCreated", "When the content was created (date or date-time)."),
    ("XMP", "XMP-photoshop:City", "City where the photo was taken."),
    ("XMP", "XMP-photoshop:State", "State or province where the photo was taken."),
    ("XMP", "XMP-photoshop:Country", "Country where the photo was taken."),
    ("XMP", "XMP-photoshop:Credit", "Credit line required when publishing."),
    ("XMP", "XMP-photoshop:Source", "Original owner or supplier of the content."),
    ("XMP", "XMP-photoshop:Instructions", "Special instructions, e.g. embargoes or usage restrictions."),
    ("XMP", "XMP-iptcCore:Location", "Sub-location (venue, landmark or neighbourhood)."),
    ("XMP", "XMP-iptcCore:CountryCode", "ISO country code, e.g. FR or USA."),
    ("XMP", "XMP-xmpRights:UsageTerms", "Licence or terms for using the content."),
    ("XMP", "XMP-xmpRights:WebStatement", "URL of a web page with copyright / licence details."),
    ("XMP", "XMP-xmpRights:Marked", "True if the content is copyrighted, False if public domain."),
    ("IPTC", "IPTC:ObjectName", "Short title (IPTC). Used by news agencies and older photo software."),
    ("IPTC", "IPTC:Caption-Abstract", "Caption / description (IPTC)."),
    ("IPTC", "IPTC:Keywords", "Keywords (IPTC); one per line."),
    ("IPTC", "IPTC:By-line", "Photographer / creator (IPTC)."),
    ("IPTC", "IPTC:CopyrightNotice", "Copyright notice (IPTC)."),
    ("IPTC", "IPTC:Headline", "Short headline (IPTC)."),
    ("IPTC", "IPTC:Credit", "Credit line (IPTC)."),
    ("IPTC", "IPTC:Source", "Original owner of the content (IPTC)."),
    ("IPTC", "IPTC:City", "City (IPTC)."),
    ("IPTC", "IPTC:Province-State", "State or province (IPTC)."),
    ("IPTC", "IPTC:Country-PrimaryLocationName", "Country (IPTC)."),
    ("IPTC", "IPTC:DateCreated", "Date the content was created (IPTC). Format: YYYY:MM:DD."),
    ("IPTC", "IPTC:SpecialInstructions", "Usage instructions or restrictions (IPTC)."),
    ("PNG", "PNG:Title", "Short title of the image (PNG text chunk)."),
    ("PNG", "PNG:Author", "Name of the image's creator."),
    ("PNG", "PNG:Description", "Description of the image."),
    ("PNG", "PNG:Comment", "Miscellaneous comment."),
    ("PNG", "PNG:Copyright", "Copyright notice."),
    ("PNG", "PNG:CreationTime", "When the original image was created." + _DATE),
    ("PNG", "PNG:Software", "Software used to create the image."),
    ("PNG", "PNG:Source", "Device used to create the image."),
    ("PNG", "PNG:Disclaimer", "Legal disclaimer."),
    ("PNG", "PNG:Parameters", "Stable Diffusion (A1111/Forge) generation parameters: prompt, negative prompt, settings."),
    ("QuickTime", "QuickTime:Title", "Title shown by media players."),
    ("QuickTime", "QuickTime:Artist", "Artist or creator."),
    ("QuickTime", "QuickTime:Author", "Author of the video."),
    ("QuickTime", "QuickTime:Director", "Director of the video."),
    ("QuickTime", "QuickTime:Album", "Album or collection the video/track belongs to."),
    ("QuickTime", "QuickTime:Genre", "Genre, e.g. Documentary."),
    ("QuickTime", "QuickTime:Year", "Release year."),
    ("QuickTime", "QuickTime:Comment", "Free-form comment."),
    ("QuickTime", "QuickTime:Description", "Description of the content."),
    ("QuickTime", "QuickTime:Keywords", "Keywords, separated by commas."),
    ("QuickTime", "QuickTime:Copyright", "Copyright notice."),
    ("QuickTime", "QuickTime:Rating", "Content rating."),
    ("QuickTime", "QuickTime:CreateDate", "When the video was recorded (stored as UTC)." + _DATE),
    ("QuickTime", "QuickTime:ContentCreateDate", "When the content was created, with time zone, e.g. 2026:10:02 14:00:00+02:00."),
    ("QuickTime", "QuickTime:GPSCoordinates", "Recording location: “latitude longitude [altitude]”, e.g. 48.8584 2.2945 35."),
    ("QuickTime", "QuickTime:Encoder", "Software used to encode the video."),
    ("PDF", "PDF:Title", "Document title shown by PDF viewers."),
    ("PDF", "PDF:Author", "Document author."),
    ("PDF", "PDF:Subject", "Document subject."),
    ("PDF", "PDF:Keywords", "Keywords for searching."),
    ("PDF", "PDF:Creator", "Application that created the original document."),
    ("PDF", "PDF:Producer", "Application that produced the PDF."),
    ("PDF", "PDF:CreateDate", "When the document was created." + _DATE),
    ("PDF", "PDF:ModifyDate", "When the document was last modified." + _DATE),
    ("GIF", "GIF:Comment", "Plain-text comment stored in the GIF."),
]

TAG_HELP = {key: blurb for _, key, blurb in TAG_CATALOG}

# list-type tags offered in the catalog: the editor writes one item per line
LIST_TAGS = {"XMP-dc:Creator", "XMP-dc:Subject", "XMP-lr:HierarchicalSubject", "IPTC:Keywords", "IPTC:By-line"}

_EXIF_GROUPS = {"IFD0", "IFD1", "ExifIFD", "GPS", "InteropIFD", "SubIFD", "EXIF"}
_QT_GROUPS = {"QuickTime", "ItemList", "Keys", "UserData"}


def tag_identity(key):
    """Normalise "Group:Tag" so a catalog key (EXIF:Artist) matches what full_metadata shows (IFD0:Artist)."""
    group, _, tag = key.rpartition(":")
    if group in _EXIF_GROUPS:
        group = "EXIF"
    elif group in _QT_GROUPS:
        group = "QuickTime"
    elif group.startswith("PNG"):
        group = "PNG"
    return group, tag.lower()


def tag_families(path):
    """Metadata families exiftool can write into this file, most relevant first; empty if it can't write it."""
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext in _FAMILIES_BY_EXT:
        return list(_FAMILIES_BY_EXT[ext])
    return ["XMP"] if ext.upper() in _writable_exts() else []


_wexts = None


def _writable_exts():
    global _wexts
    if _wexts is None:
        _wexts = set()
        if can_edit():
            try:
                out = subprocess.run(["exiftool", "-listwf"], capture_output=True, text=True, timeout=30).stdout
                _wexts = set(out.split()[3:])  # skip "Writable file extensions:"
            except Exception:
                pass
    return _wexts


_tag_db = None

_NUMBER_TYPES = re.compile(r"^(int\d+[su]|int16uRev|rational\d*[su]?|real|float|double|integer|digits|fixed32[su])$")
_CATEGORY = {"Author": "Author / rights info", "Camera": "Camera setting", "Location": "Location",
             "Time": "Date / time", "Image": "Image info", "Video": "Video info", "Audio": "Audio info",
             "Document": "Document info", "Preview": "Embedded preview"}


def _kind(group, name, typ, g2, has_values):
    """What a tag accepts, in plain words: Text, Numbers only, Date/time, Choice or True/False."""
    if has_values:
        return "Choice"
    if typ == "boolean":
        return "True/False"
    if typ == "date" or g2 == "Time":
        return "Date/time"
    if typ == "struct":
        return "Structured"
    if group == "EXIF" and name.startswith("XP"):  # stored as UCS-2 bytes but written as text
        return "Text"
    if _NUMBER_TYPES.match(typ or ""):
        return "Numbers only"
    return "Text"


def _parse_listx(xml_path, groups):
    """{(group, name): (description, kind, [allowed values])} from `exiftool -listx` for the given groups."""
    import xml.etree.ElementTree as ET
    wanted, out = set(groups), {}
    tg0 = tg1 = None
    for ev, el in ET.iterparse(xml_path, events=("start", "end")):
        if el.tag == "table" and ev == "start":
            tg0, tg1 = el.get("g0"), el.get("g1")
        elif el.tag == "tag" and ev == "end":
            name, typ = el.get("name"), el.get("type")
            g0, g1 = el.get("g0", tg0), el.get("g1", tg1)
            for g in {g0, g1} & wanted:
                prev = out.get((g, name))
                if el.get("writable") != "true" or (prev and prev[4] != "?"):
                    continue
                d = el.find("desc")
                values = [v.text for v in el.findall("values/key/val") if v.text]
                kind = _kind(g, name, typ, el.get("g2"), bool(values))
                cat = _CATEGORY.get(el.get("g2") or "", "")
                out[(g, name)] = (d.text if d is not None else name, kind, values[:40], cat, typ)
            el.clear()
        elif el.tag == "table" and ev == "end":
            el.clear()
    return {k: v[:4] for k, v in out.items()}


def tag_db():
    """{group: [[name, description, kind, allowed values, category], ...]} for every group in _FAMILY_GROUPS, cached on disk
    per exiftool version (slow the first time: ~6 s)."""
    global _tag_db
    if _tag_db is not None:
        return _tag_db
    from . import util
    try:
        ver = subprocess.run(["exiftool", "-ver"], capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return {}
    cache = util.APP_CACHE / f"exiftool-tagdb-{ver}.json"
    try:
        _tag_db = json.loads(cache.read_text())
        return _tag_db
    except (OSError, ValueError):
        pass
    groups = [g for gs in _FAMILY_GROUPS.values() for g in gs]
    args = []
    for g in groups:
        args += ["-listw", f"-{g}:All", "-execute"]
    out = subprocess.run(["exiftool", *args[:-1]], capture_output=True, text=True, timeout=120).stdout
    names, cur = {}, None
    for line in out.splitlines():
        m = re.match(r"Writable (\S+) tags:", line)
        if m:
            cur = names.setdefault(m.group(1), [])
        elif cur is not None:
            cur += [t for t in line.split() if not t.startswith("Unknown")]
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".xml") as tmp:
        subprocess.run(["exiftool", "-listx", "-lang", "en"], stdout=tmp, timeout=120)
        info = _parse_listx(tmp.name, groups)
    db = {g: [[n, *info.get((g, n), (_split_name(n), "Text", [], ""))] for n in sorted(set(ns), key=str.lower)]
          for g, ns in names.items()}
    _tag_db = db
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(db))
    except OSError:
        pass
    return db


def _split_name(name):
    """"DateTimeOriginal" -> "Date Time Original"."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name)


def _auto_blurb(desc, values, category):
    """Best-effort description for tags without a hand-written one, from exiftool's name, category and values."""
    text = (f"{category}: {desc}" if category else desc).rstrip(".") + "."
    if values:
        shown = ", ".join(values[:6]) + ("…" if len(values) > 6 else "")
        text += f" One of: {shown}."
    return text


def tag_choices(path, db=None):
    """[(heading, [(key, blurb, kind, allowed values)])] for the Add Tag picker: common tags first, then the rest."""
    fams = tag_families(path)
    db = db or {}
    info = {f"{g}:{row[0]}": row[1:] for g, rows in db.items() for row in rows}
    out = []
    common = []
    for f, k, b in TAG_CATALOG:
        if f in fams:
            _, kind, values, _ = info.get(k, ("", "Text", [], ""))
            common.append((k, b, "Text (list)" if k in LIST_TAGS else kind, values))
    if common:
        out.append(("Common tags", common))
    seen = {c[0] for c in common}
    for fam in fams:
        for g in _FAMILY_GROUPS.get(fam, []):
            items = [(f"{g}:{n}", _auto_blurb(desc, values, cat), kind, values)
                     for n, desc, kind, values, cat in db.get(g, []) if f"{g}:{n}" not in seen]
            if items:
                out.append((f"All {g} tags", items))
    return out
