from self_improving_agent.config import load_settings
from self_improving_agent.models import Position, PositionState, TokenSnapshot, Venue
from self_improving_agent.positions import manage_position, mark_initial_sold


def _pos(tmp_path) -> Position:
    settings = load_settings(data_dir=tmp_path)
    snap = TokenSnapshot(
        mint="PosMint111111111111111111111111111111111111",
        venue=Venue.pumpfun,
        price_usd=1,
        liquidity_usd=20000,
        renounced_mint=True,
        renounced_freeze=True,
        sol_usd=150,
        expected_path="2x_then_trail",
    )
    return Position(
        mint=snap.mint,
        venue=Venue.pumpfun,
        state=PositionState.ENTERED,
        entry_price_usd=1,
        size_tokens=100,
        remaining_tokens=100,
        cost_sol=settings.clamped_trade_size(),
        cost_usd=100,
        fees_usd=1,
        entry_ts=1_000,
        snapshot=snap,
    )


def test_position_hits_2x_sells_initial_then_trails(tmp_path):
    settings = load_settings(data_dir=tmp_path, initial_scale_mode="half_at_2x", trail_arm_mult=2.4, trail_giveback_pct=0.22)
    pos = _pos(tmp_path)
    first = manage_position(pos, 2.0, 1_100, settings)
    assert first.action == "sell_initial"
    assert abs(first.sell_fraction - 0.5) < 1e-9
    mark_initial_sold(pos, 50, 100, 0.5)
    assert pos.state == PositionState.RUNNER
    assert pos.remaining_tokens == 50

    armed = manage_position(pos, 3.0, 1_200, settings)
    assert armed.action == "hold_runner"
    assert pos.runner_peak_multiple >= 3

    # 22% off the 3.0 peak is 2.34.
    exit_step = manage_position(pos, 2.30, 1_260, settings)
    assert exit_step.action == "flatten"
    assert pos.flat_reason == "trail"
