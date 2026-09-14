#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "${repo_root}"
export PYTHONDONTWRITEBYTECODE=1
for dependency in python3 pandoc magick epubcheck shellcheck; do
  command -v "${dependency}" >/dev/null || { echo "missing dependency: ${dependency}" >&2; exit 1; }
done
python3 scripts/check_package.py
bash -n scripts/build.sh scripts/check.sh
shellcheck scripts/build.sh scripts/check.sh
python3 -m unittest discover -s tests -p 'test_*.py'
