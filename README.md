# self-improving-agent

Solana-only memecoin agent. The edge is mechanical: sell the initial at about 2x so the cost basis comes back, then trail or hold the free runner. It starts in paper mode. Live swaps cannot run unless `LIVE_TRADING=true`.

This is experimental software. Memecoins can go to zero. Nothing here is financial advice.

## Setup

Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

`.env` is gitignored. Do not put keys in the repo.

### GMGN API key

Create a key at [https://gmgn.ai/ai](https://gmgn.ai/ai). Upload the public key that matches `GMGN_PRIVATE_KEY` (Ed25519 or RSA PEM). Read routes send `X-APIKEY`, a timestamp valid about ±5 seconds, and a unique `client_id`. Swap, order query, and strategy orders also send `X-Signature`.

`GMGN_HOST` defaults to `https://openapi.gmgn.ai`.

GMGN is IPv4 only. The client binds its HTTP transport to `0.0.0.0`. If calls return 401 or 403 with a valid key, disable IPv6 on the interface. A 401/403 with correct credentials usually means the request left over IPv6.

### Pump.fun

Discovery uses the frontend API v3 at `https://frontend-api-v3.pump.fun` (latest, featured, search, coin, bulk mints, king of the hill, graduated, candlesticks, top holders). `PUMPFUN_BEARER` is optional. Public coin routes are called without it and degrade cleanly on 401. Pump fields are curve and discovery state, not execution quotes.

### StonkFun

Discovery uses the public API at [https://www.stonkfun.xyz/developers](https://www.stonkfun.xyz/developers), base `https://www.stonkfun.xyz/api/public/v1`. No key. The client honors the per-IP limit (300 reads/minute, `Retry-After` on 429). Quote asset, launchpad stage, transfer-fee, and flywheel flags are recorded when the payload includes them. `STONKS_API_KEY` or `BITQUERY_API_KEY` only enables an optional Bitquery trades enrichment. The default path does not need it.

## Paper, then live

Paper is the default. `python -m self_improving_agent paper` forces paper even if the env flag is set. Paper simulates fills from a GMGN quote plus slippage and never calls swap.

Live mode (`LIVE_TRADING=true` only; `1` and `yes` do not count) quotes, checks price impact against pool liquidity, swaps, and polls order status. A 2x `profit_stop` condition order is attached on the entry swap when strategy orders are accepted (`price_scale` `100` means +100%). Local polling still manages the runner if that order is missing. `FLAT_ON_EXIT` defaults to false.

```bash
python -m self_improving_agent run
python -m self_improving_agent status
python -m self_improving_agent report --hours 1
python -m self_improving_agent learn --hours 4
```

The loop discovers every 15–30 seconds, writes an hourly report, and learns every 4 hours. Ctrl+C stops it.

The paper book starts at `PAPER_EQUITY_USD` (default $500). Each new coin spends at most `MAX_BUY_USD` (default $10), fee included. Slots default to 50, so the book can hold fifty $10 tickets. New entries halt when daily realized plus unrealized drawdown exceeds `MAX_DAILY_DD_PCT` (default 25).

`ENTRY_THRESHOLD` defaults to 64. On the seeded paper replay (`self_improving_agent.replay`), that cutoff resolves at least 30 trades and sells the 2x initial on at least 70% of them. Raise the threshold to take fewer, cleaner names.

## What it trades

A candidate is a mint plus a venue (`pumpfun`, `stonkfun`, `gmgn_other`) and a frozen snapshot. Pump and GMGN sightings of the same mint merge. StonkFun stays its own venue even if GMGN also lists the mint. Score is a 0–100 checklist. Buys require score ≥ `ENTRY_THRESHOLD` (default 72) and a free risk slot. Honeypots, dangerous freeze or mint authority, thin liquidity, high impact, insane top-10, unvested dev supply, already-extended charts, scam names, and StonkFun reward taxes that make a net 2x unrealistic are immediate skips.

`INITIAL_SCALE_MODE=cost_basis` sells enough tokens at 2x that proceeds cover the original cost including fees. `half_at_2x` sells 50%. The remainder is the runner: trail after `TRAIL_ARM_MULT` (default 2.4) with `TRAIL_GIVEBACK_PCT` (default 0.22), or hold when 15m/1h structure is still clean. A ~35% dump off the runner peak flattens. Stops: 22% from entry, and a time stop if it never reaches ~1.4x.

2x is measured in USD (GMGN `price.price`, Pump `usd_market_cap`, or the StonkFun USD mark), so a stock-quoted StonkFun pool is comparable to a SOL curve.

## Reports and learning

Hourly notes land in `data/reports/YYYY-MM-DD/HH.md` and on stdout. Telegram and Discord webhooks fire when those env vars are set.

```markdown
# 2026-09-25 18:00 UTC

Mode: PAPER
Equity USD: 1500.00
Open risk USD: 12.40
Day drawdown: 0.80%

## Open positions
- Abcdef… pumpfun entry $0.00002 2.10x RUNNER next=trail or hold runner quote=SOL
```

Every 4 hours the learner reads taken and missed outcomes over a 48–168 hour window, writes `data/weights.json` with per-venue slices (`pumpfun`, `stonkfun`, `gmgn_other`), and appends a line to `data/playbook.md`. `ENTRY_THRESHOLD`, trail giveback, min liquidity, and max top-10 move only when the sample is at least `MIN_LEARN_SAMPLES` (default 30), and each move is clamped.

Labels: `MISSED_RUNNER`, `FAILED_2X`, `RUNNER_GAVE_BACK`, `RUNNER_CAPTURED`. Coins that were already extended when first seen are not misses.

SQLite tables: candidates, orders, fills, positions, missed_events, reports_index, weight_versions. Decisions also append to `data/events.jsonl`. Client order ids are deterministic so a retry does not double-fill.

## Tests

```bash
pytest
```

## Disclaimer

Experimental software. Crypto memecoins can go to zero. This is not financial advice.
