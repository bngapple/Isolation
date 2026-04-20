from __future__ import annotations

from datetime import date, datetime, timedelta

from learning.backtest_runner import run_day
from utils.logger import get_logger


LOGGER = get_logger("corpus_builder")


def build(start_date: str, end_date: str, parquet_dir: str, db):
    start = datetime.fromisoformat(start_date).date()
    end = datetime.fromisoformat(end_date).date()

    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)

    results = []
    total = len(days)
    skipped = 0
    for idx, day in enumerate(days, start=1):
        if idx % 50 == 0 or idx == total:
            print(f"Processing {day.isoformat()}... ({idx}/{total})")
        result = run_day(day.isoformat(), parquet_dir)
        if result["trade_count"] == 0 and result["session_range"] == 0.0:
            skipped += 1
            continue
        results.append(result)

    if not results:
        return {
            "total_days": total,
            "processed_days": 0,
            "skipped_days": skipped,
            "high": 0,
            "medium": 0,
            "low": 0,
            "p25": None,
            "p75": None,
        }

    ranges = sorted(r["session_range"] for r in results)
    p25 = ranges[max(0, int(len(ranges) * 0.25) - 1)]
    p75 = ranges[max(0, int(len(ranges) * 0.75) - 1)]
    db.execute("INSERT OR REPLACE INTO config_values (key, value) VALUES (?, ?)", ("range_p25", str(p25)))
    db.execute("INSERT OR REPLACE INTO config_values (key, value) VALUES (?, ?)", ("range_p75", str(p75)))

    high = 0
    medium = 0
    low = 0
    for result in results:
        if result["session_range"] > p75:
            volatility = "high"
            high += 1
        elif result["session_range"] < p25:
            volatility = "low"
            low += 1
        else:
            volatility = "medium"
            medium += 1
        db.execute(
            """
            INSERT OR REPLACE INTO session_summaries
            (session_date, session_range_points, volatility_class, strategy_gross_pnl, strategy_net_pnl, trade_count, win_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result["date"],
                result["session_range"],
                volatility,
                result["total_pnl"],
                result["total_pnl"],
                result["trade_count"],
                result["win_count"],
            ),
        )

    return {
        "total_days": total,
        "processed_days": len(results),
        "skipped_days": skipped,
        "high": high,
        "medium": medium,
        "low": low,
        "p25": p25,
        "p75": p75,
    }
