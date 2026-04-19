from __future__ import annotations

import time

from bridge.nt_bridge import NTBridge
from config import ANTHROPIC_API_KEY, BRIDGE_HOST, BRIDGE_PORT, DB_PATH, NEWSAPI_KEY
from database.db import Database
from governor.claude_client import ClaudeClient
from governor.governor import Governor
from news.calendar_feed import fetch_today_events
from news.live_news import LiveNewsMonitor
from news.news_classifier import classify
from utils.logger import get_logger


def main():
    try:
        import schedule
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("schedule is required to run main()") from exc

    logger = get_logger("main")
    db = Database(DB_PATH)
    bridge = NTBridge(BRIDGE_HOST, BRIDGE_PORT, db)
    bridge.start()
    claude_client = ClaudeClient(ANTHROPIC_API_KEY, timeout=10)
    governor = Governor(db, bridge, claude_client)
    live_news_monitor = LiveNewsMonitor(NEWSAPI_KEY, governor, db, classify)
    live_news_monitor.start()

    schedule.every().day.at("08:30").do(lambda: governor.run_premarket(fetch_today_events(), current_pnl=0.0))
    logger.info("system started")
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
