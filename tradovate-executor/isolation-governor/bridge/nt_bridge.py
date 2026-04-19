from __future__ import annotations

import socket
import threading

from governor.risk_profile import RiskProfile
from utils.logger import get_logger


class NTBridge:
    def __init__(self, host, port, db):
        self.host = host
        self.port = port
        self.db = db
        self.logger = get_logger("nt_bridge")
        self.current_profile = RiskProfile.offline_fallback()
        self._clients = []
        self._lock = threading.Lock()
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

    def _accept_loop(self):
        while self._running:
            try:
                client, address = self._server.accept()
            except OSError:
                break
            with self._lock:
                self._clients.append(client)
            self.logger.info("client connected", extra={"data": {"address": address}})
            self._send(client, self.current_profile)
            threading.Thread(target=self._client_loop, args=(client, address), daemon=True).start()

    def _client_loop(self, client, address):
        try:
            while self._running:
                data = client.recv(1024)
                if not data:
                    break
        finally:
            with self._lock:
                if client in self._clients:
                    self._clients.remove(client)
            try:
                client.close()
            except OSError:
                pass
            self.logger.info("client disconnected", extra={"data": {"address": address}})

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
