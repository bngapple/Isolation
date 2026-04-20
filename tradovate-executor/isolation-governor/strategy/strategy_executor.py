from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from config import (
    BE_MINUTES,
    CONTRACTS,
    KILLSWITCH_DOLLAR,
    OVERBOUGHT,
    OVERSOLD,
    RSI_PERIOD,
    SESSION_END,
    SESSION_START,
    STOP_POINTS,
    TARGET_POINTS,
    TRAIL_LOCK_1,
    TRAIL_LOCK_2,
    TRAIL_LOCK_3,
    TRAIL_STEP_1,
    TRAIL_STEP_2,
    TRAIL_STEP_3,
)
from run_hybrid_v2_parity import calc_rsi
from utils.logger import get_logger


@dataclass
class StrategyState:
    position: int = 0
    entry_price: float = 0.0
    entry_time: datetime | None = None
    daily_pnl: float = 0.0
    be_applied: bool = False
    trail_level: int = 0


class StrategyExecutor:
    def __init__(self, db):
        self.db = db
        self.logger = get_logger("strategy_executor")
        self.state = StrategyState()
        self.close_buffer: list[float] = []
        self.last_bar_ts: int | None = None

    def handle_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        message_type = payload.get("type")
        if message_type == "bar":
            response = self._handle_bar(payload)
        elif message_type == "minute":
            response = self._handle_minute(payload)
        else:
            response = {"action": "HOLD", "reason": "unknown_type"}

        self._log_decision(payload, response)
        return response

    def _handle_bar(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._sync_state(payload)
        ts = self._parse_ts(payload["ts"])
        self.last_bar_ts = payload["ts"]
        close_price = float(payload["close"])
        self.close_buffer.append(close_price)
        rsi_value = self._current_rsi()

        if self.state.position == 0:
            self._reset_trade_state_if_flat()

        if self.state.position != 0:
            return {"action": "HOLD", "reason": "in_position"}

        if not self._in_session(ts):
            return {"action": "HOLD", "reason": "outside_session"}

        if self.state.daily_pnl <= -KILLSWITCH_DOLLAR:
            return {"action": "FLAT", "reason": "killswitch"}

        if rsi_value is None:
            return {"action": "HOLD", "reason": "insufficient_rsi_history"}

        if rsi_value < OVERSOLD:
            return {
                "action": "LONG",
                "contracts": CONTRACTS,
                "stop": close_price - STOP_POINTS,
                "target": close_price + TARGET_POINTS,
                "reason": f"rsi={rsi_value:.2f}",
            }
        if rsi_value > OVERBOUGHT:
            return {
                "action": "SHORT",
                "contracts": CONTRACTS,
                "stop": close_price + STOP_POINTS,
                "target": close_price - TARGET_POINTS,
                "reason": f"rsi={rsi_value:.2f}",
            }
        return {"action": "HOLD", "reason": f"rsi={rsi_value:.2f}"}

    def _handle_minute(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._sync_state(payload)
        ts = self._parse_ts(payload["ts"])

        if self.state.position == 0:
            self._reset_trade_state_if_flat()
            return {"action": "HOLD", "reason": "flat"}

        if self.state.daily_pnl <= -KILLSWITCH_DOLLAR:
            return {"action": "FLAT", "reason": "killswitch"}

        if self.state.entry_time is None:
            return {"action": "HOLD", "reason": "missing_entry_time"}

        elapsed = (ts - self.state.entry_time).total_seconds() / 60.0
        if elapsed >= BE_MINUTES and not self.state.be_applied:
            self.state.be_applied = True
            self.state.trail_level = 0
            return {"action": "MOVE_STOP", "price": self.state.entry_price, "reason": "break_even"}

        if self.state.be_applied:
            high = float(payload["high"])
            low = float(payload["low"])
            if self.state.position > 0:
                favorable = high - self.state.entry_price
            else:
                favorable = self.state.entry_price - low

            if favorable >= TRAIL_STEP_3 and self.state.trail_level < 3:
                self.state.trail_level = 3
                return {"action": "MOVE_STOP", "price": self._trail_price(TRAIL_LOCK_3), "reason": "trail3"}
            if favorable >= TRAIL_STEP_2 and self.state.trail_level < 2:
                self.state.trail_level = 2
                return {"action": "MOVE_STOP", "price": self._trail_price(TRAIL_LOCK_2), "reason": "trail2"}
            if favorable >= TRAIL_STEP_1 and self.state.trail_level < 1:
                self.state.trail_level = 1
                return {"action": "MOVE_STOP", "price": self._trail_price(TRAIL_LOCK_1), "reason": "trail1"}

        if not self._in_session(ts):
            return {"action": "FLAT", "reason": "session_end"}

        return {"action": "HOLD", "reason": "manage"}

    def _trail_price(self, lock_points: float) -> float:
        if self.state.position > 0:
            return self.state.entry_price + lock_points
        return self.state.entry_price - lock_points

    def _sync_state(self, payload: dict[str, Any]) -> None:
        incoming_position = int(payload.get("position", 0))
        daily_pnl = payload.get("daily_pnl")
        if daily_pnl is not None:
            self.state.daily_pnl = float(daily_pnl)

        if incoming_position == 0 and self.state.position != 0:
            self._reset_trade_state_if_flat()

        self.state.position = incoming_position
        if incoming_position != 0:
            entry_price = payload.get("entry_price")
            if entry_price is not None and float(entry_price) > 0:
                if self.state.entry_price == 0.0:
                    self.state.entry_price = float(entry_price)
                else:
                    self.state.entry_price = float(entry_price)
            if self.state.entry_time is None:
                self.state.entry_time = self._parse_ts(payload["ts"])

    def _reset_trade_state_if_flat(self) -> None:
        self.state.position = 0
        self.state.entry_price = 0.0
        self.state.entry_time = None
        self.state.be_applied = False
        self.state.trail_level = 0

    def _current_rsi(self) -> float | None:
        if len(self.close_buffer) < RSI_PERIOD + 1:
            return None
        values = calc_rsi(self.close_buffer, RSI_PERIOD)
        latest = values[-1]
        if latest != latest:
            return None
        return float(latest)

    def _parse_ts(self, ts: int | float) -> datetime:
        return datetime.fromtimestamp(float(ts), tz=None)

    def _in_session(self, ts: datetime) -> bool:
        hhmm = ts.hour * 100 + ts.minute
        start_h, start_m = map(int, SESSION_START.split(":"))
        end_h, end_m = map(int, SESSION_END.split(":"))
        start = start_h * 100 + start_m
        end = end_h * 100 + end_m
        return start <= hhmm < end

    def _log_decision(self, payload: dict[str, Any], response: dict[str, Any]) -> None:
        self.db.execute(
            """
            INSERT INTO governor_decisions
            (decision_datetime, trigger, mode_decided, size_multiplier, reason, session_pnl_at_decision, claude_prompt, claude_response)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.utcnow().isoformat(),
                payload.get("type", "unknown"),
                response.get("action", "HOLD"),
                float(response.get("contracts", 0) or 0),
                response.get("reason", ""),
                self.state.daily_pnl,
                json.dumps(payload, default=str),
                json.dumps(response, default=str),
            ),
        )


def sample_rsi_values(prices: list[float]) -> list[float | None]:
    values = calc_rsi(prices, RSI_PERIOD)
    out = []
    for value in values:
        if value != value:
            out.append(None)
        else:
            out.append(round(float(value), 6))
    return out
