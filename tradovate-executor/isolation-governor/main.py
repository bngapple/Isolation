from __future__ import annotations

import time

from bridge.nt_bridge import NTBridge
from config import BRIDGE_HOST, BRIDGE_PORT, DB_PATH
from database.db import Database
from strategy.strategy_executor import StrategyExecutor
from utils.logger import get_logger


def main():
    logger = get_logger("main")
    db = Database(DB_PATH)
    executor = StrategyExecutor(db)
    bridge = NTBridge(BRIDGE_HOST, BRIDGE_PORT, db, executor)
    bridge.start()
    logger.info("strategy bridge server ready", extra={"data": {"host": BRIDGE_HOST, "port": BRIDGE_PORT}})
    print(f"Strategy bridge server ready on {BRIDGE_HOST}:{BRIDGE_PORT}")
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
