"""S08 — Basket: back della squadra avanti di 15+ punti all'inizio del quarto quarto (in-play).

Evidenza (ricerca del 29/09/2026 su dati NBA ed Eurolega): chi conduce di 15-17 punti a fine terzo quarto
vince circa il 96,5% delle volte, di 18-20 il 98,5%, di oltre 20 il 99,5%; se è anche il favorito
pre-partita la percentuale sale ancora. Probabilità altissima, ma il mercato lo sa: le quote tipiche sono
1,01-1,04 e una sola sconfitta cancella decine di vincite. Si punta SOLO se la quota Betfair, al netto
della commissione, paga più della probabilità empirica (tabella qui sotto) di almeno `min_edge`.

Resta in osservazione: serve il punteggio live (il feed simulato lo ha; con Betfair va aggiunta una fonte
di punteggi per il basket) e i prezzi reali di betfair.it registrati per qualche settimana.
"""
from __future__ import annotations

from .s05_favoriti_exchange_v1 import ev_net

STRATEGY_ID = "S08_basket_quarto_quarto_v1"
NAME = "Basket: +15 nel quarto quarto"
KIND = "live"

# vantaggio minimo → probabilità empirica di vittoria (NBA ed Eurolega, fine terzo quarto)
TABLE = [(20, 0.995), (18, 0.985), (15, 0.965)]
DEFAULTS = {"min_lead": 15, "min_elapsed": 0.75, "max_elapsed": 0.85, "min_edge": 0.005, "commission": 0.045,
            "odds_max": 1.10}


def empirical_prob(lead: int) -> float | None:
    return next((p for lo, p in TABLE if lead >= lo), None)


def propose(snapshot: dict, params: dict, ctx: dict) -> list[dict]:
    q = {**DEFAULTS, **params}
    out = []
    for m in snapshot["matches"].values():
        if not (m.get("sport") or "").startswith("basketball") or m["status"] != "LIVE" or m.get("minute") is None:
            continue
        elapsed = m["minute"] / float(m.get("duration") or 48)
        if not (q["min_elapsed"] <= elapsed <= q["max_elapsed"]):
            continue
        lead = (m.get("home_score") or 0) - (m.get("away_score") or 0)
        sel = "home" if lead > 0 else "away"
        p = empirical_prob(abs(lead))
        price = ((m.get("exchange") or {}).get(sel) or {}).get("back")
        if abs(lead) < q["min_lead"] or not p or not price or price > q["odds_max"]:
            continue
        comm = m.get("commission") or q["commission"]
        edge = ev_net(p, price, comm)
        if edge < q["min_edge"]:
            continue
        out.append({"strategy_id": STRATEGY_ID, "match_id": m["match_id"],
                    "market_id": (m.get("betfair") or {}).get("market_id") or m["match_id"],
                    "league": m["league"], "sport": m.get("sport"), "home": m["home"], "away": m["away"],
                    "label": f"{m['home']} - {m['away']} · {m[sel]} (+{abs(lead)} nel 4° quarto)", "market": "h2h",
                    "selection": sel, "bookmaker": "Betfair", "odds": price, "fair_prob": p, "edge": edge,
                    "commission": comm, "n_books": 3, "dispersion": 0.0, "live": True, "odds_ts": m.get("odds_ts"),
                    "reason": f"{m[sel]} avanti di {abs(lead)} a {elapsed:.0%} della partita: storicamente vince il "
                              f"{p:.1%}; Betfair {price:.2f}, EV netto {edge:+.1%}"})
    return out
