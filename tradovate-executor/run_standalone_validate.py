"""Validate standalone RSI Phase 1 features against the old RSI research engine."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import numpy as np
import polars as pl

from backtest_data import discover_parquet_files, load_parquet_files
from run_hybrid_v2_parity import (
    CONTRACTS_PER_STRATEGY,
    HYBRID_V2,
    POINT_VALUE,
    SLIP_PTS,
    TICK_SIZE,
    Trade,
    calc_atr,
    calc_metrics,
    calc_rsi,
    resample_15m_session,
    rt_cost,
)


RSI_PERIOD = 4
OVERSOLD = 40.0
OVERBOUGHT = 60.0
BREAK_EVEN_MINUTES = 5
KILLSWITCH_DOLLAR = -750.0
LAST_ENTRY_HHMM = 1630
FLATTEN_HHMM = 1645


@dataclass(frozen=True)
class Variant:
    name: str
    break_even: bool
    killswitch: bool
    atr_filter: bool
    atr_threshold_mult: float = 1.0
    contracts: int = CONTRACTS_PER_STRATEGY
    killswitch_dollar: float = KILLSWITCH_DOLLAR
    be_minutes: int = BREAK_EVEN_MINUTES
    morning_only: bool = False
    pyramid: bool = False
    pyramid_add_qty: int = 0
    pyramid_max_qty: int = 0
    trailing_stop: bool = False
    partial_tp: bool = False
    partial_tp_qty: int = 0
    scaled_tp: bool = False
    reentry: bool = False
    opposite_exit: bool = False


@dataclass
class PositionState:
    direction: int
    entry_px: float
    entry_time: object
    entry_bar: int
    stop_px: float
    target_px: float
    contracts: int
    be_applied: bool = False
    pyramid_added: bool = False
    partial_taken: bool = False
    favorable_max: float = 0.0


@dataclass
class IntradayPositionState:
    direction: int
    entry_px: float
    entry_time: object
    entry_bar: int
    entry_minute_idx: int
    stop_px: float
    target_px: float
    contracts: int
    be_applied: bool = False
    pending_be: bool = False
    trail_level: int = 0
    favorable_max: float = 0.0


VARIANTS = (
    Variant("baseline", break_even=False, killswitch=False, atr_filter=False),
    Variant("be_only", break_even=True, killswitch=False, atr_filter=False),
    Variant("killswitch_only", break_even=False, killswitch=True, atr_filter=False),
    Variant("be_killswitch", break_even=True, killswitch=True, atr_filter=False),
    Variant("be_killswitch_atr_loose", break_even=True, killswitch=True, atr_filter=True, atr_threshold_mult=0.75),
    Variant("all_features", break_even=True, killswitch=True, atr_filter=True),
)


def rolling_atr_filter_flags(df_15m: pl.DataFrame, threshold_mult: float) -> np.ndarray:
    highs = df_15m["high"].to_numpy()
    lows = df_15m["low"].to_numpy()
    closes = df_15m["close"].to_numpy()
    atr = calc_atr(highs, lows, closes, 14)

    flags = np.ones(len(df_15m), dtype=bool)
    buffer = np.full(50, np.nan)
    buffer_idx = 0
    buffer_count = 0

    for i, atr_value in enumerate(atr):
        if not np.isnan(atr_value) and atr_value > 0:
            buffer[buffer_idx] = atr_value
            buffer_idx = (buffer_idx + 1) % len(buffer)
            if buffer_count < len(buffer):
                buffer_count += 1

        if buffer_count < len(buffer):
            flags[i] = True
            continue

        values = np.sort(buffer[~np.isnan(buffer)])
        if len(values) == 0:
            flags[i] = True
            continue

        mid = len(values) // 2
        median = (values[mid - 1] + values[mid]) / 2.0 if len(values) % 2 == 0 else values[mid]
        flags[i] = not np.isnan(atr_value) and atr_value >= (threshold_mult * median)

    return flags


def build_signals(df_15m: pl.DataFrame, enable_atr_filter: bool, atr_threshold_mult: float) -> np.ndarray:
    closes = df_15m["close"].to_numpy()
    hhmm = df_15m["hhmm"].to_numpy()
    rsi = calc_rsi(closes, RSI_PERIOD)
    atr_flags = rolling_atr_filter_flags(df_15m, atr_threshold_mult) if enable_atr_filter else np.ones(len(df_15m), dtype=bool)

    signals = np.zeros(len(df_15m), dtype=np.int8)
    for i in range(len(df_15m)):
        if np.isnan(rsi[i]) or int(hhmm[i]) >= 1630 or not atr_flags[i]:
            continue

        if rsi[i] < OVERSOLD:
            signals[i] = 1
        elif rsi[i] > OVERBOUGHT:
            signals[i] = -1

    return signals


def _build_trade(direction: int, entry_px: float, exit_px: float, contracts: int, entry_time: object, exit_time: object, bars_held: int, reason: str) -> Trade:
    net_pnl = ((exit_px - entry_px) * direction * POINT_VALUE * contracts) - rt_cost(contracts)
    return Trade(
        direction=direction,
        entry_px=entry_px,
        exit_px=exit_px,
        contracts=contracts,
        net_pnl=net_pnl,
        entry_time=str(entry_time),
        exit_time=str(exit_time),
        bars_held=bars_held,
        reason=reason,
        strategy="RSI_STANDALONE",
    )


def _run_variant_legacy(minute_df: pl.DataFrame, variant: Variant, session_classifications: dict[str, str] | None = None) -> tuple[dict, list[Trade]]:
    df_15m = resample_15m_session(minute_df, end_hhmm=1645)
    opens = df_15m["open"].to_numpy()
    highs = df_15m["high"].to_numpy()
    lows = df_15m["low"].to_numpy()
    closes = df_15m["close"].to_numpy()
    timestamps = df_15m["timestamp"].to_list()
    dates = df_15m["date_et"].to_list()
    hhmm = df_15m["hhmm"].to_numpy()
    rsi = calc_rsi(closes, RSI_PERIOD)
    signals = build_signals(df_15m, enable_atr_filter=variant.atr_filter, atr_threshold_mult=variant.atr_threshold_mult)

    rsi_params = HYBRID_V2["RSI"]
    sl_ticks = int(rsi_params["sl_pts"] / TICK_SIZE)
    default_tp_ticks = int(rsi_params["tp_pts"] / TICK_SIZE)
    max_hold_bars = int(rsi_params["hold"])
    contracts = variant.contracts

    trades: list[Trade] = []
    position: PositionState | None = None
    pending = 0
    daily_realized = 0.0
    trading_halted = False
    killswitch_days = 0
    current_date = None
    reentry_block_until = -1
    reentry_pending_bar = -1
    reentry_direction = 0
    reentry_used_signal = None

    for i in range(len(df_15m)):
        if current_date != dates[i]:
            current_date = dates[i]
            daily_realized = 0.0
            trading_halted = False
            reentry_block_until = -1
            reentry_pending_bar = -1
            reentry_direction = 0
            reentry_used_signal = None

        if variant.reentry and position is None and not trading_halted and reentry_pending_bar == i and i >= reentry_block_until:
            if not np.isnan(rsi[i]):
                if (reentry_direction == 1 and rsi[i] < OVERSOLD) or (reentry_direction == -1 and rsi[i] > OVERBOUGHT):
                    pending = reentry_direction
            reentry_pending_bar = -1

        if pending != 0 and position is None:
            if not trading_halted:
                if variant.morning_only and int(hhmm[i]) > 1130:
                    pending = 0
                else:
                    entry_px = opens[i] + int(pending) * SLIP_PTS
                    direction = int(pending)
                    target_ticks = default_tp_ticks
                    if variant.scaled_tp and session_classifications is not None:
                        day_key = str(dates[i])
                        day_class = session_classifications.get(day_key, "medium")
                        if day_class == "high":
                            target_ticks = int(140.0 / TICK_SIZE)
                        elif day_class == "low":
                            target_ticks = int(70.0 / TICK_SIZE)
                    position = PositionState(
                        direction=direction,
                        entry_px=entry_px,
                        entry_time=timestamps[i],
                        entry_bar=i,
                        stop_px=entry_px - direction * sl_ticks * TICK_SIZE,
                        target_px=entry_px + direction * target_ticks * TICK_SIZE,
                        contracts=contracts,
                    )
            pending = 0

        if position is not None and i > position.entry_bar:
            if variant.break_even and not position.be_applied:
                position.stop_px = position.entry_px
                position.be_applied = True

            favorable = highs[i] - position.entry_px if position.direction == 1 else position.entry_px - lows[i]
            position.favorable_max = max(position.favorable_max, favorable)

            if variant.trailing_stop and position.be_applied:
                if position.favorable_max >= 75.0:
                    position.stop_px = max(position.stop_px, position.entry_px + position.direction * 50.0)
                elif position.favorable_max >= 50.0:
                    position.stop_px = max(position.stop_px, position.entry_px + position.direction * 30.0)
                elif position.favorable_max >= 30.0:
                    position.stop_px = max(position.stop_px, position.entry_px + position.direction * 15.0)

            if variant.pyramid and not position.pyramid_added and (i - position.entry_bar) >= 1 and position.favorable_max >= 20.0:
                add_qty = min(variant.pyramid_add_qty, max(0, variant.pyramid_max_qty - position.contracts))
                if add_qty > 0:
                    add_px = opens[i] + position.direction * SLIP_PTS
                    weighted_entry = ((position.entry_px * position.contracts) + (add_px * add_qty)) / (position.contracts + add_qty)
                    position.entry_px = weighted_entry
                    position.contracts += add_qty
                    position.pyramid_added = True

            bars_held = i - position.entry_bar
            exit_px = None
            reason = ""
            exit_contracts = position.contracts

            if position.direction == 1 and lows[i] <= position.stop_px:
                exit_px = position.stop_px - position.direction * SLIP_PTS
                reason = "break_even" if position.be_applied and abs(position.stop_px - position.entry_px) < 1e-12 else "stop_loss"
            elif position.direction == -1 and highs[i] >= position.stop_px:
                exit_px = position.stop_px - position.direction * SLIP_PTS
                reason = "break_even" if position.be_applied and abs(position.stop_px - position.entry_px) < 1e-12 else "stop_loss"

            if exit_px is None:
                if variant.partial_tp and not position.partial_taken and position.contracts > 1 and position.favorable_max >= 50.0:
                    exit_px = position.entry_px + position.direction * 50.0 - position.direction * SLIP_PTS
                    exit_contracts = variant.partial_tp_qty if variant.partial_tp_qty > 0 else max(1, position.contracts // 2)
                    exit_contracts = min(exit_contracts, max(1, position.contracts - 1))
                    reason = "partial_tp"
                elif position.direction == 1 and highs[i] >= position.target_px:
                    exit_px = position.target_px - position.direction * SLIP_PTS
                    reason = "take_profit"
                elif position.direction == -1 and lows[i] <= position.target_px:
                    exit_px = position.target_px - position.direction * SLIP_PTS
                    reason = "take_profit"

            if exit_px is None and variant.opposite_exit and not np.isnan(rsi[i]):
                if position.direction == 1 and rsi[i] > OVERBOUGHT:
                    exit_px = closes[i] - position.direction * SLIP_PTS
                    reason = "opposite_exit"
                elif position.direction == -1 and rsi[i] < OVERSOLD:
                    exit_px = closes[i] - position.direction * SLIP_PTS
                    reason = "opposite_exit"

            if exit_px is None and bars_held >= max_hold_bars:
                exit_px = closes[i] - position.direction * SLIP_PTS
                reason = "max_hold"

            if exit_px is not None:
                trade = _build_trade(position.direction, position.entry_px, exit_px, exit_contracts, position.entry_time, timestamps[i], bars_held, reason)
                trades.append(trade)
                daily_realized += trade.net_pnl

                if reason == "partial_tp":
                    position.contracts -= exit_contracts
                    position.partial_taken = True
                else:
                    if reason == "break_even":
                        reentry_block_until = max(reentry_block_until, position.entry_bar + max_hold_bars)
                        if variant.reentry and reentry_used_signal is None:
                            reentry_pending_bar = i + 1
                            reentry_direction = position.direction
                            reentry_used_signal = f"{dates[i]}_{position.entry_bar}"
                    position = None

                if variant.killswitch and not trading_halted and daily_realized <= variant.killswitch_dollar:
                    trading_halted = True
                    killswitch_days += 1

        if position is None and not trading_halted and i >= reentry_block_until and signals[i] != 0:
            pending = int(signals[i])

    metrics = calc_metrics(trades)
    metrics["killswitch_triggers"] = killswitch_days
    return metrics, trades


def _build_intraday_signal_schedule(minute_df: pl.DataFrame, df_15m: pl.DataFrame, signals: np.ndarray, variant: Variant) -> dict[int, dict]:
    minute_times = minute_df["ts_et"].to_list()
    time_to_idx = {ts: idx for idx, ts in enumerate(minute_times)}
    schedule = {}
    for i, signal in enumerate(signals):
        if signal == 0:
            continue
        entry_time = df_15m["timestamp"][i] + timedelta(minutes=15)
        entry_idx = time_to_idx.get(entry_time)
        if entry_idx is None:
            continue
        if minute_times[entry_idx].date() != entry_time.date():
            continue
        hhmm = int(minute_df["hhmm"][entry_idx])
        if hhmm >= LAST_ENTRY_HHMM:
            continue
        if variant.morning_only and hhmm > 1130:
            continue
        schedule[entry_idx] = {
            "direction": int(signal),
            "signal_bar": i,
        }
    return schedule


def _build_bar_minute_map(minute_df: pl.DataFrame, df_15m: pl.DataFrame) -> tuple[dict[int, int], dict[int, int], dict[int, int]]:
    minute_times = minute_df["ts_et"].to_list()
    minute_to_bar: dict[int, int] = {}
    close_idx_to_bar: dict[int, int] = {}
    next_open_idx_by_bar: dict[int, int] = {}
    cursor = 0
    for bar_idx in range(len(df_15m)):
        start = df_15m["timestamp"][bar_idx]
        end = start + timedelta(minutes=15)
        while cursor < len(minute_times) and minute_times[cursor] < start:
            cursor += 1
        probe = cursor
        last_idx = None
        while probe < len(minute_times) and minute_times[probe] < end:
            idx = probe
            minute_to_bar[idx] = bar_idx
            last_idx = idx
            probe += 1
        if last_idx is not None:
            close_idx_to_bar[last_idx] = bar_idx
        next_open_time = end
        if probe < len(minute_times) and minute_times[probe] == next_open_time:
            next_open_idx_by_bar[bar_idx] = probe
    return minute_to_bar, close_idx_to_bar, next_open_idx_by_bar


def _run_variant_nt_accurate(minute_df: pl.DataFrame, variant: Variant, session_classifications: dict[str, str] | None = None) -> tuple[dict, list[Trade]]:
    df_15m = resample_15m_session(minute_df, end_hhmm=1645)
    signals = build_signals(df_15m, enable_atr_filter=variant.atr_filter, atr_threshold_mult=variant.atr_threshold_mult)
    minute_to_bar, close_idx_to_bar, next_open_idx_by_bar = _build_bar_minute_map(minute_df, df_15m)

    minute_times = minute_df["ts_et"].to_list()
    minute_opens = minute_df["open"].to_numpy()
    minute_highs = minute_df["high"].to_numpy()
    minute_lows = minute_df["low"].to_numpy()
    minute_closes = minute_df["close"].to_numpy()
    minute_hhmm = minute_df["hhmm"].to_numpy()
    minute_dates = minute_df["date_et"].to_list()

    rsi_params = HYBRID_V2["RSI"]
    stop_points = float(rsi_params["sl_pts"])
    target_points = float(rsi_params["tp_pts"])
    max_hold_bars = int(rsi_params["hold"])
    max_hold_minutes = int(rsi_params["hold"] * 15)
    contracts = variant.contracts

    trades: list[Trade] = []
    position: IntradayPositionState | None = None
    pending_entry: dict | None = None
    daily_realized = 0.0
    trading_halted = False
    killswitch_days = 0
    current_date = None

    for idx, ts in enumerate(minute_times):
        if current_date != minute_dates[idx]:
            current_date = minute_dates[idx]
            daily_realized = 0.0
            trading_halted = False
            pending_entry = None
            if position is not None and str(position.entry_time)[:10] != str(current_date):
                position = None

        if pending_entry is not None and position is None and not trading_halted and idx == pending_entry["entry_idx"]:
            direction = pending_entry["direction"]
            entry_px = minute_opens[idx] + direction * SLIP_PTS
            target_px = entry_px + direction * target_points
            position = IntradayPositionState(
                direction=direction,
                entry_px=entry_px,
                entry_time=ts,
                entry_bar=pending_entry["signal_bar"],
                entry_minute_idx=idx,
                stop_px=entry_px - direction * stop_points,
                target_px=target_px,
                contracts=contracts,
            )
            pending_entry = None

        if position is None:
            pass
        else:
            current_bar_idx = minute_to_bar.get(idx, position.entry_bar)
            if idx <= position.entry_minute_idx or current_bar_idx == position.entry_bar:
                goto_signal_processing = True
            else:
                goto_signal_processing = False

            if not goto_signal_processing:
                if position.pending_be:
                    position.stop_px = position.entry_px
                    position.be_applied = True
                    position.pending_be = False

                exit_px = None
                reason = ""
                bars_held = current_bar_idx - position.entry_bar

                if int(minute_hhmm[idx]) >= FLATTEN_HHMM:
                    exit_px = minute_closes[idx] - position.direction * SLIP_PTS
                    reason = "time_exit"
                else:
                    long_side = position.direction == 1
                    hit_stop = minute_lows[idx] <= position.stop_px if long_side else minute_highs[idx] >= position.stop_px
                    hit_target = minute_highs[idx] >= position.target_px if long_side else minute_lows[idx] <= position.target_px
                    if hit_stop:
                        exit_px = position.stop_px - position.direction * SLIP_PTS
                        if position.be_applied and abs(position.stop_px - position.entry_px) < 1e-12:
                            reason = "break_even"
                        elif position.trail_level > 0:
                            reason = "trailing_stop"
                        else:
                            reason = "stop_loss"
                    elif hit_target:
                        exit_px = position.target_px - position.direction * SLIP_PTS
                        reason = "take_profit"

                    if exit_px is None and ts >= position.entry_time + timedelta(minutes=max_hold_minutes):
                        exit_bar_idx = min(position.entry_bar + max_hold_bars, len(df_15m) - 1)
                        exit_px = df_15m["close"][exit_bar_idx] - position.direction * SLIP_PTS
                        reason = "max_hold"

                if exit_px is not None:
                    trade = _build_trade(position.direction, position.entry_px, exit_px, position.contracts, position.entry_time, ts, bars_held, reason)
                    trades.append(trade)
                    daily_realized += trade.net_pnl
                    position = None
                    if variant.killswitch and not trading_halted and daily_realized <= variant.killswitch_dollar:
                        trading_halted = True
                        killswitch_days += 1
                else:
                    favorable = minute_highs[idx] - position.entry_px if position.direction == 1 else position.entry_px - minute_lows[idx]
                    position.favorable_max = max(position.favorable_max, favorable)

                    if variant.break_even and not position.be_applied and ts >= position.entry_time + timedelta(minutes=variant.be_minutes):
                        position.pending_be = True

                    if variant.trailing_stop and position.be_applied:
                        if position.favorable_max >= 75.0 and position.trail_level < 3:
                            new_stop = position.entry_px + 50.0 if position.direction == 1 else position.entry_px - 50.0
                            position.stop_px = max(position.stop_px, new_stop) if position.direction == 1 else min(position.stop_px, new_stop)
                            position.trail_level = 3
                        elif position.favorable_max >= 50.0 and position.trail_level < 2:
                            new_stop = position.entry_px + 30.0 if position.direction == 1 else position.entry_px - 30.0
                            position.stop_px = max(position.stop_px, new_stop) if position.direction == 1 else min(position.stop_px, new_stop)
                            position.trail_level = 2
                        elif position.favorable_max >= 30.0 and position.trail_level < 1:
                            new_stop = position.entry_px + 15.0 if position.direction == 1 else position.entry_px - 15.0
                            position.stop_px = max(position.stop_px, new_stop) if position.direction == 1 else min(position.stop_px, new_stop)
                            position.trail_level = 1

        if idx in close_idx_to_bar and not trading_halted:
            bar_idx = close_idx_to_bar[idx]
            signal = signals[bar_idx]
            if signal != 0 and position is None:
                next_idx = next_open_idx_by_bar.get(bar_idx)
                if next_idx is not None:
                    next_hhmm = int(minute_hhmm[next_idx])
                    if next_hhmm < LAST_ENTRY_HHMM and (not variant.morning_only or next_hhmm <= 1130):
                        pending_entry = {
                            "entry_idx": next_idx,
                            "direction": int(signal),
                            "signal_bar": bar_idx,
                        }

    metrics = calc_metrics(trades)
    metrics["killswitch_triggers"] = killswitch_days
    return metrics, trades


def run_variant(minute_df: pl.DataFrame, variant: Variant, session_classifications: dict[str, str] | None = None) -> tuple[dict, list[Trade]]:
    if variant.trailing_stop:
        return _run_variant_nt_accurate(minute_df, variant, session_classifications=session_classifications)
    return _run_variant_legacy(minute_df, variant, session_classifications=session_classifications)


def print_params() -> None:
    rsi_params = HYBRID_V2["RSI"]
    print("Validator params:")
    print(f"  RSI period: {RSI_PERIOD}")
    print(f"  Oversold: {OVERSOLD}")
    print(f"  Overbought: {OVERBOUGHT}")
    print(f"  SL points: {rsi_params['sl_pts']}")
    print(f"  TP points: {rsi_params['tp_pts']}")
    print(f"  Max hold bars: {rsi_params['hold']}")
    print(f"  Contracts: {CONTRACTS_PER_STRATEGY}")
    print()


def print_comparison(results: dict[str, dict]) -> None:
    headers = (
        "variant",
        "total_pnl",
        "monthly_avg",
        "max_drawdown",
        "trade_count",
        "worst_day",
        "killswitch_triggers",
    )

    rows = []
    for variant in VARIANTS:
        metrics = results[variant.name]
        rows.append((
            variant.name,
            f"${metrics['pnl']:,.2f}",
            f"${metrics['monthly_avg']:,.2f}",
            f"${metrics['max_dd']:,.2f}",
            str(metrics['n']),
            f"${metrics['worst_day']:,.2f}",
            str(metrics['killswitch_triggers']),
        ))

    widths = [len(header) for header in headers]
    for row in rows:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))

    print("  ".join(header.ljust(widths[i]) for i, header in enumerate(headers)))
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        print("  ".join(value.ljust(widths[i]) for i, value in enumerate(row)))


def export_trades(results: dict[str, tuple[dict, list[Trade]]], export_dir: Path) -> None:
    export_dir.mkdir(parents=True, exist_ok=True)
    for variant in VARIANTS:
        metrics, trades = results[variant.name]
        exported_trades = []
        for trade in trades:
            row = asdict(trade)
            row["pnl_total"] = float(trade.net_pnl) / CONTRACTS_PER_STRATEGY
            exported_trades.append(row)
        payload = {
            "variant": variant.name,
            "summary": metrics,
            "trades": exported_trades,
        }
        output_path = export_dir / f"{variant.name}_trades.json"
        output_path.write_text(json.dumps(payload, indent=2, default=str))
        print(f"Exported trades: {output_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate standalone RSI Phase 1 features")
    parser.add_argument("--data", action="append", default=[], help="Parquet file, directory, or glob. Can be repeated.")
    parser.add_argument("--start-date", default=None, help="Optional ET session date lower bound (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=None, help="Optional ET session date upper bound (YYYY-MM-DD)")
    parser.add_argument("--export-trades", action="store_true", help="Write per-variant trade JSON files to reports/backtests")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_inputs = args.data or [str(Path("data/processed/MNQ/1m"))]
    parquet_files = discover_parquet_files(data_inputs)
    minute_df = load_parquet_files(parquet_files)

    if args.start_date:
        minute_df = minute_df.filter(pl.col("date_et") >= pl.lit(args.start_date).str.to_date())
    if args.end_date:
        minute_df = minute_df.filter(pl.col("date_et") <= pl.lit(args.end_date).str.to_date())

    result_bundle: dict[str, tuple[dict, list[Trade]]] = {}
    for variant in VARIANTS:
        metrics, trades = run_variant(minute_df, variant)
        result_bundle[variant.name] = (metrics, trades)

    results = {name: pair[0] for name, pair in result_bundle.items()}

    if args.export_trades:
        export_trades(result_bundle, Path("reports/backtests"))

    print_params()
    print_comparison(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
