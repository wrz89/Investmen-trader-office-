"""Osservatorio dei tassi di finanziamento (config/funding_watch.yaml). SOLO lettura dati.

Stima quanto renderebbe "compro la moneta a pronti + vendo lo stesso importo in
contratto perpetuo": il prezzo si annulla, resta il funding (positivo = incasso).
Nessun ordine viene mai inviato: serve a decidere con dati veri se valga la pena
proporre un'eccezione alle regole.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import numpy as np

from .config import load_yaml

SCHEMA = """
CREATE TABLE IF NOT EXISTS funding_obs (
    symbol TEXT, ts INTEGER, rate REAL, PRIMARY KEY (symbol, ts)
);
"""
YEAR_MS = 365 * 86_400_000


def carry_stats(ts: list[int], rates: list[float], costs: dict) -> dict | None:
    """Rendimento annuo lordo (sul valore della posizione) e netto sul capitale totale."""
    if len(rates) < 3:
        return None
    r = np.asarray(rates, float)
    interval = float(np.median(np.diff(sorted(ts))))
    per_year = YEAR_MS / interval if interval > 0 else 1095
    gross = float(r.mean() * per_year)
    slip = costs["slippage_bps"] / 10_000
    round_trip = 2 * (costs["spot_taker"] + costs["perp_taker"]) + 4 * slip
    cost_year = round_trip * 365 / costs["holding_days"]
    capital = 1 + costs["margin_share"]
    return {"n": len(r), "days": (max(ts) - min(ts)) / 86_400_000, "positive_share": float((r > 0).mean()),
            "gross_annual": gross, "cost_annual": cost_year,
            "net_annual_on_capital": (gross - cost_year) / capital, "last_rate": float(r[-1]),
            "worst_rate": float(r.min())}


class FundingWatch:
    def __init__(self, office):
        self.office = office
        self.store = office.store
        self.cfg = load_yaml("funding_watch.yaml")
        with self.store._lock:
            self.store.conn.executescript(SCHEMA)
            self.store.conn.commit()
        self._ex = None

    def _exchange(self):
        if self._ex is None:
            import ccxt
            name = os.environ.get("OFFICE_FUNDING_EXCHANGE") or self.cfg["exchange"]
            self._ex = getattr(ccxt, name)({"enableRateLimit": True, "timeout": 15000, "requests_trust_env": True})
        return self._ex

    def run(self) -> None:
        last = self.store.get("funding_last_fetch") or 0
        if time.time() - last < self.cfg["refresh_minutes"] * 60:
            return
        self.store.set("funding_last_fetch", time.time())
        ex = self._exchange()
        start = int(time.time() * 1000) - self.cfg["backfill_days"] * 86_400_000
        added, failed = 0, []
        for sym in self.cfg["symbols"]:
            row = self.store.query("SELECT MAX(ts) AS t FROM funding_obs WHERE symbol=?", (sym,))[0]
            since = (row["t"] + 1) if row["t"] else start
            try:
                for _ in range(20):                                   # a pagine, fino ad oggi
                    hist = ex.fetch_funding_rate_history(sym, since=since, limit=200)
                    hist = [h for h in hist if h.get("timestamp") and h["timestamp"] >= since]
                    if not hist:
                        break
                    for h in hist:
                        cur = self.store.execute("INSERT OR IGNORE INTO funding_obs(symbol, ts, rate) VALUES(?,?,?)",
                                                 (sym, int(h["timestamp"]), float(h["fundingRate"])))
                        added += cur.rowcount
                    since = max(h["timestamp"] for h in hist) + 1
                    if len(hist) < 50:
                        break
            except Exception:
                failed.append(sym)
        if failed:
            self.office.scanner.log(f"Osservatorio funding: dati non disponibili per {', '.join(failed)} "
                                    "(nessun effetto sul trading).", "WARN", "funding")
        if added:
            s = self.summary()
            parts = [f"{a['symbol'].split('/')[0]} {a['observed']['net_annual_on_capital'] * 100:+.1f}%/anno"
                     for a in s["assets"] if a.get("observed")]
            self.office.scanner.log("Osservatorio funding (solo dati): " + (
                "netto sul capitale dal " + self.cfg["start"] + ": " + ", ".join(parts) if parts
                else f"{added} nuovi pagamenti registrati, osservazione appena iniziata."), "INFO", "funding")

    def summary(self) -> dict:
        cfg = self.cfg
        start_ms = int(datetime.fromisoformat(cfg["start"]).replace(tzinfo=timezone.utc).timestamp() * 1000)
        rv = cfg["review"]
        assets = []
        for sym in cfg["symbols"]:
            rows = self.store.query("SELECT ts, rate FROM funding_obs WHERE symbol=? ORDER BY ts", (sym,))
            hist = [r for r in rows if r["ts"] < start_ms]
            obs = [r for r in rows if r["ts"] >= start_ms]
            o = carry_stats([r["ts"] for r in obs], [r["rate"] for r in obs], cfg["costs"])
            h = carry_stats([r["ts"] for r in hist], [r["rate"] for r in hist], cfg["costs"])
            passing = bool(o and o["days"] >= rv["min_days"]
                           and o["net_annual_on_capital"] >= rv["min_net_annual_on_capital"]
                           and o["positive_share"] >= rv["min_positive_share"])
            assets.append({"symbol": sym, "observed": o, "history": h, "passing": passing})
        days = (time.time() * 1000 - start_ms) / 86_400_000
        return {"start": cfg["start"], "days": max(0.0, days), "review": rv, "costs": cfg["costs"],
                "exchange": cfg["exchange"], "assets": assets,
                "review_ready": days >= rv["min_days"],
                "review_passed": sum(a["passing"] for a in assets) >= rv["min_assets_passing"]}
