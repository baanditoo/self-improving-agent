"""Hit rate of real launches: peak market cap at least 2x the start market cap.

This is the share of coins that ever printed a 2x from the launch mark, which
is the print required before the initial scale-out can fill. It is not the
seeded replay.
"""

from __future__ import annotations

import time
import urllib.request
from typing import Any


def summarize(rows: list[dict[str, Any]]) -> dict[str, float]:
    usable = [row for row in rows if float(row.get("mult") or 0) > 0]
    n = len(usable)
    hits = sum(1 for row in usable if float(row["mult"]) >= 2)
    return {
        "n": n,
        "initials_2x": hits,
        "hit_ratio": (hits / n) if n else 0.0,
    }


def _get(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "self-improving-agent"})
    with urllib.request.urlopen(request, timeout=30) as response:
        import json
        return json.load(response)


def fetch_stonk_hit_rate(pages: tuple[int, ...] = (700, 1100, 1800), page_size: int = 20) -> dict[str, float]:
    base = "https://www.stonkfun.xyz/api/public/v1"
    rows: list[dict[str, Any]] = []
    for page in pages:
        payload = _get(f"{base}/launches?pageSize={page_size}&page={page}")
        launches = (payload.get("data") or {}).get("launches") or []
        for launch in launches:
            start = float(launch.get("startMarketCapUsd") or 0)
            mint = launch.get("mint")
            if start <= 0 or not mint:
                continue
            try:
                detail = _get(f"{base}/tokens/{mint}")
            except Exception:
                time.sleep(0.5)
                continue
            token = ((detail.get("data") or {}).get("token") or {})
            market = token.get("market") or {}
            peak = float(market.get("peakMarketCapUsd") or 0)
            if peak <= 0:
                continue
            rows.append({"mult": peak / start, "symbol": launch.get("symbol"), "mode": launch.get("mode")})
            time.sleep(0.15)
        time.sleep(0.3)
    stats = summarize(rows)
    stats["source"] = "stonkfun"  # type: ignore[assignment]
    return stats
