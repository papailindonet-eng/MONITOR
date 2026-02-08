#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python -m pip install -r "$ROOT_DIR/backend/requirements.txt"
exec python "$ROOT_DIR/backend/app.py"
