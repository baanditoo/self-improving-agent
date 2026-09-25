# self-improving-agent

A tiny **self-improving agent**: an evolutionary optimizer that starts from
random noise and iteratively improves a candidate solution until it reproduces
a target phrase. Ships with a small Flask web UI that visualizes the agent
improving generation by generation.

## How it works

The agent (`self_improving_agent/agent.py`) runs a mutation-and-selection loop:

1. Start from a random string the same length as the target.
2. Each generation, mutate the characters that don't yet match.
3. Keep the mutation only if it scores at least as well as the incumbent.

Because correct characters are never discarded, fitness (fraction of matching
characters) is monotonically non-decreasing and the agent visibly improves
over time.

## Requirements

- Python 3.10+

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Run the web app

```bash
bash scripts/run-dev-server.sh
# then open http://localhost:5000
```

## Run from the command line

```bash
python -m self_improving_agent "Hello, world!" --seed 42
```

## Run the tests

```bash
pytest
```

## Cloud Agent environment

This repo includes a [`.cursor/environment.json`](.cursor/environment.json)
that provisions the dev environment for Cursor Cloud Agents:

- `install` — creates a virtualenv and installs dependencies
  (`scripts/cloud-agent-install.sh`).
- `terminals.web` — runs the Flask dev server on port `5000`
  (`scripts/run-dev-server.sh`).
