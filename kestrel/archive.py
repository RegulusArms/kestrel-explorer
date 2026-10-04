"""Archive engine: create and extract archives with the system's command-line tools.

Creating: 7z, zip, rar, tar (plain, or piped through pigz/gzip, pbzip2/lbzip2/bzip2, xz, zstd, plzip/lzip,
lz4), zpaq, and single-file gz/bz2/xz/zst/lz/lz4. Extracting: all of those plus iso/cab/deb/rpm/... through 7z.

Each job runs on a fileops.Task thread: tools are started detached from any terminal with stdin closed (so
nothing can sit waiting on a password prompt), progress is read from their "NN%" output or counted from the
bytes Kestrel pipes through them, Cancel kills the whole process group, and partial output is removed.

Passwords go to 7z on stdin. unrar, rar and zpaq only accept them as a command-line switch, which other
local users could read from the process list while the job runs.
"""
import os
import re
import select
import shlex
import shutil
import signal
import subprocess
import time

from . import util
from .fileops import Cancelled

_SYSTEM_PATH = "/usr/local/bin:/usr/bin:/bin"
# English messages (Kestrel parses them) but UTF-8 file names: under plain LC_ALL=C, rar stores non-ASCII names as
# garbage and unrar can't recreate them.
_ENV = dict(os.environ, LC_ALL="C.UTF-8")
_PERCENT = re.compile(rb"(\d{1,3})(?:\.\d+)?%")


class WrongPassword(Exception):
    pass


def tool(name):
    """Full path of a command-line tool, preferring the system's copy over e.g. conda's (unless Kestrel was started
    from an explicitly activated conda environment; see env.py)."""
    from .env import explicit_conda_env
    if explicit_conda_env():
        return shutil.which(name)
    return shutil.which(name, path=_SYSTEM_PATH) or shutil.which(name)


def _exe(name):
    """Path of a tool, or just its name (for previews of a command whose tool isn't installed)."""
    return tool(name) or name


def cpu_count():
    return os.cpu_count() or 1


# ---------------------------------------------------------------- formats you can create

# per compressor: (level min, max, default, threads switch builder or None)
COMPRESSORS = {
    "pigz": (1, 9, 6, lambda n: ["-p", str(n)]),
    "gzip": (1, 9, 6, None),
    "pbzip2": (1, 9, 9, lambda n: [f"-p{n}"]),
    "lbzip2": (1, 9, 9, lambda n: ["-n", str(n)]),
    "bzip2": (1, 9, 9, None),
    "xz": (0, 9, 6, lambda n: [f"-T{n}"]),
    "zstd": (1, 22, 3, lambda n: [f"-T{n}"]),
    "plzip": (0, 9, 6, lambda n: ["-n", str(n)]),
    "lzip": (0, 9, 6, None),
    "lz4": (1, 12, 1, None),
}
# compressed-tar / single-file flavours: suffix -> compressors in order of preference
STREAMS = {
    "gz": ["pigz", "gzip"],
    "bz2": ["pbzip2", "lbzip2", "bzip2"],
    "xz": ["xz"],
    "zst": ["zstd"],
    "lz": ["plzip", "lzip"],
    "lz4": ["lz4"],
}
STREAM_NAMES = {"gz": "gzip", "bz2": "bzip2", "xz": "XZ", "zst": "Zstandard", "lz": "lzip", "lz4": "LZ4"}

LEVEL_NAMES_7Z = {0: "Store", 1: "Fastest", 3: "Fast", 5: "Normal", 7: "Maximum", 9: "Ultra"}
METHODS = {"7z": ["LZMA2", "LZMA", "PPMd", "BZip2", "Deflate", "Copy"],
           "zip": ["Deflate", "Deflate64", "BZip2", "LZMA", "PPMd", "Copy"]}


def formats():
    """Formats you can create, in menu order. Each is a dict:
    id, label, ext, tools (candidates, available first), plus feature flags."""
    out = [
        dict(id="7z", label="7z", ext=".7z", tools=["7z"], levels=(0, 9, 5), threads=True, password=True,
             encrypt_names=True, volumes=True, solid=True, methods=METHODS["7z"]),
        dict(id="zip", label="zip", ext=".zip", tools=["7z", "zip"], levels=(0, 9, 5), threads=True,
             password=True, volumes=True, methods=METHODS["zip"]),
        dict(id="rar", label="rar", ext=".rar", tools=["rar"], levels=(0, 5, 3), threads=True, password=True,
             encrypt_names=True, volumes=True, solid=True, recovery=True),
        dict(id="zpaq", label="zpaq (journaling, very high ratio)", ext=".zpaq", tools=["zpaq"],
             levels=(1, 5, 1), threads=True, password=True),
        dict(id="tar", label="tar (no compression)", ext=".tar", tools=["tar"]),
    ]
    for suf in STREAMS:
        out.append(dict(id=f"tar.{suf}", label=f"tar.{suf} ({STREAM_NAMES[suf]})", ext=f".tar.{suf}",
                        tools=STREAMS[suf], stream=suf))
    for suf in STREAMS:
        out.append(dict(id=suf, label=f"{suf} ({STREAM_NAMES[suf]}, one file only)", ext=f".{suf}",
                        tools=STREAMS[suf], stream=suf, single=True))
    for f in out:
        f["available"] = [t for t in f["tools"] if tool(t)]
    return out


def tool_levels(fmt, tool_name):
    """(min, max, default) compression level for a format/tool pair."""
    if fmt.get("stream"):
        return COMPRESSORS[tool_name][:3]
    if fmt["id"] == "zip" and tool_name == "zip":
        return (0, 9, 6)
    return fmt.get("levels")


def tool_threads(fmt, tool_name):
    if fmt.get("stream"):
        return COMPRESSORS[tool_name][3] is not None
    return fmt.get("threads", False) and not (fmt["id"] == "zip" and tool_name == "zip")


def install_hint(name):
    return {"7z": "7zip", "rar": "rar", "zpaq": "zpaq", "pigz": "pigz", "pbzip2": "pbzip2", "lbzip2": "lbzip2",
            "plzip": "plzip", "lzip": "lzip", "lz4": "lz4", "zstd": "zstd", "xz": "xz-utils", "unrar": "unrar",
            "zip": "zip", "gzip": "gzip", "bzip2": "bzip2"}.get(name, name)


# ---------------------------------------------------------------- what you can extract

_7Z_EXTS = (".7z", ".zip", ".jar", ".apk", ".xpi", ".iso", ".cab", ".arj", ".lzh", ".lha", ".wim", ".deb",
            ".rpm", ".cpio", ".xar", ".dmg", ".vhd", ".vhdx", ".msi", ".chm", ".squashfs", ".001", ".zipx")
_TAR_EXTS = {".tar": None, ".tar.gz": "gz", ".tgz": "gz", ".tar.bz2": "bz2", ".tbz2": "bz2", ".tbz": "bz2",
             ".tar.xz": "xz", ".txz": "xz", ".tar.zst": "zst", ".tzst": "zst", ".tar.lz": "lz", ".tlz": "lz",
             ".tar.lz4": "lz4"}
_SINGLE_EXTS = {".gz": "gz", ".bz2": "bz2", ".xz": "xz", ".zst": "zst", ".lz": "lz", ".lz4": "lz4"}


def kind(path):
    """(kind, stream suffix) for an archive path, or (None, None). kind is rar, 7z, tar, single or zpaq."""
    low = path.lower()
    if low.endswith(".rar") or re.search(r"\.r\d\d$", low):
        return "rar", None
    if low.endswith(".zpaq"):
        return "zpaq", None
    for ext, suf in sorted(_TAR_EXTS.items(), key=lambda e: -len(e[0])):
        if low.endswith(ext):
            return "tar", suf
    for ext, suf in _SINGLE_EXTS.items():
        if low.endswith(ext):
            return "single", suf
    if low.endswith(_7Z_EXTS) or re.search(r"\.7z\.\d{3}$|\.zip\.\d{3}$", low):
        return "7z", None
    return None, None


def first_volume(path):
    """For a later part of a split archive (x.part3.rar, x.7z.003, x.zip.002, x.r05), the part to start from,
    if it exists; otherwise path itself."""
    for pat, repl in ((r"\.part0*\d+\.rar$", None), (r"\.(7z|zip)\.\d{3}$", None), (r"\.r\d\d$", ".rar")):
        m = re.search(pat, path, re.I)
        if not m:
            continue
        if repl == ".rar":
            cands = [path[:m.start()] + ".rar"]
        elif pat.startswith(r"\.part"):
            width = len(re.search(r"\d+", m.group(0)).group(0))
            cands = [path[:m.start()] + f".part{1:0{width}}.rar", path[:m.start()] + ".part1.rar"]
        else:
            cands = [path[:m.start()] + f".{m.group(1)}.001"]
        return next((c for c in cands if os.path.exists(c)), path)
    return path


def extract_tool(path):
    """The tool that will extract this archive, or None if none is installed."""
    k, suf = kind(path)
    if k == "rar":
        return tool("unrar") and "unrar"
    if k == "7z":
        return (tool("7z") and "7z") or (path.lower().endswith(".zip") and tool("unzip") and "unzip") or None
    if k == "zpaq":
        return tool("zpaq") and "zpaq"
    if k in ("tar", "single"):
        if suf is None:
            return tool("tar") and "tar"
        return next((t for t in STREAMS[suf] if tool(t)), None)
    return None


def can_extract(path):
    return kind(path)[0] is not None


def missing_extract_tool(path):
    """Package to install to extract this archive, or None if a tool is available."""
    if extract_tool(path):
        return None
    k, suf = kind(path)
    return {"rar": "unrar", "7z": "7zip", "zpaq": "zpaq"}.get(k) or install_hint(STREAMS[suf][-1] if suf else "tar")


def archive_stem(path):
    """Name for the folder an archive extracts into: photos.tar.gz -> photos, a.part1.rar -> a."""
    name = os.path.basename(path)
    low = name.lower()
    for ext in sorted(list(_TAR_EXTS) + list(_SINGLE_EXTS) + list(_7Z_EXTS) + [".rar", ".zpaq"], key=len,
                      reverse=True):
        if low.endswith(ext):
            name = name[:-len(ext)]
            break
    return re.sub(r"\.part0*1$", "", name, flags=re.I) or "archive"


# ---------------------------------------------------------------- running tools

def _start(argv, cwd=None, stdin=None, stdout=subprocess.PIPE):
    return subprocess.Popen(argv, cwd=cwd, stdin=stdin if stdin is not None else subprocess.DEVNULL,
                            stdout=stdout, stderr=subprocess.STDOUT, start_new_session=True, env=_ENV)


def _kill(*procs):
    for p in procs:
        if p and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            p.wait()


def _elapsed(seconds):
    m, sec = divmod(int(seconds), 60)
    return f"{m // 60}:{m % 60:02}:{sec:02}" if m >= 60 else f"{m}:{sec:02}"


_ZIP_COUNT = re.compile(rb"(\d+)/\s*(\d+) ")


def _run_reporting(task, argv, label, cwd=None, stdin_text=None, read_phase=None, progress="percent"):
    """Run a tool, turning its output into progress. Returns (exit code, output text).

    progress: "percent" for tools printing an overall "NN%" (7z, rar, unrar, zpaq); "zipcount" for zip -dc's
    "done/remaining" file counter (zip's own percentages are compression ratios, not progress); None to show
    only a busy bar. Percentages never go backwards, and a number split across two reads isn't misread.

    The tool runs under stdbuf so its progress lines arrive as they're printed: in a pipe, programs like zpaq
    otherwise hold their output back until a buffer fills or they exit, which freezes the bar. Long jobs show
    the elapsed time so it's clear they're still working when a tool goes quiet.

    read_phase: for tools whose percentage only counts input *read* (zpaq add), the text to show once it
    reaches 100% while the real work carries on silently; the bar then shows busy instead of a stuck number."""
    if tool("stdbuf"):
        argv = [tool("stdbuf"), "-o0", "-e0"] + list(argv)
    p = _start(argv, cwd=cwd, stdin=subprocess.PIPE if stdin_text is not None else None)
    if stdin_text is not None:
        try:
            p.stdin.write(stdin_text.encode())
            p.stdin.close()
        except BrokenPipeError:
            pass
    out = bytearray()
    start, pct, carry = time.monotonic(), None, b""
    try:
        fd = p.stdout.fileno()
        while True:
            task.check()
            ready, _, _ = select.select([fd], [], [], 0.2)
            if ready:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                out += chunk
                if len(out) > 2_000_000:
                    del out[:1_000_000]
                text, carry = carry + chunk, (carry + chunk)[-16:]
                if progress == "percent":
                    found = [int(v) for v in _PERCENT.findall(text)]
                elif progress == "zipcount":
                    found = [int(d) * 100 // max(int(d) + int(r), 1) for d, r in _ZIP_COUNT.findall(text)]
                else:
                    found = []
                if found:
                    pct = max(pct or 0, min(found[-1], 100))
            elif p.poll() is not None:
                break
            secs = time.monotonic() - start
            clock = f" · {_elapsed(secs)}" if secs >= 5 else ""
            if read_phase and pct is not None and pct >= 100:
                task.report(0, 0, f"{label} — {read_phase}{clock}")
            elif pct is not None:
                task.report(pct, 100, f"{label} — {'reading files ' if read_phase else ''}{pct}%{clock}")
            else:
                task.report(0, 0, f"{label}{clock}")
        p.wait()
    except Cancelled:
        _kill(p)
        raise
    return p.returncode, out.decode(errors="replace")


def _relay(task, src, dst, total, label, done=0):
    """Copy bytes from file object src to dst, reporting progress against `total`. Returns bytes copied."""
    while True:
        task.check()
        buf = src.read(1 << 20)
        if not buf:
            return done
        dst.write(buf)
        done += len(buf)
        task.report(done, total, f"{label} — {util.human_size(done)} of {util.human_size(total)}")


def _tail(text, n=8):
    lines = [ln.strip() for ln in text.replace("\b", "").splitlines() if ln.strip()]
    useful = [ln for ln in lines if re.search(r"error|cannot|can't|fail|denied|incorrect|wrong|unsupported|"
                                             r"not |no such|missing|corrupt|unexpected", ln, re.I)]
    return "\n".join((useful or lines)[-n:])


# ---------------------------------------------------------------- compress

def _safe_rels(rels):
    """Names starting with "-" get a "./" prefix so no tool can mistake them for options (zpaq has no "--")."""
    return ["./" + r if r.startswith("-") else r for r in rels]


def compress_command(spec):
    """(argv, stdin text, display argv with the password masked, cwd) for 7z/zip/rar/zpaq/tar jobs.
    Stream formats are built by _stream_commands()."""
    fmt, t = spec["format"], spec["tool"]
    base, rels, out = spec["base"], _safe_rels(spec["rels"]), spec["out"]
    pw, lvl, thr = spec.get("password") or "", spec.get("level"), spec.get("threads") or 0
    extra = spec.get("extra") or []
    stdin, secret = None, None
    if t == "7z":
        argv = [_exe("7z"), "a", "-y", "-bso0", "-bsp1", "-bse1", "-snl",  # -snl: keep symlinks as links
                f"-t{'7z' if fmt['id'] == '7z' else 'zip'}", f"-mx={lvl}"]
        method = spec.get("method")
        if method:
            argv.append(f"-m0={method}" if fmt["id"] == "7z" else f"-mm={method}")
        if thr:
            argv.append(f"-mmt={thr}")
        if fmt["id"] == "7z" and spec.get("solid") is not None:
            argv.append(f"-ms={'on' if spec['solid'] else 'off'}")
        if pw:
            argv.append("-p")
            stdin = f"{pw}\n{pw}\n"
            if fmt["id"] == "7z" and spec.get("encrypt_names"):
                argv.append("-mhe=on")
            if fmt["id"] == "zip":
                argv.append(f"-mem={spec.get('zip_encryption') or 'AES256'}")
        if spec.get("volume_mb"):
            argv.append(f"-v{spec['volume_mb']}m")
        argv += extra + ["--", out] + rels
    elif t == "zip":
        argv = [_exe("zip"), "-r", "-y", "-dc", f"-{lvl}"]  # -dc: "done/remaining" count per file
        if pw:
            argv += ["-P", pw]
            secret = pw
        argv += extra + [out, "--"] + rels
    elif t == "rar":
        argv = [_exe("rar"), "a", "-y", "-idc", "-idd", "-ol", f"-m{lvl}", "-r"]  # -ol: keep symlinks as links
        if thr:
            argv.append(f"-mt{thr}")
        if spec.get("solid"):
            argv.append("-s")
        if spec.get("recovery"):
            argv.append(f"-rr{spec['recovery']}%")
        if spec.get("volume_mb"):
            argv.append(f"-v{spec['volume_mb']}m")
        if pw:
            argv.append(f"-{'hp' if spec.get('encrypt_names') else 'p'}{pw}")
            secret = pw
        argv += extra + ["--", out] + rels
    elif t == "zpaq":
        argv = [_exe("zpaq"), "add", out] + rels + [f"-m{lvl}"]
        if thr:
            argv += ["-threads", str(thr)]
        if pw:
            argv += ["-key", pw]
            secret = pw
        argv += extra
    elif t == "tar":
        argv = [_exe("tar"), "-cf", "-", "--"] + rels
    else:
        raise ValueError(t)
    display = [a.replace(secret, "•" * 8) for a in argv] if secret else list(argv)
    return argv, stdin, display, base


def _stream_compressor(spec):
    t, lvl, thr = spec["tool"], spec.get("level"), spec.get("threads") or 0
    argv = [_exe(t), "-c", f"-{lvl}"]
    if t == "zstd" and lvl > 19:
        argv.insert(2, "--ultra")
    if thr and COMPRESSORS[t][3]:
        argv += COMPRESSORS[t][3](thr)
    return argv + (spec.get("extra") or [])


def command_preview(spec):
    """Shell-like text of what will run (password masked), for the Compress dialog."""
    fmt = spec["format"]
    if fmt.get("stream"):
        comp = shlex.join(os.path.basename(a) if i == 0 else a for i, a in enumerate(_stream_compressor(spec)))
        src = shlex.join(spec["rels"])
        if fmt.get("single"):
            return f"{comp} < {src} > {shlex.quote(os.path.basename(spec['out']))}"
        return f"tar -cf - -- {src} | {comp} > {shlex.quote(os.path.basename(spec['out']))}"
    _, _, display, _ = compress_command(spec)
    out_rel = os.path.relpath(spec["out"], spec["base"])  # the tool runs in the source folder
    text = shlex.join([os.path.basename(display[0])] + [out_rel if a == spec["out"] else a for a in display[1:]])
    if fmt["id"] == "tar":
        text += f" > {shlex.quote(os.path.basename(spec['out']))}"
    if spec.get("password") and spec["tool"] == "7z":
        text += "    (password passed on stdin)"
    return text


def _volume_files(out):
    d, name = os.path.dirname(out) or ".", os.path.basename(out)
    stem = re.sub(r"\.rar$", "", name)
    pat = re.compile(re.escape(name) + r"\.\d{3}$|" + re.escape(stem) + r"\.part\d+\.rar$")
    try:
        return [os.path.join(d, f) for f in os.listdir(d) if pat.match(f)]
    except OSError:
        return []


def compress(task, spec):
    """Create the archive described by spec (see archive_ui.CompressDialog.spec()). Returns the output path."""
    fmt, out = spec["format"], spec["out"]
    label = f"Compressing {os.path.basename(out)}"
    try:
        if fmt.get("stream"):
            _compress_stream(task, spec, label)
        else:
            argv, stdin, _, cwd = compress_command(spec)
            if fmt["id"] == "tar":
                _compress_tar_plain(task, argv, cwd, out, spec["total"], label)
            else:
                rc, text = _run_reporting(task, argv, label, cwd=cwd, stdin_text=stdin, read_phase=(
                    "compressing (zpaq reports no progress for this step)" if spec["tool"] == "zpaq" else None),
                    progress="zipcount" if spec["tool"] == "zip" else "percent")
                ok = rc == 0 or (spec["tool"] in ("7z", "rar") and rc == 1)  # 1 = warnings (e.g. a locked file)
                if not ok:
                    raise RuntimeError(_tail(text) or f"{os.path.basename(argv[0])} failed (exit code {rc})")
    except BaseException:
        for f in [out] + _volume_files(out):
            try:
                os.unlink(f)
            except OSError:
                pass
        raise
    if not os.path.exists(out):
        vols = sorted(_volume_files(out))
        return vols[0] if vols else out
    return out


def _compress_tar_plain(task, argv, cwd, out, total, label):
    p = _start(argv, cwd=cwd)
    try:
        with open(out, "wb") as fo:
            _relay(task, p.stdout, fo, total, label)
        p.wait()
    except BaseException:
        _kill(p)
        raise
    if p.returncode not in (0, 1):
        raise RuntimeError(f"tar failed (exit code {p.returncode})")


def _wait_finishing(task, proc, label, name):
    """After all input is handed over: multi-threaded compressors (xz, zstd -T) buffer a lot of it and may need
    much longer to finish than it took to feed them, so show that instead of a bar stuck at 100%."""
    start = time.monotonic()
    while proc.poll() is None:
        task.check()
        secs = time.monotonic() - start
        if secs >= 0.5:
            task.report(0, 0, f"{label} — {name} is compressing the last data · {_elapsed(secs)}")
        time.sleep(0.1)


def _compress_stream(task, spec, label):
    fmt, out, total = spec["format"], spec["out"], spec["total"]
    comp_argv = _stream_compressor(spec)
    with open(out, "wb") as fo:
        comp = subprocess.Popen(comp_argv, stdin=subprocess.PIPE, stdout=fo, stderr=subprocess.PIPE,
                                start_new_session=True, env=_ENV)
        src = None
        try:
            if fmt.get("single"):
                with open(os.path.join(spec["base"], spec["rels"][0]), "rb") as fi:
                    _relay(task, fi, comp.stdin, total, label)
            else:
                src = _start([tool("tar"), "-cf", "-", "--"] + _safe_rels(spec["rels"]), cwd=spec["base"])
                _relay(task, src.stdout, comp.stdin, total, label)
                src.wait()
            comp.stdin.close()
            _wait_finishing(task, comp, label, spec["tool"])
        except BaseException:
            _kill(src, comp)
            raise
    if src is not None and src.returncode not in (0, 1):
        raise RuntimeError(f"tar failed (exit code {src.returncode})")
    if comp.returncode != 0:
        err = comp.stderr.read().decode(errors="replace")
        raise RuntimeError(_tail(err) or f"{os.path.basename(comp_argv[0])} failed (exit code {comp.returncode})")


# ---------------------------------------------------------------- extract

def probe(path):
    """Look inside an archive without a password: {"encrypted": bool, "error": str or None}."""
    path = first_volume(path)
    k, _ = kind(path)
    t = extract_tool(path)
    if not t:
        return {"encrypted": False, "error": f"No tool to open this archive. Install {missing_extract_tool(path)}."}
    try:
        if t == "7z":
            r = subprocess.run([tool("7z"), "l", "-slt", "-p", "--", path], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=60, start_new_session=True, errors="replace", env=_ENV)
            text = r.stdout + r.stderr
            if "Encrypted = +" in text or "Cannot open encrypted archive" in text or "Wrong password" in text:
                return {"encrypted": True, "error": None}
            if r.returncode != 0 and "Can not open the file as archive" in text:
                return {"encrypted": False, "error": "This file isn't a valid archive, or it's damaged."}
        elif t == "unrar":
            r = subprocess.run([tool("unrar"), "lt", "-p-", "--", path], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=60, start_new_session=True, errors="replace", env=_ENV)
            text = r.stdout + r.stderr
            if r.returncode == 11 or "Flags: encrypted" in text or "encrypted headers" in text:
                return {"encrypted": True, "error": None}
        elif t == "zpaq":
            r = subprocess.run([tool("zpaq"), "l", path], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                               timeout=60, start_new_session=True, errors="replace", env=_ENV)
            if "password incorrect" in r.stdout + r.stderr:
                return {"encrypted": True, "error": None}
    except subprocess.TimeoutExpired:
        pass
    return {"encrypted": False, "error": None}


# overwrite policies: "overwrite", "skip", "rename"
def _extract_command(path, dest, password, overwrite, threads):
    t = extract_tool(path)
    stdin = None
    if t == "7z":
        argv = [tool("7z"), "x", "-y", "-bso0", "-bsp1", "-bse1", f"-o{dest}",
                {"overwrite": "-aoa", "skip": "-aos", "rename": "-aou"}[overwrite]]
        if threads:
            argv.append(f"-mmt={threads}")
        argv += ["--", path]
        stdin = (password or "") + "\n"  # no -p switch: on extract a bare -p means "empty password"
    elif t == "unzip":
        argv = [tool("unzip"), {"overwrite": "-o", "skip": "-n", "rename": "-n"}[overwrite]]
        argv += (["-P", password] if password else []) + ["--", path, "-d", dest]
    elif t == "unrar":
        argv = [tool("unrar"), "x", "-y", "-idc", "-idd",
                {"overwrite": "-o+", "skip": "-o-", "rename": "-or"}[overwrite],
                f"-p{password}" if password else "-p-"]
        if threads:
            argv.append(f"-mt{threads}")
        argv += ["--", path, dest.rstrip("/") + "/"]
    elif t == "zpaq":
        argv = [tool("zpaq"), "x", path, "-to", dest] + (["-force"] if overwrite == "overwrite" else [])
        if threads:
            argv += ["-threads", str(threads)]
        if password:
            argv += ["-key", password]
    else:
        raise ValueError(t)
    return t, argv, stdin


def extract(task, path, dest, password=None, overwrite="rename", threads=0):
    """Extract `path` into the existing folder `dest`. Raises WrongPassword, RuntimeError or Cancelled."""
    path = first_volume(path)
    k, suf = kind(path)
    label = f"Extracting {os.path.basename(path)}"
    if k in ("tar", "single"):
        return _extract_stream(task, path, dest, k, suf, overwrite, label)
    t, argv, stdin = _extract_command(path, dest, password, overwrite, threads)
    rc, text = _run_reporting(task, argv, label, stdin_text=stdin,
                              progress=None if t == "unzip" else "percent")  # unzip's % are ratios
    wrong = (t == "unrar" and rc == 11) or "Wrong password" in text or "password incorrect" in text or \
        "incorrect password" in text.lower() or (t == "unzip" and "incorrect password" in text)
    if wrong:
        raise WrongPassword()
    if t == "7z" and rc == 2 and "encrypted" in text.lower():
        raise WrongPassword()
    if rc != 0 and not (t in ("7z", "unrar") and rc == 1) and not (t == "unzip" and rc == 1):
        raise RuntimeError(_tail(text) or f"{t} failed (exit code {rc})")
    return dest


def _extract_stream(task, path, dest, k, suf, overwrite, label):
    total = max(os.path.getsize(path), 1)
    dec = None
    if suf:
        name = next(t for t in STREAMS[suf] if tool(t))
        dec_argv = [tool(name), "-dc"] + (["-T0"] if name in ("xz", "zstd") else [])
    with open(path, "rb") as fi:
        if k == "single":
            out = os.path.join(dest, archive_stem(path))
            if os.path.exists(out):
                if overwrite == "skip":
                    return dest
                if overwrite == "rename":
                    out = util.unique_path(dest, os.path.basename(out), "num")
            with open(out, "wb") as fo:
                dec = subprocess.Popen(dec_argv, stdin=subprocess.PIPE, stdout=fo, stderr=subprocess.PIPE,
                                       start_new_session=True, env=_ENV)
                try:
                    _relay(task, fi, dec.stdin, total, label)
                    dec.stdin.close()
                    dec.wait()
                except BaseException:
                    _kill(dec)
                    os.unlink(out)
                    raise
            if dec.returncode != 0:
                os.unlink(out)
                raise RuntimeError(_tail(dec.stderr.read().decode(errors="replace")) or "decompression failed")
            return dest
        policy = {"overwrite": "--overwrite", "skip": "--skip-old-files", "rename": "--backup=numbered"}[overwrite]
        tar_argv = [tool("tar"), "-xf", "-", "-C", dest, "--no-same-owner", policy]
        if suf:
            dec = subprocess.Popen(dec_argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True, env=_ENV)
            tar = _start(tar_argv, stdin=dec.stdout)
            dec.stdout.close()
            feed = dec.stdin
        else:
            tar = _start(tar_argv, stdin=subprocess.PIPE)
            feed = tar.stdin
        try:
            _relay(task, fi, feed, total, label)
            feed.close()
            if dec:
                dec.wait()
            out = tar.stdout.read().decode(errors="replace")
            tar.wait()
        except BrokenPipeError:
            _kill(dec)
            out = tar.stdout.read().decode(errors="replace")
            tar.wait()
        except BaseException:
            _kill(dec, tar)
            raise
    if dec and dec.returncode != 0:
        raise RuntimeError(_tail(dec.stderr.read().decode(errors="replace")) or "decompression failed")
    if tar.returncode not in (0, 1):
        raise RuntimeError(_tail(out) or f"tar failed (exit code {tar.returncode})")
    return dest
