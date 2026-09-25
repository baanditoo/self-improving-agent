#!/usr/bin/env bash
# Idempotent install script for the Cloud Agent environment.
# Creates a virtualenv and installs runtime + dev dependencies.
set -euo pipefail

cd "$(dirname "$0")/.."

# The base image ships Python but not the venv/ensurepip module, so install it.
# apt-get install is idempotent, so this is safe to run repeatedly.
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  PY_MINOR="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  sudo apt-get update -y
  sudo apt-get install -y "python${PY_MINOR}-venv"
fi

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt

echo "Install complete. Python: $(python --version)"
