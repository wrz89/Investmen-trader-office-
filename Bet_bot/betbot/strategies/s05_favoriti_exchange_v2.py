"""S05 v2 — Favoriti netti su Betfair Exchange (calcio, tennis, basket), parametri per sport.

Cosa cambia dalla v1 (ricerca del 29/09/2026 su tennis ATP/WTA, NBA e calcio):
  • EV minimo 2% al netto della commissione: nei casi in cui un prezzo batte il "giusto" il favorito vince
    0,5-1 punti meno del previsto (selezione avversa), quindi l'1% non basta;
  • tennis: esclusi Challenger, ITF, UTR e doppi (libri sottili e problemi di integrità);
  • esecuzione: spread ≤ 2 tick e almeno `min_book_eur` € al miglior prezzo; finestra da 2 ore a 15 minuti
    prima dell'inizio (nel tennis l'orario è indicativo: ordine fill-or-kill, niente ordini appesi);
  • parametri per sport: fascia di quota e probabilità minima.
Senza quote di riferimento esterne la strategia resta ferma: la quota Betfair da sola non ha un "giusto".
"""
from __future__ import annotations

import re
from datetime import datetime

from ..feeds.mock import tick_up
from ..odds import consensus
from .s05_favoriti_exchange_v1 import ev_net

STRATEGY_ID = "S05_favoriti_exchange_v2"
NAME = "Favoriti su exchange"
KIND = "prematch"

DEFAULTS = {
    "min_edge": 0.02, "commission": 0.045, "min_reference_books": 2, "min_book_eur": 3.0, "max_spread_ticks": 2,
    "max_minutes_before": 120, "min_minutes_before": 15,
    "sports": {
        "soccer": {"odds_min": 1.10, "odds_max": 1.40, "min_fair_prob": 0.75},
        "tennis": {"odds_min": 1.10, "odds_max": 1.35, "min_fair_prob": 0.75,
                   "exclude": "challenger|itf|utr|m15|m25|w15|w35|w50|w75|w100|doppio|doubles"},
        "basketball": {"odds_min": 1.10, "odds_max": 1.30, "min_fair_prob": 0.77},
    },
}


def _family(sport: str) -> str | None:
    s = sport or ""
    return "soccer" if s.startswith("soccer") else "tennis" if s.startswith("tennis") else \
        "basketball" if s.startswith("basketball") else None


def _spread_ticks(back: float, lay: float) -> int:
    n, p = 0, back
    while p < lay - 1e-9 and n < 10:
        p = tick_up(p)
        n += 1
    return n


def propose(snapshot: dict, params: dict, ctx: dict) -> list[dict]:
    q = {**DEFAULTS, **{k: v for k, v in params.items() if k != "sports"}}
    sports = {**DEFAULTS["sports"], **(params.get("sports") or {})}
    now = snapshot.get("sim_time") or snapshot["ts"]
    out = []
    for m in snapshot["matches"].values():
        fam = _family(m.get("sport"))
        sp = sports.get(fam) if fam else None
        ex = m.get("exchange") or {}
        if not sp or m["status"] != "SCHEDULED" or not ex or len(m.get("books") or {}) < q["min_reference_books"]:
            continue
        if sp.get("exclude") and re.search(sp["exclude"], (m.get("league") or "").lower()):
            continue
        mins = (datetime.fromisoformat(m["kickoff"]).timestamp() - now) / 60
        if not (q["min_minutes_before"] <= mins <= q["max_minutes_before"]):
            continue
        for sel, v in consensus(m["books"]).items():
            b = ex.get(sel) or {}
            if sel.startswith("_") or not b.get("back"):
                continue
            price = b["back"]
            if not (sp["odds_min"] <= price <= sp["odds_max"]) or v["fair_prob"] < sp["min_fair_prob"]:
                continue
            if b.get("lay") and _spread_ticks(price, b["lay"]) > q["max_spread_ticks"]:
                continue
            if (b.get("back_size_best") or b.get("back_size") or 0) < q["min_book_eur"]:
                continue
            comm = m.get("commission") or q["commission"]
            edge = ev_net(v["fair_prob"], price, comm)
            if edge < q["min_edge"]:
                continue
            name = m["home"] if sel == "home" else m["away"] if sel == "away" else "Pareggio"
            out.append({"strategy_id": STRATEGY_ID, "match_id": m["match_id"],
                        "market_id": (m.get("betfair") or {}).get("market_id") or m["match_id"],
                        "league": m["league"], "home": m["home"], "away": m["away"],
                        "label": f"{m['home']} - {m['away']} · {name}", "market": "h2h", "selection": sel,
                        "bookmaker": "Betfair", "odds": price, "fair_prob": v["fair_prob"], "edge": edge,
                        "commission": comm, "n_books": v["n_books"], "dispersion": v["dispersion"], "live": False,
                        "odds_ts": m.get("odds_ts"), "ref_ts": m.get("ref_ts"),
                        "reason": f"{name} ({fam}): probabilità giusta {v['fair_prob']:.0%} da {v['n_books']} book, "
                                  f"Betfair {price:.2f} con {(b.get('back_size_best') or b.get('back_size') or 0):.0f} € "
                                  f"disponibili, EV netto {edge:+.1%}, inizio tra {mins:.0f} min"})
    return out
