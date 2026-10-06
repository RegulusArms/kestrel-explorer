"""Performance counters, for KESTREL_STATS=1 (off otherwise: each call is one check). Kestrel then prints a summary to
stderr when it quits: folder listing times, thumbnail cache use and queue length, copy speed, archive job times, tower
message delay, and the most tasks at once. The same counters and output as the C++ version (stats.cpp); bench/ can
collect them. Thread-safe: thumbnail workers and file-operation tasks report from their own threads."""
import os
import sys
import threading
import time

ENABLED = os.environ.get("KESTREL_STATS", "") not in ("", "0")
_lock = threading.Lock()
_samples = {}   # what -> [count, sum, largest]
_counts = {}
_peaks = {}
_start = time.monotonic()


def enabled():
    return ENABLED


def enable():
    """As if KESTREL_STATS=1 (tests)."""
    global ENABLED
    ENABLED = True


def sample(what, value):
    """A timing or a rate: how many, average, largest."""
    if not ENABLED:
        return
    with _lock:
        s = _samples.get(what)
        if s is None:
            _samples[what] = [1, value, value]
        else:
            s[0] += 1
            s[1] += value
            s[2] = max(s[2], value)


def count(what, n=1):
    if not ENABLED:
        return
    with _lock:
        _counts[what] = _counts.get(what, 0) + n


def peak(what, value):
    """The largest seen."""
    if not ENABLED:
        return
    with _lock:
        _peaks[what] = max(_peaks.get(what, 0), value)


def now_ms():
    """For timings."""
    return int((time.monotonic() - _start) * 1000)


def summary():
    """The report ("" if nothing was counted)."""
    with _lock:
        lines = {w: f"{n}×, average {total / n:.1f}, largest {largest:.1f}" for w, (n, total, largest) in _samples.items()}
        lines.update({w: str(n) for w, n in _counts.items()})
        lines.update({w: f"most {n}" for w, n in _peaks.items()})
    if not lines:
        return ""
    return "Kestrel stats (KESTREL_STATS):\n" + "".join(f"  {w}: {lines[w]}\n" for w in sorted(lines))


def print_at_quit(app):
    """Prints summary() to stderr when the app quits, if enabled."""
    if ENABLED:
        app.aboutToQuit.connect(lambda: sys.stderr.write(summary()))
