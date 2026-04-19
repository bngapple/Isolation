#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GOVERNOR_DIR="$ROOT_DIR/isolation-governor"
CACHE_PATH="$ROOT_DIR/data/news/governor_decisions_cache.json"
OPENCODE_BIN="${OPENCODE_BIN:-$HOME/.opencode/bin/opencode}"
OPENCODE_MODEL="${OPENCODE_MODEL:-openai/gpt-5.4}"
START_DATE="${START_DATE:-2020-01-01}"
END_DATE="${END_DATE:-2024-12-31}"

mkdir -p "$ROOT_DIR/data/news"

python3 - "$ROOT_DIR" "$GOVERNOR_DIR" "$CACHE_PATH" "$OPENCODE_BIN" "$OPENCODE_MODEL" "$START_DATE" "$END_DATE" <<'PY'
import json
import pathlib
import re
import subprocess
import sys

root_dir = pathlib.Path(sys.argv[1])
governor_dir = pathlib.Path(sys.argv[2])
cache_path = pathlib.Path(sys.argv[3])
opencode_bin = sys.argv[4]
opencode_model = sys.argv[5]
start_date = sys.argv[6]
end_date = sys.argv[7]

SYSTEM_PROMPT = """You are a risk manager for a funded NQ futures trading account.
Account: $25,000 | Killswitch: -$750 | Base contracts: 5

A GOOD day is HIGH VOLATILITY — the RSI strategy thrives on large directional moves.
A BAD day is LOW VOLATILITY or consolidation — the strategy gets chopped.
Your job: run FULL SIZE on high-volatility days, REDUCE or HALT on low-volatility days.

Output ONLY valid JSON, no other text:
{"mode": "NORMAL"|"REDUCED"|"DEFENSIVE"|"HALTED", "size_multiplier": <0.2 to 1.0>, "reason": "<10 words max>"}

size_multiplier guide: 1.0=5 contracts, 0.6=3 contracts, 0.4=2 contracts, 0.2=1 contract
"""


def extract_decision(stdout: str) -> str:
    text_parts = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") == "text":
            candidate = payload.get("part", {}).get("text", "")
            if candidate:
                text_parts.append(candidate)
    if not text_parts:
        raise RuntimeError(f"No text payload found in opencode response:\n{stdout}")
    merged = "\n".join(text_parts)
    match = re.search(r"\{.*\}", merged, re.DOTALL)
    if not match:
        raise RuntimeError(f"No JSON object found in opencode text:\n{merged}")
    json.loads(match.group(0))
    return match.group(0)


cmd = [
    sys.executable,
    "-u",
    "-m",
    "learning.news_backtest",
    "cache-range",
    "--start-date",
    start_date,
    "--end-date",
    end_date,
    "--cache-path",
    str(cache_path),
    "--self-reason",
]

proc = subprocess.Popen(
    cmd,
    cwd=str(governor_dir),
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1,
)

try:
    assert proc.stdout is not None
    assert proc.stdin is not None
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        if line.startswith("DECISION_NEEDED|"):
            _, trading_date, payload_json = line.split("|", 2)
            payload = json.loads(payload_json)
            prompt = (
                SYSTEM_PROMPT
                + "\n\nUser message: the context string built from events + news + recent sessions\n"
                + json.dumps(payload, indent=2, sort_keys=True)
                + "\n\nReturn only the JSON object."
            )
            result = subprocess.run(
                [
                    opencode_bin,
                    "run",
                    prompt,
                    "--format",
                    "json",
                    "--model",
                    opencode_model,
                    "--dir",
                    str(governor_dir),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            decision = extract_decision(result.stdout)
            proc.stdin.write(decision + "\n")
            proc.stdin.flush()
        else:
            print(line, flush=True)
finally:
    if proc.stdin:
        proc.stdin.close()
    return_code = proc.wait()
    if return_code != 0:
        raise SystemExit(return_code)
PY

cd "$GOVERNOR_DIR"
python3 -m learning.news_backtest simulate --cache-path "$CACHE_PATH"
