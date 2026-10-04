"""Prefer the system's programs and libraries over Anaconda's (or Miniconda's, Miniforge's…).

Shell profiles set up by `conda init` put Anaconda's bin folder ahead of /usr/bin, with copies of gsettings, gio,
ffmpeg, xz, zstd… that don't match the desktop: Anaconda's gsettings can't see your real settings (no dconf), so
"Set as Wallpaper" silently does nothing, and its libraries and Qt plugins can break the tools Kestrel runs. So at
startup Kestrel moves Anaconda's folders to the end of PATH and similar search paths, and drops the conda-only
overrides for GLib and Qt modules.

Unless you deliberately run Kestrel from an activated conda environment (`conda activate myenv`, anything but the
auto-activated "base"): then the environment is left exactly as it is.
"""
import os
import re

# conda installs that aren't named in the environment variables (e.g. a PATH entry left by a shell profile)
_CONDA_DIR = re.compile(r"/(ana|mini)conda\d*(/|$)|/miniforge\d*(/|$)|/mambaforge(/|$)|/micromamba(/|$)")
_SEARCH_PATHS = ("PATH", "LD_LIBRARY_PATH", "XDG_DATA_DIRS")
_CONDA_OVERRIDES = ("GSETTINGS_SCHEMA_DIR", "GIO_MODULE_DIR", "GIO_EXTRA_MODULES", "QT_PLUGIN_PATH",
                    "QT_QPA_PLATFORM_PLUGIN_PATH")


def explicit_conda_env():
    """True when Kestrel was started from a conda environment the user activated on purpose (not "base")."""
    env = os.environ.get("CONDA_DEFAULT_ENV", "")
    return bool(env) and env != "base"


def _conda_roots():
    roots = set()
    prefix = os.environ.get("CONDA_PREFIX")
    if prefix:
        roots.add(prefix.rstrip("/"))
    for var in ("CONDA_EXE", "CONDA_PYTHON_EXE"):   # <root>/bin/conda
        exe = os.environ.get(var)
        if exe:
            roots.add(os.path.dirname(os.path.dirname(exe)).rstrip("/"))
    return {r for r in roots if r}


def _is_conda(path, roots):
    return any(path == r or path.startswith(r + "/") for r in roots) or bool(_CONDA_DIR.search(path))


def prefer_system():
    """Put the system's folders first (see the module docstring). Call before GLib or Qt are loaded."""
    if explicit_conda_env():
        return
    roots = _conda_roots()
    for var in _SEARCH_PATHS:
        value = os.environ.get(var)
        if not value:
            continue
        parts = value.split(":")
        conda = [p for p in parts if p and _is_conda(p, roots)]
        if conda:
            os.environ[var] = ":".join([p for p in parts if p not in conda] + conda)
    for var in _CONDA_OVERRIDES:
        value = os.environ.get(var)
        if value:
            keep = [p for p in value.split(":") if p and not _is_conda(p, roots)]
            if keep:
                os.environ[var] = ":".join(keep)
            else:
                del os.environ[var]
