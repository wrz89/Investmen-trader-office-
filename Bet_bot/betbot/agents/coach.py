"""AGENTE 10 — ALLENATORE (Leo). Studia ogni puntata chiusa, capisce perché è andata così e impara dagli errori.

1. ALL'INGRESSO (note_entry): per ogni puntata vera, paper o in ombra salva la "fotografia" della decisione:
   probabilità, valore atteso, quota, fonte e freschezza del riferimento, liquidità, spread, anticipo, sport,
   campionato, lato (back/lay).
2. FINO ALL'INIZIO (observe): segue il prezzo della selezione e la probabilità giusta del riferimento; l'ultimo
   valore prima dell'inizio è la CHIUSURA, cioè il giudizio del mercato più informato.
3. ALLA CHIUSURA (review): autopsia della puntata. Si separa la FORTUNA (risultato contro probabilità) dalla
   BRAVURA (il mercato alla chiusura ci ha dato ragione o torto: CLV). Una perdita col mercato dalla nostra parte
   è varianza, non un errore; una vincita col mercato contro è fortuna, e va trattata come un errore.
4. LEZIONI (learn): per ogni strategia e ogni caratteristica (campionato, fascia di quota, anticipo, fonte del
   riferimento, vantaggio dichiarato, sport, lato) misura il CLV medio con il suo errore. Un segmento con almeno
   `min_n` puntate e CLV significativamente negativo (limite superiore al 90% sotto zero) diventa una REGOLA.
   Controlla anche la calibrazione: se la strategia vince meno di quanto stima, le probabilità vanno corrette.
5. LE REGOLE POSSONO SOLO FRENARE: il Risk Manager le usa come veto in più (mai per allargare i limiti) e le
   proposte bloccate si seguono in ombra, così si misura se la regola ha evitato perdite e la si può ritirare
   quando i dati nuovi la smentiscono. Il file dei limiti sigillato non viene mai toccato.
"""
from __future__ import annotations

import json
import math
from datetime import datetime

from ..odds import consensus, remove_margin
from ..store import now_iso
from .base import Agent

TABLES = """
CREATE TABLE IF NOT EXISTS coach_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, src TEXT, row_id INTEGER, strategy_id TEXT, match_id TEXT,
    selection TEXT, side TEXT, odds REAL, fair_prob REAL, edge REAL, features TEXT, track TEXT,
    blocked_by TEXT, reviewed INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS coach_entries_open ON coach_entries(reviewed, match_id);
CREATE TABLE IF NOT EXISTS coach_lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, entry_id INTEGER UNIQUE, strategy_id TEXT, label TEXT, src TEXT,
    outcome TEXT, pnl REAL, stake REAL, p_entry REAL, p_close REAL, clv REAL, price_clv REAL, luck REAL,
    expected REAL, cause TEXT, explanation TEXT, features TEXT, blocked_by TEXT
);
CREATE TABLE IF NOT EXISTS coach_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, updated TEXT, strategy_id TEXT, kind TEXT, feature TEXT,
    value TEXT, n INTEGER, clv REAL, clv_hi REAL, adjust REAL, active INTEGER DEFAULT 1, evidence TEXT,
    UNIQUE(strategy_id, kind, feature, value)
);
"""

CAUSES = {
    "merito": "Vinta con merito",
    "fortuna": "Vinta per fortuna",
    "varianza": "Persa per varianza",
    "smentita": "Smentita dal mercato",
    "riferimento": "Riferimento vecchio",
    "notizia": "Movimento forte (notizia?)",
    "esecuzione": "Esecuzione",
    "non_valutabile": "Senza chiusura",
}
FEATURES = ("campionato", "fascia_quota", "anticipo", "fonte", "vantaggio", "sport", "lato", "liquidita")
Z90 = 1.645


def _band(x: float | None, edges: list[float], fmt: str = "{:.2f}") -> str:
    if x is None:
        return "n.d."
    lo = None
    for e in edges:
        if x < e:
            return f"< {fmt.format(e)}" if lo is None else f"{fmt.format(lo)}-{fmt.format(e)}"
        lo = e
    return f"≥ {fmt.format(edges[-1])}"


def fair_now(m: dict) -> dict | None:
    """Probabilità giuste del mercato ora: Pinnacle senza margine se c'è, altrimenti il consenso dei book."""
    books = m.get("books") or {}
    pin = books.get("Pinnacle")
    if pin and len(pin) >= 2:
        return remove_margin(pin)
    if len(books) >= 2:
        return {s: v["fair_prob"] for s, v in consensus(books).items() if not s.startswith("_")}
    return None


def our_prob(side: str, p: float | None) -> float | None:
    """Probabilità che la NOSTRA puntata vinca: il back vince se l'esito succede, il lay se non succede."""
    return None if p is None else (1 - p if side == "LAY" else p)


def classify(won: bool | None, clv: float | None, f: dict, move: float | None, thr: float = 0.01) -> str:
    if won is None:
        return "esecuzione"
    if clv is None:
        return "non_valutabile"
    if abs(clv) >= 0.05 or (move is not None and abs(move) >= 0.06):
        if not won or clv < 0:
            return "notizia"
    if clv < -thr and (f.get("ref_age_min") or 0) > 60:
        return "riferimento"
    if won:
        return "merito" if clv >= -thr else "fortuna"
    return "varianza" if clv >= -thr else "smentita"


def explain(cause: str, won: bool | None, pe: float | None, pc: float | None, f: dict, pnl: float) -> str:
    pct = lambda x: "n.d." if x is None else f"{x:.0%}"
    base = (f"Risultato {pnl:+.2f} €." if cause == "esecuzione" else
            f"Probabilità di vincere all'ingresso {pct(pe)}, alla chiusura {pct(pc)}; risultato {pnl:+.2f} €.")
    why = {
        "merito": "Il mercato alla chiusura ci ha dato ragione: la decisione era buona e il risultato l'ha confermata.",
        "fortuna": "Abbiamo vinto, ma alla chiusura il mercato ci dava torto: il prezzo preso non aveva valore. "
                   "È un errore anche se ha pagato.",
        "varianza": "Il mercato alla chiusura ci dava ragione: la decisione era corretta e la perdita rientra nella "
                    f"normale varianza (succede circa {pct(None if pe is None else 1 - pe)} delle volte). Nessuna correzione.",
        "smentita": "Alla chiusura il mercato stimava meno di noi: il vantaggio che avevamo visto non c'era. "
                    "È l'errore da studiare: si guarda se si ripete nello stesso segmento.",
        "riferimento": f"Il riferimento aveva {f.get('ref_age_min') or 0:.0f} minuti quando siamo entrati e il mercato "
                       "si era già mosso: il 'valore' era un prezzo vecchio.",
        "notizia": "Tra l'ingresso e l'inizio la probabilità si è mossa di molto: quasi sempre una notizia "
                   "(formazioni, infortunio, meteo) che il mercato ha saputo prima di noi.",
        "esecuzione": "Trade chiuso dalla gestione: conta come è stato eseguito, non chi ha vinto.",
        "non_valutabile": "Manca la quota di chiusura del riferimento: si può giudicare solo il risultato.",
    }[cause]
    return f"{CAUSES[cause]}. {why} {base}"


class CoachBook:
    """Le statistiche dell'allenatore lette dal database (usate anche dalla dashboard)."""

    def __init__(self, store, min_n: int = 30):
        self.store, self.min_n = store, min_n
        self.store.conn.executescript(TABLES)


    @staticmethod
    def segment_values(f: dict) -> dict[str, str]:
        return {"campionato": f.get("league") or "n.d.",
                "fascia_quota": _band(f.get("odds"), [1.2, 1.4, 1.7, 2.2, 3.0, 5.0, 8.0]),
                "anticipo": _band(f.get("minutes_before"), [30, 60, 120, 360, 1440], "{:.0f}") + " min",
                "fonte": f.get("source") or "n.d.",
                "vantaggio": _band(f.get("edge"), [0.02, 0.03, 0.05, 0.08], "{:.0%}"),
                "sport": f.get("sport") or "n.d.",
                "lato": f.get("side") or "BACK",
                "liquidita": _band(f.get("liquidity"), [5, 20, 100, 500], "{:.0f}") + " €",
                **{k: str(v) for k, v in (f.get("dyn") or {}).items()}}      # dinamiche della palestra (forma, assenze…)

    def segments(self) -> list[dict]:
        out: dict[tuple, list] = {}
        for L in self.store.query("SELECT strategy_id, clv, outcome, pnl, p_entry, features FROM coach_lessons "
                                  "WHERE clv IS NOT NULL"):
            for feat, val in self.segment_values(json.loads(L["features"] or "{}")).items():
                out.setdefault((L["strategy_id"], feat, val), []).append(L)
        res = []
        for (sid, feat, val), ls in out.items():
            c = [x["clv"] for x in ls]
            mean = sum(c) / len(c)
            sd = math.sqrt(sum((x - mean) ** 2 for x in c) / (len(c) - 1)) if len(c) > 1 else None
            se = sd / math.sqrt(len(c)) if sd is not None else None
            dec = [x for x in ls if x["outcome"] in ("WON", "LOST")]
            res.append({"strategy_id": sid, "feature": feat, "value": val, "n": len(ls), "clv": mean,
                        "clv_hi": None if se is None else mean + Z90 * se, "clv_lo": None if se is None else mean - Z90 * se,
                        "win_rate": sum(1 for x in dec if x["outcome"] == "WON") / len(dec) if dec else None,
                        "expected_win": sum(x["p_entry"] or 0 for x in dec) / len(dec) if dec else None,
                        "pnl": sum(x["pnl"] or 0 for x in ls)})
        return sorted(res, key=lambda r: (r["strategy_id"], r["feature"], -r["n"]))

    def calibration(self, strategy_id: str | None = None) -> list[dict]:
        q = "SELECT p_entry, outcome FROM coach_lessons WHERE outcome IN ('WON','LOST') AND p_entry IS NOT NULL"
        rows = self.store.query(q + (" AND strategy_id=?" if strategy_id else ""), (strategy_id,) if strategy_id else ())
        out = []
        for lo, hi in ((0, .5), (.5, .6), (.6, .7), (.7, .8), (.8, .9), (.9, 1.01)):
            b = [r for r in rows if lo <= r["p_entry"] < hi]
            if b:
                out.append({"band": f"{lo:.0%}-{min(hi, 1):.0%}", "n": len(b),
                            "expected": sum(r["p_entry"] for r in b) / len(b),
                            "actual": sum(1 for r in b if r["outcome"] == "WON") / len(b)})
        return out


    def summary(self) -> dict:
        L = self.store.query("SELECT cause, clv, pnl, outcome FROM coach_lessons")
        c = [x["clv"] for x in L if x["clv"] is not None]
        causes = {}
        for x in L:
            causes[x["cause"]] = causes.get(x["cause"], 0) + 1
        blocked = self.store.query("SELECT COUNT(*) n, SUM(pnl) pnl FROM coach_lessons WHERE blocked_by IS NOT NULL")[0]
        return {"lessons": len(L), "clv": sum(c) / len(c) if c else None,
                "rules": self.store.query("SELECT COUNT(*) n FROM coach_rules WHERE active=1")[0]["n"],
                "causes": causes, "blocked": blocked["n"], "blocked_pnl": blocked["pnl"]}

    def view(self) -> dict:
        """Tutto quello che la dashboard mostra nella 'scuola degli errori'."""
        s = self.summary()
        return {**s, "cause_labels": CAUSES, "calibration": self.calibration(),
                "segments": [x for x in self.segments() if x["n"] >= 5][:80],
                "rules": self.store.query("SELECT * FROM coach_rules ORDER BY active DESC, updated DESC LIMIT 30"),
                "lessons": self.store.query("SELECT id, ts, strategy_id, label, outcome, pnl, p_entry, p_close, clv, cause, "
                                            "explanation, blocked_by FROM coach_lessons ORDER BY id DESC LIMIT 40"),
                "min_n": self.min_n}


class Coach(Agent, CoachBook):
    key = "coach"
    name = "Leo"
    role = "Allenatore: autopsia di ogni puntata, lezioni dagli errori, regole che possono solo frenare"

    def __init__(self, office):
        super().__init__(office)
        self.store.conn.executescript(TABLES)
        c = (self.settings.get("coach") or {})
        self.min_n = int(c.get("min_n", 30))
        self.apply_rules = bool(c.get("apply_rules", True))
        self.max_rules = int(c.get("max_rules", 12))

    # ── 1. ingresso ─────────────────────────────────────────────────────────────
    def note_entry(self, p: dict, snapshot: dict | None, src: str, row_id: int | None, blocked_by: str | None = None) -> None:
        try:
            m = (snapshot or {}).get("matches", {}).get(p["match_id"]) or {}
            now = (snapshot or {}).get("sim_time") or (snapshot or {}).get("ts")
            side = p.get("side") or "BACK"
            sel = p["selection"][4:] if p["selection"].startswith("LAY:") else p["selection"]
            ex = (m.get("exchange") or {}).get(sel) or {}
            try:
                mins = (datetime.fromisoformat(m["kickoff"]).timestamp() - now) / 60 if m.get("kickoff") and now else None
            except (TypeError, ValueError):
                mins = None
            ref_age = (now - p["ref_ts"]) / 60 / ((snapshot or {}).get("time_scale") or 1.0) if p.get("ref_ts") and now else None
            books = m.get("books") or {}
            liq = ex.get("lay_size_best" if side == "LAY" else "back_size_best") or ex.get("lay_size" if side == "LAY" else "back_size")
            f = {"sport": (p.get("sport") or m.get("sport") or "").split("_")[0] or "n.d.", "league": p.get("league"),
                 "odds": p.get("odds"), "fair_prob": p.get("fair_prob"), "edge": p.get("edge"), "side": side,
                 "minutes_before": mins, "ref_age_min": ref_age, "n_books": p.get("n_books") or len(books),
                 "source": "Pinnacle" if "Pinnacle" in books else (f"consenso {len(books)} book" if books else "nessuna"),
                 "liquidity": liq, "live": bool(p.get("live")), "market": p.get("market")}
            self.store.execute("INSERT INTO coach_entries(ts, src, row_id, strategy_id, match_id, selection, side, odds, "
                               "fair_prob, edge, features, track, blocked_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (now_iso(), src, row_id, p["strategy_id"], p["match_id"], sel, side, p.get("odds"),
                                p.get("fair_prob"), p.get("edge"), json.dumps(f, default=str), json.dumps({}), blocked_by))
        except Exception as exc:                               # l'allenatore non deve mai fermare una puntata
            self.log(f"Non riesco a registrare l'ingresso ({exc}).", "WARN", "error")

    # ── 2. prezzo fino all'inizio ──────────────────────────────────────────────
    def observe(self, snapshot: dict) -> None:
        rows = self.store.query("SELECT id, match_id, selection, side, track FROM coach_entries WHERE reviewed=0")
        for e in rows:
            m = snapshot.get("matches", {}).get(e["match_id"])
            if not m or m.get("status") != "SCHEDULED":
                continue                                        # dall'inizio in poi la chiusura resta congelata
            t = json.loads(e["track"] or "{}")
            fair = fair_now(m)
            b = (m.get("exchange") or {}).get(e["selection"]) or {}
            if fair and e["selection"] in fair:
                p = fair[e["selection"]]
                t["p_close"] = p
                t["p_min"], t["p_max"] = min(t.get("p_min", p), p), max(t.get("p_max", p), p)
            if b.get("back") or b.get("lay"):
                t["back_close"], t["lay_close"] = b.get("back"), b.get("lay")
            t["ts"] = now_iso()
            self.store.execute("UPDATE coach_entries SET track=? WHERE id=?", (json.dumps(t), e["id"]))

    # ── 3. autopsia ────────────────────────────────────────────────────────────
    def _settled(self, e: dict) -> dict | None:
        if e["src"] == "shadow_bets":
            r = self.store.query("SELECT status, pnl, stake, label FROM shadow_bets WHERE id=?", (e["row_id"],))
        else:
            r = self.store.query("SELECT status, pnl, stake, label, market, closing_odds FROM bets WHERE id=?", (e["row_id"],))
        return r[0] if r and r[0]["status"] not in ("OPEN", None) else None

    def review(self) -> int:
        n = 0
        for e in self.store.query("SELECT * FROM coach_entries WHERE reviewed=0 AND row_id IS NOT NULL"):
            s = self._settled(e)
            if not s:
                continue
            f, t = json.loads(e["features"] or "{}"), json.loads(e["track"] or "{}")
            side = e["side"] or "BACK"
            trade = (s.get("market") or "") in ("exchange_trade", "exchange_win")
            won = None if trade or s["status"] in ("HEDGED", "CASHOUT") else (s["status"] == "WON")
            if s["status"] == "VOID":
                self.store.execute("UPDATE coach_entries SET reviewed=1 WHERE id=?", (e["id"],))
                continue
            pe = our_prob(side, e["fair_prob"])
            pc = our_prob(side, t.get("p_close"))
            clv = None if pe is None or pc is None or trade else pc - pe   # un trade si giudica sull'esecuzione
            price_clv = None
            if side == "LAY" and t.get("lay_close"):
                price_clv = t["lay_close"] / e["odds"] - 1
            elif side == "BACK" and t.get("back_close"):
                price_clv = e["odds"] / t["back_close"] - 1
            move = None
            if t.get("p_min") is not None and e["fair_prob"] is not None:
                move = max(abs(t["p_max"] - e["fair_prob"]), abs(t["p_min"] - e["fair_prob"]))
            luck = None if won is None or pe is None else (1.0 if won else 0.0) - pe
            stake = s.get("stake") or 1.0
            expected = (e["edge"] or 0) * (stake * ((e["odds"] or 1) - 1) if side == "LAY" else stake)
            cause = classify(won, clv, f, move)
            label = s.get("label") or f"{e['match_id']} · {e['selection']}"
            text = explain(cause, won, pe, pc, f, s["pnl"] or 0.0)
            self.store.execute(
                "INSERT OR IGNORE INTO coach_lessons(ts, entry_id, strategy_id, label, src, outcome, pnl, stake, p_entry, "
                "p_close, clv, price_clv, luck, expected, cause, explanation, features, blocked_by) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (now_iso(), e["id"], e["strategy_id"], label, e["src"], s["status"], s["pnl"], stake, pe, pc, clv,
                 price_clv, luck, expected, cause, text, json.dumps(f, default=str), e["blocked_by"]))
            self.store.execute("UPDATE coach_entries SET reviewed=1 WHERE id=?", (e["id"],))
            if cause in ("smentita", "riferimento", "notizia", "fortuna"):
                self.log(f"Autopsia · {label}: {text}", "INFO", "lesson",
                         payload={"cause": cause, "clv": clv, "strategy": e["strategy_id"], "agent": "coach"})
            n += 1
        return n

    def learn(self) -> list[dict]:
        """Crea o ritira le regole. Crea: CLV significativamente negativo su almeno min_n puntate. Ritira: il
        limite superiore torna sopra zero (i dati nuovi, anche delle proposte bloccate e seguite in ombra, la smentiscono)."""
        changes = []
        segs = {(s["strategy_id"], s["feature"], s["value"]): s for s in self.segments()}
        for key, s in segs.items():
            bad = s["n"] >= self.min_n and s["clv_hi"] is not None and s["clv_hi"] < 0
            row = self.store.query("SELECT * FROM coach_rules WHERE strategy_id=? AND kind='blocca' AND feature=? AND value=?",
                                   (s["strategy_id"], s["feature"], s["value"]))
            ev = json.dumps({k: s[k] for k in ("n", "clv", "clv_hi", "win_rate", "expected_win", "pnl")}, default=str)
            if bad and (not row or not row[0]["active"]):
                if self.store.query("SELECT COUNT(*) n FROM coach_rules WHERE active=1")[0]["n"] >= self.max_rules:
                    continue
                self.store.execute("INSERT INTO coach_rules(created, updated, strategy_id, kind, feature, value, n, clv, clv_hi, "
                                   "active, evidence) VALUES(?,?,?,?,?,?,?,?,?,1,?) ON CONFLICT(strategy_id, kind, feature, value) "
                                   "DO UPDATE SET active=1, updated=excluded.updated, n=excluded.n, clv=excluded.clv, "
                                   "clv_hi=excluded.clv_hi, evidence=excluded.evidence",
                                   (now_iso(), now_iso(), s["strategy_id"], "blocca", s["feature"], s["value"], s["n"],
                                    s["clv"], s["clv_hi"], ev))
                changes.append(("nuova", s))
                self.say(f"Nuova lezione per {s['strategy_id']}: con {s['feature'].replace('_', ' ')} = {s['value']} il mercato "
                         f"ci ha smentito in media di {abs(s['clv']):.1%} su {s['n']} puntate (anche nel caso migliore "
                         f"{s['clv_hi']:+.1%}). Da ora queste proposte vengono bloccate e seguite solo in ombra.",
                         "ok", "rule", level="WARN", payload={"agent": "coach", **s})
            elif row and row[0]["active"] and s["clv_hi"] is not None and s["clv_lo"] is not None and s["clv_lo"] > -0.002:
                self.store.execute("UPDATE coach_rules SET active=0, updated=?, evidence=? WHERE id=?", (now_iso(), ev, row[0]["id"]))
                changes.append(("ritirata", s))
                self.say(f"Regola ritirata per {s['strategy_id']} ({s['feature'].replace('_', ' ')} = {s['value']}): i dati "
                         f"nuovi non confermano più l'errore (CLV {s['clv']:+.1%} su {s['n']}).", "ok", "rule", level="WARN",
                         payload={"agent": "coach", **s})
        self._calibrate()
        return changes

    def _calibrate(self) -> None:
        """Se una strategia vince meno di quanto stima (con significatività), si abbassa la sua probabilità di quel
        divario prima di calcolare il valore: regola 'correggi'. Si ritira quando il divario sparisce."""
        for sid in {r["strategy_id"] for r in self.store.query("SELECT DISTINCT strategy_id FROM coach_lessons")}:
            rows = self.store.query("SELECT p_entry, outcome FROM coach_lessons WHERE strategy_id=? AND outcome IN ('WON','LOST') "
                                    "AND p_entry IS NOT NULL", (sid,))
            if len(rows) < self.min_n:
                continue
            exp = sum(r["p_entry"] for r in rows) / len(rows)
            act = sum(1 for r in rows if r["outcome"] == "WON") / len(rows)
            se = math.sqrt(max(exp * (1 - exp), 1e-6) / len(rows))
            gap = exp - act
            row = self.store.query("SELECT * FROM coach_rules WHERE strategy_id=? AND kind='correggi'", (sid,))
            if gap - Z90 * se > 0:
                adj = round(min(gap, 0.05), 4)
                self.store.execute("INSERT INTO coach_rules(created, updated, strategy_id, kind, feature, value, n, adjust, active, "
                                   "evidence) VALUES(?,?,?,'correggi','probabilita','tutte',?,?,1,?) ON CONFLICT(strategy_id, kind, "
                                   "feature, value) DO UPDATE SET active=1, updated=excluded.updated, n=excluded.n, "
                                   "adjust=excluded.adjust, evidence=excluded.evidence",
                                   (now_iso(), now_iso(), sid, len(rows), adj,
                                    json.dumps({"expected": exp, "actual": act, "se": se})))
                if not row or not row[0]["active"] or abs((row[0]["adjust"] or 0) - adj) > 0.005:
                    self.say(f"{sid} è troppo ottimista: stima di vincere il {exp:.1%}, vince il {act:.1%} su {len(rows)} "
                             f"puntate. Da ora tolgo {adj:.1%} alle sue probabilità prima di calcolare il valore.",
                             "ok", "rule", level="WARN", payload={"agent": "coach", "strategy": sid})
            elif row and row[0]["active"] and gap < Z90 * se * 0.5:
                self.store.execute("UPDATE coach_rules SET active=0, updated=? WHERE id=?", (now_iso(), row[0]["id"]))

    # ── 5. uso delle regole (dal Risk Manager) ─────────────────────────────────
    def check(self, p: dict, snapshot: dict) -> tuple[bool, str]:
        """(ok, etichetta). Solo freni: una regola non può mai approvare quello che il Risk Manager boccia."""
        if not self.apply_rules:
            return True, "Lezioni dell'allenatore (spente)"
        rules = self.store.query("SELECT * FROM coach_rules WHERE active=1 AND strategy_id=?", (p["strategy_id"],))
        if not rules:
            return True, "Nessuna lezione dell'allenatore contraria"
        m = snapshot.get("matches", {}).get(p["match_id"]) or {}
        now = snapshot.get("sim_time") or snapshot.get("ts")
        try:
            mins = (datetime.fromisoformat(m["kickoff"]).timestamp() - now) / 60 if m.get("kickoff") and now else None
        except (TypeError, ValueError):
            mins = None
        books = m.get("books") or {}
        sel = p["selection"][4:] if p["selection"].startswith("LAY:") else p["selection"]
        ex = (m.get("exchange") or {}).get(sel) or {}
        side = p.get("side") or "BACK"
        f = {"league": p.get("league"), "odds": p.get("odds"), "minutes_before": mins, "edge": p.get("edge"),
             "source": "Pinnacle" if "Pinnacle" in books else (f"consenso {len(books)} book" if books else "nessuna"),
             "sport": (p.get("sport") or "").split("_")[0] or "n.d.", "side": side,
             "liquidity": ex.get("lay_size_best" if side == "LAY" else "back_size_best")}
        vals = self.segment_values(f)
        for r in rules:
            if r["kind"] == "blocca" and vals.get(r["feature"]) == r["value"]:
                return False, (f"Lezione di Leo: {r['feature'].replace('_', ' ')} = {r['value']} (CLV {r['clv']:+.1%} "
                               f"su {r['n']} puntate)")
            if r["kind"] == "correggi" and p.get("fair_prob") is not None:
                pb = p["fair_prob"] + (r["adjust"] if side == "LAY" else -r["adjust"])
                q, c = p.get("odds") or 1.0, p.get("commission") or 0.045
                ev = (1 - pb) * (1 - c) - pb * (q - 1) if side == "LAY" else pb * (q - 1) * (1 - c) - (1 - pb)
                if ev < 0:
                    return False, (f"Lezione di Leo: con la probabilità corretta di {r['adjust']:.1%} "
                                   f"(la strategia è troppo ottimista) il valore sparisce")
        return True, "Nessuna lezione dell'allenatore contraria"

    # ── ciclo e riepilogo ──────────────────────────────────────────────────────
    def run(self, snapshot: dict) -> None:
        self.observe(snapshot)
        n = self.review()
        if n:
            self.learn()
        s = self.summary()
        self.status("ok" if s["lessons"] else "idle",
                    f"{s['lessons']} autopsie, {s['rules']} regole attive. "
                    + (f"CLV medio {s['clv']:+.1%}: " + ("il mercato ci dà ragione." if s["clv"] >= 0 else "il mercato ci dà torto.")
                       if s["clv"] is not None else "Aspetto le prime puntate chiuse."),
                    stats={"lezioni": s["lessons"], "regole": s["rules"], "clv": s["clv"]})

