#!/usr/bin/env bash
# Run the test suite (see tests/README.md).
#   tests/run.sh                    every test
#   tests/run.sh fileops atc_undo   only these (names: fileops atc_sync atc_tasks atc_undo atc_tabs devices sidebar theme chooser installer)
# Each test runs with a throwaway HOME on a private D-Bus session bus: your files, settings, dock and running
# Kestrel windows are never touched.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
ALL=(fileops atc_sync atc_tasks atc_undo atc_tabs devices sidebar theme chooser installer)
TESTS=("$@")
(( ${#TESTS[@]} )) || TESTS=("${ALL[@]}")
for t in "${TESTS[@]}"; do
    [[ " ${ALL[*]} " == *" $t "* ]] || { echo "Unknown test: $t (tests: ${ALL[*]})" >&2; exit 2; }
done

# the C++ version, if it's built next to this project, joins the cross-version checks in atc_tabs
# (KESTREL_BUILD_DIR: the name of its build folder, when another machine shares these folders)
KES_CXX=""
CXX_BUILD="$ROOT/../kes-c/${KESTREL_BUILD_DIR:-build}"
[[ -x "$CXX_BUILD/kes" ]] && KES_CXX="$(cd "$CXX_BUILD" && pwd)/kes"
export KES_CXX

failed=()
for t in "${TESTS[@]}"; do
    echo
    echo "== $t"
    work="$(mktemp -d "${TMPDIR:-/tmp}/kestrel-test.XXXXXX")"
    mkdir -p "$work/home"
    if [[ $t == installer ]]; then
        cmd=("$HERE/test_installer.sh")
    else
        cmd=(/usr/bin/python3 "$HERE/test_$t.py")
    fi
    env -u XDG_CONFIG_HOME -u XDG_CACHE_HOME -u XDG_DATA_HOME -u CONDA_DEFAULT_ENV \
        HOME="$work/home" QT_QPA_PLATFORM=offscreen \
        timeout 600 dbus-run-session -- "${cmd[@]}" >"$work/log" 2>&1
    rc=$?
    grep -E '^(PASS|FAIL|SKIP) ' "$work/log"
    if grep -q '^ALL PASSED' "$work/log" && (( rc == 0 )); then
        grep '^ALL PASSED' "$work/log"
    else
        failed+=("$t")
        grep -q -E '^FAILED' "$work/log" && grep -E '^FAILED' "$work/log" ||
            { echo "CRASHED or timed out (exit $rc). Last lines:"; tail -15 "$work/log"; }
    fi
    # (a desktop service started by the test, such as gvfsd-metadata, may still be writing there for a moment)
    case "$work" in */kestrel-test.*) rm -rf "$work" 2>/dev/null || { sleep 1; rm -rf "$work"; } ;; esac
done

echo
if (( ${#failed[@]} )); then
    echo "Failed: ${failed[*]}"
    exit 1
fi
echo "All ${#TESTS[@]} tests passed."
