"""Run account-level weighted ensemble backtests over a weight grid."""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from backtest_data import discover_parquet_files, load_parquet_files
from backtest_engine import (
    BacktestTrade,
    EnsembleRunResult,
    EnsembleSummary,
    MinuteBar,
    StrategyBacktester,
    write_result_files,
)
from config import AppConfig, POINT_VALUE
from signal_engine import Side, Signal, SignalEngine


@dataclass
class EnsembleCandidate:
    weights: dict[str, float]
    total_pnl: float
    avg_monthly_pnl: float
    max_drawdown: float
    win_rate: float
    trades: int
    result_json: str


class EnsembleBacktester(StrategyBacktester):
    def __init__(self, config: AppConfig, weights: dict[str, float], threshold: float = 0.0, slippage_points: float = 0.0):
        super().__init__(config=config, strategy="RSI", slippage_points=slippage_points)
        self.strategy = "ENSEMBLE"
        self.weights = {name: float(weights.get(name, 0.0)) for name in ("RSI", "IB", "MOM")}
        self.threshold = float(threshold)
        self.vote_engine = SignalEngine(config.rsi, config.ib, config.mom, config.session)

    def run(self, minute_df, data_sources=None) -> EnsembleRunResult:
        minute_bars = self._build_minute_bars(minute_df)
        if not minute_bars:
            raise ValueError("No minute bars available for ensemble backtest")

        bars_15m = self._aggregate_15m(minute_bars)
        pending_entry: Signal | None = None
        active_trade = None
        closed_trades: list[BacktestTrade] = []
        daily_running = 0.0
        monthly_running = 0.0
        current_day = None
        current_month = None

        for bar_index, bar in enumerate(bars_15m):
            first_minute = minute_bars[bar.start_idx]
            if current_day != first_minute.timestamp.date():
                current_day = first_minute.timestamp.date()
                daily_running = 0.0
            month_key = (first_minute.timestamp.year, first_minute.timestamp.month)
            if current_month != month_key:
                current_month = month_key
                monthly_running = 0.0

            if pending_entry and active_trade is None:
                active_trade = self._enter_trade(pending_entry, first_minute)
                pending_entry = None

            if active_trade is not None:
                closed = self._manage_intrabar(active_trade, minute_bars, bar)
                if closed is not None:
                    daily_running += closed.pnl_total
                    monthly_running += closed.pnl_total
                    closed.daily_pnl_after = daily_running
                    closed.monthly_pnl_after = monthly_running
                    closed_trades.append(closed)
                    active_trade = None

            self._ingest_15m(bar)

            if active_trade is not None:
                bars_held = max(0, bar_index - active_trade.entry_bar_index + 1)
                if bars_held >= active_trade.max_hold_bars:
                    closed = self._close_trade(active_trade, bar.timestamp, bar.close, "MaxHold")
                    daily_running += closed.pnl_total
                    monthly_running += closed.pnl_total
                    closed.daily_pnl_after = daily_running
                    closed.monthly_pnl_after = monthly_running
                    closed_trades.append(closed)
                    active_trade = None

            if active_trade is None and pending_entry is None:
                raw_signals = [sig for sig in self.vote_engine.evaluate(self.market_data.state) if sig.contracts > 0]
                ensemble_signal = self._combine_signals(raw_signals)
                if ensemble_signal is not None:
                    pending_entry = ensemble_signal

            next_bar = bars_15m[bar_index + 1] if bar_index + 1 < len(bars_15m) else None
            is_last_bar_of_day = next_bar is None or next_bar.timestamp.date() != bar.timestamp.date()
            if is_last_bar_of_day:
                remainder = self._session_remainder_minutes(minute_bars, bar.end_idx)
                if active_trade is not None:
                    closed = self._manage_remaining_minutes(active_trade, remainder)
                    if closed is None and remainder:
                        last_minute = remainder[-1]
                        closed = self._close_trade(active_trade, last_minute.timestamp, last_minute.close, "EOD")
                    if closed is not None:
                        daily_running += closed.pnl_total
                        monthly_running += closed.pnl_total
                        closed.daily_pnl_after = daily_running
                        closed.monthly_pnl_after = monthly_running
                        closed_trades.append(closed)
                        active_trade = None
                pending_entry = None

        if active_trade is not None:
            last_minute = minute_bars[-1]
            closed = self._close_trade(active_trade, last_minute.timestamp, last_minute.close, "DataEnd")
            daily_running += closed.pnl_total
            monthly_running += closed.pnl_total
            closed.daily_pnl_after = daily_running
            closed.monthly_pnl_after = monthly_running
            closed_trades.append(closed)

        self._loop.close()
        summary = self._summarize_ensemble(closed_trades)
        return EnsembleRunResult(
            strategy="ENSEMBLE",
            config_path="config.json",
            data_sources=list(data_sources or []),
            trades=closed_trades,
            summary=summary,
        )

    def _combine_signals(self, signals: list[Signal]) -> Signal | None:
        if not signals:
            return None

        score = 0.0
        for sig in signals:
            direction = 1.0 if sig.side == Side.BUY else -1.0
            score += self.weights.get(sig.strategy, 0.0) * direction

        if score > self.threshold:
            side = Side.BUY
        elif score < -self.threshold:
            side = Side.SELL
        else:
            return None

        aligned = [sig for sig in signals if sig.side == side and self.weights.get(sig.strategy, 0.0) > 0]
        if not aligned:
            return None

        total_weight = sum(self.weights[sig.strategy] for sig in aligned)
        stop = sum(self.weights[sig.strategy] * sig.stop_loss_pts for sig in aligned) / total_weight
        target = sum(self.weights[sig.strategy] * sig.take_profit_pts for sig in aligned) / total_weight
        hold = round(sum(self.weights[sig.strategy] * sig.max_hold_bars for sig in aligned) / total_weight)
        bar_timestamp = aligned[0].bar_timestamp
        signal_price = aligned[0].signal_price
        reason = ", ".join(f"{sig.strategy}:{sig.side.value}" for sig in aligned)

        return Signal(
            strategy="ENSEMBLE",
            side=side,
            contracts=1,
            stop_loss_pts=float(stop),
            take_profit_pts=float(target),
            max_hold_bars=max(1, int(hold)),
            reason=f"score={score:.3f} | {reason}",
            bar_timestamp=bar_timestamp,
            signal_price=signal_price,
        )

    def _enter_trade(self, signal: Signal, minute_bar: MinuteBar):
        trade = super()._enter_trade(signal, minute_bar)
        trade.max_hold_bars = signal.max_hold_bars
        trade.entry_bar_index = self.vote_engine._bar_count + 1
        return trade

    def _close_trade(self, trade, exit_time, raw_exit_price, exit_reason):
        closed = super()._close_trade(trade, exit_time, raw_exit_price, exit_reason)
        bars_held = max(0, self.vote_engine._bar_count - trade.entry_bar_index + 1)
        closed.bars_held = bars_held
        closed.strategy = "ENSEMBLE"
        return closed

    def _summarize_ensemble(self, trades: list[BacktestTrade]) -> EnsembleSummary:
        base = super()._summarize(trades)
        return EnsembleSummary(**asdict(base), weights=self.weights, threshold=self.threshold)


def generate_weight_grid(step: float) -> list[dict[str, float]]:
    units = int(round(1.0 / step))
    combos: list[dict[str, float]] = []
    for rsi_units in range(units + 1):
        for ib_units in range(units + 1 - rsi_units):
            mom_units = units - rsi_units - ib_units
            weights = {
                "RSI": round(rsi_units * step, 6),
                "IB": round(ib_units * step, 6),
                "MOM": round(mom_units * step, 6),
            }
            if sum(weights.values()) > 0:
                combos.append(weights)
    return combos


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run weighted ensemble backtests")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--data", action="append", default=[], help="Parquet file, directory, or glob")
    parser.add_argument("--step", type=float, default=0.25, help="Weight grid step. Example: 0.25 -> 15 combinations")
    parser.add_argument("--threshold", type=float, default=0.0, help="Minimum absolute ensemble score to enter")
    parser.add_argument("--top", type=int, default=10, help="How many top candidates to print")
    parser.add_argument("--reports-dir", default="reports/backtests")
    parser.add_argument("--slippage-points", type=float, default=0.0)
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
    logging.getLogger("market_data").setLevel(logging.WARNING)
    logging.getLogger("signal_engine").setLevel(logging.WARNING)

    args = parse_args()
    config = AppConfig.load(args.config)
    data_inputs = args.data or [str(Path("data/processed/MNQ/1m"))]
    parquet_files = discover_parquet_files(data_inputs)
    minute_df = load_parquet_files(parquet_files)

    weight_grid = generate_weight_grid(args.step)
    candidates: list[EnsembleCandidate] = []
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    summary_path = Path(args.reports_dir) / f"ensemble_sweep_{stamp}.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    for weights in weight_grid:
        backtester = EnsembleBacktester(
            config=config,
            weights=weights,
            threshold=args.threshold,
            slippage_points=args.slippage_points,
        )
        result = backtester.run(minute_df, data_sources=parquet_files)
        _, json_path = write_result_files(result, args.reports_dir)
        summary = result.summary
        candidates.append(EnsembleCandidate(
            weights=weights,
            total_pnl=summary.total_pnl,
            avg_monthly_pnl=summary.avg_monthly_pnl,
            max_drawdown=summary.max_drawdown,
            win_rate=summary.win_rate,
            trades=summary.trades,
            result_json=json_path,
        ))

    candidates.sort(key=lambda c: (c.total_pnl, c.max_drawdown), reverse=True)
    top = candidates[: max(1, args.top)]

    with summary_path.open("w") as handle:
        json.dump({
            "config": str(Path(args.config).resolve()),
            "data_sources": parquet_files,
            "step": args.step,
            "threshold": args.threshold,
            "candidates": [asdict(c) for c in candidates],
        }, handle, indent=2)

    print(f"Weight combinations tested: {len(candidates)}")
    print(f"Summary JSON: {summary_path}")
    for idx, candidate in enumerate(top, start=1):
        print(
            f"{idx}. weights={candidate.weights} trades={candidate.trades} "
            f"win_rate={candidate.win_rate:.2f}% total=${candidate.total_pnl:,.2f} "
            f"avg_monthly=${candidate.avg_monthly_pnl:,.2f} max_dd=${candidate.max_drawdown:,.2f}"
        )
        print(f"   result={candidate.result_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
