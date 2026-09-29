"""Piano di accumulo automatico (config/accumulation.yaml).

Non è una strategia di trading: ogni mese, dal giorno stabilito, compra un
importo FISSO in euro di BTC. Marco lo avvia, Franco lo controlla (può solo
rimandarlo al ciclo successivo, mai saltarlo), Paolo lo esegue scegliendo la
strada più economica, Elena lo registra in un libro separato e immutabile.
Non vende mai e non compra mai di più dopo un calo.

Strade confrontate a ogni acquisto (e misurate a ogni ciclo, per i dati):
  diretta   EUR → BTC            (1 commissione)
  via USDC  EUR → USDC → BTC     (2 commissioni)
"""
from __future__ import annotations

import time
from datetime import datetime

from .config import load_yaml
from .store import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS accumulation_buys (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, month TEXT, mode TEXT, route TEXT,
    eur REAL, price_eur REAL, qty REAL, fee_eur REAL, note TEXT
);
CREATE TRIGGER IF NOT EXISTS accum_no_update BEFORE UPDATE ON accumulation_buys
BEGIN SELECT RAISE(ABORT, 'registro accumulo immutabile'); END;
CREATE TRIGGER IF NOT EXISTS accum_no_delete BEFORE DELETE ON accumulation_buys
BEGIN SELECT RAISE(ABORT, 'registro accumulo immutabile'); END;
CREATE TABLE IF NOT EXISTS route_gaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, direct REAL, via_usdc REAL, gap_bps REAL, best TEXT
);
"""


def due_month(now: datetime, cfg: dict, bought: set[str]) -> str | None:
    """Mese dovuto (AAAA-MM) se oggi è dal giorno stabilito in poi e il mese non è ancora stato comprato."""
    if not cfg.get("enabled"):
        return None
    month = now.strftime("%Y-%m")
    if month < cfg["start_month"] or now.day < cfg["day_of_month"] or month in bought:
        return None
    return month


def best_route(direct_ask: float | None, btc_usdc_ask: float | None, usdc_eur_ask: float | None,
               fee: float, slip: float) -> dict:
    """Costo effettivo (euro per 1 BTC, commissioni comprese) delle due strade."""
    routes = {}
    if direct_ask:
        routes["diretta"] = direct_ask * (1 + slip) * (1 + fee)
    if btc_usdc_ask and usdc_eur_ask:
        routes["via USDC"] = btc_usdc_ask * (1 + slip) * usdc_eur_ask * (1 + slip) * (1 + fee) ** 2
    if not routes:
        return {"best": None, "routes": routes, "gap_bps": None}
    best = min(routes, key=routes.get)
    gap = ((routes.get("via USDC") / routes["diretta"] - 1) * 10_000
           if len(routes) == 2 else None)
    return {"best": best, "routes": routes, "gap_bps": gap}


class Accumulation:
    def __init__(self, office):
        self.office = office
        self.store = office.store
        self.cfg = load_yaml("accumulation.yaml")
        with self.store._lock:
            self.store.conn.executescript(SCHEMA)
            self.store.conn.commit()

    # ── prezzi delle due strade ─────────────────────────────
    def _quotes(self, snapshot: dict) -> dict:
        md = self.office.market
        out = {"direct": None, "direct_bid": None, "usdc_eur": None, "spread_bps": None, "age_s": None}
        try:
            t = md.ticker(self.cfg["symbol"])
            out["direct"], out["direct_bid"] = t.get("ask"), t.get("bid")
            if t.get("ask") and t.get("bid"):
                out["spread_bps"] = (t["ask"] - t["bid"]) / ((t["ask"] + t["bid"]) / 2) * 10_000
            out["age_s"] = time.time() - t["timestamp"] / 1000 if t.get("timestamp") else 0.0
        except Exception:
            pass
        try:
            out["usdc_eur"] = md.ticker("USDC/EUR").get("ask")
        except Exception:
            pass
        out["btc_usdc"] = (snapshot["symbols"].get("BTC/USDC") or {}).get("ask")
        return out

    def run(self, snapshot: dict) -> None:
        cfg, costs = self.cfg, self.office.settings["costs"]
        fee, slip = costs["taker_fee"], costs["slippage_bps"] / 10_000
        q = self._quotes(snapshot)
        route = best_route(q["direct"], q["btc_usdc"], q["usdc_eur"], fee, slip)
        if route["gap_bps"] is not None:
            self.store.execute("INSERT INTO route_gaps(ts, direct, via_usdc, gap_bps, best) VALUES(?,?,?,?,?)",
                               (now_iso(), route["routes"]["diretta"], route["routes"]["via USDC"],
                                route["gap_bps"], route["best"]))
        self.store.set("accumulation_quote", {"price": q["direct_bid"], "updated": time.time(),
                                              "route": route})

        bought = {r["month"] for r in self.store.query("SELECT month FROM accumulation_buys")}
        month = due_month(datetime.now(), cfg, bought)
        if not month:
            return
        pm, risk, ex, aud = self.office.pm, self.office.risk, self.office.execution, self.office.auditor
        if self.store.get("accum_announced") != month:
            pm.say(f"Piano di accumulo: è il momento dell'acquisto di {month} ({cfg['amount_eur']:.0f} € di BTC).",
                   "working", "accumulation")
            self.store.set("accum_announced", month)
        ok, reasons = risk.check_accumulation(q, snapshot)
        if not ok:
            key = f"{month}|{reasons[0]}"
            if self.store.get("accum_last_postpone") != key:      # un solo messaggio per motivo
                risk.say(f"Accumulo {month} RIMANDATO al prossimo ciclo: {'; '.join(reasons)}. "
                         "L'acquisto resta dovuto.", "blocked", "accumulation")
                self.store.set("accum_last_postpone", key)
            return
        if route["best"] is None:
            risk.say("Accumulo rimandato: nessun prezzo disponibile.", "blocked", "accumulation")
            return
        cost_per_btc = route["routes"][route["best"]]
        eur = self.next_amount()
        qty = eur / cost_per_btc
        fee_eur = eur - qty * (q["direct"] if route["best"] == "diretta" else cost_per_btc / (1 + fee) ** 2)
        mode = self.office.settings["mode"]
        other = [k for k in route["routes"] if k != route["best"]]
        note = (f"alternativa {other[0]} {route['routes'][other[0]]:,.0f} €/BTC" if other else "unica strada disponibile")
        self.store.execute(
            "INSERT INTO accumulation_buys(ts, month, mode, route, eur, price_eur, qty, fee_eur, note) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (now_iso(), month, mode, route["best"], eur, cost_per_btc, qty, fee_eur, note))
        ex.say(f"Accumulo {month} ({mode}): comprati {qty:.8f} BTC con {eur:.0f} € "
               f"(strada {route['best']}, {cost_per_btc:,.0f} €/BTC commissioni comprese; {note}).",
               "ok", "fill", payload={"accumulation": True, "month": month})
        s = self.summary()
        aud.say(f"Registro accumulo: {s['buys']} acquisti, {s['eur_in']:.0f} € versati, {s['qty']:.8f} BTC.",
                "ok", "accumulation")

    def next_amount(self) -> float:
        """Importo del prossimo acquisto: quota fissa + eventuale rata del capitale iniziale (calendario prefissato)."""
        cfg = self.cfg
        done = self.store.query("SELECT COUNT(*) AS n FROM accumulation_buys")[0]["n"]
        n, initial = int(cfg.get("initial_tranches") or 0), float(cfg.get("initial_eur") or 0)
        return float(cfg["amount_eur"]) + (initial / n if n and done < n else 0.0)

    def summary(self) -> dict:
        rows = self.store.query("SELECT * FROM accumulation_buys ORDER BY id")
        eur_in = sum(r["eur"] for r in rows)
        qty = sum(r["qty"] for r in rows)
        quote = self.store.get("accumulation_quote") or {}
        price = quote.get("price")
        value = qty * price if price else None
        gaps = self.store.query("SELECT gap_bps, best FROM route_gaps ORDER BY id DESC LIMIT 2000")
        cfg = self.cfg
        bought = {r["month"] for r in rows}
        return {
            "enabled": cfg.get("enabled"), "amount_eur": cfg["amount_eur"], "day_of_month": cfg["day_of_month"],
            "initial_eur": cfg.get("initial_eur", 0), "initial_tranches": cfg.get("initial_tranches", 0),
            "next_amount": self.next_amount(),
            "symbol": cfg["symbol"], "start_month": cfg["start_month"], "go_live": cfg.get("go_live"),
            "buys": len(rows), "eur_in": eur_in, "qty": qty, "price": price, "value": value,
            "avg_price": eur_in / qty if qty else None,
            "last": rows[-6:][::-1], "route": quote.get("route"),
            "gaps": {"n": len(gaps),
                     "via_usdc_better": sum(g["best"] == "via USDC" for g in gaps),
                     "best_gap_bps": min((g["gap_bps"] for g in gaps), default=None),
                     "median_gap_bps": sorted(g["gap_bps"] for g in gaps)[len(gaps) // 2] if gaps else None},
            "due_now": due_month(datetime.now(), cfg, bought) is not None,
        }
