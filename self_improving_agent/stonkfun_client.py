"""StonkFun public HTTP client.

Base path defaults to https://www.stonkfun.xyz/api/public/v1.
No API key is required. Rate limit is 300 reads/minute per IP.
Market fields are read only when present. Execution quotes still come from GMGN.
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


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def _token_rows(payload: Any) -> list[dict[str, Any]]:
    data = _unwrap(payload)
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("tokens", "items", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        if data.get("mint"):
            return [data]
    return []


def parse_token(payload: dict[str, Any]) -> dict[str, Any]:
    fee = payload.get("transferFee") if isinstance(payload.get("transferFee"), dict) else {}
    flywheel = payload.get("flywheel") if isinstance(payload.get("flywheel"), dict) else {}
    bps = _num(fee.get("bps"))
    if bps is None:
        bps = _num(payload.get("transferFeeBps"))
    quote_obj = payload.get("quote") if isinstance(payload.get("quote"), dict) else {}
    quote_symbol = payload.get("quoteSymbol") or payload.get("quote_symbol") or quote_obj.get("symbol") or ""
    launchpad = str(payload.get("launchpad") or "")
    status = str(payload.get("status") or "")
    if status == "graduated" or launchpad in {"raydium", "cpmm", "clmm"}:
        stage = "graduated"
    elif status == "aboutToGraduate":
        stage = "migrating"
    elif launchpad == "launchlab" or status == "new":
        stage = "curve"
    else:
        stage = "unknown"
    market = payload.get("market") if isinstance(payload.get("market"), dict) else {}
    return {
        "mint": payload.get("mint") or "",
        "name": payload.get("name") or "",
        "symbol": payload.get("symbol") or "",
        "quote_symbol": quote_symbol or "",
        "quote_mint": payload.get("quoteMint") or payload.get("quote_mint") or quote_obj.get("mint") or "",
        "quote_category": payload.get("quoteCategory") or payload.get("category") or quote_obj.get("category") or "",
        "launchpad": launchpad,
        "status": status,
        "stage": stage,
        "mode": payload.get("mode") or "",
        "market_cap_usd": _num(payload.get("marketCap")) or _num(market.get("marketCapUsd")) or _num(market.get("marketCap")),
        "volume_usd": _num(payload.get("volume")) or _num(payload.get("volume24h")) or _num(market.get("volume24hUsd")) or _num(market.get("volume")),
        "price_usd": _num(payload.get("priceUsd")) or _num(payload.get("price")) or _num(market.get("priceUsd")),
        "liquidity_usd": _num(payload.get("liquidity")) or _num(payload.get("liquidityUsd")) or _num(market.get("liquidityUsd")),
        "peak_market_cap_usd": _num(market.get("peakMarketCapUsd")),
        "graduation_progress": _num(payload.get("graduationProgress")),
        "transfer_fee_bps": bps,
        "flywheel_active": flywheel.get("active") if "active" in flywheel else payload.get("flywheelActive"),
        "created_at": payload.get("createdAt") or payload.get("created_at"),
        "raw": payload,
    }


class StonkFunClient:
    def __init__(
        self,
        base_url: str = "https://www.stonkfun.xyz/api/public/v1",
        bucket: TokenBucket | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.bucket = bucket or TokenBucket(4, 10)
        self._client = httpx.AsyncClient(timeout=20, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        await self.bucket.acquire()
        response = await self._client.get(f"{self.base_url}{path}", params=params, headers={"Accept": "application/json"})
        if response.status_code == 429:
            raise RuntimeError("stonkfun rate_limited")
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and payload.get("error"):
            err = payload["error"]
            raise RuntimeError(f"stonkfun {err.get('code')}: {err.get('message')}")
        return payload

    async def list_tokens(
        self,
        sort: str = "newest",
        page_size: int = 25,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"sort": sort, "pageSize": page_size, "page": 1}
        if status:
            params["status"] = status
        payload = await self._get("/tokens", params)
        return [parse_token(row) for row in _token_rows(payload)]

    async def token(self, mint: str) -> dict[str, Any] | None:
        payload = await self._get(f"/tokens/{mint}")
        data = _unwrap(payload)
        if isinstance(data, dict) and data.get("mint"):
            return parse_token(data)
        rows = _token_rows(payload)
        return rows and parse_token(rows[0]) or None

    async def market(self, mint: str) -> dict[str, Any] | None:
        """Live platform-pool market data is the token payload itself."""
        return await self.token(mint)

    async def backing(self, mint: str, launchpad: str) -> Any:
        """Backing exists only for pump-launched StonkFun tokens."""
        if launchpad.lower() != "pump":
            return None
        return await self._get(f"/tokens/{mint}/backing")


class BitqueryEnrichment:
    """Optional trades/launches enrichment. Idle unless BITQUERY_API_KEY or STONKS_API_KEY is set."""

    def __init__(self, api_key: str = "", url: str = "https://streaming.bitquery.io/graphql") -> None:
        self.api_key = api_key.strip()
        self.url = url

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def trades(self, mint: str, limit: int = 20) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        query = """
        query StonkTrades($mint: String!, $limit: Int!) {
          Trading {
            Trades(
              where: { Token: { Address: { is: $mint } } }
              limit: { count: $limit }
              orderBy: { descending: Block_Time }
            ) {
              Block { Time }
              Price { Ohlc { Close } IsQuotedInUsd }
              Volume { Usd }
              Supply { MarketCap }
              Side
              Pair { Market { Protocol } QuoteToken { Symbol } }
            }
          }
        }
        """
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                self.url,
                json={"query": query, "variables": {"mint": mint, "limit": limit}},
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            )
            response.raise_for_status()
            body = response.json()
        trades = (((body.get("data") or {}).get("Trading") or {}).get("Trades")) or []
        return [row for row in trades if isinstance(row, dict)]
