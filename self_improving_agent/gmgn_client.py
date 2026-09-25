"""GMGN OpenAPI client.

Read routes use normal auth (X-APIKEY + timestamp + client_id).
Swap, order query, and strategy routes use critical auth (X-Signature).
Quote uses normal auth, matching the official OpenApiClient.
GMGN is IPv4-only: the HTTP transport binds 0.0.0.0.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from typing import Any
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from .config import CHAIN, WSOL_MINT
from .ratelimit import TokenBucket

NORMAL_PATHS = {
    "token_info": "/v1/token/info",
    "token_security": "/v1/token/security",
    "token_pool": "/v1/token/pool_info",
    "token_holders": "/v1/market/token_top_holders",
    "token_traders": "/v1/market/token_top_traders",
    "token_kline": "/v1/market/token_kline",
    "trending": "/v1/market/rank",
    "signals": "/v1/market/token_signal",
    "smart_money": "/v1/user/smartmoney",
    "wallet_activity": "/v1/user/wallet_activity",
    "quote": "/v1/trade/quote",
    "gas_price": "/v1/trade/gas_price",
}
SIGNED_PATHS = {
    "swap": "/v1/trade/swap",
    "query_order": "/v1/trade/query_order",
    "strategy_create": "/v1/trade/strategy/create",
    "strategy_orders": "/v1/trade/strategy/orders",
    "strategy_cancel": "/v1/trade/strategy/cancel",
}


class GmgnError(RuntimeError):
    pass


def _sorted_query(params: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in sorted(params):
        value = params[key]
        if isinstance(value, list):
            for item in sorted(str(v) for v in value):
                parts.append(f"{quote(str(key), safe='')}={quote(item, safe='')}")
        else:
            parts.append(f"{quote(str(key), safe='')}={quote(str(value), safe='')}")
    return "&".join(parts)


def build_signature_message(sub_path: str, params: dict[str, Any], body: str, timestamp: int) -> str:
    return f"{sub_path}:{_sorted_query(params)}:{body}:{timestamp}"


def sign_message(message: str, pem: str) -> str:
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    data = message.encode()
    if isinstance(key, ed25519.Ed25519PrivateKey):
        signature = key.sign(data)
    elif isinstance(key, rsa.RSAPrivateKey):
        signature = key.sign(
            data,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
            hashes.SHA256(),
        )
    else:
        raise GmgnError("GMGN private key must be Ed25519 or RSA")
    return base64.b64encode(signature).decode()


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _yes(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"yes", "true", "1"}:
        return True
    if text in {"no", "false", "0"}:
        return False
    return None


def normalize_token_info(payload: dict[str, Any]) -> dict[str, Any]:
    """Map documented token-info fields. Missing keys stay None."""
    price = payload.get("price") if isinstance(payload.get("price"), dict) else {}
    stat = payload.get("stat") if isinstance(payload.get("stat"), dict) else {}
    dev = payload.get("dev") if isinstance(payload.get("dev"), dict) else {}
    tags = payload.get("wallet_tags_stat") if isinstance(payload.get("wallet_tags_stat"), dict) else {}
    pool = payload.get("pool") if isinstance(payload.get("pool"), dict) else {}
    buys = _num(price.get("buys_5m")) or _num(price.get("buys_1h"))
    sells = _num(price.get("sells_5m")) or _num(price.get("sells_1h"))
    ratio = None
    if buys is not None and sells is not None:
        ratio = buys / sells if sells else buys
    current = _num(price.get("price"))
    ath = _num(payload.get("ath_price"))
    price_1h = _num(price.get("price_1h"))
    extension = None
    if current and price_1h and price_1h > 0:
        extension = current / price_1h
    migration = _num(payload.get("migration_market_cap"))
    # market cap is price * circulating_supply when both exist (documented).
    circ = _num(payload.get("circulating_supply"))
    mcap = current * circ if current is not None and circ is not None else None
    if extension is None and mcap and migration and migration > 0:
        extension = mcap / migration
    holders = payload.get("holder_count")
    if holders is None:
        holders = stat.get("holder_count")
    return {
        "address": payload.get("address"),
        "name": payload.get("name") or "",
        "symbol": payload.get("symbol") or "",
        "decimals": payload.get("decimals"),
        "price_usd": current,
        "liquidity_usd": _num(payload.get("liquidity")) or _num(pool.get("liquidity")),
        "holder_count": int(holders) if isinstance(holders, (int, float)) else None,
        "creation_timestamp": _num(payload.get("creation_timestamp")),
        "launchpad": payload.get("launchpad") or "",
        "launchpad_status": payload.get("launchpad_status"),
        "launchpad_progress": _num(payload.get("launchpad_progress")),
        "ath_price": ath,
        "price_vs_ath": (current / ath) if current and ath else None,
        "extension_multiple": extension,
        "top10_pct": _num(stat.get("top_10_holder_rate")) or _num(dev.get("top_10_holder_rate")),
        "bundler_pct": _num(stat.get("top_bundler_trader_percentage")),
        "sniper_wallets": tags.get("sniper_wallets"),
        "smart_wallets": tags.get("smart_wallets"),
        "volume_usd": _num(price.get("volume_5m")) or _num(price.get("volume_1h")) or _num(price.get("volume_24h")),
        "buy_sell_ratio": ratio,
        "quote_symbol": pool.get("quote_symbol") or "SOL",
        "quote_address": pool.get("quote_address") or "",
        "dev_hold_pct": _num(stat.get("creator_hold_rate")) or _num(stat.get("dev_team_hold_rate")),
        "creator_status": dev.get("creator_token_status"),
        "buys": buys,
        "sells": sells,
    }


def normalize_security(payload: dict[str, Any]) -> dict[str, Any]:
    honeypot = _yes(payload.get("is_honeypot"))
    return {
        "honeypot": bool(honeypot) if honeypot is not None else False,
        "renounced_mint": payload.get("renounced_mint") if isinstance(payload.get("renounced_mint"), bool) else _yes(payload.get("renounced_mint")),
        "renounced_freeze": payload.get("renounced_freeze_account")
        if isinstance(payload.get("renounced_freeze_account"), bool)
        else _yes(payload.get("renounced_freeze_account")),
        "buy_tax": _num(payload.get("buy_tax")),
        "sell_tax": _num(payload.get("sell_tax")),
        "top10_pct": _num(payload.get("top_10_holder_rate")),
        "rug_ratio": _num(payload.get("rug_ratio")),
        "bundler_pct": _num(payload.get("bundler_trader_amount_rate")),
        "sniper_count": payload.get("sniper_count"),
        "dev_hold_pct": _num(payload.get("creator_balance_rate")) or _num(payload.get("dev_team_hold_rate")),
    }


def normalize_quote(payload: dict[str, Any]) -> dict[str, Any]:
    """Documented order-quote fields only."""
    return {
        "input_token": payload.get("input_token"),
        "output_token": payload.get("output_token"),
        "input_amount": payload.get("input_amount"),
        "output_amount": payload.get("output_amount"),
        "min_output_amount": payload.get("min_output_amount"),
        "slippage": payload.get("slippage"),
    }


def extract_rank(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("rank"), list):
        return [row for row in payload["rank"] if isinstance(row, dict)]
    nested = payload.get("data")
    if isinstance(nested, dict) and isinstance(nested.get("rank"), list):
        return [row for row in nested["rank"] if isinstance(row, dict)]
    return []


class GmgnClient:
    def __init__(
        self,
        api_key: str,
        private_key_pem: str = "",
        host: str = "https://openapi.gmgn.ai",
        bucket: TokenBucket | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.private_key_pem = private_key_pem.replace("\\n", "\n") if private_key_pem else ""
        self.host = host.rstrip("/")
        self.bucket = bucket or TokenBucket(8, 16)
        self._client = httpx.AsyncClient(
            timeout=20,
            transport=transport
            or httpx.AsyncHTTPTransport(local_address="0.0.0.0"),
        )
        self.swap_calls = 0

    async def aclose(self) -> None:
        await self._client.aclose()

    def _auth_query(self, extra: dict[str, Any]) -> dict[str, Any]:
        params = dict(extra)
        params["timestamp"] = int(time.time())
        params["client_id"] = str(uuid.uuid4())
        return params

    async def _request(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        signed: bool = False,
        cost: float = 1,
    ) -> Any:
        await self.bucket.acquire(cost)
        params = self._auth_query(query or {})
        body_text = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = {
            "X-APIKEY": self.api_key,
            "Content-Type": "application/json",
            "User-Agent": "self-improving-agent/0.2.0",
        }
        if signed:
            if not self.private_key_pem:
                raise GmgnError("GMGN_PRIVATE_KEY is required for critical-auth routes")
            message = build_signature_message(path, params, body_text, int(params["timestamp"]))
            headers["X-Signature"] = sign_message(message, self.private_key_pem)
        url = f"{self.host}{path}"
        response = await self._client.request(
            method,
            url,
            params=params,
            content=body_text.encode() if body is not None else None,
            headers=headers,
        )
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise GmgnError(f"{method} {path} non-JSON HTTP {response.status_code}") from exc
        if isinstance(payload, dict) and payload.get("code") not in (0, "0", None) and "data" not in payload:
            raise GmgnError(f"{method} {path} failed code={payload.get('code')} error={payload.get('error')}")
        if isinstance(payload, dict) and payload.get("code") not in (0, "0", None):
            if payload.get("code") not in (0, "0"):
                raise GmgnError(
                    f"{method} {path} failed code={payload.get('code')} error={payload.get('error')}"
                )
        if isinstance(payload, dict) and "data" in payload and payload.get("code") in (0, "0"):
            return payload["data"]
        return payload

    async def get_token_info(self, address: str, chain: str = CHAIN) -> Any:
        return await self._request("GET", NORMAL_PATHS["token_info"], {"chain": chain, "address": address})

    async def get_token_security(self, address: str, chain: str = CHAIN) -> Any:
        return await self._request("GET", NORMAL_PATHS["token_security"], {"chain": chain, "address": address})

    async def get_token_pool(self, address: str, chain: str = CHAIN) -> Any:
        return await self._request("GET", NORMAL_PATHS["token_pool"], {"chain": chain, "address": address})

    async def get_token_holders(self, address: str, chain: str = CHAIN, limit: int = 20) -> Any:
        return await self._request(
            "GET",
            NORMAL_PATHS["token_holders"],
            {"chain": chain, "address": address, "limit": limit},
            cost=5,
        )

    async def get_token_traders(self, address: str, chain: str = CHAIN, limit: int = 20) -> Any:
        return await self._request(
            "GET",
            NORMAL_PATHS["token_traders"],
            {"chain": chain, "address": address, "limit": limit},
            cost=5,
        )

    async def get_kline(
        self,
        address: str,
        resolution: str = "1m",
        chain: str = CHAIN,
        from_ms: int | None = None,
        to_ms: int | None = None,
    ) -> Any:
        query: dict[str, Any] = {"chain": chain, "address": address, "resolution": resolution}
        if from_ms is not None:
            query["from"] = from_ms
        if to_ms is not None:
            query["to"] = to_ms
        return await self._request("GET", NORMAL_PATHS["token_kline"], query)

    async def get_trending(self, interval: str, chain: str = CHAIN, limit: int = 30) -> Any:
        return await self._request(
            "GET",
            NORMAL_PATHS["trending"],
            {"chain": chain, "interval": interval, "limit": limit, "order_by": "volume", "direction": "desc"},
        )

    async def get_signals(self, groups: list[dict[str, Any]], chain: str = CHAIN) -> Any:
        return await self._request("POST", NORMAL_PATHS["signals"], {"chain": chain}, {"groups": groups})

    async def get_smart_money(self, chain: str = CHAIN, limit: int = 20) -> Any:
        return await self._request("GET", NORMAL_PATHS["smart_money"], {"chain": chain, "limit": limit})

    async def get_wallet_activity(self, wallet: str, chain: str = CHAIN) -> Any:
        return await self._request(
            "GET", NORMAL_PATHS["wallet_activity"], {"chain": chain, "wallet_address": wallet}
        )

    async def quote(
        self,
        input_token: str,
        output_token: str,
        input_amount: str,
        from_address: str,
        slippage: float = 0.1,
        chain: str = CHAIN,
    ) -> Any:
        return await self._request(
            "GET",
            NORMAL_PATHS["quote"],
            {
                "chain": chain,
                "from_address": from_address,
                "input_token": input_token,
                "output_token": output_token,
                "input_amount": input_amount,
                "slippage": slippage,
            },
        )

    async def swap(self, params: dict[str, Any]) -> Any:
        self.swap_calls += 1
        body = {"chain": CHAIN, **params}
        return await self._request("POST", SIGNED_PATHS["swap"], body=body, signed=True)

    async def query_order(self, order_id: str, chain: str = CHAIN) -> Any:
        return await self._request(
            "GET", SIGNED_PATHS["query_order"], {"order_id": order_id, "chain": chain}, signed=True
        )

    async def create_strategy_order(self, params: dict[str, Any]) -> Any:
        body = {"chain": CHAIN, **params}
        return await self._request("POST", SIGNED_PATHS["strategy_create"], body=body, signed=True)

    async def get_strategy_orders(self, chain: str = CHAIN) -> Any:
        return await self._request("GET", SIGNED_PATHS["strategy_orders"], {"chain": chain}, signed=True)

    async def cancel_strategy_order(self, order_id: str, from_address: str, chain: str = CHAIN) -> Any:
        return await self._request(
            "POST",
            SIGNED_PATHS["strategy_cancel"],
            body={"chain": chain, "from_address": from_address, "order_id": order_id},
            signed=True,
        )

    async def sol_price_usd(self, fallback: float) -> float:
        try:
            info = await self.get_token_info(WSOL_MINT)
            if isinstance(info, dict):
                price = _num((info.get("price") or {}).get("price") if isinstance(info.get("price"), dict) else None)
                if price and price > 0:
                    return price
        except Exception:
            return fallback
        return fallback
