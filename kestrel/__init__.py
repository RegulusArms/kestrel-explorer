"""Kestrel Explorer - a file manager that brings terminal tools into the window, alongside GNOME Files."""

__version__ = "0.2.1-alpha1"

# Before anything loads GLib or Qt: prefer the system's programs and libraries over Anaconda's (see env.py).
from . import env as _env  # noqa: E402

_env.prefer_system()
