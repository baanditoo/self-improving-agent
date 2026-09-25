"""Domain models. Snapshots are frozen at decision time."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Venue(str, Enum):
    pumpfun = "pumpfun"
    stonkfun = "stonkfun"
    gmgn_other = "gmgn_other"


class Stage(str, Enum):
    curve = "curve"
    migrating = "migrating"
    graduated = "graduated"
    unknown = "unknown"


class PositionState(str, Enum):
    WATCH = "WATCH"
    ENTERED = "ENTERED"
    INITIAL_2X_SOLD = "INITIAL_2X_SOLD"
    RUNNER = "RUNNER"
    FLAT = "FLAT"


class ExpectedPath(str, Enum):
    trail = "2x_then_trail"
    hold = "2x_then_hold"
    skip = "skip"


class OutcomeLabel(str, Enum):
    MISSED_RUNNER = "MISSED_RUNNER"
    FAILED_2X = "FAILED_2X"
    RUNNER_GAVE_BACK = "RUNNER_GAVE_BACK"
    RUNNER_CAPTURED = "RUNNER_CAPTURED"


class TokenSnapshot(BaseModel):
    mint: str
    venue: Venue
    stage: Stage = Stage.unknown
    quote_asset: str = "SOL"
    quote_mint: str = ""
    name: str = ""
    symbol: str = ""
    age_sec: float | None = None
    liquidity_usd: float | None = None
    curve_progress: float | None = None
    volume_usd: float | None = None
    unique_wallets: int | None = None
    top10_pct: float | None = None
    sniper_pct: float | None = None
    bundler_pct: float | None = None
    smart_money_buy_usd: float | None = None
    holder_count: int | None = None
    price_usd: float | None = None
    price_vs_ath: float | None = None
    extension_multiple: float | None = None
    buy_sell_ratio: float | None = None
    honeypot: bool = False
    renounced_mint: bool | None = None
    renounced_freeze: bool | None = None
    buy_tax: float | None = None
    sell_tax: float | None = None
    rug_ratio: float | None = None
    dev_hold_pct: float | None = None
    pump_complete: bool | None = None
    stonk_reward_tax: bool = False
    transfer_fee_bps: float | None = None
    flywheel_active: bool | None = None
    launchpad: str = ""
    higher_highs: bool = False
    volume_holding: bool = False
    distribution: bool = False
    price_impact_pct: float | None = None
    sol_usd: float = 150.0
    score: float = 0
    score_components: dict[str, float] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    expected_path: ExpectedPath = ExpectedPath.skip
    raw: dict[str, Any] = Field(default_factory=dict)

    def mark_usd(self) -> float | None:
        return self.price_usd


class ScoreDecision(BaseModel):
    score: float
    components: dict[str, float]
    reasons: list[str]
    red_flags: list[str]
    expected_path: ExpectedPath
    rejected: bool


class Position(BaseModel):
    mint: str
    venue: Venue
    state: PositionState = PositionState.ENTERED
    quote_asset: str = "SOL"
    entry_price_usd: float
    size_tokens: float
    remaining_tokens: float
    cost_sol: float
    cost_usd: float
    fees_usd: float = 0
    entry_tx: str = ""
    entry_ts: float
    snapshot: TokenSnapshot
    thesis: str = ""
    peak_multiple: float = 1.0
    runner_peak_multiple: float = 1.0
    runner_peak_ts: float = 0
    last_multiple: float = 1.0
    initial_sold: bool = False
    initial_sold_tokens: float = 0
    realized_usd: float = 0
    client_order_ids: list[str] = Field(default_factory=list)
    hold_notes: list[str] = Field(default_factory=list)
    strategy_order_id: str = ""
    flat_reason: str = ""


class OrderRecord(BaseModel):
    client_order_id: str
    mint: str
    side: Literal["buy", "sell"]
    mode: Literal["paper", "live"]
    status: str
    qty_tokens: float = 0
    price_usd: float = 0
    fee_usd: float = 0
    tx: str = ""
    ts: float
    raw: dict[str, Any] = Field(default_factory=dict)


class MissedEvent(BaseModel):
    mint: str
    venue: Venue
    label: OutcomeLabel
    ts: float
    price_usd: float | None = None
    multiple: float | None = None
    reasons: list[str] = Field(default_factory=list)
    detail: dict[str, Any] = Field(default_factory=dict)
