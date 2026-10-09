# Packaging for PyPI

> **Status: published.** [`kestrel-explorer`](https://pypi.org/project/kestrel-explorer/) 0.2.1a0 is on PyPI,
> uploaded by `.github/workflows/pypi.yml`. The README's [pip (PyPI)](README.md#pip-pypi) section is the user guide.

## What is set up

| Item | Where | Notes |
|---|---|---|
| Build config | `pyproject.toml` | setuptools backend, package `kestrel` |
| Name | `pyproject.toml` | `kestrel-explorer` |
| Version | `kestrel/__init__.py` (`__version__`) | Read from the code at build time, so there's one source of truth. `0.2.0-alpha` is published as `0.2.0a0` (the [PEP 440](https://peps.python.org/pep-0440/) form). |
| Python | `requires-python = ">=3.11.4"` | 3.11.4 added the tar extraction safety filter that Extract Here uses |
| License | `LICENSE` | MIT |
| Dependencies | `PyQt6>=6.5`, `Pillow>=9.0` | Installed from PyPI wheels |
| Optional extra | `pip install kestrel-explorer[gio]` | Adds PyGObject for Open With, default apps, and drive and network mounting. The app runs without it, with those features off. |
| Package contents | every module in `kestrel/` | No data files are needed. `admin_helper.py` ships as an ordinary module; the admin session runs it as a file through `pkexec` |
| Command | `kes` (gui-script → `kestrel.app:main`) | Same command as `install.sh` sets up |
| App-grid entry | Created on first launch by `util.ensure_desktop_entry()` | Points at the pip-installed `kes` |
| Ignored build output | `.gitignore` | `build/`, `dist/`, `*.egg-info/` |
| Upload guard | `Private :: Do Not Upload` classifier | Removed. Add it back to block uploads; the publish workflow also stops early while it's there |
| Publishing | `.github/workflows/pypi.yml` | Run by hand, to PyPI, with [trusted publishing](https://docs.pypi.org/trusted-publishers/) (no API tokens) |
| Project links | `[project.urls]` | Homepage, source and issues on `github.com/RegulusArms/kestrel-explorer` |

## Checklist

- [x] **License:** MIT (`LICENSE`, with `license = "MIT"` in `pyproject.toml`).
- [x] **Check the name.** [`kestrel-explorer`](https://pypi.org/project/kestrel-explorer/) was free on PyPI on 2026-10-05.
- [x] **Fill in `[project.urls]`** (homepage, source, issues).
- [x] **Author:** `RegulusArms` (no email, so none is shown on PyPI).
- [x] **Confirm the GitHub URL:** `https://github.com/RegulusArms/kestrel-explorer` (the repo's `origin`).
- [x] **Set up trusted publishing** (see below).
- [x] **Fix the README's relative links for PyPI:** they're absolute GitHub URLs, since PyPI shows the README
  without the repo. Keep new links absolute.
- [x] **Document the system programs pip can't install,** the image formats and the admin session: README,
  "pip (PyPI)". It recommends a `--system-site-packages` virtual environment, where pip reuses Ubuntu's PyQt6, Pillow
  and `gi`, so the system's Qt image plugins and GIO work.
- [x] **Install test:** a clean virtual environment with the system Python 3.14 installs 0.2.1a0 from PyPI and runs.
- [ ] **Test the minimum versions.** Python 3.11.4, PyQt6 6.5 and Pillow 9.0 are best guesses; so far only Python
  3.14 with Ubuntu's PyQt6 6.10 has been tried. From a pip install, check the features that call other programs:
  Compress / Extract, the admin session (Retry as Administrator), metadata editing, video thumbnails, and UWP.
- [ ] **Image formats with the PyQt6 wheel.** In a plain virtual environment, pip's PyQt6 has its own Qt and can't
  load `kimageformat6-plugins` (no AVIF, HEIC, JPEG XL, PSD or camera RAW). Documented; a Pillow-based fallback
  (`pillow-heif`, `rawpy`) would fix it.
- [ ] **Drop focus extension.** `data/gnome-shell/` isn't in the wheel, so pip users on GNOME on Wayland don't get
  drag-drop focus (documented in the README).
- [ ] **kes-setup for pip users.** It isn't in the wheel either; the README has them run it from a clone.
- [ ] **Bump the version** in `kestrel/__init__.py` before every upload: PyPI never
  takes the same version twice.
- [x] **Remove `Private :: Do Not Upload`** from the classifiers.

## Trusted publishing (one-time setup)

Uploads go through GitHub Actions with [trusted publishing](https://docs.pypi.org/trusted-publishers/): PyPI trusts
`.github/workflows/pypi.yml` in this repo, so no API token is stored anywhere. It was set up as a *pending*
publisher, which created the project on the first upload; the publisher is now under the project's Settings → Publishing on PyPI.

1. **GitHub environment.** In the repo: Settings → Environments → New environment, create `pypi`. Add yourself under
   "Required reviewers" (each release then waits for your approval) and, under "Deployment branches and tags", limit
   it to `main`.
2. **PyPI.** Log in at [pypi.org](https://pypi.org) (2FA on), go to
   [Account → Publishing](https://pypi.org/manage/account/publishing/) → "Add a new pending publisher" → GitHub, and
   enter:

   | Field | Value |
   |---|---|
   | PyPI project name | `kestrel-explorer` |
   | Owner | `RegulusArms` |
   | Repository name | `kestrel-explorer` |
   | Workflow name | `pypi.yml` |
   | Environment name | `pypi` |

A pending publisher reserves nothing: until the first upload, anyone can register the name. After the first upload the
project's publishers are under the project's Settings → Publishing.

## Building and publishing

Check a build locally first:

```bash
python3 -m pip install --upgrade build twine
python3 -m build                      # creates dist/*.tar.gz and dist/*.whl
python3 -m twine check --strict dist/*   # checks that the README renders on PyPI
```

Then, with the changes pushed to `main`:

1. Actions → "Publish to PyPI" → Run workflow → branch `main`, and approve the deployment when asked.
2. Try it in a clean environment: `python3 -m venv /tmp/kes-venv && /tmp/kes-venv/bin/pip install kestrel-explorer`

PyPI never accepts the same version twice, even after deleting it, so bump `__version__` in `kestrel/__init__.py`
before each new upload.

## pip install vs install.sh

| | `install.sh` | `pip install` |
|---|---|---|
| Python and Qt | System `/usr/bin/python3` with Ubuntu's PyQt6 | Any Python 3.11.4+, with PyQt6 and Qt from PyPI |
| Extra image formats (AVIF, HEIC, RAW…) | Yes, with `kimageformat6-plugins` | No (see the to-do list above) |
| GIO features | Yes (`python3-gi`) | Only with the `[gio]` extra, or a system Python that has `gi` |
| App-grid entry | Full entry with a "New Window" action | Basic entry, created on first launch |
| Default folder app | `./install.sh --default` | From a clone of the repo: `./kes-setup --kes "$(command -v kes)" --default` |
| Archive tools, exiftool, ffmpeg | `./install.sh --install-recommended` installs them | Install them yourself with apt |
| Admin session | Uses `pkexec` and the system Python | Same: needs `pkexec` and `/usr/bin/python3`, whatever Python runs Kestrel |
| UWP integration | Works if UWP is installed | Same |
| Updates | Live from the repo (it links to `kes`) | `pip install --upgrade kestrel-explorer` |
