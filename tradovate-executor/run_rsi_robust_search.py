"""Bounded RSI robustness search with train/test splits and Lucid scoring."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, date
from pathlib import Path

import numpy as np

from backtest_data import discover_parquet_files, load_parquet_files
from model_lucid import evaluate_funded_path, evaluate_lucid_path, run_monte_carlo_eval
from run_hybrid_v2_parity import backtest, calc_atr, calc_rsi, resample_15m_session


@dataclass(frozen=True)
class Candidate:
    period: int
    oversold: int
    overbought: int
    vwap_mode: str
    vwap_atr_mult: float
    atr_regime: str
    time_filter: str


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


def time_filter_ok(hhmm: int, mode: str) -> bool:
    if mode == "all":
        return True
    if mode == "open":
        return 930 <= hhmm <= 1130
    if mode == "morning":
        return 930 <= hhmm <= 1300
    raise ValueError(f"Unknown time filter mode: {mode}")


def build_signals(df, candidate: Candidate) -> np.ndarray:
    closes = df["close"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    hhmm = df["hhmm"].to_numpy()
    rsi = calc_rsi(closes, candidate.period)
    atr = calc_atr(highs, lows, closes, 14)
    atr_med = rolling_median(atr, 50)
    vwap = calc_vwap(df)

    sigs = np.zeros(len(df), dtype=np.int8)
    for i in range(len(df)):
        if np.isnan(rsi[i]) or not time_filter_ok(int(hhmm[i]), candidate.time_filter):
            continue

        direction = 0
        if rsi[i] < candidate.oversold:
            direction = 1
        elif rsi[i] > candidate.overbought:
            direction = -1
        if direction == 0:
            continue

        if candidate.vwap_mode == "side":
            if direction == 1 and not closes[i] < vwap[i]:
                continue
            if direction == -1 and not closes[i] > vwap[i]:
                continue
        elif candidate.vwap_mode == "distance":
            if np.isnan(atr[i]) or atr[i] <= 0:
                continue
            if direction == 1 and not closes[i] < vwap[i]:
                continue
            if direction == -1 and not closes[i] > vwap[i]:
                continue
            if abs(closes[i] - vwap[i]) < candidate.vwap_atr_mult * atr[i]:
                continue

        if candidate.atr_regime == "high":
            if np.isnan(atr[i]) or np.isnan(atr_med[i]) or not atr[i] > atr_med[i]:
                continue

        sigs[i] = direction
    return sigs


def summarize_trades(trades) -> dict:
    monthly = defaultdict(float)
    daily = defaultdict(float)
    for t in trades:
        pnl = float(t.net_pnl)
        monthly[str(t.entry_time)[:7]] += pnl
        daily[str(t.entry_time)[:10]] += pnl
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
        "daily_values": [daily[k] for k in sorted(daily)],
    }


def score_candidate(train_summary: dict, test_summary: dict, lucid_25k: dict, lucid_150k: dict) -> float:
    score = 0.0
    score += test_summary["monthly_avg"]
    score += train_summary["monthly_avg"] * 0.25
    score += min(0.0, test_summary["max_drawdown"]) * 0.15
    score += 500.0 if lucid_25k["direct"]["passed"] else -500.0
    score += 500.0 if lucid_150k["direct"]["passed"] else -500.0
    score += 300.0 if lucid_150k["funded"]["survived"] else -300.0
    return score


def run_backtest_for_candidate(df, candidate: Candidate):
    sigs = build_signals(df, candidate)
    opens = df["open"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    timestamps = df["timestamp"].to_list()
    hhmm = df["hhmm"].to_numpy()
    trades = backtest(
        opens,
        highs,
        lows,
        closes,
        timestamps,
        hhmm,
        sigs,
        sl_ticks=int(10 / 0.25),
        tp_ticks=int(100 / 0.25),
        max_hold=5,
        contracts=3,
        strategy_name="RSI_SEARCH",
        flatten_time=1645,
    )
    for t in trades:
        t.contracts = 1
        t.net_pnl = float(t.net_pnl) / 3.0
    return trades


def candidate_space() -> list[Candidate]:
    space = []
    for period in (4, 5, 6, 7):
        for oversold, overbought in ((30, 70), (35, 65), (40, 60)):
            for vwap_mode, vwap_atr_mult in (("none", 0.0), ("side", 0.0), ("distance", 0.25), ("distance", 0.5)):
                for atr_regime in ("none", "high"):
                    for time_filter in ("all", "morning", "open"):
                        space.append(Candidate(period, oversold, overbought, vwap_mode, vwap_atr_mult, atr_regime, time_filter))
    return space


def run_search(minute_df, train_end: str, test_start: str, top_n_mc: int = 20) -> dict:
    df_15m = resample_15m_session(minute_df, end_hhmm=1645)
    train_df = df_15m.filter(df_15m["date_et"] <= datetime.fromisoformat(train_end).date())
    test_df = df_15m.filter(df_15m["date_et"] >= datetime.fromisoformat(test_start).date())

    rows = []
    for candidate in candidate_space():
        train_trades = run_backtest_for_candidate(train_df, candidate)
        test_trades = run_backtest_for_candidate(test_df, candidate)
        train_summary = summarize_trades(train_trades)
        test_summary = summarize_trades(test_trades)

        lucid_25k = {
            "direct": evaluate_lucid_path(test_summary["daily_values"], -1000.0, 1250.0, 50.0).__dict__,
            "funded": evaluate_funded_path(test_summary["daily_values"], -1000.0).__dict__,
        }
        two_mnq_test = [v * 2.0 for v in test_summary["daily_values"]]
        lucid_150k = {
            "direct": evaluate_lucid_path(two_mnq_test, -4500.0, 9000.0, 50.0).__dict__,
            "funded": evaluate_funded_path(two_mnq_test, -4500.0).__dict__,
        }

        row = {
            "candidate": asdict(candidate),
            "train": {k: v for k, v in train_summary.items() if k != "daily_values"},
            "test": {k: v for k, v in test_summary.items() if k != "daily_values"},
            "lucid_25k_test": lucid_25k,
            "lucid_150k_2mnq_test": lucid_150k,
            "score": score_candidate(train_summary, test_summary, lucid_25k, lucid_150k),
            "_daily_test": test_summary["daily_values"],
        }
        rows.append(row)

    rows.sort(key=lambda r: r["score"], reverse=True)

    for row in rows[:top_n_mc]:
        row["lucid_25k_test"]["mc"] = asdict(run_monte_carlo_eval(row["_daily_test"], -1000.0, 1250.0, 50.0, 2000, 1.0))
        row["lucid_150k_2mnq_test"]["mc"] = asdict(run_monte_carlo_eval([v * 2.0 for v in row["_daily_test"]], -4500.0, 9000.0, 50.0, 2000, 1.0))

    for row in rows:
        row.pop("_daily_test", None)

    return {
        "train_end": train_end,
        "test_start": test_start,
        "candidate_count": len(rows),
        "top_candidates": rows[:50],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run bounded RSI robustness search")
    parser.add_argument("--data", action="append", default=[])
    parser.add_argument("--train-end", default="2023-12-31")
    parser.add_argument("--test-start", default="2024-01-01")
    parser.add_argument("--output", default="reports/backtests/rsi_robust_search.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_inputs = args.data or [str(Path("data/processed/MNQ/1m"))]
    parquet_files = discover_parquet_files(data_inputs)
    minute_df = load_parquet_files(parquet_files)
    result = run_search(minute_df, args.train_end, args.test_start)
    result["data_sources"] = parquet_files

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, default=str))

    print(f"Output: {output_path}")
    for idx, row in enumerate(result["top_candidates"][:10], start=1):
        cand = row["candidate"]
        print(
            f"{idx}. score={row['score']:.2f} period={cand['period']} os={cand['oversold']} ob={cand['overbought']} "
            f"vwap={cand['vwap_mode']} atr_regime={cand['atr_regime']} time={cand['time_filter']} "
            f"test_monthly=${row['test']['monthly_avg']:,.2f} 150k_pass={row['lucid_150k_2mnq_test']['direct']['passed']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
