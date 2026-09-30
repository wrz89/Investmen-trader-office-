"""Matematica delle quote: probabilità implicite, rimozione del margine,
consenso tra bookmaker, valore atteso e Kelly.

Regola che vale per tutto l'ufficio: una quota bassa NON è una scommessa
sicura. A quota 1,25 il break-even è l'80% di vittorie; il profitto nasce
solo quando la quota presa è più alta di quanto "meriterebbe" la probabilità
reale (edge > 0), e si misura confrontandola con la quota di chiusura (CLV).
"""
from __future__ import annotations

import math
from statistics import mean, pstdev


def implied(price: float) -> float:
    return 1.0 / price if price and price > 1.0 else 0.0


# Bookmaker "sharp" (margini bassi, limiti alti): nel consenso pesano di più.
SHARP_WEIGHTS = {"Pinnacle": 3.0, "Betfair": 2.0, "Betfair Exchange": 2.0, "Exchange": 2.0}


def remove_margin(prices: dict[str, float], method: str = "power") -> dict[str, float]:
    """Probabilità "giuste" di un mercato (es. {"home":1.5,"draw":4.2,"away":6.5}).

    power (predefinito): p_i = (1/quota_i)^(1/k) con k tale che la somma sia 1. Toglie più margine
    dalle quote alte e meno dai favoriti, come fanno davvero i bookmaker (favourite-longshot bias).
    proportional: divide tutte le implicite per la loro somma (sottostima i favoriti)."""
    raw = {k: implied(p) for k, p in prices.items()}
    total = sum(raw.values())
    if total <= 0:
        return {k: 0.0 for k in prices}
    if method == "proportional" or total <= 1.0:
        return {k: v / total for k, v in raw.items()}
    lo, hi = 1.0, 3.0                          # esponente e = 1/k ≥ 1
    for _ in range(50):
        e = (lo + hi) / 2
        if sum(v ** e for v in raw.values()) > 1.0:
            lo = e
        else:
            hi = e
    fair = {k: v ** e for k, v in raw.items()}
    s = sum(fair.values())
    return {k: v / s for k, v in fair.items()}


def consensus(books: dict[str, dict[str, float]]) -> dict:
    """books = {bookmaker: {selection: price}}. Restituisce, per ogni selezione:
    fair_prob (media pesata delle probabilità senza margine; i bookmaker sharp pesano di più), best_odds, best_book,
    dispersion (deviazione std delle probabilità tra bookmaker), n_books, avg_margin."""
    fair: dict[str, list[tuple[float, float]]] = {}
    best: dict[str, tuple[float, str]] = {}
    margins = []
    valid = {b: p for b, p in books.items() if len(p) >= 2 and all((x or 0) > 1.0 for x in p.values())}
    # solo i bookmaker che quotano TUTTI gli esiti del mercato: un 1X2 senza la X, normalizzato su 2 esiti,
    # gonfierebbe la probabilità del favorito (falso valore)
    full = max((len(p) for p in valid.values()), default=0)
    for book, prices in valid.items():
        if len(prices) < full:
            continue
        margins.append(sum(implied(p) for p in prices.values()) - 1.0)
        w = SHARP_WEIGHTS.get(book, 1.0)
        for sel, prob in remove_margin(prices).items():
            fair.setdefault(sel, []).append((prob, w))
            if sel not in best or prices[sel] > best[sel][0]:
                best[sel] = (prices[sel], book)
    out = {}
    for sel, pw in fair.items():
        probs = [x for x, _ in pw]
        p = sum(x * w for x, w in pw) / sum(w for _, w in pw)
        out[sel] = {"fair_prob": p, "best_odds": best[sel][0], "best_book": best[sel][1],
                    "dispersion": pstdev(probs) if len(probs) > 1 else 0.0, "n_books": len(probs),
                    "edge": p * best[sel][0] - 1.0, "fair_odds": 1.0 / p if p > 0 else math.inf}
    if out:
        out["_avg_margin"] = mean(margins) if margins else 0.0
    return out


def expected_value(prob: float, price: float) -> float:
    """Valore atteso per unità puntata: p·(quota−1) − (1−p) = p·quota − 1."""
    return prob * price - 1.0


def kelly(prob: float, price: float) -> float:
    """Frazione di Kelly piena: (p·b − q)/b con b = quota − 1. 0 se non c'è vantaggio."""
    b = price - 1.0
    if b <= 0 or prob <= 0:
        return 0.0
    return max(0.0, (prob * b - (1.0 - prob)) / b)


def breakeven_win_rate(price: float) -> float:
    return implied(price)


def surebet(best: dict[str, tuple[float, str]]) -> dict | None:
    """best = {selection: (best_odds, book)}. Se la somma delle probabilità implicite
    è < 1 c'è un arbitraggio: restituisce quote di puntata per selezione e margine."""
    total = sum(implied(o) for o, _ in best.values())
    if total <= 0 or total >= 1.0:
        return None
    weights = {sel: implied(o) / total for sel, (o, _) in best.items()}
    return {"margin": 1.0 / total - 1.0, "weights": weights, "books": {s: b for s, (_, b) in best.items()}}

def exchange_prices_sane(back: list | None, ref: list | None, max_dev: float = 0.25) -> bool:
    """Prezzi Betfair del file credibili? Scarta i record rotti (colonne scambiate, prezzi fermi o inventati):
    somma delle probabilità implicite tra 0,97 e 1,08 e nessuna quota lontana più del 25% da quella di Pinnacle.
    Senza questo filtro pochi prezzi assurdi (un outsider "a 2,0" che vale 8) gonfiano qualunque backtest di lay."""
    if not back or not ref or len(back) != len(ref):
        return False
    s = sum(1 / q for q in back)
    if not 0.97 <= s <= 1.08:
        return False
    return all(abs(b / r - 1) <= max_dev for b, r in zip(back, ref))
