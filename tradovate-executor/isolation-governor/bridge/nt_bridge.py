from __future__ import annotations

import json
import socket
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from governor.risk_profile import RiskProfile
from utils.logger import get_logger


class NTBridge:
    def __init__(self, host, port, db, account_id: str = "lucid_25k_1", killswitch_dollar: float = 750.0):
        self.host = host
        self.port = port
        self.db = db
        self.account_id = account_id
        self.killswitch_dollar = killswitch_dollar
        self.logger = get_logger("nt_bridge")
        self.current_profile = RiskProfile.offline_fallback()
        self._clients = []
        self._lock = threading.Lock()
        self._server = None
        self._thread = None
        self._running = False
        self._ticker_thread = None
        self._last_profile_key = None
        self._buffers = {}
        self._daily_pnl = {}

    def start(self):
        if self._running:
            return
        self._running = True
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((self.host, self.port))
        self._server.listen()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        self._ticker_thread = threading.Thread(target=self._ticker_loop, daemon=True)
        self._ticker_thread.start()
        self.logger.info("bridge started", extra={"data": {"host": self.host, "port": self.port}})

    def _accept_loop(self):
        while self._running:
            try:
                client, address = self._server.accept()
            except OSError:
                break
            client.settimeout(1.0)
            with self._lock:
                self._clients.append(client)
                self._buffers[client] = ""
            self.logger.info("client connected", extra={"data": {"address": address}})
            profile = self._evaluate_profile()
            self._send(client, profile)
            threading.Thread(target=self._client_loop, args=(client, address), daemon=True).start()

    def _client_loop(self, client, address):
        try:
            while self._running:
                try:
                    data = client.recv(1024)
                    if not data:
                        break
                    self._process_data(client, data.decode("utf-8", errors="ignore"))
                except socket.timeout:
                    continue
        except OSError:
            pass
        finally:
            with self._lock:
                if client in self._clients:
                    self._clients.remove(client)
                self._buffers.pop(client, None)
            try:
                client.close()
            except OSError:
                pass
            self.logger.info("client disconnected", extra={"data": {"address": address}})

    def _process_data(self, client, chunk: str):
        with self._lock:
            buffer = self._buffers.get(client, "") + chunk
        lines = buffer.splitlines(keepends=True)
        remainder = ""
        for line in lines:
            if not line.endswith("\n"):
                remainder = line
                continue
            self._process_message(line.strip())
        with self._lock:
            self._buffers[client] = remainder

    def _process_message(self, line: str):
        if not line:
            return
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            self.logger.info("bridge received non-json payload", extra={"data": {"payload": line[:200]}})
            return

        account_id = payload.get("account") or payload.get("account_id") or self.account_id
        self.account_id = account_id
        daily_pnl = payload.get("daily_pnl")
        if daily_pnl is not None:
            try:
                pnl_value = float(daily_pnl)
                self._daily_pnl[account_id] = pnl_value
                self.db.execute(
                    "INSERT OR REPLACE INTO config_values (key, value) VALUES (?, ?)",
                    (self._daily_pnl_key(account_id), str(pnl_value)),
                )
            except (TypeError, ValueError):
                pnl_value = None
        else:
            pnl_value = self._daily_pnl.get(account_id)

        self.logger.info(
            "bridge received payload",
            extra={"data": {"account_id": account_id, "daily_pnl": pnl_value, "payload": payload}},
        )

    def _ticker_loop(self):
        while self._running:
            profile = self._evaluate_profile()
            profile_key = (profile.mode, profile.size_multiplier, profile.reason)
            if profile_key != self._last_profile_key:
                self._last_profile_key = profile_key
                daily_pnl = self._current_daily_pnl(self.account_id)
                now_et = datetime.now(ZoneInfo("America/New_York"))
                self.db.execute(
                    """
                    INSERT INTO governor_decisions
                    (decision_datetime, trigger, mode_decided, size_multiplier, reason, session_pnl_at_decision, claude_prompt, claude_response)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now_et.isoformat(),
                        "bridge_tick",
                        profile.mode,
                        profile.size_multiplier,
                        profile.reason,
                        daily_pnl,
                        "bridge_evaluator",
                        profile.to_json_line().strip(),
                    ),
                )
                self.push_profile(profile)
            time.sleep(1.0)

    def _daily_pnl_key(self, account_id: str) -> str:
        return f"daily_pnl_{account_id}"

    def _current_daily_pnl(self, account_id: str) -> float:
        if account_id in self._daily_pnl:
            return self._daily_pnl[account_id]
        row = self.db.fetchone("SELECT value FROM config_values WHERE key = ?", (self._daily_pnl_key(account_id),))
        if row is None:
            return 0.0
        try:
            return float(row["value"])
        except (TypeError, ValueError):
            return 0.0

    def _evaluate_profile(self) -> RiskProfile:
        now_et = datetime.now(ZoneInfo("America/New_York"))
        hhmm = now_et.hour * 100 + now_et.minute
        daily_pnl = self._current_daily_pnl(self.account_id)

        if hhmm < 930 or hhmm >= 1645:
            profile = RiskProfile(mode="HALTED", size_multiplier=0.0, reason="outside trading window")
        elif daily_pnl <= -self.killswitch_dollar:
            profile = RiskProfile(mode="HALTED", size_multiplier=0.0, reason="killswitch")
        else:
            profile = RiskProfile(mode="NORMAL", size_multiplier=1.0, reason="ok")
        return profile

    def _send(self, client, profile: RiskProfile):
        try:
            client.sendall(profile.to_json_line().encode("utf-8"))
        except OSError:
            with self._lock:
                if client in self._clients:
                    self._clients.remove(client)

    def push_profile(self, profile: RiskProfile):
        self.current_profile = profile
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            self._send(client, profile)
        self.logger.info("profile pushed", extra={"data": {"mode": profile.mode, "size_multiplier": profile.size_multiplier, "reason": profile.reason, "clients": len(clients)}})
