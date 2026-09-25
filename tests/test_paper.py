import asyncio

from self_improving_agent.config import load_settings
from self_improving_agent.execution import PaperBroker
from self_improving_agent.models import TokenSnapshot, Venue
from self_improving_agent.storage import Storage


class _Gmgn:
    def __init__(self):
        self.swap_calls = 0

    async def quote(self, *args, **kwargs):
        return {
            "input_token": "So11111111111111111111111111111111111111112",
            "output_token": "PaperMint1111111111111111111111111111111111",
            "input_amount": "100000000",
            "output_amount": "10000000000",
            "min_output_amount": "9900000000",
            "slippage": 0.01,
        }

    async def swap(self, *args, **kwargs):
        self.swap_calls += 1
        raise AssertionError("paper path called swap")


def test_paper_buy_never_calls_swap(tmp_path):
    settings = load_settings(data_dir=tmp_path, live_trading=False, wallet_address="Wallet111", gmgn_api_key="k")
    storage = Storage(tmp_path)
    gmgn = _Gmgn()
    broker = PaperBroker(settings, storage, gmgn)  # type: ignore[arg-type]
    snap = TokenSnapshot(
        mint="PaperMint1111111111111111111111111111111111",
        venue=Venue.gmgn_other,
        price_usd=0.001,
        liquidity_usd=20000,
        sol_usd=150,
        renounced_mint=True,
        renounced_freeze=True,
    )
    position = asyncio.run(broker.buy(snap, 0.1))
    assert position is not None
    assert gmgn.swap_calls == 0
    assert storage.order_exists(position.client_order_ids[0])


def test_live_flag_only_explicit_true(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_TRADING", "false")
    assert load_settings(data_dir=tmp_path).live_trading is False
    monkeypatch.setenv("LIVE_TRADING", "1")
    assert load_settings(data_dir=tmp_path).live_trading is False
    monkeypatch.setenv("LIVE_TRADING", "true")
    assert load_settings(data_dir=tmp_path).live_trading is True
