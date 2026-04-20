# Executable PC Bundle

This folder is the main Windows deployment/update bundle.

Use this folder instead of digging through the rest of the repo.

## What it contains

- `python_app/`
  - the Python bridge/runtime app
- `ninjatrader/`
  - `PythonBridge.cs`
  - `Rsi4060Standalone.cs`
- `scripts/`
  - optional helper scripts
- `UPDATE_AND_RUN.bat`
  - main Windows setup/update launcher
- `SETUP_NEW_PC.md`
  - detailed instructions for a brand-new machine

## Which files to compile in NinjaTrader

Copy these into:

```text
Documents\NinjaTrader 8\bin\Custom\Strategies\
```

Then compile both in NinjaScript Editor:

- `PythonBridge.cs`
- `Rsi4060Standalone.cs`

`PythonBridge.cs` is the live runtime bridge executor.

`Rsi4060Standalone.cs` is kept for reference/fallback.

## Main workflow

On Windows, run:

```text
Executable_PC_Bundle\UPDATE_AND_RUN.bat
```

That script will:

1. try to pull the latest repo updates
2. stop old Python bridge processes on port 5001
3. remove and recreate `.venv`
4. reinstall Python dependencies
5. overwrite NinjaTrader strategy files with the current bundle versions
6. start the Python bridge in a new window
7. print the final NinjaTrader compile steps

## Notes

- `.env` is intentionally not committed
- `.env.example` is included
- `isolation.db` is included
- if you update live logic later, update this folder and push it again
