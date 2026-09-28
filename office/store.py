"""Archivio SQLite dell'ufficio.

Contiene lo stato degli agenti (per la dashboard), il registro eventi,
le posizioni aperte e il libro contabile dei trade. Le tabelle `trades`
ed `events` sono immutabili: un trigger SQLite rifiuta UPDATE e DELETE,
così l'Auditor non può "correggere" il passato.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, agent TEXT NOT NULL, level TEXT NOT NULL,
    kind TEXT NOT NULL, message TEXT NOT NULL, payload TEXT
);
CREATE TABLE IF NOT EXISTS agent_status (
    agent TEXT PRIMARY KEY, state TEXT, message TEXT, updated TEXT, stats TEXT
);
CREATE TABLE IF NOT EXISTS strategy_status (
    strategy_id TEXT PRIMARY KEY, status TEXT, reason TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, cycle_id TEXT, strategy_id TEXT, symbol TEXT,
    data TEXT, decision TEXT, reasons TEXT
);
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT, symbol TEXT, qty REAL, entry_price REAL, expected_price REAL,
    entry_ts TEXT, stop REAL, entry_fee REAL, entry_slippage REAL,
    signal TEXT, entry_reason TEXT, is_open INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_open TEXT, ts_close TEXT, mode TEXT, symbol TEXT, strategy_id TEXT,
    signal TEXT, side TEXT, qty REAL,
    expected_entry REAL, exec_entry REAL, expected_exit REAL, exec_exit REAL,
    fees REAL, slippage REAL, pnl_gross REAL, pnl_net REAL,
    entry_reason TEXT, exit_reason TEXT
);
CREATE TABLE IF NOT EXISTS equity (
    ts TEXT, equity REAL, cash REAL, exposure REAL
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS daily_reports (day TEXT PRIMARY KEY, data TEXT, markdown TEXT);

CREATE TRIGGER IF NOT EXISTS trades_no_update BEFORE UPDATE ON trades
BEGIN SELECT RAISE(ABORT, 'registro trade immutabile'); END;
CREATE TRIGGER IF NOT EXISTS trades_no_delete BEFORE DELETE ON trades
BEGIN SELECT RAISE(ABORT, 'registro trade immutabile'); END;
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'registro eventi immutabile'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'registro eventi immutabile'); END;
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ── primitive ──────────────────────────────────────────────
    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    # ── key/value ──────────────────────────────────────────────
    def get(self, key: str, default=None):
        rows = self.query("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set(self, key: str, value) -> None:
        self.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    # ── eventi e stato agenti ─────────────────────────────────
    def event(self, agent: str, message: str, level: str = "INFO",
              kind: str = "info", payload: dict | None = None) -> None:
        self.execute(
            "INSERT INTO events(ts, agent, level, kind, message, payload) VALUES(?,?,?,?,?,?)",
            (now_iso(), agent, level, kind, message,
             json.dumps(payload, default=str) if payload else None),
        )

    def set_status(self, agent: str, state: str, message: str, stats: dict | None = None) -> None:
        self.execute(
            "INSERT INTO agent_status(agent, state, message, updated, stats) VALUES(?,?,?,?,?) "
            "ON CONFLICT(agent) DO UPDATE SET state=excluded.state, message=excluded.message, "
            "updated=excluded.updated, stats=COALESCE(excluded.stats, agent_status.stats)",
            (agent, state, message, now_iso(),
             json.dumps(stats, default=str) if stats is not None else None),
        )

    # ── strategie ──────────────────────────────────────────────
    def strategy_status(self, strategy_id: str) -> str | None:
        rows = self.query("SELECT status FROM strategy_status WHERE strategy_id=?", (strategy_id,))
        return rows[0]["status"] if rows else None

    def set_strategy_status(self, strategy_id: str, status: str, reason: str) -> None:
        self.execute(
            "INSERT INTO strategy_status(strategy_id, status, reason, updated) VALUES(?,?,?,?) "
            "ON CONFLICT(strategy_id) DO UPDATE SET status=excluded.status, "
            "reason=excluded.reason, updated=excluded.updated",
            (strategy_id, status, reason, now_iso()),
        )
