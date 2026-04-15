# Parity Vs Live-Compatible Comparison

This document compares three different models that now exist in `Isolation`.

## The Three Models

### 1. Original parity model

Source:

- `run_hybrid_v2_parity.py`

Behavior:

- `3 MNQ` per strategy
- `RSI`, `IB`, and `MOM` can all hold independent positions
- up to `9 MNQ` total exposure
- original `run_htf_swing.py` signal logic and execution engine
- old RTH window `09:30-16:00 ET`

This reproduces the old research stack.

### 2. Live-compatible single-account model

Sources:

- `run_backtest.py`
- `run_ensemble_backtest.py`

Behavior:

- one account-level position at a time
- current live-style session rules
- no overlapping independent per-strategy positions

This is the direction that can actually map to one real NinjaTrader/Lucid account.

### 3. Lucid eval/funded model

Source:

- `model_lucid.py`

Behavior:

- EOD trailing drawdown
- no daily loss limit
- eval target based on account size
- 50% max single-day consistency check

This is the prop-firm model we should optimize for.

## Original Parity Result

File:

- `reports/backtests/hybrid_v2_parity_stitched_8yr.json`

Aggregate result on stitched 8-year data:

- total pnl: `$564,168.48`
- avg monthly: `$5,641.68`
- trades: `19,853`
- worst month: `-$2,891.46`
- worst day: `-$1,332.96`
- max drawdown: `-$8,580.90`

Per-strategy contribution:

- `RSI`: `$318,225.84`
- `IB`: `$51,395.46`
- `MOM`: `$194,547.18`

This closely reproduces the old `~$563k` 8-year headline from the original notes.

## The Critical Reality Check

When that same original-parity portfolio is evaluated as a direct chronological Lucid 150K path at its baked-in original size, it does **not** survive.

Direct chronological Lucid 150K-like path for original parity:

- passed: `False`
- blown: `True`
- days processed before failure: `136`
- peak pnl before failure: `$1,223.94`
- end pnl at failure: `-$3,389.94`
- max drawdown: `-$4,613.88`

At the same time, the old Monte Carlo-style shortcut for that parity portfolio still looks excellent because it shuffles daily returns and uses the old research simplification.

Original-parity Lucid-style Monte Carlo on daily returns at baked-in original size:

- pass rate: `99.5%`
- blowup rate: `0.5%`
- median days to pass: `33`

## Why Those Two Answers Conflict

They are measuring different things.

### Original parity backtest proves:

- the old research stack can reproduce the large historical headline numbers

### Direct Lucid path proves:

- the actual chronological order of daily results matters a lot
- the original `9 MNQ` parallel portfolio can breach Lucid EOD trailing drawdown before it reaches target

### Monte Carlo proves:

- if the same set of daily outcomes arrives in a more favorable order, passing becomes much easier

So the old headline numbers were not fake, but they were not the same thing as a guaranteed live-passable Lucid eval path either.

## Best Live-Compatible Result So Far

The best current Lucid-compatible candidate from the coarse sweep is:

- `IB-only`

Detailed file:

- `reports/backtests/lucid_ib_only_25k_150k.json`

### 25K Lucid

- direct eval passed: `True`
- days to pass: `451`
- direct max DD: `-$606.00`
- MC pass rate: `94.14%`
- funded survived: `True`

### 150K Lucid

- direct eval passed: `True`
- days to pass: `462`
- direct max DD: `-$3,636.00`
- MC pass rate: `73.18%`
- funded survived: `False`

## Best Coarse Weighted Ensemble So Far

Best raw ensemble from the coarse sweep:

- `RSI 0.00 / IB 0.75 / MOM 0.25`

But under Lucid eval rules, it is weaker than `IB-only`.

File:

- `reports/backtests/lucid_best_ensemble_25k_150k.json`

### 25K Lucid

- direct eval passed: `True`
- MC pass rate: `39.34%`
- funded survived: `False`

### 150K Lucid

- direct eval passed: `False`
- direct eval blown: `True`
- MC pass rate: `22.48%`
- funded survived: `False`

## What This Means

### What the original parity model tells us

- the old system edge was largely driven by a larger overlapping portfolio
- `RSI` was the biggest contributor under that original assumption set

### What the live-compatible Lucid model tells us

- once we enforce one-account realism and Lucid constraints, `IB-only` is currently much more robust than the broader combined portfolio

### Most important conclusion

The original research edge and the live-compatible Lucid edge are not the same object.

The original portfolio:

- is good at producing large historical total P&L
- is not automatically safe under chronological Lucid eval constraints

The live-compatible model:

- produces smaller numbers
- but those numbers are much closer to what a single real account can actually do

## Decision Frame From Here

There are now two legitimate next paths.

### Path A: replicate the original edge more faithfully

- build more of the original `Hybrid v2` assumptions into analysis
- test whether some reduced-size version of the original overlapping portfolio can survive Lucid rules

### Path B: optimize what is actually live-compatible

- continue with one-account logic
- improve the weighted or regime-switched model
- port Lucid EOD trailing drawdown into the live executor

## Recommendation

Do both, but in the right order:

1. keep `Hybrid v2 parity` as the historical reference model
2. do not deploy it directly as-is
3. test reduced contract sizing on the original parity portfolio for Lucid survival
4. in parallel, keep improving the live-compatible one-account model

That way we learn whether the large original edge can be made prop-firm-safe without fooling ourselves about what one account can really hold.

## Reduced-Size Original Parity Test

To test whether the old `Hybrid v2` portfolio can survive Lucid without throwing away the original strategy logic, we modeled the original parity trade stream at reduced size.

Reference files:

- `reports/backtests/lucid_hybrid_v2_150k_1perstrat.json`
- `reports/backtests/lucid_hybrid_v2_150k_2perstrat.json`
- `reports/backtests/lucid_hybrid_v2_150k_3perstrat.json`

These correspond to the original overlapping portfolio scaled to:

- `1 MNQ` per strategy
- `2 MNQ` per strategy
- `3 MNQ` per strategy

### 150K Lucid, 1 MNQ per strategy

- direct eval passed: `True`
- days to pass: `662`
- direct max DD: `-$2,860.30`
- MC pass rate: `100.0%`
- funded survived full path: `True`
- funded end pnl: `$188,056.16`

### 150K Lucid, 2 MNQ per strategy

- direct eval passed: `False`
- direct eval blown: `True`
- failed after `163` days
- direct max DD: `-$4,527.12`
- funded survived full path: `False`

### 150K Lucid, 3 MNQ per strategy

- direct eval passed: `False`
- direct eval blown: `True`
- failed after `136` days
- direct max DD: `-$4,613.88`
- funded survived full path: `False`

## New Practical Conclusion

The original `Hybrid v2` portfolio is not obviously unusable.

What appears unusable is the original size.

## Why The Reduced-Size Pass Was So Slow

The original reduced-size parity model looked painfully slow on the stitched `NQ+MNQ` history because the early pre-MNQ years were weak in chronological order.

For the `1 MNQ per strategy` reduced-size parity path on the stitched dataset:

- direct Lucid 150K pass took `662` trading days
- pass date landed on `2020-08-10`

Chronological yearly contribution at reduced size on the stitched dataset:

- `2018`: `$141.34`
- `2019`: `$1,325.60`
- `2020`: `$12,787.04`

So the account was effectively grinding sideways through 2018-2019, and only really accelerated once the 2020 regime arrived.

### Strategy contribution in the slow early years

Reduced-size parity yearly contribution by strategy on the stitched dataset:

- `RSI`
  - `2018`: `-$194.94`
  - `2019`: `$634.40`
- `IB`
  - `2018`: `$195.58`
  - `2019`: `-$664.52`
- `MOM`
  - `2018`: `$140.70`
  - `2019`: `$1,355.72`

No single strategy or simple two-strategy subset solved the slow chronological pass at reduced size. All of them still took roughly `687-933` trading days to pass on the stitched path.

## MNQ-Only Interpretation

When we remove the stitched pre-launch `NQ` segment and look only at actual `MNQ` history, the same reduced-size original parity portfolio looks materially better.

Reference file:

- `reports/backtests/hybrid_v2_parity_mnq_only.json`

MNQ-only parity aggregate:

- total pnl: `$561,767.70`
- avg monthly: `$6,687.71`
- max DD: `-$6,818.22`
- MC pass: `83.06%`

Reduced-size parity at `1 MNQ per strategy`, Lucid 150K, MNQ-only path:

- direct eval passed: `True`
- days to pass: `347`
- direct max DD: `-$2,272.74`
- MC pass rate: `100.0%`
- funded survived: `True`
- funded end pnl: `$187,255.90`

## Revised Interpretation

The reduced-size original parity portfolio is still slow on the stitched `NQ+MNQ` path, but a large part of that slowness comes from the pre-MNQ period rather than from the strategy logic alone.

That means we should be careful not to over-penalize the original system for a synthetic pre-launch history segment that was only added to force an 8-year window.

## Later Start-Date Sensitivity

We also tested the original parity model on the `MNQ`-only dataset using later start dates.

For all results below, the Lucid model uses the reduced-size original parity portfolio at `1 MNQ per strategy`.

### Start 2020-01-01

- full parity monthly avg: `$7,365.36`
- direct Lucid pass: `182` trading days
- funded survived: `True`

### Start 2021-01-01

- full parity monthly avg: `$8,146.98`
- direct Lucid pass: `90` trading days
- funded survived: `True`

### Start 2022-01-01

- full parity monthly avg: `$8,979.23`
- direct Lucid pass: `42` trading days
- funded survived: `True`

### Start 2023-01-01

- full parity monthly avg: `$8,709.30`
- direct Lucid pass: `96` trading days
- funded survived: `True`

### Start 2024-01-01

- full parity monthly avg: `$9,830.49`
- direct Lucid pass: `106` trading days
- funded survived: `True`

## Interpretation Of Later Starts

This shows the original parity edge is very regime-sensitive.

- It is weak and slow when pre-2020 history is included chronologically.
- It becomes much stronger and much faster from `2020+` onward.
- It is strongest in the `2021+` and `2022+` windows.

So if we judge the system using only the modern MNQ era, the original strategy logic looks much more viable than the stitched full-history pass timing suggested.

Based on the current tests:

- original parity at `3 MNQ` per strategy is too aggressive for Lucid 150K
- original parity at `2 MNQ` per strategy is still too aggressive
- original parity at `1 MNQ` per strategy becomes Lucid-viable in both direct eval and funded-path testing

That creates a realistic bridge between the old research edge and a prop-firm-safe deployment path.
