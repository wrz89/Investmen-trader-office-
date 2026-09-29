"""S03 — Sure bet (arbitraggio tra bookmaker) pre-partita.

Se, prendendo la quota migliore di ogni esito su bookmaker diversi, la somma
delle probabilità implicite è < 1, si copre ogni esito e il profitto è garantito
(al netto di arrotondamenti). Nella realtà dura pochi minuti e i bookmaker
limitano i conti: qui serve a misurare quanto spesso capita.
"""
from __future__ import annotations

from ..odds import surebet

STRATEGY_ID = "S03_surebet_v1"
NAME = "Sure bet tra bookmaker"
KIND = "arb"


def propose(snapshot: dict, params: dict, ctx: dict) -> list[dict]:
    out = []
    for m in snapshot["matches"].values():
        if m["status"] != "SCHEDULED" or len(m.get("books") or {}) < 2:
            continue
        best: dict[str, tuple[float, str]] = {}
        outcomes = {sel for prices in m["books"].values() for sel in prices}
        for book, prices in m["books"].items():
            if set(prices) != outcomes:
                continue                               # bookmaker con mercato incompleto: escluso
            for sel, p in prices.items():
                if sel not in best or p > best[sel][0]:
                    best[sel] = (p, book)
        # un arbitraggio deve coprire TUTTI gli esiti; quote troppo alte = puntate su esiti improbabili, scarto la partita
        if set(best) != outcomes or len(best) < 2 or max(o for o, _ in best.values()) > params["max_odds"]:
            continue
        arb = surebet(best)
        if not arb or arb["margin"] < params["min_margin"] or arb["margin"] > params.get("max_margin", 0.03):
            continue
        legs = [{"selection": sel, "bookmaker": best[sel][1], "odds": best[sel][0], "weight": w}
                for sel, w in arb["weights"].items()]
        out.append({"strategy_id": STRATEGY_ID, "match_id": m["match_id"], "league": m["league"],
                    "label": f"{m['home']} - {m['away']} · sure bet {arb['margin']:.2%}", "market": "h2h",
                    "selection": "+".join(arb["weights"]), "bookmaker": "+".join(sorted(set(l["bookmaker"] for l in legs))),
                    "odds": 1 + arb["margin"], "fair_prob": 1.0, "edge": arb["margin"], "n_books": len(m["books"]),
                    "dispersion": 0.0, "live": False, "legs": legs, "odds_ts": m.get("odds_ts"),
                    "reason": "sure bet: " + ", ".join(f"{l['selection']} {l['odds']:.2f}@{l['bookmaker']}" for l in legs)
                              + f" → profitto garantito {arb['margin']:.2%}"})
    return out
