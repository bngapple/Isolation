from __future__ import annotations

import time

from bridge.nt_bridge import NTBridge
from config import ACCOUNTS, BRIDGE_HOST, BRIDGE_PORT, DB_PATH
from database.db import Database
from utils.logger import get_logger


def main():
    logger = get_logger("main")
    db = Database(DB_PATH)
    account = next((account for account in ACCOUNTS if int(account["port"]) == int(BRIDGE_PORT)), ACCOUNTS[0])
    bridge = NTBridge(BRIDGE_HOST, BRIDGE_PORT, db, account_id=account["id"], killswitch_dollar=float(account["max_loss"]))
    bridge.start()
    logger.info("bridge server ready", extra={"data": {"host": BRIDGE_HOST, "port": BRIDGE_PORT, "account_id": account["id"], "killswitch_dollar": account["max_loss"]}})
    print(f"Bridge server ready on {BRIDGE_HOST}:{BRIDGE_PORT} for {account['id']}")
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
