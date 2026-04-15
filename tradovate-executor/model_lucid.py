"""Lucid eval and funded modeling from a backtest result JSON."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median

import numpy as np

from config import lucid_defaults
from model_accounts import contracts_for_account


@dataclass
class LucidDirectEval:
    passed: bool
    blown: bool
    days_processed: int
    days_to_pass: int | None
    end_pnl: float
    peak_pnl: float
    max_drawdown: float
    best_day: float
    consistency_pct_at_pass: float | None
    consistency_passed: bool | None


@dataclass
class LucidMonteCarloEval:
    simulations: int
    pass_rate: float
    blowup_rate: float
    median_days_to_pass: int | None
    consistency_pass_rate: float


@dataclass
class LucidFundedPath:
    survived: bool
    blown: bool
    end_pnl: float
    peak_pnl: float
    max_drawdown: float
    best_day: float
    worst_day: float
    profitable_days: int
    losing_days: int
    days_processed: int


@dataclass
class LucidAccountModel:
    account_size: float
    contracts: int
    pnl_scale: float
    drawdown_limit: float
    profit_target: float
    consistency_limit_pct: float
    direct_eval: LucidDirectEval
    mc_eval_baseline: LucidMonteCarloEval
    mc_eval_conservative: LucidMonteCarloEval
    funded_path: LucidFundedPath


def load_scaled_daily_pnl(result_json: str | Path, contracts: int, pnl_scale: float = 1.0) -> list[tuple[str, float]]:
    payload = json.loads(Path(result_json).read_text())
    daily = defaultdict(float)
    for trade in payload["trades"]:
        day_key = str(trade["entry_time"])[:10]
        pnl = trade.get("pnl_total", trade.get("net_pnl"))
        if pnl is None:
            raise KeyError("Trade record is missing pnl_total/net_pnl")
        daily[day_key] += float(pnl) * contracts * pnl_scale
    return sorted(daily.items())


def evaluate_lucid_path(
    daily_pnl: list[float],
    drawdown_limit: float,
    profit_target: float,
    consistency_limit_pct: float = 50.0,
) -> LucidDirectEval:
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    best_day = 0.0
    days_to_pass = None
    consistency_pct_at_pass = None
    consistency_passed = None

    for idx, pnl in enumerate(daily_pnl, start=1):
        cum += pnl
        if pnl > best_day:
            best_day = pnl
        if cum > peak:
            peak = cum
        dd = cum - peak
        if dd < max_dd:
            max_dd = dd
        if dd <= drawdown_limit:
            return LucidDirectEval(
                passed=False,
                blown=True,
                days_processed=idx,
                days_to_pass=None,
                end_pnl=cum,
                peak_pnl=peak,
                max_drawdown=max_dd,
                best_day=best_day,
                consistency_pct_at_pass=None,
                consistency_passed=None,
            )

        if cum >= profit_target:
            consistency_pct = (best_day / cum * 100.0) if cum > 0 else None
            consistency_ok = consistency_pct is not None and consistency_pct <= consistency_limit_pct
            if consistency_ok:
                days_to_pass = idx
                consistency_pct_at_pass = consistency_pct
                consistency_passed = True
                return LucidDirectEval(
                    passed=True,
                    blown=False,
                    days_processed=idx,
                    days_to_pass=days_to_pass,
                    end_pnl=cum,
                    peak_pnl=peak,
                    max_drawdown=max_dd,
                    best_day=best_day,
                    consistency_pct_at_pass=consistency_pct_at_pass,
                    consistency_passed=True,
                )
            consistency_pct_at_pass = consistency_pct
            consistency_passed = False

    return LucidDirectEval(
        passed=False,
        blown=False,
        days_processed=len(daily_pnl),
        days_to_pass=None,
        end_pnl=cum,
        peak_pnl=peak,
        max_drawdown=max_dd,
        best_day=best_day,
        consistency_pct_at_pass=consistency_pct_at_pass,
        consistency_passed=consistency_passed,
    )


def run_monte_carlo_eval(
    daily_pnl: list[float],
    drawdown_limit: float,
    profit_target: float,
    consistency_limit_pct: float,
    simulations: int,
    pnl_mult: float,
) -> LucidMonteCarloEval:
    if not daily_pnl:
        return LucidMonteCarloEval(simulations, 0.0, 1.0, None, 0.0)

    pass_count = 0
    blow_count = 0
    consistency_pass_count = 0
    days_to_pass: list[int] = []
    n_days = len(daily_pnl)

    for sim in range(simulations):
        rng = np.random.RandomState(sim)
        order = rng.permutation(n_days)
        shuffled = [daily_pnl[i] * pnl_mult for i in order]
        result = evaluate_lucid_path(shuffled, drawdown_limit, profit_target, consistency_limit_pct)
        if result.blown:
            blow_count += 1
        if result.consistency_passed:
            consistency_pass_count += 1
        if result.passed:
            pass_count += 1
            if result.days_to_pass is not None:
                days_to_pass.append(result.days_to_pass)

    return LucidMonteCarloEval(
        simulations=simulations,
        pass_rate=pass_count / simulations,
        blowup_rate=blow_count / simulations,
        median_days_to_pass=int(median(days_to_pass)) if days_to_pass else None,
        consistency_pass_rate=consistency_pass_count / simulations,
    )


def evaluate_funded_path(daily_pnl: list[float], drawdown_limit: float) -> LucidFundedPath:
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    best_day = 0.0
    worst_day = 0.0
    profitable_days = 0
    losing_days = 0

    for idx, pnl in enumerate(daily_pnl, start=1):
        cum += pnl
        if pnl > 0:
            profitable_days += 1
        elif pnl < 0:
            losing_days += 1
        best_day = max(best_day, pnl)
        worst_day = min(worst_day, pnl)
        if cum > peak:
            peak = cum
        dd = cum - peak
        if dd < max_dd:
            max_dd = dd
        if dd <= drawdown_limit:
            return LucidFundedPath(
                survived=False,
                blown=True,
                end_pnl=cum,
                peak_pnl=peak,
                max_drawdown=max_dd,
                best_day=best_day,
                worst_day=worst_day,
                profitable_days=profitable_days,
                losing_days=losing_days,
                days_processed=idx,
            )

    return LucidFundedPath(
        survived=True,
        blown=False,
        end_pnl=cum,
        peak_pnl=peak,
        max_drawdown=max_dd,
        best_day=best_day,
        worst_day=worst_day,
        profitable_days=profitable_days,
        losing_days=losing_days,
        days_processed=len(daily_pnl),
    )


def model_lucid_account(
    result_json: str | Path,
    account_size: float,
    base_account_size: float = 25_000.0,
    simulations: int = 5000,
    conservative_pnl_mult: float = 0.70,
    consistency_limit_pct: float = 50.0,
    pnl_scale: float = 1.0,
    fixed_contracts: int | None = None,
) -> LucidAccountModel:
    defaults = lucid_defaults(account_size)
    contracts = fixed_contracts if fixed_contracts is not None else contracts_for_account(account_size, base_account_size=base_account_size)
    drawdown_limit = float(defaults["max_drawdown"])
    profit_target = float(defaults["profit_target"])
    daily = load_scaled_daily_pnl(result_json, contracts, pnl_scale=pnl_scale)
    daily_values = [pnl for _, pnl in daily]

    direct_eval = evaluate_lucid_path(daily_values, drawdown_limit, profit_target, consistency_limit_pct)
    mc_eval_baseline = run_monte_carlo_eval(
        daily_values,
        drawdown_limit,
        profit_target,
        consistency_limit_pct,
        simulations,
        1.0,
    )
    mc_eval_conservative = run_monte_carlo_eval(
        daily_values,
        drawdown_limit,
        profit_target,
        consistency_limit_pct,
        simulations,
        conservative_pnl_mult,
    )
    funded_path = evaluate_funded_path(daily_values, drawdown_limit)

    return LucidAccountModel(
        account_size=account_size,
        contracts=contracts,
        pnl_scale=pnl_scale,
        drawdown_limit=drawdown_limit,
        profit_target=profit_target,
        consistency_limit_pct=consistency_limit_pct,
        direct_eval=direct_eval,
        mc_eval_baseline=mc_eval_baseline,
        mc_eval_conservative=mc_eval_conservative,
        funded_path=funded_path,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Model Lucid eval/funded outcomes from a backtest result JSON")
    parser.add_argument("result_json")
    parser.add_argument("--account-size", type=float, action="append", required=True)
    parser.add_argument("--base-account-size", type=float, default=25_000.0)
    parser.add_argument("--simulations", type=int, default=5000)
    parser.add_argument("--conservative-pnl-mult", type=float, default=0.70)
    parser.add_argument("--consistency-limit-pct", type=float, default=50.0)
    parser.add_argument("--pnl-scale", type=float, default=1.0, help="Additional multiplier applied to all trade P&L")
    parser.add_argument("--fixed-contracts", type=int, default=None, help="Override account-size-derived contract multiplier")
    parser.add_argument("--output", default="reports/backtests/lucid_models.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    models = [
        model_lucid_account(
            args.result_json,
            account_size=size,
            base_account_size=args.base_account_size,
            simulations=args.simulations,
            conservative_pnl_mult=args.conservative_pnl_mult,
            consistency_limit_pct=args.consistency_limit_pct,
            pnl_scale=args.pnl_scale,
            fixed_contracts=args.fixed_contracts,
        )
        for size in args.account_size
    ]
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps([asdict(model) for model in models], indent=2))

    print(f"Result file: {args.result_json}")
    print(f"Lucid model summary: {out_path}")
    for model in models:
        de = model.direct_eval
        mc = model.mc_eval_baseline
        fp = model.funded_path
        print(
            f"account=${model.account_size:,.0f} contracts={model.contracts} target=${model.profit_target:,.0f} "
            f"scale={model.pnl_scale:.4f} dd=${model.drawdown_limit:,.0f} direct_pass={de.passed} direct_blow={de.blown} "
            f"mc_pass={mc.pass_rate:.1%} mc_blow={mc.blowup_rate:.1%} funded_survived={fp.survived}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
