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
echo "all files"
git diff --shortstat "$base"
echo
echo "code and tests, packed traces excluded"
git diff --shortstat "$base" -- . ':(exclude)pro/examples' ':(exclude)data'
echo
echo "examples recorded in the window (17 Nov 2026) and their traces"
git diff --shortstat "$base" -- ':(glob)pro/examples/live-*.json' pro/examples/traces
echo
echo "older packed examples (26 Oct 2026) and data fixtures"
git diff --shortstat "$base" -- pro/examples data ':(exclude,glob)pro/examples/live-*.json' ':(exclude)pro/examples/traces'
echo
git diff --stat "$base"
