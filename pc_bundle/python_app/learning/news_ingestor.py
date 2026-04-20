from __future__ import annotations

import argparse
import csv
import gzip
import html
import io
import json
import re
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import DB_PATH
from database.db import DB
from utils.logger import get_logger


LOGGER = get_logger("news_ingestor")
ROOT_DIR = Path(__file__).resolve().parents[2]
NEWS_DIR = ROOT_DIR / "data" / "news"
FOREX_URLS = [
    "https://huggingface.co/datasets/Ehsanrs2/Forex_Factory_Calendar/resolve/main/data/train-00000-of-00001.parquet",
    "https://huggingface.co/datasets/Ehsanrs2/Forex_Factory_Calendar/resolve/main/forex_factory_calendar.parquet",
]
TRUMP_TWITTER_URLS = {
    2020: "https://raw.githubusercontent.com/bpb27/trump_tweet_data_archive/master/data/condensed_2020.json.gz",
    2021: "https://raw.githubusercontent.com/bpb27/trump_tweet_data_archive/master/data/condensed_2021.json.gz",
}
TRUTH_URL = "https://ix.cnn.io/data/truth-social/truth_archive.parquet"
GDELT_MASTER_URL = "http://data.gdeltproject.org/gdeltv2/masterfilelist.txt"
TRUMP_KEYWORDS = [
    "tariff", "china", "fed", "federal reserve", "rate", "market", "stock", "trade",
    "economy", "sanction", "inflation", "gdp", "jobs", "stimulus", "covid", "coronavirus",
]
TRUTH_KEYWORDS = [
    "tariff", "china", "fed", "federal reserve", "rate", "market", "stock", "trade",
    "economy", "sanction", "inflation", "gdp", "jobs",
]
GDELT_THEMES = {
    "ECON_TARIFF",
    "ECON_TRADE",
    "ECON_INTEREST_RATES",
    "ECON_INFLATION",
    "ECON_UNEMPLOYMENT",
    "ECON_GOVERNMENT_DEBT",
    "TAX_FNCACT_FEDERAL_RESERVE",
    "SANCTION",
    "UNGP_ECONOMIC_RIGHTS",
    "CRISISLEX_CRISISLEXREC",
}
GDELT_HIGH_THEMES = {"SANCTION", "TAX_FNCACT_FEDERAL_RESERVE"}


def _ensure_news_dir() -> None:
    NEWS_DIR.mkdir(parents=True, exist_ok=True)


def _download_file(urls: list[str], target_path: Path) -> Path:
    _ensure_news_dir()
    if target_path.exists() and target_path.stat().st_size > 0:
        return target_path

    import requests

    last_error = None
    for url in urls:
        try:
            response = requests.get(url, timeout=120)
            response.raise_for_status()
            target_path.write_bytes(response.content)
            return target_path
        except Exception as exc:  # pragma: no cover
            last_error = exc
            LOGGER.error("download failed", extra={"data": {"url": url, "error": str(exc)}})
    raise RuntimeError(f"Failed downloading {target_path.name}: {last_error}")


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _find_column(columns: list[str], candidates: list[str]) -> str | None:
    normalized = {_normalize_name(col): col for col in columns}
    for candidate in candidates:
        key = _normalize_name(candidate)
        if key in normalized:
            return normalized[key]
    return None


def _to_float(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "n/a", "na", "--"}:
        return None
    text = text.replace(",", "")
    match = re.search(r"[-+]?\d*\.?\d+", text)
    return float(match.group(0)) if match else None


def _contains_keywords(text: str, keywords: list[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _strip_html(text: str) -> str:
    clean = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(clean)).strip()


def _bulk_insert(db: DB, query: str, rows: list[tuple]) -> None:
    if not rows:
        return
    db.conn.executemany(query, rows)
    db.conn.commit()


def ingest_forex_factory(db_path: str = DB_PATH) -> int:
    import pandas as pd

    target = NEWS_DIR / "forex_factory_calendar.parquet"
    _download_file(FOREX_URLS, target)
    df = pd.read_parquet(target)
    columns = list(df.columns)

    currency_col = _find_column(columns, ["currency", "curr", "country_currency"])
    impact_col = _find_column(columns, ["impact", "impact_title", "impactlevel"])
    name_col = _find_column(columns, ["event_name", "title", "event", "name"])
    ts_col = _find_column(columns, ["datetime", "event_datetime", "timestamp", "date"])
    date_col = _find_column(columns, ["date"])
    time_col = _find_column(columns, ["time"])
    actual_col = _find_column(columns, ["actual", "actual_value"])
    forecast_col = _find_column(columns, ["forecast", "forecast_value"])
    previous_col = _find_column(columns, ["previous", "previous_value"])

    if ts_col is None and not (date_col and time_col):
        raise RuntimeError(f"Could not infer timestamp columns from: {columns}")

    if ts_col is not None:
        timestamps = pd.to_datetime(df[ts_col], errors="coerce")
    else:
        timestamps = pd.to_datetime(df[date_col].astype(str) + " " + df[time_col].astype(str), errors="coerce")

    if getattr(timestamps.dt, "tz", None) is None:
        timestamps = timestamps.dt.tz_localize("Asia/Tehran", nonexistent="shift_forward", ambiguous="NaT")
    else:
        timestamps = timestamps.dt.tz_convert("Asia/Tehran")
    timestamps = timestamps.dt.tz_convert("UTC")

    currency = df[currency_col].astype(str).str.upper() if currency_col else pd.Series([""] * len(df))
    impact = df[impact_col].astype(str).str.strip() if impact_col else pd.Series([""] * len(df))
    impact_mask = impact.str.contains("High", case=False, na=False) | impact.str.contains("Medium", case=False, na=False)
    start = pd.Timestamp("2020-01-01", tz="UTC")
    end = pd.Timestamp("2024-12-31 23:59:59", tz="UTC")
    mask = (currency == "USD") & impact_mask & timestamps.notna() & (timestamps >= start) & (timestamps <= end)
    filtered = df.loc[mask].copy()
    filtered["_event_datetime_utc"] = timestamps.loc[mask]

    rows = []
    for _, row in filtered.iterrows():
        actual = _to_float(row.get(actual_col)) if actual_col else None
        forecast = _to_float(row.get(forecast_col)) if forecast_col else None
        previous = _to_float(row.get(previous_col)) if previous_col else None
        surprise_magnitude = None if actual is None or forecast is None else actual - forecast
        if forecast is None:
            surprise_direction = "no_forecast"
        elif actual is None:
            surprise_direction = "no_forecast"
        elif abs(actual - forecast) <= 0.01:
            surprise_direction = "inline"
        elif actual > forecast:
            surprise_direction = "beat"
        else:
            surprise_direction = "miss"
        impact_value = str(row.get(impact_col, "")).strip()
        impact_value = "High" if "high" in impact_value.lower() else "Medium"
        rows.append(
            (
                str(row.get(name_col, "")).strip(),
                row["_event_datetime_utc"].isoformat(),
                "USD",
                impact_value,
                actual,
                forecast,
                previous,
                surprise_magnitude,
                surprise_direction,
            )
        )

    db = DB(db_path)
    db.execute(
        "DELETE FROM economic_events WHERE currency = 'USD' AND event_datetime >= ? AND event_datetime <= ?",
        ("2020-01-01", "2024-12-31T23:59:59+00:00"),
    )
    _bulk_insert(
        db,
        """
        INSERT INTO economic_events
        (event_name, event_datetime, currency, impact, actual, forecast, previous, surprise_magnitude, surprise_direction)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    db.close()
    return len(rows)


def _iter_nested_records(payload):
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_nested_records(item)
    elif isinstance(payload, dict):
        if any(key in payload for key in ("created_at", "full_text", "text", "fullText")):
            yield payload
        else:
            for value in payload.values():
                yield from _iter_nested_records(value)


def ingest_trump_twitter(db_path: str = DB_PATH) -> int:
    import pandas as pd

    paths = {}
    for year, url in TRUMP_TWITTER_URLS.items():
        target = NEWS_DIR / f"trump_tweets_{year}.json.gz"
        _download_file([url], target)
        paths[year] = target

    records = []
    cutoff_2021 = pd.Timestamp("2021-01-09", tz="UTC")
    for year, path in paths.items():
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        for record in _iter_nested_records(payload):
            text = str(record.get("full_text") or record.get("text") or record.get("fullText") or "").strip()
            if not text or not _contains_keywords(text, TRUMP_KEYWORDS):
                continue
            ts = pd.to_datetime(record.get("created_at") or record.get("date") or record.get("createdAt"), utc=True, errors="coerce")
            if pd.isna(ts):
                continue
            if year == 2021 and ts >= cutoff_2021:
                continue
            if ts < pd.Timestamp("2020-01-01", tz="UTC") or ts > pd.Timestamp("2024-12-31 23:59:59", tz="UTC"):
                continue
            records.append((ts.isoformat(), text, "trump_twitter", None, None, "historical_ingest"))

    db = DB(db_path)
    db.execute("DELETE FROM live_news_log WHERE source = ?", ("trump_twitter",))
    _bulk_insert(
        db,
        """
        INSERT INTO live_news_log
        (received_datetime, headline, source, classified_impact, classified_direction, action_taken)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        records,
    )
    db.close()
    return len(records)


def ingest_trump_truth_social(db_path: str = DB_PATH) -> int:
    import pandas as pd

    target = NEWS_DIR / "trump_truth_social.parquet"
    _download_file([TRUTH_URL], target)
    df = pd.read_parquet(target)
    columns = list(df.columns)
    created_col = _find_column(columns, ["created_at", "createdat", "published_at", "date"])
    content_col = _find_column(columns, ["content", "text", "body", "status"])
    if created_col is None or content_col is None:
        raise RuntimeError(f"Could not infer truth social columns from: {columns}")

    created = pd.to_datetime(df[created_col], utc=True, errors="coerce")
    content = df[content_col].fillna("").astype(str).map(_strip_html)
    start = pd.Timestamp("2022-02-14", tz="UTC")
    end = pd.Timestamp("2024-12-31 23:59:59", tz="UTC")
    mask = created.notna() & (created >= start) & (created <= end) & content.map(lambda value: _contains_keywords(value, TRUTH_KEYWORDS))
    filtered = df.loc[mask].copy()

    rows = []
    for idx, row in filtered.iterrows():
        rows.append((created.loc[idx].isoformat(), _strip_html(str(row[content_col])), "trump_truth_social", None, None, "historical_ingest"))

    db = DB(db_path)
    db.execute("DELETE FROM live_news_log WHERE source = ?", ("trump_truth_social",))
    _bulk_insert(
        db,
        """
        INSERT INTO live_news_log
        (received_datetime, headline, source, classified_impact, classified_direction, action_taken)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    db.close()
    return len(rows)


def _event_window_days(db: DB, high_only: bool = False) -> tuple[set[datetime.date], int]:
    impacts = ("High",) if high_only else ("High", "Medium")
    placeholders = ",".join("?" for _ in impacts)
    rows = db.fetchall(
        f"""
        SELECT DISTINCT date(event_datetime) AS event_date
        FROM economic_events
        WHERE currency = 'USD' AND impact IN ({placeholders})
          AND date(event_datetime) BETWEEN '2020-01-01' AND '2024-12-31'
        ORDER BY event_date
        """,
        impacts,
    )
    dates = [datetime.fromisoformat(row["event_date"]).date() for row in rows if row.get("event_date")]
    windows = set()
    for day in dates:
        for delta in range(-2, 3):
            windows.add(day + timedelta(days=delta))
    return windows, len(dates)


def _target_gdelt_urls(db: DB) -> tuple[list[str], int]:
    import requests

    windows, event_count = _event_window_days(db, high_only=False)
    response = requests.get(GDELT_MASTER_URL, timeout=120)
    response.raise_for_status()
    urls = []
    for line in response.text.splitlines():
        parts = line.split()
        if not parts:
            continue
        url = parts[-1]
        if not url.endswith(".gkg.csv.zip"):
            continue
        filename = url.rsplit("/", 1)[-1]
        stamp = filename[:14]
        try:
            day = datetime.strptime(stamp, "%Y%m%d%H%M%S").date()
        except ValueError:
            continue
        if day in windows:
            urls.append(url)

    if len(urls) > 500:
        windows, event_count = _event_window_days(db, high_only=True)
        urls = []
        for line in response.text.splitlines():
            parts = line.split()
            if not parts:
                continue
            url = parts[-1]
            if not url.endswith(".gkg.csv.zip"):
                continue
            filename = url.rsplit("/", 1)[-1]
            stamp = filename[:14]
            try:
                day = datetime.strptime(stamp, "%Y%m%d%H%M%S").date()
            except ValueError:
                continue
            if day in windows:
                urls.append(url)

    urls = sorted(urls)
    if len(urls) > 500:
        urls = urls[:500]
    return urls, event_count


def ingest_gdelt(db_path: str = DB_PATH) -> tuple[int, int]:
    import requests

    db = DB(db_path)
    for _ in range(60):
        count_row = db.fetchone("SELECT COUNT(*) AS n FROM economic_events")
        if count_row and count_row["n"] > 0:
            break
        time.sleep(5)

    urls, event_count = _target_gdelt_urls(db)
    db.execute("DELETE FROM live_news_log WHERE source = ?", ("gdelt",))

    rows = []
    for idx, url in enumerate(urls, start=1):
        if idx % 50 == 0:
            print(f"GDELT: processed {idx}/{len(urls)} files...")
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            name = archive.namelist()[0]
            with archive.open(name) as handle:
                wrapper = io.TextIOWrapper(handle, encoding="utf-8", errors="ignore")
                reader = csv.reader(wrapper, delimiter="\t")
                for fields in reader:
                    if len(fields) < 7:
                        continue
                    themes = fields[7]
                    if not themes:
                        continue
                    theme_set = set(themes.split(";"))
                    matched = theme_set & GDELT_THEMES
                    if not matched:
                        continue
                    try:
                        ts = datetime.strptime(fields[1], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
                    except ValueError:
                        continue
                    impact = "high" if matched & GDELT_HIGH_THEMES else "medium"
                    rows.append((ts.isoformat(), fields[4], "gdelt", impact, None, "historical_ingest"))

    _bulk_insert(
        db,
        """
        INSERT INTO live_news_log
        (received_datetime, headline, source, classified_impact, classified_direction, action_taken)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    db.close()
    return len(rows), event_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Historical news ingestion")
    parser.add_argument("source", choices=["forex", "twitter", "truth", "gdelt"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.source == "forex":
        count = ingest_forex_factory()
        print(f"Forex Factory: {count} events loaded")
    elif args.source == "twitter":
        count = ingest_trump_twitter()
        print(f"Trump Twitter: {count} tweets loaded")
    elif args.source == "truth":
        count = ingest_trump_truth_social()
        print(f"Trump Truth Social: {count} posts loaded")
    elif args.source == "gdelt":
        count, windows = ingest_gdelt()
        print(f"GDELT: {count} articles loaded covering {windows} event windows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
