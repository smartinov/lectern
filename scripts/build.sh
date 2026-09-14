#!/usr/bin/env bash
# Build one course directory into build/<slug>.epub and validate it.
set -euo pipefail

usage() {
  echo "usage: $0 courses/<slug>" >&2
  exit 2
}

[[ $# -eq 1 ]] || usage
course_dir="$1"
[[ -f "${course_dir}/course.yaml" ]] || usage

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
slug="$(basename "${course_dir}")"
out="${repo_root}/build/${slug}.epub"
mkdir -p "${repo_root}/build"

lessons=("${course_dir}"/lessons/*.md)
[[ -e "${lessons[0]}" ]] || { echo "no lessons in ${course_dir}/lessons" >&2; exit 1; }

inputs=("${lessons[@]}")
[[ -f "${course_dir}/answers.md" ]] && inputs+=("${course_dir}/answers.md")

pandoc \
  --defaults "${repo_root}/templates/defaults.yaml" \
  --metadata-file "${course_dir}/course.yaml" \
  --resource-path "${course_dir}" \
  --output "${out}" \
  "${inputs[@]}"

epubcheck "${out}"
echo "built ${out}"
