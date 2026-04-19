from __future__ import annotations

import json
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from config import LOG_DIR


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
            "data": getattr(record, "data", {}),
        }
        return json.dumps(payload, default=str)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
    handler = TimedRotatingFileHandler(str(Path(LOG_DIR) / f"{name}.log"), when="midnight", backupCount=14)
    handler.setFormatter(JsonFormatter())

    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    return logger
