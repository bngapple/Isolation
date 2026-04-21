# Project Status Summary

Last updated: 2026-04-20

## Current architecture

There are currently two distinct runtime paths in the repo:

1. **Rsi4060Standalone.cs**
   - Self-contained NinjaTrader strategy
   - Core logic:
     - RSI(4)
     - 40/60 thresholds
     - 10 point stop
     - 100 point target
     - break-even
     - killswitch
   - This is the strategy path that currently looks most trustworthy

2. **PythonBridge.cs + Python strategy_executor.py**
   - NinjaTrader sends bars/minute updates to Python
   - Python decides LONG / SHORT / FLAT / HOLD / MOVE_STOP
   - This path is operationally working on PC, but is not yet trusted as a profitable production path

## What is working

### GitHub / deployment packaging
- `Executable_PC_Bundle/` exists as the main Windows deployment/update folder
- `pc_bundle/` also exists, but `Executable_PC_Bundle/` is the preferred deployment path
- `CSV Seeds/` exists and contains exported standalone seed CSVs for future intelligence work

### Windows PC runtime
- The Python bridge has been started successfully on the Windows PC
- Port `5001` was confirmed listening
- NinjaTrader was confirmed connected to the bridge
- Python logs showed incoming `bar` and `minute` messages and returned actions such as `HOLD` and `MOVE_STOP`
- The current live-connected account observed in logs was `Sim101`

### Standalone strategy research
- The RSI-only standalone strategy continues to backtest positively in Python over the full available range
- Full-range test used data from `2020-01-02` to `2026-04-13`
- Example results already produced:
  - 25K / 3 contracts: positive
  - 25K / 5 contracts: positive
  - 25K / 6 contracts: positive, but more aggressive on drawdown
  - 150K / 6 contracts: positive
  - 150K / 15 contracts: positive
  - 150K / 18 contracts: positive

### Stress testing
- A realism stress matrix was run on the standalone strategy with:
  - worse slippage
  - extra stop-fill penalty
  - higher fees
  - harsh combined assumptions
- Result: standalone still held up surprisingly well under harsher assumptions

## What is not trusted yet

### Old V3 / legacy trailing-stop backtests
- These are **not trusted** as proof of real edge
- Reasons include:
  - legacy engine mismatch
  - trailing-stop modeling optimism
  - confirmed short-side trailing bug
  - same-bar / bar-path ambiguity concerns

### Current Python-executed strategy performance
- The current `PythonBridge + strategy_executor.py` logic performs much worse than the standalone strategy in Python-side testing
- On matched comparison runs, the Python-executed path overtraded badly and produced substantially worse results than the standalone strategy
- Conclusion: the Python-executed implementation is currently the most likely source of performance divergence, not the standalone idea itself

## Key findings so far

1. **The standalone RSI strategy still appears viable**
   - It remains the strongest candidate for live trading

2. **The Python-executed strategy is not yet production-trustworthy**
   - It works operationally as a bridge/runtime
   - It does not yet match the standalone strategy behavior closely enough

3. **NinjaTrader Strategy Analyzer results should not be ignored, but they are not yet fully reconciled**
   - Bad NT results versus good standalone Python results indicate there is still an unresolved mismatch in assumptions, setup, or execution semantics

4. **The cleanest practical deployment path right now is likely:**
   - trade `Rsi4060Standalone` live
   - run `PythonBridge` on sim / research only

## Latest code/package changes

### `Rsi4060Standalone.cs`
- Was adjusted so `SubmitEntry()` uses fixed `Contracts` only
- Bridge-dependent sizing was removed from the standalone strategy
- This makes the standalone file a much closer match to the standalone backtests

### `Executable_PC_Bundle/`
- Added as the main deployment/update bundle for the Windows PC
- Contains:
  - `python_app/`
  - `ninjatrader/`
  - `UPDATE_AND_RUN.bat`
  - setup docs
- Intended to be the single folder used for future PC updates

## Seed data exported

Top-level repo folder:
- `CSV Seeds/`

Current seed exports include:
- `standalone_25k_6c_daily_summary_2020_2026.csv`
- `standalone_25k_6c_trade_detail_2020_2026.csv`

These are intended as seed inputs for future day classification / intelligence work.

## Recommended next move

### Live trading
- Use **Rsi4060Standalone** for real funded/live trading
- Use a conservative contract size unless higher size is explicitly accepted
- Treat `PythonBridge` as sim/research only for now

### Research
- Use standalone trade/day exports as the basis for a future intelligence layer
- Build day classification on top of the standalone strategy, not the current Python-executed runtime

## Bottom line

If forced to choose today:

- **Trust more:** `Rsi4060Standalone`
- **Trust less:** current `PythonBridge + strategy_executor.py`

The project is operationally usable, but the Python-executed architecture is still experimental. The standalone RSI strategy is currently the best-supported and most defensible path.
