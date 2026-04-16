"""Focused RSI enhancement tests on the corrected intraday control window."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

from backtest_data import discover_parquet_files, load_parquet_files
from model_lucid import evaluate_funded_path, evaluate_lucid_path, run_monte_carlo_eval
from run_hybrid_v2_parity import (
    HYBRID_V2,
    backtest,
    calc_atr,
    calc_rsi,
    resample_15m_session,
)


def calc_vwap(df) -> np.ndarray:
    tp = (df["high"].to_numpy() + df["low"].to_numpy() + df["close"].to_numpy()) / 3.0
    vol = df["volume"].to_numpy().astype(float)
    dates = df["date_et"].to_list()
    vwap = np.full(len(df), np.nan)
    cum_tpv = 0.0
    cum_vol = 0.0
    prev_date = None
    for i in range(len(df)):
        if dates[i] != prev_date:
            cum_tpv = 0.0
            cum_vol = 0.0
            prev_date = dates[i]
        cum_tpv += tp[i] * vol[i]
        cum_vol += vol[i]
        vwap[i] = cum_tpv / cum_vol if cum_vol > 0 else tp[i]
    return vwap


def rolling_median(values: np.ndarray, window: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    for i in range(window - 1, len(values)):
        sample = values[i - window + 1:i + 1]
        sample = sample[~np.isnan(sample)]
        if len(sample):
            out[i] = float(np.median(sample))
    return out


def build_rsi_variant_signals(df, variant: str) -> np.ndarray:
    closes = df["close"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    params = HYBRID_V2["RSI"]
    rsi = calc_rsi(closes, params["period"])
    atr = calc_atr(highs, lows, closes, 14)
    vwap = calc_vwap(df)
    atr_med = rolling_median(atr, 50)

    sigs = np.zeros(len(df), dtype=np.int8)
    for i in range(len(df)):
        if np.isnan(rsi[i]):
            continue

        direction = 0
        if rsi[i] < params["os"]:
            direction = 1
        elif rsi[i] > params["ob"]:
            direction = -1
        if direction == 0:
            continue

        price = closes[i]
        side_ok = True
        stretch_ok = True
        regime_ok = True

        if variant in {"vwap_side", "vwap_atr_025", "vwap_atr_050", "vwap_side_atr_high", "vwap_atr_025_atr_high"}:
            if direction == 1:
                side_ok = price < vwap[i]
            else:
                side_ok = price > vwap[i]

        if variant in {"vwap_atr_025", "vwap_atr_050", "vwap_atr_025_atr_high"}:
            if np.isnan(atr[i]) or atr[i] <= 0:
                stretch_ok = False
            else:
                threshold = 0.25 if variant != "vwap_atr_050" else 0.50
                stretch_ok = abs(price - vwap[i]) >= threshold * atr[i]

        if variant in {"atr_high", "vwap_side_atr_high", "vwap_atr_025_atr_high"}:
            if np.isnan(atr[i]) or np.isnan(atr_med[i]):
                regime_ok = False
            else:
                regime_ok = atr[i] > atr_med[i]

        if side_ok and stretch_ok and regime_ok:
            sigs[i] = direction

    return sigs


def summarize_trades(trades) -> dict:
    monthly = defaultdict(float)
    daily = defaultdict(float)
    for t in trades:
        monthly[str(t.entry_time)[:7]] += float(t.net_pnl)
        daily[str(t.entry_time)[:10]] += float(t.net_pnl)
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for day in sorted(daily):
        cum += daily[day]
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)
    total = sum(float(t.net_pnl) for t in trades)
    return {
        "trades": len(trades),
        "total_pnl": total,
        "monthly_avg": total / max(len(monthly), 1),
        "max_drawdown": max_dd,
        "worst_month": min(monthly.values()) if monthly else 0.0,
        "best_month": max(monthly.values()) if monthly else 0.0,
        "daily": daily,
    }


def lucid_for_daily(daily_values: list[float], dd: float, target: float) -> dict:
    return {
        "direct": evaluate_lucid_path(daily_values, dd, target, 50.0).__dict__,
        "mc": run_monte_carlo_eval(daily_values, dd, target, 50.0, 5000, 1.0).__dict__,
        "funded": evaluate_funded_path(daily_values, dd).__dict__,
    }


def run_tests(minute_df, start_date: str | None = None) -> dict:
    if start_date:
        minute_df = minute_df.filter(minute_df["date_et"] >= datetime.fromisoformat(start_date).date())

    df_15m = resample_15m_session(minute_df, end_hhmm=1645)
    opens = df_15m["open"].to_numpy()
    highs = df_15m["high"].to_numpy()
    lows = df_15m["low"].to_numpy()
    closes = df_15m["close"].to_numpy()
    timestamps = df_15m["timestamp"].to_list()
    hhmm = df_15m["hhmm"].to_numpy()
    p = HYBRID_V2["RSI"]
    sl_ticks = int(p["sl_pts"] / 0.25)
    tp_ticks = int(p["tp_pts"] / 0.25)

    variants = [
        "baseline",
        "vwap_side",
        "atr_high",
        "vwap_side_atr_high",
        "vwap_atr_025",
        "vwap_atr_025_atr_high",
        "vwap_atr_050",
    ]

    results = {}
    for variant in variants:
        sigs = build_rsi_variant_signals(df_15m, variant if variant != "baseline" else "baseline")
        trades = backtest(
            opens, highs, lows, closes, timestamps, hhmm, sigs,
            sl_ticks, tp_ticks, p["hold"], 3, f"RSI_{variant}", 1645,
        )
        # Scale to 1 MNQ benchmark since parity runner used 3 contracts.
        for t in trades:
            t.contracts = 1
            t.net_pnl = float(t.net_pnl) / 3.0
        summary = summarize_trades(trades)
        daily_values = [summary["daily"][k] for k in sorted(summary["daily"])]
        results[variant] = {
            "summary": {k: v for k, v in summary.items() if k != "daily"},
            "lucid_25k_1mnq": lucid_for_daily(daily_values, -1000.0, 1250.0),
            "lucid_150k_2mnq": lucid_for_daily([v * 2.0 for v in daily_values], -4500.0, 9000.0),
        }
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run focused RSI enhancement tests")
    parser.add_argument("--data", action="append", default=[])
    parser.add_argument("--start-date", default="2020-01-01")
    parser.add_argument("--output", default="reports/backtests/rsi_enhancement_tests.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_inputs = args.data or [str(Path("data/processed/MNQ/1m"))]
    parquet_files = discover_parquet_files(data_inputs)
    minute_df = load_parquet_files(parquet_files)
    results = run_tests(minute_df, start_date=args.start_date)

    payload = {
        "window_start": args.start_date,
        "data_sources": parquet_files,
        "results": results,
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, default=str))

    print(f"Output: {output_path}")
    for name, result in results.items():
        summary = result["summary"]
        lucid_150 = result["lucid_150k_2mnq"]["direct"]
        print(
            f"{name}: pnl=${summary['total_pnl']:,.2f} monthly=${summary['monthly_avg']:,.2f} "
            f"dd=${summary['max_drawdown']:,.2f} 150k_pass={lucid_150['passed']} days={lucid_150['days_to_pass']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
