#!/usr/bin/env bash
# Benchmark Kestrel Explorer (Python and C++) against GNOME Files, or Nemo on Linux Mint (see bench/README.md).
#   bench/run.sh                      run everything and print the tables
#   bench/run.sh --runs 5             more runs per measurement (medians are reported)
#   bench/run.sh --update-readme      ...and write the results into both projects' README.md
#   bench/run.sh --only startup,copy  only some measurements
exec /usr/bin/python3 "$(dirname "${BASH_SOURCE[0]}")/bench.py" "$@"
