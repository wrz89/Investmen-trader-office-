"""Tennis: statistiche dei giocatori → modello → confronto con le quote (laboratorio, nessuna puntata).

Dati
  • Jeff Sackmann (ATP e WTA, ogni partita del circuito principale con statistiche di servizio), dal mirror pubblico
    github.com/Aneeshers/tennis-sackmann-archive (licenza CC BY-NC-SA 4.0: uso personale, citare Jeff Sackmann).
  • tennis-data.co.uk (già usato dal bot): quote Pinnacle fino al 2024, media del mercato e Betfair Exchange dal 2025.
    Da alcuni server esteri risponde 403: dal PC dell'utente scarica.

Statistiche di ogni giocatore PRIMA della partita (solo partite già giocate, niente sguardi al futuro):
  Elo generale e per superficie, % punti vinti al servizio e in risposta (media mobile), stanchezza (minuti e partite negli
  ultimi 7-14 giorni, giorni di riposo), classifica, età. Modello: regressione logistica sulle differenze tra i due
  giocatori, allenata solo sugli anni precedenti a quello di prova (walk-forward).

Verdetto (stessa regola di sempre): il modello serve solo se la sua log-loss è più bassa di quella del mercato senza
margine, oppure se la combinazione modello+mercato migliora il mercato in modo stabile; le "occasioni" (modello che vede
più probabilità del prezzo) si misurano con ROI e margine d'errore. Se non batte il mercato, lo diciamo e chiudiamo.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict, deque
from datetime import datetime, timedelta

from .config import RUNTIME_DIR

MIRROR = "https://raw.githubusercontent.com/Aneeshers/tennis-sackmann-archive/main/{tour}/{tour}_matches_{year}.csv"
FIRST_YEAR = 2014


def _dir():
    d = RUNTIME_DIR / "tennis_sackmann"
    d.mkdir(parents=True, exist_ok=True)
    return d


def download(years=None, out=print):
    import requests
    years = years or range(FIRST_YEAR, datetime.now().year + 1)
    paths = []
    for tour in ("atp", "wta"):
        for y in years:
            p = _dir() / f"{tour}_{y}.csv"
            fresh = p.exists() and (y < datetime.now().year or (datetime.now().timestamp() - p.stat().st_mtime) < 86400 * 7)
            if not fresh:
                try:
                    r = requests.get(MIRROR.format(tour=tour, year=y), timeout=60)
                    if r.status_code == 200 and r.text.startswith("tourney_id"):
                        p.write_bytes(r.content)
                    else:
                        out(f"  {tour} {y}: risposta {r.status_code}")
                except Exception as exc:
                    out(f"  {tour} {y}: non scaricato ({exc})")
            if p.exists():
                paths.append(p)
    return paths


def _f(v):
    try:
        x = float(v)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def load(paths) -> list[dict]:
    import pandas as pd
    rows = []
    for p in paths:
        tour = "WTA" if p.name.startswith("wta") else "ATP"
        try:
            df = pd.read_csv(p, low_memory=False)
        except Exception:
            continue
        for r in df.to_dict("records"):
            score = str(r.get("score") or "")
            if not score or any(x in score for x in ("RET", "W/O", "DEF", "Walkover")):
                continue                                    # ritiri e walkover: fuori (su Betfair regole diverse)
            if str(r.get("tourney_level")) in ("D",):        # Coppa Davis / BJK: pochi dati e quote rare
                continue
            try:
                d = datetime.strptime(str(int(r["tourney_date"])), "%Y%m%d")
            except (TypeError, ValueError):
                continue
            rows.append({"tour": tour, "date": d, "surface": str(r.get("surface") or "Hard"), "tourney": r.get("tourney_name"),
                         "round": r.get("round"), "match_num": r.get("match_num") or 0,
                         "w": str(r["winner_name"]), "l": str(r["loser_name"]), "minutes": _f(r.get("minutes")),
                         "w_rank": _f(r.get("winner_rank")), "l_rank": _f(r.get("loser_rank")),
                         "w_age": _f(r.get("winner_age")), "l_age": _f(r.get("loser_age")),
                         "w_sv": (_f(r.get("w_svpt")), _f(r.get("w_1stWon")), _f(r.get("w_2ndWon"))),
                         "l_sv": (_f(r.get("l_svpt")), _f(r.get("l_1stWon")), _f(r.get("l_2ndWon")))})
    rows.sort(key=lambda m: (m["date"], m["tourney"] or "", m["match_num"]))
    return rows


# ── statistiche pre-partita ───────────────────────────────────────────────────────
class _Player:
    __slots__ = ("elo", "elo_s", "n", "n_s", "sv", "rt", "recent", "last")

    def __init__(self):
        self.elo, self.elo_s, self.n, self.n_s = 1500.0, defaultdict(lambda: 1500.0), 0, defaultdict(int)
        self.sv, self.rt = 0.62, 0.38                       # % punti vinti al servizio / in risposta (media mobile)
        self.recent: deque = deque()                       # (data, minuti) ultime partite
        self.last = None


def _k(n):                                                 # K che scende con l'esperienza (stile FiveThirtyEight)
    return 250.0 / (n + 5) ** 0.4


def _p(a, b):
    return 1.0 / (1.0 + 10 ** ((b - a) / 400.0))


def build(matches: list[dict]) -> list[dict]:
    """Per ogni partita: caratteristiche del giocatore A (il primo in ordine alfabetico, così il vincitore non si indovina
    dalla posizione) contro B, calcolate PRIMA della partita, e l'esito y (1 se vince A)."""
    P = defaultdict(_Player)
    out = []
    for m in matches:
        a, b = sorted((m["w"], m["l"]))
        A, B = P[a], P[b]
        s = m["surface"]
        day = m["date"]

        def fatigue(pl):
            while pl.recent and (day - pl.recent[0][0]).days > 14:
                pl.recent.popleft()
            mins7 = sum(x[1] or 90 for x in pl.recent if (day - x[0]).days <= 7)
            return mins7, len(pl.recent), (day - pl.last).days if pl.last else 30

        fa, fb = fatigue(A), fatigue(B)
        ra, rb = (m["w_rank"], m["l_rank"]) if a == m["w"] else (m["l_rank"], m["w_rank"])
        ea = 0.5 * A.elo + 0.5 * A.elo_s[s]
        eb = 0.5 * B.elo + 0.5 * B.elo_s[s]
        if min(A.n, B.n) >= 5:                              # giocatori con un minimo di storia
            out.append({"date": day, "tour": m["tour"], "surface": s, "a": a, "b": b, "y": 1 if m["w"] == a else 0,
                        "p_elo": _p(ea, eb),
                        "x": [(ea - eb) / 100.0, (A.sv + A.rt) - (B.sv + B.rt), (fa[0] - fb[0]) / 100.0, fa[1] - fb[1],
                              min(fa[2], 30) - min(fb[2], 30),
                              math.log((rb or 500) / (ra or 500))]})
        # aggiornamento dopo la partita
        W, L = P[m["w"]], P[m["l"]]
        pe = _p(W.elo, L.elo)
        pes = _p(W.elo_s[s], L.elo_s[s])
        W.elo += _k(W.n) * (1 - pe)
        L.elo -= _k(L.n) * (1 - pe)
        W.elo_s[s] += _k(W.n_s[s]) * (1 - pes)
        L.elo_s[s] -= _k(L.n_s[s]) * (1 - pes)
        W.n += 1
        L.n += 1
        W.n_s[s] += 1
        L.n_s[s] += 1
        for pl, sv, osv in ((W, m["w_sv"], m["l_sv"]), (L, m["l_sv"], m["w_sv"])):
            if sv[0] and sv[1] is not None and sv[2] is not None and sv[0] > 0:
                pl.sv = 0.9 * pl.sv + 0.1 * ((sv[1] + sv[2]) / sv[0])
            if osv[0] and osv[1] is not None and osv[2] is not None and osv[0] > 0:
                pl.rt = 0.9 * pl.rt + 0.1 * (1 - (osv[1] + osv[2]) / osv[0])
            pl.recent.append((day, m["minutes"]))
            pl.last = day
    return out


# ── regressione logistica (numpy, senza librerie esterne) ────────────────────────
def fit(X, y, l2=1.0, iters=50):
    import numpy as np
    X = np.column_stack([np.ones(len(X)), np.asarray(X, float)])
    y = np.asarray(y, float)
    w = np.zeros(X.shape[1])
    for _ in range(iters):                                  # Newton-Raphson con un po' di regolarizzazione
        p = 1 / (1 + np.exp(-X @ w))
        g = X.T @ (p - y) + l2 * np.r_[0, w[1:]]
        H = (X * (p * (1 - p))[:, None]).T @ X + l2 * np.diag(np.r_[0, np.ones(len(w) - 1)])
        w -= np.linalg.solve(H, g)
    return w


def predict(w, X):
    import numpy as np
    X = np.column_stack([np.ones(len(X)), np.asarray(X, float)])
    return 1 / (1 + np.exp(-X @ w))


def logloss(p, y):
    return -sum(math.log(max(min(pi if yi else 1 - pi, 1 - 1e-9), 1e-9)) for pi, yi in zip(p, y)) / max(1, len(y))


def walk_forward(rows: list[dict], first_test: int = 2019) -> dict:
    """Allena su tutti gli anni precedenti, prova sull'anno dopo. Restituisce le probabilità fuori campione."""
    years = sorted({r["date"].year for r in rows})
    for y in years:
        if y < first_test:
            continue
        train = [r for r in rows if r["date"].year < y]
        test = [r for r in rows if r["date"].year == y]
        if len(train) < 2000 or not test:
            continue
        w = fit([r["x"] for r in train], [r["y"] for r in train])
        for r, p in zip(test, predict(w, [r["x"] for r in test])):
            r["p_model"] = float(p)
    done = [r for r in rows if "p_model" in r]
    return {"n": len(done), "logloss_model": logloss([r["p_model"] for r in done], [r["y"] for r in done]),
            "logloss_elo": logloss([r["p_elo"] for r in done], [r["y"] for r in done]),
            "accuracy": sum((r["p_model"] > 0.5) == bool(r["y"]) for r in done) / max(1, len(done))}


# ── abbinamento con le quote di tennis-data ───────────────────────────────────────
def _key(name: str) -> str:
    """'Jannik Sinner' e 'Sinner J.' → 'sinner j'; 'Alex de Minaur' e 'De Minaur A.' → 'de minaur a'."""
    n = re.sub(r"[^a-z\s.-]", "", name.lower().replace("-", " ")).strip()
    if re.search(r"\s[a-z]\.?$", n) or re.search(r"\s[a-z]\.\s?[a-z]\.?$", n):     # formato tennis-data: Cognome I.
        parts = n.replace(".", " ").split()
        return " ".join(parts[:-1] if len(parts[-1]) == 1 else parts) + " " + parts[-1][0]
    parts = n.split()
    if len(parts) < 2:
        return n
    return " ".join(parts[1:]) + " " + parts[0][0]


def attach_odds(rows: list[dict], odds: list[dict]) -> int:
    """odds: allenamento.tennis_matches() (date, w, l, ps, bfe). Abbina per coppia di giocatori e data entro 16 giorni
    dall'inizio del torneo. Aggiunge p_mkt (probabilità giusta di A dal prezzo senza margine) e i prezzi."""
    idx = defaultdict(list)
    for o in odds:
        idx[frozenset((_key(o["w"]), _key(o["l"])))].append(o)
    n = 0
    for r in rows:
        cands = idx.get(frozenset((_key(r["a"]), _key(r["b"]))))
        if not cands:
            continue
        o = next((c for c in cands if timedelta(0) <= c["date"] - r["date"] <= timedelta(days=16)), None)
        if not o:
            continue
        a_won_book = _key(o["w"]) == _key(r["a"])
        pw, pl = o["ps"]
        qa, qb = (pw, pl) if a_won_book else (pl, pw)
        r["q_a"], r["q_b"] = qa, qb
        r["p_mkt"] = (1 / qa) / (1 / qa + 1 / qb)
        if o.get("bfe"):
            bw, bl = o["bfe"]
            r["bf_a"], r["bf_b"] = (bw, bl) if a_won_book else (bl, bw)
        n += 1
    return n


def evaluate_vs_market(rows: list[dict], edges=(0.02, 0.05, 0.08), commission=0.045) -> dict:
    """Confronto onesto con il mercato e 'occasioni' (modello sopra il prezzo) puntate al prezzo Betfair se c'è,
    altrimenti al prezzo di riferimento meno 2% (il prezzo del file è di un bookmaker, non ottenibile tale e quale)."""
    rs = [r for r in rows if "p_model" in r and "p_mkt" in r]
    if not rs:
        return {"n": 0}
    y = [r["y"] for r in rs]
    ll_mkt = logloss([r["p_mkt"] for r in rs], y)
    ll_mod = logloss([r["p_model"] for r in rs], y)
    # combinazione: quanto peso merita il modello accanto al mercato? (stimata sulla prima metà, provata sulla seconda)
    half = len(rs) // 2
    best_a, best_ll = 0.0, None
    for a in [i / 20 for i in range(0, 11)]:
        ll = logloss([(1 - a) * r["p_mkt"] + a * r["p_model"] for r in rs[:half]], y[:half])
        if best_ll is None or ll < best_ll:
            best_a, best_ll = a, ll
    ll_mix2 = logloss([(1 - best_a) * r["p_mkt"] + best_a * r["p_model"] for r in rs[half:]], y[half:])
    ll_mkt2 = logloss([r["p_mkt"] for r in rs[half:]], y[half:])
    bets = {}
    for e in edges:
        res = []
        for r in rs:
            for side, p, q, bf in (("A", r["p_model"], r["q_a"], r.get("bf_a")), ("B", 1 - r["p_model"], r["q_b"], r.get("bf_b"))):
                price = bf if bf else q * 0.98
                if p * price - 1 < e:
                    continue
                won = (r["y"] == 1) == (side == "A")
                res.append((price - 1) * (1 - commission) if won else -1.0)
        n = len(res)
        if n:
            m = sum(res) / n
            sd = math.sqrt(sum((x - m) ** 2 for x in res) / max(1, n - 1))
            bets[e] = {"n": n, "roi": m, "se": sd / math.sqrt(n)}
        else:
            bets[e] = {"n": 0}
    return {"n": len(rs), "ll_market": ll_mkt, "ll_model": ll_mod, "mix_weight": best_a, "ll_mix_test": ll_mix2,
            "ll_market_test": ll_mkt2, "bets": bets}


def report(wf: dict, ev: dict | None, n_matches: int) -> str:
    L = [f"Tennis: statistiche dei giocatori contro il mercato ({n_matches} partite Sackmann, ATP e WTA).", "",
         f"Modello fuori campione (walk-forward 2019-oggi, {wf['n']} partite): log-loss {wf['logloss_model']:.4f} "
         f"(solo Elo {wf['logloss_elo']:.4f}), favorito azzeccato {wf['accuracy']:.1%}."]
    if not ev or not ev.get("n"):
        L += ["", "Quote non disponibili (tennis-data non raggiungibile da qui): il confronto col mercato va fatto dal PC."]
        return "\n".join(L)
    L += ["", f"Partite con le quote: {ev['n']}.",
          f"Log-loss mercato (senza margine) {ev['ll_market']:.4f} · modello {ev['ll_model']:.4f} "
          f"→ {'il modello è PEGGIO del mercato' if ev['ll_model'] > ev['ll_market'] else 'il modello è MEGLIO del mercato (da verificare)'}.",
          f"Combinazione (peso del modello {ev['mix_weight']:.0%}, provata su partite non usate per sceglierlo): "
          f"{ev['ll_mix_test']:.4f} contro mercato {ev['ll_market_test']:.4f} "
          f"({'nessun guadagno' if ev['ll_mix_test'] >= ev['ll_market_test'] - 0.0005 else 'guadagno da verificare in ombra'}).", "",
          "Occasioni (modello sopra il prezzo), puntate al prezzo Betfair se c'è, altrimenti al riferimento −2%, commissione 4,5%:"]
    for e, b in ev["bets"].items():
        L.append(f"  vantaggio ≥ {e:.0%}: " + ("nessuna" if not b.get("n") else
                 f"{b['n']} puntate · ROI {b['roi']:+.1%} ± {2 * b['se']:.1%}"))
    L += ["", "Se il ROI attraversa lo zero o la log-loss non batte il mercato, il modello NON ha un vantaggio: non si punta."]
    return "\n".join(L)


def run(out=print, odds=None) -> int:
    out("Scarico le partite di Jeff Sackmann (ATP e WTA, prima volta qualche minuto)…")
    paths = download(out=out)
    matches = load(paths)
    out(f"{len(matches)} partite caricate. Calcolo le statistiche di ogni giocatore prima di ogni partita…")
    rows = build(matches)
    wf = walk_forward(rows)
    ev = None
    if odds is None:
        try:
            from .allenamento import tennis_matches
            from .backtest_tennis import tennis_paths
            odds = tennis_matches(tennis_paths(list(range(2019, datetime.now().year + 1))))
        except Exception as exc:
            out(f"Quote tennis-data non disponibili: {exc}")
            odds = []
    if odds:
        n = attach_odds(rows, odds)
        out(f"Quote abbinate a {n} partite.")
        ev = evaluate_vs_market(rows)
    text = report(wf, ev, len(matches))
    out("\n" + text)
    try:
        p = RUNTIME_DIR / "reports" / "tennis_lab.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n", encoding="utf-8")
        out(f"\n(rapporto salvato in {p})")
    except OSError:
        pass
    return 0
