"""S01 — Favoriti netti pre-partita (quota 1,15–1,30) con vantaggio sul consenso.

Filtro: probabilità "giusta" (media dei bookmaker senza margine) ≥ min_fair_prob,
quota migliore nella fascia, edge = fair_prob × quota − 1 ≥ min_edge, partita
entro la finestra oraria. La quota bassa da sola non basta: senza edge non si punta.
"""
from __future__ import annotations

from datetime import datetime

from ..odds import consensus

STRATEGY_ID = "S01_favoriti_v1"
NAME = "Favoriti pre-partita"
KIND = "prematch"


def _hours_to(kickoff: str, now: float) -> float:
    return (datetime.fromisoformat(kickoff).timestamp() - now) / 3600


def propose(snapshot: dict, params: dict, ctx: dict) -> list[dict]:
    out = []
    now = snapshot.get("sim_time") or snapshot["ts"]
    for m in snapshot["matches"].values():
        if m["status"] != "SCHEDULED" or not m.get("books"):
            continue
        h = _hours_to(m["kickoff"], now)
        if not (params["min_hours_before_kickoff"] <= h <= params["hours_before_kickoff"]):
            continue
        c = consensus(m["books"])
        for sel, v in c.items():
            if sel.startswith("_"):
                continue
            if not (params["odds_min"] <= v["best_odds"] <= params["odds_max"]):
                continue
            if v["fair_prob"] < params["min_fair_prob"] or v["edge"] < params["min_edge"]:
                continue
            name = m["home"] if sel == "home" else m["away"] if sel == "away" else "Pareggio"
            out.append({"strategy_id": STRATEGY_ID, "match_id": m["match_id"], "league": m["league"], "home": m["home"], "away": m["away"],
                        "label": f"{m['home']} - {m['away']} · {name}", "market": "h2h", "selection": sel,
                        "bookmaker": v["best_book"], "odds": v["best_odds"], "fair_prob": v["fair_prob"],
                        "edge": v["edge"], "n_books": v["n_books"], "dispersion": v["dispersion"], "live": False,
                        "odds_ts": m.get("odds_ts"),
                        "reason": f"favorito {name}: prob. giusta {v['fair_prob']:.0%} (quota giusta "
                                  f"{v['fair_odds']:.2f}), migliore {v['best_odds']:.2f} @ {v['best_book']}, "
                                  f"edge {v['edge']:+.1%}, {v['n_books']} bookmaker, tra {h:.1f} h"})
    return out
