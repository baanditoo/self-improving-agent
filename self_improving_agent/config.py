"""Environment configuration. Secrets stay in the process environment, never in logs."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

WSOL_MINT = "So11111111111111111111111111111111111111112"
CHAIN = "sol"


def _explicit_true(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    gmgn_api_key: str = ""
    gmgn_private_key: str = ""
    gmgn_host: str = "https://openapi.gmgn.ai"
    gmgn_router_url: str = "https://gmgn.ai/defi/router/v1/sol/tx/get_swap_route"
    wallet_address: str = ""
    live_trading: bool = False

    trade_size_sol: float = 0.10
    trade_size_min_sol: float = 0.05
    trade_size_max_sol: float = 0.15
    max_positions: int = 50
    entry_threshold: float = 64
    initial_scale_mode: str = "cost_basis"
    take_initial_mult: float = 2.0
    stop_loss_pct: float = 0.22
    trail_arm_mult: float = 2.4
    trail_giveback_pct: float = 0.22
    max_daily_dd_pct: float = 25
    min_liq_usd: float = 8000
    max_top10_pct: float = 0.40
    paper_equity_sol: float = 10
    paper_equity_usd: float = 500
    max_buy_usd: float = 10

    pumpfun_base_url: str = "https://frontend-api-v3.pump.fun"
    pumpfun_bearer: str = ""
    stonkfun_base_url: str = "https://www.stonkfun.xyz/api/public/v1"
    stonks_api_key: str = ""
    bitquery_api_key: str = ""
    bitquery_url: str = "https://streaming.bitquery.io/graphql"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    discord_webhook_url: str = ""
    log_level: str = "INFO"

    max_hold_no_move_min: int = 60
    max_price_impact_pct: float = 0.10
    flat_on_exit: bool = False
    loop_interval_sec: int = 20
    require_renounced_mint: bool = True
    require_renounced_freeze: bool = True
    allow_dev_hold: bool = False
    max_dev_hold_pct: float = 0.08
    paper_slippage_pct: float = 0.01
    paper_fee_pct: float = 0.005
    sol_price_usd: float = 150
    min_learn_samples: int = 30
    learn_window_hours: int = 96
    hard_dump_pct: float = 0.35
    hard_dump_window_sec: int = 180
    time_stop_min_mult: float = 1.4
    max_extension_multiple: float = 5.0
    data_dir: Path = Field(default_factory=lambda: Path("data"))

    gmgn_rate_per_sec: float = 8
    gmgn_burst: float = 16
    pump_rate_per_sec: float = 2
    pump_burst: float = 5
    stonk_rate_per_sec: float = 4
    stonk_burst: float = 10

    @field_validator("live_trading", "flat_on_exit", "require_renounced_mint", "require_renounced_freeze", "allow_dev_hold", mode="before")
    @classmethod
    def _bools(cls, value: object) -> bool:
        return _explicit_true(value)

    @field_validator("initial_scale_mode")
    @classmethod
    def _scale_mode(cls, value: str) -> str:
        mode = value.strip().lower()
        if mode not in {"cost_basis", "half_at_2x"}:
            raise ValueError("INITIAL_SCALE_MODE must be cost_basis or half_at_2x")
        return mode

    @field_validator("loop_interval_sec")
    @classmethod
    def _loop(cls, value: int) -> int:
        return min(30, max(15, int(value)))

    @field_validator("max_hold_no_move_min")
    @classmethod
    def _hold(cls, value: int) -> int:
        return min(90, max(45, int(value)))

    @field_validator("trade_size_sol")
    @classmethod
    def _size(cls, value: float) -> float:
        return float(value)

    def clamped_trade_size(self) -> float:
        return min(self.trade_size_max_sol, max(self.trade_size_min_sol, self.trade_size_sol))

    def ticket_usd(self) -> tuple[float, float]:
        """Cash outlay per coin is max_buy_usd, fee included. Returns notional, fee."""
        fee_rate = max(self.paper_fee_pct, 0.0)
        notional = self.max_buy_usd / (1 + fee_rate)
        return notional, self.max_buy_usd - notional

    def starting_cash_usd(self) -> float:
        return float(self.paper_equity_usd)

    def trading_mode(self) -> str:
        return "LIVE" if self.live_trading else "PAPER"


def load_settings(**overrides: object) -> Settings:
    settings = Settings(**overrides)  # type: ignore[arg-type]
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "reports").mkdir(parents=True, exist_ok=True)
    return settings
