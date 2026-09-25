"""4-hour learner. Per-venue weights. Clamped parameter moves."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import Settings
from .scorer import DEFAULT_WEIGHTS, blank_weights
from .storage import Storage


def load_weights(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    book = blank_weights()
    path.write_text(json.dumps(book, indent=2), encoding="utf-8")
    return book


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def learn(settings: Settings, storage: Storage, hours: int | None = None, now: float | None = None) -> dict[str, Any]:
    now = now if now is not None else time.time()
    window = hours if hours is not None else settings.learn_window_hours
    window = min(168, max(48, window))
    since = now - window * 3600
    events = storage.missed_since(since)
    book = load_weights(settings.data_dir / "weights.json")
    changes: list[str] = []
    by_venue: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_venue.setdefault(event.get("venue") or "gmgn_other", []).append(event)

    positive = {"MISSED_RUNNER", "RUNNER_CAPTURED"}
    negative = {"FAILED_2X", "RUNNER_GAVE_BACK"}
    total = len(events)
    book["sample_size"] = total
    book["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))

    for venue, rows in book.get("venues", {}).items():
        sample = by_venue.get(venue, [])
        rows["sample_size"] = len(sample)
        weights = dict(DEFAULT_WEIGHTS)
        weights.update(rows.get("weights") or {})
        fails = [row for row in sample if row.get("label") in negative]
        high_top = [
            row
            for row in fails
            if (row.get("detail") or {}).get("top10_pct") not in (None, 0)
            and float((row.get("detail") or {}).get("top10_pct") or 0) >= 0.45
        ]
        if len(sample) >= 8 and len(high_top) >= max(3, len(fails) // 2) and fails:
            before = weights["concentration"]
            weights["concentration"] = _clamp(before - 1.5, -25, -4)
            if weights["concentration"] != before:
                changes.append(
                    f"{venue}: concentration {before:.2f} -> {weights['concentration']:.2f} after high-top10 failures"
                )
        rows["weights"] = weights

    params = dict(book.get("params") or {})
    entry = float(params.get("entry_threshold", settings.entry_threshold))
    trail = float(params.get("trail_giveback_pct", settings.trail_giveback_pct))
    min_liq = float(params.get("min_liq_usd", settings.min_liq_usd))
    max_top = float(params.get("max_top10_pct", settings.max_top10_pct))
    tuned = False
    if total >= settings.min_learn_samples:
        fail_n = sum(1 for row in events if row.get("label") in negative)
        miss_n = sum(1 for row in events if row.get("label") == "MISSED_RUNNER")
        gave = sum(1 for row in events if row.get("label") == "RUNNER_GAVE_BACK")
        fail_rate = fail_n / total
        if fail_rate > 0.55:
            entry = _clamp(entry + 1, 60, 85)
            max_top = _clamp(max_top - 0.02, 0.25, 0.70)
            tuned = True
            changes.append("raised entry threshold and tightened max top10 after a weak window")
        elif miss_n > fail_n and miss_n >= 10:
            entry = _clamp(entry - 1, 60, 85)
            tuned = True
            changes.append("lowered entry threshold: valid missed 2x runs outnumbered failed entries")
        if gave >= 5:
            trail = _clamp(trail - 0.02, 0.12, 0.35)
            tuned = True
            changes.append("tightened trail giveback after runner givebacks")
        if fail_rate > 0.5:
            min_liq = _clamp(min_liq * 1.1, 3000, 50000)
            tuned = True
            changes.append("raised min liquidity after failed entries")
    params.update(
        {
            "entry_threshold": round(entry, 2),
            "trail_giveback_pct": round(trail, 4),
            "min_liq_usd": round(min_liq, 2),
            "max_top10_pct": round(max_top, 4),
        }
    )
    book["params"] = params
    book["version"] = int(book.get("version") or 1) + 1
    path = settings.data_dir / "weights.json"
    path.write_text(json.dumps(book, indent=2), encoding="utf-8")
    storage.save_weights(book["version"], book)

    taken_pnl = 0.0
    missed_pnl = 0.0
    for event in events:
        multiple = float(event.get("multiple") or 1)
        if event.get("label") in {"RUNNER_CAPTURED", "RUNNER_GAVE_BACK", "FAILED_2X"}:
            taken_pnl += (multiple - 1) * settings.max_buy_usd
        if event.get("label") == "MISSED_RUNNER":
            missed_pnl += (multiple - 1) * settings.max_buy_usd

    if not tuned and not changes:
        rule = "No parameter change: evidence is weak or the sample is still thin."
    else:
        rule = "\n".join(f"- {item}" for item in changes[:3]) or "No parameter change: evidence is weak."

    venue_lines = []
    for venue in ("pumpfun", "stonkfun", "gmgn_other"):
        sample = by_venue.get(venue, [])
        wins = sum(1 for row in sample if row.get("label") in positive)
        losses = sum(1 for row in sample if row.get("label") in negative)
        venue_lines.append(f"- {venue}: n={len(sample)} positive={wins} negative={losses}")

    report = "\n".join(
        [
            f"# Learn {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(now))}",
            "",
            f"Window hours: {window}",
            f"Sample size: {total}",
            f"Weight version: {book['version']}",
            "",
            "## What worked and what we missed",
            *venue_lines,
            "",
            f"Taken-trade paper PnL (USD, size x multiple): {taken_pnl:.2f}",
            f"Missed valid 2x opportunity-cost paper PnL (USD): {missed_pnl:.2f}",
            "",
            "## Rule changes",
            rule,
            "",
        ]
    )
    stamp = time.strftime("%Y-%m-%d", time.gmtime(now))
    hour = time.strftime("%H", time.gmtime(now))
    folder = settings.data_dir / "reports" / stamp
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{hour}-learn.md"
    out.write_text(report, encoding="utf-8")
    playbook = settings.data_dir / "playbook.md"
    bullet = f"- {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(now))} v{book['version']}: {changes[0] if changes else 'no change, weak evidence'}\n"
    with playbook.open("a", encoding="utf-8") as handle:
        if playbook.stat().st_size == 0:
            handle.write("# Playbook\n\n")
        handle.write(bullet)
    storage.index_report("learn", str(out), rule[:180], now)
    print(report)
    return {"report_path": str(out), "weights": book, "changes": changes}
