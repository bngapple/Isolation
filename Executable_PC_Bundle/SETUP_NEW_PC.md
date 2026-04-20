# New PC Setup Guide

This guide is written for a brand-new Windows PC and assumes:

- you have **zero local context**
- you will use **OpenCode** to help with setup
- the goal is to get the Python side and NinjaTrader side running from this repo

Use this folder as the starting point:

- `pc_bundle/`

It contains everything relevant for deployment:

- `python_app/` = Python runtime
- `ninjatrader/` = NinjaTrader `.cs` files
- `scripts/` = convenience launch scripts

## 1. Prerequisites

Install these on the PC first:

1. Git
2. Python 3.11+
3. NinjaTrader 8
4. OpenCode

Recommended checks:

```bash
git --version
python --version
```

## 2. Clone the Repo

Open a terminal in the folder where you want the project.

```bash
git clone https://github.com/bngapple/Isolation
cd Isolation
```

The deployment bundle is here:

```text
Isolation/pc_bundle
```

## 3. OpenCode Prompt for a Fresh Session

If using OpenCode with zero context, start with this prompt:

```text
You are setting up this repo on a brand-new Windows PC.

Work only from the folder `pc_bundle/`.

Goal:
1. verify Python is installed
2. install Python dependencies from `pc_bundle/python_app/requirements.txt`
3. verify `pc_bundle/python_app/main.py` runs without import errors
4. identify where NinjaTrader custom strategy files must be copied
5. confirm the files in `pc_bundle/ninjatrader/` are the ones to use
6. give me exact next steps if anything is missing

Assume zero context and inspect files before making assumptions.
```

## 4. Python App Setup

Change into the Python app directory:

```bash
cd pc_bundle/python_app
```

Install dependencies:

```bash
pip install -r requirements.txt
```

If `pip` points to the wrong Python version, use:

```bash
python -m pip install -r requirements.txt
```

## 5. Optional `.env` Setup

There is an example env file here:

- `pc_bundle/python_app/.env.example`

If you need environment variables later, copy it to `.env` and fill in values.

For the current bridge startup, this may not be required immediately, but keep the file structure ready.

Example:

```bash
copy .env.example .env
```

or in PowerShell:

```powershell
Copy-Item .env.example .env
```

## 6. Start the Python Side

From `pc_bundle/python_app`:

```bash
python main.py
```

Expected result:

- the bridge server starts
- it listens on port `5001`
- it stays running waiting for NinjaTrader

If you want the convenience script instead:

From the repo root:

```bash
pc_bundle\scripts\start_bridge.bat
```

## 7. Verify Python Startup

If startup succeeds, leave the terminal open.

If it fails:

1. read the traceback fully
2. verify `requirements.txt` installed correctly
3. rerun with OpenCode and ask it to fix the exact import/runtime issue

Useful prompt:

```text
Read the traceback from `python main.py` in `pc_bundle/python_app` and fix the setup issue with the smallest correct change. Do not refactor unrelated code.
```

## 8. NinjaTrader File Installation

Use these two files from:

- `pc_bundle/ninjatrader/PythonBridge.cs`
- `pc_bundle/ninjatrader/Rsi4060Standalone.cs`

Copy them into NinjaTrader's custom strategy folder.

Typical Windows path:

```text
Documents\NinjaTrader 8\bin\Custom\Strategies\
```

If the `Strategies` folder does not exist yet, open NinjaTrader once so it creates the standard folder structure.

## 9. Compile in NinjaTrader

In NinjaTrader:

1. Open **NinjaScript Editor**
2. Confirm both files are present
3. Compile

Files to compile:

- `PythonBridge.cs`
- `Rsi4060Standalone.cs`

Do not skip compile errors. Fix them before going live.

## 10. What Runs Live

Live architecture:

- `PythonBridge.cs` = dumb executor / bridge
- `Rsi4060Standalone.cs` = kept in repo as reference/fallback

If your latest live architecture decision is to use the Python-executed flow, prioritize `PythonBridge.cs` and the Python app.

If you instead need the older direct NinjaTrader strategy flow, confirm that explicitly before using `Rsi4060Standalone.cs` live.

## 11. Attach the Strategy in NinjaTrader

For the bridge-driven setup:

1. Open an MNQ chart
2. Set chart timeframe appropriately for the executor flow
3. Attach `PythonBridge`
4. Set:
   - `BridgeHost = 127.0.0.1`
   - `BridgePort = 5001`
   - `Contracts = 5` (or whatever live size you intend)

Make sure the Python app is already running before you enable the strategy.

## 12. Runtime Validation Checklist

Before trusting the setup, verify all of these:

- Python bridge is running with no errors
- NinjaTrader compiled both `.cs` files
- NinjaTrader can connect to the Python bridge
- no repeated disconnect/reconnect spam
- account name and position info are being sent correctly
- bridge commands are being received and parsed

## 13. If Something Fails

Use OpenCode with targeted prompts.

Examples:

For Python errors:

```text
We are in `pc_bundle/python_app`. `python main.py` failed. Read the traceback and fix only the root cause. Keep changes minimal.
```

For NinjaTrader compile errors:

```text
Read the compile errors for `pc_bundle/ninjatrader/PythonBridge.cs` and `Rsi4060Standalone.cs`. Fix only the compile issue, do not change strategy behavior.
```

For connection issues:

```text
Python main.py is running on port 5001, but NinjaTrader is not connecting. Inspect the bridge host/port flow and tell me exactly what to check next.
```

## 14. Recommended First Bring-Up Order

Do this in this order every time on a new machine:

1. Clone repo
2. Open `pc_bundle/`
3. Install Python requirements
4. Start Python app with `python main.py`
5. Copy `.cs` files into NinjaTrader custom strategies
6. Compile in NinjaTrader
7. Attach bridge strategy
8. Confirm connection and idle behavior

## 15. Files That Matter Most

If OpenCode is helping and you want it focused only on startup-critical files, point it to:

- `pc_bundle/python_app/main.py`
- `pc_bundle/python_app/bridge/nt_bridge.py`
- `pc_bundle/python_app/strategy/strategy_executor.py`
- `pc_bundle/python_app/config.py`
- `pc_bundle/ninjatrader/PythonBridge.cs`

## 16. Final Notes

- Do not assume old research/backtest files are needed for first startup
- Do not move files around unless required
- Get the bridge running first, then deal with strategy behavior
- Keep one terminal open for Python logs and one NinjaTrader output window visible during first connection
