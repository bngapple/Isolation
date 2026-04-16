# Rsi4060Standalone Setup

This folder packages the native NinjaTrader strategy for the repo's current RSI-only variant.

Contents:
- `Rsi4060Standalone.cs`: NinjaTrader 8 strategy source

Target strategy defaults:
- `RSI(4)`
- buy below `40`
- sell above `60`
- `1` position at a time
- stop loss `10` points
- profit target `100` points
- max hold `5` bars
- last entry `16:30`
- flatten `16:45`

## What Claude Code Should Do On Another PC

Use Claude Code on the target machine to perform these steps:

1. Clone this repo.
2. Locate this file:
   - `tradovate-executor/NinjaTrader/Rsi4060Standalone/Rsi4060Standalone.cs`
3. Copy it to the NinjaTrader custom strategies folder:
   - Windows path: `Documents\NinjaTrader 8\bin\Custom\Strategies\Rsi4060Standalone.cs`
4. Open NinjaTrader 8.
5. Compile NinjaScript:
   - `Tools -> Edit NinjaScript -> Strategy -> Compile`
6. In Strategy Analyzer or on a chart, select:
   - strategy: `Rsi4060Standalone`
   - instrument: `MNQ`
   - bars: `15 Minute`
7. Keep the default parameters unless intentionally testing a variation.

## Ready-To-Paste Claude Code Prompt

```text
Set up the NinjaTrader strategy from this repo on this PC.

Source file:
tradovate-executor/NinjaTrader/Rsi4060Standalone/Rsi4060Standalone.cs

Tasks:
1. Confirm the repo is present.
2. Copy that file to Documents\NinjaTrader 8\bin\Custom\Strategies\Rsi4060Standalone.cs.
3. Tell me the exact next NinjaTrader clicks to compile it.
4. Tell me the exact Strategy Analyzer settings to run it on MNQ 15-minute bars.

Do not modify the strategy logic unless I ask.
```

## Notes

- This is a native NinjaTrader strategy. It does not use the Python bridge.
- Results should be directionally similar to the repo backtests, but not bit-for-bit identical because NinjaTrader fill modeling, chart session templates, and costs can differ.
