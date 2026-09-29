"""S05 — Favoriti pre-partita su Betfair Exchange, con probabilità "giusta" da un bookmaker sharp.

La quota si PRENDE su Betfair (unico posto dove il bot può puntare da solo); la probabilità si STIMA
dal consenso dei bookmaker di riferimento (Pinnacle pesa triplo) senza margine, col metodo potenza.
Valore atteso AL NETTO della commissione exchange:

    EV = p × (quota − 1) × (1 − commissione) − (1 − p)

Si punta solo se EV ≥ `min_edge`, con prezzo nella fascia, probabilità ≥ `min_fair_prob`, partita
nella finestra oraria e abbastanza denaro sul miglior prezzo (lo controlla l'exchange).
Se non ci sono quote di riferimento (niente chiave The Odds API) la strategia resta ferma: senza un
metro esterno la quota Betfair non ha un "giusto" con cui confrontarsi.
"""
from __future__ import annotations

from datetime import datetime

from ..odds import consensus

STRATEGY_ID = "S05_favoriti_exchange_v1"
NAME = "Favoriti su exchange"
KIND = "prematch"

DEFAULTS = {"odds_min": 1.10, "odds_max": 1.40, "min_fair_prob": 0.75, "min_edge": 0.01, "commission": 0.045,
            "hours_before_kickoff": 36, "min_hours_before_kickoff": 0.25, "min_reference_books": 2}


def ev_net(p: float, price: float, commission: float) -> float:
    return p * (price - 1.0) * (1.0 - commission) - (1.0 - p)


def propose(snapshot: dict, params: dict, ctx: dict) -> list[dict]:
    q = {**DEFAULTS, **params}
    out = []
    now = snapshot.get("sim_time") or snapshot["ts"]
    for m in snapshot["matches"].values():
        ex = m.get("exchange") or {}
        if m["status"] != "SCHEDULED" or not ex or len(m.get("books") or {}) < q["min_reference_books"]:
            continue
        hours = (datetime.fromisoformat(m["kickoff"]).timestamp() - now) / 3600
        if not (q["min_hours_before_kickoff"] <= hours <= q["hours_before_kickoff"]):
            continue
        ref = consensus(m["books"])
        for sel, v in ref.items():
            if sel.startswith("_") or sel not in ex or not ex[sel].get("back"):
                continue
            price = ex[sel]["back"]
            if not (q["odds_min"] <= price <= q["odds_max"]) or v["fair_prob"] < q["min_fair_prob"]:
                continue
            comm = m.get("commission") or q["commission"]            # tasso reale del mercato, se il feed lo dà
            edge = ev_net(v["fair_prob"], price, comm)
            if edge < q["min_edge"]:
                continue
            name = m["home"] if sel == "home" else m["away"] if sel == "away" else "Pareggio"
            out.append({"strategy_id": STRATEGY_ID, "match_id": m["match_id"],
                        "market_id": (m.get("betfair") or {}).get("market_id") or m["match_id"],
                        "league": m["league"], "sport": m.get("sport"), "home": m["home"], "away": m["away"],
                        "label": f"{m['home']} - {m['away']} · {name}", "market": "h2h", "selection": sel,
                        "bookmaker": "Betfair", "odds": price, "fair_prob": v["fair_prob"], "edge": edge,
                        "commission": comm, "n_books": v["n_books"], "dispersion": v["dispersion"],
                        "live": False, "odds_ts": m.get("odds_ts"), "ref_ts": m.get("ref_ts"),
                        "reason": f"{name}: probabilità giusta {v['fair_prob']:.0%} (riferimento {v['n_books']} book), "
                                  f"Betfair {price:.2f} ({ex[sel].get('back_size') or 0:.0f} € disponibili), EV netto "
                                  f"commissione {edge:+.1%}, tra {hours:.1f} h"})
    return out
