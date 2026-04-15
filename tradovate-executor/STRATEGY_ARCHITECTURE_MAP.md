# Strategy Architecture Map

This file distinguishes three different systems that have been mixed together during analysis.

## 1. Current Live Runtime On The PC

Source path:

- `berjquant-windows-local-python-bridge-live/tradovate-executor/`

Core files:

- `app.py`
- `signal_engine.py`
- `market_data.py`
- `ninjatrader_bridge.py`
- `config.json`

Behavior:

- runs `RSI`, `IB`, and `MOM` as separate strategy modules
- each strategy can generate its own entry signal
- each strategy is tracked with its own local Python position state
- Python can queue multiple strategy trades on the same account
- execution is routed to NinjaTrader over TCP

Configured sizing:

- `RSI`: `1` contract
- `IB`: `1` contract
- `MOM`: `1` contract

Reality:

- this is **not** a weighted combined strategy
- this is **not** the original strong backtest model
- this is **not** a clean one-account execution model

Main problem:

- one futures account is netted by symbol, but the Python app behaves like the three strategies can hold independent positions in the same symbol

## 2. Original Winning Backtest Model

Source path:

- root backtest scripts in the original repo

Main source:

- `run_htf_swing_v3_hybrid_v2.py`

Supporting source:

- `run_htf_swing.py`

Behavior:

- runs `RSI`, `IB`, and `MOM` independently
- combines all strategy trades into one portfolio result
- allows overlapping independent positions per strategy
- uses original HTF signal logic from the old research stack
- uses original cost/slippage assumptions

Original sizing:

- `3 MNQ` per strategy
- up to `9 MNQ` total

Reality:

- this is the model that produced the strong historical results
- this is **not directly one-account live compatible** without changing risk/sizing/account logic

## 3. Proposed Corrected Live System

Target path:

- `Isolation/tradovate-executor/`

Required behavior:

- one real account-level execution engine
- one net position at a time per symbol
- Lucid-correct risk model
- live behavior must match backtest assumptions

Two valid designs:

### Option A: one account-level ensemble

- `RSI`, `IB`, and `MOM` become signal contributors
- a single combiner decides `long`, `short`, or `flat`
- one shared stop/target/max-hold policy per live trade

### Option B: reduced-size original parity logic with strict account handling

- preserve more of the original `Hybrid v2` signal behavior
- reduce size to something Lucid-safe
- likely begin around `1 MNQ per strategy` equivalent risk
- still needs one coherent live account/risk model

## Short Summary

### Current live system

- separate `RSI` + `IB` + `MOM`
- `1` contract each
- not weighted combined
- structurally wrong for one account

### Original winning backtest

- separate `RSI` + `IB` + `MOM`
- `3` contracts each
- overlapping positions allowed
- produced the big historical numbers

### Corrected future live system

- must be one-account compatible
- must match backtests
- should either be:
  - a weighted combined ensemble
  - or a reduced-size port of the original logic with account-level enforcement
