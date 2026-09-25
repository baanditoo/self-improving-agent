"""Sizing, slot limits, and the daily drawdown kill switch."""

from __future__ import annotations

from .config import Settings
from .models import Position, TokenSnapshot


def day_drawdown_pct(equity_start_usd: float, equity_now_usd: float) -> float:
    if equity_start_usd <= 0:
        return 0
    return max(0.0, (equity_start_usd - equity_now_usd) / equity_start_usd * 100)


def kill_switch_active(equity_start_usd: float, equity_now_usd: float, settings: Settings) -> bool:
    return day_drawdown_pct(equity_start_usd, equity_now_usd) > settings.max_daily_dd_pct


def approve_entry(
    snapshot: TokenSnapshot,
    settings: Settings,
    open_positions: list[Position],
    equity_start_usd: float,
    equity_now_usd: float,
    cash_usd: float,
) -> tuple[bool, str, float]:
    if kill_switch_active(equity_start_usd, equity_now_usd, settings):
        return False, "kill_switch", 0
    if any(p.mint == snapshot.mint and p.state.value != "FLAT" for p in open_positions):
        return False, "already_open", 0
    if sum(1 for p in open_positions if p.state.value != "FLAT") >= settings.max_positions:
        return False, "max_positions", 0
    if snapshot.expected_path.value == "skip" or snapshot.score < settings.entry_threshold:
        return False, "score", 0
    if cash_usd + 1e-9 < settings.max_buy_usd:
        return False, "cash", 0
    notional, _fee = settings.ticket_usd()
    sol_usd = snapshot.sol_usd or settings.sol_price_usd
    if sol_usd <= 0 or notional <= 0:
        return False, "size", 0
    # Full $10 ticket only. Never add to a loser.
    return True, "ok", notional / sol_usd
