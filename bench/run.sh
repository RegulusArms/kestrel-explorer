#!/usr/bin/env bash
# Benchmark Kestrel Explorer against GNOME Files, or Nemo on Linux Mint (see bench/README.md).
#   bench/run.sh                      run everything, print the tables and write them to bench/results.md
#   bench/run.sh --runs 5             more runs per measurement (medians are reported)
#   bench/run.sh --only startup,copy  only some measurements
exec /usr/bin/python3 "$(dirname "${BASH_SOURCE[0]}")/bench.py" "$@"
