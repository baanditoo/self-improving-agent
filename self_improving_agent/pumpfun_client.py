"""Pump.fun frontend API v3 client.

Auth is optional. Public coin routes are called without a bearer when
PUMPFUN_BEARER is empty. These payloads are discovery and curve state only.
They are never used as execution quotes.
"""

from __future__ import annotations

from typing import Any

import httpx

from .ratelimit import TokenBucket


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("coins", "items", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        if payload.get("mint"):
            return [payload]
    return []


def created_seconds(payload: dict[str, Any]) -> float | None:
    raw = _num(payload.get("created_timestamp"))
    if raw is None:
        return None
    if raw > 10_000_000_000:
        return raw / 1000
    return raw


def curve_progress(payload: dict[str, Any]) -> float | None:
    """Use an explicit progress field when the payload has one.

    Otherwise estimate from documented virtual SOL reserves. Pump curves start
    near 30 SOL virtual and graduate near 115 SOL virtual (about 85 SOL real).
    """
    for key in ("bonding_curve_progress", "curve_progress", "progress"):
        value = _num(payload.get(key))
        if value is None:
            continue
        return value / 100 if value > 1 else value
    virtual = _num(payload.get("virtual_sol_reserves"))
    if virtual is None:
        return None
    # lamports vs SOL
    sol = virtual / 1_000_000_000 if virtual > 1_000 else virtual
    start, end = 30.0, 115.0
    if end <= start:
        return None
    return max(0.0, min(1.0, (sol - start) / (end - start)))


def parse_coin(payload: dict[str, Any]) -> dict[str, Any]:
    complete = bool(payload.get("complete"))
    progress = curve_progress(payload)
    if complete:
        stage = "graduated"
    elif progress is not None and progress >= 0.98:
        stage = "migrating"
    else:
        stage = "curve"
    return {
        "mint": payload.get("mint") or "",
        "name": payload.get("name") or "",
        "symbol": payload.get("symbol") or "",
        "creator": payload.get("creator") or "",
        "created_ts": created_seconds(payload),
        "complete": complete,
        "stage": stage,
        "curve_progress": progress,
        "virtual_sol_reserves": _num(payload.get("virtual_sol_reserves")),
        "virtual_token_reserves": _num(payload.get("virtual_token_reserves")),
        "real_sol_reserves": _num(payload.get("real_sol_reserves")),
        "real_token_reserves": _num(payload.get("real_token_reserves")),
        "market_cap_sol": _num(payload.get("market_cap")),
        "usd_market_cap": _num(payload.get("usd_market_cap")) or _num(payload.get("market_cap_usd")),
        "ath_market_cap": _num(payload.get("ath_market_cap")),
        "total_supply": _num(payload.get("total_supply")),
        "reply_count": payload.get("reply_count"),
        "last_reply": payload.get("last_reply"),
        "nsfw": payload.get("nsfw"),
        "raydium_pool": payload.get("raydium_pool"),
        "pump_swap_pool": payload.get("pump_swap_pool"),
        "username": payload.get("username"),
        "raw": payload,
    }


def parse_holders(payload: Any) -> list[dict[str, Any]]:
    rows = _as_list(payload)
    if isinstance(payload, dict) and not rows:
        for key in ("topHolders", "holders", "accounts"):
            if isinstance(payload.get(key), list):
                rows = [row for row in payload[key] if isinstance(row, dict)]
                break
    return rows


def parse_candles(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("candlesticks", "candles", "items", "data"):
            if isinstance(payload.get(key), list):
                return [row for row in payload[key] if isinstance(row, dict)]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


class PumpFunClient:
    def __init__(
        self,
        base_url: str = "https://frontend-api-v3.pump.fun",
        bearer: str = "",
        bucket: TokenBucket | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.bearer = bearer.strip()
        self.bucket = bucket or TokenBucket(2, 5)
        self._client = httpx.AsyncClient(timeout=20, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.bearer:
            headers["Authorization"] = f"Bearer {self.bearer}"
        return headers

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        await self.bucket.acquire()
        response = await self._client.get(f"{self.base_url}{path}", params=params, headers=self._headers())
        if response.status_code in {401, 404} and path != "/coins":
            return None
        response.raise_for_status()
        return response.json()

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        await self.bucket.acquire()
        response = await self._client.post(f"{self.base_url}{path}", json=body, headers=self._headers())
        if response.status_code == 401 and not self.bearer:
            return None
        response.raise_for_status()
        return response.json()

    async def latest(self) -> list[dict[str, Any]]:
        payload = await self._get(
            "/coins",
            {"offset": 0, "limit": 30, "sort": "created_timestamp", "order": "DESC", "includeNsfw": "false"},
        )
        return [parse_coin(row) for row in _as_list(payload)]

    async def featured(self, window: str | None = None) -> list[dict[str, Any]]:
        path = f"/coins/featured/{window}" if window else "/coins/currently-live"
        payload = await self._get(path, {"offset": 0, "limit": 20, "includeNsfw": "false"})
        return [parse_coin(row) for row in _as_list(payload)]

    async def search(self, term: str, limit: int = 20) -> list[dict[str, Any]]:
        payload = await self._get(
            "/coins/search",
            {"limit": limit, "offset": 0, "searchTerm": term, "sort": "created_timestamp", "order": "desc"},
        )
        return [parse_coin(row) for row in _as_list(payload)]

    async def coin(self, mint: str) -> dict[str, Any] | None:
        payload = await self._get(f"/coins/{mint}")
        if not isinstance(payload, dict) or not payload.get("mint"):
            return None
        return parse_coin(payload)

    async def coins_by_mints(self, mints: list[str]) -> list[dict[str, Any]]:
        if not mints:
            return []
        payload = await self._post("/coins/mints", {"mints": mints})
        return [parse_coin(row) for row in _as_list(payload)]

    async def king_of_the_hill(self) -> dict[str, Any] | None:
        payload = await self._get("/coins/king-of-the-hill")
        if isinstance(payload, dict) and payload.get("mint"):
            return parse_coin(payload)
        rows = _as_list(payload)
        return parse_coin(rows[0]) if rows else None

    async def graduated(self, limit: int = 20) -> list[dict[str, Any]]:
        payload = await self._get("/coins/graduated", {"limit": limit, "offset": 0})
        return [parse_coin(row) for row in _as_list(payload)]

    async def candlesticks(self, mint: str, timeframe: int = 15, limit: int = 50) -> list[dict[str, Any]]:
        payload = await self._get(
            f"/candlesticks/{mint}",
            {"offset": 0, "limit": limit, "timeframe": timeframe},
        )
        return parse_candles(payload)

    async def top_holders(self, mint: str) -> list[dict[str, Any]]:
        payload = await self._get(f"/coins/top-holders-and-sol-balance/{mint}")
        return parse_holders(payload)
