#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

SCRIPT_DIR="$(pwd)"

launcher=""
if [ -f "$SCRIPT_DIR/launcher.py" ]; then
  launcher="$SCRIPT_DIR/launcher.py"
elif [ -f "$SCRIPT_DIR/distribution/launcher.py" ]; then
  launcher="$SCRIPT_DIR/distribution/launcher.py"
fi

if [ -z "$launcher" ]; then
  echo "[CognArch] launcher.py not found."
  exit 1
fi

runtime_candidates=(
  "$SCRIPT_DIR/runtime/bin/python3"
  "$SCRIPT_DIR/runtime/bin/python"
  "$SCRIPT_DIR/runtime/python/bin/python3"
  "$SCRIPT_DIR/runtime/python/bin/python"
  "$SCRIPT_DIR/runtime/install/bin/python3"
  "$SCRIPT_DIR/runtime/install/bin/python"
  "$SCRIPT_DIR/../runtime/bin/python3"
  "$SCRIPT_DIR/../runtime/bin/python"
  "$SCRIPT_DIR/../runtime/python/bin/python3"
  "$SCRIPT_DIR/../runtime/python/bin/python"
  "$SCRIPT_DIR/../runtime/install/bin/python3"
  "$SCRIPT_DIR/../runtime/install/bin/python"
)

for python_path in "${runtime_candidates[@]}"; do
  if [ -x "$python_path" ]; then
    exec "$python_path" "$launcher" "$@"
  fi
done

if command -v python3.12 >/dev/null 2>&1; then
  exec python3.12 "$launcher" "$@"
elif command -v python3 >/dev/null 2>&1; then
  exec python3 "$launcher" "$@"
else
  echo "CognArch could not find a packaged runtime Python or system Python 3.12/python3."
  exit 1
fi
