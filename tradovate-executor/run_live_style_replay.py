"""Replay the live-style one-account executor on historical data."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from backtest_data import discover_parquet_files, load_parquet_files
from config import AppConfig, POINT_VALUE
from execution_policy import select_account_entry
from market_data import MarketDataEngine
from model_lucid import evaluate_funded_path, evaluate_lucid_path, run_monte_carlo_eval
from signal_engine import Side, Signal, SignalEngine


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
class FifteenBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    start_idx: int
    end_idx: int


@dataclass
class OpenTrade:
    signal: Signal
    entry_time: datetime
    entry_price: float
    sl_price: float
    tp_price: float


@dataclass
class ReplayTrade:
    strategy: str
    side: str
    contracts: int
    signal_time: str
    entry_time: str
    exit_time: str
    signal_price: float
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    reason: str
    bars_held: int
    net_pnl: float


def _bucket_start(ts: datetime) -> datetime:
    return ts.replace(minute=(ts.minute // 15) * 15, second=0, microsecond=0)


def build_minute_bars(minute_df) -> list[MinuteBar]:
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


def aggregate_15m(minute_bars: list[MinuteBar]) -> list[FifteenBar]:
    result: list[FifteenBar] = []
    start_idx = 0
    while start_idx < len(minute_bars):
        bucket = _bucket_start(minute_bars[start_idx].timestamp)
        end_idx = start_idx
        highs = [minute_bars[start_idx].high]
        lows = [minute_bars[start_idx].low]
        volume = minute_bars[start_idx].volume
        while end_idx + 1 < len(minute_bars) and _bucket_start(minute_bars[end_idx + 1].timestamp) == bucket:
            end_idx += 1
            highs.append(minute_bars[end_idx].high)
            lows.append(minute_bars[end_idx].low)
            volume += minute_bars[end_idx].volume
        if end_idx - start_idx + 1 == 15:
            first = minute_bars[start_idx]
            last = minute_bars[end_idx]
            result.append(FifteenBar(
                timestamp=bucket,
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


def manage_intrabar(trade: OpenTrade, minute_bars: list[MinuteBar], start_idx: int, end_idx: int) -> tuple[datetime, float, str] | None:
    for idx in range(start_idx, end_idx + 1):
        minute = minute_bars[idx]
        if minute.hhmm >= 1645:
            return minute.timestamp, minute.open, "EOD"
        if trade.signal.side == Side.BUY:
            if minute.low <= trade.sl_price:
                return minute.timestamp, trade.sl_price, "SL"
            if minute.high >= trade.tp_price:
                return minute.timestamp, trade.tp_price, "TP"
        else:
            if minute.high >= trade.sl_price:
                return minute.timestamp, trade.sl_price, "SL"
            if minute.low <= trade.tp_price:
                return minute.timestamp, trade.tp_price, "TP"
    return None


def session_remainder_minutes(minute_bars: list[MinuteBar], bar_end_idx: int) -> list[MinuteBar]:
    if bar_end_idx + 1 >= len(minute_bars):
        return []
    session_date = minute_bars[bar_end_idx].timestamp.date()
    remainder: list[MinuteBar] = []
    idx = bar_end_idx + 1
    while idx < len(minute_bars) and minute_bars[idx].timestamp.date() == session_date:
        remainder.append(minute_bars[idx])
        idx += 1
    return remainder


def close_trade(trade: OpenTrade, exit_time: datetime, exit_price: float, reason: str, bars_held: int) -> ReplayTrade:
    if trade.signal.side == Side.BUY:
        pnl = (exit_price - trade.entry_price) * POINT_VALUE * trade.signal.contracts
    else:
        pnl = (trade.entry_price - exit_price) * POINT_VALUE * trade.signal.contracts
    return ReplayTrade(
        strategy=trade.signal.strategy,
        side=trade.signal.side.value,
        contracts=trade.signal.contracts,
        signal_time=trade.signal.bar_timestamp.isoformat(),
        entry_time=trade.entry_time.isoformat(),
        exit_time=exit_time.isoformat(),
        signal_price=trade.signal.signal_price,
        entry_price=trade.entry_price,
        exit_price=exit_price,
        stop_price=trade.sl_price,
        target_price=trade.tp_price,
        reason=reason,
        bars_held=bars_held,
        net_pnl=pnl,
    )


def run_replay(config: AppConfig, minute_df, start_date: str | None = None) -> dict:
    disabled = set(getattr(config, "_replay_disabled_strategies", set()))
    if start_date:
        minute_df = minute_df.filter(minute_df["date_et"] >= datetime.fromisoformat(start_date).date())

    minute_bars = build_minute_bars(minute_df)
    bars_15m = aggregate_15m(minute_bars)

    loop = asyncio.new_event_loop()
    market_data = MarketDataEngine(on_bar_complete=None)
    signal_engine = SignalEngine(config.rsi, config.ib, config.mom, config.session)

    pending_signal: Signal | None = None
    active_trade: OpenTrade | None = None
    closed_trades: list[ReplayTrade] = []

    for bar_idx, bar in enumerate(bars_15m):
        if pending_signal and active_trade is None:
            entry_price = bar.open
            if pending_signal.side == Side.BUY:
                sl_price = entry_price - pending_signal.stop_loss_pts
                tp_price = entry_price + pending_signal.take_profit_pts
            else:
                sl_price = entry_price + pending_signal.stop_loss_pts
                tp_price = entry_price - pending_signal.take_profit_pts
            active_trade = OpenTrade(
                signal=pending_signal,
                entry_time=minute_bars[bar.start_idx].timestamp,
                entry_price=entry_price,
                sl_price=sl_price,
                tp_price=tp_price,
            )
            signal_engine.mark_filled(pending_signal.strategy, pending_signal.side)
            pending_signal = None

        if active_trade is not None:
            intrabar = manage_intrabar(active_trade, minute_bars, bar.start_idx, bar.end_idx)
            if intrabar is not None:
                exit_time, exit_price, reason = intrabar
                bars_held = signal_engine.positions[active_trade.signal.strategy].bars_held
                closed_trades.append(close_trade(active_trade, exit_time, exit_price, reason, bars_held))
                signal_engine.mark_flat(active_trade.signal.strategy)
                active_trade = None

        loop.run_until_complete(market_data.ingest_historical_bar(
            timestamp=bar.timestamp,
            open_=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
        ))

        signals = signal_engine.evaluate(market_data.state)
        flattens = [sig for sig in signals if sig.contracts == 0]
        entries = [sig for sig in signals if sig.contracts > 0 and sig.strategy not in disabled]

        for sig in flattens:
            if active_trade is not None and active_trade.signal.strategy == sig.strategy:
                bars_held = signal_engine.positions[active_trade.signal.strategy].bars_held
                closed_trades.append(close_trade(active_trade, bar.timestamp, bar.close, "MaxHold", bars_held))
                signal_engine.mark_flat(sig.strategy)
                active_trade = None

        selected, _ = select_account_entry(
            entries,
            execution_cfg=config.execution,
            has_pending_entry=pending_signal is not None,
            has_open_position=active_trade is not None,
        )
        if selected is not None:
            pending_signal = selected

        next_bar = bars_15m[bar_idx + 1] if bar_idx + 1 < len(bars_15m) else None
        is_last_bar_of_day = next_bar is None or next_bar.timestamp.date() != bar.timestamp.date()
        if is_last_bar_of_day:
            remainder = session_remainder_minutes(minute_bars, bar.end_idx)
            if active_trade is not None:
                intrabar = None
                if remainder:
                    intrabar = manage_intrabar(active_trade, remainder, 0, len(remainder) - 1)
                if intrabar is None and remainder:
                    last = remainder[-1]
                    intrabar = (last.timestamp, last.close, "EOD")
                if intrabar is not None:
                    exit_time, exit_price, reason = intrabar
                    bars_held = signal_engine.positions[active_trade.signal.strategy].bars_held
                    closed_trades.append(close_trade(active_trade, exit_time, exit_price, reason, bars_held))
                    signal_engine.mark_flat(active_trade.signal.strategy)
                    active_trade = None

            pending_signal = None

    if active_trade is not None:
        last = minute_bars[-1]
        bars_held = signal_engine.positions[active_trade.signal.strategy].bars_held
        closed_trades.append(close_trade(active_trade, last.timestamp, last.close, "DataEnd", bars_held))
        signal_engine.mark_flat(active_trade.signal.strategy)

    loop.close()

    daily = defaultdict(float)
    monthly = defaultdict(float)
    strat_counts = defaultdict(int)
    strat_pnl = defaultdict(float)
    for trade in closed_trades:
        daily[trade.entry_time[:10]] += trade.net_pnl
        monthly[trade.entry_time[:7]] += trade.net_pnl
        strat_counts[trade.strategy] += 1
        strat_pnl[trade.strategy] += trade.net_pnl

    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for day in sorted(daily):
        cum += daily[day]
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)

    lucid_direct = evaluate_lucid_path([daily[k] for k in sorted(daily)], -4500.0, 9000.0, 50.0)
    lucid_mc = run_monte_carlo_eval([daily[k] for k in sorted(daily)], -4500.0, 9000.0, 50.0, 5000, 1.0)
    lucid_funded = evaluate_funded_path([daily[k] for k in sorted(daily)], -4500.0)

    return {
        "config": {
            "mode": "live_style_replay",
            "single_position_mode": config.execution.single_position_mode,
            "same_bar_conflict_policy": config.execution.same_bar_conflict_policy,
            "strategy_edge_scores": config.execution.strategy_edge_scores,
            "rsi_contracts": config.rsi.contracts,
            "ib_contracts": config.ib.contracts,
            "mom_contracts": config.mom.contracts,
            "disabled_strategies": sorted(disabled),
        },
        "summary": {
            "trades": len(closed_trades),
            "total_pnl": sum(t.net_pnl for t in closed_trades),
            "monthly_avg": sum(t.net_pnl for t in closed_trades) / max(len(monthly), 1),
            "worst_month": min(monthly.values()) if monthly else 0.0,
            "best_month": max(monthly.values()) if monthly else 0.0,
            "worst_day": min(daily.values()) if daily else 0.0,
            "best_day": max(daily.values()) if daily else 0.0,
            "max_drawdown": max_dd,
            "strategy_counts": dict(strat_counts),
            "strategy_pnl": dict(strat_pnl),
        },
        "lucid_150k": {
            "direct": asdict(lucid_direct),
            "mc": asdict(lucid_mc),
            "funded": asdict(lucid_funded),
        },
        "trades": [asdict(t) for t in closed_trades],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay live-style executor on historical data")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--data", action="append", default=[])
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--disable-strategy", action="append", default=[], choices=["RSI", "IB", "MOM"])
    parser.add_argument("--output", default="reports/backtests/live_style_replay.json")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
    logging.getLogger("market_data").setLevel(logging.WARNING)
    logging.getLogger("signal_engine").setLevel(logging.WARNING)

    args = parse_args()
    config = AppConfig.load(args.config)
    config._replay_disabled_strategies = set(args.disable_strategy)
    data_inputs = args.data or [str(Path("data/processed/MNQ/1m"))]
    parquet_files = discover_parquet_files(data_inputs)
    minute_df = load_parquet_files(parquet_files)
    result = run_replay(config, minute_df, start_date=args.start_date)
    result["data_sources"] = parquet_files
    result["start_date_filter"] = args.start_date

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, default=str))

    summary = result["summary"]
    print(f"Output: {output_path}")
    print(f"Trades: {summary['trades']}")
    print(f"Total P&L: ${summary['total_pnl']:,.2f}")
    print(f"Monthly avg: ${summary['monthly_avg']:,.2f}")
    print(f"Max DD: ${summary['max_drawdown']:,.2f}")
    print(f"Lucid direct pass: {result['lucid_150k']['direct']['passed']}")
    print(f"Lucid direct blown: {result['lucid_150k']['direct']['blown']}")
    print(f"Lucid MC pass: {result['lucid_150k']['mc']['pass_rate']:.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
