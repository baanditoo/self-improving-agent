"""Hourly operator reports."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .config import Settings
from .models import Position
from .risk import day_drawdown_pct
from .storage import Storage


def _fmt_positions(positions: list[Position]) -> list[str]:
    lines = []
    for pos in positions:
        if pos.state.value == "FLAT":
            continue
        nxt = "arm 2x scale-out" if pos.state.value == "ENTERED" else "trail or hold runner"
        lines.append(
            f"- {pos.mint[:6]}… {pos.venue.value} entry ${pos.entry_price_usd:.6g} "
            f"{pos.last_multiple:.2f}x {pos.state.value} next={nxt} quote={pos.quote_asset}"
        )
    return lines or ["- none"]


def render_hourly(
    settings: Settings,
    storage: Storage,
    positions: list[Position],
    equity_usd: float,
    equity_start_usd: float,
    observations: list[str],
    anomalies: list[str],
    now: float | None = None,
) -> str:
    now = now if now is not None else time.time()
    stamp = datetime.fromtimestamp(now, tz=timezone.utc)
    since = now - 3600
    fills = storage.recent_fills(since)
    candidates = storage.list_candidates(since)
    open_risk = sum(
        p.remaining_tokens * p.entry_price_usd * p.last_multiple
        for p in positions
        if p.state.value != "FLAT"
    )
    by_venue: dict[str, list[str]] = {"pumpfun": [], "stonkfun": [], "gmgn_other": []}
    for row in candidates:
        if row["decision"] not in {"skip", "watch"}:
            continue
        snap = row["snapshot"]
        why = ",".join(snap.get("red_flags") or snap.get("reasons") or ["below_threshold"])
        bucket = by_venue.setdefault(row["venue"], [])
        if len(bucket) < 5:
            bucket.append(f"{snap.get('symbol') or row['mint'][:6]} score={row['score']} {why}")
    tape = {
        "pumpfun": "Pump tape: watching curve progress and graduation, not reply count.",
        "stonkfun": "Stonk tape: size off the quote asset, skip reward-tax if 2x net is unrealistic.",
        "gmgn_other": "GMGN tape: trending and smart-money prints only after the security checklist.",
    }
    dd = day_drawdown_pct(equity_start_usd, equity_usd)
    lines = [
        f"# {stamp:%Y-%m-%d %H}:00 UTC",
        "",
        f"Mode: {settings.trading_mode()}",
        f"Equity USD: {equity_usd:.2f}",
        f"Open risk USD: {open_risk:.2f}",
        f"Day drawdown: {dd:.2f}%",
        "",
        "## Open positions",
        *(_fmt_positions(positions)),
        "",
        "## Last hour fills",
    ]
    if fills:
        lines.extend(
            f"- {row['side']} {row['mint'][:6]}… qty={row['qty_tokens']:.4g} px={row['price_usd']:.6g}"
            for row in fills[:12]
        )
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Skipped candidates")
    for venue, rows in by_venue.items():
        lines.append(f"### {venue}")
        lines.extend(f"- {item}" for item in rows) if rows else lines.append("- none")
    lines.append("")
    lines.append("## Tape")
    for venue, text in tape.items():
        lines.append(f"- {text}")
    lines.append("")
    lines.append("## Anomalies")
    lines.extend(f"- {item}" for item in anomalies) if anomalies else lines.append("- none")
    lines.append("")
    lines.append("## Observations")
    for idx, text in enumerate(observations[:3], start=1):
        lines.append(f"{idx}. {text}")
    while len(observations) < 3:
        observations.append("No extra observation this hour.")
    if len([line for line in lines if line[:2].isdigit()]) < 3:
        pass
    return "\n".join(lines) + "\n"


def write_hourly(settings: Settings, body: str, now: float | None = None) -> Path:
    now = now if now is not None else time.time()
    stamp = datetime.fromtimestamp(now, tz=timezone.utc)
    folder = settings.data_dir / "reports" / stamp.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stamp.strftime('%H')}.md"
    path.write_text(body, encoding="utf-8")
    print(body)
    return path


async def notify(settings: Settings, body: str) -> None:
    async with httpx.AsyncClient(timeout=15) as client:
        if settings.telegram_bot_token and settings.telegram_chat_id:
            await client.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                json={"chat_id": settings.telegram_chat_id, "text": body[:3500]},
            )
        if settings.discord_webhook_url:
            await client.post(settings.discord_webhook_url, json={"content": body[:1800]})


def default_observations(positions: list[Position], skipped: int) -> list[str]:
    open_n = sum(1 for p in positions if p.state.value != "FLAT")
    runners = sum(1 for p in positions if p.state.value == "RUNNER")
    return [
        f"{open_n} open, {runners} already through the 2x initial.",
        f"{skipped} scored names were left on the watchlist this pass.",
        "Edge stays 2x-the-initial then a managed runner. No chase, no dead bag.",
    ]
