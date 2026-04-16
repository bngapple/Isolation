# Edge Loss Map

This document answers one question:

- what exact edge from the original system are we trying to preserve, and what exact edge are we losing when we port it into the current one-account live-style replay?

## Common Comparison Window

- Instrument: `MNQ`
- Start: `2020-01-01`
- End: `2026-04-13`
- Bars: `15-minute`

## The Edge We Need To Emulate

The correct benchmark is **not** the current live-style replay.

The correct benchmark is the **original reduced parity model** on the same window:

- source: `hybrid_v2_parity_mnq_only.json`
- scaled to `1 MNQ per strategy`

That benchmark produced:

- total pnl: `$186,589.22`
- monthly avg: `$2,455.12`
- max DD: `-$2,272.74`

Lucid outcomes for that benchmark:

- `25K`: direct pass in `48` trading days
- `150K`: direct pass in `182` trading days

This is the edge we are trying to preserve.

## What The Current Port Produces Instead

### Current live-style one-account replay, all strategies

- total pnl: `-$7,601.50`
- monthly avg: `-$100.02`
- max DD: `-$9,951.00`

### Current live-style one-account replay, `IB + MOM` only

- total pnl: `$8,451.00`
- monthly avg: `$111.20`
- max DD: `-$3,303.00`

That means the gap to close is:

- reduced original parity vs current full live-style: `$194,190.72`
- reduced original parity vs best current subset (`IB+MOM`): `$178,138.22`

## Strategy Contribution: Original Reduced Parity

On the same `MNQ 2020+` window, reduced original parity earned:

- `RSI`: `$105,635.82`
- `MOM`: `$63,352.64`
- `IB`: `$17,600.76`

This is the composition of the original edge.

## Strategy Contribution: Current Live-Style Replay

### Full live-style replay

- `RSI`: `-$15,412.00`
- `MOM`: `$4,903.00`
- `IB`: `$2,907.50`

### IB+MOM live-style replay

- `MOM`: `$5,314.00`
- `IB`: `$3,137.00`

## Exact Strategy-Level Edge Loss

Comparing reduced original parity to the current full live-style replay:

- `RSI` loss: `$121,047.82`
- `MOM` loss: `$58,449.64`
- `IB` loss: `$14,693.26`

The single biggest edge loss is `RSI`, but `MOM` also loses a very large amount of edge in the one-account port.

## How Much Of The Original Edge Depends On Overlap

The original system allowed independent overlapping strategy positions.

On the same `MNQ 2020+` window, reduced original parity produced:

- total reduced pnl: `$186,589.22`
- pnl from trades that overlapped another trade at some point: `$134,426.00`
- pnl from non-overlapping trades: `$52,163.22`

So about **72%** of the original reduced edge came from trades that were part of an overlapping portfolio structure.

Trade counts:

- overlapping trades: `7,675`
- non-overlapping trades: `8,101`

### Overlap by strategy

Profit from overlapping trades:

- `MOM`: `$66,454.00`
- `RSI`: `$50,405.94`
- `IB`: `$17,566.06`

This means:

- `IB` is almost entirely an overlap-driven contributor
- `MOM` earns more from overlapping trades than its total standalone contribution, implying its non-overlap trades are a drag
- `RSI` is also heavily overlap-dependent

## What The One-Account Gate Is Suppressing

Current live-style full replay raw entry signals on the same window:

- `RSI`: `19,449`
- `MOM`: `6,082`
- `IB`: `731`

Selected entries after one-account gating:

- `RSI`: `10,920`
- `MOM`: `5,390`
- `IB`: `480`

Bars with multi-signal conflict:

- `4,617`

Conflict combinations:

- `MOM + RSI`: `3,989`
- `IB + MOM + RSI`: `182`
- `IB + RSI`: `119`
- `IB + MOM`: `68`

Conflict winners under current edge ranking:

- `MOM`: `3,989`
- `IB`: `369`
- `RSI`: `0`

Signals blocked while another position was already open:

- `RSI`: `4,241`
- `MOM`: `442`
- `IB`: `251`

## What This Means

The edge loss is coming from **two separate sources**.

### 1. Overlap removal is a real and large edge cut

The original system made most of its money while strategies were allowed to overlap.

So forcing one account / one trade at a time is expected to remove a large chunk of the original edge.

### 2. The current one-account port is also mis-emulating strategy behavior

If the port were preserving strategy behavior well, we would expect at least some reduced one-account version of the original strategy contributions.

Instead we see:

- original reduced `RSI`: `$105,635.82`
- current replay `RSI`: `-$15,412.00`

That is not just overlap removal. That is a major behavior drift or structural mismatch.

Likewise:

- original reduced `MOM`: `$63,352.64`
- current replay `MOM`: `$4,903.00`

So `MOM` is also losing most of its edge in the current port, even before we worry about `RSI`.

## Practical Conclusion

We should emulate **the original reduced parity edge**, not the current weak live-style replay.

That means the porting target is:

- monthly avg around `$2,455` on this control window, not `$111`
- 25K direct pass in weeks, not immediate failure
- 150K direct pass in months, not `1,267` trading days

## What Must Be Preserved

To preserve the original edge as much as possible, the one-account port must recover:

1. the original signal behavior, especially `RSI` and `MOM`
2. more of the original portfolio interaction benefit, even if true overlap is not possible on one account
3. the original execution timing and exit behavior that made the reduced parity model strong

## What Must Change

Even if we preserve the original edge logic, one account still cannot hold impossible independent positions.

So the final system must still enforce:

- one account
- one net tradable reality
- Lucid-compatible drawdown rules

The task now is not to guess a new strategy.

The task is to close the gap between:

- reduced original parity: `$186,589.22`
- current best one-account replay: `$8,451.00`

as much as possible without violating one-account execution reality.
