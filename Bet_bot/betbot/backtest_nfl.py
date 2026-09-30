"""NFL: c'è un vantaggio semplice nelle quote di chiusura? (testa a testa, handicap, totale punti)

Dati: nflverse (games.csv, gratuito): dal 2012 quote di chiusura dei bookmaker americani per testa a testa, handicap
(spread) e totale punti, con il risultato. Niente prezzi Betfair: si simula l'exchange con la quota GIUSTA (margine
tolto col metodo potenza) peggiorata dell'1% e la commissione di betfair.it sulla vincita. È un tetto ottimistico:
su betfair.it i libri NFL sono più sottili e si entra prima della chiusura.

Le prove sono decise prima di guardare i risultati (elenco PROVE) e sono tante: con ~30 prove, 1-2 "significative"
escono per caso. Conta la coerenza tra le due metà (2012-18 e 2019-25), non il singolo numero.
"""
from __future__ import annotations

import numpy as np

from .backtest import HISTORY_DIR

URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
COMM, SLIP = 0.045, 0.01


def load(first: int = 2012, last: int | None = None):
    import time

    import pandas as pd
    import requests
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path = HISTORY_DIR / "nfl_games.csv"
    if not path.exists() or time.time() - path.stat().st_mtime > 7 * 86400:
        r = requests.get(URL, timeout=60)
        r.raise_for_status()
        path.write_bytes(r.content)
    d = pd.read_csv(path)
    last = last or int(d.season.max()) - 1                    # l'ultima stagione è in corso
    need = ["home_moneyline", "away_moneyline", "spread_line", "total_line", "home_spread_odds", "away_spread_odds",
            "under_odds", "over_odds", "result", "total"]
    return d[(d.season >= first) & (d.season <= last)].dropna(subset=need).reset_index(drop=True)


def _dec(us):
    us = np.asarray(us, dtype=float)
    return np.where(us > 0, 1 + us / 100, 1 + 100 / np.abs(us))


def _power(o1, o2):
    """Probabilità giusta del primo esito di un mercato a due esiti (metodo potenza), vettoriale."""
    lo, hi = np.full(len(o1), 0.5), np.full(len(o1), 2.0)
    for _ in range(60):
        k = (lo + hi) / 2
        over = (1 / o1) ** k + (1 / o2) ** k > 1
        lo, hi = np.where(over, k, lo), np.where(over, hi, k)
    return (1 / o1) ** k


def _pnl(p_fair, won, push=None):
    o = (1 / p_fair) * (1 - SLIP)
    r = np.where(won, (o - 1) * (1 - COMM), -1.0)
    return np.where(push, 0.0, r) if push is not None else r


def run(first: int = 2012, last: int | None = None) -> dict:
    d = load(first, last)
    ph = _power(_dec(d.home_moneyline), _dec(d.away_moneyline))
    psh = _power(_dec(d.home_spread_odds), _dec(d.away_spread_odds))
    pov = _power(_dec(d.over_odds), _dec(d.under_odds))
    res, sp, tot, tl = d.result.values, d.spread_line.values, d.total.values, d.total_line.values
    tie = res == 0                                              # dead heat: metà vince, metà perde
    ml_h = np.where(tie, (1 / ph * (1 - SLIP)) / 2 - 1, _pnl(ph, res > 0))
    ml_a = np.where(tie, (1 / (1 - ph) * (1 - SLIP)) / 2 - 1, _pnl(1 - ph, res < 0))
    sp_h, sp_a = _pnl(psh, res > sp, res == sp), _pnl(1 - psh, res < sp, res == sp)
    ov, un = _pnl(pov, tot > tl, tot == tl), _pnl(1 - pov, tot < tl, tot == tl)
    fav_h, div = ph >= 0.5, d.div_game.values == 1
    rest_h, rest_a = d.home_rest.values, d.away_rest.values
    wind = d.wind.fillna(0).values
    dome = d.roof.isin(["dome", "closed"]).values
    prime = d.gametime.astype(str).str[:2].isin(["19", "20", "21"]).values
    po = (d.game_type != "REG").values
    fav_sp, dog_sp = np.where(sp > 0, sp_h, sp_a), np.where(sp > 0, sp_a, sp_h)
    PROVE = [
        ("Testa a testa: casa favorita", ml_h, fav_h), ("Testa a testa: ospite favorita", ml_a, ~fav_h),
        ("Testa a testa: casa sfavorita", ml_h, ~fav_h), ("Testa a testa: ospite sfavorita", ml_a, fav_h),
        ("Testa a testa: favorita netta (>80%) in casa", ml_h, ph > 0.8),
        ("Testa a testa: sfavorita netta (<25%) in casa", ml_h, ph < 0.25),
        ("Testa a testa: sfavorita netta (<25%) ospite", ml_a, ph > 0.75),
        ("Testa a testa: sfavorita in derby di division", np.where(fav_h, ml_a, ml_h), div),
        ("Testa a testa: casa dopo la settimana di riposo", ml_h, (rest_h >= 13) & (rest_a < 13)),
        ("Testa a testa: ospite dopo la settimana di riposo", ml_a, (rest_a >= 13) & (rest_h < 13)),
        ("Testa a testa: sfavorita nei playoff", np.where(fav_h, ml_a, ml_h), po),
        ("Handicap: casa", sp_h, sp == sp), ("Handicap: ospite", sp_a, sp == sp),
        ("Handicap: ospite sfavorita", sp_a, sp > 0), ("Handicap: casa sfavorita", sp_h, sp < 0),
        ("Handicap: favorita di 7+ punti", fav_sp, np.abs(sp) >= 7), ("Handicap: sfavorita di 7+ punti", dog_sp, np.abs(sp) >= 7),
        ("Handicap: sfavorita in derby di division", dog_sp, div & (sp != 0)),
        ("Totale: over", ov, tl == tl), ("Totale: under", un, tl == tl),
        ("Totale: under con vento ≥ 15 mph", un, wind >= 15), ("Totale: under con linea ≥ 50", un, tl >= 50),
        ("Totale: over con linea ≤ 40", ov, tl <= 40), ("Totale: under in derby di division", un, div),
        ("Totale: over al coperto", ov, dome), ("Totale: under in prima serata", un, prime), ("Totale: under nei playoff", un, po),
    ]
    seasons = d.season.values
    rows = []
    for name, r, mask in PROVE:
        x, s = r[mask], seasons[mask]
        n = len(x)
        m = float(x.mean()) if n else 0.0
        se = float(x.std(ddof=1) / np.sqrt(n)) if n > 1 else float("nan")
        rows.append({"prova": name, "n": n, "roi": m, "se": se,
                     "prima": float(x[s <= 2018].mean()) if (s <= 2018).any() else None,
                     "seconda": float(x[s >= 2019].mean()) if (s >= 2019).any() else None})
    return {"partite": len(d), "stagioni": (int(d.season.min()), int(d.season.max())), "prove": rows}


def report(res: dict) -> str:
    pct = lambda v: "—" if v is None else f"{v:+.1%}"
    a, b = res["stagioni"]
    L = [f"# NFL {a}-{b}: {res['partite']} partite, {len(res['prove'])} prove", "",
         "Exchange simulato: quota giusta −1%, commissione 4,5% sulla vincita. ROI per puntata, intervallo ±2 errori standard.",
         "", "| Prova | Puntate | ROI | ±2σ | 2012-18 | 2019-25 |", "|---|---|---|---|---|---|"]
    for r in res["prove"]:
        flag = " ⚑" if r["roi"] - 2 * r["se"] > 0 else ""
        L.append(f"| {r['prova']}{flag} | {r['n']} | {pct(r['roi'])} | {2 * r['se']:.1%} | {pct(r['prima'])} | {pct(r['seconda'])} |")
    good = [r for r in res["prove"] if r["roi"] - 2 * r["se"] > 0]
    L += ["", "Verdetto: " + ("nessuna prova batte il mercato in modo solido." if not good else
                              f"{len(good)} prove sopra lo zero: con {len(res['prove'])} prove possono essere caso, "
                              "vanno confermate sui prezzi veri di betfair.it (test rapido, poi esame).")]
    return "\n".join(L) + "\n"
