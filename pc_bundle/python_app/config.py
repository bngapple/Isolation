from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*args, **kwargs):
        return False


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _get_str(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _get_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _get_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


ANTHROPIC_API_KEY = _get_str("ANTHROPIC_API_KEY")
NEWSAPI_KEY = _get_str("NEWSAPI_KEY")
DATABENTO_API_KEY = _get_str("DATABENTO_API_KEY")

BRIDGE_HOST = _get_str("BRIDGE_HOST", "0.0.0.0")
BRIDGE_PORT = _get_int("BRIDGE_PORT", 5001)
KILLSWITCH_DOLLAR = _get_float("KILLSWITCH_DOLLAR", 750.0)
CONTRACTS = _get_int("CONTRACTS", 3)
ACCOUNT_SIZE = _get_float("ACCOUNT_SIZE", 25000.0)

RSI_PERIOD = 4
OVERSOLD = 40
OVERBOUGHT = 60
STOP_POINTS = 10.0
TARGET_POINTS = 100.0
BE_MINUTES = 15
TRAIL_STEP_1 = 30
TRAIL_STEP_2 = 50
TRAIL_STEP_3 = 75
TRAIL_LOCK_1 = 15
TRAIL_LOCK_2 = 30
TRAIL_LOCK_3 = 50
SESSION_START = "09:30"
SESSION_END = "16:45"

DB_PATH = str(BASE_DIR / "isolation.db")
LOG_DIR = str(BASE_DIR / "logs")

ACCOUNTS = [
    {"id": "lucid_25k_1", "port": 5001, "max_loss": 750.0, "contracts": 5},
    {"id": "lucid_150k_1", "port": 5002, "max_loss": 3375.0, "contracts": 15},
]
