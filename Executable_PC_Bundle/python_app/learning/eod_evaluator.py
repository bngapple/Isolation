from __future__ import annotations

from learning.backtest_runner import run_day
from utils.logger import get_logger


LOGGER = get_logger("eod_evaluator")


def run(session_date: str, db, parquet_dir: str):
    summary = db.fetchone("SELECT * FROM session_summaries WHERE session_date = ?", (session_date,))
    if summary is None:
        return

    volatility = summary.get("volatility_class")
    decisions = db.fetchall("SELECT * FROM governor_decisions WHERE date(decision_datetime) = ?", (session_date,))
    for decision in decisions:
        mode = decision["mode_decided"]
        if volatility == "high":
            good_call = 1 if mode == "NORMAL" else 0
        elif volatility == "medium":
            good_call = 1 if mode in {"NORMAL", "REDUCED"} else 0
        else:
            good_call = 1 if mode in {"REDUCED", "DEFENSIVE", "HALTED"} else 0
        db.execute(
            "UPDATE governor_decisions SET outcome_good = ?, outcome_scored = 1, outcome_session_range = ?, outcome_strategy_pnl = ? WHERE id = ?",
            (good_call, summary.get("session_range_points"), summary.get("strategy_net_pnl"), decision["id"]),
        )

    replay = run_day(session_date, parquet_dir)
    db.execute("UPDATE session_summaries SET eod_backtest_run = 1 WHERE session_date = ?", (session_date,))
    rolling = db.fetchone(
        "SELECT AVG(outcome_good) AS accuracy, COUNT(*) AS n FROM (SELECT outcome_good FROM governor_decisions WHERE outcome_scored = 1 ORDER BY decision_datetime DESC LIMIT 30)",
    )
    LOGGER.info("eod complete", extra={"data": {"session_date": session_date, "replay": replay, "rolling_accuracy": rolling}})
