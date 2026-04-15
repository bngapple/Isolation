"""Shared account-level execution policy helpers."""

from __future__ import annotations

from typing import Optional

from config import ExecutionConfig
from signal_engine import Signal


def select_account_entry(
    entry_signals: list[Signal],
    execution_cfg: ExecutionConfig,
    has_pending_entry: bool,
    has_open_position: bool,
) -> tuple[Optional[Signal], Optional[str]]:
    if not entry_signals:
        return None, None

    if has_pending_entry:
        return None, "account already has a pending entry candidate"

    if execution_cfg.single_position_mode and has_open_position:
        return None, "account already has an open position"

    if len(entry_signals) == 1:
        return entry_signals[0], None

    scores = execution_cfg.strategy_edge_scores or {}
    ranked = sorted(
        entry_signals,
        key=lambda sig: (float(scores.get(sig.strategy, 0.0)), sig.strategy),
        reverse=True,
    )
    selected = ranked[0]
    skipped = ", ".join(f"{sig.strategy}:{sig.side.value}" for sig in ranked[1:])
    reason = (
        f"selected {selected.strategy} {selected.side.value} by edge score "
        f"{float(scores.get(selected.strategy, 0.0)):.2f}; skipped {skipped}"
    )
    return selected, reason
