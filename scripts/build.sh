#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "${repo_root}/plugins/lectern/scripts/course.py" build --workspace "${repo_root}" "$@"
