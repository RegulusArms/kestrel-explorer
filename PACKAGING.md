# Packaging for PyPI

> **Status: not ready for release.** The packaging setup is in place, but nothing has been built or uploaded.
> `pyproject.toml` includes the classifier `Private :: Do Not Upload`, so PyPI will reject any upload until it is
> removed on purpose.

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
| Upload guard | `Private :: Do Not Upload` classifier | Remove it for the first real release |

## To do before the first release

- [x] **License:** MIT (`LICENSE`, with `license = "MIT"` in `pyproject.toml`).
- [ ] **Check the name.** Make sure `kestrel-explorer` is free on [PyPI](https://pypi.org/project/kestrel-explorer/) and [TestPyPI](https://test.pypi.org/project/kestrel-explorer/).
- [ ] **Fill in `[project.urls]`** (homepage, issues) once the GitHub repo exists.
- [x] **Author:** `RegulusArms` (no email, so none is shown on PyPI).
- [ ] **Confirm the GitHub URL.** UWP's README already links to `https://github.com/RegulusArms/kestrel-explorer`; check that's the real repo name before using it in `[project.urls]`.
- [ ] **Test in a clean virtual environment.** The minimum versions (Python 3.11.4, PyQt6 6.5, Pillow 9.0) are best guesses and haven't been tested. So far it has only been run on Python 3.14 with Ubuntu's PyQt6 6.10. Besides browsing, check the features that call other programs from a pip install: Compress / Extract, the admin session (Retry as Administrator), metadata editing, video thumbnails, and UWP.
  ```bash
  python3 -m venv /tmp/kes-venv && /tmp/kes-venv/bin/pip install . && /tmp/kes-venv/bin/kes
  ```
- [ ] **Decide how to handle image formats.** The PyQt6 wheel from PyPI comes with its own Qt and won't load the system's `kimageformat6-plugins`. A pip install therefore can't show AVIF, HEIC, JPEG XL, PSD or camera RAW, even when those plugins are installed. Either document this or add a Pillow-based fallback for those formats (for example with `pillow-heif` and `rawpy`).
- [ ] **Document the system programs pip can't install.** The README's "Dependencies" section lists them for `install.sh` users; a pip user needs a short "pip install" section saying they must install these with apt themselves:
  - archives: `7zip`, `unrar`, `zip`/`unzip`, `pigz`, `zpaq`, `zstd`, `xz-utils`, `bzip2`, `lzip` (and optionally `rar`, `pbzip2`/`lbzip2`, `plzip`, `lz4`);
  - `libimage-exiftool-perl` (metadata), `ffmpeg` (video thumbnails);
  - `pkexec` (admin session), `gvfs`/`gvfs-backends` and `udisks2` (network and drives);
  - `kimageformat6-plugins` only helps `install.sh` users (see the image formats item above).
- [ ] **Decide what pip users get for the admin session.** The helper always runs with the system's `/usr/bin/python3` (it only needs the standard library), wherever Kestrel itself is installed, and is read from the install location (e.g. inside a virtualenv). That works, but say so, and note that pkexec's prompt names `/usr/bin/python3`. A polkit policy file with a clearer prompt can't be installed by pip.
- [ ] **Bump the version** in `kestrel/__init__.py` if needed.
- [ ] **Remove `Private :: Do Not Upload`** from the classifiers.

## Building and uploading (when ready)

```bash
python3 -m pip install --upgrade build twine
python3 -m build                      # creates dist/*.tar.gz and dist/*.whl
python3 -m twine check dist/*         # checks that the README renders on PyPI
python3 -m twine upload --repository testpypi dist/*   # try TestPyPI first
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ kestrel-explorer
python3 -m twine upload dist/*        # the real upload
```

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
