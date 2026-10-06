"""Qualità dei dati di una puntata (0-100): quanto ci si può fidare del prezzo e del riferimento.

NON misura se la squadra è forte: misura l'errore che possiamo commettere noi. Cinque cose, tutte osservabili:
  • liquidità al prezzo (30 punti): con poco denaro il prezzo è fragile e la puntata si abbina male;
  • spread back-lay (20): libro stretto = prezzo affidabile;
  • riferimento esterno (25): Pinnacle/consenso fresco; solo il prezzo medio di Betfair vale poco, vecchio ancora meno;
  • campionato (15): quelli grandi hanno prezzi più precisi (Pinnacle li quota bene);
  • anticipo (10): vicino all'inizio il prezzo ha già incorporato formazioni e notizie.
Serve a due cose: un filtro (fun_min_quality in risk_limits.yaml) e un segmento di Leo ("qualita"), che dice dai dati se
le puntate di qualità alta hanno davvero un CLV migliore. Se non è così il filtro va tolto: un'idea non misurata.
"""
from __future__ import annotations

import math
from datetime import datetime


def score(p: dict, snapshot: dict | None = None) -> tuple[int, dict]:
    now = ((snapshot or {}).get("sim_time") or (snapshot or {}).get("ts")) or None
    scale = (snapshot or {}).get("time_scale") or 1.0
    parts = {}
    book = float(p.get("book_eur") or 0)
    parts["liquidità"] = 0 if book < 30 else round(min(30.0, 30.0 * math.log10(book / 30.0) / math.log10(500.0 / 30.0)), 1)
    spread = p.get("spread")
    parts["spread"] = 10.0 if spread is None else round(max(0.0, min(20.0, 20.0 * (1 - (float(spread) - 0.01) / 0.02))), 1)
    ref_age = (now - p["ref_ts"]) / 60 / scale if p.get("ref_ts") and now else None
    if p.get("prob_source") == "exchange" or ref_age is None:
        parts["riferimento"] = 5
    elif ref_age <= 40:
        parts["riferimento"] = 25
    elif ref_age <= 90:
        parts["riferimento"] = 15
    else:
        parts["riferimento"] = 8
    from .feeds import league_key
    league = (p.get("league") or "").lower()
    if league_key(p.get("league")):
        parts["campionato"] = 15
    elif (p.get("sport") or "").startswith("tennis") and not any(x in league for x in ("challenger", "itf", "utr")):
        parts["campionato"] = 10
    else:
        parts["campionato"] = 5
    mins = None
    try:
        mins = (datetime.fromisoformat(p["kickoff"]).timestamp() - now) / 60 if p.get("kickoff") and now else None
    except (TypeError, ValueError):
        pass
    parts["anticipo"] = 5 if mins is None else 10 if mins <= 120 else 5 if mins <= 240 else 0
    return int(round(sum(parts.values()))), parts
