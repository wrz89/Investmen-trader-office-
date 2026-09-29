"""S06 — Favorito in vantaggio nel finale, su Betfair Exchange (in-play).

Ingresso: partita LIVE dal minuto `min_minute`, squadra avanti di almeno `min_lead`, quota Betfair
nella fascia, EV al netto della commissione ≥ `min_edge` rispetto alla probabilità live di riferimento
(bookmaker live di The Odds API, oppure il modello del feed simulato). Niente ingresso se la squadra in
vantaggio ha un uomo in meno o se l'avversario sta assediando (tiri in porta, da API-Football).
Gestione: se il vantaggio sparisce si chiude con un lay (green/red-up) invece di aspettare il fischio.
"""
from __future__ import annotations

from ..odds import consensus
from .s05_favoriti_exchange_v1 import ev_net

STRATEGY_ID = "S06_live_exchange_v1"
NAME = "Live finale su exchange"
KIND = "live"

DEFAULTS = {"min_minute": 70, "min_lead": 1, "odds_min": 1.05, "odds_max": 1.30, "min_edge": 0.01,
            "commission": 0.045, "skip_if_red_card": True, "min_opp_shots_check": 5, "max_opp_shot_ratio": 2.0,
            "close_on_lead_lost": True}


def _leader(m: dict) -> str | None:
    if m.get("home_score") is None or m.get("away_score") is None:
        return None
    d = m["home_score"] - m["away_score"]
    return "home" if d > 0 else "away" if d < 0 else None


def propose(snapshot: dict, params: dict, ctx: dict) -> list[dict]:
    q = {**DEFAULTS, **params}
    out = []
    for m in snapshot["matches"].values():
        ex = m.get("exchange") or {}
        if m["status"] != "LIVE" or not ex or not m.get("live_books") or m.get("minute") is None:
            continue
        sel = _leader(m)
        if not sel or m["minute"] < q["min_minute"] or abs(m["home_score"] - m["away_score"]) < q["min_lead"]:
            continue
        st = m.get("stats") or {}
        mine, opp = st.get(sel) or {}, st.get("away" if sel == "home" else "home") or {}
        if q["skip_if_red_card"] and mine.get("red_cards", 0) > opp.get("red_cards", 0):
            continue
        if opp.get("shots_on_target", 0) >= q["min_opp_shots_check"] and \
                opp["shots_on_target"] > q["max_opp_shot_ratio"] * max(1, mine.get("shots_on_target", 0)):
            continue
        v = consensus(m["live_books"]).get(sel)
        price = (ex.get(sel) or {}).get("back")
        if not v or not price or not (q["odds_min"] <= price <= q["odds_max"]):
            continue
        comm = m.get("commission") or q["commission"]
        edge = ev_net(v["fair_prob"], price, comm)
        if edge < q["min_edge"]:
            continue
        out.append({"strategy_id": STRATEGY_ID, "match_id": m["match_id"],
                    "market_id": (m.get("betfair") or {}).get("market_id") or m["match_id"],
                    "league": m["league"], "home": m["home"], "away": m["away"],
                    "label": f"{m['home']} - {m['away']} · {m[sel]} (live {m['minute']}')", "market": "h2h",
                    "selection": sel, "bookmaker": "Betfair", "odds": price, "fair_prob": v["fair_prob"], "edge": edge,
                    "commission": comm, "n_books": v["n_books"], "dispersion": v["dispersion"], "live": True,
                    "odds_ts": m.get("odds_ts"), "ref_ts": m.get("ref_ts"),
                    "reason": f"{m[sel]} avanti {m['home_score']}-{m['away_score']} al {m['minute']}': Betfair {price:.2f}, "
                              f"probabilità live {v['fair_prob']:.0%}, EV netto {edge:+.1%}"})
    return out


def manage(open_bets: list[dict], snapshot: dict, params: dict, ctx: dict) -> list[dict]:
    q = {**DEFAULTS, **params}
    if not q["close_on_lead_lost"]:
        return []
    actions = []
    for b in open_bets:
        m = snapshot["matches"].get(b["match_id"])
        if not m or m["status"] != "LIVE" or not m.get("exchange"):
            continue
        if _leader(m) != b["selection"]:
            lay = (m["exchange"].get(b["selection"]) or {}).get("lay")
            if lay:
                actions.append({"bet_id": b["id"], "action": "hedge", "urgent": True, "price": lay,
                                "reason": f"vantaggio perso ({m['home_score']}-{m['away_score']} al {m['minute']}'): "
                                          f"chiudo con un lay a {lay:.2f}"})
    return actions
