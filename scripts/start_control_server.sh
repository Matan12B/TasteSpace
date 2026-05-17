#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -x ".venv/bin/python" ]; then
  exec ".venv/bin/python" "scripts/control_server.py"
fi

exec python3 "scripts/control_server.py"
