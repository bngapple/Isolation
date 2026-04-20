from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"
BACKTESTS = REPORTS / "backtests"


def direction_label(direction: int) -> str:
    return "LONG" if int(direction) > 0 else "SHORT"


def export_csv(source_json: Path, output_csv: Path, contracts: int = 5) -> None:
    payload = json.loads(source_json.read_text())
    trades = payload["trades"]

    by_day = defaultdict(list)
    for trade in trades:
        by_day[str(trade["entry_time"])[:10]].append(trade)

    rows = []
    cumulative_total = 0.0
    for day in sorted(by_day):
        day_trades = sorted(by_day[day], key=lambda t: str(t["entry_time"]))
        cumulative_day = 0.0
        for idx, trade in enumerate(day_trades, start=1):
            pnl = round(float(trade["pnl_total"]) * contracts, 2)
            cumulative_day = round(cumulative_day + pnl, 2)
            cumulative_total = round(cumulative_total + pnl, 2)
            rows.append(
                {
                    "date": day,
                    "trade_num": idx,
                    "direction": direction_label(trade["direction"]),
                    "entry_time": trade["entry_time"],
                    "exit_time": trade["exit_time"],
                    "entry_price": round(float(trade["entry_px"]), 2),
                    "exit_price": round(float(trade["exit_px"]), 2),
                    "exit_reason": trade["reason"],
                    "contracts": contracts,
                    "pnl": pnl,
                    "cumulative_daily_pnl": cumulative_day,
                    "cumulative_total_pnl": cumulative_total,
                }
            )

        rows.append(
            {
                "date": day,
                "trade_num": "DAILY_SUMMARY",
                "direction": "-",
                "entry_time": "-",
                "exit_time": "-",
                "entry_price": "-",
                "exit_price": "-",
                "exit_reason": "-",
                "contracts": f"trades={len(day_trades)}",
                "pnl": round(cumulative_day, 2),
                "cumulative_daily_pnl": "-",
                "cumulative_total_pnl": cumulative_total,
            }
        )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "date",
                "trade_num",
                "direction",
                "entry_time",
                "exit_time",
                "entry_price",
                "exit_price",
                "exit_reason",
                "contracts",
                "pnl",
                "cumulative_daily_pnl",
                "cumulative_total_pnl",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    export_csv(BACKTESTS / "be_killswitch_trades.json", REPORTS / "daily_breakdown_baseline.csv")
    export_csv(BACKTESTS / "v3_legacy_trades_for_mc.json", REPORTS / "daily_breakdown_v3.csv")
    print(REPORTS / "daily_breakdown_baseline.csv")
    print(REPORTS / "daily_breakdown_v3.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
