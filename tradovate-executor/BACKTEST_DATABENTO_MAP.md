# Databento Backtest Map

This document maps the old Databento-backed backtest code, what is reusable, what is incompatible with the current live executor, and what must be added to this repo so we can run real backtests.

## Short Answer

The original codebase already had a Databento research and backtest stack.

It lived in root-level scripts such as:

- `run_always_on_databento.py`
- `run_htf_swing.py`
- `run_htf_swing_v3.py`
- `run_htf_swing_v3_hybrid_v2.py`
- `run_htf_swing_8yr.py`

The current live executor in `tradovate-executor/` does not use that stack.

So we are not missing the idea of Databento support. We are missing a clean integration into the current live-oriented system.

## What Exists In The Old Databento Stack

### 1. Databento historical download path

File:

- `run_always_on_databento.py`

What it does:

- imports `databento as db`
- calls `db.Historical(api_key)`
- requests CME/Globex data from `GLBX.MDP3`
- requests schema `ohlcv-1m`
- tries symbols such as:
  - `MNQ.c.0`
  - `MNQ.FUT`
  - `MNQ` with `stype_in="continuous"`
  - `NQ.c.0`
- normalizes the result into a consistent parquet-friendly OHLCV format

### 2. Historical parquet layout

Documented in:

- `ANTHONY/README.md`

Expected historical files:

- `data/processed/MNQ/1m/full_2yr.parquet`
- `data/processed/MNQ/1m/databento_extended.parquet`
- `data/processed/MNQ/1m/databento_8yr_ext.parquet`

Expected 1m schema:

- `timestamp`
- `open`
- `high`
- `low`
- `close`
- `volume`
- `tick_count`

### 3. Resampling and session conversion

Base file:

- `run_htf_swing.py`

Reusable behaviors already implemented there:

- read parquet with Polars
- convert timestamps from UTC to `US/Eastern`
- derive `date_et` and `hhmm`
- resample 1m bars into 15m, 1h, and 4h bars

### 4. Backtest engine

Base file:

- `run_htf_swing.py`

Current behavior of the old backtest engine:

- signals are generated on completed bars
- entries are executed on next bar open
- no same-bar exits on the entry bar
- stop is checked before target in a bar
- max hold exits at the current bar close
- slippage and round-trip costs are applied

### 5. Strategy-specific HTF backtests

Files:

- `run_htf_swing_v3.py`
- `run_htf_swing_v3_hybrid_v2.py`
- `run_htf_swing_8yr.py`

Important note:

- `run_htf_swing_v3_hybrid_v2.py` is the closest parameter match to the current live executor for `RSI`, `IB`, and `MOM`

## What The Old Databento Stack Gets Right

These are the pieces we can reuse conceptually, and in some cases directly:

- Databento historical download and normalization approach
- parquet-based local market-data storage
- UTC to ET conversion
- 1m to 15m resampling
- basic next-bar-open entry model
- basic stop/target/max-hold simulator structure
- strategy-by-strategy reporting

## What Does Not Match The Current Live Executor

This is the critical part.

### 1. The old stack is separate from the current live executor

- old backtests live in root `run_*.py` scripts
- current live trading lives in `tradovate-executor/`

They are not one shared engine today.

### 2. Old portfolio logic assumes parallel independent strategies

Examples:

- `run_htf_swing_v3.py`
- `run_htf_swing_v3_hybrid_v2.py`
- `run_htf_swing_8yr.py`

Those scripts run `RSI`, `IB`, and `MOM` independently and then combine all trades.

That does not match a single live MNQ account, which can only have one net position by symbol.

### 3. Old IB breakout logic does not match current live IB logic

Current live executor in `signal_engine.py`:

- long if `bar.close > ib.high`
- short if `bar.close < ib.low`

Old HTF backtest in `run_htf_swing.py::sig_ib_breakout()`:

- long if `highs[i] > ib_h`
- short if `lows[i] < ib_l`

That is a real strategy mismatch.

If we backtest the old IB rule, we are not backtesting what the live app currently trades.

### 4. Old session handling does not match current live session handling

Current live executor config:

- new entries allowed until `16:30 ET`
- flatten at `16:45 ET`

Old HTF backtest loader in `run_htf_swing.py`:

- filters data to `9:30-16:00 ET` only

This means the old backtest stack removes the exact period needed to simulate:

- 16:00-16:30 late-session entries
- 16:45 EOD flatten behavior

So even where the strategy rules look similar, the session model is not aligned.

### 5. Old sizing and params are not automatically the live params

Current live config in `tradovate-executor/config.json`:

- `RSI`: 1 contract, 5/35/65, SL 10, TP 100, hold 5
- `IB`: 1 contract, SL 10, TP 120, hold 15
- `MOM`: 1 contract, SL 15, TP 100, hold 5

The old general HTF scripts use many other parameter sets.

The closest old match is in:

- `run_htf_swing_v3_hybrid_v2.py`

But even there:

- contracts default to 3
- portfolio combination still assumes independent strategies
- IB trigger logic still differs from live

### 6. The old historical files referenced by the scripts are not actually present now

Expected old files:

- `full_2yr.parquet`
- `databento_extended.parquet`
- `databento_8yr_ext.parquet`

What is actually present in the original workspace right now:

- `data/processed/MNQ/1m/extended_history.parquet`

So the old Databento backtest scripts are not currently runnable as-is with the files they expect.

### 7. The old scripts depend on packages not currently declared in this isolated runtime

The migrated live runtime `requirements.txt` does not include:

- `polars`
- `databento`
- `python-dotenv`

Those will need to be added if we bring Databento-backed backtesting into this repo.

## What We Should Reuse

These parts are worth carrying forward into this repo:

### Reuse directly or adapt heavily

- Databento download logic from `run_always_on_databento.py`
- historical normalization rules from `run_always_on_databento.py`
- parquet schema used by the old HTF scripts
- UTC to ET conversion and bar resampling ideas from `run_htf_swing.py`

### Reuse conceptually, but not as final source of truth

- cost/slippage model ideas from `run_htf_swing.py`
- trade reporting ideas from the old scripts
- old HTF parameter references, especially `run_htf_swing_v3_hybrid_v2.py`

## What Must Be Rewritten Or Corrected

These are the pieces we should not just copy blindly.

### 1. Strategy signal logic must come from the live engine

The backtester should use the same core logic as:

- `market_data.py`
- `indicators.py`
- `signal_engine.py`

That avoids drift between live and backtest.

### 2. Backtests must run one strategy at a time first

Before any multi-strategy mode is trusted, we need:

- `RSI` solo backtest
- `IB` solo backtest
- `MOM` solo backtest

Each one must match live assumptions.

### 3. Session handling must match the live config

The backtest data pipeline must preserve enough time coverage to simulate:

- session start at `09:30 ET`
- no new entries after `16:30 ET`
- flatten at `16:45 ET`

So we should not hard-filter to `16:00 ET` the way the old scripts do.

### 4. Account model must match reality

The backtester cannot treat one single MNQ account as if it can hold independent opposing strategy positions simultaneously.

Short-term rule:

- one strategy active per backtest run

Long-term options:

- one strategy per account
- or one account-level merged net-position model

### 5. Execution simulation must match the live engine

The backtester must explicitly model:

- signal on bar close
- entry at next bar open
- stop-loss before take-profit within the bar, if both are touched
- max-hold exit timing
- late-session entry cutoff
- 16:45 flatten behavior
- slippage and costs

## What We Need To Add To This Repo

To make `Isolation/tradovate-executor` actually backtestable with Databento, we need at least these new pieces:

### Data layer

- a Databento historical downloader module
- local parquet storage path and schema contract
- a loader that reads parquet and builds 1m or 15m data consistently

### Backtest layer

- a dedicated backtest runner inside this repo
- a bar simulator that follows the current live execution rules
- per-strategy runs for `RSI`, `IB`, and `MOM`

### Shared strategy layer

- either direct reuse of `signal_engine.py`
- or a thin adapter around it so the backtest and live paths use the same signal definitions

### Reporting layer

- backtest trade log output that can be compared to `trade_logger.py`
- run summaries by strategy, month, and day

### Dependency changes

- add `polars`
- add `databento`
- add `python-dotenv` if we keep `.env`-based API key loading

## Recommended Build Order

### Phase 1: data foundation

1. add Databento dependencies
2. add a historical download script/module
3. standardize parquet schema and local storage path

### Phase 2: single-strategy backtest engine

1. build a 15m backtest runner for the current live rules
2. make `RSI` runnable by itself
3. make `IB` runnable by itself
4. make `MOM` runnable by itself

### Phase 3: parity checks

1. compare backtest assumptions against current live code paths
2. align session windows and exit timing
3. align output logs with live trade logs where possible

### Phase 4: multi-strategy design, only after parity exists

1. decide one-account merged execution model or separate-account model
2. only then add multi-strategy portfolio testing back on top

## Bottom Line

The old repo already solved the historical market-data sourcing problem with Databento.

What it did not solve for this current system is:

- shared live/backtest strategy logic
- live-aligned session handling
- single-account position realism
- clean integration into `tradovate-executor`

So the right move is not to port the old backtest scripts wholesale.

The right move is to reuse the Databento data pipeline ideas, then build a backtest path inside this repo that matches the current live strategy engine and current live account constraints.
