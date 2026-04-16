"""Replay original Hybrid v2 signals through a one-account execution model."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from backtest_data import discover_parquet_files, load_parquet_files
from config import lucid_defaults
from execution_policy import select_account_entry
from model_lucid import evaluate_funded_path, evaluate_lucid_path, run_monte_carlo_eval
from run_hybrid_v2_parity import (
    HYBRID_V2,
    resample_15m_session,
    sig_ib_breakout,
    sig_momentum_bar,
    sig_rsi_extreme,
)
from run_live_style_replay import (
    FifteenBar,
    MinuteBar,
    OpenTrade,
    aggregate_15m,
    build_minute_bars,
    close_trade,
    manage_intrabar,
    session_remainder_minutes,
)
from signal_engine import Side, Signal


EDGE_SCORES = {"IB": 53.76, "MOM": 36.21, "RSI": 23.53}


def _build_signal_arrays(df_15m):
    rsi = HYBRID_V2["RSI"]
    ib = HYBRID_V2["IB"]
    mom = HYBRID_V2["MOM"]
    return {
        "RSI": sig_rsi_extreme(df_15m, rsi["period"], rsi["ob"], rsi["os"]),
        "IB": sig_ib_breakout(df_15m, ib["ib_filter"]),
        "MOM": sig_momentum_bar(df_15m, mom["atr_mult"], mom["vol_mult"]),
    }


def _signal_for_bar(strategy: str, value: int, bar: FifteenBar) -> Signal | None:
    if value == 0:
        return None
    params = HYBRID_V2[strategy]
    side = Side.BUY if value > 0 else Side.SELL
    return Signal(
        strategy=strategy,
        side=side,
        contracts=1,
        stop_loss_pts=params["sl_pts"],
        take_profit_pts=params["tp_pts"],
        max_hold_bars=params["hold"],
        reason=f"Hybrid v2 {strategy}",
        bar_timestamp=bar.timestamp,
        signal_price=bar.close,
    )


def run_replay(minute_df, start_date: str | None = None, disabled_strategies: set[str] | None = None) -> dict:
    disabled = disabled_strategies or set()
    if start_date:
        minute_df = minute_df.filter(minute_df["date_et"] >= datetime.fromisoformat(start_date).date())

    minute_bars = build_minute_bars(minute_df)
    bars_15m = aggregate_15m(minute_bars)
    df_15m = resample_15m_session(minute_df, end_hhmm=1645)
    signal_arrays = _build_signal_arrays(df_15m)

    pending_signal: Signal | None = None
    active_trade: OpenTrade | None = None
    active_entry_bar_index = 0
    closed_trades = []

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
            active_entry_bar_index = bar_idx
            pending_signal = None

        if active_trade is not None:
            intrabar = manage_intrabar(active_trade, minute_bars, bar.start_idx, bar.end_idx)
            if intrabar is not None:
                exit_time, exit_price, reason = intrabar
                bars_held = max(0, bar_idx - active_entry_bar_index)
                closed_trades.append(close_trade(active_trade, exit_time, exit_price, reason, bars_held))
                active_trade = None

        if active_trade is not None:
            bars_held = max(0, bar_idx - active_entry_bar_index)
            if bars_held >= active_trade.signal.max_hold_bars:
                closed_trades.append(close_trade(active_trade, bar.timestamp, bar.close, "MaxHold", bars_held))
                active_trade = None

        entries = []
        if bar_idx < len(df_15m):
            for strategy, arr in signal_arrays.items():
                if strategy in disabled:
                    continue
                sig = _signal_for_bar(strategy, int(arr[bar_idx]), bar)
                if sig is not None:
                    entries.append(sig)

        selected, _ = select_account_entry(
            entries,
            execution_cfg=type("ExecCfg", (), {
                "single_position_mode": True,
                "same_bar_conflict_policy": "strongest_edge",
                "strategy_edge_scores": EDGE_SCORES,
            })(),
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
                    bars_held = max(0, bar_idx - active_entry_bar_index)
                    closed_trades.append(close_trade(active_trade, exit_time, exit_price, reason, bars_held))
                    active_trade = None
            pending_signal = None

    if active_trade is not None:
        last = minute_bars[-1]
        bars_held = max(0, len(bars_15m) - 1 - active_entry_bar_index)
        closed_trades.append(close_trade(active_trade, last.timestamp, last.close, "DataEnd", bars_held))

    daily = defaultdict(float)
    monthly = defaultdict(float)
    strategy_pnl = defaultdict(float)
    strategy_counts = defaultdict(int)
    for trade in closed_trades:
        daily[trade.entry_time[:10]] += trade.net_pnl
        monthly[trade.entry_time[:7]] += trade.net_pnl
        strategy_pnl[trade.strategy] += trade.net_pnl
        strategy_counts[trade.strategy] += 1

    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for day in sorted(daily):
        cum += daily[day]
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)

    daily_values = [daily[k] for k in sorted(daily)]

    def lucid_model(account_size: float) -> dict:
        defaults = lucid_defaults(account_size)
        dd = float(defaults["max_drawdown"])
        target = float(defaults["profit_target"])
        return {
            "account_size": account_size,
            "drawdown_limit": dd,
            "profit_target": target,
            "direct": asdict(evaluate_lucid_path(daily_values, dd, target, 50.0)),
            "mc": asdict(run_monte_carlo_eval(daily_values, dd, target, 50.0, 5000, 1.0)),
            "funded": asdict(evaluate_funded_path(daily_values, dd)),
        }

    return {
        "config": {
            "mode": "hybrid_v2_one_account_replay",
            "session_end": 1645,
            "flatten_time": 1645,
            "edge_scores": EDGE_SCORES,
            "disabled_strategies": sorted(disabled),
        },
        "summary": {
            "trades": len(closed_trades),
            "total_pnl": sum(t.net_pnl for t in closed_trades),
            "monthly_avg": sum(t.net_pnl for t in closed_trades) / max(len(monthly), 1),
            "worst_month": min(monthly.values()) if monthly else 0.0,
            "best_month": max(monthly.values()) if monthly else 0.0,
            "max_drawdown": max_dd,
            "strategy_counts": dict(strategy_counts),
            "strategy_pnl": dict(strategy_pnl),
        },
        "lucid": {
            "25k": lucid_model(25_000.0),
            "150k": lucid_model(150_000.0),
        },
        "trades": [asdict(t) for t in closed_trades],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay original Hybrid v2 signals through one-account logic")
    parser.add_argument("--data", action="append", default=[])
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--disable-strategy", action="append", default=[], choices=["RSI", "IB", "MOM"])
    parser.add_argument("--output", default="reports/backtests/hybrid_v2_one_account_replay.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_inputs = args.data or [str(Path("data/processed/MNQ/1m"))]
    parquet_files = discover_parquet_files(data_inputs)
    minute_df = load_parquet_files(parquet_files)
    result = run_replay(minute_df, start_date=args.start_date, disabled_strategies=set(args.disable_strategy))
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
    print(f"Lucid 25k direct pass: {result['lucid']['25k']['direct']['passed']}")
    print(f"Lucid 150k direct pass: {result['lucid']['150k']['direct']['passed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
