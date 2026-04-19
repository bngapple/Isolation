from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import polars as pl

from backtest_data import discover_parquet_files, load_parquet_files
from config import POINT_VALUE
from run_standalone_validate import Variant, run_variant


ROOT = Path(__file__).resolve().parent
DATA_INPUTS = [str(ROOT / "data" / "processed" / "MNQ" / "1m")]
START_DATE = "2020-01-01"
END_DATE = "2026-04-13"
DAILY_CSV = ROOT / "reports" / "daily_performance_v3_live_configs.csv"
DETAIL_CSV = ROOT / "reports" / "trade_detail_v3_live_configs.csv"


def gross_before_costs(trade) -> float:
    return (trade.exit_px - trade.entry_px) * trade.direction * POINT_VALUE * trade.contracts


def direction_label(value: int) -> str:
    return "LONG" if int(value) > 0 else "SHORT"


def export_account_rows(account_label: str, contracts: int, killswitch: float, all_dates: list[str]):
    variant = Variant(
        name=f"trailing_stop_{account_label}",
        break_even=True,
        killswitch=True,
        atr_filter=False,
        contracts=contracts,
        killswitch_dollar=killswitch,
        trailing_stop=True,
    )

    minute_df = load_parquet_files(discover_parquet_files(DATA_INPUTS))
    minute_df = minute_df.filter(pl.col("date_et") >= pl.lit(START_DATE).str.to_date())
    minute_df = minute_df.filter(pl.col("date_et") <= pl.lit(END_DATE).str.to_date())
    _, trades = run_variant(minute_df, variant)

    by_day = defaultdict(list)
    for trade in trades:
        by_day[str(trade.entry_time)[:10]].append(trade)

    daily_rows = []
    detail_rows = []
    for day in all_dates:
        day_trades = by_day.get(day, [])
        pnls = [float(t.net_pnl) for t in day_trades]
        grosses = [gross_before_costs(t) for t in day_trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        net = sum(pnls)

        daily_rows.append(
            {
                "account": account_label,
                "contracts": contracts,
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
                "killswitch_triggered": 1 if net <= killswitch else 0,
                "day_result": "WIN" if net > 0 else "LOSS" if net < 0 else "FLAT",
            }
        )

        for idx, trade in enumerate(day_trades, start=1):
            detail_rows.append(
                {
                    "account": account_label,
                    "contracts": contracts,
                    "date": day,
                    "trade_num": idx,
                    "entry_time": str(trade.entry_time),
                    "exit_time": str(trade.exit_time),
                    "direction": direction_label(trade.direction),
                    "entry_px": trade.entry_px,
                    "exit_px": trade.exit_px,
                    "net_pnl": round(float(trade.net_pnl), 2),
                    "reason": trade.reason,
                }
            )

    return daily_rows, detail_rows, len(trades)


def main() -> int:
    minute_df = load_parquet_files(discover_parquet_files(DATA_INPUTS))
    minute_df = minute_df.filter(pl.col("date_et") >= pl.lit(START_DATE).str.to_date())
    minute_df = minute_df.filter(pl.col("date_et") <= pl.lit(END_DATE).str.to_date())
    all_dates = sorted(str(d) for d in minute_df.select("date_et").unique().sort("date_et")["date_et"].to_list())

    daily_25, detail_25, trades_25 = export_account_rows("25K", 6, -750.0, all_dates)
    daily_150, detail_150, trades_150 = export_account_rows("150K", 20, -3375.0, all_dates)

    DAILY_CSV.parent.mkdir(parents=True, exist_ok=True)
    with DAILY_CSV.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "account",
                "contracts",
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
        writer.writerows(daily_25)
        writer.writerows(daily_150)

    with DETAIL_CSV.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "account",
                "contracts",
                "date",
                "trade_num",
                "entry_time",
                "exit_time",
                "direction",
                "entry_px",
                "exit_px",
                "net_pnl",
                "reason",
            ],
        )
        writer.writeheader()
        writer.writerows(detail_25)
        writer.writerows(detail_150)

    print(f"Exported {len(all_dates)} trading dates per account")
    print(f"25K trades exported: {trades_25}")
    print(f"150K trades exported: {trades_150}")
    print(f"Daily CSV: {DAILY_CSV}")
    print(f"Trade CSV: {DETAIL_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
