"""S07 — Scalping pre-partita su Betfair Exchange (calcio, tennis, basket): back→lay prima dell'inizio.

Sostituisce il trading sui cavalli, che su betfair.it non c'è. Stessa meccanica della S04 v2:
  ingresso BACK sulla selezione con più denaro in attesa sul lato back (weight of money ≥ `wom_min`),
  spread di 1 tick, abbastanza denaro sul miglior prezzo per entrare e uscire;
  uscita LAY al target (−`target_ticks`), allo stop (+`stop_ticks`) oppure `exit_minutes_before`
  minuti prima dell'inizio: la partita non viene mai giocata, e in-play i prezzi saltano (bet delay).
La puntata la decide il Risk Manager dalla perdita massima allo stop (con scivolamento).

Avvertenza onesta: il vantaggio di questa strategia dipende dal fatto che il weight of money anticipi
davvero il movimento del prezzo sul pool italiano. Non c'è un dataset pubblico per dimostrarlo: va
misurato prima in paper sui prezzi veri di betfair.it (registratore incluso: feed.record = true).
"""
from __future__ import annotations

from datetime import datetime

from ..feeds.mock import tick_up
from .s04_greenup_cavalli_v2 import trade_plan

STRATEGY_ID = "S07_scalping_prepartita_v1"
NAME = "Scalping pre-partita"
KIND = "exchange"

DEFAULTS = {"odds_min": 1.50, "odds_max": 4.0, "max_minutes_before": 120, "min_minutes_before": 10,
            "exit_minutes_before": 3, "target_ticks": 2, "stop_ticks": 3, "slippage_ticks": 2, "wom_min": 0.65,
            "commission": 0.045, "min_book_multiple": 5.0, "min_stake": 2.0, "min_net_target_return": 0.004,
            "sports": ["soccer", "tennis", "basketball"]}


def _wom(b: dict) -> float:
    tot = (b.get("back_size") or 0) + (b.get("lay_size") or 0)
    return (b.get("back_size") or 0) / tot if tot else 0.5


def _minutes_to(kickoff: str, now: float) -> float:
    return (datetime.fromisoformat(kickoff).timestamp() - now) / 60


def _sport_ok(m: dict, sports: list[str]) -> bool:
    s = (m.get("sport") or "")
    return any(s.startswith(x) or (x == "soccer" and s.startswith("soccer")) for x in sports)


def propose(snapshot: dict, params: dict, ctx: dict) -> list[dict]:
    p = {**DEFAULTS, **params}
    now = snapshot.get("sim_time") or snapshot["ts"]
    out = []
    for m in snapshot["matches"].values():
        ex = m.get("exchange") or {}
        if m["status"] != "SCHEDULED" or not ex or not _sport_ok(m, p["sports"]):
            continue
        mins = _minutes_to(m["kickoff"], now)
        if not (p["min_minutes_before"] <= mins <= p["max_minutes_before"]):
            continue
        best = None
        for sel, b in ex.items():
            back, lay = b.get("back"), b.get("lay")
            if not back or not lay or not (p["odds_min"] <= back <= p["odds_max"]) or lay > tick_up(back):
                continue
            if min(b.get("back_size") or 0, b.get("lay_size") or 0) < p["min_book_multiple"] * p["min_stake"]:
                continue
            w = _wom(b)
            if w >= p["wom_min"] and (best is None or w > best[2]):
                best = (sel, b, w)
        if not best:
            continue
        sel, b, w = best
        plan = trade_plan(b["back"], {**p, "commission": m.get("commission") or p["commission"]})
        if plan["gain_per_unit"] < p["min_net_target_return"]:
            continue
        name = m["home"] if sel == "home" else m["away"] if sel == "away" else "Pareggio"
        out.append({"strategy_id": STRATEGY_ID, "match_id": m["match_id"],
                    "market_id": (m.get("betfair") or {}).get("market_id") or m["match_id"],
                    "league": m["league"], "sport": m.get("sport"), "home": m["home"], "away": m["away"],
                    "label": f"{m['home']} - {m['away']} · {name} (back→lay)", "market": "exchange_trade",
                    "selection": sel, "bookmaker": "Betfair", "odds": b["back"],
                    "fair_prob": 1 - plan["breakeven_hit_rate"], "edge": plan["gain_per_unit"],
                    "n_books": 1, "dispersion": 0.0, "live": False, "odds_ts": m.get("odds_ts"),
                    "exchange": {"target": plan["target"], "stop": plan["stop"], "worst": plan["worst"],
                                 "risk_per_unit": plan["risk_per_unit"], "gain_per_unit": plan["gain_per_unit"],
                                 "exit_minutes_before": p["exit_minutes_before"],
                                 "commission": m.get("commission") or p["commission"]},
                    "reason": f"{name} back {b['back']:.2f}, WoM {w:.0%}, target lay {plan['target']:.2f} "
                              f"(+{plan['gain_per_unit']:.1%} netto), stop {plan['stop']:.2f} "
                              f"(−{plan['risk_per_unit']:.1%} nel caso peggiore), serve centrare il "
                              f"{plan['breakeven_hit_rate']:.0%} dei trade, inizio tra {mins:.0f} min"})
    return out


def manage(open_bets: list[dict], snapshot: dict, params: dict, ctx: dict) -> list[dict]:
    p = {**DEFAULTS, **params}
    now = snapshot.get("sim_time") or snapshot["ts"]
    actions = []
    for bet in open_bets:
        m = snapshot["matches"].get(bet["match_id"])
        b = ((m or {}).get("exchange") or {}).get(bet["selection"])
        if not m or not b or m["status"] != "SCHEDULED":
            actions.append({"bet_id": bet["id"], "action": "hedge", "urgent": True, "price": (b or {}).get("lay") or bet["odds"],
                            "reason": "partita iniziata o mercato non più nel feed: chiusura prudenziale"})
            continue
        plan = trade_plan(bet["odds"], p)
        lay = b.get("lay") or bet["odds"]
        if lay <= plan["target"]:
            actions.append({"bet_id": bet["id"], "action": "hedge", "price": lay,
                            "reason": f"target raggiunto: lay a {lay:.2f} (back {bet['odds']:.2f}) → profitto verde"})
        elif lay >= plan["stop"]:
            actions.append({"bet_id": bet["id"], "action": "hedge", "urgent": True, "price": lay,
                            "reason": f"stop loss: lay a {lay:.2f} (back {bet['odds']:.2f})"})
        elif _minutes_to(m["kickoff"], now) <= p["exit_minutes_before"]:
            actions.append({"bet_id": bet["id"], "action": "hedge", "urgent": True, "price": lay,
                            "reason": f"inizio tra meno di {p['exit_minutes_before']} min: chiudo a {lay:.2f}"})
    return actions
