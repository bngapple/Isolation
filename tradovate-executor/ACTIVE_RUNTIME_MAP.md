# Active Runtime Map

This document captures the code path that is actually live on this PC today.

## Runtime Identity

- Project path: `C:\Users\areko\Downloads\berjquant-windows-local-python-bridge-live\tradovate-executor`
- Primary entrypoint: `app.py`
- Expected operator command: `python app.py --live`
- Active execution mode: NinjaTrader-only bridge mode
- NinjaTrader account name: `LTT07T22GBH`
- Bridge target: `127.0.0.1:6000`
- Instrument symbol: `MNQU6`

## Evidence This Is The Live Path

- `NinjaTrader.exe` is listening on TCP `6000`.
- A local `python.exe` process is connected to `127.0.0.1:6000`.
- `logs/executor_20260414.log` shows:
  - `NT-only mode detected`
  - `Connected to NinjaTrader at 127.0.0.1:6000`
  - `EXECUTOR RUNNING`

## Startup Flow

1. `app.py` loads `config.json` through `AppConfig.load()` in `config.py`.
2. `TradovateExecutor.start()` detects NT-only mode because:
   - `config.nt` exists
   - `config.accounts` is empty
3. `app.py` builds a synthetic master `AuthSession` using the first NinjaTrader account key.
4. `app.py` resolves the NT bridge target from `config.json` and constructs `NinjaTraderBridge`.
5. `NinjaTraderBridge.connect()` starts a persistent TCP connection loop.
6. `app.py` waits for `NinjaTraderBridge.connected`.
7. `app.py` runs `PositionSync.sync_all()`.
8. `app.py` waits for market data and historical bars from NinjaTrader.
9. `RiskManager.start_eod_timer()` starts the session and flatten watchdog.
10. The app waits indefinitely on `_shutdown_event`.

## Live Data And Execution Flow

1. `PythonBridge.cs` sends `bar` and `market` messages over TCP.
2. `ninjatrader_bridge.py` reads newline-delimited JSON and dispatches messages.
3. `app.py._on_nt_market_message()` forwards:
   - `market` -> `MarketDataEngine.on_tick()`
   - `bar` -> `MarketDataEngine.ingest_historical_bar()`
4. `market_data.py` builds 15-minute bars and indicator state.
5. On completed bars, `MarketDataEngine` calls `app.py._on_bar_complete()`.
6. `app.py._on_bar_complete()`:
   - executes pending signals from the prior bar at the new bar open
   - checks `RiskManager.can_trade()`
   - asks `SignalEngine.evaluate()` for new signals
   - queues new entries for the next bar open
   - handles max-hold flatten signals immediately
7. `app.py._execute_signal()` calls `NinjaTraderBridge.place_entry_with_bracket()`.
8. `ninjatrader_bridge.py` sends an `ENTRY` command to NinjaTrader.
9. `PythonBridge.cs` places the order inside NinjaTrader and is supposed to send back a `fill` message.
10. Python records entries in `TradeLogger` and persists strategy state in `state.json`.
11. NinjaTrader bracket exits are supposed to come back as `exit` messages and be processed by `app.py._on_nt_exit()`.

## Files Actively Used In The Current Live Path

### Core runtime

- `app.py`
- `config.py`
- `config.json`
- `market_data.py`
- `indicators.py`
- `signal_engine.py`
- `ninjatrader_bridge.py`
- `risk_manager.py`
- `trade_logger.py`
- `position_sync.py`
- `order_executor.py`
- `auth_manager.py`

### NinjaTrader side

- `NinjaTrader/PythonBridge.cs`

### Runtime state and logs

- `state.json`
- `state/risk_state.json`
- `logs/executor_YYYYMMDD.log`
- `logs/trades_YYYY-MM-DD.csv`

## Files Present But Not Part Of The Active NT-Only Runtime

### Disabled by current config or mode

- `websocket_client.py`
- Tradovate REST execution paths inside `order_executor.py`
- `copy_engine.py`
- real Tradovate login flow inside `auth_manager.py`

### UI and dashboard stack

- `app_launcher.py`
- `run_dashboard.py`
- `server/*`
- `dashboard/*`

### Separate alternative trading architecture

- `NinjaTrader/HTFSwingV3HybridV2.cs`

## Current Runtime Configuration

From `config.json`:

- Environment: `live`
- Symbol: `MNQU6`
- Session start: `09:30 ET`
- No new entries after: `16:30 ET`
- Flatten time: `16:45 ET`
- Monthly loss limit: `-4500.0`
- Daily loss limit: `null` (disabled)

### Strategy sizing

- RSI: `1` contract
- IB: `1` contract
- MOM: `1` contract

## Strategy Definitions

### RSI

- Source file: `signal_engine.py`
- Long: `RSI(5) < 35`
- Short: `RSI(5) > 65`
- Stop loss: `10` points
- Take profit: `100` points
- Max hold: `5` bars

### IB

- Source files: `signal_engine.py`, `market_data.py`
- IB window: `09:30-10:00 ET`
- Long: completed bar closes above IB high
- Short: completed bar closes below IB low
- Range filter: IB must sit between trailing `P25` and `P75` of prior IB ranges
- Daily cap: one IB signal per day
- Stop loss: `10` points
- Take profit: `120` points
- Max hold: `15` bars

### MOM

- Source file: `signal_engine.py`
- Conditions:
  - bar range > `ATR(14)`
  - bar volume > `SMA(volume, 20)`
  - bullish bar and close > `EMA(21)` for longs
  - bearish bar and close < `EMA(21)` for shorts
- Stop loss: `15` points
- Take profit: `100` points
- Max hold: `5` bars

## Persistence And State

- `state.json`
  - stores per-strategy flat/open state for crash recovery
  - stores side, bars held, fill price, qty
- `state/risk_state.json`
  - stores daily and monthly realized P&L
  - stores whether monthly halt has already been hit
- `logs/trades_YYYY-MM-DD.csv`
  - stores entry and exit rows in CSV format
- `logs/executor_YYYYMMDD.log`
  - stores runtime logs and operational evidence

## Isolation Set For Migration

If we move only the code needed for the currently running system, the minimum meaningful set is:

- `app.py`
- `config.py`
- `config.json`
- `auth_manager.py`
- `market_data.py`
- `indicators.py`
- `signal_engine.py`
- `ninjatrader_bridge.py`
- `order_executor.py`
- `risk_manager.py`
- `trade_logger.py`
- `position_sync.py`
- `requirements.txt`
- `NinjaTrader/PythonBridge.cs`

The dashboard, server, copy engine, Tradovate WebSocket layer, and the standalone native NinjaTrader strategy are not required for the isolated live path.
