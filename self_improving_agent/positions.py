"""Position state machine: WATCH -> ENTERED -> INITIAL_2X_SOLD -> RUNNER -> FLAT."""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .models import Position, PositionState


@dataclass
class ManageResult:
    position: Position
    action: str
    sell_fraction: float = 0
    note: str = ""


def multiple(position: Position, price_usd: float) -> float:
    if position.entry_price_usd <= 0:
        return 1
    return price_usd / position.entry_price_usd


def initial_sell_fraction(position: Position, price_usd: float, settings: Settings) -> float:
    if settings.initial_scale_mode == "half_at_2x":
        return 0.5
    value = position.remaining_tokens * price_usd
    if value <= 0:
        return 0.5
    cost = position.cost_usd + position.fees_usd
    # Sell enough of the current bag that proceeds cover original cost including fees.
    fraction = cost / value
    return max(0.05, min(0.9, fraction))


def manage_position(
    position: Position,
    price_usd: float,
    now: float,
    settings: Settings,
    *,
    higher_highs: bool = False,
    volume_holding: bool = False,
    distribution: bool = False,
    volume_collapsed: bool = False,
) -> ManageResult:
    if position.state in {PositionState.FLAT, PositionState.WATCH}:
        return ManageResult(position, "noop")

    mult = multiple(position, price_usd)
    position.last_multiple = mult
    if mult > position.peak_multiple:
        position.peak_multiple = mult
    if position.initial_sold and mult >= position.runner_peak_multiple:
        position.runner_peak_multiple = mult
        position.runner_peak_ts = now

    if position.state == PositionState.ENTERED:
        if mult <= 1 - settings.stop_loss_pct:
            position.flat_reason = "stop_loss"
            return ManageResult(position, "flatten", 1.0, "stop from entry")
        held = now - position.entry_ts
        structure_dead = distribution or (not higher_highs and not volume_holding)
        if (
            held >= settings.max_hold_no_move_min * 60
            and mult < settings.time_stop_min_mult
            and structure_dead
        ):
            position.flat_reason = "time_stop"
            return ManageResult(position, "flatten", 1.0, "no 1.4x and structure died")
        if mult >= settings.take_initial_mult:
            fraction = initial_sell_fraction(position, price_usd, settings)
            position.flat_reason = ""
            return ManageResult(position, "sell_initial", fraction, "2x initial scale-out")
        return ManageResult(position, "hold")

    # Runner management after the initial 2x print.
    peak = max(position.runner_peak_multiple, mult)
    giveback = settings.trail_giveback_pct
    if volume_collapsed or distribution:
        giveback = min(giveback, 0.12)
    drop = 0.0 if peak <= 0 else (peak - mult) / peak
    window = now - (position.runner_peak_ts or now)
    hard = drop >= settings.hard_dump_pct and window <= settings.hard_dump_window_sec
    if hard:
        position.flat_reason = "hard_dump"
        return ManageResult(position, "flatten", 1.0, "35% off runner peak")

    hold_ok = higher_highs and volume_holding and not distribution and not volume_collapsed
    armed = peak >= settings.trail_arm_mult
    if hold_ok and position.snapshot.expected_path.value == "2x_then_hold":
        note = "hold runner: higher highs, volume holding, no distribution"
        if note not in position.hold_notes:
            position.hold_notes.append(note)
        return ManageResult(position, "hold_runner", note=note)
    if armed and drop >= giveback:
        position.flat_reason = "trail"
        return ManageResult(position, "flatten", 1.0, "trail giveback")
    return ManageResult(position, "hold_runner")


def mark_initial_sold(position: Position, sold_tokens: float, proceeds_usd: float, fee_usd: float) -> Position:
    position.remaining_tokens = max(0.0, position.remaining_tokens - sold_tokens)
    position.initial_sold = True
    position.initial_sold_tokens += sold_tokens
    position.realized_usd += proceeds_usd - fee_usd
    position.fees_usd += fee_usd
    position.state = PositionState.RUNNER
    position.runner_peak_multiple = max(position.runner_peak_multiple, position.last_multiple)
    return position


def mark_flat(position: Position, sold_tokens: float, proceeds_usd: float, fee_usd: float, reason: str) -> Position:
    position.remaining_tokens = max(0.0, position.remaining_tokens - sold_tokens)
    position.realized_usd += proceeds_usd - fee_usd
    position.fees_usd += fee_usd
    position.state = PositionState.FLAT
    position.flat_reason = reason or position.flat_reason
    return position
