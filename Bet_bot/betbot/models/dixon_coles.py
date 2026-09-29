"""Modello dei gol di Dixon e Coles (1997) con decadimento nel tempo — il "primo core" di un sistema alla Mercurius.

Ogni squadra ha una forza d'attacco e una di difesa; in casa c'è un vantaggio. I gol attesi sono
    λ_casa = vantaggio_casa × attacco_casa × difesa_ospite        μ_ospite = attacco_ospite × difesa_casa
e il punteggio segue due Poisson, con la correzione di Dixon-Coles (ρ) sui risultati bassi (0-0, 1-0, 0-1, 1-1).
Le partite vecchie contano meno: peso = exp(−ξ × giorni), con emivita di circa 180 giorni.

Stima con il metodo iterativo di Maher (massima verosimiglianza Poisson a punto fisso): veloce, solo numpy,
nessuna libreria di ottimizzazione. Il ρ è fisso (−0,05, valore tipico della letteratura).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class Ratings:
    teams: dict[str, int]
    attack: np.ndarray
    defence: np.ndarray
    home: float
    rho: float
    games: dict[str, int]

    def lambdas(self, home: str, away: str) -> tuple[float, float] | None:
        if home not in self.teams or away not in self.teams:
            return None
        i, j = self.teams[home], self.teams[away]
        return self.home * self.attack[i] * self.defence[j], self.attack[j] * self.defence[i]


def fit(matches: list[tuple], ref_ts: float, half_life_days: float = 180.0, iters: int = 60,
        min_games: int = 6, rho: float = -0.05) -> Ratings | None:
    """matches: (timestamp, casa, ospite, gol_casa, gol_ospite), SOLO partite prima di ref_ts."""
    rows = [m for m in matches if m[0] < ref_ts]
    if len(rows) < 50:
        return None
    teams: dict[str, int] = {}
    for _, h, a, _, _ in rows:
        teams.setdefault(h, len(teams))
        teams.setdefault(a, len(teams))
    n = len(teams)
    hi = np.array([teams[m[1]] for m in rows])
    ai = np.array([teams[m[2]] for m in rows])
    hg = np.array([m[3] for m in rows], float)
    ag = np.array([m[4] for m in rows], float)
    xi = math.log(2) / (half_life_days * 86400)
    w = np.exp(-xi * (ref_ts - np.array([m[0] for m in rows], float)))
    att, dfn, home = np.ones(n), np.ones(n), 1.3
    for _ in range(iters):
        # attacco: gol fatti / gol attesi con attacco 1
        scored = np.bincount(hi, w * hg, n) + np.bincount(ai, w * ag, n)
        exp_s = np.bincount(hi, w * home * dfn[ai], n) + np.bincount(ai, w * dfn[hi], n)
        att = np.where(exp_s > 0, scored / np.maximum(exp_s, 1e-9), 1.0)
        att /= att.mean()
        conceded = np.bincount(ai, w * hg, n) + np.bincount(hi, w * ag, n)
        exp_c = np.bincount(ai, w * home * att[hi], n) + np.bincount(hi, w * att[ai], n)
        dfn = np.where(exp_c > 0, conceded / np.maximum(exp_c, 1e-9), 1.0)
        home = float((w * hg).sum() / max((w * att[hi] * dfn[ai]).sum(), 1e-9))
    games = {t: int(((hi == k) | (ai == k)).sum()) for t, k in teams.items()}
    keep = {t: k for t, k in teams.items() if games[t] >= min_games}
    return Ratings(keep, att, dfn, home, rho, games)


def _pois(k: int, lam: float) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


def outcome_probs(r: Ratings, home: str, away: str, max_goals: int = 10) -> dict[str, float] | None:
    lm = r.lambdas(home, away)
    if lm is None:
        return None
    lam, mu = lm
    ph = pd = pa = 0.0
    for x in range(max_goals + 1):
        px = _pois(x, lam)
        for y in range(max_goals + 1):
            p = px * _pois(y, mu)
            if x == 0 and y == 0:
                p *= 1 - lam * mu * r.rho
            elif x == 0 and y == 1:
                p *= 1 + lam * r.rho
            elif x == 1 and y == 0:
                p *= 1 + mu * r.rho
            elif x == 1 and y == 1:
                p *= 1 - r.rho
            if x > y:
                ph += p
            elif x == y:
                pd += p
            else:
                pa += p
    s = ph + pd + pa
    return {"home": ph / s, "draw": pd / s, "away": pa / s}


def blend(p_model: dict, p_market: dict, w: float) -> dict:
    """Probabilità finale = w × modello + (1 − w) × mercato. Con w scelto sui dati: se il w migliore è ~0,
    il modello non aggiunge informazione al mercato e va scartato."""
    out = {k: w * p_model[k] + (1 - w) * p_market[k] for k in p_market if k in p_model}
    s = sum(out.values())
    return {k: v / s for k, v in out.items()}
