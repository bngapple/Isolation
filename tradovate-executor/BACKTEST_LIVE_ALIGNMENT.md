# Backtest And Live Alignment

This project must obey one core rule:

- if live trading cannot execute the same behavior that the backtest assumes, the backtest is not valid

## Current Constraint

For a single futures account trading one symbol like `MNQ`, the broker account is netted by symbol.

That means one account can only be:

- flat
- net long
- net short

It cannot hold separate independent positions for `RSI`, `IB`, and `MOM` at the same time on the same symbol.

## Immediate Implication

The current strategy-state model in `app.py` and `signal_engine.py` is not valid for a single live account when strategies disagree or overlap.

## Required Validation Path

Before any multi-strategy live design is trusted, each strategy must be testable and measurable by itself.

That means:

1. `RSI` must be backtested individually
2. `IB` must be backtested individually
3. `MOM` must be backtested individually
4. each strategy's live execution path must match its backtest assumptions

## What Must Match Between Backtest And Live

For every strategy run individually:

- signal generated on bar close
- entry attempted on next bar open
- one real account position model
- actual stop-loss behavior
- actual take-profit behavior
- actual max-hold flatten behavior
- actual session cutoff and EOD flatten behavior
- actual fill and slippage accounting

## Safe Path Forward

Phase 1:

- make the NinjaTrader bridge reliable for one strategy at a time
- confirm fills, exits, restart recovery, and logging are correct
- build or adapt a backtest path for each strategy individually

Phase 2:

- choose a valid multi-strategy live model

Valid options are:

- one strategy per account
- one account-level net-position engine that merges strategy signals
- same-direction-only stacking with one net position state

Invalid option:

- treating one single MNQ account as if it can hold independent long and short positions for different strategies simultaneously

## Working Rule For Debugging

Until a new account-level execution model is implemented, all debugging and verification should assume:

- one strategy active at a time
- one net account position
- one source of truth for realized fills and exits
