from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from backtest_data import discover_parquet_files, load_parquet_files
from run_hybrid_v2_parity import HYBRID_V2, POINT_VALUE, SLIP_PTS, calc_metrics, calc_rsi, resample_15m_session, rt_cost
from run_standalone_validate import build_signals


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "isolation-governor" / "isolation.db"
REPORTS_DIR = ROOT / "reports" / "backtests"
START_DATE = "2020-01-01"
END_DATE = "2026-04-13"
LAST_ENTRY_HHMM = 1630
FLATTEN_HHMM = 1645
RSI_PERIOD = 4
OVERSOLD = 40.0
OVERBOUGHT = 60.0


@dataclass(frozen=True)
class Experiment:
    name: str
    morning_only: bool = False
    atr_loose: bool = False
    skip_low_vol: bool = False
    event_skip_minutes: int = 0
    partial_tp: bool = False
    partial_profit_points: float = 50.0
    partial_fraction: float = 0.5
    long_only: bool = False
    short_only: bool = False
    be_minutes: int = 5
    disable_be: bool = False
    start_hhmm: int = 930
    end_hhmm: int = 1630
    allowed_hhmm: tuple[int, ...] = ()
    exclude_weekdays: tuple[int, ...] = ()


@dataclass
class Position:
    direction: int
    entry_px: float
    entry_time: object
    entry_minute_idx: int
    signal_bar_idx: int
    stop_px: float
    target_px: float
    contracts: int
    be_applied: bool = False
    partial_taken: bool = False
    remaining_contracts: int = 0
    realized_pnl: float = 0.0


EXPERIMENTS = {
    "strict_baseline": Experiment("strict_baseline"),
    "morning_only": Experiment("morning_only", morning_only=True),
    "atr_loose": Experiment("atr_loose", atr_loose=True),
    "not_low_vol": Experiment("not_low_vol", skip_low_vol=True),
    "event_skip": Experiment("event_skip", event_skip_minutes=30),
    "partial_tp": Experiment("partial_tp", partial_tp=True),
    "combo": Experiment("combo", morning_only=True, atr_loose=True, event_skip_minutes=30, skip_low_vol=True),
    "long_only": Experiment("long_only", long_only=True),
    "short_only": Experiment("short_only", short_only=True),
    "long_morning": Experiment("long_morning", long_only=True, morning_only=True),
    "long_combo": Experiment("long_combo", long_only=True, morning_only=True, atr_loose=True, event_skip_minutes=30, skip_low_vol=True),
    "no_be": Experiment("no_be", disable_be=True),
    "be_10": Experiment("be_10", be_minutes=10),
    "be_15": Experiment("be_15", be_minutes=15),
    "long_open_only": Experiment("long_open_only", long_only=True, allowed_hhmm=(945,)),
    "long_1145_only": Experiment("long_1145_only", long_only=True, allowed_hhmm=(1145,)),
    "long_open_1145": Experiment("long_open_1145", long_only=True, allowed_hhmm=(945, 1145)),
    "long_combo_no_tue_wed": Experiment("long_combo_no_tue_wed", long_only=True, morning_only=True, atr_loose=True, event_skip_minutes=30, skip_low_vol=True, exclude_weekdays=(1,2)),
    "long_only_no_tue_wed": Experiment("long_only_no_tue_wed", long_only=True, exclude_weekdays=(1,2)),
}


def load_data():
    files = discover_parquet_files([str(ROOT / "data" / "processed" / "MNQ" / "1m")])
    minute_df = load_parquet_files(files)
    minute_df = minute_df.filter(pl.col("date_et") >= pl.lit(START_DATE).str.to_date())
    minute_df = minute_df.filter(pl.col("date_et") <= pl.lit(END_DATE).str.to_date())
    bars_15m = resample_15m_session(minute_df, end_hhmm=1645)
    return minute_df, bars_15m


def load_session_classes() -> dict[str, str]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT session_date, volatility_class FROM session_summaries").fetchall()
    conn.close()
    return {row["session_date"]: row["volatility_class"] for row in rows}


def load_event_windows(skip_minutes: int) -> dict[str, list[tuple[datetime, datetime]]]:
    if skip_minutes <= 0:
        return {}
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT event_datetime
        FROM economic_events
        WHERE currency = 'USD' AND impact = 'High'
          AND date(event_datetime) BETWEEN ? AND ?
        ORDER BY event_datetime
        """,
        (START_DATE, END_DATE),
    ).fetchall()
    conn.close()
    out: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    for row in rows:
        ts = datetime.fromisoformat(row["event_datetime"])
        day = ts.date().isoformat()
        out[day].append((ts - timedelta(minutes=skip_minutes), ts + timedelta(minutes=skip_minutes)))
    return out


def build_schedule(minute_df: pl.DataFrame, bars_15m: pl.DataFrame, experiment: Experiment, session_classes: dict[str, str], event_windows: dict[str, list[tuple[datetime, datetime]]]):
    signals = build_signals(bars_15m, enable_atr_filter=experiment.atr_loose, atr_threshold_mult=0.75 if experiment.atr_loose else 1.0)
    minute_times = minute_df["ts_et"].to_list()
    time_to_idx = {ts: idx for idx, ts in enumerate(minute_times)}
    schedule = {}
    for i, signal in enumerate(signals):
        if signal == 0:
            continue
        entry_time = bars_15m["timestamp"][i] + timedelta(minutes=15)
        entry_idx = time_to_idx.get(entry_time)
        if entry_idx is None:
            continue
        day = entry_time.date().isoformat()
        hhmm = int(minute_df["hhmm"][entry_idx])
        if experiment.allowed_hhmm and hhmm not in experiment.allowed_hhmm:
            continue
        if hhmm >= min(LAST_ENTRY_HHMM, experiment.end_hhmm):
            continue
        if experiment.morning_only and hhmm > 1130:
            continue
        if hhmm < experiment.start_hhmm or hhmm > experiment.end_hhmm:
            continue
        if experiment.long_only and int(signal) < 0:
            continue
        if experiment.short_only and int(signal) > 0:
            continue
        if experiment.skip_low_vol and session_classes.get(day) == "low":
            continue
        if experiment.exclude_weekdays and entry_time.weekday() in experiment.exclude_weekdays:
            continue
        if experiment.event_skip_minutes > 0:
            blocked = False
            for start, end in event_windows.get(day, []):
                if start <= entry_time <= end:
                    blocked = True
                    break
            if blocked:
                continue
        schedule[entry_idx] = {"direction": int(signal), "signal_bar_idx": i}
    return schedule


def close_trade(position: Position, exit_px: float, exit_time, bars_held: int, reason: str, contracts: int):
    pnl = ((exit_px - position.entry_px) * position.direction * POINT_VALUE * contracts) - rt_cost(contracts)
    return {
        "entry_time": str(position.entry_time),
        "exit_time": str(exit_time),
        "direction": position.direction,
        "contracts": contracts,
        "entry_px": position.entry_px,
        "exit_px": exit_px,
        "reason": reason,
        "net_pnl": pnl,
    }


def run_experiment(experiment: Experiment, contracts: int, killswitch: float):
    minute_df, bars_15m = load_data()
    session_classes = load_session_classes()
    event_windows = load_event_windows(experiment.event_skip_minutes)
    schedule = build_schedule(minute_df, bars_15m, experiment, session_classes, event_windows)

    minute_times = minute_df["ts_et"].to_list()
    opens = minute_df["open"].to_numpy()
    highs = minute_df["high"].to_numpy()
    lows = minute_df["low"].to_numpy()
    closes = minute_df["close"].to_numpy()
    hhmm = minute_df["hhmm"].to_numpy()
    dates = minute_df["date_et"].to_list()

    stop_points = float(HYBRID_V2["RSI"]["sl_pts"])
    target_points = float(HYBRID_V2["RSI"]["tp_pts"])
    max_hold_minutes = int(HYBRID_V2["RSI"]["hold"] * 15)

    trades = []
    position = None
    daily_realized = 0.0
    trading_halted = False
    killswitch_days = 0
    current_date = None

    unique_days = sorted(dict.fromkeys(str(d) for d in dates))
    day_seen = set()
    for idx, ts in enumerate(minute_times):
        day = str(dates[idx])
        if day != current_date:
            current_date = day
            daily_realized = 0.0
            trading_halted = False
            if day not in day_seen:
                day_seen.add(day)
                if len(day_seen) % 100 == 0:
                    print(f"[{experiment.name}] processed {len(day_seen)}/{len(unique_days)} days", flush=True)

        if position is None and not trading_halted and idx in schedule:
            direction = schedule[idx]["direction"]
            entry_px = opens[idx] + direction * SLIP_PTS
            position = Position(
                direction=direction,
                entry_px=entry_px,
                entry_time=ts,
                entry_minute_idx=idx,
                signal_bar_idx=schedule[idx]["signal_bar_idx"],
                stop_px=entry_px - direction * stop_points,
                target_px=entry_px + direction * target_points,
                contracts=contracts,
                remaining_contracts=contracts,
            )

        if position is None:
            continue
        if idx <= position.entry_minute_idx:
            continue

        bars_held = int((ts - position.entry_time).total_seconds() // 60 / 15)
        exit_px = None
        reason = ""
        exit_contracts = position.remaining_contracts

        if int(hhmm[idx]) >= FLATTEN_HHMM:
            exit_px = closes[idx] - position.direction * SLIP_PTS
            reason = "time_exit"
        elif position.direction == 1 and lows[idx] <= position.stop_px:
            exit_px = position.stop_px - position.direction * SLIP_PTS
            reason = "break_even" if position.be_applied and abs(position.stop_px - position.entry_px) < 1e-12 else "stop_loss"
        elif position.direction == -1 and highs[idx] >= position.stop_px:
            exit_px = position.stop_px - position.direction * SLIP_PTS
            reason = "break_even" if position.be_applied and abs(position.stop_px - position.entry_px) < 1e-12 else "stop_loss"
        elif position.direction == 1 and highs[idx] >= position.target_px:
            exit_px = position.target_px - position.direction * SLIP_PTS
            reason = "take_profit"
        elif position.direction == -1 and lows[idx] <= position.target_px:
            exit_px = position.target_px - position.direction * SLIP_PTS
            reason = "take_profit"
        elif ts >= position.entry_time + timedelta(minutes=max_hold_minutes):
            exit_px = closes[idx] - position.direction * SLIP_PTS
            reason = "max_hold"

        if exit_px is not None:
            trade = close_trade(position, exit_px, ts, bars_held, reason, exit_contracts)
            trades.append(trade)
            daily_realized += trade["net_pnl"]
            position = None
            if daily_realized <= killswitch and not trading_halted:
                trading_halted = True
                killswitch_days += 1
            continue

        favorable = highs[idx] - position.entry_px if position.direction == 1 else position.entry_px - lows[idx]

        if not experiment.disable_be and not position.be_applied and ts >= position.entry_time + timedelta(minutes=experiment.be_minutes):
            position.stop_px = position.entry_px
            position.be_applied = True

        if experiment.partial_tp and not position.partial_taken and favorable >= experiment.partial_profit_points and position.remaining_contracts > 1:
            partial_contracts = max(1, int(position.contracts * experiment.partial_fraction))
            partial_contracts = min(partial_contracts, position.remaining_contracts - 1)
            partial_exit_px = position.entry_px + position.direction * experiment.partial_profit_points - position.direction * SLIP_PTS
            trade = close_trade(position, partial_exit_px, ts, bars_held, "partial_tp", partial_contracts)
            trades.append(trade)
            daily_realized += trade["net_pnl"]
            position.remaining_contracts -= partial_contracts
            position.partial_taken = True
            if daily_realized <= killswitch and not trading_halted:
                trading_halted = True
                killswitch_days += 1

    metrics = calc_metrics([
        type("T", (), {
            "net_pnl": t["net_pnl"],
            "entry_time": t["entry_time"],
            "exit_time": t["exit_time"],
            "direction": t["direction"],
            "contracts": t["contracts"],
            "entry_px": t["entry_px"],
            "exit_px": t["exit_px"],
            "bars_held": 0,
            "reason": t["reason"],
            "strategy": experiment.name,
        })() for t in trades
    ])
    metrics["killswitch_triggers"] = killswitch_days
    return metrics, trades


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", choices=sorted(EXPERIMENTS), required=True)
    parser.add_argument("--account", choices=["25k", "150k"], required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.account == "25k":
        contracts = 6
        killswitch = -750.0
    else:
        contracts = 20
        killswitch = -3375.0
    metrics, _ = run_experiment(EXPERIMENTS[args.experiment], contracts, killswitch)
    print(json.dumps({
        "experiment": args.experiment,
        "account": args.account,
        "total_pnl": float(metrics["pnl"]),
        "monthly_avg": float(metrics["monthly_avg"]),
        "max_drawdown": float(metrics["max_dd"]),
        "worst_day": float(metrics["worst_day"]),
        "trade_count": int(metrics["n"]),
        "killswitch_triggers": int(metrics.get("killswitch_triggers", 0)),
    }))


if __name__ == "__main__":
    main()
