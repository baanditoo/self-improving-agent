"""CLI. `python -m self_improving_agent run` starts the scheduler."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from .agent import build_agent
from .learner import load_weights
from .live_hitrate import fetch_stonk_hit_rate
from .replay import build_tape, run_replay


def _print_status(agent) -> int:
    positions = agent.positions()
    open_positions = [pos for pos in positions if pos.state.value != "FLAT"]
    payload = {
        "mode": agent.settings.trading_mode(),
        "portfolio_usd": agent.settings.paper_equity_usd,
        "max_buy_usd": agent.settings.max_buy_usd,
        "cash_usd": round(agent.cash_usd(), 4),
        "equity_usd": round(agent.equity_usd(positions), 4),
        "hit_ratio": agent.hit_ratio(),
        "open": len(open_positions),
        "positions": [
            {
                "mint": pos.mint,
                "venue": pos.venue.value,
                "state": pos.state.value,
                "multiple": round(pos.last_multiple, 4),
                "quote": pos.quote_asset,
            }
            for pos in open_positions
        ],
        "weights_version": load_weights(agent.settings.data_dir / "weights.json").get("version"),
    }
    print(json.dumps(payload, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Self-improving Solana memecoin agent")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="Start discovery, management, reports, and learning")
    sub.add_parser("paper", help="Alias of run with live trading forced off")
    sub.add_parser("status", help="Show mode, equity, and open positions")
    report = sub.add_parser("report", help="Write an operator report")
    report.add_argument("--hours", type=int, default=1)
    learn = sub.add_parser("learn", help="Run one learning pass")
    learn.add_argument("--hours", type=int, default=4)
    sub.add_parser("replay", help="Run the seeded paper tape and print the 2x hit ratio")
    sub.add_parser("hitrate", help="Measure 2x prints on older StonkFun launches")
    args = parser.parse_args(argv)

    if args.cmd == "paper":
        agent = build_agent(force_paper=True)
    else:
        agent = build_agent()

    if args.cmd in {"run", "paper"}:
        asyncio.run(agent.run())
        return 0
    if args.cmd == "status":
        return _print_status(agent)
    if args.cmd == "report":
        agent.hourly()
        return 0
    if args.cmd == "learn":
        agent.learn_once(args.hours)
        return 0
    if args.cmd == "replay":
        replay_agent = build_agent(force_paper=True, data_dir=Path(tempfile.mkdtemp(prefix="paper-replay-")))
        stats = asyncio.run(run_replay(replay_agent, build_tape()))
        print(json.dumps(stats, indent=2))
        return 0 if stats["hit_ratio"] >= 0.70 and stats["resolved"] >= 30 else 1
    if args.cmd == "hitrate":
        measured = fetch_stonk_hit_rate()
        print(json.dumps(measured, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
