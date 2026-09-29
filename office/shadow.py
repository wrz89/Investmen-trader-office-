"""Osservazione in ombra.

Per le strategie promettenti ma non ancora provate: i loro segnali vengono seguiti
sui dati NUOVI come se fossero eseguiti, ma senza toccare il capitale (né paper né reale).
Il registro dei trade in ombra è separato e immutabile come quello vero.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np

from .store import Store, now_iso
from .validation import profit_factor

SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, strategy_id TEXT, symbol TEXT, entry_ts TEXT,
    entry_price REAL, stop REAL, stop_dist REAL, signal TEXT, is_open INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS shadow_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, strategy_id TEXT, symbol TEXT, ts_open TEXT, ts_close TEXT,
    entry REAL, exit REAL, stop_dist REAL, gross REAL, net REAL, reason TEXT
);
CREATE TRIGGER IF NOT EXISTS shadow_no_update BEFORE UPDATE ON shadow_trades
BEGIN SELECT RAISE(ABORT, 'registro ombra immutabile'); END;
CREATE TRIGGER IF NOT EXISTS shadow_no_delete BEFORE DELETE ON shadow_trades
BEGIN SELECT RAISE(ABORT, 'registro ombra immutabile'); END;
"""


class ShadowBook:
    def __init__(self, store: Store, costs: dict):
        self.store = store
        self.fee = costs["taker_fee"]
        self.slip = costs["slippage_bps"] / 10_000
        with store._lock:
            store.conn.executescript(SCHEMA)
            store.conn.commit()

    def open_positions(self) -> list[dict]:
        return self.store.query("SELECT * FROM shadow_positions WHERE is_open=1 ORDER BY id")

    def has_open(self, strategy_id: str, symbol: str) -> bool:
        return bool(self.store.query("SELECT 1 FROM shadow_positions WHERE is_open=1 AND strategy_id=? AND symbol=?",
                                     (strategy_id, symbol)))

    def open(self, opp: dict) -> dict:
        entry = opp["price"] * (1 + self.slip)
        stop_dist = max((entry - opp["stop"]) / entry, 1e-6)
        self.store.execute(
            "INSERT INTO shadow_positions(strategy_id, symbol, entry_ts, entry_price, stop, stop_dist, signal, is_open) "
            "VALUES(?,?,?,?,?,?,?,1)",
            (opp["strategy_id"], opp["symbol"], now_iso(), entry, opp["stop"], stop_dist, opp["signal"]))
        return {"entry": entry, "stop": opp["stop"]}

    def close(self, pos: dict, exit_ref: float, reason: str) -> dict:
        exit_px = exit_ref * (1 - self.slip)
        gross = exit_px / pos["entry_price"] - 1
        net = (exit_px * (1 - self.fee)) / (pos["entry_price"] * (1 + self.fee)) - 1
        self.store.execute(
            "INSERT INTO shadow_trades(strategy_id, symbol, ts_open, ts_close, entry, exit, stop_dist, gross, net, reason) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (pos["strategy_id"], pos["symbol"], pos["entry_ts"], now_iso(), pos["entry_price"], exit_px,
             pos["stop_dist"], gross, net, reason))
        self.store.execute("UPDATE shadow_positions SET is_open=0 WHERE id=?", (pos["id"],))
        return {"exit": exit_px, "gross": gross, "net": net}

    def summary(self, strategy_id: str, risk_per_trade: float, cap: float, since: str | None,
                sizing: str = "risk") -> dict:
        trades = self.store.query("SELECT * FROM shadow_trades WHERE strategy_id=? ORDER BY id", (strategy_id,))
        net = np.array([t["net"] for t in trades])
        size = (lambda t: cap) if sizing == "allocation" else (lambda t: min(risk_per_trade / max(t["stop_dist"], 1e-6), cap))
        sized = np.array([size(t) * t["net"] for t in trades])
        months = None
        if since:
            months = (datetime.now().date() - datetime.fromisoformat(since).date()).days / 30.44
        return {
            "since": since, "months": months,
            "trades": len(trades),
            "win_rate": float((net > 0).mean()) if len(net) else None,
            "profit_factor": profit_factor(net) if len(net) else None,
            "total_sized": float(np.prod(1 + sized) - 1) if len(sized) else 0.0,
            "open": self.store.query("SELECT symbol, entry_price, stop, entry_ts FROM shadow_positions "
                                     "WHERE is_open=1 AND strategy_id=?", (strategy_id,)),
            "last": trades[-5:][::-1],
        }
