from __future__ import annotations

from pathlib import Path

import polars as pl

from backtest_data import discover_parquet_files, load_parquet_files
from run_standalone_validate import run_variant, VARIANTS


def run_day(date: str, parquet_dir: str) -> dict:
    parquet_files = discover_parquet_files([parquet_dir])
    minute_df = load_parquet_files(parquet_files)
    day_df = minute_df.filter(pl.col("date_et") == pl.lit(date).str.to_date())
    if len(day_df) == 0:
        return {"date": date, "total_pnl": 0.0, "trade_count": 0, "win_count": 0, "session_range": 0.0}

    metrics, trades = run_variant(day_df, VARIANTS[0])
    session_range = float(day_df["high"].max() - day_df["low"].min()) if len(day_df) else 0.0
    return {
        "date": date,
        "total_pnl": metrics["pnl"],
        "trade_count": metrics["n"],
        "win_count": sum(1 for t in trades if t.net_pnl > 0),
        "session_range": session_range,
    }
