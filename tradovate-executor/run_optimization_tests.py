from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from pathlib import Path

import polars as pl

from backtest_data import discover_parquet_files, load_parquet_files
from model_lucid import model_lucid_account
from run_standalone_validate import Variant, run_variant


ROOT = Path(__file__).resolve().parent
REPORTS_DIR = ROOT / "reports" / "backtests"
BASELINE_PATH = REPORTS_DIR / "be_killswitch_trades.json"
DB_PATH = ROOT / "isolation-governor" / "isolation.db"
DATA_INPUTS = [str(ROOT / "data" / "processed" / "MNQ" / "1m")]
ACCOUNT_CONFIGS = {
    "25k": {"contracts": 5, "killswitch_dollar": -750.0, "account_size": 25_000.0},
    "150k": {"contracts": 15, "killswitch_dollar": -3375.0, "account_size": 150_000.0},
}
VARIANT_CHOICES = ["pyramid", "morning_only", "trailing_stop", "partial_tp", "scaled_tp", "reentry", "opposite_exit", "combined_best"]


def load_minute_df(start_date: str = "2020-01-01", end_date: str = "2024-12-31") -> pl.DataFrame:
    minute_df = load_parquet_files(discover_parquet_files(DATA_INPUTS))
    minute_df = minute_df.filter(pl.col("date_et") >= pl.lit(start_date).str.to_date())
    minute_df = minute_df.filter(pl.col("date_et") <= pl.lit(end_date).str.to_date())
    return minute_df


def load_session_classifications() -> dict[str, str]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT session_date, volatility_class FROM session_summaries").fetchall()
    conn.close()
    return {row["session_date"]: row["volatility_class"] for row in rows}


def export_trade_payload(trades, contracts: int) -> list[dict]:
    payload = []
    for trade in trades:
        payload.append(
            {
                "direction": trade.direction,
                "entry_px": trade.entry_px,
                "exit_px": trade.exit_px,
                "contracts": trade.contracts,
                "net_pnl": trade.net_pnl,
                "entry_time": trade.entry_time,
                "exit_time": trade.exit_time,
                "bars_held": trade.bars_held,
                "reason": trade.reason,
                "strategy": trade.strategy,
                "pnl_total": float(trade.net_pnl) / contracts,
            }
        )
    return payload


def build_account_variant(name: str, account_key: str, combined_from: list[str] | None = None) -> Variant:
    cfg = ACCOUNT_CONFIGS[account_key]
    if name == "combined_best":
        combined_from = combined_from or []
        flags = {
            "pyramid": False,
            "morning_only": False,
            "trailing_stop": False,
            "partial_tp": False,
            "scaled_tp": False,
            "reentry": False,
            "opposite_exit": False,
        }
        for item in combined_from:
            flags[item] = True
        name_for_variant = "combined_best"
    else:
        flags = {key: key == name for key in ["pyramid", "morning_only", "trailing_stop", "partial_tp", "scaled_tp", "reentry", "opposite_exit"]}
        name_for_variant = name

    partial_qty = 2 if account_key == "25k" else 7
    pyramid_add_qty = 2 if account_key == "25k" else 5
    pyramid_max_qty = 7 if account_key == "25k" else 20

    return Variant(
        name=name_for_variant,
        break_even=True,
        killswitch=True,
        atr_filter=False,
        contracts=cfg["contracts"],
        killswitch_dollar=cfg["killswitch_dollar"],
        be_minutes=5,
        morning_only=flags["morning_only"],
        pyramid=flags["pyramid"],
        pyramid_add_qty=pyramid_add_qty,
        pyramid_max_qty=pyramid_max_qty,
        trailing_stop=flags["trailing_stop"],
        partial_tp=flags["partial_tp"],
        partial_tp_qty=partial_qty,
        scaled_tp=flags["scaled_tp"],
        reentry=flags["reentry"],
        opposite_exit=flags["opposite_exit"],
    )


def validate_baseline(minute_df: pl.DataFrame) -> tuple[bool, dict]:
    file_payload = json.loads(BASELINE_PATH.read_text())
    runner_variant = Variant("be_killswitch", break_even=True, killswitch=True, atr_filter=False)
    runner_metrics, runner_trades = run_variant(minute_df, runner_variant)

    file_trade_count = len(file_payload["trades"])
    runner_trade_count = len(runner_trades)
    file_total_pnl = float(file_payload["summary"]["pnl"])
    runner_total_pnl = float(runner_metrics["pnl"])

    row_match = True
    if runner_trade_count == file_trade_count:
        runner_export = export_trade_payload(runner_trades, 3)
        for lhs, rhs in zip(runner_export, file_payload["trades"]):
            if (
                lhs["entry_time"] != rhs["entry_time"]
                or lhs["exit_time"] != rhs["exit_time"]
                or lhs["reason"] != rhs["reason"]
                or abs(float(lhs["pnl_total"]) - float(rhs["pnl_total"])) > 1e-6
            ):
                row_match = False
                break
    else:
        row_match = False

    count_match = runner_trade_count == file_trade_count
    pnl_match = abs(runner_total_pnl - file_total_pnl) <= 1.0
    ok = count_match and pnl_match and row_match
    summary = {
        "runner_trade_count": runner_trade_count,
        "file_trade_count": file_trade_count,
        "count_match": count_match,
        "runner_total_pnl": runner_total_pnl,
        "file_total_pnl": file_total_pnl,
        "pnl_match": pnl_match,
        "row_match": row_match,
    }
    return ok, summary


def baseline_metrics_by_account() -> dict[str, dict]:
    payload = json.loads(BASELINE_PATH.read_text())
    out = {
        "25k": {
            "total_pnl": float(payload["summary"]["pnl"]),
            "monthly_avg": float(payload["summary"]["monthly_avg"]),
            "max_drawdown": float(payload["summary"]["max_dd"]),
            "worst_day": float(payload["summary"]["worst_day"]),
            "trade_count": int(payload["summary"]["n"]),
            "killswitch_triggers": int(payload["summary"].get("killswitch_triggers", 0)),
        }
    }
    # 150k baseline = same trades scaled to 15 contracts from file's per-contract pnl_total
    daily = {}
    for trade in payload["trades"]:
        day = str(trade["entry_time"])[:10]
        daily.setdefault(day, 0.0)
        daily[day] += float(trade["pnl_total"]) * 15
    monthly = {}
    total = 0.0
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for day in sorted(daily):
        pnl = daily[day]
        total += pnl
        monthly.setdefault(day[:7], 0.0)
        monthly[day[:7]] += pnl
        cum += pnl
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)
    out["150k"] = {
        "total_pnl": total,
        "monthly_avg": total / max(len(monthly), 1),
        "max_drawdown": max_dd,
        "worst_day": min(daily.values()) if daily else 0.0,
        "trade_count": int(payload["summary"]["n"]),
        "killswitch_triggers": 0,
    }
    return out


def run_variant_for_both_accounts(minute_df: pl.DataFrame, name: str, combined_from: list[str] | None = None) -> dict:
    session_classes = load_session_classifications()
    account_results = {}
    for account_key in ("25k", "150k"):
        variant = build_account_variant(name, account_key, combined_from=combined_from)
        metrics, trades = run_variant(minute_df, variant, session_classifications=session_classes)
        account_results[account_key] = {
            "summary": {
                "total_pnl": float(metrics["pnl"]),
                "monthly_avg": float(metrics["monthly_avg"]),
                "max_drawdown": float(metrics["max_dd"]),
                "worst_day": float(metrics["worst_day"]),
                "trade_count": int(metrics["n"]),
                "killswitch_triggers": int(metrics.get("killswitch_triggers", 0)),
            },
            "trades": export_trade_payload(trades, ACCOUNT_CONFIGS[account_key]["contracts"]),
        }

    payload = {"variant": name, "combined_from": combined_from or [], "account_results": account_results}
    out_path = REPORTS_DIR / f"opt_{name}_trades.json"
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    payload["output_path"] = str(out_path)
    return payload


def lucid_summary(variant_payload: dict, account_key: str) -> dict:
    contracts = ACCOUNT_CONFIGS[account_key]["contracts"]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump({"trades": variant_payload["account_results"][account_key]["trades"]}, handle)
        temp_path = handle.name
    model = model_lucid_account(temp_path, account_size=ACCOUNT_CONFIGS[account_key]["account_size"], base_account_size=25_000.0, consistency_limit_pct=50.0, fixed_contracts=contracts)
    return {
        "direct_pass": model.direct_eval.passed,
        "mc_blowup_rate": model.mc_eval_baseline.blowup_rate,
        "funded_survived": model.funded_path.survived,
        "funded_max_dd": model.funded_path.max_drawdown,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Optimization suite using shared standalone validator engine")
    parser.add_argument("--validate-baseline", action="store_true")
    parser.add_argument("--variant", choices=VARIANT_CHOICES)
    parser.add_argument("--combined-from", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    minute_df = load_minute_df()

    if args.validate_baseline:
        ok, summary = validate_baseline(minute_df)
        print(f"Baseline trade count (runner):   {summary['runner_trade_count']}")
        print(f"Baseline trade count (file):     {summary['file_trade_count']}")
        print(f"Match: {'YES' if summary['count_match'] else 'NO'}")
        print()
        print(f"Baseline total PnL (runner):     ${summary['runner_total_pnl']:,.2f}")
        print(f"Baseline total PnL (file):       ${summary['file_total_pnl']:,.2f}")
        print(f"Match: {'YES' if summary['pnl_match'] else 'NO'} (within $1.00)")
        print()
        if ok:
            print("Baseline matches be_killswitch_trades.json exactly — proceeding to variants")
            return 0
        print("Baseline still does not match — stopping here")
        return 1

    if args.variant:
        payload = run_variant_for_both_accounts(minute_df, args.variant, [v for v in args.combined_from.split(",") if v])
        summary_payload = {
            "variant": payload["variant"],
            "combined_from": payload["combined_from"],
            "output_path": payload["output_path"],
            "account_results": {
                acct: payload["account_results"][acct]["summary"] for acct in ("25k", "150k")
            },
        }
        print(json.dumps(summary_payload))
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
