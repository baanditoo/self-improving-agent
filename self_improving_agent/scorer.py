"""Checklist scorer, 0–100. Same snapshot and weights always produce the same score."""

from __future__ import annotations

import re
import time
from typing import Any

from .config import Settings
from .models import ExpectedPath, ScoreDecision, TokenSnapshot, Venue

SCAM_RE = re.compile(r"(rug|scam|honeypot|fakeairdrop|airdrop.?claim|steal|drain)", re.I)

DEFAULT_WEIGHTS: dict[str, float] = {
    "fresh_tradeable": 14,
    "liquidity": 12,
    "unique_buyers": 10,
    "buy_sell_ratio": 10,
    "smart_money": 12,
    "concentration": -12,
    "sniper": -6,
    "bundler": -6,
    "not_extended": 10,
    "structure": 8,
    "security": 10,
}

VENUES = (Venue.pumpfun.value, Venue.stonkfun.value, Venue.gmgn_other.value)


def blank_weights() -> dict[str, Any]:
    venues = {
        venue: {"sample_size": 0, "weights": dict(DEFAULT_WEIGHTS)}
        for venue in VENUES
    }
    return {
        "version": 1,
        "updated_at": None,
        "sample_size": 0,
        "params": {},
        "venues": venues,
    }


def venue_weights(book: dict[str, Any], venue: str) -> dict[str, float]:
    slice_ = (book.get("venues") or {}).get(venue) or {}
    weights = dict(DEFAULT_WEIGHTS)
    weights.update(slice_.get("weights") or {})
    return weights


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _round_trip_multiple(buy_tax: float, sell_tax: float, fee_bps: float) -> float:
    fee = max(fee_bps, 0) / 10_000
    keep = (1 - buy_tax) * (1 - sell_tax) * (1 - fee) * (1 - fee)
    if keep <= 0.05:
        return 999
    return 2.0 / keep


def score_snapshot(snapshot: TokenSnapshot, settings: Settings, weights: dict[str, float] | None = None) -> ScoreDecision:
    weights = dict(DEFAULT_WEIGHTS if weights is None else weights)
    flags: list[str] = []
    reasons: list[str] = []
    components: dict[str, float] = {}

    name = f"{snapshot.name} {snapshot.symbol}"
    if SCAM_RE.search(name):
        flags.append("scam_name")
    if snapshot.honeypot:
        flags.append("honeypot")
    if settings.require_renounced_freeze and snapshot.renounced_freeze is False:
        flags.append("freeze_authority")
    if settings.require_renounced_mint and snapshot.renounced_mint is False:
        flags.append("mint_not_renounced")
    liq = snapshot.liquidity_usd or 0
    size_usd = settings.max_buy_usd
    if liq < settings.min_liq_usd or (liq > 0 and size_usd > liq * 0.05):
        flags.append("thin_liquidity")
    if liq <= 0:
        flags.append("thin_liquidity")
    impact = snapshot.price_impact_pct
    if impact is None and liq > 0:
        impact = size_usd / liq
    if impact is not None and impact > settings.max_price_impact_pct:
        flags.append("price_impact")
    top10 = snapshot.top10_pct if snapshot.top10_pct is not None else 1.0
    if top10 > settings.max_top10_pct:
        flags.append("holder_concentration")
    dev = snapshot.dev_hold_pct or 0
    if dev > settings.max_dev_hold_pct and not settings.allow_dev_hold:
        flags.append("dev_supply")
    extension = snapshot.extension_multiple or 1
    if extension >= settings.max_extension_multiple:
        flags.append("already_extended")
    taxes = _round_trip_multiple(snapshot.buy_tax or 0, snapshot.sell_tax or 0, snapshot.transfer_fee_bps or 0)
    if snapshot.venue == Venue.stonkfun and snapshot.stonk_reward_tax and taxes > 2.6:
        flags.append("reward_tax_unrealistic")
    if snapshot.venue == Venue.stonkfun and (snapshot.transfer_fee_bps or 0) > 0 and taxes > 2.6:
        flags.append("reward_tax_unrealistic")

    hard = {
        "honeypot",
        "freeze_authority",
        "mint_not_renounced",
        "thin_liquidity",
        "price_impact",
        "holder_concentration",
        "dev_supply",
        "already_extended",
        "scam_name",
        "reward_tax_unrealistic",
    }
    if any(flag in hard for flag in flags):
        reasons.append("rejected:" + ",".join(flags))
        return ScoreDecision(
            score=0,
            components={flag: 0 for flag in flags},
            reasons=reasons,
            red_flags=flags,
            expected_path=ExpectedPath.skip,
            rejected=True,
        )

    age = snapshot.age_sec if snapshot.age_sec is not None else 3600
    fresh = _clamp01(1 - max(age - 120, 0) / 7200)
    if age < 30:
        fresh *= 0.4
        reasons.append("very_new")
    components["fresh_tradeable"] = round(fresh * weights["fresh_tradeable"], 4)

    liq_q = _clamp01(liq / (settings.min_liq_usd * 2))
    components["liquidity"] = round(liq_q * weights["liquidity"], 4)

    holders = snapshot.holder_count or snapshot.unique_wallets or 0
    buyer_q = _clamp01(holders / 120)
    components["unique_buyers"] = round(buyer_q * weights["unique_buyers"], 4)

    ratio = snapshot.buy_sell_ratio if snapshot.buy_sell_ratio is not None else 1
    ratio_q = _clamp01((ratio - 0.8) / 1.5)
    components["buy_sell_ratio"] = round(ratio_q * weights["buy_sell_ratio"], 4)

    smart = snapshot.smart_money_buy_usd or 0
    smart_q = _clamp01(smart / 5000)
    components["smart_money"] = round(smart_q * weights["smart_money"], 4)

    # Negative weights: higher concentration / sniper / bundler reduces the score.
    components["concentration"] = round(top10 * weights["concentration"], 4)
    sniper = snapshot.sniper_pct or 0
    bundler = snapshot.bundler_pct or 0
    components["sniper"] = round(sniper * weights["sniper"], 4)
    components["bundler"] = round(bundler * weights["bundler"], 4)

    ext_q = _clamp01(1 - max(extension - 1, 0) / 4)
    components["not_extended"] = round(ext_q * weights["not_extended"], 4)

    structure = 0.35
    if snapshot.higher_highs and snapshot.volume_holding and not snapshot.distribution:
        structure = 1.0
        reasons.append("impulse_holding")
    elif ratio >= 1.3 and not snapshot.distribution:
        structure = 0.7
        reasons.append("shallow_bid")
    if snapshot.distribution:
        structure = 0.15
        reasons.append("distribution")
    components["structure"] = round(structure * weights["structure"], 4)

    security = 1.0
    if snapshot.renounced_mint is False or snapshot.renounced_freeze is False:
        security -= 0.5
    if (snapshot.rug_ratio or 0) > 0.2:
        security -= 0.4
    if (snapshot.buy_tax or 0) + (snapshot.sell_tax or 0) > 0.02:
        security -= 0.3
    components["security"] = round(_clamp01(security) * weights["security"], 4)

    curve_penalty = 0.0
    if snapshot.venue == Venue.pumpfun and snapshot.pump_complete is False:
        progress = snapshot.curve_progress or 0
        if progress >= 0.99:
            curve_penalty = 22
            flags.append("curve_99_no_graduation_thesis")
            reasons.append("pump_curve_99")
        elif 0.25 <= progress <= 0.85:
            reasons.append("curve_tradeable")
    if snapshot.venue == Venue.pumpfun and snapshot.pump_complete:
        reasons.append("pump_graduated")

    score = sum(components.values()) - curve_penalty
    score = max(0.0, min(100.0, score))
    score = round(score, 2)

    if curve_penalty and score < settings.entry_threshold:
        flags.append("curve_99")
        reasons.append("rejected:curve_99")
        return ScoreDecision(
            score=score,
            components=components,
            reasons=reasons,
            red_flags=flags,
            expected_path=ExpectedPath.skip,
            rejected=True,
        )

    if score >= settings.entry_threshold:
        if snapshot.higher_highs and snapshot.volume_holding and not snapshot.distribution:
            path = ExpectedPath.hold
            reasons.append("path_2x_then_hold")
        else:
            path = ExpectedPath.trail
            reasons.append("path_2x_then_trail")
        rejected = False
    else:
        path = ExpectedPath.skip
        rejected = True
        reasons.append("below_threshold")

    return ScoreDecision(
        score=score,
        components=components,
        reasons=reasons,
        red_flags=flags,
        expected_path=path,
        rejected=rejected,
    )


def apply_decision(snapshot: TokenSnapshot, decision: ScoreDecision) -> TokenSnapshot:
    snapshot.score = decision.score
    snapshot.score_components = decision.components
    snapshot.reasons = decision.reasons
    snapshot.red_flags = decision.red_flags
    snapshot.expected_path = decision.expected_path
    return snapshot


def age_from_ts(created: float | None, now: float | None = None) -> float | None:
    if created is None:
        return None
    now = now if now is not None else time.time()
    return max(0.0, now - created)
