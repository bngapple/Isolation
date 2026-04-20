# PC Bundle

This folder contains the files needed to start the Python side and install the NinjaTrader strategy files on the PC.

## Contents

- `python_app/`
  - full Python bridge/runtime app
- `ninjatrader/PythonBridge.cs`
  - dumb executor strategy that sends bars to Python and executes returned commands
- `ninjatrader/Rsi4060Standalone.cs`
  - kept for reference/fallback
- `scripts/start_bridge.sh`
- `scripts/start_bridge.bat`

## Quick start

1. Install Python 3.11+
2. Open a terminal in `pc_bundle/python_app`
3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Start the Python side:

```bash
python main.py
```

5. Copy the `.cs` files from `pc_bundle/ninjatrader/` into NinjaTrader's custom strategy folder and compile them.

## Notes

- `.env` is intentionally not included
- `isolation.db` is included in `python_app/`
- `PythonBridge.cs` is the intended live runtime strategy
- `Rsi4060Standalone.cs` is included as reference/fallback
