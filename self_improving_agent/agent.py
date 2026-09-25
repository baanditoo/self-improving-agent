"""Scheduler: discovery, position management, hourly report, 4h learn."""

from __future__ import annotations

import asyncio
import signal
import time
from typing import Any

from .config import Settings, load_settings
from .discovery import Discovery
from .execution import LiveBroker, PaperBroker
from .gmgn_client import GmgnClient
from .learner import learn, load_weights
from .logutil import get_logger
from .memory import MissedTracker
from .models import Position, TokenSnapshot
from .positions import manage_position
from .pumpfun_client import PumpFunClient
from .reports import default_observations, notify, render_hourly, write_hourly
from .risk import approve_entry, kill_switch_active
from .scorer import apply_decision, score_snapshot, venue_weights
from .stonkfun_client import BitqueryEnrichment, StonkFunClient
from .storage import Storage
from .watchlist import remember_entry, remember_skip

logger = get_logger("agent")


class TradingAgent:
    def __init__(self, settings: Settings, storage: Storage | None = None, discovery: Discovery | None = None) -> None:
        self.settings = settings
        self.storage = storage or Storage(settings.data_dir)
        self.discovery = discovery
        self.gmgn: GmgnClient | None = None
        self.pump: PumpFunClient | None = None
        self.stonk: StonkFunClient | None = None
        self._stop = asyncio.Event()
        self.anomalies: list[str] = []
        self._owns_clients = discovery is None

    def _ensure_clients(self) -> Discovery:
        if self.discovery is not None:
            return self.discovery
        self.gmgn = GmgnClient(
            self.settings.gmgn_api_key,
            self.settings.gmgn_private_key,
            self.settings.gmgn_host,
        )
        self.pump = PumpFunClient(self.settings.pumpfun_base_url, self.settings.pumpfun_bearer)
        self.stonk = StonkFunClient(self.settings.stonkfun_base_url)
        bitquery = BitqueryEnrichment(
            self.settings.bitquery_api_key or self.settings.stonks_api_key,
            self.settings.bitquery_url,
        )
        self.discovery = Discovery(self.settings, self.pump, self.stonk, self.gmgn, bitquery)
        return self.discovery

    def positions(self) -> list[Position]:
        return [Position.model_validate(row) for row in self.storage.load_positions()]

    def equity_usd(self, positions: list[Position] | None = None) -> float:
        positions = self.positions() if positions is None else positions
        cash = float(self.storage.get_meta("cash_usd", str(self.settings.starting_cash_usd())))
        unreal = 0.0
        for pos in positions:
            if pos.state.value == "FLAT":
                continue
            unreal += pos.remaining_tokens * pos.entry_price_usd * pos.last_multiple
        return cash + unreal

    def _roll_day(self, now: float) -> float:
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        stored = self.storage.get_meta("equity_day")
        start = self.storage.get_meta("equity_start_usd")
        if stored != day or start is None:
            equity = self.equity_usd()
            self.storage.set_meta("equity_day", day)
            self.storage.set_meta("equity_start_usd", str(equity))
            if self.storage.get_meta("cash_usd") is None:
                self.storage.set_meta("cash_usd", str(self.settings.starting_cash_usd()))
            return equity
        return float(start)

    def broker(self) -> PaperBroker | LiveBroker:
        if self.settings.live_trading:
            if self.gmgn is None:
                self._ensure_clients()
            assert self.gmgn is not None
            return LiveBroker(self.settings, self.storage, self.gmgn)
        return PaperBroker(self.settings, self.storage, self.gmgn)

    async def tick(self, now: float | None = None, marks: dict[str, float] | None = None) -> dict[str, Any]:
        now = now if now is not None else time.time()
        discovery = self._ensure_clients()
        book = load_weights(self.settings.data_dir / "weights.json")
        params = book.get("params") or {}
        if int(book.get("sample_size") or 0) >= self.settings.min_learn_samples:
            if params.get("entry_threshold"):
                self.settings.entry_threshold = float(params["entry_threshold"])
            if params.get("trail_giveback_pct"):
                self.settings.trail_giveback_pct = float(params["trail_giveback_pct"])
            if params.get("min_liq_usd"):
                self.settings.min_liq_usd = float(params["min_liq_usd"])
            if params.get("max_top10_pct"):
                self.settings.max_top10_pct = float(params["max_top10_pct"])

        start_equity = self._roll_day(now)
        snapshots = await discovery.collect(now)
        positions = self.positions()
        broker = self.broker()
        entries = 0
        for snapshot in snapshots:
            weights = venue_weights(book, snapshot.venue.value)
            decision = score_snapshot(snapshot, self.settings, weights)
            apply_decision(snapshot, decision)
            if decision.rejected or decision.expected_path.value == "skip":
                remember_skip(self.storage, snapshot, now)
                continue
            cash = float(self.storage.get_meta("cash_usd", str(self.settings.starting_cash_usd())))
            ok, reason, size = approve_entry(
                snapshot, self.settings, positions, start_equity, self.equity_usd(positions), cash
            )
            if not ok:
                snapshot.reasons.append(reason)
                remember_skip(self.storage, snapshot, now)
                if reason == "kill_switch":
                    self.anomalies.append("kill_switch_halt_entries")
                continue
            bought = await broker.buy(snapshot, size)
            if bought:
                remember_entry(self.storage, snapshot, now)
                positions.append(bought)
                entries += 1
                cash = float(self.storage.get_meta("cash_usd", "0"))
                self.storage.set_meta("cash_usd", str(cash - bought.cost_usd - bought.fees_usd))

        managed = await self._manage(broker, positions, marks or {}, now)
        tracker = MissedTracker(self.storage, self.settings)
        price_map = {snap.mint: snap.price_usd for snap in snapshots if snap.price_usd}
        if marks:
            price_map.update(marks)
        missed = tracker.mark_watchlist(price_map, now)
        return {"entries": entries, "managed": managed, "candidates": len(snapshots), "missed": len(missed)}

    async def _manage(
        self,
        broker: PaperBroker | LiveBroker,
        positions: list[Position],
        marks: dict[str, float],
        now: float,
    ) -> int:
        tracker = MissedTracker(self.storage, self.settings)
        acted = 0
        for pos in positions:
            if pos.state.value == "FLAT":
                continue
            price = marks.get(pos.mint, pos.entry_price_usd * pos.last_multiple)
            result = manage_position(
                pos,
                price,
                now,
                self.settings,
                higher_highs=pos.snapshot.higher_highs,
                volume_holding=pos.snapshot.volume_holding,
                distribution=pos.snapshot.distribution,
            )
            if result.action == "sell_initial":
                before = pos.realized_usd
                await broker.sell(pos, result.sell_fraction, "2x initial scale-out")
                self._credit_cash(pos.realized_usd - before)
                acted += 1
            elif result.action == "flatten":
                before = pos.realized_usd
                await broker.sell(pos, 1.0, result.note or pos.flat_reason or "flatten")
                tracker.record_closed(pos, now)
                self._credit_cash(pos.realized_usd - before)
                acted += 1
            else:
                self.storage.save_position(pos.model_dump(mode="json"))
        return acted

    def _credit_cash(self, amount: float) -> None:
        if amount == 0:
            return
        cash = float(self.storage.get_meta("cash_usd", str(self.settings.starting_cash_usd())))
        self.storage.set_meta("cash_usd", str(cash + amount))

    def cash_usd(self) -> float:
        return float(self.storage.get_meta("cash_usd", str(self.settings.starting_cash_usd())))

    def hit_ratio(self) -> dict[str, float]:
        sold = failed = 0
        for pos in self.positions():
            if pos.initial_sold:
                sold += 1
            elif pos.state.value == "FLAT":
                failed += 1
        resolved = sold + failed
        return {
            "initials_sold": sold,
            "failed_2x": failed,
            "resolved": resolved,
            "hit_ratio": (sold / resolved) if resolved else 0.0,
        }

    def hourly(self, now: float | None = None) -> str:
        now = now if now is not None else time.time()
        positions = self.positions()
        start = float(self.storage.get_meta("equity_start_usd", str(self.equity_usd(positions))))
        equity = self.equity_usd(positions)
        skipped = len(self.storage.watched_candidates())
        if kill_switch_active(start, equity, self.settings):
            self.anomalies.append("kill_switch")
        body = render_hourly(
            self.settings,
            self.storage,
            positions,
            equity,
            start,
            default_observations(positions, skipped),
            list(dict.fromkeys(self.anomalies)),
            now,
        )
        path = write_hourly(self.settings, body, now)
        self.storage.index_report("hourly", str(path), body.splitlines()[0], now)
        return body

    def learn_once(self, hours: int = 4, now: float | None = None) -> dict[str, Any]:
        # The CLI flag is the report cadence. The sample window stays inside 48–168h.
        window = max(hours, 48) if hours < 48 else min(hours, 168)
        return learn(self.settings, self.storage, window, now)

    async def run(self) -> None:
        self._ensure_clients()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except NotImplementedError:
                pass
        last_report = 0.0
        last_learn = 0.0
        logger.info("agent start mode=%s", self.settings.trading_mode())
        try:
            while not self._stop.is_set():
                now = time.time()
                try:
                    await self.tick(now)
                except Exception as exc:
                    self.anomalies.append(f"tick:{exc.__class__.__name__}")
                    logger.exception("tick failed")
                if now - last_report >= 3600:
                    body = self.hourly(now)
                    try:
                        await notify(self.settings, body)
                    except Exception:
                        self.anomalies.append("notify_failed")
                    last_report = now
                if now - last_learn >= 4 * 3600:
                    self.learn_once(4, now)
                    last_learn = now
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.settings.loop_interval_sec)
                except asyncio.TimeoutError:
                    pass
        finally:
            if self.settings.flat_on_exit and self.settings.live_trading:
                broker = self.broker()
                for pos in self.positions():
                    if pos.state.value != "FLAT":
                        await broker.sell(pos, 1.0, "flat_on_exit")
            await self.aclose()

    async def aclose(self) -> None:
        if self.gmgn:
            await self.gmgn.aclose()
        if self.pump:
            await self.pump.aclose()
        if self.stonk:
            await self.stonk.aclose()


def build_agent(force_paper: bool = False, **overrides: Any) -> TradingAgent:
    settings = load_settings(**overrides)
    if force_paper:
        settings.live_trading = False
    return TradingAgent(settings)
