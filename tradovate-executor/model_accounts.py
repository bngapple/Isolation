"""Model account-sized outcomes from a 1-contract backtest result."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

from config import lucid_defaults


@dataclass
class AccountModelSummary:
    account_size: float
    contracts: int
    monthly_loss_limit: float
    raw_scaled_total_pnl: float
    trades_taken: int
    trades_skipped_after_halt: int
    monthly_halt_count: int
    total_pnl: float
    avg_monthly_pnl: float
    best_month: float
    worst_month: float
    best_day: float
    worst_day: float
    max_drawdown: float


def contracts_for_account(account_size: float, base_account_size: float = 25_000.0) -> int:
    return max(1, int(round(account_size / base_account_size)))


def model_account(result_path: str | Path, account_size: float, base_account_size: float = 25_000.0) -> AccountModelSummary:
    payload = json.loads(Path(result_path).read_text())
    contracts = contracts_for_account(account_size, base_account_size=base_account_size)
    monthly_loss_limit = lucid_defaults(account_size)["monthly_loss_limit"]

    monthly_running = 0.0
    running = 0.0
    peak = 0.0
    max_drawdown = 0.0
    current_month = None
    halted = False
    monthly_halt_count = 0
    trades_taken = 0
    trades_skipped = 0
    raw_scaled_total_pnl = 0.0
    daily: dict[str, float] = {}
    monthly: dict[str, float] = {}

    for trade in payload["trades"]:
        entry_time = datetime.fromisoformat(trade["entry_time"])
        month_key = entry_time.strftime("%Y-%m")
        day_key = entry_time.strftime("%Y-%m-%d")

        if current_month != month_key:
            current_month = month_key
            monthly_running = 0.0
            halted = False

        raw_scaled_total_pnl += float(trade["pnl_total"]) * contracts

        if halted:
            trades_skipped += 1
            continue

        scaled_pnl = float(trade["pnl_total"]) * contracts
        monthly_running += scaled_pnl
        running += scaled_pnl
        peak = max(peak, running)
        max_drawdown = min(max_drawdown, running - peak)
        monthly[month_key] = monthly.get(month_key, 0.0) + scaled_pnl
        daily[day_key] = daily.get(day_key, 0.0) + scaled_pnl
        trades_taken += 1

        if monthly_running <= monthly_loss_limit:
            halted = True
            monthly_halt_count += 1

    total_pnl = sum(monthly.values())
    return AccountModelSummary(
        account_size=account_size,
        contracts=contracts,
        monthly_loss_limit=monthly_loss_limit,
        raw_scaled_total_pnl=raw_scaled_total_pnl,
        trades_taken=trades_taken,
        trades_skipped_after_halt=trades_skipped,
        monthly_halt_count=monthly_halt_count,
        total_pnl=total_pnl,
        avg_monthly_pnl=total_pnl / max(len(monthly), 1),
        best_month=max(monthly.values()) if monthly else 0.0,
        worst_month=min(monthly.values()) if monthly else 0.0,
        best_day=max(daily.values()) if daily else 0.0,
        worst_day=min(daily.values()) if daily else 0.0,
        max_drawdown=max_drawdown,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Model account sizes from a backtest result JSON")
    parser.add_argument("result_json", help="Path to backtest result JSON")
    parser.add_argument("--account-size", type=float, action="append", required=True, help="Account size to model. Can repeat.")
    parser.add_argument("--base-account-size", type=float, default=25_000.0)
    parser.add_argument("--output", default="reports/backtests/account_models.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summaries = [model_account(args.result_json, size, base_account_size=args.base_account_size) for size in args.account_size]
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps([asdict(summary) for summary in summaries], indent=2))

    print(f"Result file: {args.result_json}")
    print(f"Account model summary: {out_path}")
    for summary in summaries:
        print(
            f"account=${summary.account_size:,.0f} contracts={summary.contracts} "
            f"monthly_limit=${summary.monthly_loss_limit:,.2f} raw=${summary.raw_scaled_total_pnl:,.2f} "
            f"halted_pnl=${summary.total_pnl:,.2f} "
            f"max_dd=${summary.max_drawdown:,.2f} halts={summary.monthly_halt_count} "
            f"taken={summary.trades_taken} skipped={summary.trades_skipped_after_halt}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
