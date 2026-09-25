"""Deterministic paper replay used to measure the 2x initial-sold hit ratio.

Each coin has a hidden path. Features correlate with that path but do not copy
the scorer, so a threshold that clears 70% is a real filter, not a label leak.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .agent import TradingAgent
from .config import Settings
from .discovery import Discovery
from .models import Stage, TokenSnapshot, Venue


@dataclass
class TapeCoin:
    snapshot: TokenSnapshot
    multiples: list[float]


class TapeDiscovery(Discovery):
    def __init__(self, batches: list[list[TokenSnapshot]]) -> None:
        self.batches = batches

    async def collect(self, now: float | None = None) -> list[TokenSnapshot]:
        if not self.batches:
            return []
        return self.batches.pop(0)


def build_tape(n: int = 180, seed: int = 8102) -> list[TapeCoin]:
    rng = random.Random(seed)
    coins: list[TapeCoin] = []
    for i in range(n):
        top10 = rng.uniform(0.08, 0.55)
        sniper = rng.uniform(0.0, 0.35)
        bundler = rng.uniform(0.0, 0.35)
        extension = rng.choice([1.1, 1.2, 1.4, 1.8, 2.2, 3.5, 6.0])
        holders = rng.randint(20, 220)
        smart = rng.uniform(0, 9000)
        ratio = rng.uniform(0.6, 2.8)
        liq = rng.uniform(4000, 80000)
        higher = rng.random() < 0.55
        volume_holding = rng.random() < 0.6
        distribution = rng.random() < 0.25
        latent = (
            1.3 * (smart / 5000)
            + 0.9 * (holders / 140)
            + 0.8 * max(ratio - 1, 0)
            + 0.4 * (1 if higher and volume_holding and not distribution else 0)
            - 2.4 * top10
            - 1.6 * sniper
            - 1.5 * bundler
            - 1.3 * max(extension - 1.2, 0)
            + rng.uniform(-0.35, 0.35)
        )
        runner = latent > 1.05 and extension < 5 and top10 < 0.45
        snap = TokenSnapshot(
            mint=f"Tape{i:04d}Mint111111111111111111111111111111",
            venue=Venue.pumpfun if i % 3 == 0 else Venue.stonkfun if i % 3 == 1 else Venue.gmgn_other,
            stage=Stage.curve,
            quote_asset="SOL" if i % 3 != 1 else "NVDAx",
            name=f"Tape {i}",
            symbol=f"T{i}",
            age_sec=rng.uniform(180, 4000),
            liquidity_usd=liq,
            curve_progress=rng.uniform(0.3, 0.7),
            volume_usd=rng.uniform(2000, 40000),
            unique_wallets=holders,
            holder_count=holders,
            top10_pct=top10,
            sniper_pct=sniper,
            bundler_pct=bundler,
            smart_money_buy_usd=smart,
            price_usd=0.00002,
            price_vs_ath=0.7,
            extension_multiple=extension,
            buy_sell_ratio=ratio,
            honeypot=False,
            renounced_mint=True,
            renounced_freeze=True,
            dev_hold_pct=0.02,
            pump_complete=False if i % 3 == 0 else None,
            higher_highs=higher,
            volume_holding=volume_holding,
            distribution=distribution,
            sol_usd=150,
        )
        multiples = [1.15, 2.15, 2.8] if runner else [0.92, 0.78, 0.7]
        coins.append(TapeCoin(snap, multiples))
    return coins


async def run_replay(agent: TradingAgent, coins: list[TapeCoin], start: float = 1_700_000_000) -> dict[str, float]:
    batch = 12
    batches = [ [c.snapshot for c in coins[i : i + batch]] for i in range(0, len(coins), batch) ]
    agent.discovery = TapeDiscovery(batches)
    now = start
    marks: dict[str, float] = {}
    paths = {c.snapshot.mint: c.multiples for c in coins}
    step = 0
    while True:
        await agent.tick(now, marks)
        open_positions = [p for p in agent.positions() if p.state.value != "FLAT"]
        if not open_positions and not agent.discovery.batches:
            break
        step += 1
        now += 20
        for pos in agent.positions():
            if pos.state.value == "FLAT":
                continue
            path = paths.get(pos.mint) or [1.0]
            idx = min(step - 1, len(path) - 1)
            marks[pos.mint] = pos.entry_price_usd * path[idx]
        if step > 8 and not agent.discovery.batches:
            # Force the last mark so unresolved names take their final print.
            await agent.tick(now, marks)
            break
    stats = agent.hit_ratio()
    stats["equity_usd"] = agent.equity_usd()
    stats["cash_usd"] = agent.cash_usd()
    return stats
