"""CLI entrypoint for live-aligned historical backtests."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from backtest_data import (
    discover_parquet_files,
    download_databento_ohlcv_1m,
    load_parquet_files,
    save_parquet,
)
from backtest_engine import StrategyBacktester, write_result_files
from config import AppConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a live-aligned single-strategy backtest")
    parser.add_argument("--config", default="config.json", help="Path to config.json")
    parser.add_argument("--strategy", required=True, choices=["RSI", "IB", "MOM"], help="Strategy to backtest")
    parser.add_argument("--data", action="append", default=[], help="Parquet file, directory, or glob. Can be repeated.")
    parser.add_argument("--download-databento", action="store_true", help="Download 1m OHLCV from Databento before backtesting")
    parser.add_argument("--download-only", action="store_true", help="Download parquet and exit")
    parser.add_argument("--start", default="2018-01-01", help="Databento start date/time")
    parser.add_argument("--end", default="2026-12-31", help="Databento end date/time")
    parser.add_argument("--symbol", action="append", default=[], help="Databento symbol candidate. Can be repeated.")
    parser.add_argument(
        "--output-data",
        default="data/processed/MNQ/1m/mnq_8yr_databento.parquet",
        help="Where to store downloaded parquet data",
    )
    parser.add_argument(
        "--reports-dir",
        default="reports/backtests",
        help="Directory for backtest CSV and JSON outputs",
    )
    parser.add_argument(
        "--slippage-points",
        type=float,
        default=0.0,
        help="Adverse slippage in points applied on entry and exit",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
    logging.getLogger("market_data").setLevel(logging.WARNING)
    logging.getLogger("signal_engine").setLevel(logging.WARNING)

    args = parse_args()
    config = AppConfig.load(args.config)
    data_sources = list(args.data)

    if args.download_databento:
        df, used_symbol = download_databento_ohlcv_1m(
            start=args.start,
            end=args.end,
            symbols=args.symbol or None,
        )
        output_path = save_parquet(df, args.output_data)
        logging.info("Downloaded Databento data using %s -> %s", used_symbol, output_path)
        data_sources = [output_path]
        if args.download_only:
            return 0

    if not data_sources:
        default_path = Path("data/processed/MNQ/1m")
        data_sources = [str(default_path)]

    parquet_files = discover_parquet_files(data_sources)
    minute_df = load_parquet_files(parquet_files)

    backtester = StrategyBacktester(
        config=config,
        strategy=args.strategy,
        slippage_points=args.slippage_points,
    )
    result = backtester.run(minute_df, data_sources=parquet_files)
    result.config_path = str(Path(args.config).resolve())
    csv_path, json_path = write_result_files(result, args.reports_dir)

    summary = result.summary
    print(f"Strategy: {summary.strategy}")
    print(f"Data range: {summary.data_start} -> {summary.data_end}")
    print(f"Trades: {summary.trades}")
    print(f"Win rate: {summary.win_rate:.2f}%")
    print(f"Total P&L: ${summary.total_pnl:,.2f}")
    print(f"Avg monthly P&L: ${summary.avg_monthly_pnl:,.2f}")
    print(f"Worst month: ${summary.worst_month:,.2f}")
    print(f"Worst day: ${summary.worst_day:,.2f}")
    print(f"Max drawdown: ${summary.max_drawdown:,.2f}")
    print(f"CSV: {csv_path}")
    print(f"JSON: {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
