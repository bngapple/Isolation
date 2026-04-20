from __future__ import annotations

import json
import socket
import threading
from datetime import datetime

from utils.logger import get_logger


class NTBridge:
    def __init__(self, host, port, db, executor):
        self.host = host
        self.port = port
        self.db = db
        self.executor = executor
        self.logger = get_logger("nt_bridge")
        self._server = None
        self._thread = None
        self._running = False

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
        self.logger.info("bridge started", extra={"data": {"host": self.host, "port": self.port}})

    def stop(self):
        self._running = False
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass

    def _accept_loop(self):
        while self._running:
            try:
                client, address = self._server.accept()
            except OSError:
                break
            self.logger.info("client connected", extra={"data": {"address": address}})
            threading.Thread(target=self._client_loop, args=(client, address), daemon=True).start()

    def _client_loop(self, client, address):
        buffer = ""
        try:
            with client:
                while self._running:
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    buffer += chunk.decode("utf-8", errors="ignore")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        response = self._handle_line(line)
                        client.sendall((json.dumps(response) + "\n").encode("utf-8"))
        except OSError as exc:
            self.logger.error("client loop error", extra={"data": {"address": address, "error": str(exc)}})
        finally:
            self.logger.info("client disconnected", extra={"data": {"address": address}})

    def _handle_line(self, line: str) -> dict:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            self.logger.error("invalid json payload", extra={"data": {"payload": line[:200]}})
            return {"action": "HOLD", "reason": "invalid_json"}

        response = self.executor.handle_message(payload)
        self.logger.info(
            "bridge decision",
            extra={"data": {"type": payload.get("type"), "action": response.get("action"), "account": payload.get("account")}},
        )
        return response
