"""Il pronostico di Leo: una sua opinione a parte, messa accanto al mercato e giudicata a fine partita.

NON serve a puntare: serve a capire quanto vale la conoscenza delle squadre rispetto al prezzo. Per ogni partita che il
bot vede (con prezzi veri) Leo dà le sue probabilità, il bot salva anche quelle del mercato (Pinnacle se c'è, altrimenti
il prezzo medio di Betfair) e, a partita finita, calcola l'errore (log-loss) di tutti e due. Più basso = meglio.
  • calcio: rating Elo di ogni squadra dallo storico di football-data (51.000 partite) + curva Elo → 1X2 appresa dai dati;
  • altri sport (tennis, basket, hockey, football americano, baseball, pallavolo…): Elo a due esiti che impara SOLO dai
    risultati che il bot vede; finché una squadra ha meno di 5 partite osservate Leo dice "ne so troppo poco".
Tutti i rating si aggiornano da soli quando una partita finisce. Niente di tutto questo cambia le puntate.
"""
from __future__ import annotations

import difflib
import json
import math
import re
from datetime import datetime, timezone

K = 20.0
HOME_ADV = {"soccer": 60.0, "tennis": 0.0, "basketball": 80.0, "americanfootball": 55.0, "icehockey": 40.0,
            "baseball": 25.0, "volleyball": 50.0, "handball": 60.0, "rugbyunion": 60.0, "rugbyleague": 60.0}
MIN_GAMES = 5
KEY = "leo_elo"
DONE = "leo_elo_done"
_ALIAS = {"man": "manchester", "utd": "united", "ath": "atletico", "wolves": "wolverhampton", "spurs": "tottenham",
          "fc": "", "ac": "", "as": "", "cf": "", "sc": "", "rc": "", "ud": "", "cd": "", "sd": ""}


def norm(name: str) -> str:
    n = (name or "").lower().replace("-", " ").replace(".", " ")
    n = " ".join(_ALIAS.get(t, t) for t in n.split())
    return re.sub(r"\s+", " ", n).strip()


def _sport(m: dict) -> str:
    return (m.get("sport") or "").split("_")[0] or "n.d."


class Ratings:
    """{sport: {nome normalizzato: [elo, partite]}} e la curva del calcio {W: 2x3 pesi}."""

    def __init__(self, data: dict | None = None):
        d = data or {}
        self.t: dict[str, dict[str, list]] = d.get("t", {})
        self.W = d.get("W")

    def dump(self) -> dict:
        return {"t": self.t, "W": self.W}

    def find(self, sport: str, name: str, fuzzy: bool = True) -> str | None:
        table = self.t.get(sport, {})
        n = norm(name)
        if n in table or not fuzzy:
            return n if n in table else None
        best, score = None, 0.0
        toks = set(n.split())
        for k in table:
            kt = set(k.split())
            s = max(difflib.SequenceMatcher(None, n, k).ratio(), len(toks & kt) / max(1, min(len(toks), len(kt))) * 0.95
                    if toks & kt and (toks <= kt or kt <= toks) else 0.0)
            if s > score:
                best, score = k, s
        return best if score >= 0.86 else None

    def get(self, sport: str, name: str, create: bool = False, fuzzy: bool = True):
        k = self.find(sport, name, fuzzy)
        if k is None and create:
            k = norm(name)
            self.t.setdefault(sport, {})[k] = [1500.0, 0]
        return k

    def elo(self, sport: str, k: str) -> tuple[float, int]:
        e = self.t[sport][k]
        return e[0], e[1]

    # curva calcio: probabilità 1X2 dalla differenza Elo (appresa sullo storico)
    def soccer_probs(self, diff: float) -> list[float]:
        W = self.W or [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
        z = [W[0][i] + W[1][i] * diff / 100.0 for i in range(3)]
        mx = max(z)
        e = [math.exp(x - mx) for x in z]
        s = sum(e)
        return [x / s for x in e]


def predict(r: Ratings, sport: str, home: str, away: str) -> dict | None:
    kh, ka = r.get(sport, home), r.get(sport, away)
    if kh is None or ka is None:
        return None
    (eh, nh), (ea, na) = r.elo(sport, kh), r.elo(sport, ka)
    if min(nh, na) < MIN_GAMES:
        return None
    diff = eh + HOME_ADV.get(sport, 50.0) - ea
    if sport == "soccer":
        p = r.soccer_probs(diff)
        return {"home": p[0], "draw": p[1], "away": p[2], "n": min(nh, na)}
    ph = 1.0 / (1.0 + 10 ** (-diff / 400.0))
    return {"home": ph, "away": 1.0 - ph, "n": min(nh, na)}


def update_result(r: Ratings, sport: str, home: str, away: str, result: str) -> None:
    sc = {"home": 1.0, "draw": 0.5, "away": 0.0}.get(result)
    if sc is None:
        return                                           # pari dopo i supplementari, annullata…: non si impara
    kh, ka = r.get(sport, home, create=True), r.get(sport, away, create=True)
    (eh, nh), (ea, na) = r.elo(sport, kh), r.elo(sport, ka)
    exp = 1.0 / (1.0 + 10 ** (-(eh + HOME_ADV.get(sport, 50.0) - ea) / 400.0))
    d = K * (sc - exp)
    r.t[sport][kh] = [eh + d, nh + 1]
    r.t[sport][ka] = [ea - d, na + 1]


def bootstrap_soccer(r: Ratings, history_dir) -> int:
    """Rating Elo dallo storico football-data (cartella runtime/history) e curva 1X2 appresa sulle stesse partite."""
    import glob

    import numpy as np
    import pandas as pd
    rows = []
    for f in sorted(glob.glob(str(history_dir / "*_*.csv"))):
        try:
            df = pd.read_csv(f, encoding="utf-8-sig", on_bad_lines="skip")
        except Exception:
            continue
        div = f.replace("\\", "/").split("/")[-1][:-4].split("_", 1)[-1]
        for c in ("Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"):
            if c not in df.columns:
                break
        else:
            sub = df.dropna(subset=["HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"])
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                dates = pd.to_datetime(sub["Date"], dayfirst=True, errors="coerce")
            for dt, h, a, hg, ag, res in zip(dates, sub["HomeTeam"], sub["AwayTeam"], sub["FTHG"], sub["FTAG"], sub["FTR"]):
                if pd.isna(dt) or res not in ("H", "D", "A"):
                    continue
                rows.append((dt, div, h, a, int(hg), int(ag), res))
    rows.sort(key=lambda t: t[0])
    X, y = [], []
    for dt, div, h, a, hg, ag, res in rows:
        kh, ka = r.get("soccer", h, create=True, fuzzy=False), r.get("soccer", a, create=True, fuzzy=False)
        (eh, nh), (ea, na) = r.elo("soccer", kh), r.elo("soccer", ka)
        diff = eh + HOME_ADV["soccer"] - ea
        if min(nh, na) >= MIN_GAMES:
            X.append([1.0, diff / 100.0])
            y.append("HDA".index(res))
        gd = abs(hg - ag)
        mult = 1.0 if gd <= 1 else 1.5 if gd == 2 else (11 + gd) / 8
        exp = 1.0 / (1.0 + 10 ** (-diff / 400.0))
        d = K * mult * ({"H": 1.0, "D": 0.5, "A": 0.0}[res] - exp)
        r.t["soccer"][kh] = [eh + d, nh + 1]
        r.t["soccer"][ka] = [ea - d, na + 1]
    if X:
        X, Y = np.array(X), np.eye(3)[np.array(y)]
        W = np.zeros((2, 3))
        for _ in range(500):
            Z = X @ W
            Z -= Z.max(1, keepdims=True)
            P = np.exp(Z)
            P /= P.sum(1, keepdims=True)
            W -= 0.5 * (X.T @ (P - Y) / len(y))
        r.W = W.tolist()
    return len(rows)


# ── a ogni ciclo ────────────────────────────────────────────────────────────
def _ensure(store):
    store.execute("CREATE TABLE IF NOT EXISTS leo_pronostici (match_id TEXT PRIMARY KEY, sport TEXT, league TEXT, home TEXT, "
                  "away TEXT, kickoff TEXT, ts TEXT, p_leo TEXT, p_mkt TEXT, mkt_src TEXT, result TEXT, ll_leo REAL, "
                  "ll_mkt REAL, scored INTEGER DEFAULT 0)")


def load(store, history_dir=None) -> Ratings:
    data = store.get(KEY)
    if data:
        return Ratings(data)
    r = Ratings()
    if history_dir is not None:
        try:
            bootstrap_soccer(r, history_dir)
        except Exception:
            pass
    return r


def _logloss(p: dict, result: str) -> float | None:
    q = p.get(result)
    return None if not q else -math.log(max(q, 1e-9))


def update(store, snapshot: dict, history_dir=None) -> dict:
    """Chiamata a ogni ciclo: aggiorna i rating con le partite finite, registra i pronostici delle prossime e giudica
    quelli già finiti. Restituisce un piccolo riepilogo."""
    from .agents.coach import exchange_fair, fair_now
    _ensure(store)
    r = load(store, history_dir)
    # 1) impara dai risultati (una volta per partita)
    done = set(store.get(DONE) or [])
    first = store.get(KEY) is None
    new = 0
    for m in store.query("SELECT match_id, sport, home, away, result FROM matches WHERE result IS NOT NULL"):
        if m["match_id"] in done:
            continue
        sport = (m["sport"] or "").split("_")[0]
        res = m["result"]
        if res in ("home", "draw", "away") and sport:
            update_result(r, sport, m["home"], m["away"], res)
            new += 1
        done.add(m["match_id"])
    # 2) pronostici delle partite che il bot vede
    now = snapshot.get("sim_time") or snapshot.get("ts") or 0
    nowiso = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds") if now else ""
    made = 0
    for mid, m in (snapshot.get("matches") or {}).items():
        if m.get("status") != "SCHEDULED" or not m.get("exchange"):
            continue
        sport = _sport(m)
        p = predict(r, sport, m.get("home", ""), m.get("away", ""))
        if not p:
            continue
        mk, src = fair_now(m), "Pinnacle/consenso"
        if not mk:
            mk, src = exchange_fair(m["exchange"]), "Betfair"
        if not mk:
            continue
        pl = {k: round(v, 4) for k, v in p.items() if k in ("home", "draw", "away")}
        store.execute("INSERT INTO leo_pronostici(match_id, sport, league, home, away, kickoff, ts, p_leo, p_mkt, mkt_src) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(match_id) DO UPDATE SET ts=excluded.ts, p_leo=excluded.p_leo, "
                      "p_mkt=excluded.p_mkt, mkt_src=excluded.mkt_src WHERE scored=0",
                      (mid, sport, m.get("league"), m.get("home"), m.get("away"), m.get("kickoff"), nowiso,
                       json.dumps(pl), json.dumps({k: round(v, 4) for k, v in mk.items()}), src))
        made += 1
    # 3) giudizio dei pronostici delle partite finite
    for row in store.query("SELECT p.match_id, p.p_leo, p.p_mkt, m.result FROM leo_pronostici p JOIN matches m ON m.match_id=p.match_id "
                           "WHERE p.scored=0 AND m.result IN ('home','draw','away')"):
        pl, pm = json.loads(row["p_leo"]), json.loads(row["p_mkt"])
        a, b = _logloss(pl, row["result"]), _logloss(pm, row["result"])
        if a is not None and b is not None:
            store.execute("UPDATE leo_pronostici SET result=?, ll_leo=?, ll_mkt=?, scored=1 WHERE match_id=?",
                          (row["result"], a, b, row["match_id"]))
    if new or first:                                     # i rating si riscrivono solo se sono cambiati
        store.set(KEY, r.dump())
        store.set(DONE, sorted(done)[-5000:])
    return {"nuovi_risultati": new, "pronostici": made}


def summary(store) -> dict:
    try:
        _ensure(store)
        rows = store.query("SELECT sport, COUNT(*) n, AVG(ll_leo) leo, AVG(ll_mkt) mkt FROM leo_pronostici WHERE scored=1 GROUP BY sport")
        up = store.query("SELECT sport, league, home, away, kickoff, p_leo, p_mkt, mkt_src FROM leo_pronostici WHERE scored=0 "
                         "ORDER BY kickoff LIMIT 80")
    except Exception:
        return {"per_sport": [], "prossime": []}
    for u in up:
        u["p_leo"], u["p_mkt"] = json.loads(u["p_leo"]), json.loads(u["p_mkt"])
    return {"per_sport": [{"sport": r["sport"], "n": r["n"], "leo": r["leo"], "mercato": r["mkt"]} for r in rows], "prossime": up}


def text(s: dict) -> str:
    if not s["per_sport"]:
        return "Pronostici di Leo: ancora nessuna partita finita da giudicare."
    L = ["Pronostici di Leo contro il mercato (errore: più basso è meglio):"]
    for r in sorted(s["per_sport"], key=lambda r: -r["n"]):
        diff = r["leo"] - r["mercato"]
        verdict = "meglio del mercato" if diff < -0.005 else "peggio del mercato" if diff > 0.005 else "come il mercato"
        L.append(f"• {r['sport']}: {r['n']} partite · Leo {r['leo']:.3f} · mercato {r['mercato']:.3f} → {verdict}"
                 + (" (campione ancora piccolo)" if r["n"] < 100 else ""))
    return "\n".join(L)
