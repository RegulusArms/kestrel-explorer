"""Kestrel Explorer - a lightweight, gallery-friendly file manager."""

__version__ = "0.1.3-alpha"

# Before anything loads GLib or Qt: prefer the system's programs and libraries over Anaconda's (see env.py).
from . import env as _env  # noqa: E402

_env.prefer_system()
