"""Unify Pump.fun, StonkFun, and GMGN into mint+venue candidates."""

from __future__ import annotations

import time
from typing import Any

from .config import Settings
from .gmgn_client import GmgnClient, extract_rank, normalize_security, normalize_token_info
from .models import Stage, TokenSnapshot, Venue
from .pumpfun_client import PumpFunClient
from .scorer import age_from_ts
from .stonkfun_client import BitqueryEnrichment, StonkFunClient


def _stage(value: str) -> Stage:
    try:
        return Stage(value)
    except ValueError:
        return Stage.unknown


def snapshot_from_pump(coin: dict[str, Any], sol_usd: float, now: float) -> TokenSnapshot:
    usd_mcap = coin.get("usd_market_cap") or 0
    supply = coin.get("total_supply") or 0
    # Pump total_supply in the public coin object is raw base units (6 decimals).
    human_supply = supply / 1_000_000 if supply > 1_000_000_000 else supply
    price = (usd_mcap / human_supply) if human_supply else None
    progress = coin.get("curve_progress")
    # Curve liquidity is the real SOL in the curve when the API sends it; else a fraction of virtual.
    real_sol = coin.get("real_sol_reserves")
    if real_sol and real_sol > 1_000:
        real_sol = real_sol / 1_000_000_000
    virtual = coin.get("virtual_sol_reserves")
    if virtual and virtual > 1_000:
        virtual = virtual / 1_000_000_000
    liq = None
    if real_sol:
        liq = real_sol * sol_usd * 2
    elif virtual:
        liq = max(virtual - 30, 0) * sol_usd * 2
    return TokenSnapshot(
        mint=coin["mint"],
        venue=Venue.pumpfun,
        stage=_stage(coin.get("stage") or "curve"),
        quote_asset="SOL",
        name=coin.get("name") or "",
        symbol=coin.get("symbol") or "",
        age_sec=age_from_ts(coin.get("created_ts"), now),
        liquidity_usd=liq,
        curve_progress=progress,
        volume_usd=None,
        holder_count=None,
        price_usd=price,
        pump_complete=bool(coin.get("complete")),
        launchpad="pump",
        sol_usd=sol_usd,
        extension_multiple=1.0 if not coin.get("complete") else None,
        raw={"pumpfun": {k: v for k, v in coin.items() if k != "raw"}},
    )


def snapshot_from_stonk(token: dict[str, Any], sol_usd: float, now: float) -> TokenSnapshot:
    created = token.get("created_at")
    created_ts = None
    if isinstance(created, (int, float)):
        created_ts = float(created)
    fee = token.get("transfer_fee_bps") or 0
    return TokenSnapshot(
        mint=token["mint"],
        venue=Venue.stonkfun,
        stage=_stage(token.get("stage") or "unknown"),
        quote_asset=token.get("quote_symbol") or "SOL",
        quote_mint=token.get("quote_mint") or "",
        name=token.get("name") or "",
        symbol=token.get("symbol") or "",
        age_sec=age_from_ts(created_ts, now) if created_ts else None,
        liquidity_usd=token.get("liquidity_usd"),
        volume_usd=token.get("volume_usd"),
        price_usd=token.get("price_usd"),
        launchpad=token.get("launchpad") or "",
        stonk_reward_tax=bool(fee) or str(token.get("mode") or "") == "reward",
        transfer_fee_bps=fee,
        flywheel_active=token.get("flywheel_active") if isinstance(token.get("flywheel_active"), bool) else None,
        sol_usd=sol_usd,
        raw={"stonkfun": {k: v for k, v in token.items() if k != "raw"}},
    )


def snapshot_from_gmgn(info: dict[str, Any], security: dict[str, Any] | None, sol_usd: float, now: float) -> TokenSnapshot:
    created = info.get("creation_timestamp")
    holders = info.get("holder_count")
    sniper_wallets = info.get("sniper_wallets") or 0
    sniper_pct = None
    if holders:
        try:
            sniper_pct = float(sniper_wallets) / float(holders)
        except (TypeError, ValueError):
            sniper_pct = None
    smart = info.get("smart_wallets") or 0
    price = info.get("price_usd")
    smart_usd = None
    if price and smart:
        smart_usd = float(smart) * float(price) * 1000
    sec = security or {}
    launchpad = (info.get("launchpad") or "").lower()
    venue = Venue.gmgn_other
    stage = Stage.graduated if info.get("launchpad_status") == 2 else Stage.unknown
    progress = info.get("launchpad_progress")
    return TokenSnapshot(
        mint=info.get("address") or "",
        venue=venue,
        stage=stage,
        quote_asset=info.get("quote_symbol") or "SOL",
        quote_mint=info.get("quote_address") or "",
        name=info.get("name") or "",
        symbol=info.get("symbol") or "",
        age_sec=age_from_ts(created, now),
        liquidity_usd=info.get("liquidity_usd"),
        curve_progress=progress,
        volume_usd=info.get("volume_usd"),
        unique_wallets=holders,
        holder_count=holders,
        top10_pct=sec.get("top10_pct") if sec.get("top10_pct") is not None else info.get("top10_pct"),
        sniper_pct=sniper_pct,
        bundler_pct=sec.get("bundler_pct") if sec.get("bundler_pct") is not None else info.get("bundler_pct"),
        smart_money_buy_usd=smart_usd,
        price_usd=price,
        price_vs_ath=info.get("price_vs_ath"),
        extension_multiple=info.get("extension_multiple"),
        buy_sell_ratio=info.get("buy_sell_ratio"),
        honeypot=bool(sec.get("honeypot")),
        renounced_mint=sec.get("renounced_mint"),
        renounced_freeze=sec.get("renounced_freeze"),
        buy_tax=sec.get("buy_tax"),
        sell_tax=sec.get("sell_tax"),
        rug_ratio=sec.get("rug_ratio"),
        dev_hold_pct=sec.get("dev_hold_pct") if sec.get("dev_hold_pct") is not None else info.get("dev_hold_pct"),
        launchpad=launchpad,
        sol_usd=sol_usd,
        higher_highs=bool(info.get("extension_multiple") and 1 < info["extension_multiple"] < 2.5),
        volume_holding=bool((info.get("volume_usd") or 0) > 0),
        raw={"gmgn": {"info": info, "security": sec}},
    )


def merge_snapshots(primary: TokenSnapshot, extra: TokenSnapshot) -> TokenSnapshot:
    """Pump + GMGN collapse to one candidate. StonkFun stays its own venue."""
    raw = dict(primary.raw)
    raw.update(extra.raw)
    data = primary.model_dump()
    for field in (
        "liquidity_usd",
        "volume_usd",
        "holder_count",
        "unique_wallets",
        "top10_pct",
        "sniper_pct",
        "bundler_pct",
        "smart_money_buy_usd",
        "price_usd",
        "price_vs_ath",
        "extension_multiple",
        "buy_sell_ratio",
        "buy_tax",
        "sell_tax",
        "rug_ratio",
        "dev_hold_pct",
        "renounced_mint",
        "renounced_freeze",
    ):
        if data.get(field) in (None, 0, 0.0) and getattr(extra, field) not in (None, 0, 0.0):
            data[field] = getattr(extra, field)
    if extra.honeypot:
        data["honeypot"] = True
    data["raw"] = raw
    if primary.venue == Venue.pumpfun:
        data["venue"] = Venue.pumpfun.value
    return TokenSnapshot.model_validate(data)


class Discovery:
    def __init__(
        self,
        settings: Settings,
        pump: PumpFunClient | None = None,
        stonk: StonkFunClient | None = None,
        gmgn: GmgnClient | None = None,
        bitquery: BitqueryEnrichment | None = None,
    ) -> None:
        self.settings = settings
        self.pump = pump
        self.stonk = stonk
        self.gmgn = gmgn
        self.bitquery = bitquery or BitqueryEnrichment("")

    async def collect(self, now: float | None = None) -> list[TokenSnapshot]:
        now = now if now is not None else time.time()
        sol_usd = self.settings.sol_price_usd
        if self.gmgn and self.settings.gmgn_api_key:
            try:
                sol_usd = await self.gmgn.sol_price_usd(sol_usd)
            except Exception:
                pass
        pump_snaps: dict[str, TokenSnapshot] = {}
        stonk_snaps: dict[str, TokenSnapshot] = {}
        gmgn_snaps: dict[str, TokenSnapshot] = {}
        errors: list[str] = []

        if self.pump:
            try:
                coins = []
                coins.extend(await self.pump.latest())
                coins.extend(await self.pump.featured("1h"))
                koth = await self.pump.king_of_the_hill()
                if koth:
                    coins.append(koth)
                for coin in coins:
                    if not coin.get("mint"):
                        continue
                    pump_snaps[coin["mint"]] = snapshot_from_pump(coin, sol_usd, now)
            except Exception as exc:
                errors.append(f"pumpfun:{exc.__class__.__name__}")

        if self.stonk:
            try:
                rows = []
                rows.extend(await self.stonk.list_tokens(sort="newest"))
                rows.extend(await self.stonk.list_tokens(sort="volume"))
                for row in rows:
                    if not row.get("mint"):
                        continue
                    snap = snapshot_from_stonk(row, sol_usd, now)
                    if self.bitquery.enabled:
                        try:
                            trades = await self.bitquery.trades(row["mint"], limit=5)
                            snap.raw["bitquery_trades"] = len(trades)
                        except Exception:
                            snap.raw["bitquery_trades"] = 0
                    stonk_snaps[row["mint"]] = snap
            except Exception as exc:
                errors.append(f"stonkfun:{exc.__class__.__name__}")

        if self.gmgn and self.settings.gmgn_api_key:
            try:
                ranked: list[dict[str, Any]] = []
                for interval in ("1m", "5m", "1h"):
                    payload = await self.gmgn.get_trending(interval)
                    ranked.extend(extract_rank(payload))
                signals = await self.gmgn.get_signals([{"signal_type": [12], "mc_min": 5000, "mc_max": 5_000_000}])
                if isinstance(signals, list):
                    ranked.extend(row for row in signals if isinstance(row, dict))
                elif isinstance(signals, dict):
                    for value in signals.values():
                        if isinstance(value, list):
                            ranked.extend(row for row in value if isinstance(row, dict))
                smart = await self.gmgn.get_smart_money(limit=10)
                if isinstance(smart, list):
                    ranked.extend(row for row in smart if isinstance(row, dict) and row.get("address"))
                seen: set[str] = set()
                for row in ranked:
                    address = row.get("address") or row.get("token_address") or row.get("mint")
                    if not address or address in seen:
                        continue
                    seen.add(address)
                    if len(seen) > 12:
                        break
                    info_raw = await self.gmgn.get_token_info(address)
                    sec_raw = await self.gmgn.get_token_security(address)
                    info = normalize_token_info(info_raw if isinstance(info_raw, dict) else {"address": address})
                    if not info.get("address"):
                        info["address"] = address
                    sec = normalize_security(sec_raw if isinstance(sec_raw, dict) else {})
                    gmgn_snaps[address] = snapshot_from_gmgn(info, sec, sol_usd, now)
            except Exception as exc:
                errors.append(f"gmgn:{exc.__class__.__name__}")

        merged: dict[tuple[str, str], TokenSnapshot] = {}
        for mint, snap in pump_snaps.items():
            if mint in gmgn_snaps:
                snap = merge_snapshots(snap, gmgn_snaps[mint])
            merged[(mint, Venue.pumpfun.value)] = snap
        for mint, snap in gmgn_snaps.items():
            if mint in pump_snaps or mint in stonk_snaps:
                continue
            merged[(mint, Venue.gmgn_other.value)] = snap
        for mint, snap in stonk_snaps.items():
            if mint in gmgn_snaps:
                snap.raw["gmgn"] = gmgn_snaps[mint].raw.get("gmgn", {})
                if snap.liquidity_usd is None:
                    snap.liquidity_usd = gmgn_snaps[mint].liquidity_usd
                if snap.price_usd is None:
                    snap.price_usd = gmgn_snaps[mint].price_usd
            merged[(mint, Venue.stonkfun.value)] = snap
        if errors:
            for snap in merged.values():
                snap.raw.setdefault("discovery_errors", errors)
        return list(merged.values())
