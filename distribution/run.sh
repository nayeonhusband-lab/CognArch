#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if command -v python3.12 >/dev/null 2>&1; then
  exec python3.12 launcher.py "$@"
elif command -v python3 >/dev/null 2>&1; then
  exec python3 launcher.py "$@"
else
  echo "Python 3.12 is required. Please install Python 3.12 and try again."
  exit 1
fi

