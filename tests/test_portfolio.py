import asyncio

from self_improving_agent.agent import TradingAgent
from self_improving_agent.config import load_settings
from self_improving_agent.replay import build_tape, run_replay
from self_improving_agent.storage import Storage


def test_replay_hits_seventy_percent_2x_initials(tmp_path):
    settings = load_settings(
        data_dir=tmp_path,
        live_trading=False,
        paper_equity_usd=500,
        max_buy_usd=10,
        max_positions=50,
        entry_threshold=64,
        min_liq_usd=8000,
        max_top10_pct=0.35,
    )
    agent = TradingAgent(settings, Storage(tmp_path))
    stats = asyncio.run(run_replay(agent, build_tape()))
    assert stats["resolved"] >= 30
    assert stats["hit_ratio"] >= 0.70
    for pos in agent.positions():
        assert pos.cost_usd <= 10 + 1e-6
        assert pos.cost_usd * (1 + settings.paper_fee_pct) <= settings.max_buy_usd + 1e-6
    assert agent.cash_usd() <= 500 + 1e-6
    assert agent.equity_usd() > 0
