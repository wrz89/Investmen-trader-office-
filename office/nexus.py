"""Nexus: allocatore autonomo di un capitale di 30 € (config/nexus.yaml).

Libro separato e immutabile (nexus_ledger, nexus_decisions): Nexus non tocca mai il
conto delle strategie né il piano di accumulo. A ogni ciclo (al massimo uno al giorno)
valuta i candidati con numeri scritti PRIMA, decide la postura e registra la decisione.
Le spese passano solo da `spend`, che applica le regole del file: riserva minima,
tetto per singola spesa, niente costi ricorrenti, ritorno atteso ≥ soglia, cancello superato.
Il codice non può pagare nulla da solo: una spesa registrata è un'autorizzazione che
l'utente esegue con il proprio mezzo di pagamento.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta

from .config import load_yaml
from .store import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS nexus_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, kind TEXT NOT NULL, venture TEXT, eur REAL NOT NULL, note TEXT
);
CREATE TRIGGER IF NOT EXISTS nexus_ledger_no_update BEFORE UPDATE ON nexus_ledger
BEGIN SELECT RAISE(ABORT, 'libro Nexus immutabile'); END;
CREATE TRIGGER IF NOT EXISTS nexus_ledger_no_delete BEFORE DELETE ON nexus_ledger
BEGIN SELECT RAISE(ABORT, 'libro Nexus immutabile'); END;
CREATE TABLE IF NOT EXISTS nexus_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, posture TEXT NOT NULL, action TEXT NOT NULL, milestone TEXT, data TEXT
);
CREATE TRIGGER IF NOT EXISTS nexus_decisions_no_update BEFORE UPDATE ON nexus_decisions
BEGIN SELECT RAISE(ABORT, 'registro decisioni Nexus immutabile'); END;
CREATE TRIGGER IF NOT EXISTS nexus_decisions_no_delete BEFORE DELETE ON nexus_decisions
BEGIN SELECT RAISE(ABORT, 'registro decisioni Nexus immutabile'); END;
CREATE TABLE IF NOT EXISTS nexus_ventures (
    id TEXT PRIMARY KEY, status TEXT NOT NULL, opened TEXT, review_by TEXT,
    traction REAL DEFAULT 0, closed TEXT, reason TEXT
);
"""

# Stati di un'ipotesi: aperta → (cancello superato) spesa approvata → chiusa / bocciata
OPEN, APPROVED, CLOSED, REJECTED = "aperta", "spesa approvata", "chiusa", "bocciata"


def trading_round_trip_cost(capital_eur: float, costs: dict) -> float:
    """Commissioni di un giro completo (compro e vendo) a mercato, slippage e spread compresi."""
    per_side = costs["taker_fee"] + costs["slippage_bps"] / 10_000
    return capital_eur * (2 * per_side + costs["default_spread_bps"] / 10_000)


def evaluate(cfg: dict, costs: dict | None = None) -> list[dict]:
    """Applica le regole a ogni candidato. Puro: niente rete, niente database."""
    rules, budget = cfg["rules"], float(cfg["budget_eur"])
    out = []
    for c in cfg["candidates"]:
        cost = float(c.get("cost_eur") or 0.0)
        if c["id"] == "bybit_spot_trading" and costs:
            cost = trading_round_trip_cost(budget, costs)        # il costo vero sono le commissioni
        ret, prob = float(c.get("expected_return_eur") or 0.0), float(c.get("probability") or 0.0)
        if c.get("gate"):                                        # un candidato con cancello si giudica a cancello aperto
            prob = float(c.get("probability_after_gate") or prob)
        expected_net = prob * ret - cost                         # valore atteso netto (perdita certa del costo)
        checks = {
            "costo entro il tetto": cost <= rules["max_single_spend_eur"],
            "lascia la riserva minima": budget - cost >= rules["min_cash_reserve_eur"],
            "ritorno netto atteso ≥ soglia": expected_net >= max(rules["min_expected_net_ratio"] * cost,
                                                                 rules["min_expected_net_eur"]),
            "probabilità ≥ soglia": prob >= rules["min_probability"],
            "cancello superato": not c.get("gate"),
        }
        zero_cost_probe = cost == 0 and c.get("active")
        if c.get("kind") == "riserva":
            verdict = "riserva"
        elif zero_cost_probe:
            verdict = "ipotesi attiva"
        elif c.get("gate") and all(v for k, v in checks.items() if k != "cancello superato"):
            verdict = "in attesa del cancello"
        elif all(checks.values()) and expected_net > 0:
            verdict = "spesa approvabile"
        else:
            verdict = "bocciata"
        out.append({**c, "cost_eur": round(cost, 4), "expected_net_eur": round(expected_net, 4),
                    "checks": checks, "verdict": verdict})
    return out


def posture_of(cands: list[dict], balance: float, budget: float) -> str:
    if any(c["verdict"] == "spesa approvabile" for c in cands):
        return "Investimento mirato"
    if any(c["verdict"] == "ipotesi attiva" for c in cands):
        return "Liquidità al 100% + validazione a costo zero" if balance >= budget - 0.005 else "Validazione in corso"
    return "Liquidità al 100%"


class Nexus:
    def __init__(self, office):
        self.office = office
        self.store = office.store
        self.cfg = load_yaml("nexus.yaml")
        self.costs = getattr(office, "settings", None) and office.settings.get("costs")
        with self.store._lock:
            self.store.conn.executescript(SCHEMA)
            self.store.conn.commit()

    # ── libro ────────────────────────────────────────────────
    def _ensure_deposit(self) -> None:
        if not self.store.query("SELECT 1 FROM nexus_ledger WHERE kind='dotazione'"):
            self.store.execute("INSERT INTO nexus_ledger(ts, kind, venture, eur, note) VALUES(?,?,?,?,?)",
                               (now_iso(), "dotazione", None, float(self.cfg["budget_eur"]),
                                f"dotazione iniziale del {self.cfg['started']}: non verrà mai aggiunto altro denaro"))

    def totals(self) -> dict:
        rows = self.store.query("SELECT kind, COALESCE(SUM(eur),0) AS eur FROM nexus_ledger GROUP BY kind")
        t = {r["kind"]: float(r["eur"]) for r in rows}
        dep, spent, inc = t.get("dotazione", 0.0), t.get("spesa", 0.0), t.get("incasso", 0.0)
        return {"deposit": dep, "spent": spent, "income": inc, "balance": dep - spent + inc}

    def venture(self, vid: str) -> dict | None:
        rows = self.store.query("SELECT * FROM nexus_ventures WHERE id=?", (vid,))
        return rows[0] if rows else None

    def spend(self, vid: str, eur: float, note: str = "") -> dict:
        """Registra una spesa SOLO se tutte le regole sono rispettate. Altrimenti ValueError."""
        rules, eur = self.cfg["rules"], float(eur)
        if eur <= 0:
            raise ValueError("Importo non valido.")
        v = self.venture(vid)
        if not v or v["status"] != APPROVED:
            raise ValueError(f"'{vid}' non ha una spesa approvata: il cancello va superato prima (stato: "
                             f"{v['status'] if v else 'sconosciuta'}).")
        if eur > rules["max_single_spend_eur"]:
            raise ValueError(f"Spesa {eur:.2f} € oltre il tetto di {rules['max_single_spend_eur']:.2f} €.")
        bal = self.totals()["balance"]
        if bal - eur < rules["min_cash_reserve_eur"]:
            raise ValueError(f"Con {eur:.2f} € la liquidità scenderebbe a {bal - eur:.2f} €, sotto la riserva "
                             f"minima di {rules['min_cash_reserve_eur']:.2f} €.")
        self.store.execute("INSERT INTO nexus_ledger(ts, kind, venture, eur, note) VALUES(?,?,?,?,?)",
                           (now_iso(), "spesa", vid, eur, note))
        self.store.event("nexus", f"Nexus: spesa autorizzata di {eur:.2f} € per '{vid}'. {note}".strip(), "WARN", "nexus")
        return self.totals()

    def income(self, vid: str, eur: float, note: str = "") -> dict:
        if float(eur) <= 0:
            raise ValueError("Importo non valido.")
        self.store.execute("INSERT INTO nexus_ledger(ts, kind, venture, eur, note) VALUES(?,?,?,?,?)",
                           (now_iso(), "incasso", vid, float(eur), note))
        self.store.event("nexus", f"Nexus: incasso di {float(eur):.2f} € da '{vid}'. {note}".strip(), "INFO", "nexus")
        return self.totals()

    def record_traction(self, vid: str, value: float) -> None:
        if not self.venture(vid):
            raise ValueError(f"Ipotesi '{vid}' sconosciuta.")
        self.store.execute("UPDATE nexus_ventures SET traction=? WHERE id=?", (float(value), vid))

    # ── ciclo decisionale ────────────────────────────────────
    def _sync_ventures(self, cands: list[dict], today: date) -> None:
        by_id = {c["id"]: c for c in cands}
        review = timedelta(days=self.cfg["rules"]["review_days"])
        for c in cands:
            v = self.venture(c["id"])
            if c["verdict"] == "ipotesi attiva" and not v:
                self.store.execute("INSERT INTO nexus_ventures(id, status, opened, review_by, reason) VALUES(?,?,?,?,?)",
                                   (c["id"], OPEN, today.isoformat(), (today + review).isoformat(), c.get("note")))
            elif c["verdict"] == "bocciata" and not v:
                self.store.execute("INSERT INTO nexus_ventures(id, status, opened, closed, reason) VALUES(?,?,?,?,?)",
                                   (c["id"], REJECTED, today.isoformat(), today.isoformat(), c.get("note")))
        for v in self.store.query("SELECT * FROM nexus_ventures WHERE status=?", (OPEN,)):
            c = by_id.get(v["id"], {})
            target = float(c.get("traction_target") or 0)
            if target and (v["traction"] or 0) >= target:
                self.store.execute("UPDATE nexus_ventures SET status=?, reason=? WHERE id=?",
                                   (APPROVED, f"obiettivo di trazione raggiunto ({v['traction']:.0f} ≥ {target:.0f})", v["id"]))
                for d in cands:                                  # apre il cancello dei candidati che dipendevano da lei
                    if d.get("gate") and v["id"] in d["gate"] and not self.venture(d["id"]):
                        self.store.execute("INSERT INTO nexus_ventures(id, status, opened, review_by, reason) "
                                           "VALUES(?,?,?,?,?)", (d["id"], APPROVED, today.isoformat(),
                                                                 (today + review).isoformat(), f"cancello aperto da {v['id']}"))
            elif v["review_by"] and today.isoformat() > v["review_by"]:
                self.store.execute("UPDATE nexus_ventures SET status=?, closed=?, reason=? WHERE id=?",
                                   (CLOSED, today.isoformat(), f"revisione del {v['review_by']}: trazione "
                                    f"{(v['traction'] or 0):.0f} su {target:.0f} richiesta. Chiusa senza rimpianti", v["id"]))

    def decide(self, today: date | None = None) -> dict:
        today = today or date.today()
        self._ensure_deposit()
        cands = evaluate(self.cfg, self.costs)
        self._sync_ventures(cands, today)
        t, budget = self.totals(), float(self.cfg["budget_eur"])
        ventures = {v["id"]: v for v in self.store.query("SELECT * FROM nexus_ventures")}
        active = [v for v in ventures.values() if v["status"] in (OPEN, APPROVED)]
        posture = posture_of(cands, t["balance"], budget)
        started = date.fromisoformat(self.cfg["started"])
        days = (today - started).days
        fb = self.cfg["rules"]["fallback_after_days"]
        if active:
            v = active[0]
            c = next(x for x in cands if x["id"] == v["id"])
            action = (f"Nessuna spesa: tengo {t['balance']:.2f} € liquidi. Unica ipotesi aperta '{c['name']}' "
                      f"(costo 0 €), revisione il {v['review_by']}.")
            milestone = (f"{c.get('traction_metric', 'trazione')}: {(v['traction'] or 0):.0f} / "
                         f"{c.get('traction_target', 0)} entro il {v['review_by']}")
        elif days >= fb:
            action = (f"Dopo {days} giorni nessuna ipotesi ha superato il cancello: propongo la destinazione di riserva "
                      f"'{self.cfg['rules']['fallback']}'. Decide l'utente.")
            milestone = "decisione dell'utente sulla destinazione di riserva"
        else:
            action = f"Nessuna ipotesi conveniente: tengo {t['balance']:.2f} € liquidi."
            milestone = f"nuova ipotesi a costo zero entro il {(started + timedelta(days=fb)).isoformat()}"
        rejected = [c["name"] for c in cands if c["verdict"] == "bocciata"]
        rationale = (f"Bocciate perché il ritorno atteso non copre il costo o le regole: {'; '.join(rejected)}."
                     if rejected else "Nessun candidato bocciato.")
        data = {"balance": t["balance"], "candidates": [{k: c[k] for k in ("id", "verdict", "cost_eur", "expected_net_eur")}
                                                       for c in cands], "rationale": rationale}
        self.store.execute("INSERT INTO nexus_decisions(ts, posture, action, milestone, data) VALUES(?,?,?,?,?)",
                           (now_iso(), posture, action, milestone, json.dumps(data, ensure_ascii=False)))
        self.store.set("nexus_last_decision", time.time())
        self.store.event("nexus", f"Nexus · {posture}: {action}", "INFO", "nexus")
        return self.summary()

    def run(self) -> None:
        if not self.cfg.get("enabled", True):
            return
        last = self.store.get("nexus_last_decision") or 0
        if time.time() - last < self.cfg["cycle_hours"] * 3600:
            return
        self.decide()

    # ── fotografia ───────────────────────────────────────────
    def summary(self) -> dict:
        self._ensure_deposit()
        t = self.totals()
        cands = evaluate(self.cfg, self.costs)
        ventures = {v["id"]: v for v in self.store.query("SELECT * FROM nexus_ventures")}
        for c in cands:
            c["venture"] = ventures.get(c["id"])
        last = self.store.query("SELECT * FROM nexus_decisions ORDER BY id DESC LIMIT 1")
        last = last[0] if last else None
        if last:
            last["data"] = json.loads(last["data"]) if last["data"] else None
        budget = float(self.cfg["budget_eur"])
        return {"budget": budget, "currency": self.cfg["currency"], "started": self.cfg["started"],
                "rules": self.cfg["rules"], **t,
                "spent_share": t["spent"] / budget if budget else 0.0,
                "runway_eur": t["balance"] - self.cfg["rules"]["min_cash_reserve_eur"],
                "posture": last["posture"] if last else posture_of(cands, t["balance"], budget),
                "last": last, "candidates": cands,
                "ledger": self.store.query("SELECT * FROM nexus_ledger ORDER BY id DESC LIMIT 20"),
                "decisions": self.store.query("SELECT id, ts, posture, action, milestone FROM nexus_decisions "
                                              "ORDER BY id DESC LIMIT 10")}


def render_report(s: dict) -> str:
    """Il rapporto nel formato richiesto a Nexus: 4 sezioni, numeri espliciti."""
    last = s.get("last") or {}
    lines = [f"NEXUS · rapporto del {datetime.now().strftime('%d/%m/%Y %H:%M')}", "",
             "1. Postura strategica attuale", f"   {s['posture']}", "",
             "2. Capitale e risorse",
             f"   Saldo {s['balance']:.2f} € su {s['budget']:.2f} € · spesi {s['spent']:.2f} € "
             f"({s['spent_share'] * 100:.0f}%) · incassati {s['income']:.2f} €",
             f"   Spendibile oltre la riserva di {s['rules']['min_cash_reserve_eur']:.2f} €: {s['runway_eur']:.2f} €", "",
             "3. Azione e motivazione", f"   {last.get('action', 'nessuna decisione registrata')}"]
    if last.get("data"):
        lines.append(f"   {last['data'].get('rationale', '')}")
    lines += ["", "   Candidati:"]
    for c in s["candidates"]:
        lines.append(f"   - {c['name']}: {c['verdict']} · costo {c['cost_eur']:.2f} € · valore atteso netto "
                     f"{c['expected_net_eur']:+.2f} €")
    lines += ["", "4. Prossimo traguardo", f"   {last.get('milestone', '—')}"]
    return "\n".join(lines)
