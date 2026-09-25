"""Paper fills from a GMGN quote plus slippage. Live swaps only when LIVE_TRADING is true."""

from __future__ import annotations

import time
from typing import Any

from .config import WSOL_MINT, Settings
from .gmgn_client import GmgnClient, normalize_quote
from .models import Position, TokenSnapshot
from .positions import mark_flat, mark_initial_sold
from .storage import Storage


def client_order_id(mint: str, side: str, intent: str, entry_ts: float) -> str:
    return f"{side}:{intent}:{mint}:{int(entry_ts)}"


def _lamports(sol_amount: float) -> str:
    return str(int(round(sol_amount * 1_000_000_000)))


def _tokens_raw(amount: float, decimals: int = 6) -> str:
    return str(int(round(amount * (10**decimals))))


class PaperBroker:
    """Simulates fills. Does not call swap."""

    def __init__(self, settings: Settings, storage: Storage, gmgn: GmgnClient | None = None) -> None:
        self.settings = settings
        self.storage = storage
        self.gmgn = gmgn

    async def quote_buy(self, snapshot: TokenSnapshot, size_sol: float) -> dict[str, Any]:
        amount = _lamports(size_sol)
        if self.gmgn and self.settings.gmgn_api_key and self.settings.wallet_address:
            raw = await self.gmgn.quote(
                WSOL_MINT,
                snapshot.mint,
                amount,
                self.settings.wallet_address,
                slippage=self.settings.paper_slippage_pct,
            )
            if isinstance(raw, dict):
                parsed = normalize_quote(raw)
                parsed["raw"] = raw
                return parsed
        return {
            "input_token": WSOL_MINT,
            "output_token": snapshot.mint,
            "input_amount": amount,
            "output_amount": None,
            "min_output_amount": None,
            "slippage": self.settings.paper_slippage_pct,
        }

    def _tokens_from_quote(self, quote: dict[str, Any], snapshot: TokenSnapshot, size_sol: float) -> float:
        raw_out = quote.get("min_output_amount") or quote.get("output_amount")
        decimals = 6
        if raw_out:
            try:
                return int(raw_out) / (10**decimals)
            except (TypeError, ValueError):
                pass
        price = snapshot.price_usd or 0
        if price <= 0:
            return 0
        usd = size_sol * snapshot.sol_usd
        slip = 1 - self.settings.paper_slippage_pct
        return (usd * slip) / price

    async def buy(self, snapshot: TokenSnapshot, size_sol: float) -> Position | None:
        if self.gmgn:
            # Paper mode must never submit a swap, even if a client is attached.
            assert not self.settings.live_trading
        quote = await self.quote_buy(snapshot, size_sol)
        tokens = self._tokens_from_quote(quote, snapshot, size_sol)
        if tokens <= 0 or not snapshot.price_usd:
            return None
        now = time.time()
        sol_usd = snapshot.sol_usd or self.settings.sol_price_usd
        notional_cap, _ = self.settings.ticket_usd()
        cost_usd = min(size_sol * sol_usd, notional_cap)
        size_sol = cost_usd / sol_usd
        fee = cost_usd * self.settings.paper_fee_pct
        if cost_usd + fee > self.settings.max_buy_usd + 1e-6:
            return None
        # Fill slightly worse than the mark.
        fill_price = cost_usd / tokens
        order_id = client_order_id(snapshot.mint, "buy", "entry", now)
        if self.storage.order_exists(order_id):
            return None
        order = {
            "client_order_id": order_id,
            "mint": snapshot.mint,
            "side": "buy",
            "mode": "paper",
            "status": "filled",
            "qty_tokens": tokens,
            "price_usd": fill_price,
            "fee_usd": fee,
            "tx": f"paper:{order_id}",
            "ts": now,
            "raw": {"quote": {k: quote.get(k) for k in ("input_amount", "output_amount", "min_output_amount", "slippage")}},
        }
        self.storage.save_order(order)
        self.storage.save_fill(
            {
                "client_order_id": order_id,
                "mint": snapshot.mint,
                "side": "buy",
                "qty_tokens": tokens,
                "price_usd": fill_price,
                "fee_usd": fee,
                "tx": order["tx"],
                "ts": now,
            }
        )
        position = Position(
            mint=snapshot.mint,
            venue=snapshot.venue,
            entry_price_usd=fill_price,
            size_tokens=tokens,
            remaining_tokens=tokens,
            cost_sol=size_sol,
            cost_usd=cost_usd,
            fees_usd=fee,
            entry_tx=order["tx"],
            entry_ts=now,
            snapshot=snapshot,
            thesis=snapshot.expected_path.value,
            quote_asset=snapshot.quote_asset,
            client_order_ids=[order_id],
        )
        self.storage.save_position(position.model_dump(mode="json"))
        self.storage.event(
            "entry",
            mint=snapshot.mint,
            venue=snapshot.venue.value,
            price=fill_price,
            size_sol=size_sol,
            thesis=position.thesis,
        )
        return position

    async def sell(self, position: Position, fraction: float, reason: str) -> Position:
        fraction = max(0.0, min(1.0, fraction))
        qty = position.remaining_tokens * fraction
        price = position.entry_price_usd * position.last_multiple
        proceeds = qty * price
        fee = proceeds * self.settings.paper_fee_pct
        now = time.time()
        intent = "initial" if reason == "2x initial scale-out" else "exit"
        order_id = client_order_id(position.mint, "sell", intent, position.entry_ts)
        if reason != "2x initial scale-out":
            order_id = client_order_id(position.mint, "sell", f"exit-{reason}", now)
        if not self.storage.order_exists(order_id):
            self.storage.save_order(
                {
                    "client_order_id": order_id,
                    "mint": position.mint,
                    "side": "sell",
                    "mode": "paper",
                    "status": "filled",
                    "qty_tokens": qty,
                    "price_usd": price,
                    "fee_usd": fee,
                    "tx": f"paper:{order_id}",
                    "ts": now,
                    "raw": {},
                }
            )
            self.storage.save_fill(
                {
                    "client_order_id": order_id,
                    "mint": position.mint,
                    "side": "sell",
                    "qty_tokens": qty,
                    "price_usd": price,
                    "fee_usd": fee,
                    "tx": f"paper:{order_id}",
                    "ts": now,
                }
            )
            position.client_order_ids.append(order_id)
        if reason == "2x initial scale-out":
            mark_initial_sold(position, qty, proceeds, fee)
        else:
            mark_flat(position, qty, proceeds, fee, reason)
        self.storage.save_position(position.model_dump(mode="json"))
        self.storage.event("exit", mint=position.mint, reason=reason, fraction=fraction, price=price)
        return position


class LiveBroker:
    """Quote, impact check, swap, poll. Refuses to run unless LIVE_TRADING is true."""

    def __init__(self, settings: Settings, storage: Storage, gmgn: GmgnClient) -> None:
        self.settings = settings
        self.storage = storage
        self.gmgn = gmgn

    def _guard(self) -> None:
        if not self.settings.live_trading:
            raise RuntimeError("live trading is disabled")
        if not self.settings.gmgn_api_key or not self.settings.gmgn_private_key or not self.settings.wallet_address:
            raise RuntimeError("live trading requires GMGN_API_KEY, GMGN_PRIVATE_KEY, and WALLET_ADDRESS")

    async def buy(self, snapshot: TokenSnapshot, size_sol: float) -> Position | None:
        self._guard()
        liq = snapshot.liquidity_usd or 0
        impact = (size_sol * snapshot.sol_usd / liq) if liq else 1
        if impact > self.settings.max_price_impact_pct:
            self.storage.event("live_skip", mint=snapshot.mint, reason="price_impact", impact=impact)
            return None
        amount = _lamports(size_sol)
        quote_raw = await self.gmgn.quote(
            WSOL_MINT, snapshot.mint, amount, self.settings.wallet_address, self.settings.paper_slippage_pct
        )
        quote = normalize_quote(quote_raw if isinstance(quote_raw, dict) else {})
        now = time.time()
        order_id = client_order_id(snapshot.mint, "buy", "entry", int(now))
        if self.storage.order_exists(order_id):
            return None
        condition = [
            {
                "order_type": "profit_stop",
                "side": "sell",
                "price_scale": "100",
                "sell_ratio": "50" if self.settings.initial_scale_mode == "half_at_2x" else "50",
            }
        ]
        result = await self.gmgn.swap(
            {
                "from_address": self.settings.wallet_address,
                "input_token": WSOL_MINT,
                "output_token": snapshot.mint,
                "input_amount": amount,
                "slippage": self.settings.paper_slippage_pct,
                "is_anti_mev": True,
                "condition_orders": condition,
                "sell_ratio_type": "buy_amount",
            }
        )
        result = result if isinstance(result, dict) else {}
        status = await self._poll(str(result.get("order_id") or ""))
        report = status.get("report") if isinstance(status.get("report"), dict) else {}
        decimals = int(report.get("output_token_decimals") or 6)
        out_raw = report.get("output_amount") or quote.get("output_amount") or "0"
        tokens = int(out_raw) / (10**decimals) if str(out_raw).isdigit() else 0
        price = float(report.get("price_usd") or snapshot.price_usd or 0)
        fee = float(report.get("gas_usd") or 0)
        position = Position(
            mint=snapshot.mint,
            venue=snapshot.venue,
            entry_price_usd=price or snapshot.price_usd or 0,
            size_tokens=tokens,
            remaining_tokens=tokens,
            cost_sol=size_sol,
            cost_usd=size_sol * snapshot.sol_usd,
            fees_usd=fee,
            entry_tx=str(result.get("hash") or ""),
            entry_ts=now,
            snapshot=snapshot,
            thesis=snapshot.expected_path.value,
            quote_asset=snapshot.quote_asset,
            strategy_order_id=str(result.get("strategy_order_id") or ""),
            client_order_ids=[order_id],
        )
        self.storage.save_order(
            {
                "client_order_id": order_id,
                "mint": snapshot.mint,
                "side": "buy",
                "mode": "live",
                "status": str(status.get("status") or result.get("status") or "pending"),
                "qty_tokens": tokens,
                "price_usd": position.entry_price_usd,
                "fee_usd": fee,
                "tx": position.entry_tx,
                "ts": now,
                "raw": {"order_id": result.get("order_id"), "hash": result.get("hash")},
            }
        )
        self.storage.save_fill(
            {
                "client_order_id": order_id,
                "mint": snapshot.mint,
                "side": "buy",
                "qty_tokens": tokens,
                "price_usd": position.entry_price_usd,
                "fee_usd": fee,
                "tx": position.entry_tx,
                "ts": now,
            }
        )
        self.storage.save_position(position.model_dump(mode="json"))
        return position

    async def sell(self, position: Position, fraction: float, reason: str) -> Position:
        self._guard()
        fraction = max(0.0, min(1.0, fraction))
        qty = position.remaining_tokens * fraction
        raw_amount = _tokens_raw(qty)
        result = await self.gmgn.swap(
            {
                "from_address": self.settings.wallet_address,
                "input_token": position.mint,
                "output_token": WSOL_MINT,
                "input_amount": raw_amount,
                "slippage": self.settings.paper_slippage_pct,
                "is_anti_mev": True,
            }
        )
        result = result if isinstance(result, dict) else {}
        status = await self._poll(str(result.get("order_id") or ""))
        report = status.get("report") if isinstance(status.get("report"), dict) else {}
        price = float(report.get("price_usd") or (position.entry_price_usd * position.last_multiple))
        proceeds = qty * price
        fee = float(report.get("gas_usd") or 0)
        now = time.time()
        order_id = client_order_id(position.mint, "sell", reason, now)
        self.storage.save_order(
            {
                "client_order_id": order_id,
                "mint": position.mint,
                "side": "sell",
                "mode": "live",
                "status": str(status.get("status") or "pending"),
                "qty_tokens": qty,
                "price_usd": price,
                "fee_usd": fee,
                "tx": str(result.get("hash") or ""),
                "ts": now,
                "raw": {"order_id": result.get("order_id")},
            }
        )
        self.storage.save_fill(
            {
                "client_order_id": order_id,
                "mint": position.mint,
                "side": "sell",
                "qty_tokens": qty,
                "price_usd": price,
                "fee_usd": fee,
                "tx": str(result.get("hash") or ""),
                "ts": now,
            }
        )
        if reason == "2x initial scale-out":
            mark_initial_sold(position, qty, proceeds, fee)
        else:
            mark_flat(position, qty, proceeds, fee, reason)
        self.storage.save_position(position.model_dump(mode="json"))
        return position

    async def _poll(self, order_id: str) -> dict[str, Any]:
        if not order_id:
            return {}
        last: dict[str, Any] = {}
        for _ in range(3):
            raw = await self.gmgn.query_order(order_id)
            last = raw if isinstance(raw, dict) else {}
            if last.get("status") in {"confirmed", "successful", "failed", "expired"}:
                return last
        return last


class RouterFallback:
    """Optional older gmgn.ai router. Disabled unless explicitly constructed and used.

    The OpenAPI swap is the execution path. This adapter is not called in paper mode.
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self.enabled = False

    async def swap(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("router fallback is disabled; use GMGN OpenAPI swap")
