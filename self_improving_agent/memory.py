"""Missed-trade memory. Extended first-seen coins are not valid misses."""

from __future__ import annotations

import time
from typing import Any

from .config import Settings
from .models import OutcomeLabel, Position, Venue
from .storage import Storage

HORIZONS = (("1h", 3600), ("4h", 4 * 3600), ("12h", 12 * 3600), ("24h", 24 * 3600))


def classify_closed(position: Position) -> OutcomeLabel | None:
    if position.state.value != "FLAT":
        return None
    if not position.initial_sold:
        return OutcomeLabel.FAILED_2X
    exit_mult = position.last_multiple
    peak = position.runner_peak_multiple or exit_mult
    captured = exit_mult >= 2.4 or (peak >= 3 and exit_mult >= 2 + 0.6 * (peak - 2))
    if captured and exit_mult >= 2.15:
        return OutcomeLabel.RUNNER_CAPTURED
    return OutcomeLabel.RUNNER_GAVE_BACK


class MissedTracker:
    def __init__(self, storage: Storage, settings: Settings) -> None:
        self.storage = storage
        self.settings = settings

    def record_closed(self, position: Position, now: float | None = None) -> OutcomeLabel | None:
        label = classify_closed(position)
        if label is None:
            return None
        self.storage.add_missed(
            {
                "mint": position.mint,
                "venue": position.venue.value,
                "label": label.value,
                "ts": now if now is not None else time.time(),
                "price_usd": position.entry_price_usd * position.last_multiple,
                "multiple": position.last_multiple,
                "reasons": position.snapshot.reasons,
                "detail": {
                    "flat_reason": position.flat_reason,
                    "peak": position.peak_multiple,
                    "runner_peak": position.runner_peak_multiple,
                    "top10_pct": position.snapshot.top10_pct,
                    "components": position.snapshot.score_components,
                },
            }
        )
        return label

    def mark_watchlist(self, prices: dict[str, float], now: float | None = None) -> list[dict[str, Any]]:
        """prices maps mint -> current usd mark. Returns newly logged missed runners."""
        now = now if now is not None else time.time()
        logged: list[dict[str, Any]] = []
        for row in self.storage.watched_candidates():
            snap = row["snapshot"]
            mint = row["mint"]
            price = prices.get(mint)
            if price is None:
                continue
            first_price = snap.get("price_usd") or 0
            extension = snap.get("extension_multiple") or 1
            if extension >= self.settings.max_extension_multiple:
                continue
            checkpoints = dict(row["checkpoints"])
            anchor = first_price or price
            elapsed = now - row["first_seen"]
            for name, seconds in HORIZONS:
                if elapsed < seconds or name in checkpoints:
                    continue
                multiple = price / anchor if anchor else 1
                checkpoints[name] = {"price_usd": price, "multiple": multiple, "ts": now}
                if multiple >= 2 and "MISSED_RUNNER" not in checkpoints:
                    event = {
                        "mint": mint,
                        "venue": row["venue"],
                        "label": OutcomeLabel.MISSED_RUNNER.value,
                        "ts": now,
                        "price_usd": price,
                        "multiple": multiple,
                        "reasons": snap.get("reasons") or snap.get("red_flags") or [],
                        "detail": {
                            "first_price": anchor,
                            "horizon": name,
                            "red_flags": snap.get("red_flags") or [],
                            "score": snap.get("score"),
                            "extended": False,
                        },
                    }
                    self.storage.add_missed(event)
                    checkpoints["MISSED_RUNNER"] = name
                    logged.append(event)
            self.storage.update_checkpoints(mint, row["venue"], checkpoints)
        return logged


def venue_of(value: str) -> Venue:
    try:
        return Venue(value)
    except ValueError:
        return Venue.gmgn_other
