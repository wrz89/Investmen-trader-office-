"""Backtest del metodo "alla Mercurius": quote giuste da un modello proprio, confronto col prezzo Betfair.

Mercurius (Mercurius BI srl, Milano, 2017-2021) non ha mai pubblicato il suo algoritmo; dai webinar dei
fondatori si conosce solo l'architettura: probabilità proprie da un modello dei gol, confronto con l'exchange,
piano di puntata, esecuzione automatica. Qui si ricostruisce con strumenti pubblici e si VERIFICA:

1. modello Dixon-Coles con decadimento nel tempo, stimato ogni giornata SOLO sulle partite precedenti;
2. probabilità di mercato = consenso dei bookmaker senza margine (metodo potenza);
3. probabilità finale = w × modello + (1 − w) × mercato, con w scelto sulla stagione 2024/25 (addestramento)
   minimizzando la log-loss; se il w migliore è 0, il modello non aggiunge niente al mercato;
4. test FUORI CAMPIONE sulle stagioni successive: si punta al prezzo Betfair Exchange (BFE) solo se
   l'EV al netto della commissione è ≥ min_edge; si misurano ROI e CLV contro la chiusura Betfair (BFEC).
"""
from __future__ import annotations

import math
from collections import defaultdict

import pandas as pd

from .backtest import EXCHANGE_DIVS, HISTORY_DIR, download, exchange_seasons
from .models.dixon_coles import blend, fit, outcome_probs
from .odds import consensus

FD_BOOKS = {"B365": "Bet365", "BW": "Bet&Win", "IW": "Interwetten", "PS": "Pinnacle", "WH": "William Hill",
            "VC": "VC Bet", "1XB": "1xBet", "BFD": "Betfred"}


def _num(x):
    try:
        v = float(x)
        return v if v > 1.0 and not math.isnan(v) else None
    except (TypeError, ValueError):
        return None


def _triple(r, pre):
    h, d, a = (_num(r.get(f"{pre}{s}")) for s in "HDA")
    return {"home": h, "draw": d, "away": a} if h and d and a else None


def load_league_history(divs: list[str], seasons: list[str]) -> dict[str, list[dict]]:
    """Tutte le partite (anche senza quote Betfair), per campionato, in ordine di data."""
    paths = download(divs, seasons)
    out: dict[str, list[dict]] = defaultdict(list)
    for p in paths:
        div = p.stem.split("_", 1)[1]
        try:
            df = pd.read_csv(p, encoding="latin-1", on_bad_lines="skip")
        except Exception:
            continue
        df.columns = [str(c).strip().lstrip("﻿").lstrip("ï»¿") for c in df.columns]
        for _, r in df.iterrows():
            if pd.isna(r.get("FTHG")) or pd.isna(r.get("HomeTeam")):
                continue
            d = pd.to_datetime(r["Date"], dayfirst=True, errors="coerce")
            if pd.isna(d):
                continue
            out[div].append({"ts": d.timestamp(), "date": d.date(), "season": p.stem.split("_")[0],
                             "home": r["HomeTeam"], "away": r["AwayTeam"], "hg": int(r["FTHG"]), "ag": int(r["FTAG"]),
                             "result": {"H": "home", "D": "draw", "A": "away"}[r["FTR"]],
                             "books": {n: t for pre, n in FD_BOOKS.items() if (t := _triple(r, pre))},
                             "bfe": _triple(r, "BFE"), "bfec": _triple(r, "BFEC")})
    for div in out:
        out[div].sort(key=lambda m: m["ts"])
    return out


def _predictions(history: dict[str, list[dict]], seasons_eval: set[str]) -> list[dict]:
    """Per ogni partita delle stagioni da valutare: probabilità del modello (stimato solo sul passato) e del mercato."""
    preds = []
    for div, matches in history.items():
        rows = [(m["ts"], m["home"], m["away"], m["hg"], m["ag"]) for m in matches]
        cache: dict = {}
        for m in matches:
            if m["season"] not in seasons_eval or not m["books"]:
                continue
            day = m["date"]
            if day not in cache:
                cache[day] = fit(rows, m["ts"] - 3600)            # solo partite giocate PRIMA di questa giornata
            r = cache[day]
            pm = outcome_probs(r, m["home"], m["away"]) if r else None
            c = consensus(m["books"])
            pk = {s: v["fair_prob"] for s, v in c.items() if not s.startswith("_")}
            if not pm or len(pk) != 3:
                continue
            preds.append({**m, "div": div, "p_model": pm, "p_market": pk})
    return preds


def _logloss(preds: list[dict], w: float) -> float:
    ll = 0.0
    for p in preds:
        pf = blend(p["p_model"], p["p_market"], w)
        ll -= math.log(max(pf[p["result"]], 1e-9))
    return ll / max(1, len(preds))


def run(divs: list[str] | None = None, min_edge: float = 0.02, min_prob: float = 0.0, commission: float = 0.045,
        odds_max: float = 4.0) -> dict:
    divs = divs or EXCHANGE_DIVS
    ex_seasons = exchange_seasons()                              # stagioni con prezzi Betfair: 2024/25 → oggi
    first = int(ex_seasons[0][:2]) + 2000
    fit_seasons = [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in range(first - 2, first)]   # 2 stagioni di rodaggio
    history = load_league_history(divs, fit_seasons + ex_seasons)
    train_s, test_s = {ex_seasons[0]}, set(ex_seasons[1:])
    preds = _predictions(history, train_s | test_s)
    train = [p for p in preds if p["season"] in train_s]
    test = [p for p in preds if p["season"] in test_s]
    grid = [i / 10 for i in range(11)]
    lls = {w: _logloss(train, w) for w in grid}
    w_best = min(lls, key=lls.get)
    out = {"train_matches": len(train), "test_matches": len(test), "w": w_best,
           "logloss_train": {"mercato (w=0)": lls[0.0], "modello (w=1)": lls[1.0], f"miscela w={w_best}": lls[w_best]},
           "logloss_test": {"mercato": _logloss(test, 0.0), "modello": _logloss(test, 1.0), "miscela": _logloss(test, w_best)}}
    bets = []
    for p in test:
        if not p["bfe"]:
            continue
        pf = blend(p["p_model"], p["p_market"], w_best)
        for sel in ("home", "draw", "away"):
            price = p["bfe"][sel]
            if not price or price > odds_max or pf[sel] < min_prob:
                continue
            ev = pf[sel] * (price - 1) * (1 - commission) - (1 - pf[sel])
            if ev < min_edge:
                continue
            won = p["result"] == sel
            pnl = (price - 1) * (1 - commission) if won else -1.0
            clv = price / p["bfec"][sel] - 1 if p.get("bfec") and p["bfec"].get(sel) else None
            bets.append({"date": p["date"], "div": p["div"], "match": f"{p['home']} - {p['away']}", "selection": sel,
                         "odds": price, "p": round(pf[sel], 4), "ev": round(ev, 4), "won": won, "pnl": pnl, "clv": clv})
    b = pd.DataFrame(bets)
    if len(b):
        clv = b["clv"].dropna()
        out.update(bets=len(b), win_rate=float(b["won"].mean()), avg_odds=float(b["odds"].mean()),
                   roi=float(b["pnl"].mean()), roi_se=float(b["pnl"].std() / math.sqrt(len(b))),
                   clv_avg=float(clv.mean()) if len(clv) else None,
                   clv_positive=float((clv > 0).mean()) if len(clv) else None)
    else:
        out.update(bets=0)
    out["bets_table"] = b
    return out


def report(res: dict, title: str) -> str:
    lines = [f"## {title}", "",
             f"Partite: {res['train_matches']} per scegliere il peso (2024/25), {res['test_matches']} di prova fuori campione.",
             f"Peso del modello scelto: **w = {res['w']:.1f}** (0 = solo mercato, 1 = solo modello).", "",
             "| Log-loss (più bassa = meglio) | Mercato | Modello | Miscela |", "|---|---|---|---|",
             f"| Prova fuori campione | {res['logloss_test']['mercato']:.4f} | {res['logloss_test']['modello']:.4f} | "
             f"{res['logloss_test']['miscela']:.4f} |", ""]
    if res.get("bets"):
        clv = "—" if res.get("clv_avg") is None else f"{res['clv_avg']:+.2%} ({res['clv_positive']:.0%} positivi)"
        lines += ["| Puntate | Vinte | Quota media | ROI a 1 € | Errore standard | CLV vs chiusura Betfair |",
                  "|---|---|---|---|---|---|",
                  f"| {res['bets']} | {res['win_rate']:.1%} | {res['avg_odds']:.2f} | {res['roi']:+.2%} | "
                  f"±{res['roi_se']:.2%} | {clv} |"]
    else:
        lines += ["Nessuna puntata: con il peso scelto il modello non trova mai valore netto sopra la soglia."]
    return "\n".join(lines) + "\n"
