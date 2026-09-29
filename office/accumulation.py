"""Piano di accumulo automatico (config/accumulation.yaml).

Non è una strategia di trading: ogni mese, dal giorno stabilito, investe un
importo FISSO in euro divise tra più crypto con quote obiettivo. Marco lo avvia,
Franco controlla ogni asset (può solo rimandarne l'acquisto al ciclo successivo,
mai saltarlo o ingrandirlo), Paolo esegue scegliendo la strada più economica,
Elena registra in un libro separato e immutabile.

- Ribilanciamento solo con i soldi nuovi: l'importo del mese va prima agli asset
  sotto la loro quota. Mai vendite, mai acquisti extra dopo un calo.
- Strade confrontate per ogni asset (e misurate a ogni ciclo, per i dati):
    diretta   EUR → asset            (1 commissione)
    via USDC  EUR → USDC → asset     (2 commissioni)
- Ordine limite al miglior prezzo di acquisto (commissione maker); se non viene
  eseguito entro il tempo stabilito si annulla e si compra a mercato.
"""
from __future__ import annotations

import time
from datetime import datetime

from .config import load_yaml
from .store import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS accumulation_buys (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, month TEXT, mode TEXT, route TEXT,
    eur REAL, price_eur REAL, qty REAL, fee_eur REAL, note TEXT, asset TEXT
);
CREATE TRIGGER IF NOT EXISTS accum_no_update BEFORE UPDATE ON accumulation_buys
BEGIN SELECT RAISE(ABORT, 'registro accumulo immutabile'); END;
CREATE TRIGGER IF NOT EXISTS accum_no_delete BEFORE DELETE ON accumulation_buys
BEGIN SELECT RAISE(ABORT, 'registro accumulo immutabile'); END;
CREATE TABLE IF NOT EXISTS route_gaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, direct REAL, via_usdc REAL, gap_bps REAL, best TEXT, asset TEXT
);
"""


def allocation_of(cfg: dict) -> dict[str, float]:
    return cfg.get("allocation") or {cfg.get("symbol", "BTC/EUR"): 1.0}


def due_month(now: datetime, cfg: dict, bought: set[str]) -> str | None:
    """Mese dovuto (AAAA-MM) se oggi è dal giorno stabilito in poi e il mese non è ancora completo."""
    if not cfg.get("enabled"):
        return None
    month = now.strftime("%Y-%m")
    if month < cfg["start_month"] or now.day < cfg["day_of_month"] or month in bought:
        return None
    return month


def split_amount(amount: float, targets: dict[str, float], values: dict[str, float], min_leg: float) -> dict[str, float]:
    """Divide l'importo del mese: prima agli asset sotto quota, così il portafoglio torna verso gli obiettivi
    senza mai vendere. Le quote sotto `min_leg` passano all'asset più sotto peso."""
    total_after = sum(values.values()) + amount
    need = {a: max(0.0, w * total_after - values.get(a, 0.0)) for a, w in targets.items()}
    tot_need = sum(need.values())
    legs = ({a: amount * n / tot_need for a, n in need.items()} if tot_need > 0
            else {a: amount * w for a, w in targets.items()})
    small = [a for a, v in legs.items() if 0 < v < min_leg]
    if small and len(small) < len(legs):
        top = max(legs, key=lambda a: need[a] if tot_need > 0 else targets[a])
        for a in small:
            if a != top:
                legs[top] += legs.pop(a)
    return {a: round(v, 2) for a, v in legs.items() if v > 0}


def best_route(direct_ask: float | None, usdc_ask: float | None, usdc_eur_ask: float | None,
               fee: float, slip: float) -> dict:
    """Costo effettivo (euro per 1 unità, commissioni comprese) delle due strade."""
    routes = {}
    if direct_ask:
        routes["diretta"] = direct_ask * (1 + slip) * (1 + fee)
    if usdc_ask and usdc_eur_ask:
        routes["via USDC"] = usdc_ask * (1 + slip) * usdc_eur_ask * (1 + slip) * (1 + fee) ** 2
    if not routes:
        return {"best": None, "routes": routes, "gap_bps": None}
    best = min(routes, key=routes.get)
    gap = (routes["via USDC"] / routes["diretta"] - 1) * 10_000 if len(routes) == 2 else None
    return {"best": best, "routes": routes, "gap_bps": gap}


class Accumulation:
    def __init__(self, office):
        self.office = office
        self.store = office.store
        self.cfg = load_yaml("accumulation.yaml")
        with self.store._lock:
            self.store.conn.executescript(SCHEMA)
            for table in ("accumulation_buys", "route_gaps"):          # database creati prima dei multi-asset
                cols = {r[1] for r in self.store.conn.execute(f"PRAGMA table_info({table})")}
                if "asset" not in cols:
                    self.store.conn.execute(f"ALTER TABLE {table} ADD COLUMN asset TEXT")
            self.store.conn.commit()

    @property
    def targets(self) -> dict[str, float]:
        return allocation_of(self.cfg)

    # ── prezzi ──────────────────────────────────────────────
    def _quote(self, symbol: str, snapshot: dict, usdc_eur: float | None) -> dict:
        md = self.office.market
        q = {"symbol": symbol, "direct": None, "bid": None, "spread_bps": None, "age_s": None}
        try:
            t = md.ticker(symbol)
            q["direct"], q["bid"] = t.get("ask"), t.get("bid")
            if t.get("ask") and t.get("bid"):
                q["spread_bps"] = (t["ask"] - t["bid"]) / ((t["ask"] + t["bid"]) / 2) * 10_000
            q["age_s"] = time.time() - t["timestamp"] / 1000 if t.get("timestamp") else 0.0
        except Exception:
            pass
        base = symbol.split("/")[0]
        q["usdc_ask"] = (snapshot["symbols"].get(f"{base}/USDC") or {}).get("ask")
        q["usdc_eur"] = usdc_eur
        return q

    def run(self, snapshot: dict) -> None:
        cfg, costs = self.cfg, self.office.settings["costs"]
        fee, slip = costs["taker_fee"], costs["slippage_bps"] / 10_000
        try:
            usdc_eur = self.office.market.ticker("USDC/EUR").get("ask")
        except Exception:
            usdc_eur = None
        quotes, prices = {}, {}
        for sym in self.targets:
            q = self._quote(sym, snapshot, usdc_eur)
            q["route"] = best_route(q["direct"], q["usdc_ask"], usdc_eur, fee, slip)
            quotes[sym] = q
            prices[sym] = q["bid"]
            r = q["route"]
            if r["gap_bps"] is not None:
                self.store.execute("INSERT INTO route_gaps(ts, direct, via_usdc, gap_bps, best, asset) VALUES(?,?,?,?,?,?)",
                                   (now_iso(), r["routes"]["diretta"], r["routes"]["via USDC"], r["gap_bps"], r["best"], sym))
        self.store.set("accumulation_quote", {"prices": prices, "updated": time.time(),
                                              "routes": {s: q["route"] for s, q in quotes.items()}})

        month = due_month(datetime.now(), cfg, self._completed_months())
        if not month:
            return
        pm, risk, ex, aud = self.office.pm, self.office.risk, self.office.execution, self.office.auditor
        plan = self.store.get("accum_plan") or {}
        if plan.get("month") != month:                          # la divisione del mese si decide una volta sola
            amount = self.next_amount()
            values = {a: h["qty"] * (prices.get(a) or 0) for a, h in self.holdings().items()}
            legs = split_amount(amount, self.targets, values, float(cfg.get("min_leg_eur", 5)))
            plan = {"month": month, "legs": legs}
            self.store.set("accum_plan", plan)
            pm.say(f"Piano di accumulo: acquisti di {month}, {amount:.0f} € → "
                   + ", ".join(f"{a.split('/')[0]} {v:.2f} €" for a, v in legs.items())
                   + " (prima gli asset sotto quota).", "working", "accumulation")
        done = self._legs_done(month)
        mode = self.office.settings["mode"]
        ex_cfg = cfg.get("execution") or {}
        orders = self.store.get("accum_orders") or {}
        for sym, eur in plan["legs"].items():
            if sym in done:
                continue
            q = quotes[sym]
            ok, reasons = risk.check_accumulation(q, snapshot, sym.split("/")[0])
            route = q["route"]
            if ok and route["best"] is None:
                ok, reasons = False, ["nessun prezzo disponibile"]
            pending = orders.get(sym) if (orders.get(sym) or {}).get("month") == month else None
            if not ok:
                if pending:                                                 # prudenza: niente ordini in attesa
                    orders.pop(sym, None)
                    ex.say(f"Accumulo {month} · {sym}: annullo l'ordine limite in attesa ({reasons[0]}).",
                           "blocked", "accumulation")
                key = f"{month}|{sym}|{reasons[0]}"
                if self.store.get(f"accum_postpone_{sym}") != key:          # un solo messaggio per motivo
                    risk.say(f"Accumulo {month} · {sym} RIMANDATO al prossimo ciclo: {'; '.join(reasons)}. "
                             "L'acquisto resta dovuto.", "blocked", "accumulation")
                    self.store.set(f"accum_postpone_{sym}", key)
                continue
            if pending:
                if self._limit_filled(sym, pending):
                    maker = costs.get("maker_fee", fee)
                    cost = pending["limit"] * (1 + maker)
                    self._record(month, mode, "limite (maker)", sym, eur, cost, eur / cost,
                                 eur - (eur / cost) * pending["limit"],
                                 f"ordine limite eseguito; a mercato sarebbe costato {route['routes'].get(route['best'], 0):,.2f} €")
                    orders.pop(sym, None)
                    continue
                hours = (time.time() - pending["placed"]) / 3600
                if hours < float(ex_cfg.get("limit_timeout_hours", 24)):
                    continue                                                # resta in attesa
                orders.pop(sym, None)
                ex.say(f"Accumulo {month} · {sym}: ordine limite non eseguito in {hours:.0f} ore, "
                       "lo annullo e compro a mercato.", "working", "accumulation")
            elif ex_cfg.get("order_type") == "limit" and route["best"] == "diretta" and q.get("bid"):
                orders[sym] = {"month": month, "eur": eur, "limit": q["bid"], "placed": time.time()}
                ex.say(f"Accumulo {month} · {sym}: ordine limite di {eur:.2f} € a {q['bid']:,.2f} € "
                       f"(commissione maker). Se non si esegue entro {ex_cfg.get('limit_timeout_hours', 24)} ore "
                       "compro a mercato.", "working", "accumulation")
                continue
            cost = route["routes"][route["best"]]
            qty = eur / cost
            ref = q["direct"] if route["best"] == "diretta" else cost / (1 + fee) ** 2
            fee_eur = eur - qty * ref
            other = [k for k in route["routes"] if k != route["best"]]
            note = (f"alternativa {other[0]} {route['routes'][other[0]]:,.2f} €" if other else "unica strada disponibile")
            self._record(month, mode, route["best"], sym, eur, cost, qty, fee_eur, note)
        self.store.set("accum_orders", orders)
        if set(plan["legs"]) <= set(self._legs_done(month)):
            s = self.summary()
            aud.say(f"Registro accumulo: mese {month} completo. Versati {s['eur_in']:.0f} € in totale, "
                    f"valore {s['value'] or 0:.2f} €.", "ok", "accumulation")

    def _record(self, month, mode, route, sym, eur, cost, qty, fee_eur, note) -> None:
        self.store.execute(
            "INSERT INTO accumulation_buys(ts, month, mode, route, eur, price_eur, qty, fee_eur, note, asset) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)", (now_iso(), month, mode, route, eur, cost, qty, fee_eur, note, sym))
        self.office.execution.say(
            f"Accumulo {month} ({mode}): {qty:.8f} {sym.split('/')[0]} con {eur:.2f} € "
            f"(strada {route}, {cost:,.2f} € commissioni comprese; {note}).",
            "ok", "fill", payload={"accumulation": True, "month": month, "asset": sym})

    def _limit_filled(self, sym: str, order: dict) -> bool:
        """Paper: l'ordine limite si considera eseguito solo se dopo l'inserimento il prezzo è sceso SOTTO il limite."""
        try:
            df = self.office.market.candles(sym, "5m", limit=320)
        except Exception:
            return False
        after = df[df["ts"] >= order["placed"] * 1000]
        return bool(len(after) and float(after["low"].min()) < order["limit"])

    # ── libro ───────────────────────────────────────────────
    def _rows(self) -> list[dict]:
        rows = self.store.query("SELECT * FROM accumulation_buys ORDER BY id")
        for r in rows:
            r["asset"] = r.get("asset") or "BTC/EUR"
        return rows

    def _legs_done(self, month: str) -> set[str]:
        return {r["asset"] for r in self._rows() if r["month"] == month}

    def _completed_months(self) -> set[str]:
        plan = self.store.get("accum_plan") or {}
        months = {r["month"] for r in self._rows()}
        # un mese è completo quando tutte le sue parti pianificate sono state comprate
        return {m for m in months if m != plan.get("month") or set(plan.get("legs", {})) <= self._legs_done(m)}

    def holdings(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for r in self._rows():
            h = out.setdefault(r["asset"], {"qty": 0.0, "eur": 0.0})
            h["qty"] += r["qty"]
            h["eur"] += r["eur"]
        return out

    def next_amount(self) -> float:
        """Importo del mese: quota fissa + eventuale rata del capitale iniziale (calendario prefissato)."""
        cfg = self.cfg
        months_done = len({r["month"] for r in self._rows()})     # si calcola prima del primo acquisto del mese
        n, initial = int(cfg.get("initial_tranches") or 0), float(cfg.get("initial_eur") or 0)
        return float(cfg["amount_eur"]) + (initial / n if n and months_done < n else 0.0)

    def summary(self) -> dict:
        rows = self._rows()
        quote = self.store.get("accumulation_quote") or {}
        prices = quote.get("prices") or {}
        hold = self.holdings()
        assets = []
        total_value = 0.0
        for sym, w in self.targets.items():
            h = hold.get(sym, {"qty": 0.0, "eur": 0.0})
            val = h["qty"] * prices[sym] if prices.get(sym) else None
            total_value += val or 0.0
            assets.append({"symbol": sym, "target": w, "qty": h["qty"], "eur_in": h["eur"], "value": val,
                           "price": prices.get(sym)})
        for a in assets:
            a["weight"] = (a["value"] or 0) / total_value if total_value else None
        gaps = self.store.query("SELECT gap_bps, best FROM route_gaps ORDER BY id DESC LIMIT 3000")
        cfg = self.cfg
        eur_in = sum(r["eur"] for r in rows)
        return {
            "enabled": cfg.get("enabled"), "amount_eur": cfg["amount_eur"], "day_of_month": cfg["day_of_month"],
            "initial_eur": cfg.get("initial_eur", 0), "initial_tranches": cfg.get("initial_tranches", 0),
            "next_amount": self.next_amount(), "start_month": cfg["start_month"], "go_live": cfg.get("go_live"),
            "months": len({r["month"] for r in rows}), "buys": len(rows), "eur_in": eur_in,
            "value": total_value if rows else None, "assets": assets,
            "last": rows[-8:][::-1],
            "orders": self.store.get("accum_orders") or {},
            "order_type": (cfg.get("execution") or {}).get("order_type", "market"),
            "gaps": {"n": len(gaps), "via_usdc_better": sum(g["best"] == "via USDC" for g in gaps),
                     "best_gap_bps": min((g["gap_bps"] for g in gaps), default=None),
                     "median_gap_bps": sorted(g["gap_bps"] for g in gaps)[len(gaps) // 2] if gaps else None},
            "due_now": due_month(datetime.now(), cfg, self._completed_months()) is not None,
        }
