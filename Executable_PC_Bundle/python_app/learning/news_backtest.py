from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests


ROOT_DIR = Path(__file__).resolve().parents[2]
GOVERNOR_DIR = ROOT_DIR / "isolation-governor"
BASELINE_TRADES_PATH = ROOT_DIR / "reports" / "backtests" / "be_killswitch_trades.json"
CACHE_PATH = ROOT_DIR / "data" / "news" / "governor_decisions_cache.json"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL = "gpt-4o"
INPUT_PRICE_PER_MTOK = 2.50
OUTPUT_PRICE_PER_MTOK = 10.00

SYSTEM_PROMPT = """You are a risk manager for a funded NQ futures trading account.
Account: $25,000 | Killswitch: -$750 | Base contracts: 5

A GOOD day is HIGH VOLATILITY — the RSI strategy thrives on large directional moves.
A BAD day is LOW VOLATILITY or consolidation — the strategy gets chopped.
Your job: run FULL SIZE on high-volatility days, REDUCE or HALT on low-volatility days.

Output ONLY valid JSON, no other text:
{"mode": "NORMAL"|"REDUCED"|"DEFENSIVE"|"HALTED", "size_multiplier": <0.2 to 1.0>, "reason": "<10 words max>"}

size_multiplier guide: 1.0=5 contracts, 0.6=3 contracts, 0.4=2 contracts, 0.2=1 contract
"""


@dataclass
class RiskProfile:
    mode: str
    size_multiplier: float
    reason: str
    input_tokens: int = 0
    output_tokens: int = 0


def load_openai_api_key() -> str | None:
    env_path = GOVERNOR_DIR / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text().splitlines():
        if line.startswith("OPENAI_API_KEY="):
            value = line.split("=", 1)[1].strip()
            return value or None
    return None


def load_cache(path: Path = CACHE_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_cache(cache: dict[str, Any], path: Path = CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, default=str))


def merge_cache_shards(shard_paths: list[Path], output_path: Path = CACHE_PATH) -> dict[str, Any]:
    merged = load_cache(output_path)
    for shard_path in shard_paths:
        if shard_path.exists():
            merged.update(json.loads(shard_path.read_text()))
    save_cache(merged, output_path)
    return merged


def load_baseline_payload() -> dict[str, Any]:
    return json.loads(BASELINE_TRADES_PATH.read_text())


def baseline_trading_dates(payload: dict[str, Any]) -> list[str]:
    dates = sorted({str(trade["entry_time"])[:10] for trade in payload["trades"]})
    return dates


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(GOVERNOR_DIR / "isolation.db")
    conn.row_factory = sqlite3.Row
    return conn


def fetch_day_context_payload(conn: sqlite3.Connection, trading_date: str) -> dict[str, Any]:
    start = datetime.fromisoformat(trading_date).replace(tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    lookback = start - timedelta(hours=48)

    events = conn.execute(
        """
        SELECT event_name, event_datetime, impact, actual, forecast, previous, surprise_magnitude, surprise_direction
        FROM economic_events
        WHERE date(event_datetime) = date(?)
        ORDER BY event_datetime
        """,
        (trading_date,),
    ).fetchall()

    news = conn.execute(
        """
        SELECT received_datetime, headline, source, classified_impact, action_taken
        FROM live_news_log
        WHERE received_datetime >= ? AND received_datetime < ?
        ORDER BY received_datetime DESC
        LIMIT 20
        """,
        (lookback.isoformat(), end.isoformat()),
    ).fetchall()

    sessions = conn.execute(
        """
        SELECT session_date, session_range_points, volatility_class, strategy_net_pnl, trade_count
        FROM session_summaries
        WHERE session_date < ?
        ORDER BY session_date DESC
        LIMIT 10
        """,
        (trading_date,),
    ).fetchall()

    return {
        "trading_date": trading_date,
        "events": [dict(row) for row in events],
        "news": [dict(row) for row in news],
        "recent_sessions": [dict(row) for row in sessions],
    }


def build_context_string(context_payload: dict[str, Any]) -> str:
    trading_date = context_payload["trading_date"]
    events = context_payload["events"]
    news = context_payload["news"]
    sessions = context_payload["recent_sessions"]

    lines = [f"TRADING DATE: {trading_date}"]
    lines.append("ECONOMIC EVENTS:")
    if events:
        for row in events:
            lines.append(
                f"- {row['event_datetime']} | {row['impact']} | {row['event_name']} | actual={row['actual']} forecast={row['forecast']} previous={row['previous']} surprise={row['surprise_direction']} ({row['surprise_magnitude']})"
            )
    else:
        lines.append("- None")

    lines.append("RECENT NEWS (48H):")
    if news:
        for row in news:
            lines.append(f"- {row['received_datetime']} | {row['source']} | {row['headline']} | impact={row['classified_impact']}")
    else:
        lines.append("- None")

    lines.append("RECENT SESSION CONTEXT:")
    if sessions:
        for row in sessions:
            lines.append(
                f"- {row['session_date']} | range={row['session_range_points']} | vol={row['volatility_class']} | pnl={row['strategy_net_pnl']} | trades={row['trade_count']}"
            )
    else:
        lines.append("- None")
    return "\n".join(lines)


def fetch_day_context(conn: sqlite3.Connection, trading_date: str) -> str:
    return build_context_string(fetch_day_context_payload(conn, trading_date))


def parse_profile(text: str) -> RiskProfile:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    payload = json.loads(cleaned)
    return RiskProfile(
        mode=payload["mode"],
        size_multiplier=float(payload["size_multiplier"]),
        reason=str(payload.get("reason", "")),
    )


def call_openai(api_key: str, context: str) -> RiskProfile:
    response = requests.post(
        OPENAI_CHAT_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": OPENAI_MODEL,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": context},
            ],
        },
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    content = payload["choices"][0]["message"]["content"]
    profile = parse_profile(content)
    usage = payload.get("usage", {})
    profile.input_tokens = int(usage.get("prompt_tokens", 0))
    profile.output_tokens = int(usage.get("completion_tokens", 0))
    return profile


def _read_profile_from_stdin(trading_date: str) -> RiskProfile:
    line = sys.stdin.readline()
    if not line:
        raise RuntimeError(f"No decision received on stdin for {trading_date}")
    return parse_profile(line)


def cache_range(start_date: str, end_date: str, shard_path: Path | None = None, self_reason: bool = False) -> dict[str, Any]:
    api_key = None
    if not self_reason:
        api_key = load_openai_api_key()
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY not found in isolation-governor/.env\n"
                "Add:\nOPENAI_API_KEY=your_key_here"
            )

    baseline = load_baseline_payload()
    dates = [d for d in baseline_trading_dates(baseline) if start_date <= d <= end_date]
    cache = load_cache(shard_path) if shard_path else load_cache()
    conn = get_db_connection()
    try:
        for idx, trading_date in enumerate(dates, start=1):
            if trading_date in cache:
                continue
            context_payload = fetch_day_context_payload(conn, trading_date)
            if self_reason:
                print(f"DECISION_NEEDED|{trading_date}|{json.dumps({'events': context_payload['events'], 'news': context_payload['news'], 'recent_sessions': context_payload['recent_sessions']}, separators=(',', ':'), default=str)}", flush=True)
                profile = _read_profile_from_stdin(trading_date)
            else:
                context = build_context_string(context_payload)
                profile = call_openai(api_key, context)
            cache[trading_date] = {
                "mode": profile.mode,
                "size_multiplier": profile.size_multiplier,
                "reason": profile.reason,
                "input_tokens": profile.input_tokens,
                "output_tokens": profile.output_tokens,
            }
            if self_reason:
                save_cache(cache, shard_path or CACHE_PATH)
            if idx % 20 == 0:
                print(f"cached {idx}/{len(dates)} days for {start_date}..{end_date}")
                save_cache(cache, shard_path or CACHE_PATH)
        save_cache(cache, shard_path or CACHE_PATH)
        return cache
    finally:
        conn.close()


def _simulate_from_trades(trades: list[dict[str, Any]], decision_map: dict[str, Any]) -> dict[str, Any]:
    daily_trades = defaultdict(list)
    for trade in trades:
        daily_trades[str(trade["entry_time"])[:10]].append(trade)

    all_days = sorted(daily_trades)
    daily_pnl = {}
    trade_count = 0
    days_halted = 0
    days_reduced = 0
    days_normal = 0
    killswitch_triggers = 0

    for day in all_days:
        decision = decision_map.get(day, {"mode": "NORMAL", "size_multiplier": 1.0, "reason": "cache_miss_default"})
        mode = decision["mode"]
        multiplier = float(decision.get("size_multiplier", 1.0))
        if mode == "HALTED":
            daily_pnl[day] = 0.0
            days_halted += 1
            continue

        live_contracts = max(1, round(5 * multiplier))
        if mode == "NORMAL" and live_contracts == 5:
            days_normal += 1
        else:
            days_reduced += 1

        realized = 0.0
        halted_intraday = False
        for trade in daily_trades[day]:
            if halted_intraday:
                break
            pnl = float(trade["pnl_total"]) * live_contracts
            realized += pnl
            trade_count += 1
            if realized <= -750.0:
                halted_intraday = True
                killswitch_triggers += 1
        daily_pnl[day] = realized

    total_pnl = sum(daily_pnl.values())
    months = defaultdict(float)
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    worst_day = min(daily_pnl.values()) if daily_pnl else 0.0
    for day in sorted(daily_pnl):
        months[day[:7]] += daily_pnl[day]
        cumulative += daily_pnl[day]
        peak = max(peak, cumulative)
        max_dd = min(max_dd, cumulative - peak)

    return {
        "total_pnl": total_pnl,
        "monthly_avg": total_pnl / max(len(months), 1),
        "max_drawdown": max_dd,
        "worst_day": worst_day,
        "trade_count": trade_count,
        "days_halted": days_halted,
        "days_reduced": days_reduced,
        "days_normal": days_normal,
        "killswitch_triggers": killswitch_triggers,
        "daily_pnl": daily_pnl,
    }


def simulate_news_backtest(cache_path: Path = CACHE_PATH) -> dict[str, Any]:
    baseline_payload = load_baseline_payload()
    trades = baseline_payload["trades"]
    cache = load_cache(cache_path)

    baseline_decisions = {str(trade["entry_time"])[:10]: {"mode": "NORMAL", "size_multiplier": 1.0, "reason": "baseline"} for trade in trades}
    baseline = _simulate_from_trades(trades, baseline_decisions)
    news_enhanced = _simulate_from_trades(trades, cache)

    worst_10 = sorted(baseline["daily_pnl"].items(), key=lambda item: item[1])[:10]
    worst_rows = []
    for date, pnl in worst_10:
        decision = cache.get(date, {"mode": "NORMAL", "reason": "cache_miss_default"})
        worst_rows.append({
            "date": date,
            "baseline_pnl": pnl,
            "mode": decision.get("mode", "NORMAL"),
            "reason": decision.get("reason", ""),
        })

    total_input_tokens = sum(int(value.get("input_tokens", 0)) for value in cache.values())
    total_output_tokens = sum(int(value.get("output_tokens", 0)) for value in cache.values())
    estimated_cost = (total_input_tokens / 1_000_000.0) * INPUT_PRICE_PER_MTOK + (total_output_tokens / 1_000_000.0) * OUTPUT_PRICE_PER_MTOK

    return {
        "baseline_5c": baseline,
        "news_enhanced": news_enhanced,
        "worst_days": worst_rows,
        "token_usage": {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "estimated_cost_usd": estimated_cost,
        },
    }


def print_comparison(result: dict[str, Any]) -> None:
    baseline = result["baseline_5c"]
    enhanced = result["news_enhanced"]
    print("metric                 baseline_5c     news_enhanced")
    print("---------------------  --------------  --------------")
    print(f"total_pnl              ${baseline['total_pnl']:,.2f}    ${enhanced['total_pnl']:,.2f}")
    print(f"monthly_avg            ${baseline['monthly_avg']:,.2f}      ${enhanced['monthly_avg']:,.2f}")
    print(f"max_drawdown           ${baseline['max_drawdown']:,.2f}     ${enhanced['max_drawdown']:,.2f}")
    print(f"worst_day              ${baseline['worst_day']:,.2f}       ${enhanced['worst_day']:,.2f}")
    print(f"trade_count            {baseline['trade_count']}            {enhanced['trade_count']}")
    print(f"days_halted            {baseline['days_halted']}             {enhanced['days_halted']}")
    print(f"days_reduced           {baseline['days_reduced']}             {enhanced['days_reduced']}")
    print(f"days_normal            {baseline['days_normal']}           {enhanced['days_normal']}")
    print(f"killswitch_triggers    {baseline['killswitch_triggers']}             {enhanced['killswitch_triggers']}")
    print()
    print("10 worst baseline days:")
    for row in result["worst_days"]:
        print(f"- {row['date']} | baseline=${row['baseline_pnl']:,.2f} | governor={row['mode']} | reason={row['reason']}")
    usage = result["token_usage"]
    print()
    print(f"Estimated GPT-4o API cost: ${usage['estimated_cost_usd']:.4f} ({usage['input_tokens']} input tokens, {usage['output_tokens']} output tokens)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="News-enhanced GPT-4o backtest")
    subparsers = parser.add_subparsers(dest="command", required=True)

    cache_parser = subparsers.add_parser("cache-range")
    cache_parser.add_argument("--start-date", required=True)
    cache_parser.add_argument("--end-date", required=True)
    cache_parser.add_argument("--cache-path", default=None)
    cache_parser.add_argument("--self-reason", action="store_true")

    merge_parser = subparsers.add_parser("merge-cache")
    merge_parser.add_argument("shards", nargs="+")

    simulate_parser = subparsers.add_parser("simulate")
    simulate_parser.add_argument("--cache-path", default=str(CACHE_PATH))

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "cache-range":
            shard_path = Path(args.cache_path) if args.cache_path else None
            cache_range(args.start_date, args.end_date, shard_path=shard_path, self_reason=args.self_reason)
            print(f"Cached decisions for {args.start_date}..{args.end_date}")
            return 0
        if args.command == "merge-cache":
            merge_cache_shards([Path(p) for p in args.shards], CACHE_PATH)
            print(f"Merged cache shards into {CACHE_PATH}")
            return 0
        if args.command == "simulate":
            result = simulate_news_backtest(Path(args.cache_path))
            print_comparison(result)
            return 0
        return 1
    except RuntimeError as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
