# Original Parity Mode

`run_hybrid_v2_parity.py` exists to reproduce the original HTF Swing `Hybrid v2` research assumptions as closely as possible.

## What It Preserves

- `3 MNQ` per strategy
- `RSI`, `IB`, and `MOM` each run independently
- up to `9 MNQ` total exposure
- original `run_htf_swing.py` signal logic
- original RTH filter: `09:30-16:00 ET`
- original flatten cutoff logic
- original cost model:
  - `2` slippage ticks per side
  - commissions and exchange fees from the old script
- original Lucid Monte Carlo shortcut from the old root scripts

## What It Does Not Claim

This mode is **not** the same thing as a live-compatible single-account execution model.

It intentionally preserves the old research behavior where strategies can overlap independently in the same symbol.

## Why It Exists

We need two modes in this repo:

1. original-parity mode
2. live-compatible mode

Without both, we cannot tell whether the original strong headline numbers came from:

- genuinely strong strategy logic
- larger independent per-strategy sizing
- unrealistic overlapping position assumptions
- different session and signal rules

## Usage

```powershell
python run_hybrid_v2_parity.py --data data/processed/MNQ/1m/nq_mnq_stitched_8yr_databento.parquet --output reports/backtests/hybrid_v2_parity.json
```

## Output

The JSON output includes:

- aggregate metrics
- per-strategy metrics
- Monte Carlo summary
- all trades

Use this output to compare against the original reports such as:

- `htf_swing_v3.json`
- `htf_swing_lucid.json`
- `htf_swing_competition.json`
- `htf_swing_8yr.json`
