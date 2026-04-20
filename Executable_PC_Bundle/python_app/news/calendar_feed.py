from __future__ import annotations

import xml.etree.ElementTree as ET

from utils.logger import get_logger


URL = "https://www.forexfactory.com/ff_cal_thisweek.xml"
LOGGER = get_logger("calendar_feed")


def fetch_today_events() -> list[dict]:
    try:
        import requests

        response = requests.get(URL, timeout=10)
        response.raise_for_status()
        root = ET.fromstring(response.text)
    except Exception as exc:  # pragma: no cover
        LOGGER.error("calendar fetch failed", extra={"data": {"error": str(exc)}})
        return []

    events = []
    for item in root.findall(".//event"):
        currency = (item.findtext("currency") or "").strip()
        impact = (item.findtext("impact") or "").strip()
        if currency != "USD" or impact not in {"High", "Medium"}:
            continue
        events.append(
            {
                "name": (item.findtext("title") or "").strip(),
                "datetime": (item.findtext("date") or "").strip(),
                "impact": impact,
                "forecast": (item.findtext("forecast") or "").strip(),
                "actual": (item.findtext("actual") or "").strip(),
                "previous": (item.findtext("previous") or "").strip(),
            }
        )
    return events
