#!/usr/bin/env bash
# Print diff stats from the pre-SLAY HACKS tag to the current tree.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"
base=${1:-pre-slayhacks}
if ! git rev-parse --verify --quiet "${base}^{commit}" >/dev/null; then
  echo "missing base ${base}" >&2
  exit 1
fi
echo "base ${base} $(git rev-parse --short "$base")"
echo "head $(git rev-parse --short HEAD) ($(git rev-parse --abbrev-ref HEAD))"
echo
git diff --shortstat "$base"
echo
git diff --stat "$base"
