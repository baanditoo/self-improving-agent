import json
from pathlib import Path

from self_improving_agent.gmgn_client import normalize_quote, normalize_security, normalize_token_info
from self_improving_agent.pumpfun_client import parse_coin
from self_improving_agent.stonkfun_client import _unwrap, parse_token

FIX = Path(__file__).parent / "fixtures"


def test_pump_and_stonk_parse_fixtures_offline():
    pump = parse_coin(json.loads((FIX / "pump_coin.json").read_text()))
    assert pump["mint"].startswith("PumpGood")
    assert pump["complete"] is False
    assert pump["stage"] == "curve"
    assert pump["curve_progress"] is not None
    assert 0.2 < pump["curve_progress"] < 0.8
    assert pump["virtual_sol_reserves"] == 62000000000
    assert pump["real_sol_reserves"] == 32000000000
    assert pump["reply_count"] == 3

    raw = json.loads((FIX / "stonk_token.json").read_text())
    stonk = parse_token(_unwrap(raw))
    assert stonk["quote_symbol"] == "NVDAx"
    assert stonk["launchpad"] == "launchlab"
    assert stonk["stage"] == "curve"
    assert stonk["transfer_fee_bps"] == 0
    assert stonk["flywheel_active"] is False
    assert stonk["price_usd"] == 0.04


def test_gmgn_normalizers_use_documented_fields():
    info = normalize_token_info(json.loads((FIX / "gmgn_token_info.json").read_text()))
    sec = normalize_security(json.loads((FIX / "gmgn_security.json").read_text()))
    quote = normalize_quote(json.loads((FIX / "gmgn_quote.json").read_text()))
    assert info["price_usd"] == 0.00003
    assert info["liquidity_usd"] == 30000
    assert info["top10_pct"] == 0.22
    assert info["quote_symbol"] == "SOL"
    assert sec["renounced_mint"] is True
    assert sec["renounced_freeze"] is True
    assert sec["honeypot"] is False
    assert quote["output_amount"] == "5000000000000"
    assert quote["min_output_amount"] == "4900000000000"
