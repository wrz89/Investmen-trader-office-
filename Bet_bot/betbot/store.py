"""Archivio SQLite dell'ufficio sportivo.

Tabelle:
  matches        partite (calendario, stato, punteggio live, risultato finale)
  odds           storico delle quote per partita/bookmaker/selezione (feed real-time)
  bets           libro delle scommesse — IMMUTABILE tranne la chiusura (settlement)
  orders         ordini veri inviati a Betfair, registrati PRIMA dell'invio (bet_row_id = puntata del libro
                 a cui l'ordine appartiene: il BACK che la apre o il LAY che la chiude)
  shadow_bets    scommesse "ombra" delle strategie in osservazione (nessun capitale)
  bankroll       serie storica del bankroll (equity curve)
  events         registro eventi — IMMUTABILE
  agent_status   stato di ogni scrivania per la dashboard
  kv             valori chiave/valore (cash, picco, kill switch, ciclo)
  daily_reports  report giornalieri

Le scommesse possono essere aggiornate SOLO per essere chiuse (trigger SQLite):
puntata, quota e selezione non cambiano mai dopo l'inserimento.
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
CREATE TABLE IF NOT EXISTS matches (
    match_id TEXT PRIMARY KEY, sport TEXT, league TEXT, home TEXT, away TEXT,
    kickoff TEXT, status TEXT, minute INTEGER, home_score INTEGER, away_score INTEGER,
    result TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS odds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, match_id TEXT, bookmaker TEXT, market TEXT, selection TEXT,
    price REAL, live INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS odds_match ON odds(match_id, ts);
CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, cycle_id TEXT, mode TEXT, strategy_id TEXT, match_id TEXT, league TEXT, label TEXT,
    market TEXT, selection TEXT, bookmaker TEXT, odds REAL, fair_prob REAL, edge REAL,
    stake REAL, kelly_full REAL, live INTEGER DEFAULT 0, reason TEXT,
    status TEXT DEFAULT 'OPEN', settled_ts TEXT, payout REAL, pnl REAL,
    closing_odds REAL, clv REAL, settle_reason TEXT, extra TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, ref TEXT UNIQUE, strategy_id TEXT, market_id TEXT, selection_id TEXT, side TEXT, price REAL, size REAL,
    status TEXT, bet_id TEXT, matched REAL, avg_price REAL, error TEXT, updated TEXT,
    bet_row_id INTEGER
);
CREATE TABLE IF NOT EXISTS shadow_bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, strategy_id TEXT, match_id TEXT, label TEXT, selection TEXT, odds REAL,
    fair_prob REAL, edge REAL, stake REAL, status TEXT DEFAULT 'OPEN', settled_ts TEXT, pnl REAL
);
CREATE TABLE IF NOT EXISTS bankroll (
    ts TEXT, bankroll REAL, cash REAL, open_stakes REAL, profits REAL
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS daily_reports (day TEXT PRIMARY KEY, data TEXT, markdown TEXT);

CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'registro eventi immutabile'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'registro eventi immutabile'); END;
CREATE TRIGGER IF NOT EXISTS bets_no_delete BEFORE DELETE ON bets
BEGIN SELECT RAISE(ABORT, 'libro scommesse immutabile'); END;
CREATE TRIGGER IF NOT EXISTS bets_no_rewrite BEFORE UPDATE OF ts, strategy_id, match_id, selection,
    bookmaker, odds, stake, fair_prob, edge, mode, market ON bets
BEGIN SELECT RAISE(ABORT, 'una scommessa piazzata non si modifica: si può solo chiudere'); END;
CREATE TRIGGER IF NOT EXISTS bets_settle_once BEFORE UPDATE OF status ON bets
WHEN OLD.status != 'OPEN'
BEGIN SELECT RAISE(ABORT, 'scommessa già chiusa'); END;
"""


def now_iso() -> str:
    from . import clock
    return datetime.fromtimestamp(clock.now(), timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Colonne aggiunte dopo la prima versione: i database già esistenti le ricevono qui (ALTER TABLE)."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(orders)").fetchall()}
        if "bet_row_id" not in cols:
            self.conn.execute("ALTER TABLE orders ADD COLUMN bet_row_id INTEGER")
        if "role" not in cols:                     # 'open' = apre una posizione, 'close' = LAY di chiusura di un trade
            self.conn.execute("ALTER TABLE orders ADD COLUMN role TEXT")

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        with self._lock:
            self.conn.executemany(sql, rows)
            self.conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def get(self, key: str, default=None):
        rows = self.query("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set(self, key: str, value) -> None:
        self.execute("INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, json.dumps(value, default=str)))

    def event(self, agent: str, message: str, level: str = "INFO", kind: str = "info",
              payload: dict | None = None) -> None:
        self.execute("INSERT INTO events(ts, agent, level, kind, message, payload) VALUES(?,?,?,?,?,?)",
                     (now_iso(), agent, level, kind, message, json.dumps(payload, default=str) if payload else None))

    def set_status(self, agent: str, state: str, message: str, stats: dict | None = None) -> None:
        self.execute(
            "INSERT INTO agent_status(agent, state, message, updated, stats) VALUES(?,?,?,?,?) "
            "ON CONFLICT(agent) DO UPDATE SET state=excluded.state, message=excluded.message, "
            "updated=excluded.updated, stats=COALESCE(excluded.stats, agent_status.stats)",
            (agent, state, message, now_iso(), json.dumps(stats, default=str) if stats is not None else None))

    # ── partite e quote ────────────────────────────────────────
    def upsert_match(self, m: dict) -> None:
        self.execute(
            "INSERT INTO matches(match_id, sport, league, home, away, kickoff, status, minute, home_score, "
            "away_score, result, updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(match_id) DO UPDATE SET "
            "status=excluded.status, minute=excluded.minute, home_score=excluded.home_score, "
            "away_score=excluded.away_score, result=excluded.result, updated=excluded.updated",
            (m["match_id"], m.get("sport"), m.get("league"), m["home"], m["away"], m["kickoff"], m["status"],
             m.get("minute"), m.get("home_score"), m.get("away_score"), m.get("result"), now_iso()))

    def record_odds(self, rows: list[dict]) -> None:
        ts = now_iso()
        self.executemany("INSERT INTO odds(ts, match_id, bookmaker, market, selection, price, live) "
                         "VALUES(?,?,?,?,?,?,?)",
                         [(ts, r["match_id"], r["bookmaker"], r.get("market", "h2h"), r["selection"],
                           float(r["price"]), int(bool(r.get("live")))) for r in rows])

    def latest_odds(self, match_id: str, selection: str) -> list[dict]:
        """Ultima quota di ogni bookmaker per una selezione."""
        return self.query(
            "SELECT bookmaker, price, ts, live FROM odds WHERE id IN (SELECT MAX(id) FROM odds WHERE match_id=? "
            "AND selection=? GROUP BY bookmaker) ORDER BY price DESC", (match_id, selection))
