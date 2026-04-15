"""Original HTF Swing Hybrid v2 parity runner.

This script intentionally preserves the original research assumptions:
- 3 MNQ per strategy
- up to 9 MNQ total across RSI, IB, and MOM
- independent overlapping strategy positions
- old 15m RTH(9:30-16:00) signal definitions from run_htf_swing.py
- old honest backtest engine and old Lucid Monte Carlo shortcut

Use this only to reproduce the old research numbers. It is not a live-compatible
one-account execution model.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import polars as pl

from backtest_data import discover_parquet_files, load_parquet_files


TICK_SIZE = 0.25
POINT_VALUE = 2.0
SLIP_TICKS = 2
COMM_PER_SIDE = 0.62
EXCH_PER_SIDE = 0.27
SLIP_PTS = SLIP_TICKS * TICK_SIZE

DAILY_LIMIT = -3000.0
MLL = -4500.0
EVAL_TARGET = 9000.0
FLATTEN_TIME = 1645
CONTRACTS_PER_STRATEGY = 3

HYBRID_V2 = {
    "RSI": {"period": 5, "ob": 65, "os": 35, "sl_pts": 10, "tp_pts": 100, "hold": 5},
    "IB": {"ib_filter": True, "sl_pts": 10, "tp_pts": 120, "hold": 15},
    "MOM": {"atr_mult": 1.0, "vol_mult": 1.0, "sl_pts": 15, "tp_pts": 100, "hold": 5},
}


@dataclass
class Trade:
    direction: int
    entry_px: float
    exit_px: float
    contracts: int
    net_pnl: float
    entry_time: str
    exit_time: str
    bars_held: int
    reason: str
    strategy: str


def rt_cost(contracts: int) -> float:
    return (COMM_PER_SIDE + EXCH_PER_SIDE) * 2 * contracts + SLIP_TICKS * TICK_SIZE * POINT_VALUE * 2 * contracts


def resample_15m_rth(minute_df: pl.DataFrame) -> pl.DataFrame:
    rth = minute_df.filter((pl.col("hhmm") >= 930) & (pl.col("hhmm") < 1600))
    return (
        rth.group_by_dynamic("ts_et", every="15m")
        .agg([
            pl.col("open").first().alias("open"),
            pl.col("high").max().alias("high"),
            pl.col("low").min().alias("low"),
            pl.col("close").last().alias("close"),
            pl.col("volume").sum().alias("volume"),
            pl.col("date_et").last().alias("date_et"),
            pl.col("hhmm").last().alias("hhmm"),
        ])
        .filter(pl.col("open").is_not_null())
        .sort("ts_et")
        .rename({"ts_et": "timestamp"})
    )


def calc_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(highs)
    tr = np.zeros(n)
    if n == 0:
        return np.array([])
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atr = np.full(n, np.nan)
    if n >= period:
        atr[period - 1] = np.mean(tr[:period])
        for i in range(period, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def calc_ema(data: np.ndarray, period: int) -> np.ndarray:
    n = len(data)
    ema = np.full(n, np.nan)
    if n < period:
        return ema
    ema[period - 1] = np.mean(data[:period])
    k = 2 / (period + 1)
    for i in range(period, n):
        ema[i] = data[i] * k + ema[i - 1] * (1 - k)
    return ema


def calc_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(closes)
    rsi = np.full(n, np.nan)
    if n < period + 1:
        return rsi
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g = np.mean(gains[:period])
    avg_l = np.mean(losses[:period])
    rsi[period] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    for i in range(period, n - 1):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        rsi[i + 1] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return rsi


def sig_rsi_extreme(df: pl.DataFrame, period: int, ob: float, os_: float) -> np.ndarray:
    closes = df["close"].to_numpy()
    rsi = calc_rsi(closes, period)
    sigs = np.zeros(len(closes), dtype=np.int8)
    for i in range(len(closes)):
        if np.isnan(rsi[i]):
            continue
        if rsi[i] < os_:
            sigs[i] = 1
        elif rsi[i] > ob:
            sigs[i] = -1
    return sigs


def sig_ib_breakout(df: pl.DataFrame, ib_range_filter: bool) -> np.ndarray:
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    hhmm = df["hhmm"].to_numpy()
    dates = df["date_et"].to_list()
    n = len(df)

    ib_data: dict[object, dict[str, float]] = {}
    for i in range(n):
        if hhmm[i] < 1000:
            day = dates[i]
            if day not in ib_data:
                ib_data[day] = {"high": highs[i], "low": lows[i]}
            else:
                ib_data[day]["high"] = max(ib_data[day]["high"], highs[i])
                ib_data[day]["low"] = min(ib_data[day]["low"], lows[i])

    ib_dates = sorted(ib_data.keys())
    ib_ranges = [ib_data[day]["high"] - ib_data[day]["low"] for day in ib_dates]
    traded: dict[object, bool] = {}
    sigs = np.zeros(n, dtype=np.int8)

    for i in range(n):
        if hhmm[i] < 1000 or hhmm[i] >= 1530:
            continue
        day = dates[i]
        if day not in ib_data or day in traded:
            continue

        ib_h = ib_data[day]["high"]
        ib_l = ib_data[day]["low"]
        ib_r = ib_h - ib_l
        if ib_r < 2.0:
            continue

        if ib_range_filter and len(ib_ranges) > 20:
            idx = ib_dates.index(day) if day in ib_dates else -1
            if idx > 20:
                recent = ib_ranges[max(0, idx - 50):idx]
                p25, p75 = np.percentile(recent, 25), np.percentile(recent, 75)
                if ib_r < p25 or ib_r > p75:
                    continue

        if highs[i] > ib_h:
            sigs[i] = 1
            traded[day] = True
        elif lows[i] < ib_l:
            sigs[i] = -1
            traded[day] = True

    return sigs


def sig_momentum_bar(df: pl.DataFrame, atr_mult: float, vol_mult: float) -> np.ndarray:
    closes = df["close"].to_numpy()
    opens = df["open"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    volumes = df["volume"].to_numpy().astype(float)
    atr = calc_atr(highs, lows, closes, 14)
    ema21 = calc_ema(closes, 21)
    avg_vol = np.full(len(closes), np.nan)
    for i in range(20, len(closes)):
        avg_vol[i] = np.mean(volumes[i - 20:i])

    sigs = np.zeros(len(closes), dtype=np.int8)
    for i in range(1, len(closes)):
        if np.isnan(atr[i]) or np.isnan(ema21[i]) or np.isnan(avg_vol[i]):
            continue
        bar_range = highs[i] - lows[i]
        if bar_range < atr[i] * atr_mult:
            continue
        if volumes[i] < avg_vol[i] * vol_mult:
            continue

        bar_dir = 1 if closes[i] > opens[i] else -1
        trend_dir = 1 if closes[i] > ema21[i] else -1
        if bar_dir == trend_dir:
            sigs[i] = bar_dir
    return sigs


def backtest(
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    timestamps: list,
    hhmm_arr: np.ndarray,
    signals: np.ndarray,
    sl_ticks: int,
    tp_ticks: int,
    max_hold: int,
    contracts: int,
    strategy_name: str,
    flatten_time: int = 1545,
) -> list[Trade]:
    n = len(opens)
    cost = rt_cost(contracts)
    trades: list[Trade] = []
    in_pos = False
    direction = 0
    entry_px = 0.0
    entry_bar = 0
    stop_px = 0.0
    target_px = 0.0
    pending = 0

    for i in range(n):
        h = int(hhmm_arr[i]) if i < len(hhmm_arr) else 0

        if in_pos and h >= flatten_time and i > entry_bar:
            ex = closes[i] - direction * SLIP_PTS
            raw = (ex - entry_px) * direction * POINT_VALUE * contracts
            trades.append(Trade(direction, entry_px, ex, contracts, raw - cost, str(timestamps[entry_bar]), str(timestamps[i]), i - entry_bar, "time_exit", strategy_name))
            in_pos = False
            pending = 0
            continue

        if pending != 0 and not in_pos:
            if h >= flatten_time - 15:
                pending = 0
            else:
                entry_px = opens[i] + int(pending) * SLIP_PTS
                direction = int(pending)
                entry_bar = i
                stop_px = entry_px - direction * sl_ticks * TICK_SIZE
                target_px = entry_px + direction * tp_ticks * TICK_SIZE
                in_pos = True
                pending = 0

        if in_pos and i > entry_bar:
            bh = i - entry_bar
            ex = None
            reason = ""

            if direction == 1 and lows[i] <= stop_px:
                ex = stop_px
                reason = "stop_loss"
            elif direction == -1 and highs[i] >= stop_px:
                ex = stop_px
                reason = "stop_loss"

            if ex is None:
                if direction == 1 and highs[i] >= target_px:
                    ex = target_px
                    reason = "take_profit"
                elif direction == -1 and lows[i] <= target_px:
                    ex = target_px
                    reason = "take_profit"

            if ex is None and bh >= max_hold:
                ex = closes[i]
                reason = "max_hold"

            if ex is not None:
                ex -= direction * SLIP_PTS
                raw = (ex - entry_px) * direction * POINT_VALUE * contracts
                trades.append(Trade(direction, entry_px, ex, contracts, raw - cost, str(timestamps[entry_bar]), str(timestamps[i]), bh, reason, strategy_name))
                in_pos = False

        if not in_pos and i < len(signals) and signals[i] != 0:
            pending = int(signals[i])

    return trades


def calc_metrics(trades: list[Trade]) -> dict:
    if not trades:
        return {
            "pnl": 0.0,
            "n": 0,
            "wr": 0.0,
            "monthly_avg": 0.0,
            "worst_month": 0.0,
            "best_month": 0.0,
            "worst_day": 0.0,
            "best_day": 0.0,
            "max_dd": 0.0,
            "bars_mean": 0.0,
            "monthly": {},
            "n_months": 0,
            "months_pos": 0,
            "trades_per_day": 0.0,
        }

    pnls = [t.net_pnl for t in trades]
    bars = [t.bars_held for t in trades]
    monthly = defaultdict(float)
    daily = defaultdict(float)
    for t in trades:
        monthly[t.entry_time[:7]] += t.net_pnl
        daily[t.entry_time[:10]] += t.net_pnl

    cum = 0.0
    peak = 0.0
    mdd = 0.0
    for key in sorted(daily):
        cum += daily[key]
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)

    nm = max(len(monthly), 1)
    nd = max(len(daily), 1)
    return {
        "pnl": sum(pnls),
        "n": len(trades),
        "wr": sum(1 for p in pnls if p > 0) / len(trades) * 100.0,
        "monthly_avg": sum(pnls) / nm,
        "worst_month": min(monthly.values()) if monthly else 0.0,
        "best_month": max(monthly.values()) if monthly else 0.0,
        "worst_day": min(daily.values()) if daily else 0.0,
        "best_day": max(daily.values()) if daily else 0.0,
        "max_dd": mdd,
        "bars_mean": float(np.mean(bars)) if bars else 0.0,
        "monthly": dict(monthly),
        "n_months": nm,
        "months_pos": sum(1 for value in monthly.values() if value > 0),
        "trades_per_day": len(trades) / nd,
    }


def run_mc(trades: list[Trade], n_sims: int = 5000, pnl_mult: float = 1.0) -> dict:
    daily = defaultdict(list)
    for t in trades:
        daily[t.entry_time[:10]].append(t.net_pnl * pnl_mult)
    days = list(daily.values())
    nd = len(days)
    if nd == 0:
        return {"pass_rate": 0.0, "blowup": 1.0, "med_days": 0, "p95_days": 0}

    passed = 0
    blown = 0
    days_to_pass: list[int] = []
    for sim in range(n_sims):
        rng = np.random.RandomState(sim)
        order = rng.permutation(nd)
        cum = 0.0
        peak = 0.0
        did_pass = False
        ok = True
        day_count = 0
        for idx in order:
            dp = sum(days[idx])
            if dp < DAILY_LIMIT:
                dp = DAILY_LIMIT
            cum += dp
            day_count += 1
            peak = max(peak, cum)
            if cum - peak <= MLL:
                ok = False
                break
            if not did_pass and cum >= EVAL_TARGET:
                did_pass = True
                days_to_pass.append(day_count)
        if not ok:
            blown += 1
        if did_pass and ok:
            passed += 1

    arr = np.array(days_to_pass) if days_to_pass else np.array([0])
    return {
        "pass_rate": passed / n_sims,
        "blowup": blown / n_sims,
        "med_days": int(np.median(arr)) if len(arr) else 0,
        "p95_days": int(np.percentile(arr, 95)) if len(arr) else 0,
    }


def run_hybrid_v2_parity(minute_df: pl.DataFrame) -> dict:
    df_15m = resample_15m_rth(minute_df)
    opens = df_15m["open"].to_numpy()
    highs = df_15m["high"].to_numpy()
    lows = df_15m["low"].to_numpy()
    closes = df_15m["close"].to_numpy()
    timestamps = df_15m["timestamp"].to_list()
    hhmm = df_15m["hhmm"].to_numpy()

    trades_by_strategy: dict[str, list[Trade]] = {}

    rsi = HYBRID_V2["RSI"]
    rsi_sigs = sig_rsi_extreme(df_15m, rsi["period"], rsi["ob"], rsi["os"])
    trades_by_strategy["RSI"] = backtest(opens, highs, lows, closes, timestamps, hhmm, rsi_sigs, int(rsi["sl_pts"] / TICK_SIZE), int(rsi["tp_pts"] / TICK_SIZE), rsi["hold"], CONTRACTS_PER_STRATEGY, "RSI", FLATTEN_TIME)

    ib = HYBRID_V2["IB"]
    ib_sigs = sig_ib_breakout(df_15m, ib["ib_filter"])
    trades_by_strategy["IB"] = backtest(opens, highs, lows, closes, timestamps, hhmm, ib_sigs, int(ib["sl_pts"] / TICK_SIZE), int(ib["tp_pts"] / TICK_SIZE), ib["hold"], CONTRACTS_PER_STRATEGY, "IB", FLATTEN_TIME)

    mom = HYBRID_V2["MOM"]
    mom_sigs = sig_momentum_bar(df_15m, mom["atr_mult"], mom["vol_mult"])
    trades_by_strategy["MOM"] = backtest(opens, highs, lows, closes, timestamps, hhmm, mom_sigs, int(mom["sl_pts"] / TICK_SIZE), int(mom["tp_pts"] / TICK_SIZE), mom["hold"], CONTRACTS_PER_STRATEGY, "MOM", FLATTEN_TIME)

    all_trades: list[Trade] = []
    for strategy_trades in trades_by_strategy.values():
        all_trades.extend(strategy_trades)
    all_trades.sort(key=lambda t: (t.entry_time, t.strategy, t.exit_time))

    return {
        "config": {
            "name": "Hybrid v2 parity",
            "contracts_per_strategy": CONTRACTS_PER_STRATEGY,
            "max_total_contracts": CONTRACTS_PER_STRATEGY * 3,
            "flatten_time": FLATTEN_TIME,
            "daily_limit": DAILY_LIMIT,
            "monthly_loss_limit": MLL,
            "eval_target": EVAL_TARGET,
            "rth_session": "09:30-16:00 ET",
            "assumption": "independent overlapping strategy positions",
            "params": HYBRID_V2,
        },
        "data": {
            "start": str(df_15m["timestamp"].min()),
            "end": str(df_15m["timestamp"].max()),
            "bars_15m": len(df_15m),
        },
        "aggregate": calc_metrics(all_trades),
        "per_strategy": {name: calc_metrics(trades) for name, trades in trades_by_strategy.items()},
        "mc": {
            "baseline": run_mc(all_trades, 5000, 1.0),
            "conservative": run_mc(all_trades, 5000, 0.70),
        },
        "trades": [asdict(t) for t in all_trades],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run original Hybrid v2 parity backtest")
    parser.add_argument("--data", action="append", default=[], help="Parquet file, directory, or glob. Can repeat.")
    parser.add_argument("--output", default="reports/backtests/hybrid_v2_parity.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_inputs = args.data or [str(Path("data/processed/MNQ/1m"))]
    parquet_files = discover_parquet_files(data_inputs)
    minute_df = load_parquet_files(parquet_files)
    result = run_hybrid_v2_parity(minute_df)
    result["data_sources"] = parquet_files

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, default=str))

    agg = result["aggregate"]
    print(f"Output: {output_path}")
    print(f"15m bars: {result['data']['bars_15m']}")
    print(f"Trades: {agg['n']}")
    print(f"Monthly avg: ${agg['monthly_avg']:,.2f}")
    print(f"Total pnl: ${agg['pnl']:,.2f}")
    print(f"Worst month: ${agg['worst_month']:,.2f}")
    print(f"Worst day: ${agg['worst_day']:,.2f}")
    print(f"Max drawdown: ${agg['max_dd']:,.2f}")
    print(f"MC pass: {result['mc']['baseline']['pass_rate']:.2%}")
    print(f"MC blowup: {result['mc']['baseline']['blowup']:.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
