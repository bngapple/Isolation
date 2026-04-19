from __future__ import annotations

import sqlite3
from pathlib import Path


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        schema_path = Path(__file__).resolve().parent / "schema.sql"
        self.conn.executescript(schema_path.read_text())
        self.conn.commit()

    def execute(self, query, params=()):
        cur = self.conn.execute(query, params)
        self.conn.commit()
        return cur

    def fetchall(self, query, params=()):
        cur = self.conn.execute(query, params)
        return [dict(row) for row in cur.fetchall()]

    def fetchone(self, query, params=()):
        cur = self.conn.execute(query, params)
        row = cur.fetchone()
        return dict(row) if row is not None else None

    def close(self):
        self.conn.close()


DB = Database
