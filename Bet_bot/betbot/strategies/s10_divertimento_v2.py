"""S10 v2 — Divertimento, ma PRIMA il lay di valore quando c'è.

Perché (confronto del 30/09/2026 sui prezzi Betfair 2024/25 e 2025/26 di football-data, regola scelta sulla prima
stagione e confermata sulla seconda):
  • v1 (back al prezzo più giusto, EV ≥ −3%): CLV +0,1% ± 0,5% e +0,3% ± 0,6% → nessun vantaggio, nessun danno;
  • lay 3-8 con EV ≥ 2% (la regola di S09): CLV +16,2% ± 5,1% e +12,8% ± 5,1% → il mercato alla chiusura ci dà
    ragione in tutte e due le stagioni. ROI +2,5% ± 14% e +12,6% ± 19%: positivo ma con poche puntate (65 e 27).
    Con la commissione un CLV del 13-16% sul lay vale circa +2% di guadagno atteso sul rischio.
  • le altre varianti (solo EV ≥ 0, quote 1,40-2,00, senza pareggio, lay con EV ≥ 0) non migliorano in modo solido.
Quindi: se c'è un lay di valore (calcio, quote 3-8, EV ≥ 2% sul rischio, Pinnacle fresco) si fa quello, con la
puntata del backer minima (0,50 €: rischio 1-3,50 €); altrimenti il back della v1. Stessi freni: 2 € sui back,
al massimo 5 al giorno, una aperta alla volta. I prezzi del backtest sono del venerdì: su betfair.it vicino al fischio
d'inizio il vantaggio può essere più piccolo. Lo dicono il test rapido e l'esame.
"""
from __future__ import annotations

from . import s09_lay_valore_v1 as S09
from . import s10_divertimento_v1 as V1

STRATEGY_ID = "S10_divertimento_v2"
NAME = "Divertimento (lay di valore prima)"
KIND = "prematch"

DEFAULTS = {**V1.DEFAULTS, "lay_min_edge": 0.02, "lay_min": 3.0, "lay_max": 8.0}


def lay_candidates(snapshot: dict, q: dict) -> list[dict]:
    now = snapshot.get("sim_time") or snapshot["ts"]
    fresh = {k: m for k, m in snapshot["matches"].items()
             if not m.get("ref_ts") or now - m["ref_ts"] <= q["max_ref_age_s"]}     # riferimento vecchio: niente lay
    lays = S09.propose({**snapshot, "matches": fresh},
                       {"min_edge": q["lay_min_edge"], "lay_min": q["lay_min"], "lay_max": q["lay_max"],
                        "min_minutes_before": q["min_minutes_before"]}, {})
    out = []
    for p in sorted(lays, key=lambda p: -p["edge"]):
        m = snapshot["matches"][p["match_id"]]
        p.update(strategy_id=STRATEGY_ID, fun=True, kickoff=m["kickoff"],
                 book_eur=(m["exchange"].get(p["selection"][4:]) or {}).get("lay_size_best") or 0.0,
                 reason="Divertimento, lay di valore: " + p["reason"])
        out.append(p)
    return out


def candidates(snapshot: dict, params: dict | None = None) -> list[dict]:
    q = {**DEFAULTS, **(params or {})}
    lays = lay_candidates(snapshot, q)
    taken = {p["match_id"] for p in lays}
    backs = [dict(p, strategy_id=STRATEGY_ID) for p in V1.candidates(snapshot, q) if p["match_id"] not in taken]
    return lays + backs                                  # prima i lay di valore, poi i back più giusti


def propose(snapshot: dict, params: dict, ctx: dict) -> list[dict]:
    q = {**DEFAULTS, **params}
    if _at_limit((ctx or {}).get("store")):
        return []
    return candidates(snapshot, q)[:q["max_proposals"]]


def _at_limit(store) -> bool:
    if store is None:
        return False
    from ..agents.risk import _day_start_iso
    from ..config import load_yaml
    lim = load_yaml("risk_limits.yaml")
    day = _day_start_iso()
    rows = store.query("SELECT status, ts FROM bets WHERE mode!='shadow' AND strategy_id=? AND (status='OPEN' OR ts >= ?)",
                       (STRATEGY_ID, day))
    return (sum(1 for r in rows if r["status"] == "OPEN") >= lim.get("fun_max_open", 1)
            or sum(1 for r in rows if r["ts"] >= day) >= lim.get("fun_max_bets_per_day", 5))

