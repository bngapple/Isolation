# Live Path Issues

This document records the defects confirmed in the currently running NinjaTrader bridge path.

## Confirmed Runtime Symptoms

From `logs/executor_20260414.log`:

- repeated `Fill timeout` warnings for `RSI`, `IB`, and `MOM`
- repeated `Fill unconfirmed after timeout` warnings
- repeated `Connection closed by NinjaTrader` events
- entry rows written to CSV without corresponding exit rows for max-hold flattens

## Issue 1: Entry Fill Confirmation Frequently Times Out

### Code path

1. `app.py::_execute_signal()`
2. `ninjatrader_bridge.py::place_entry_with_bracket()`
3. `ninjatrader_bridge.py::_send()` sends `{"cmd":"ENTRY",...}`
4. `PythonBridge.cs::HandleEntry()` receives the command and calls:
   - `SetStopLoss(...)`
   - `SetProfitTarget(...)`
   - `EnterLong(...)` or `EnterShort(...)`
5. Python waits up to `FILL_TIMEOUT = 30s` for a matching `fill` message.
6. The pending future is only completed by `ninjatrader_bridge.py::_on_fill()`.

### What happens now

- The `fill` callback is often not received in time.
- On timeout, Python leaves the order record in `WORKING` status.
- `app.py::_execute_signal()` still marks the strategy as filled and logs an entry.
- It uses the open price or signal price as a fallback fill price.

### Why this is dangerous

- Python can believe it is in a position even when confirmation was never received.
- Logged entry prices can differ from real NinjaTrader fills.
- Bracket values persisted in Python can diverge from the actual bracket managed by NinjaTrader.

## Issue 2: Max-Hold Flatten Does Not Produce A Realized Exit In Python

### Code path

1. `signal_engine.py::_check_max_hold()` creates a flatten signal with `contracts=0`.
2. `app.py::_on_bar_complete()` routes that signal to `app.py::_handle_flatten_signal()`.
3. `app.py::_handle_flatten_signal()`:
   - sends `FLATTEN` through `master_executor.flatten_position(strategy)`
   - immediately calls `master_executor.clear_strategy(strategy)`
   - immediately calls `signal_engine.mark_flat(strategy)`
   - saves state

### NinjaTrader side

- `PythonBridge.cs::HandleFlatten()` sends only an `ack`.
- It does not send a structured `exit` event with fill price and qty.

### Result

- `app.py::_on_nt_exit()` never runs for those max-hold exits.
- `TradeLogger.log_exit()` is never called.
- `RiskManager.record_trade_pnl()` is never called.
- CSV trade history misses those exits.
- Daily/monthly realized P&L can be understated or stale.

## Issue 3: NT Restart Recovery Is Not Real Recovery

### Code path

- `app.py::_sync_positions()` calls `PositionSync.sync_all()`.
- In NT mode, `NinjaTraderBridge` exposes:
  - `get_current_position()` -> `None`
  - `get_working_orders()` -> `[]`

### Result

- startup sync always sees no actual position data from NinjaTrader
- Python cannot reconstruct real live positions after reconnect or restart
- the app relies on local `state.json`, which may already be wrong if fills were timed out or exits were not reported

## Issue 4: Bridge Disconnects Cause Additional State Drift Risk

### Evidence

- `executor_20260414.log` shows repeated `Connection closed by NinjaTrader`
- `ninjatrader_bridge.py` reconnects automatically every `5s`

### Risk

- disconnects can happen between ENTRY, fill, and exit events
- pending futures are failed only when the disconnect is observed inside the connection loop
- during reconnect windows, Python and NinjaTrader can disagree about open strategy state

## Issue 5: Current Trade Log Is Not Backtest-Grade Yet

The current runtime CSV is not reliable enough to serve as a clean backtest truth source because:

- some entries are fallback fills, not confirmed fills
- some exits are never written
- some realized P&L is not recorded when flatten happens outside the NT exit callback path

That means the live path needs repair before it can become a trustworthy source for forward-vs-backtest parity.

## Priority Fix Order

1. make NT entry fills deterministic and confirmed
2. make every flatten path emit a structured exit with fill price and qty
3. make Python update realized P&L only from confirmed exits
4. add a real NT position/status sync path for startup and reconnect recovery
5. only after that, use the runtime logs and CSVs as parity data for backtest validation
