"""La palestra di Leo: rivive le partite degli ultimi anni SENZA conoscere il risultato, scommette, poi impara.

Per ogni partita, in ordine di data, Leo vede solo quello che si sapeva in quel momento:
  • MOMENTO A (circa 2 giorni prima, quote raccolte il venerdì/martedì): quote di Pinnacle e Betfair del
    momento + tutta la storia fino al giorno prima (forma, Elo, tiri, xG, riposo, classifica, fase di stagione);
  • MOMENTO B (al fischio d'inizio, quote di chiusura): in più le FORMAZIONI ufficiali — chi gioca, chi manca,
    quanto valgono i giocatori in campo rispetto ai titolari abituali (5 grandi campionati, dati Understat).
Il modello parte dalle probabilità del mercato e impara solo CORREZIONI (se non trova niente, resta il mercato).
Si riaddestra ogni 4 settimane su tutto il passato: la progressione è quella vera, niente sbirciate al futuro.
Dopo il risultato: autopsia di ogni puntata (fortuna o bravura, con il CLV sulla chiusura di Pinnacle), lezioni per
segmento e un'analisi di dove il mercato sbaglia per ogni dinamica (forma, assenze, stanchezza, classifica…).
"""
from __future__ import annotations

import difflib
import json
import math
import re
from collections import defaultdict, deque
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .backtest import EXCHANGE_DIVS, HISTORY_DIR, download
from .config import REPORTS_DIR, RUNTIME_DIR
from .feeds.mock import tick_up
from .odds import exchange_prices_sane, remove_margin

PALESTRA_DB = RUNTIME_DIR / "palestra.db"
SUMMARY_FILE = RUNTIME_DIR / "palestra.json"
MODEL_FILE = RUNTIME_DIR / "leo_modello.json"
SEL = ("home", "draw", "away")
COMM = 0.045


# ── dati ─────────────────────────────────────────────────────────────────────
def _num(v):
    try:
        v = float(v)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def _triple(r, cols):
    t = [_num(r.get(c)) for c in cols]
    return t if all(x and x > 1.0 for x in t) else None


def seasons_back(years: int, today=None) -> list[str]:
    today = today or datetime.now()
    last = today.year if today.month >= 7 else today.year - 1
    return [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in range(last - years, last + 1)]


def load_matches(years: int = 5, divs: list[str] | None = None) -> list[dict]:
    rows = []
    for path in download(divs or EXCHANGE_DIVS, seasons_back(years), refresh_current=True):
        try:
            df = pd.read_csv(path, encoding="latin-1", on_bad_lines="skip")
        except Exception:
            continue
        df.columns = [str(c).strip().lstrip("﻿").lstrip("ï»¿") for c in df.columns]
        season, div = path.stem.split("_", 1)
        for _, r in df.iterrows():
            if pd.isna(r.get("FTHG")) or r.get("FTR") not in ("H", "D", "A") or pd.isna(r.get("HomeTeam")):
                continue
            d = pd.to_datetime(f"{r['Date']} {r.get('Time') if isinstance(r.get('Time'), str) else '15:00'}",
                               dayfirst=True, errors="coerce")
            if pd.isna(d):
                continue
            g = {k: _num(r.get(k)) for k in ("HS", "AS", "HST", "AST", "HC", "AC", "HF", "AF", "HY", "AY", "HR", "AR")}
            rows.append({"date": d.to_pydatetime(), "div": div, "season": season, "home": str(r["HomeTeam"]),
                         "away": str(r["AwayTeam"]), "hg": int(r["FTHG"]), "ag": int(r["FTAG"]),
                         "res": {"H": 0, "D": 1, "A": 2}[r["FTR"]], "stats": g,
                         "ps": _triple(r, ("PSH", "PSD", "PSA")), "psc": _triple(r, ("PSCH", "PSCD", "PSCA")),
                         "bfe": _triple(r, ("BFEH", "BFED", "BFEA")), "bfec": _triple(r, ("BFECH", "BFECD", "BFECA")),
                         "avg": _triple(r, ("AvgH", "AvgD", "AvgA"))})
    rows.sort(key=lambda m: (m["date"], m["div"], m["home"]))
    return rows


_ALIAS = {"man": "manchester", "utd": "united", "ath": "atletico", "nott'm": "nottingham", "wolves": "wolverhampton",
          "sheffield weds": "sheffield wednesday", "spurs": "tottenham", "m'gladbach": "monchengladbach",
          "ein": "eintracht", "fc": "", "ac": "", "as": "", "cf": "", "sc": "", "rc": "", "ud": "", "cd": "", "sd": ""}


def _norm(name: str) -> str:
    n = name.lower().replace("-", " ").replace(".", " ")
    n = " ".join(_ALIAS.get(t, t) for t in n.split())
    return re.sub(r"\s+", " ", n).strip()


def _sim(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    ta, tb = set(a.split()), set(b.split())
    tok = len(ta & tb) / max(1, min(len(ta), len(tb)))
    return max(difflib.SequenceMatcher(None, a, b).ratio(), tok, 0.9 if (a in b or b in a) else 0)


def attach_understat(matches: list[dict], log=print) -> int:
    """Collega ogni partita dei 5 grandi campionati alla sua scheda Understat (xG e formazioni)."""
    from . import understat as us
    rosters = us.load_rosters()
    by_key = defaultdict(list)
    for div in us.LEAGUES:
        for y in sorted({int("20" + m["season"][:2]) for m in matches if m["div"] == div}):
            try:
                s = us.league_season(div, y)
            except Exception as exc:
                log(f"Understat {div} {y} non disponibile ({exc})")
                continue
            for u in (s or {}).get("dates", []):
                if u.get("isResult"):
                    by_key[(div, u["datetime"][:10])].append(u)
    n = 0
    for m in matches:
        if m["div"] not in us.LEAGUES:
            continue
        best, score = None, 0.0
        for dd in (0, -1, 1):
            day = (m["date"] + timedelta(days=dd)).strftime("%Y-%m-%d")
            for u in by_key.get((m["div"], day), []):
                sc = _sim(m["home"], u["h"]["title"]) + _sim(m["away"], u["a"]["title"])
                if sc > score:
                    best, score = u, sc
        if best and score >= 1.2 and int(best["goals"]["h"]) == m["hg"] and int(best["goals"]["a"]) == m["ag"]:
            m["xg"] = (float(best["xG"]["h"]), float(best["xG"]["a"]))
            m["rosters"] = rosters.get(best["id"])
            n += 1
    return n


# ── dinamiche: stato di squadre e giocatori, aggiornato DOPO ogni partita ────
class World:
    def __init__(self):
        self.elo = defaultdict(lambda: 1500.0)
        self.hist = defaultdict(lambda: deque(maxlen=10))
        self.last = {}
        self.table = defaultdict(lambda: defaultdict(lambda: {"pts": 0, "gd": 0, "gp": 0}))
        self.players = defaultdict(dict)       # squadra → giocatore → {min, contrib, chain, apps}
        self.recent_xi = defaultdict(lambda: deque(maxlen=6))

    @staticmethod
    def _team_key(m, side):
        return (m["div"], m[side])

    def _roll(self, key, f, n=6, home=None):
        h = [x for x in self.hist[key] if home is None or x["home"] == home][-n:]
        if not h:
            return None
        w = [0.85 ** (len(h) - 1 - i) for i in range(len(h))]
        vals = [(f(x), wi) for x, wi in zip(h, w) if f(x) is not None]
        return sum(v * wi for v, wi in vals) / sum(wi for _, wi in vals) if vals else None

    def _lineup(self, team, roster):
        """Forza della formazione di oggi rispetto a quella abituale (dai giocatori delle partite precedenti)."""
        pl = self.players.get(team) or {}
        if not roster or not pl:
            return None
        starters = [p for p in roster if p.get("positionOrder") and int(p["positionOrder"]) <= 11
                    and str(p.get("roster_in") or "0") == "0"]
        if len(starters) < 9:
            return None
        val = lambda pid: pl.get(pid, {}).get("contrib", 0.0)
        usual = sorted(pl, key=lambda pid: -pl[pid]["min_recent"])[:11]
        today = [p["player_id"] for p in starters]
        s_today, s_usual = sum(val(p) for p in today), sum(val(p) for p in usual)
        key = sorted(pl, key=lambda pid: -pl[pid]["chain"] * min(1, pl[pid]["min_recent"] / 270))[:3]
        gk_usual = [pid for pid in usual if pl[pid].get("pos") == "GK"][:1]
        return {"absence": max(0.0, 1 - s_today / s_usual) if s_usual > 0 else 0.0,
                "key_missing": sum(1 for pid in key if pid not in today),
                "gk_changed": bool(gk_usual and gk_usual[0] not in today),
                "debut": sum(1 for pid in today if pid not in pl)}

    def features(self, m: dict, with_lineup: bool) -> dict:
        H, A = self._team_key(m, "home"), self._team_key(m, "away")
        tH, tA = self.table[(m["div"], m["season"])][m["home"]], self.table[(m["div"], m["season"])][m["away"]]
        f = {}
        f["elo_diff"] = (self.elo[H] + 60 - self.elo[A]) / 400
        pts = lambda k: self._roll(k, lambda x: x["pts"])
        f["form_h"], f["form_a"] = pts(H), pts(A)
        f["form_home_only"] = self._roll(H, lambda x: x["pts"], home=True)
        f["form_away_only"] = self._roll(A, lambda x: x["pts"], home=False)
        share = lambda k, a, b: self._roll(k, lambda x: x[a] / (x[a] + x[b]) if x.get(a) is not None and x.get(b) is not None and x[a] + x[b] > 0 else None)
        f["shots_share_h"], f["shots_share_a"] = share(H, "sf", "sa"), share(A, "sf", "sa")
        f["sot_share_h"], f["sot_share_a"] = share(H, "stf", "sta"), share(A, "stf", "sta")
        xgd = lambda k: self._roll(k, lambda x: None if x.get("xgf") is None else x["xgf"] - x["xga"])
        f["xgd_h"], f["xgd_a"] = xgd(H), xgd(A)
        luck = lambda k: self._roll(k, lambda x: None if x.get("xgf") is None else (x["gf"] - x["xgf"]) - (x["ga"] - x["xga"]))
        f["luck_h"], f["luck_a"] = luck(H), luck(A)
        rest = lambda k: min(14.0, (m["date"] - self.last[k]).total_seconds() / 86400) if k in self.last else None
        f["rest_h"], f["rest_a"] = rest(H), rest(A)
        busy = lambda k: sum(1 for x in self.hist[k] if (m["date"] - x["date"]).days <= 21)
        f["busy_h"], f["busy_a"] = busy(H), busy(A)
        n_teams = max(10, len(self.table[(m["div"], m["season"])]))
        stage = min(1.0, (tH["gp"] + tA["gp"]) / 2 / (2 * (n_teams - 1)))
        f["stage"] = stage
        order = sorted(self.table[(m["div"], m["season"])].items(), key=lambda kv: (-kv[1]["pts"], -kv[1]["gd"]))
        pos = {t: i + 1 for i, (t, _) in enumerate(order)}
        f["pos_diff"] = ((pos.get(m["away"], n_teams / 2) - pos.get(m["home"], n_teams / 2)) / n_teams) if stage > 0.1 else 0.0
        drop_line = order[-4][1]["pts"] if len(order) >= 4 else 0
        danger = lambda t: stage > 0.7 and self.table[(m["div"], m["season"])][t]["pts"] - drop_line <= 3
        f["relegation_h"], f["relegation_a"] = float(danger(m["home"])), float(danger(m["away"]))
        if with_lineup and m.get("rosters"):
            for side, key in (("h", H), ("a", A)):
                lu = self._lineup(key, m["rosters"].get(side))
                if lu:
                    f[f"absence_{side}"], f[f"key_missing_{side}"] = lu["absence"], lu["key_missing"]
                    f[f"gk_changed_{side}"], f[f"debut_{side}"] = float(lu["gk_changed"]), lu["debut"]
        return f

    def update(self, m: dict) -> None:
        H, A = self._team_key(m, "home"), self._team_key(m, "away")
        s = m["stats"]
        hp, ap = (3, 0) if m["res"] == 0 else (1, 1) if m["res"] == 1 else (0, 3)
        xg = m.get("xg") or (None, None)
        self.hist[H].append({"date": m["date"], "home": True, "pts": hp, "gf": m["hg"], "ga": m["ag"], "sf": s.get("HS"),
                             "sa": s.get("AS"), "stf": s.get("HST"), "sta": s.get("AST"), "xgf": xg[0], "xga": xg[1]})
        self.hist[A].append({"date": m["date"], "home": False, "pts": ap, "gf": m["ag"], "ga": m["hg"], "sf": s.get("AS"),
                             "sa": s.get("HS"), "stf": s.get("AST"), "sta": s.get("HST"), "xgf": xg[1], "xga": xg[0]})
        self.last[H] = self.last[A] = m["date"]
        # Elo con margine di gol
        exp = 1 / (1 + 10 ** (-(self.elo[H] + 60 - self.elo[A]) / 400))
        score = 1.0 if m["res"] == 0 else 0.5 if m["res"] == 1 else 0.0
        k = 20 * math.log(abs(m["hg"] - m["ag"]) + 1 + 1e-9 + 1)
        self.elo[H] += k * (score - exp)
        self.elo[A] -= k * (score - exp)
        tb = self.table[(m["div"], m["season"])]
        for t, p, gd in ((m["home"], hp, m["hg"] - m["ag"]), (m["away"], ap, m["ag"] - m["hg"])):
            tb[t]["pts"] += p
            tb[t]["gd"] += gd
            tb[t]["gp"] += 1
        if m.get("rosters"):
            for side, key in (("h", H), ("a", A)):
                pl = self.players[key]
                for p in pl.values():
                    p["min_recent"] *= 0.8                           # conta di più chi ha giocato di recente
                for p in m["rosters"].get(side) or []:
                    mins = float(p.get("time") or 0)
                    if mins <= 0:
                        continue
                    q = pl.setdefault(p["player_id"], {"min": 0.0, "contrib": 0.0, "chain": 0.0, "min_recent": 0.0,
                                                       "pos": p.get("position"), "name": p.get("player")})
                    per90 = (float(p.get("xG") or 0) + float(p.get("xA") or 0)) / mins * 90
                    chain = float(p.get("xGChain") or 0) / mins * 90
                    w = min(1.0, mins / 90) * 0.25
                    q["contrib"] = (1 - w) * q["contrib"] + w * per90
                    q["chain"] = (1 - w) * q["chain"] + w * chain
                    q["min"] += mins
                    q["min_recent"] += mins
                    q["pos"] = p.get("position") or q["pos"]


# ── modello: mercato + correzioni imparate (regressione softmax con L2) ─────
FEATS_A = ["elo_diff", "form_h", "form_a", "form_home_only", "form_away_only", "shots_share_h", "shots_share_a",
           "sot_share_h", "sot_share_a", "xgd_h", "xgd_a", "luck_h", "luck_a", "rest_h", "rest_a", "busy_h", "busy_a",
           "stage", "pos_diff", "relegation_h", "relegation_a"]
FEATS_B = FEATS_A + ["absence_h", "absence_a", "key_missing_h", "key_missing_a", "gk_changed_h", "gk_changed_a",
                     "debut_h", "debut_a"]
NAMES = {"elo_diff": "differenza Elo", "form_h": "forma casa", "form_a": "forma ospite", "form_home_only": "forma in casa",
         "form_away_only": "forma in trasferta", "shots_share_h": "dominio tiri casa", "shots_share_a": "dominio tiri ospite",
         "sot_share_h": "tiri in porta casa", "sot_share_a": "tiri in porta ospite", "xgd_h": "xG netti casa",
         "xgd_a": "xG netti ospite", "luck_h": "fortuna recente casa (gol − xG)", "luck_a": "fortuna recente ospite",
         "rest_h": "riposo casa", "rest_a": "riposo ospite", "busy_h": "partite in 21 giorni casa",
         "busy_a": "partite in 21 giorni ospite", "stage": "fase della stagione", "pos_diff": "distanza in classifica",
         "relegation_h": "casa in lotta salvezza", "relegation_a": "ospite in lotta salvezza",
         "absence_h": "assenze casa (valore)", "absence_a": "assenze ospite (valore)", "key_missing_h": "big assenti casa",
         "key_missing_a": "big assenti ospite", "gk_changed_h": "portiere cambiato casa", "gk_changed_a": "portiere cambiato ospite",
         "debut_h": "esordienti casa", "debut_a": "esordienti ospite"}


class Model:
    def __init__(self, feats, l2=0.05):
        self.feats, self.l2 = feats, l2
        self.W = np.zeros((len(feats) + 0, 3))
        self.mu = self.sd = None
        self.history = []

    def _X(self, F):
        X = np.array([[f.get(k) if f.get(k) is not None else np.nan for k in self.feats] for f in F], dtype=float)
        if self.mu is None:
            return X
        X = (X - self.mu) / self.sd
        return np.nan_to_num(X, nan=0.0)                         # dato mancante = valore medio

    def fit(self, F, logp, y, stamp):
        X = np.array([[f.get(k) if f.get(k) is not None else np.nan for k in self.feats] for f in F], dtype=float)
        self.mu, self.sd = np.nanmean(X, 0), np.nanstd(X, 0)
        self.mu, self.sd = np.nan_to_num(self.mu), np.where(np.nan_to_num(self.sd) < 1e-6, 1.0, np.nan_to_num(self.sd))
        X = np.nan_to_num((X - self.mu) / self.sd, nan=0.0)
        Y = np.eye(3)[y]
        W = self.W.copy()
        n = len(y)
        for it in range(400):                                    # discesa del gradiente con passo adattivo
            Z = logp + X @ W
            Z -= Z.max(1, keepdims=True)
            P = np.exp(Z)
            P /= P.sum(1, keepdims=True)
            G = X.T @ (P - Y) / n + self.l2 * W                # freno forte: senza prove i pesi restano a zero (= mercato)
            W -= 0.5 * G
        self.W = W
        self.history.append({"stamp": stamp, "n": n, "weights": {k: [round(float(x), 4) for x in W[i]] for i, k in enumerate(self.feats)}})

    def predict(self, f, logp):
        z = logp + self._X([f])[0] @ self.W
        z = np.exp(z - z.max())
        return z / z.sum()


def logloss(P, y):
    return float(-np.mean(np.log(np.clip(P[np.arange(len(y)), y], 1e-9, 1))))


# ── la palestra ──────────────────────────────────────────────────────────────
def _band(x, edges, fmt="{:.2f}"):
    if x is None:
        return "n.d."
    lo = None
    for e in edges:
        if x < e:
            return f"< {fmt.format(e)}" if lo is None else f"{fmt.format(lo)}-{fmt.format(e)}"
        lo = e
    return f"≥ {fmt.format(edges[-1])}"


def dynamics_bands(f: dict, sel: str) -> dict:
    """Le dinamiche viste dal lato della selezione (squadra su cui si punta o contro cui si banca)."""
    me, other = ("h", "a") if sel == "home" else ("a", "h") if sel == "away" else (None, None)
    out = {"fase_stagione": "inizio" if f.get("stage", 0) < 0.25 else "metà" if f.get("stage", 0) < 0.7 else "finale"}
    if me is None:
        out["partita"] = "equilibrata" if abs(f.get("elo_diff", 0)) < 0.25 else "squilibrata"
        return out
    g = lambda k: f.get(f"{k}_{me}")
    o = lambda k: f.get(f"{k}_{other}")
    if g("form") is not None and o("form") is not None:
        d = g("form") - o("form")
        out["forma"] = "migliore" if d > 0.5 else "peggiore" if d < -0.5 else "simile"
    if g("xgd") is not None:
        out["xg_trend"] = "domina" if g("xgd") > 0.5 else "subisce" if g("xgd") < -0.3 else "neutro"
    if g("luck") is not None:
        out["fortuna_recente"] = "gonfiata" if g("luck") > 0.4 else "sfortunata" if g("luck") < -0.4 else "normale"
    if g("rest") is not None and o("rest") is not None:
        d = g("rest") - o("rest")
        out["riposo"] = "più riposata" if d >= 2 else "meno riposata" if d <= -2 else "pari"
    if g("busy") is not None:
        out["calendario"] = "fitto" if g("busy") >= 5 else "normale"
    if f.get(f"relegation_{me}"):
        out["classifica"] = "lotta salvezza"
    if g("absence") is not None:
        out["assenze"] = ("big assenti" if (g("key_missing") or 0) >= 2 else
                          "assenze pesanti" if g("absence") > 0.15 else "formazione tipo")
        if o("absence") is not None:
            out["assenze_avversario"] = "avversario decimato" if o("absence") > 0.15 or (o("key_missing") or 0) >= 2 else "avversario completo"
        if g("gk_changed"):
            out["portiere"] = "portiere di riserva"
    return out


def run(years: int = 5, min_edge: float = 0.02, retrain_days: int = 28, log=print, with_understat: bool = True) -> dict:
    from .agents.coach import classify, explain, price_clv_of
    from .core import SportOffice
    log(f"Carico le partite degli ultimi {years + 1} campionati (stagione in corso compresa)…")
    matches = load_matches(years)
    if with_understat:
        n = attach_understat(matches, log)
        log(f"Collegate a Understat (xG e formazioni): {n} partite.")
    log(f"{len(matches)} partite, dal {matches[0]['date']:%d/%m/%Y} al {matches[-1]['date']:%d/%m/%Y}.")
    PALESTRA_DB.unlink(missing_ok=True)
    office = SportOffice(db_path=PALESTRA_DB, connect_feed=False, overrides={"coach": {"min_n": 40}})
    coach = office.coach
    world = World()
    models = {"A": Model(FEATS_A), "B": Model(FEATS_B)}
    train = {"A": [], "B": []}
    warm_until = matches[0]["date"] + timedelta(days=300)          # la prima stagione serve a conoscere squadre e giocatori
    next_fit = warm_until
    preds = {"A": [], "B": []}
    bets = []
    market_view = []
    n_fit = 0
    trust = {"A": deque(maxlen=1500), "B": deque(maxlen=1500)}     # (log-loss Leo, log-loss mercato) delle ultime previsioni
    dropped = 0
    for m in matches:
        # prezzi Betfair del file incoerenti con Pinnacle: record rotto, non si usa (né per puntare né per imparare)
        if m["bfe"] and not exchange_prices_sane(m["bfe"], m["ps"]):
            m["bfe"], dropped = None, dropped + 1
        if m["bfec"] and not exchange_prices_sane(m["bfec"], m["psc"]):
            m["bfec"] = None
        fA, fB = world.features(m, False), world.features(m, True)
        pre = remove_margin(dict(zip(SEL, m["ps"]))) if m["ps"] else None
        close = remove_margin(dict(zip(SEL, m["psc"]))) if m["psc"] else None
        if m["date"] >= next_fit and len(train["A"]) > 2000:
            for k in ("A", "B"):
                F, L, Y = zip(*train[k])
                models[k].fit(list(F), np.array(L), np.array(Y), m["date"].strftime("%Y-%m-%d"))
            n_fit += 1
            next_fit = m["date"] + timedelta(days=retrain_days)
        ready = n_fit > 0
        for moment, f, mk, prices in (("A", fA, pre, m["bfe"] or m["ps"]), ("B", fB, close, m["bfec"] or m["psc"])):
            if not mk:
                continue
            logp = np.log([mk[s] for s in SEL])
            if ready:
                p = models[moment].predict(f, logp)
                preds[moment].append((p, [mk[s] for s in SEL], m["res"], m["season"]))
                # FIDUCIA: Leo punta solo se nelle ultime previsioni (già verificate) è stato più preciso del mercato
                tq = trust[moment]
                trusted = len(tq) >= 500 and sum(a for a, _ in tq) < sum(b for _, b in tq)
                tq.append((-math.log(max(p[m["res"]], 1e-9)), -math.log(max(mk[SEL[m["res"]]], 1e-9))))
                exchange = bool(m["bfe"] if moment == "A" else m["bfec"])
                for i, sel in enumerate(SEL):
                    q = prices[i]
                    evb = p[i] * (q - 1) * (1 - COMM) - (1 - p[i])
                    cands = [("BACK", q, evb, p[i])]
                    if exchange:
                        lay = tick_up(q, 2)
                        evl = ((1 - p[i]) * (1 - COMM) - p[i] * (lay - 1)) / (lay - 1)
                        cands.append(("LAY", lay, evl, p[i]))
                    for side, price, ev, pm in cands:
                        if ev < min_edge or not (1.1 <= price <= 15):
                            continue
                        won = (m["res"] == i) if side == "BACK" else (m["res"] != i)
                        pnl = ((price - 1) * (1 - COMM) if won else -1.0) if side == "BACK" else ((1 - COMM) if won else -(price - 1))
                        risk = 1.0 if side == "BACK" else price - 1
                        pe = pm if side == "BACK" else 1 - pm
                        pc = None
                        clv = None
                        if moment == "A" and close:
                            pc = close[sel] if side == "BACK" else 1 - close[sel]
                            clv = price_clv_of(side, price, close[sel])      # quota presa contro chiusura giusta
                        bets.append({"date": m["date"], "season": m["season"], "div": m["div"], "moment": moment, "trusted": trusted,
                                     "match": f"{m['home']} - {m['away']}", "sel": sel, "side": side, "price": price,
                                     "p": pe, "p_market": (mk[sel] if side == "BACK" else 1 - mk[sel]), "ev": ev,
                                     "won": won, "pnl": pnl, "risk": risk, "clv": clv, "pc": pc, "exchange": exchange,
                                     "dyn": dynamics_bands(f, sel)})
            if moment == "B" and close:
                for i, sel in enumerate(SEL):
                    market_view.append({"sel": sel, "p": close[sel], "hit": m["res"] == i, "dyn": dynamics_bands(f, sel),
                                        "season": m["season"]})
            if m["date"] >= warm_until - timedelta(days=300):
                train[moment].append((f, logp, m["res"]))
        world.update(m)

    # ── risultati e lezioni ──
    B = pd.DataFrame(bets)
    out = {"matches": len(matches), "first": matches[0]["date"].strftime("%d/%m/%Y"),
           "last": matches[-1]["date"].strftime("%d/%m/%Y"), "retrains": n_fit, "bfe_scartati": dropped, "with_understat": sum(1 for m in matches if m.get("xg"))}
    for k in ("A", "B"):
        if preds[k]:
            P = np.array([x[0] for x in preds[k]])
            M = np.array([x[1] for x in preds[k]])
            y = np.array([x[2] for x in preds[k]])
            out[f"logloss_{k}"] = {"mercato": logloss(M, y), "leo": logloss(P, y), "n": len(y)}
    for (i, b) in enumerate(bets):
        f = {"league": b["div"], "odds": b["price"], "edge": b["ev"], "side": b["side"], "sport": "soccer",
             "source": "Pinnacle" if not b["exchange"] else "Betfair", "minutes_before": 2880 if b["moment"] == "A" else 5,
             "dyn": b["dyn"]}
        cause = classify(b["won"], b["clv"], f, None)
        coach.store.execute(
            "INSERT INTO coach_lessons(ts, entry_id, strategy_id, label, src, outcome, pnl, stake, p_entry, p_close, clv, "
            "luck, expected, cause, explanation, features) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (b["date"].isoformat(), i, f"Leo_{b['moment']}_{b['side']}", f"{b['match']} · {b['side']} {b['sel']}",
             "palestra", "WON" if b["won"] else "LOST", b["pnl"], 1.0, b["p"],
             b.get("pc"), b["clv"], (1.0 if b["won"] else 0.0) - b["p"], b["ev"] * b["risk"],
             cause, explain(cause, b["won"], b["p"], b.get("pc"), f, b["pnl"]),
             json.dumps(f, default=str)))
    coach.learn()
    if len(B):
        B["roi"] = B.pnl / B.risk
        grp = []
        for (moment, side, season), g in B.groupby(["moment", "side", "season"]):
            grp.append({"momento": moment, "lato": side, "stagione": season, "puntate": len(g), "vinte": float(g.won.mean()),
                        "roi": float(g.pnl.sum() / g.risk.sum()), "se": float(g.roi.std() / math.sqrt(len(g))) if len(g) > 1 else None,
                        "clv": float(g.clv.dropna().mean()) if g.clv.notna().any() else None})
        out["per_stagione"] = grp
        tot = []
        for (moment, side), g in B.groupby(["moment", "side"]):
            tot.append({"momento": moment, "lato": side, "puntate": len(g), "vinte": float(g.won.mean()),
                        "roi": float(g.pnl.sum() / g.risk.sum()), "se": float(g.roi.std() / math.sqrt(len(g))),
                        "clv": float(g.clv.dropna().mean()) if g.clv.notna().any() else None,
                        "fiducia": {"puntate": int(g.trusted.sum()),
                                    "roi": float(g[g.trusted].pnl.sum() / g[g.trusted].risk.sum()) if g.trusted.any() else None,
                                    "clv": float(g[g.trusted].clv.dropna().mean()) if g[g.trusted].clv.notna().any() else None},
                        "solo_betfair": {"puntate": int(g.exchange.sum()),
                                         "roi": float(g[g.exchange].pnl.sum() / g[g.exchange].risk.sum()) if g.exchange.any() else None}})
        out["totale"] = tot
    # dove il mercato sbaglia: frequenza reale contro probabilità di chiusura, per ogni dinamica
    V = pd.DataFrame(market_view)
    blind = []
    if len(V):
        V = V[V.sel != "draw"]
        for key in sorted({k for d in V.dyn for k in d}):
            vals = V.dyn.map(lambda d: d.get(key))
            for val in vals.dropna().unique():
                g = V[vals == val]
                if len(g) < 300:
                    continue
                diff = g.hit.mean() - g.p.mean()
                se = math.sqrt(max(g.p.mean() * (1 - g.p.mean()), 1e-6) / len(g))
                blind.append({"dinamica": key, "valore": val, "n": len(g), "mercato": float(g.p.mean()),
                              "reale": float(g.hit.mean()), "scarto": float(diff), "z": float(diff / se)})
    out["dinamiche"] = sorted(blind, key=lambda r: -abs(r["z"]))
    # cosa ha imparato il modello (ultimo addestramento) e come è cambiato nel tempo
    learned = []
    for k, mdl in models.items():
        if mdl.history:
            last = mdl.history[-1]["weights"]
            first = mdl.history[0]["weights"]
            for feat, w in last.items():
                learned.append({"momento": k, "dinamica": NAMES.get(feat, feat), "peso_casa": w[0], "peso_pari": w[1],
                                "peso_ospite": w[2], "forza": abs(w[0] - w[2]),
                                "stabile": (w[0] - w[2]) * (first[feat][0] - first[feat][2]) > 0})
    out["pesi"] = sorted(learned, key=lambda r: -r["forza"])[:20]
    out["lezioni"] = coach.view()
    MODEL_FILE.write_text(json.dumps({k: {"feats": m.feats, "mu": None if m.mu is None else m.mu.tolist(),
                                          "sd": None if m.sd is None else m.sd.tolist(), "W": m.W.tolist(),
                                          "history": m.history[-3:]} for k, m in models.items()}), encoding="utf-8")
    SUMMARY_FILE.write_text(json.dumps(out, default=str), encoding="utf-8")
    return out


def report(res: dict) -> str:
    pct = lambda x: "—" if x is None else f"{x:+.1%}"
    L = ["# La palestra di Leo", "",
         f"{res['matches']} partite dal {res['first']} al {res['last']}; {res['with_understat']} con xG e formazioni "
         f"(Understat). Leo le ha rivissute in ordine di data senza conoscere il risultato e si è riaddestrato "
         f"{res['retrains']} volte. Prezzi Betfair scartati perché incoerenti con Pinnacle: {res.get('bfe_scartati', 0)} partite.", ""]
    for k, name in (("A", "2 giorni prima (quote del venerdì/martedì)"), ("B", "al fischio d'inizio (con le formazioni)")):
        if f"logloss_{k}" in res:
            x = res[f"logloss_{k}"]
            d = x["mercato"] - x["leo"]
            verdict = ("Leo prevede **meglio** del mercato." if d > 0.0005 else
                       "il mercato resta più preciso di Leo." if d < -0.0005 else "Leo e mercato sono **alla pari**.")
            L.append(f"- **{name}**: log-loss mercato {x['mercato']:.4f}, Leo {x['leo']:.4f} su {x['n']} partite → " + verdict)
    L += ["", "## Le puntate che avrebbe fatto (EV netto ≥ 2%, commissione 4,5%)", "",
          "| Momento | Lato | Puntate | Vinte | ROI sul rischio | Errore | CLV | Solo prezzi Betfair | Solo quando Leo si fida |",
          "|---|---|---|---|---|---|---|---|---|"]
    for t in res.get("totale", []):
        sb = t["solo_betfair"]
        L.append(f"| {t['momento']} | {t['lato']} | {t['puntate']} | {t['vinte']:.1%} | {t['roi']:+.2%} | ±{t['se']:.2%} | "
                 f"{pct(t['clv'])} | {sb['puntate']} puntate, ROI {pct(sb['roi'])} | "
                 f"{t['fiducia']['puntate']} puntate, ROI {pct(t['fiducia']['roi'])}, CLV {pct(t['fiducia']['clv'])} |")
    L += ["", "Prima del 2024/25 football-data non ha i prezzi Betfair: si usa il prezzo di Pinnacle con la commissione "
          "Betfair (un'approssimazione). Contano soprattutto le righe 'Solo prezzi Betfair'.", "",
          "## Progressione per stagione", "", "| Momento | Lato | Stagione | Puntate | Vinte | ROI | CLV |", "|---|---|---|---|---|---|---|"]
    for t in res.get("per_stagione", []):
        L.append(f"| {t['momento']} | {t['lato']} | {t['stagione']} | {t['puntate']} | {t['vinte']:.1%} | {t['roi']:+.2%} | {pct(t['clv'])} |")
    L += ["", "## Dove il mercato sbaglia (chiusura di Pinnacle contro realtà)", "",
          "Scarto = frequenza reale − probabilità del mercato. |z| ≥ 2 vuol dire difficilmente un caso.", "",
          "| Dinamica | Valore | Partite | Mercato | Realtà | Scarto | z |", "|---|---|---|---|---|---|---|"]
    for d in res.get("dinamiche", [])[:25]:
        L.append(f"| {d['dinamica']} | {d['valore']} | {d['n']} | {d['mercato']:.1%} | {d['reale']:.1%} | {d['scarto']:+.1%} | {d['z']:+.1f} |")
    L += ["", "## Cosa ha imparato il modello (pesi dell'ultimo addestramento)", "",
          "| Momento | Dinamica | Spinta verso casa − ospite | Stabile nel tempo |", "|---|---|---|---|"]
    for w in res.get("pesi", [])[:15]:
        L.append(f"| {w['momento']} | {w['dinamica']} | {w['peso_casa'] - w['peso_ospite']:+.3f} | {'sì' if w['stabile'] else 'no'} |")
    lz = res.get("lezioni") or {}
    L += ["", "## Autopsie e regole", "", f"Autopsie: {sum((lz.get('causes') or {}).values())}; CLV medio {pct(lz.get('clv'))}; "
          f"regole nate: {sum(1 for r in (lz.get('rules') or []) if r.get('active'))}.", ""]
    for c, n in sorted((lz.get("causes") or {}).items(), key=lambda kv: -kv[1]):
        L.append(f"- {(lz.get('cause_labels') or {}).get(c, c)}: {n}")
    for r in (lz.get("rules") or [])[:12]:
        if r["active"]:
            L.append(f"- Regola per {r['strategy_id']}: {r['kind']} {r['feature']} = {r['value']} "
                     f"(n {r['n']}, CLV {pct(r['clv'])})")
    return "\n".join(L) + "\n"
