# Lucid Sizing Recommendations

This document turns the control-benchmark sizing work into a practical recommendation sheet.

## Scope

These recommendations are based on the **original parity control benchmark** on:

- Instrument: `MNQ`
- Window: `2020-01-01` to `2026-04-13`
- Bars: `15-minute`

Important:

- these are derived from the **original parity benchmark**, not the current weak one-account replay
- they should be treated as **target sizing guidance** while we port the live system closer to the original edge

## Definitions

### Recommended

- practical size that passes and survives with reasonable margin

### Aggressive

- faster pass but with tighter risk margin

### Unsafe

- likely to fail eval or fail funded survival

## 25K Lucid

Rules:

- profit target: `$1,250`
- max drawdown: `-$1,000`

### Recommended size

- **`1.0 total MNQ`**

Observed on control benchmark:

- direct pass: `55` trading days
- direct max DD: `-$319.71`
- funded survived: `True`

### Still acceptable / upper practical range

- **`1.25 total MNQ`**

Observed:

- direct pass: `53` trading days
- direct max DD: `-$399.63`
- funded survived: `True`

### Aggressive but not funded-safe

- **`1.5` to `3.0 total MNQ`**

Observed:

- these sizes still pass direct eval quickly
- but they fail funded survival later

### Unsafe

- **`5+ total MNQ`**

Observed:

- `5 total MNQ` blows up in `10` trading days
- `6 total MNQ` blows up in `7` trading days

### 25K recommendation

Use:

- **start at `1.0 total MNQ`**
- at most test **`1.25 total MNQ`** as the upper practical range

Do not treat the larger benchmark sizes as 25K-deployable.

## 150K Lucid

Rules:

- profit target: `$9,000`
- max drawdown: `-$4,500`

### Recommended size

- **`5.0 total MNQ`**

Observed:

- direct pass: `135` trading days
- direct max DD: `-$3,787.90`
- funded survived: `True`

This is the most straightforward practical recommendation.

### Aggressive but still funded-safe

- **`5.25` to `5.75 total MNQ`**

Observed:

- `5.25`: direct pass in `93` days, funded survived
- `5.5`: direct pass in `92` days, funded survived
- `5.75`: direct pass in `89` days, funded survived

This range appears to be the best speed-versus-survival zone.

### Practical ceiling

- about **`5.9 total MNQ`** is already very close to the failure boundary
- the benchmark threshold work showed funded survival breaking around **`~6.0 total MNQ`**

### Unsafe

- **`6.0 total MNQ` and above**

Observed:

- `6 total MNQ` passes direct eval in `61` trading days
- but fails funded survival after `114` trading days

### 150K recommendation

Use one of these depending on goal:

- **conservative practical start:** `5.0 total MNQ`
- **faster but still benchmark-safe target:** `5.25` to `5.75 total MNQ`

Avoid:

- `6.0 total MNQ` unless the strategy is materially improved versus the current port and funded risk is revalidated

## Most Important Caveat

These sizes come from the **original parity benchmark**.

The current one-account live-style port is still much weaker than that benchmark.

So these recommendations are best understood as:

- the sizing we want to support **after** the port preserves more of the original edge

They are **not** a recommendation to trade the current weak replay at those sizes.

## Simple Summary

### 25K

- recommended: **`1.0 total MNQ`**
- upper practical: **`1.25 total MNQ`**
- unsafe: **`5+ total MNQ`**

### 150K

- recommended: **`5.0 total MNQ`**
- aggressive funded-safe zone: **`5.25` to `5.75 total MNQ`**
- unsafe: **`6.0 total MNQ+`**
