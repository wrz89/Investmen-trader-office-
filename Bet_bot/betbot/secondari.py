"""Mercati secondari (angoli e cartellini): modello statistico contro i prezzi Betfair, SOLO SU CARTA.

Perché così
  Le quote storiche di angoli e cartellini non sono gratis: OddsPapi le ha (Pinnacle, dal 01/2026) ma ogni chiamata
  per l'elenco partite consuma la quota mensile (250) e il 10/10/2026 l'abbiamo finita a metà raccolta; Betfair Historic
  Data è bloccata dall'Italia. Allora si registra da oggi in avanti: il bot, mentre gira, salva i prezzi Betfair dei
  mercati "Angoli/Cartellini Over/Under" dei campionati coperti da football-data e annota una puntata FINTA quando il
  modello vede valore. A partita finita i CSV di football-data (HC/AC angoli, HY/AY/HR/AR cartellini) dicono com'è andata.

Modello (dati football-data, 2015-oggi, ~50.000 partite)
  Angoli di ciascuna squadra: Poisson log-lineare su medie mobili (angoli fatti/subiti in casa/fuori e in generale,
  media del campionato), forza dalle quote 1X2 Betfair della partita e probabilità dell'over 2,5. Totale = somma.
  Cartellini: medie mobili dei gialli fatti/subiti, falli, media del campionato, equilibrio della partita.
  Prova walk-forward (stagioni 2025/26 e 2026/27): angoli, correlazione previsione-realtà 0,16-0,21, log-likelihood
  migliore della media del campionato; cartellini correlazione 0,28-0,29. Poco, ma i mercati secondari hanno libri sottili.
  Cartellini su Betfair: giallo = 1, rosso = 2 (approssimazione: con football-data il doppio giallo conta un po' di più).

Regola (la solita)
  Puntata finta: la migliore tra back/lay over/under dello stesso mercato, valore atteso netto ≥ 5% dopo la commissione
  (4,5%), quota 1,30-5,00, almeno 2 € disponibili, inizio tra 1 e 24 ore. Una sola per mercato.
  Verdetto con ≥ 200 puntate regolate: PROMETTENTE solo se ROI − 2 errori standard > 0, BOCCIATA se ROI + 2 e.s. < 0.
  Nessuna puntata vera finché non è PROMETTENTE e l'utente non lo chiede.
"""
from __future__ import annotations

import difflib
import json
import math
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from .config import REPORTS_DIR, RUNTIME_DIR

DB = RUNTIME_DIR / "secondari.db"
MODEL_FILE = RUNTIME_DIR / "secondari_modello.json"
REPORT = REPORTS_DIR / "secondari.txt"
COMMISSION = 0.045
EV_MIN, PRICE_MIN, PRICE_MAX, SIZE_MIN = 0.05, 1.30, 5.00, 2.0
WINDOW_H = (1.0, 24.0)
EVERY_S = 1800
A = 0.08                                    # peso della media mobile
DISP = {"angoli": 45.0, "cartellini": 60.0}  # binomiale negativa: varianza = mu + mu²/k (misurata: 1,2 e 1,07 × media)
DIVS = [  # nome del campionato su Betfair → divisione di football-data
    (r"english premier|^premier league$", "E0"), (r"english championship", "E1"), (r"english league 1|league one", "E2"),
    (r"english league 2|league two", "E3"), (r"scottish premiership", "SC0"), (r"german bundesliga 2|2\. bundesliga", "D2"),
    (r"german bundesliga$|^bundesliga$", "D1"), (r"italian serie a|^serie a$", "I1"), (r"italian serie b", "I2"),
    (r"spanish segunda|la liga 2", "SP2"), (r"spanish la liga|^la liga$", "SP1"), (r"french ligue 1|^ligue 1$", "F1"),
    (r"french ligue 2|^ligue 2$", "F2"), (r"dutch eredivisie|^eredivisie$", "N1"), (r"belgian first|jupiler|belgian pro", "B1"),
    (r"portuguese primeira|primeira liga", "P1"), (r"turkish super", "T1"), (r"greek super", "G1"),
]
ALL_DIVS = sorted({d for _, d in DIVS})
RUNNER_RX = re.compile(r"\b(over|under)\s+(\d+(?:\.\d+)?)", re.I)


def comp_div(name: str | None) -> str | None:
    t = (name or "").lower()
    return next((d for rx, d in DIVS if re.search(rx, t)), None)


def market_kind(code: str, name: str = "") -> str | None:
    t = f"{code} {name}".upper()
    if "CORN" in t:
        return "angoli"
    if "CARD" in t or "BOOK" in t:
        return "cartellini"
    return None


def parse_runner(name: str) -> tuple[str, float] | None:
    """"Over 10.5 Corners" → ("over", 10.5). Solo linee .5 (niente rimborso)."""
    m = RUNNER_RX.search(name or "")
    if not m:
        return None
    line = float(m.group(2))
    return (m.group(1).lower(), line) if abs(line - math.floor(line) - 0.5) < 1e-9 else None


# ── distribuzioni ────────────────────────────────────────────────────────────────────────────────────
def nb_cdf(k: int, mu: float, r: float) -> float:
    """P(X ≤ k) per la binomiale negativa di media mu e varianza mu + mu²/r."""
    if k < 0:
        return 0.0
    p = r / (r + mu)
    logp, log1p = math.log(p), math.log(1 - p)
    s = 0.0
    for i in range(k + 1):
        s += math.exp(math.lgamma(i + r) - math.lgamma(r) - math.lgamma(i + 1) + r * logp + i * log1p)
    return min(1.0, s)


def p_over(line: float, mu: float, r: float) -> float:
    return 1.0 - nb_cdf(int(math.floor(line)), mu, r)


def ev_options(p_ov: float, over: dict, under: dict, c: float = COMMISSION) -> list[dict]:
    """Valore atteso netto per 1 € (back: puntata; lay: puntata del banco, responsabilità = quota−1) delle 4 scelte."""
    out = []
    for side, p, book in (("over", p_ov, over), ("under", 1 - p_ov, under)):
        b, l = book.get("back"), book.get("lay")
        if b:
            out.append({"side": side, "dir": "back", "price": b, "size": book.get("back_size") or 0,
                        "ev": p * (b - 1) * (1 - c) - (1 - p)})
        if l:
            out.append({"side": side, "dir": "lay", "price": l, "size": book.get("lay_size") or 0,
                        "ev": (1 - p) * (1 - c) - p * (l - 1)})
    return out


def pick(p_ov: float, over: dict, under: dict, ev_min: float = EV_MIN) -> dict | None:
    ok = [o for o in ev_options(p_ov, over, under)
          if o["ev"] >= ev_min and PRICE_MIN <= o["price"] <= PRICE_MAX and o["size"] >= SIZE_MIN]
    return max(ok, key=lambda o: o["ev"]) if ok else None


def outcome_pl(bet: dict, total: float, c: float = COMMISSION) -> float:
    """Risultato per 1 € puntato (o bancato) dato il totale reale di angoli o cartellini."""
    over_won = total > bet["line"]
    side_won = over_won if bet["side"] == "over" else not over_won
    if bet["dir"] == "back":
        return (bet["price"] - 1) * (1 - c) if side_won else -1.0
    return -(bet["price"] - 1) if side_won else 1 - c


# ── dati football-data e modello ────────────────────────────────────────────────────────────────────
def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _date(s: str):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except (TypeError, ValueError):
            pass
    return None


def load_rows(paths=None) -> list[dict]:
    import csv
    import io
    from .backtest import HISTORY_DIR
    if paths is None:
        paths = [p for p in HISTORY_DIR.glob("*_*.csv") if p.stem.split("_", 1)[1] in ALL_DIVS]
    rows = []
    for p in paths:
        raw = open(p, encoding="utf-8-sig", errors="replace").read()
        for r in csv.DictReader(io.StringIO(raw)):
            d = _date(r.get("Date", ""))
            if not d or not r.get("HomeTeam") or _f(r.get("HC")) is None or _f(r.get("HY")) is None:
                continue
            g = lambda *ks: next((v for v in (_f(r.get(k)) for k in ks) if v), None)
            rows.append({"div": r.get("Div") or p.stem.split("_", 1)[1], "date": d, "home": r["HomeTeam"].strip(),
                         "away": r["AwayTeam"].strip(), "hc": _f(r["HC"]), "ac": _f(r["AC"]), "hy": _f(r["HY"]),
                         "ay": _f(r["AY"]), "hr": _f(r.get("HR")) or 0, "ar": _f(r.get("AR")) or 0,
                         "hf": _f(r.get("HF")), "af": _f(r.get("AF")),
                         "ph": g("PSCH", "PSH", "AvgCH", "AvgH"), "pd": g("PSCD", "PSD", "AvgCD", "AvgD"),
                         "pa": g("PSCA", "PSA", "AvgCA", "AvgA"),
                         "o25": g("PC>2.5", "P>2.5", "AvgC>2.5", "Avg>2.5"), "u25": g("PC<2.5", "P<2.5", "AvgC<2.5", "Avg<2.5")})
    rows.sort(key=lambda r: (r["date"], r["div"], r["home"]))
    return rows


def _ew(d: dict, k: str, v):
    if v is not None:
        d[k] = v if k not in d else (1 - A) * d[k] + A * v


def _probs(ph, pd, pa, o25=None, u25=None):
    ip = [1 / ph, 1 / pd, 1 / pa]
    s = sum(ip)
    pov = 0.5
    if o25 and u25:
        pov = (1 / o25) / (1 / o25 + 1 / u25)
    return ip[0] / s, ip[1] / s, ip[2] / s, pov


def x_corners(th: dict, ta: dict, lc: float, ph: float, pa: float, pov: float, side: str) -> list[float]:
    t, o = (th, ta) if side == "h" else (ta, th)
    cf = t.get("cf_" + side, t["cf"])
    ca = o.get("ca_" + ("a" if side == "h" else "h"), o["ca"])
    own, opp = (ph, pa) if side == "h" else (pa, ph)
    half = lc / 2
    lg = lambda v: math.log(max(v, 0.5) / half)
    return [1.0, lg(cf), lg(ca), lg(t["cf"]), lg(o["ca"]), math.log(half), own - opp, (own - opp) ** 2, pov - 0.5,
            1.0 if side == "h" else 0.0]


def x_cards(th: dict, ta: dict, ly: float, ph: float, pd: float, pa: float) -> list[float]:
    lg = lambda v: math.log(max(v, 0.3) / ly)
    fouls = (th.get("ff") or 11) + (ta.get("ff") or 11)
    return [1.0, lg(th["yf"] + ta["ya"]), lg(ta["yf"] + th["ya"]), math.log(ly), abs(ph - pa), pd,
            math.log(max(fouls, 1) / 22)]


def _team_update(st: dict, lg: dict, r: dict) -> None:
    L = lg.setdefault(r["div"], {"c": 10.3, "y": 3.8})
    th = st.setdefault(f'{r["div"]}|{r["home"]}', {"n": 0})
    ta = st.setdefault(f'{r["div"]}|{r["away"]}', {"n": 0})
    L["c"] = 0.995 * L["c"] + 0.005 * (r["hc"] + r["ac"])
    L["y"] = 0.995 * L["y"] + 0.005 * (r["hy"] + r["ay"])
    for t, cf, ca, yf, ya, ff, s in ((th, r["hc"], r["ac"], r["hy"], r["ay"], r["hf"], "h"),
                                     (ta, r["ac"], r["hc"], r["ay"], r["hy"], r["af"], "a")):
        for k, v in (("cf", cf), ("ca", ca), ("yf", yf), ("ya", ya), ("ff", ff), ("cf_" + s, cf), ("ca_" + s, ca)):
            _ew(t, k, v)
        t["n"] += 1
        t["last"] = r["date"].isoformat()


def poisson_fit(X, y, iters: int = 40):
    import numpy as np
    X, y = np.asarray(X, float), np.asarray(y, float)
    b = np.zeros(X.shape[1])
    b[0] = math.log(max(y.mean(), 1e-6))
    for _ in range(iters):
        mu = np.exp(np.clip(X @ b, -20, 20))
        step = np.linalg.solve((X * mu[:, None]).T @ X + 1e-6 * np.eye(len(b)), X.T @ (y - mu))
        b += step
        if np.abs(step).max() < 1e-8:
            break
    return [float(v) for v in b]


def build_model(rows: list[dict]) -> dict:
    """Stato delle squadre dopo l'ultima partita e coefficienti allenati su tutte le partite (feature sempre PRIMA)."""
    st, lg = {}, {}
    Xc, yc, Xy, yy = [], [], [], []
    for r in rows:
        L = lg.get(r["div"]) or {"c": 10.3, "y": 3.8}
        th, ta = st.get(f'{r["div"]}|{r["home"]}'), st.get(f'{r["div"]}|{r["away"]}')
        if th and ta and th["n"] >= 6 and ta["n"] >= 6 and r["ph"] and r["pd"] and r["pa"]:
            ph, pd, pa, pov = _probs(r["ph"], r["pd"], r["pa"], r["o25"], r["u25"])
            Xc += [x_corners(th, ta, L["c"], ph, pa, pov, "h"), x_corners(th, ta, L["c"], ph, pa, pov, "a")]
            yc += [r["hc"], r["ac"]]
            Xy.append(x_cards(th, ta, L["y"], ph, pd, pa))
            yy.append(r["hy"] + r["ay"] + 2 * (r["hr"] + r["ar"]))
        _team_update(st, lg, r)
    return {"built": time.time(), "n": len(rows), "corners": poisson_fit(Xc, yc) if Xc else None,
            "cards": poisson_fit(Xy, yy) if Xy else None, "teams": st, "leagues": lg}


def norm_team(s: str) -> str:
    s = s.lower().replace("utd", "united").replace("man ", "manchester ").replace("nott'm", "nottingham").replace("atletico", "ath").replace("athletic", "ath")
    s = re.sub(r"\b(fc|afc|cf|sc|ac|as|ss|us|calcio|club|sv|vfl|vfb|fk|sk|rc|cd|ud|sd|rcd|cp|bk|kv|krc|ogc|1\.)\b", " ", s)
    return re.sub(r"[^a-z0-9]", "", s)


def find_team(model: dict, div: str, name: str) -> str | None:
    """Nome Betfair → chiave della squadra in football-data (stesso campionato, giocato nell'ultimo anno)."""
    cutoff = (datetime.now().date() - timedelta(days=400)).isoformat()
    cands = [k for k, t in model["teams"].items() if k.startswith(div + "|") and t.get("last", "") >= cutoff]
    n = norm_team(name)
    best, bs = None, 0.0
    for k in cands:
        m = norm_team(k.split("|", 1)[1])
        s = 1.0 if (m == n or (len(m) >= 4 and (m in n or n in m))) else difflib.SequenceMatcher(None, n, m).ratio()
        if s > bs:
            best, bs = k, s
    return best if bs >= 0.72 else None


def predict(model: dict, div: str, home: str, away: str, ph: float, pd: float, pa: float, pov: float) -> dict | None:
    import numpy as np
    kh, ka = find_team(model, div, home), find_team(model, div, away)
    if not kh or not ka or kh == ka:
        return None
    th, ta = model["teams"][kh], model["teams"][ka]
    if th["n"] < 6 or ta["n"] < 6:
        return None
    L = model["leagues"].get(div) or {"c": 10.3, "y": 3.8}
    bc, by = np.array(model["corners"]), np.array(model["cards"])
    mu_c = float(np.exp(np.array(x_corners(th, ta, L["c"], ph, pa, pov, "h")) @ bc)
                 + np.exp(np.array(x_corners(th, ta, L["c"], ph, pa, pov, "a")) @ bc))
    mu_y = float(np.exp(np.array(x_cards(th, ta, L["y"], ph, pd, pa)) @ by))
    return {"angoli": mu_c, "cartellini": mu_y, "home_fd": kh.split("|", 1)[1], "away_fd": ka.split("|", 1)[1]}


def load_model(max_age_s: float = 86400, out=None) -> dict | None:
    """Modello in cache; si ricostruisce una volta al giorno (scaricando la stagione in corso dei campionati)."""
    try:
        m = json.loads(MODEL_FILE.read_text(encoding="utf-8"))
        if time.time() - m.get("built", 0) < max_age_s and m.get("corners"):
            return m
    except (OSError, ValueError):
        pass
    try:
        from .backtest import download, exchange_seasons
        download(ALL_DIVS, exchange_seasons()[-1:], refresh_current=True)
    except Exception as exc:                       # senza rete si usa la copia che c'è
        if out:
            out(f"football-data non raggiungibile: {exc}")
    rows = load_rows()
    if not rows:
        return None
    m = build_model(rows)
    MODEL_FILE.write_text(json.dumps(m), encoding="utf-8")
    return m


# ── registro ─────────────────────────────────────────────────────────────────────────────────────────
def db(path=None) -> sqlite3.Connection:
    con = sqlite3.connect(str(path or DB), timeout=30)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS quote(ts REAL, market_id TEXT, event_id TEXT, kickoff TEXT, kind TEXT, line REAL,
        side TEXT, back REAL, back_size REAL, lay REAL, lay_size REAL);
    CREATE INDEX IF NOT EXISTS q_m ON quote(market_id, ts);
    CREATE TABLE IF NOT EXISTS evento(event_id TEXT PRIMARY KEY, ts REAL, div TEXT, comp TEXT, home TEXT, away TEXT,
        kickoff TEXT, ph REAL, pd REAL, pa REAL, pov REAL, mu_angoli REAL, mu_cartellini REAL, home_fd TEXT, away_fd TEXT);
    CREATE TABLE IF NOT EXISTS giocata(market_id TEXT PRIMARY KEY, ts REAL, event_id TEXT, kind TEXT, line REAL,
        side TEXT, dir TEXT, price REAL, p_model REAL, ev REAL, mu REAL, settled INTEGER DEFAULT 0, total REAL, pl REAL,
        close_mid REAL);
    """)
    return con


def _book(rb: dict) -> dict:
    ex = rb.get("ex") or {}
    b = (ex.get("availableToBack") or [{}])[0]
    l = (ex.get("availableToLay") or [{}])[0]
    return {"back": b.get("price"), "back_size": b.get("size"), "lay": l.get("price"), "lay_size": l.get("size")}


def _mid(book: dict) -> float | None:
    b, l = book.get("back"), book.get("lay")
    if b and l and l / b - 1 <= 0.10:
        return (b + l) / 2
    return None


class Registratore:
    """Ogni 30 minuti: prezzi Betfair di angoli/cartellini delle prossime 24 ore + puntate finte del modello."""

    def __init__(self, client, path=None):
        self.client, self.path = client, path
        self.types: list[str] = []
        self.comps: dict[str, str] = {}
        self.meta_ts = 0.0
        self.last = 0.0
        self.note = ""
        self._lock = threading.Lock()

    def _meta(self, now: float) -> None:
        if now - self.meta_ts < 6 * 3600 and self.types:
            return
        types = self.client.market_types("1")
        self.types = sorted(t for t in types if market_kind(t))
        self.comps = {cid: name for cid, name in self.client.competitions("1").items() if comp_div(name)}
        self.meta_ts = now

    def _catalogue(self, now: float) -> list[dict]:
        out, ids = [], list(self.comps)
        t0 = datetime.fromtimestamp(now, timezone.utc)
        for i in range(0, len(ids), 10):
            f = {"eventTypeIds": ["1"], "competitionIds": ids[i:i + 10],
                 "marketTypeCodes": self.types + ["MATCH_ODDS", "OVER_UNDER_25"],
                 "marketStartTime": {"from": (t0 + timedelta(hours=WINDOW_H[0])).isoformat(timespec="seconds").replace("+00:00", "Z"),
                                     "to": (t0 + timedelta(hours=WINDOW_H[1])).isoformat(timespec="seconds").replace("+00:00", "Z")}}
            out += self.client.rpc("listMarketCatalogue", {"filter": f, "maxResults": 1000, "sort": "FIRST_TO_START",
                                                           "marketProjection": ["EVENT", "COMPETITION", "RUNNER_DESCRIPTION",
                                                                                "MARKET_START_TIME"]})
        return out

    def tick(self, now: float | None = None, model: dict | None = None) -> int:
        """Un giro di registrazione. Restituisce le puntate finte nuove."""
        from .feeds.betfair import split_event_name
        now = now or time.time()
        if not self._lock.acquire(blocking=False):
            return 0
        try:
            self.last = now
            model = model or load_model()
            self._meta(now)
            if not self.types or not self.comps:
                self.note = "nessun mercato angoli/cartellini su Betfair"
                return 0
            cat = self._catalogue(now)
            books = {b["marketId"]: b for b in self.client.books([c["marketId"] for c in cat])}
            events: dict[str, dict] = {}
            for c in cat:
                ev = c.get("event") or {}
                e = events.setdefault(str(ev.get("id")), {"name": ev.get("name", ""), "comp": (c.get("competition") or {}).get("name", ""),
                                                          "kickoff": c.get("marketStartTime"), "markets": []})
                e["markets"].append(c)
            con = db(self.path)
            new = 0
            with con:
                for eid, e in events.items():
                    div, teams = comp_div(e["comp"]), split_event_name(e["name"])
                    if not div or not teams:
                        continue
                    ref = {}
                    for c in e["markets"]:
                        b = books.get(c["marketId"])
                        if not b or b.get("status") != "OPEN":
                            continue
                        names = {r["selectionId"]: r.get("runnerName", "") for r in c.get("runners", [])}
                        for rb in b.get("runners", []):
                            nm, bk = names.get(rb["selectionId"], ""), _book(rb)
                            if c.get("marketType") == "MATCH_ODDS":
                                key = "d" if nm.lower() in ("the draw", "draw", "pareggio") else ("h" if nm == teams[0] else "a" if nm == teams[1] else None)
                                if key and _mid(bk):
                                    ref[key] = _mid(bk)
                            elif c.get("marketType") == "OVER_UNDER_25":
                                if nm.lower().startswith("over") and _mid(bk):
                                    ref["o25"] = _mid(bk)
                                elif nm.lower().startswith("under") and _mid(bk):
                                    ref["u25"] = _mid(bk)
                    pred = None
                    if model and all(k in ref for k in "hda"):
                        ph, pd, pa, pov = _probs(ref["h"], ref["d"], ref["a"], ref.get("o25"), ref.get("u25"))
                        pred = predict(model, div, teams[0], teams[1], ph, pd, pa, pov)
                        con.execute("INSERT OR REPLACE INTO evento VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                    (eid, now, div, e["comp"], teams[0], teams[1], e["kickoff"], ph, pd, pa, pov,
                                     pred and pred["angoli"], pred and pred["cartellini"], pred and pred["home_fd"],
                                     pred and pred["away_fd"]))
                    for c in e["markets"]:
                        kind = market_kind(c.get("marketType", ""), c.get("marketName", ""))
                        b = books.get(c["marketId"])
                        if not kind or not b or b.get("status") != "OPEN":
                            continue
                        names = {r["selectionId"]: r.get("runnerName", "") for r in c.get("runners", [])}
                        sides = {}
                        for rb in b.get("runners", []):
                            pr = parse_runner(names.get(rb["selectionId"], ""))
                            if pr:
                                sides[pr[0]] = (pr[1], _book(rb))
                        if set(sides) != {"over", "under"} or sides["over"][0] != sides["under"][0]:
                            continue                                  # solo mercati Over/Under a due esiti
                        line = sides["over"][0]
                        for side, (_, bk) in sides.items():
                            con.execute("INSERT INTO quote VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                        (now, c["marketId"], eid, e["kickoff"], kind, line, side, bk["back"],
                                         bk["back_size"], bk["lay"], bk["lay_size"]))
                        if not pred:
                            continue
                        p = p_over(line, pred[kind], DISP[kind])
                        choice = pick(p, sides["over"][1], sides["under"][1])
                        if choice and not con.execute("SELECT 1 FROM giocata WHERE market_id=?", (c["marketId"],)).fetchone():
                            con.execute("INSERT INTO giocata(market_id, ts, event_id, kind, line, side, dir, price, p_model, ev, mu)"
                                        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                        (c["marketId"], now, eid, kind, line, choice["side"], choice["dir"], choice["price"],
                                         p if choice["side"] == "over" else 1 - p, choice["ev"], pred[kind]))
                            new += 1
            con.close()
            self.note = f"{len(events)} partite, {new} puntate finte nuove"
            return new
        finally:
            self._lock.release()


_REG: Registratore | None = None


def maybe_tick(client, settings: dict | None = None) -> None:
    """Dal ciclo del bot: avvia un giro in un thread a parte ogni 30 minuti (non rallenta mai le puntate)."""
    global _REG
    cfg = (settings or {}).get("secondari") or {}
    if client is None or cfg.get("attivo") is False:
        return
    if _REG is None or _REG.client is not client:
        _REG = Registratore(client)
    if time.time() - _REG.last < cfg.get("ogni_secondi", EVERY_S):
        return
    _REG.last = time.time()

    def run():
        try:
            _REG.tick()
        except Exception as exc:                   # il registro non deve mai disturbare il bot
            _REG.note = f"errore: {exc}"
    threading.Thread(target=run, daemon=True, name="secondari").start()


# ── regolamento e rapporto ───────────────────────────────────────────────────────────────────────────
def settle(con, rows: list[dict]) -> int:
    """Regola le puntate finte con i risultati di football-data (stessa divisione, data ±1 giorno, nomi del modello)."""
    idx = {}
    for r in rows:
        idx.setdefault((r["div"], r["home"], r["away"]), []).append(r)
    n = 0
    q = con.execute("SELECT g.market_id, g.kind, g.line, g.side, g.dir, g.price, e.div, e.home_fd, e.away_fd, e.kickoff "
                    "FROM giocata g JOIN evento e ON e.event_id=g.event_id WHERE g.settled=0").fetchall()
    for mid, kind, line, side, dr, price, div, hf, af, ko in q:
        try:
            day = datetime.fromisoformat(str(ko).replace("Z", "+00:00")).date()
        except ValueError:
            continue
        hit = [r for r in idx.get((div, hf, af), []) if abs((r["date"] - day).days) <= 1]
        if not hit:
            continue
        r = hit[0]
        total = r["hc"] + r["ac"] if kind == "angoli" else r["hy"] + r["ay"] + 2 * (r["hr"] + r["ar"])
        pl = outcome_pl({"line": line, "side": side, "dir": dr, "price": price}, total)
        last = con.execute("SELECT back, lay FROM quote WHERE market_id=? AND side=? ORDER BY ts DESC LIMIT 1",
                           (mid, side)).fetchone()
        close = _mid({"back": last[0], "lay": last[1]}) if last else None
        con.execute("UPDATE giocata SET settled=1, total=?, pl=?, close_mid=? WHERE market_id=?", (total, pl, close, mid))
        n += 1
    return n


def summary(con) -> dict:
    out = {}
    for kind in ("angoli", "cartellini", None):
        sql = "SELECT pl, price, close_mid, dir FROM giocata WHERE settled=1" + (" AND kind=?" if kind else "")
        rows = con.execute(sql, (kind,) if kind else ()).fetchall()
        pls = [r[0] for r in rows]
        n = len(pls)
        roi = sum(pls) / n if n else 0.0
        se = (sum((x - roi) ** 2 for x in pls) / max(n - 1, 1)) ** 0.5 / math.sqrt(n) if n > 1 else 0.0
        clv = [(r[1] / r[2] - 1) if r[3] == "back" else (r[2] / r[1] - 1) for r in rows if r[2]]
        out[kind or "totale"] = {"n": n, "roi": roi, "se": se, "clv": sum(clv) / len(clv) if clv else None}
    out["aperte"] = con.execute("SELECT COUNT(*) FROM giocata WHERE settled=0").fetchone()[0]
    out["quote"] = con.execute("SELECT COUNT(DISTINCT market_id) FROM quote").fetchone()[0]
    return out


def verdict(s: dict, need: int = 200) -> str:
    if s["n"] < need:
        return f"presto ({s['n']}/{need} puntate regolate)"
    if s["roi"] - 2 * s["se"] > 0:
        return "PROMETTENTE (ancora solo su carta: decidere con l'utente)"
    if s["roi"] + 2 * s["se"] < 0:
        return "BOCCIATA"
    return "nessun vantaggio dimostrato (il margine d'errore attraversa lo zero)"


def report_text(s: dict) -> str:
    lines = ["Mercati secondari (angoli e cartellini) — modello contro Betfair, SOLO SU CARTA.", "",
             f"Mercati registrati: {s['quote']} · puntate finte in attesa del risultato: {s['aperte']}", ""]
    for k in ("angoli", "cartellini", "totale"):
        x = s[k]
        clv = f" · CLV {x['clv'] * 100:+.1f}%" if x["clv"] is not None else ""
        roi = f"ROI {x['roi'] * 100:+.1f}% ± {2 * x['se'] * 100:.1f}%" if x["n"] else "nessuna regolata"
        lines.append(f"  {k}: {x['n']} puntate · {roi}{clv} → {verdict(x)}")
    lines += ["", "Regola: valore atteso netto ≥ 5% dopo la commissione, quota 1,30-5,00, ≥ 2 € disponibili, 1-24 ore prima.",
              "I risultati arrivano da football-data (aggiornato 1-2 volte a settimana): le puntate restano aperte qualche giorno.",
              "Verdetto con almeno 200 puntate: PROMETTENTE solo se ROI − 2 errori standard > 0. Nessuna puntata vera."]
    return "\n".join(lines) + "\n"


def run(out=print) -> int:
    from .backtest import download, exchange_seasons
    try:
        download(ALL_DIVS, exchange_seasons()[-1:], refresh_current=True)
    except Exception as exc:
        out(f"football-data non raggiungibile: {exc} (uso le copie che ci sono)")
    con = db()
    with con:
        n = settle(con, load_rows())
    text = report_text(summary(con))
    con.close()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(text, encoding="utf-8")
    out(f"Regolate ora: {n}")
    out(text)
    out(f"Rapporto salvato in {REPORT}")
    return 0
