from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

from config import POINT_VALUE


ROOT = Path(__file__).resolve().parent
INPUT_PATH = ROOT / "reports" / "backtests" / "trailing_stop_trades.json"
DAILY_CSV = ROOT / "reports" / "daily_performance.csv"
DETAIL_CSV = ROOT / "reports" / "trade_detail.csv"


def direction_label(value: int) -> str:
    return "LONG" if int(value) > 0 else "SHORT"


def gross_before_costs(trade: dict) -> float:
    direction = int(trade["direction"])
    entry_px = float(trade["entry_px"])
    exit_px = float(trade["exit_px"])
    contracts = int(trade["contracts"])
    return (exit_px - entry_px) * direction * POINT_VALUE * contracts


def main() -> int:
    payload = json.loads(INPUT_PATH.read_text())
    trades = payload["trades"]

    DAILY_CSV.parent.mkdir(parents=True, exist_ok=True)

    by_day: dict[str, list[dict]] = defaultdict(list)
    for trade in trades:
        by_day[str(trade["entry_time"])[:10]].append(trade)

    daily_rows = []
    best_day = (None, float("-inf"))
    worst_day = (None, float("inf"))
    flagged_bad_days = 0

    for day in sorted(by_day):
        day_trades = by_day[day]
        pnls = [float(t["net_pnl"]) for t in day_trades]
        grosses = [gross_before_costs(t) for t in day_trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        net = sum(pnls)
        row = {
            "date": day,
            "trade_count": len(day_trades),
            "wins": len(wins),
            "losses": len(losses),
            "gross_pnl": round(sum(grosses), 2),
            "net_pnl": round(net, 2),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
            "win_rate": round((len(wins) / len(day_trades) * 100.0) if day_trades else 0.0, 2),
            "max_single_loss": round(min(pnls), 2) if pnls else 0.0,
            "max_single_win": round(max(pnls), 2) if pnls else 0.0,
            "killswitch_triggered": 1 if net <= -750.0 else 0,
            "day_result": "WIN" if net > 0 else "LOSS" if net < 0 else "FLAT",
        }
        daily_rows.append(row)
        if net < -500.0:
            flagged_bad_days += 1
        if net > best_day[1]:
            best_day = (day, net)
        if net < worst_day[1]:
            worst_day = (day, net)

    with DAILY_CSV.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "date",
                "trade_count",
                "wins",
                "losses",
                "gross_pnl",
                "net_pnl",
                "avg_win",
                "avg_loss",
                "win_rate",
                "max_single_loss",
                "max_single_win",
                "killswitch_triggered",
                "day_result",
            ],
        )
        writer.writeheader()
        writer.writerows(daily_rows)

    with DETAIL_CSV.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "date",
                "trade_num",
                "entry_time",
                "exit_time",
                "direction",
                "contracts",
                "entry_px",
                "exit_px",
                "net_pnl",
                "reason",
            ],
        )
        writer.writeheader()
        for day in sorted(by_day):
            for idx, trade in enumerate(by_day[day], start=1):
                writer.writerow(
                    {
                        "date": day,
                        "trade_num": idx,
                        "entry_time": trade["entry_time"],
                        "exit_time": trade["exit_time"],
                        "direction": direction_label(trade["direction"]),
                        "contracts": trade["contracts"],
                        "entry_px": trade["entry_px"],
                        "exit_px": trade["exit_px"],
                        "net_pnl": round(float(trade["net_pnl"]), 2),
                        "reason": trade["reason"],
                    }
                )

    print(f"Total days exported: {len(daily_rows)}")
    print(f"Total trades exported: {len(trades)}")
    print(f"Best day: {best_day[0]} {best_day[1]:.2f}")
    print(f"Worst day: {worst_day[0]} {worst_day[1]:.2f}")
    print(f"Days with net_pnl < -500: {flagged_bad_days}")
    print(f"Daily CSV: {DAILY_CSV}")
    print(f"Trade detail CSV: {DETAIL_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
