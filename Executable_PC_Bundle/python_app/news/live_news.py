from __future__ import annotations

import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from utils.logger import get_logger


KEYWORDS = [
    "Trump", "Fed", "tariff", "rate", "inflation", "NFP", "CPI", "PPI", "GDP", "jobs",
    "war", "sanction", "China", "Russia", "Israel", "FOMC", "Powell",
]


class LiveNewsMonitor:
    def __init__(self, api_key: str, governor, db, classifier):
        self.api_key = api_key
        self.governor = governor
        self.db = db
        self.classifier = classifier
        self.logger = get_logger("live_news")
        self._thread = None
        self._running = False
        self._seen = set()

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1)

    def _loop(self):
        client = None
        if self.api_key:
            try:
                from newsapi import NewsApiClient

                client = NewsApiClient(api_key=self.api_key)
            except Exception as exc:  # pragma: no cover
                self.logger.error("newsapi init failed", extra={"data": {"error": str(exc)}})

        while self._running:
            now = datetime.now(ZoneInfo("America/New_York"))
            if 9 <= now.hour < 17 and client is not None:
                self._poll(client)
            time.sleep(60)

    def _poll(self, client):
        try:
            response = client.get_everything(q=" OR ".join(KEYWORDS), language="en", page_size=20, sort_by="publishedAt")
        except Exception as exc:  # pragma: no cover
            self.logger.error("news poll failed", extra={"data": {"error": str(exc)}})
            return

        for article in response.get("articles", []):
            headline = article.get("title") or ""
            if not headline or headline in self._seen or not any(k.lower() in headline.lower() for k in KEYWORDS):
                continue
            self._seen.add(headline)
            classified = self.classifier(headline, self.api_key)
            action_taken = "logged"
            if classified.get("impact") in {"medium", "high"}:
                profile = self.governor.run_intraday_update("live_news", headline, current_pnl=0.0)
                action_taken = profile.mode
            self.db.execute(
                "INSERT INTO live_news_log (received_datetime, headline, source, classified_impact, classified_direction, action_taken) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    datetime.utcnow().isoformat(),
                    headline,
                    article.get("source", {}).get("name", "NewsAPI"),
                    classified.get("impact", "none"),
                    classified.get("direction", "uncertain"),
                    action_taken,
                ),
            )
