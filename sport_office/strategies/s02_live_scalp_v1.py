"""S02 — Live scalping: favorito in vantaggio nel finale.

Ingresso: partita LIVE dal minuto min_minute, squadra in vantaggio di almeno
min_lead, quota live nella fascia, edge sul consenso live ≥ min_edge.
Gestione: se il vantaggio sparisce si chiede il cash-out (limita la perdita).
"""
from __future__ import annotations

from ..odds import consensus

STRATEGY_ID = "S02_live_scalp_v1"
NAME = "Live scalping finale"
KIND = "live"


def _leader(m: dict) -> str | None:
    if m.get("home_score") is None or m.get("away_score") is None:
        return None
    d = m["home_score"] - m["away_score"]
    return "home" if d > 0 else "away" if d < 0 else None


def propose(snapshot: dict, params: dict, ctx: dict) -> list[dict]:
    out = []
    for m in snapshot["matches"].values():
        if m["status"] != "LIVE" or not m.get("live_books") or m.get("minute") is None:
            continue
        if m["minute"] < params["min_minute"]:
            continue
        lead = abs((m["home_score"] or 0) - (m["away_score"] or 0))
        sel = _leader(m)
        if not sel or lead < params["min_lead"]:
            continue
        st = m.get("stats") or {}
        mine, opp = st.get(sel) or {}, st.get("away" if sel == "home" else "home") or {}
        if params.get("skip_if_red_card", True) and mine.get("red_cards", 0) > opp.get("red_cards", 0):
            continue                                   # in inferiorità numerica: il vantaggio vale meno di quanto dice il punteggio
        if opp.get("shots_on_target", 0) >= params.get("min_opp_shots_check", 5) and \
                opp["shots_on_target"] > params.get("max_opp_shot_ratio", 2.0) * max(1, mine.get("shots_on_target", 0)):
            continue                                   # l'avversario sta assediando: niente ingresso
        c = consensus(m["live_books"])
        v = c.get(sel)
        if not v or not (params["odds_min"] <= v["best_odds"] <= params["odds_max"]) or v["edge"] < params["min_edge"]:
            continue
        name = m[sel]
        out.append({"strategy_id": STRATEGY_ID, "match_id": m["match_id"], "league": m["league"], "home": m["home"], "away": m["away"],
                    "label": f"{m['home']} - {m['away']} · {name} (live {m['minute']}')", "market": "h2h",
                    "selection": sel, "bookmaker": v["best_book"], "odds": v["best_odds"],
                    "fair_prob": v["fair_prob"], "edge": v["edge"], "n_books": v["n_books"],
                    "dispersion": v["dispersion"], "live": True, "odds_ts": m.get("odds_ts"),
                    "reason": f"{name} avanti {m['home_score']}-{m['away_score']} al {m['minute']}': quota live "
                              f"{v['best_odds']:.2f} @ {v['best_book']}, prob. {v['fair_prob']:.0%}, edge {v['edge']:+.1%}"})
    return out


def manage(open_bets: list[dict], snapshot: dict, params: dict, ctx: dict) -> list[dict]:
    if not params.get("cashout_on_lead_lost"):
        return []
    actions = []
    for b in open_bets:
        m = snapshot["matches"].get(b["match_id"])
        if not m or m["status"] != "LIVE" or not m.get("live_books"):
            continue
        if _leader(m) != b["selection"]:
            c = consensus(m["live_books"]).get(b["selection"])
            if c:
                actions.append({"bet_id": b["id"], "action": "cashout", "price": c["best_odds"],
                                "reason": f"vantaggio perso ({m['home_score']}-{m['away_score']} al {m['minute']}'): "
                                          f"cash-out a quota {c['best_odds']:.2f}"})
    return actions
