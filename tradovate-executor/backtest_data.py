"""
Databento/parquet data helpers for the live-aligned backtest path.

This module standardizes historical OHLCV input into a single 1-minute schema
that can be resampled or simulated against by the backtest engine.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Sequence

import polars as pl


REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "tick_count"]


def standardize_ohlcv(df: pl.DataFrame) -> pl.DataFrame:
    """Normalize historical OHLCV data into a single schema."""
    rename_map: dict[str, str] = {}
    for col in df.columns:
        lower = col.lower()
        if col == "timestamp":
            continue
        if "ts_event" in lower or lower == "ts_recv" or lower == "datetime":
            rename_map[col] = "timestamp"
        elif lower == "open":
            rename_map[col] = "open"
        elif lower == "high":
            rename_map[col] = "high"
        elif lower == "low":
            rename_map[col] = "low"
        elif lower == "close":
            rename_map[col] = "close"
        elif lower == "volume":
            rename_map[col] = "volume"
        elif lower in ("count", "tick_count"):
            rename_map[col] = "tick_count"

    if rename_map:
        df = df.rename(rename_map)

    if "timestamp" not in df.columns:
        raise ValueError("Historical data must include a timestamp column")

    if "tick_count" not in df.columns:
        df = df.with_columns(pl.lit(0).cast(pl.Int64).alias("tick_count"))

    missing = [c for c in ("open", "high", "low", "close", "volume") if c not in df.columns]
    if missing:
        raise ValueError(f"Historical data is missing required columns: {missing}")

    timestamp_dtype = df.schema["timestamp"]
    if timestamp_dtype == pl.Utf8:
        df = df.with_columns(pl.col("timestamp").str.to_datetime())

    timestamp_dtype = df.schema["timestamp"]
    time_zone = getattr(timestamp_dtype, "time_zone", None)
    if time_zone:
        df = df.with_columns(pl.col("timestamp").dt.convert_time_zone("UTC"))
    else:
        # Databento parquet in the old repo was stored as naive UTC.
        df = df.with_columns(pl.col("timestamp").dt.replace_time_zone("UTC"))

    df = df.with_columns([
        pl.col("timestamp").cast(pl.Datetime("us", "UTC")),
        pl.col("open").cast(pl.Float64),
        pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64),
        pl.col("close").cast(pl.Float64),
        pl.col("volume").cast(pl.Int64),
        pl.col("tick_count").cast(pl.Int64),
    ])

    return (
        df.select(REQUIRED_COLUMNS)
        .drop_nulls(subset=["timestamp", "open", "high", "low", "close"])
        .filter(pl.col("close") > 0)
        .sort("timestamp")
        .unique(subset=["timestamp"], keep="first")
    )


def add_session_columns(df: pl.DataFrame) -> pl.DataFrame:
    """Add ET-local columns used by the live-aligned backtester."""
    df = df.with_columns(pl.col("timestamp").dt.convert_time_zone("US/Eastern").alias("ts_et"))
    df = df.with_columns([
        pl.col("ts_et").dt.date().alias("date_et"),
        pl.col("ts_et").dt.hour().cast(pl.Int32).alias("hour_et"),
        pl.col("ts_et").dt.minute().cast(pl.Int32).alias("minute_et"),
    ])
    return df.with_columns((pl.col("hour_et") * 100 + pl.col("minute_et")).alias("hhmm"))


def filter_session(df: pl.DataFrame, start_hhmm: int = 930, end_hhmm: int = 1645) -> pl.DataFrame:
    """Keep the session range needed by the current live executor."""
    return df.filter((pl.col("hhmm") >= start_hhmm) & (pl.col("hhmm") <= end_hhmm))


def load_parquet_files(paths: Sequence[str | Path], session_only: bool = True) -> pl.DataFrame:
    """Load, normalize, and combine one or more parquet files."""
    frames: list[pl.DataFrame] = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists():
            raise FileNotFoundError(path)
        frames.append(standardize_ohlcv(pl.read_parquet(path)))

    if not frames:
        raise ValueError("No parquet files were provided")

    combined = pl.concat(frames, how="vertical_relaxed")
    combined = standardize_ohlcv(combined)
    combined = add_session_columns(combined)
    if session_only:
        combined = filter_session(combined)
    return combined


def discover_parquet_files(inputs: Sequence[str]) -> list[str]:
    """Expand file paths, directories, and globs into a sorted file list."""
    discovered: list[str] = []
    for value in inputs:
        path = Path(value)
        if path.is_file():
            discovered.append(str(path))
            continue
        if path.is_dir():
            discovered.extend(str(p) for p in sorted(path.glob("*.parquet")))
            continue

        parent = path.parent if str(path.parent) != "" else Path.cwd()
        discovered.extend(str(p) for p in sorted(parent.glob(path.name)))

    unique = sorted(dict.fromkeys(discovered))
    if not unique:
        raise FileNotFoundError(f"No parquet files found for inputs: {list(inputs)}")
    return unique


def save_parquet(df: pl.DataFrame, path: str | Path) -> str:
    """Write normalized historical data to parquet."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(target)
    return str(target)


def download_databento_ohlcv_1m(
    start: str,
    end: str,
    symbols: Iterable[str] | None = None,
    dataset: str = "GLBX.MDP3",
    api_key: str | None = None,
) -> tuple[pl.DataFrame, str]:
    """Download 1-minute OHLCV from Databento using a symbol fallback list."""
    try:
        import databento as db
    except ImportError as exc:
        raise RuntimeError("databento is not installed") from exc

    resolved_key = api_key or os.getenv("DATABENTO_API_KEY")
    if not resolved_key:
        raise RuntimeError("DATABENTO_API_KEY is not set")

    client = db.Historical(resolved_key)
    if symbols:
        candidates = []
        for symbol in symbols:
            if ".c." in symbol or ".n." in symbol:
                candidates.append((symbol, "continuous"))
            elif symbol.endswith(".FUT") or symbol.endswith(".SPOT") or symbol.endswith(".OPT"):
                candidates.append((symbol, "parent"))
            else:
                candidates.append((symbol, None))
    else:
        candidates = [
            ("MNQ.c.0", "continuous"),
            ("MNQ.n.0", "continuous"),
            ("NQ.c.0", "continuous"),
            ("MNQ.FUT", "parent"),
            ("NQ.FUT", "parent"),
        ]
    last_error: Exception | None = None

    for symbol, stype_in in candidates:
        try:
            kwargs = {
                "dataset": dataset,
                "symbols": [symbol],
                "schema": "ohlcv-1m",
                "start": start,
                "end": end,
            }
            if stype_in:
                kwargs["stype_in"] = stype_in
            data = client.timeseries.get_range(**kwargs)
            df = data.to_df()
            if len(df) == 0:
                last_error = RuntimeError(f"No rows returned for symbol {symbol} stype_in={stype_in}")
                continue
            return standardize_ohlcv(pl.from_pandas(df.reset_index())), symbol
        except Exception as exc:  # pragma: no cover - network/API surface
            last_error = exc

    raise RuntimeError(f"Databento download failed for symbols {candidates}: {last_error}")
