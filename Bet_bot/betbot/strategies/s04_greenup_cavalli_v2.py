"""S04 v2 — Green-up pre-gara sui cavalli (Betfair Exchange), dimensionato sul RISCHIO.

Cosa cambia dalla v1:
  • la puntata non si decide in % del bankroll ma dalla PERDITA MASSIMA: se il prezzo va allo stop
    (più 2 tick di scivolamento), si perde puntata × (1 − back/stop). Il Risk Manager sceglie la
    puntata perché questa perdita resti sotto `max_risk_per_trade_pct` del bankroll;
  • il guadagno del target è calcolato AL NETTO della commissione: se non supera `min_net_ticks_profit`
    la proposta non parte;
  • controllo di liquidità: sul miglior prezzo devono esserci almeno `min_book_multiple` volte la puntata
    minima, e lo spread deve essere di 1 tick (mercato vivo).

Ingresso (back): corsa aperta, tra `max_seconds_to_off` e `min_seconds_to_off` dal via, prezzo nella fascia,
peso del denaro in back ≥ `wom_min`. Uscita (lay): target raggiunto, stop, oppure time-to-jump a
`exit_seconds_to_off` secondi dal via. La corsa non viene mai giocata: si chiude sempre prima.
"""
from __future__ import annotations

from ..feeds.mock import tick_down, tick_up

STRATEGY_ID = "S04_greenup_cavalli_v2"
NAME = "Green-up cavalli (exchange)"
KIND = "exchange"

DEFAULTS = {"odds_min": 2.0, "odds_max": 6.0, "min_seconds_to_off": 180, "max_seconds_to_off": 900,
            "exit_seconds_to_off": 60, "target_ticks": 2, "stop_ticks": 3, "slippage_ticks": 2, "wom_min": 0.62,
            "commission": 0.045, "min_book_multiple": 5.0, "min_stake": 2.0, "min_net_target_return": 0.004}


def trade_plan(back: float, p: dict) -> dict:
    target = tick_down(back, p["target_ticks"])
    stop = tick_up(back, p["stop_ticks"])
    worst = tick_up(stop, p["slippage_ticks"])
    gain = (back / target - 1.0) * (1 - p["commission"])       # per 1 € puntato, se centra il target
    loss = 1.0 - back / worst                                   # per 1 € puntato, allo stop con scivolamento
    return {"target": target, "stop": stop, "worst": worst, "gain_per_unit": gain, "risk_per_unit": loss,
            "breakeven_hit_rate": loss / (gain + loss) if gain + loss > 0 else 1.0}


def propose(snapshot: dict, params: dict, ctx: dict) -> list[dict]:
    p = {**DEFAULTS, **params}
    out = []
    for r in snapshot.get("races", {}).values():
        if r["status"] != "OPEN" or not (p["min_seconds_to_off"] <= r["seconds_to_off"] <= p["max_seconds_to_off"]):
            continue
        best = None
        for rid, run in r["runners"].items():
            back, lay = run.get("back"), run.get("lay")
            if not back or not lay or not (p["odds_min"] <= back <= p["odds_max"]) or lay > tick_up(back):
                continue                                       # fuori fascia o spread > 1 tick
            if run.get("wom", 0) < p["wom_min"]:
                continue
            if min(run.get("back_size") or 0, run.get("lay_size") or 0) < p["min_book_multiple"] * p["min_stake"]:
                continue                                       # book troppo sottile per entrare e uscire
            if best is None or run["wom"] > best[1]["wom"]:
                best = (rid, run)
        if not best:
            continue
        rid, run = best
        plan = trade_plan(run["back"], p)
        if plan["gain_per_unit"] < p["min_net_target_return"]:
            continue
        out.append({"strategy_id": STRATEGY_ID, "market_id": r["market_id"], "match_id": r["market_id"],
                    "league": f"Cavalli · {r['venue']}", "label": f"{r['venue']} {r['race']} · {run['name']} (back→lay)",
                    "market": "exchange_win", "selection": rid, "bookmaker": "Betfair", "odds": run["back"],
                    "fair_prob": 1 - plan["breakeven_hit_rate"], "edge": plan["gain_per_unit"],
                    "n_books": 1, "dispersion": 0.0, "live": False, "odds_ts": snapshot.get("sim_time") or snapshot["ts"],
                    "exchange": {"target": plan["target"], "stop": plan["stop"], "worst": plan["worst"],
                                 "risk_per_unit": plan["risk_per_unit"], "gain_per_unit": plan["gain_per_unit"],
                                 "exit_seconds_to_off": p["exit_seconds_to_off"], "commission": p["commission"]},
                    "reason": f"{run['name']} back {run['back']:.2f}, WoM {run['wom']:.0%}, target lay {plan['target']:.2f} "
                              f"(+{plan['gain_per_unit']:.1%} netto), stop {plan['stop']:.2f} (−{plan['risk_per_unit']:.1%} "
                              f"nel caso peggiore), serve centrare il {plan['breakeven_hit_rate']:.0%} dei trade, "
                              f"via tra {r['seconds_to_off']:.0f} s"})
    return out


def manage(open_bets: list[dict], snapshot: dict, params: dict, ctx: dict) -> list[dict]:
    p = {**DEFAULTS, **params}
    actions = []
    for b in open_bets:
        r = snapshot.get("races", {}).get(b["match_id"])
        if not r:
            actions.append({"bet_id": b["id"], "action": "hedge", "price": b["odds"],
                            "reason": "corsa partita: mercato non più nel feed, chiusura prudenziale"})
            continue
        run = r["runners"].get(b["selection"])
        if r["status"] != "OPEN" or not run:
            actions.append({"bet_id": b["id"], "action": "hedge", "price": b["odds"], "reason": "corsa partita: chiusura forzata"})
            continue
        plan = trade_plan(b["odds"], p)
        lay = run.get("lay") or b["odds"]
        if lay <= plan["target"]:
            actions.append({"bet_id": b["id"], "action": "hedge", "price": lay,
                            "reason": f"target raggiunto: lay a {lay:.2f} (back {b['odds']:.2f}) → profitto verde"})
        elif lay >= plan["stop"]:
            actions.append({"bet_id": b["id"], "action": "hedge", "price": lay,
                            "reason": f"stop loss: lay a {lay:.2f} (back {b['odds']:.2f})"})
        elif r["seconds_to_off"] <= p["exit_seconds_to_off"]:
            actions.append({"bet_id": b["id"], "action": "hedge", "price": lay,
                            "reason": f"time-to-jump: {r['seconds_to_off']:.0f} s al via, chiudo a {lay:.2f}"})
    return actions
