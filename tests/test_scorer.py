from self_improving_agent.config import load_settings
from self_improving_agent.models import Stage, TokenSnapshot, Venue
from self_improving_agent.scorer import score_snapshot


def _settings(tmp_path):
    return load_settings(data_dir=tmp_path, live_trading=False, sol_price_usd=150)


def _base(**kwargs) -> TokenSnapshot:
    data = dict(
        mint="Mint1111111111111111111111111111111111111111",
        venue=Venue.pumpfun,
        stage=Stage.curve,
        quote_asset="SOL",
        name="Curve Cat",
        symbol="CCAT",
        age_sec=600,
        liquidity_usd=25000,
        curve_progress=0.45,
        volume_usd=12000,
        unique_wallets=90,
        holder_count=90,
        top10_pct=0.22,
        sniper_pct=0.04,
        bundler_pct=0.03,
        smart_money_buy_usd=4000,
        price_usd=0.00002,
        price_vs_ath=0.8,
        extension_multiple=1.3,
        buy_sell_ratio=1.8,
        honeypot=False,
        renounced_mint=True,
        renounced_freeze=True,
        buy_tax=0,
        sell_tax=0,
        rug_ratio=0.04,
        dev_hold_pct=0.02,
        pump_complete=False,
        higher_highs=True,
        volume_holding=True,
        distribution=False,
        sol_usd=150,
    )
    data.update(kwargs)
    return TokenSnapshot(**data)


def test_good_pump_curve_scores_for_2x_path(tmp_path):
    decision = score_snapshot(_base(), _settings(tmp_path))
    assert decision.score >= 72
    assert decision.rejected is False
    assert decision.expected_path.value == "2x_then_hold"


def test_stonk_stock_quote_setup(tmp_path):
    snap = _base(
        venue=Venue.stonkfun,
        stage=Stage.curve,
        quote_asset="NVDAx",
        name="Desk Chip",
        symbol="DSK",
        pump_complete=None,
        curve_progress=None,
        launchpad="launchlab",
        stonk_reward_tax=False,
        transfer_fee_bps=0,
    )
    decision = score_snapshot(snap, _settings(tmp_path))
    assert decision.score >= 72
    assert decision.expected_path.value in {"2x_then_hold", "2x_then_trail"}
    assert "reward_tax_unrealistic" not in decision.red_flags


def test_already_pumped_coin_rejected(tmp_path):
    decision = score_snapshot(_base(extension_multiple=8, price_vs_ath=0.99), _settings(tmp_path))
    assert decision.rejected is True
    assert decision.score == 0
    assert "already_extended" in decision.red_flags
    assert decision.expected_path.value == "skip"


def test_rug_shaped_coin_rejected(tmp_path):
    decision = score_snapshot(
        _base(
            name="Rug Pull",
            symbol="RUG",
            honeypot=True,
            renounced_freeze=False,
            renounced_mint=False,
            top10_pct=0.8,
            dev_hold_pct=0.4,
            liquidity_usd=500,
        ),
        _settings(tmp_path),
    )
    assert decision.rejected is True
    assert decision.score == 0
    assert "honeypot" in decision.red_flags
    assert "scam_name" in decision.red_flags
