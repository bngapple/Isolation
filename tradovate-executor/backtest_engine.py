"""
Single-strategy backtester aligned to the current live executor rules.

This engine intentionally runs one strategy at a time so that a backtest can
match a real single-account live deployment.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import polars as pl

from config import AppConfig, POINT_VALUE
from market_data import MarketDataEngine
from signal_engine import Side, Signal, SignalEngine
from trade_logger import CSV_HEADERS

logger = logging.getLogger(__name__)


@dataclass
class MinuteBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    hhmm: int


@dataclass
class FifteenMinuteBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    start_idx: int
    end_idx: int


@dataclass
class BacktestTrade:
    strategy: str
    side: str
    contracts: int
    signal_time: datetime
    signal_price: float
    entry_time: datetime
    entry_price: float
    sl_price: float
    tp_price: float
    exit_time: datetime
    exit_price: float
    exit_reason: str
    bars_held: int
    pnl_per_contract: float
    pnl_total: float
    daily_pnl_after: float
    monthly_pnl_after: float


@dataclass
class BacktestSummary:
    strategy: str
    data_start: str
    data_end: str
    trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    avg_trade: float
    avg_monthly_pnl: float
    best_month: float
    worst_month: float
    best_day: float
    worst_day: float
    max_drawdown: float
    avg_bars_held: float


@dataclass
class BacktestResult:
    strategy: str
    config_path: str
    data_sources: list[str]
    trades: list[BacktestTrade]
    summary: BacktestSummary


@dataclass
class EnsembleSummary(BacktestSummary):
    weights: dict[str, float]
    threshold: float


@dataclass
class EnsembleRunResult(BacktestResult):
    summary: EnsembleSummary


@dataclass
class _OpenTrade:
    signal: Signal
    entry_time: datetime
    entry_price: float
    sl_price: float
    tp_price: float
    max_hold_bars: int = 0
    entry_bar_index: int = 0


class StrategyBacktester:
    """Backtests one live strategy at a time against 1-minute historical data."""

    def __init__(self, config: AppConfig, strategy: str, slippage_points: float = 0.0):
        strategy_name = strategy.upper()
        if strategy_name not in {"RSI", "IB", "MOM"}:
            raise ValueError(f"Unsupported strategy: {strategy}")

        self.config = config
        self.strategy = strategy_name
        self.slippage_points = float(slippage_points)
        self.market_data = MarketDataEngine(on_bar_complete=None)
        self.signal_engine = SignalEngine(config.rsi, config.ib, config.mom, config.session)
        self._loop = asyncio.new_event_loop()

    def run(self, minute_df: pl.DataFrame, data_sources: Optional[list[str]] = None) -> BacktestResult:
        minute_bars = self._build_minute_bars(minute_df)
        if not minute_bars:
            raise ValueError("No minute bars available for backtest")

        bars_15m = self._aggregate_15m(minute_bars)
        pending_entry: Optional[Signal] = None
        active_trade: Optional[_OpenTrade] = None
        closed_trades: list[BacktestTrade] = []
        daily_running = 0.0
        monthly_running = 0.0
        current_day = None
        current_month = None

        for bar_index, bar in enumerate(bars_15m):
            first_minute = minute_bars[bar.start_idx]

            if current_day != first_minute.timestamp.date():
                current_day = first_minute.timestamp.date()
                daily_running = 0.0
            month_key = (first_minute.timestamp.year, first_minute.timestamp.month)
            if current_month != month_key:
                current_month = month_key
                monthly_running = 0.0

            if pending_entry and active_trade is None:
                active_trade = self._enter_trade(pending_entry, first_minute)
                self.signal_engine.mark_filled(self.strategy, pending_entry.side)
                pending_entry = None

            if active_trade is not None:
                closed = self._manage_intrabar(active_trade, minute_bars, bar)
                if closed is not None:
                    daily_running += closed.pnl_total
                    monthly_running += closed.pnl_total
                    closed.daily_pnl_after = daily_running
                    closed.monthly_pnl_after = monthly_running
                    closed_trades.append(closed)
                    self.signal_engine.mark_flat(self.strategy)
                    active_trade = None

            self._ingest_15m(bar)
            generated = [sig for sig in self.signal_engine.evaluate(self.market_data.state) if sig.strategy == self.strategy]

            for sig in generated:
                if sig.contracts == 0 and active_trade is not None:
                    closed = self._close_trade(
                        active_trade,
                        exit_time=bar.timestamp,
                        raw_exit_price=bar.close,
                        exit_reason="MaxHold",
                    )
                    daily_running += closed.pnl_total
                    monthly_running += closed.pnl_total
                    closed.daily_pnl_after = daily_running
                    closed.monthly_pnl_after = monthly_running
                    closed_trades.append(closed)
                    self.signal_engine.mark_flat(self.strategy)
                    active_trade = None
                elif sig.contracts > 0 and pending_entry is None and active_trade is None:
                    pending_entry = sig

            # Flush the post-16:30 remainder minute(s) before the next session.
            next_bar = bars_15m[bar_index + 1] if bar_index + 1 < len(bars_15m) else None
            is_last_bar_of_day = next_bar is None or next_bar.timestamp.date() != bar.timestamp.date()
            if is_last_bar_of_day:
                remainder = self._session_remainder_minutes(minute_bars, bar.end_idx)
                if active_trade is not None:
                    closed = self._manage_remaining_minutes(active_trade, remainder)
                    if closed is None and remainder:
                        last_minute = remainder[-1]
                        closed = self._close_trade(active_trade, last_minute.timestamp, last_minute.close, "EOD")
                    if closed is not None:
                        daily_running += closed.pnl_total
                        monthly_running += closed.pnl_total
                        closed.daily_pnl_after = daily_running
                        closed.monthly_pnl_after = monthly_running
                        closed_trades.append(closed)
                        self.signal_engine.mark_flat(self.strategy)
                        active_trade = None

                # A queued next-bar entry cannot roll into the next session.
                pending_entry = None

        if active_trade is not None:
            remaining_start = bars_15m[-1].end_idx + 1 if bars_15m else 0
            closed = self._manage_remaining_minutes(active_trade, minute_bars[remaining_start:])
            if closed is None:
                last_minute = minute_bars[-1]
                closed = self._close_trade(active_trade, last_minute.timestamp, last_minute.close, "DataEnd")
            daily_running += closed.pnl_total
            monthly_running += closed.pnl_total
            closed.daily_pnl_after = daily_running
            closed.monthly_pnl_after = monthly_running
            closed_trades.append(closed)
            self.signal_engine.mark_flat(self.strategy)

        self._loop.close()

        summary = self._summarize(closed_trades)
        return BacktestResult(
            strategy=self.strategy,
            config_path="config.json",
            data_sources=list(data_sources or []),
            trades=closed_trades,
            summary=summary,
        )

    def _build_minute_bars(self, minute_df: pl.DataFrame) -> list[MinuteBar]:
        bars: list[MinuteBar] = []
        for row in minute_df.iter_rows(named=True):
            bars.append(MinuteBar(
                timestamp=row["ts_et"],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row["volume"]),
                hhmm=int(row["hhmm"]),
            ))
        return bars

    def _aggregate_15m(self, minute_bars: list[MinuteBar]) -> list[FifteenMinuteBar]:
        result: list[FifteenMinuteBar] = []
        start_idx = 0
        while start_idx < len(minute_bars):
            bucket_start = self._bucket_start(minute_bars[start_idx].timestamp)
            end_idx = start_idx
            highs = [minute_bars[start_idx].high]
            lows = [minute_bars[start_idx].low]
            volume = minute_bars[start_idx].volume

            while end_idx + 1 < len(minute_bars) and self._bucket_start(minute_bars[end_idx + 1].timestamp) == bucket_start:
                end_idx += 1
                highs.append(minute_bars[end_idx].high)
                lows.append(minute_bars[end_idx].low)
                volume += minute_bars[end_idx].volume

            bucket_len = end_idx - start_idx + 1
            if bucket_len == 15:
                first = minute_bars[start_idx]
                last = minute_bars[end_idx]
                result.append(FifteenMinuteBar(
                    timestamp=bucket_start,
                    open=first.open,
                    high=max(highs),
                    low=min(lows),
                    close=last.close,
                    volume=volume,
                    start_idx=start_idx,
                    end_idx=end_idx,
                ))

            start_idx = end_idx + 1

        return result

    @staticmethod
    def _bucket_start(ts: datetime) -> datetime:
        return ts.replace(minute=(ts.minute // 15) * 15, second=0, microsecond=0)

    def _ingest_15m(self, bar: FifteenMinuteBar):
        self._loop.run_until_complete(self.market_data.ingest_historical_bar(
            timestamp=bar.timestamp,
            open_=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
        ))

    def _enter_trade(self, signal: Signal, minute_bar: MinuteBar) -> _OpenTrade:
        entry_price = self._apply_entry_slippage(minute_bar.open, signal.side)
        if signal.side == Side.BUY:
            sl_price = entry_price - signal.stop_loss_pts
            tp_price = entry_price + signal.take_profit_pts
        else:
            sl_price = entry_price + signal.stop_loss_pts
            tp_price = entry_price - signal.take_profit_pts

        return _OpenTrade(
            signal=signal,
            entry_time=minute_bar.timestamp,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
        )

    def _manage_intrabar(
        self,
        trade: _OpenTrade,
        minute_bars: list[MinuteBar],
        bar_15m: FifteenMinuteBar,
    ) -> Optional[BacktestTrade]:
        for idx in range(bar_15m.start_idx, bar_15m.end_idx + 1):
            minute = minute_bars[idx]

            if minute.hhmm >= 1645:
                return self._close_trade(trade, minute.timestamp, minute.open, "EOD")

            if trade.signal.side == Side.BUY:
                if minute.low <= trade.sl_price:
                    return self._close_trade(trade, minute.timestamp, trade.sl_price, "SL")
                if minute.high >= trade.tp_price:
                    return self._close_trade(trade, minute.timestamp, trade.tp_price, "TP")
            else:
                if minute.high >= trade.sl_price:
                    return self._close_trade(trade, minute.timestamp, trade.sl_price, "SL")
                if minute.low <= trade.tp_price:
                    return self._close_trade(trade, minute.timestamp, trade.tp_price, "TP")

        return None

    def _manage_remaining_minutes(
        self,
        trade: _OpenTrade,
        minute_bars: list[MinuteBar],
    ) -> Optional[BacktestTrade]:
        for minute in minute_bars:
            if minute.hhmm >= 1645:
                return self._close_trade(trade, minute.timestamp, minute.open, "EOD")

            if trade.signal.side == Side.BUY:
                if minute.low <= trade.sl_price:
                    return self._close_trade(trade, minute.timestamp, trade.sl_price, "SL")
                if minute.high >= trade.tp_price:
                    return self._close_trade(trade, minute.timestamp, trade.tp_price, "TP")
            else:
                if minute.high >= trade.sl_price:
                    return self._close_trade(trade, minute.timestamp, trade.sl_price, "SL")
                if minute.low <= trade.tp_price:
                    return self._close_trade(trade, minute.timestamp, trade.tp_price, "TP")

        return None

    @staticmethod
    def _session_remainder_minutes(minute_bars: list[MinuteBar], bar_end_idx: int) -> list[MinuteBar]:
        if bar_end_idx + 1 >= len(minute_bars):
            return []

        session_date = minute_bars[bar_end_idx].timestamp.date()
        remainder: list[MinuteBar] = []
        idx = bar_end_idx + 1
        while idx < len(minute_bars) and minute_bars[idx].timestamp.date() == session_date:
            remainder.append(minute_bars[idx])
            idx += 1
        return remainder

    def _close_trade(
        self,
        trade: _OpenTrade,
        exit_time: datetime,
        raw_exit_price: float,
        exit_reason: str,
    ) -> BacktestTrade:
        exit_price = self._apply_exit_slippage(raw_exit_price, trade.signal.side)
        if trade.signal.side == Side.BUY:
            pnl_per_contract = (exit_price - trade.entry_price) * POINT_VALUE
        else:
            pnl_per_contract = (trade.entry_price - exit_price) * POINT_VALUE

        bars_held = 0
        pos = self.signal_engine.positions.get(self.strategy)
        if pos is not None:
            bars_held = pos.bars_held
        return BacktestTrade(
            strategy=self.strategy,
            side=trade.signal.side.value,
            contracts=trade.signal.contracts,
            signal_time=trade.signal.bar_timestamp,
            signal_price=trade.signal.signal_price,
            entry_time=trade.entry_time,
            entry_price=trade.entry_price,
            sl_price=trade.sl_price,
            tp_price=trade.tp_price,
            exit_time=exit_time,
            exit_price=exit_price,
            exit_reason=exit_reason,
            bars_held=bars_held,
            pnl_per_contract=pnl_per_contract,
            pnl_total=pnl_per_contract * trade.signal.contracts,
            daily_pnl_after=0.0,
            monthly_pnl_after=0.0,
        )

    def _apply_entry_slippage(self, price: float, side: Side) -> float:
        if side == Side.BUY:
            return price + self.slippage_points
        return price - self.slippage_points

    def _apply_exit_slippage(self, price: float, side: Side) -> float:
        if side == Side.BUY:
            return price - self.slippage_points
        return price + self.slippage_points

    def _summarize(self, trades: list[BacktestTrade]) -> BacktestSummary:
        if not trades:
            return BacktestSummary(
                strategy=self.strategy,
                data_start="",
                data_end="",
                trades=0,
                wins=0,
                losses=0,
                win_rate=0.0,
                total_pnl=0.0,
                avg_trade=0.0,
                avg_monthly_pnl=0.0,
                best_month=0.0,
                worst_month=0.0,
                best_day=0.0,
                worst_day=0.0,
                max_drawdown=0.0,
                avg_bars_held=0.0,
            )

        daily: dict[str, float] = {}
        monthly: dict[str, float] = {}
        for trade in trades:
            day_key = trade.entry_time.strftime("%Y-%m-%d")
            month_key = trade.entry_time.strftime("%Y-%m")
            daily[day_key] = daily.get(day_key, 0.0) + trade.pnl_total
            monthly[month_key] = monthly.get(month_key, 0.0) + trade.pnl_total

        running = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for day in sorted(daily):
            running += daily[day]
            peak = max(peak, running)
            max_drawdown = min(max_drawdown, running - peak)

        total_pnl = sum(t.pnl_total for t in trades)
        wins = sum(1 for t in trades if t.pnl_total > 0)
        losses = sum(1 for t in trades if t.pnl_total < 0)

        return BacktestSummary(
            strategy=self.strategy,
            data_start=trades[0].entry_time.isoformat(),
            data_end=trades[-1].exit_time.isoformat(),
            trades=len(trades),
            wins=wins,
            losses=losses,
            win_rate=(wins / len(trades)) * 100.0,
            total_pnl=total_pnl,
            avg_trade=total_pnl / len(trades),
            avg_monthly_pnl=total_pnl / max(len(monthly), 1),
            best_month=max(monthly.values()) if monthly else 0.0,
            worst_month=min(monthly.values()) if monthly else 0.0,
            best_day=max(daily.values()) if daily else 0.0,
            worst_day=min(daily.values()) if daily else 0.0,
            max_drawdown=max_drawdown,
            avg_bars_held=sum(t.bars_held for t in trades) / len(trades),
        )


def write_result_files(result: BacktestResult, output_dir: str | Path) -> tuple[str, str]:
    """Persist backtest trades and summary in a live-comparable format."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    csv_path = out_dir / f"backtest_{result.strategy.lower()}_{stamp}.csv"
    json_path = out_dir / f"backtest_{result.strategy.lower()}_{stamp}.json"

    with csv_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADERS)
        for trade in result.trades:
            writer.writerow([
                trade.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
                trade.strategy,
                "BACKTEST",
                "Entry",
                trade.side,
                trade.contracts,
                f"{trade.signal_price:.2f}",
                f"{trade.entry_price:.2f}",
                f"{abs(trade.entry_price - trade.signal_price):.2f}",
                f"{trade.sl_price:.2f}",
                f"{trade.tp_price:.2f}",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            ])
            writer.writerow([
                trade.exit_time.strftime("%Y-%m-%d %H:%M:%S"),
                trade.strategy,
                "BACKTEST",
                "Exit",
                trade.side,
                trade.contracts,
                f"{trade.signal_price:.2f}",
                f"{trade.entry_price:.2f}",
                f"{abs(trade.entry_price - trade.signal_price):.2f}",
                f"{trade.sl_price:.2f}",
                f"{trade.tp_price:.2f}",
                trade.exit_reason,
                f"{trade.exit_price:.2f}",
                f"{trade.pnl_per_contract:.2f}",
                f"{trade.pnl_total:.2f}",
                trade.bars_held,
                f"{trade.daily_pnl_after:.2f}",
                f"{trade.monthly_pnl_after:.2f}",
            ])

    payload = {
        "strategy": result.strategy,
        "config_path": result.config_path,
        "data_sources": result.data_sources,
        "summary": asdict(result.summary),
        "trades": [asdict(trade) for trade in result.trades],
    }
    with json_path.open("w") as handle:
        json.dump(payload, handle, indent=2, default=str)

    logger.info("Backtest output written to %s and %s", csv_path, json_path)
    return str(csv_path), str(json_path)
