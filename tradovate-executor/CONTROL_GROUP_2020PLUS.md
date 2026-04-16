# Control Group: MNQ 2020+ 

This is the apples-to-apples control set for comparing the original strong backtest against the current one-account live-style replay.

## Common Window

- Instrument data: `MNQ` only
- Source file: `data/processed/MNQ/1m/mnq_8yr_databento.parquet`
- Start date: `2020-01-01`
- End date: `2026-04-13`
- Strategy bars: `15-minute`

## Models Compared

### 1. Original parity, full original size

Source:

- `reports/backtests/hybrid_v2_parity_mnq_only.json`

Meaning:

- original `Hybrid v2`
- independent overlapping strategy positions
- `3 MNQ` per strategy
- up to `9 MNQ` total

Result:

- trades: `15,776`
- total pnl: `$559,767.66`
- monthly avg: `$7,365.36`
- max DD: `-$6,818.22`

### 2. Original parity, reduced to 1 MNQ per strategy

Meaning:

- same original parity trade stream
- scaled down to `1/3` of original size
- equivalent to `1 MNQ` per strategy

Result:

- trades: `15,776`
- total pnl: `$186,589.22`
- monthly avg: `$2,455.12`
- max DD: `-$2,272.74`

Lucid 25K:

- direct pass: `True`
- days to pass: `48`
- direct max DD: `-$959.12`
- MC pass rate: `98.74%`
- funded survived: `False`

Lucid 150K:

- direct pass: `True`
- days to pass: `182`
- direct max DD: `-$2,272.74`
- MC pass rate: `100.0%`
- funded survived: `True`

### 3. Current live-style one-account replay, all three strategies

Source:

- `reports/backtests/live_style_replay_mnq_2020plus.json`

Meaning:

- one-account gate
- strongest-edge same-bar arbitration
- current live-style port with `RSI + IB + MOM`

Result:

- trades: `16,769`
- total pnl: `-$7,601.50`
- monthly avg: `-$100.02`
- max DD: `-$9,951.00`

Strategy pnl:

- `IB`: `$2,907.50`
- `MOM`: `$4,903.00`
- `RSI`: `-$15,412.00`

Lucid 25K:

- direct pass: `False`
- direct blown: `True`
- failed after `45` trading days
- MC pass rate: `24.28%`

Lucid 150K:

- direct pass: `False`
- direct blown: `True`
- failed after `568` trading days
- MC pass rate: `0.52%`

### 4. Current live-style one-account replay, IB + MOM only

Source:

- `reports/backtests/live_style_replay_mnq_2020plus_ib_mom.json`

Meaning:

- one-account gate
- strongest-edge same-bar arbitration
- `RSI` disabled

Result:

- trades: `6,190`
- total pnl: `$8,451.00`
- monthly avg: `$111.20`
- max DD: `-$3,303.00`

Strategy pnl:

- `IB`: `$3,137.00`
- `MOM`: `$5,314.00`

Lucid 25K:

- direct pass: `False`
- direct blown: `True`
- failed after `51` trading days
- MC pass rate: `45.42%`

Lucid 150K:

- direct pass: `True`
- days to pass: `1,267`
- direct max DD: `-$3,303.00`
- MC pass rate: `60.90%`
- funded survived: `True`

## Main Conclusion

The control group makes the gap explicit.

### Original parity retains the original edge

- huge positive total pnl
- strong monthly average
- reduced-size version can pass both 25K and 150K direct evals on this window

### Current live-style one-account port is still far from the original edge

- full current port loses money badly
- `RSI` is the major drag
- removing `RSI` helps materially, but the result is still far weaker than the reduced original parity model

## What This Means For Porting

We now have a clean same-window answer:

- the edge is in the original `Hybrid v2` logic
- the edge is being lost in the current one-account port
- `RSI` is either mis-ported, structurally harmed by the one-account model, or both

So the next porting work should optimize for this goal:

- make the one-account replay converge toward the reduced original parity result, not toward the current weak live-style result
