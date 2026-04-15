# Backtest Usage

The backtest path in this repo is built to match the current live strategy rules as closely as possible.

## Current Scope

- one strategy at a time
- current live `RSI`, `IB`, or `MOM`
- next-bar-open entries
- stop/target monitoring using 1-minute historical bars
- max-hold exits
- 16:45 ET EOD flatten handling

## Install Dependencies

```powershell
pip install -r requirements.txt
```

## Download Databento History

Set `DATABENTO_API_KEY` in your environment, then run:

```powershell
python run_backtest.py --strategy RSI --download-databento --download-only --start 2018-01-01 --end 2026-12-31 --output-data data/processed/MNQ/1m/mnq_8yr_databento.parquet
```

You can repeat with explicit symbol candidates if needed:

```powershell
python run_backtest.py --strategy RSI --download-databento --download-only --symbol MNQ.c.0 --symbol NQ.c.0
```

## Run A Backtest From Local Parquet

```powershell
python run_backtest.py --strategy RSI --data data/processed/MNQ/1m/mnq_8yr_databento.parquet
```

Run the other live strategies individually:

```powershell
python run_backtest.py --strategy IB --data data/processed/MNQ/1m/mnq_8yr_databento.parquet
python run_backtest.py --strategy MOM --data data/processed/MNQ/1m/mnq_8yr_databento.parquet
```

## Run Across Multiple Parquet Files

If your 8-year history is partitioned, point the runner at a directory or multiple files:

```powershell
python run_backtest.py --strategy RSI --data data/processed/MNQ/1m/
python run_backtest.py --strategy RSI --data data/processed/MNQ/1m/2018.parquet --data data/processed/MNQ/1m/2019.parquet
```

## Outputs

Results are written under `reports/backtests/` by default:

- `backtest_<strategy>_<timestamp>.csv`
- `backtest_<strategy>_<timestamp>.json`

The CSV uses the same row format as the live `trade_logger.py` output so live-vs-backtest comparison is easier.

## Important Notes

- this backtester is intentionally single-strategy first because one MNQ account cannot hold independent overlapping per-strategy positions the way the old scripts assumed
- the old root-level HTF scripts are not the source of truth anymore for live parity
- if historical data does not include enough minutes after `16:00 ET`, the current live `16:30` entry cutoff and `16:45` flatten behavior cannot be reproduced correctly
