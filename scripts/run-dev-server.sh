#!/usr/bin/env bash
# Start the Flask development server for the self-improving agent.
set -euo pipefail

cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
source .venv/bin/activate

export FLASK_APP=self_improving_agent.app:app
exec python -m flask run --host 0.0.0.0 --port 5000
