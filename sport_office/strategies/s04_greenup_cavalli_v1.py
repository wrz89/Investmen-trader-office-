"""S04 — Green-up pre-gara sui cavalli (exchange, back-to-lay).

Idee prese dal bot Betfair di riferimento: back-to-lay con obiettivo in tick,
stop loss in tick, uscita a tempo prima del via (time-to-jump), weight of money.

Ingresso (back): corsa OPEN, da min_seconds_to_off a max_seconds_to_off dal via,
runner con prezzo nella fascia, spread di 1 tick, weight of money ≥ wom_min
(più denaro in attesa sul back → prezzo che tende a scendere).
Gestione: lay quando il prezzo è sceso di target_ticks (profitto verde su tutti
i cavalli), stop se sale di stop_ticks, chiusura forzata a exit_seconds_to_off.
La corsa in sé non viene mai "scommessa": la posizione è sempre chiusa prima del via.
"""
from __future__ import annotations

from ..feeds.mock import tick_down, tick_up

STRATEGY_ID = "S04_greenup_cavalli_v1"
NAME = "Green-up cavalli (exchange)"
KIND = "exchange"

DEFAULTS = {"odds_min": 2.0, "odds_max": 6.0, "min_seconds_to_off": 180, "max_seconds_to_off": 900,
            "exit_seconds_to_off": 60, "target_ticks": 2, "stop_ticks": 3, "wom_min": 0.62,
            "commission": 0.05, "max_runners_per_race": 1}


def propose(snapshot: dict, params: dict, ctx: dict) -> list[dict]:
    p = {**DEFAULTS, **params}
    out = []
    for r in snapshot.get("races", {}).values():
        if r["status"] != "OPEN" or not (p["min_seconds_to_off"] <= r["seconds_to_off"] <= p["max_seconds_to_off"]):
            continue
        best = None
        for rid, run in r["runners"].items():
            if not (p["odds_min"] <= run["back"] <= p["odds_max"]) or run["lay"] > tick_up(run["back"]):
                continue
            if run["wom"] < p["wom_min"]:
                continue
            if best is None or run["wom"] > best[1]["wom"]:
                best = (rid, run)
        if not best:
            continue
        rid, run = best
        target = tick_down(run["back"], p["target_ticks"])
        # "probabilità" per il Risk Manager: quota del target rispetto al prezzo attuale; edge = guadagno se centrato
        out.append({"strategy_id": STRATEGY_ID, "market_id": r["market_id"], "match_id": r["market_id"],
                    "league": f"Cavalli · {r['venue']}", "label": f"{r['venue']} {r['race']} · {run['name']} (back→lay)",
                    "market": "exchange_win", "selection": rid, "bookmaker": "Exchange", "odds": run["back"],
                    "fair_prob": 1 / run["back"] * (1 + 0.5 * (run["wom"] - 0.5)), "edge": run["back"] / target - 1,
                    "n_books": 1, "dispersion": 0.0, "live": False, "odds_ts": snapshot.get("sim_time") or snapshot["ts"],
                    "exchange": {"target": target, "stop": tick_up(run["back"], p["stop_ticks"]),
                                 "exit_seconds_to_off": p["exit_seconds_to_off"], "commission": p["commission"]},
                    "reason": f"{run['name']} back {run['back']:.2f}, WoM {run['wom']:.0%}, target lay {target:.2f} "
                              f"(−{p['target_ticks']} tick), stop {tick_up(run['back'], p['stop_ticks']):.2f}, "
                              f"via tra {r['seconds_to_off']:.0f} s"})
    return out


def manage(open_bets: list[dict], snapshot: dict, params: dict, ctx: dict) -> list[dict]:
    p = {**DEFAULTS, **params}
    actions = []
    for b in open_bets:
        r = snapshot.get("races", {}).get(b["match_id"])
        if not r:
            continue
        run = r["runners"].get(b["selection"])
        if r["status"] != "OPEN" or not run:
            # la corsa è partita senza che la posizione fosse chiusa: si chiude al prezzo dell'ultima quota
            actions.append({"bet_id": b["id"], "action": "hedge", "price": b["odds"], "reason": "corsa partita: chiusura forzata"})
            continue
        target = tick_down(b["odds"], p["target_ticks"])
        stop = tick_up(b["odds"], p["stop_ticks"])
        if run["lay"] <= target:
            actions.append({"bet_id": b["id"], "action": "hedge", "price": run["lay"],
                            "reason": f"target raggiunto: lay a {run['lay']:.2f} (back {b['odds']:.2f}) → profitto verde"})
        elif run["lay"] >= stop:
            actions.append({"bet_id": b["id"], "action": "hedge", "price": run["lay"],
                            "reason": f"stop loss: lay a {run['lay']:.2f} (back {b['odds']:.2f})"})
        elif r["seconds_to_off"] <= p["exit_seconds_to_off"]:
            actions.append({"bet_id": b["id"], "action": "hedge", "price": run["lay"],
                            "reason": f"time-to-jump: {r['seconds_to_off']:.0f} s al via, chiudo a {run['lay']:.2f}"})
    return actions
