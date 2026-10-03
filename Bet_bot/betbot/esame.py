"""Esame per il live: i criteri di config/esame_live.yaml applicati alle autopsie di Leo sui prezzi VERI.

Conta solo ciò che è successo su betfair.it (feed 'betfair') o sulle sue registrazioni (feed 'replay'):
il mondo simulato non fa media. Per ogni strategia:
  • PRONTA      — almeno min_puntate chiuse, CLV positivo anche nel caso peggiore (confidenza), ROI > 0;
  • BOCCIATA    — almeno min_puntate chiuse e CLV negativo anche nel caso migliore;
  • IN ESAME    — dati ancora insufficienti o risultato non ancora chiaro.
Il ROI si misura sul rischio: per un lay il rischio è (quota − 1) × puntata del backer.
"""
from __future__ import annotations

import json
import math

from .config import load_yaml

REAL_FEEDS = ("betfair", "replay")
Z = {0.90: 1.2816, 0.95: 1.6449, 0.975: 1.96, 0.99: 2.3263}


def criteria(strategy_id: str) -> dict:
    try:
        cfg = load_yaml("esame_live.yaml")
    except FileNotFoundError:
        cfg = {}
    return {**(cfg.get("default") or {}), **(cfg.get(strategy_id) or {})}


def evaluate(store, strategy_id: str, side: str | None = None, fresh: bool = False) -> dict:
    c = criteria(strategy_id)
    rows = store.query("SELECT l.outcome, l.pnl, l.stake, l.clv, e.features, e.odds, e.side, e.src FROM coach_lessons l "
                       "JOIN coach_entries e ON e.id = l.entry_id WHERE l.strategy_id=? AND l.outcome IN ('WON','LOST')",
                       (strategy_id,))
    real = []
    for r in rows:
        f = json.loads(r["features"] or "{}")
        if side and (r["side"] or "BACK") != side:
            continue
        if fresh and (f.get("ref_age_min") is None or f["ref_age_min"] > 90):
            continue                                   # solo con Pinnacle di al massimo 90 minuti
        if f.get("feed") in REAL_FEEDS:
            # ombre lay: stake = puntata del backer; libro delle puntate: stake = responsabilità (già il rischio)
            lay_shadow = r["side"] == "LAY" and r["src"] == "shadow_bets"
            risk = (r["stake"] or 1.0) * ((r["odds"] or 2.0) - 1) if lay_shadow else (r["stake"] or 1.0)
            real.append({**r, "risk": risk})
    n = len(real)
    clvs = [r["clv"] for r in real if r["clv"] is not None]
    z = Z.get(float(c.get("confidenza", 0.95)), 1.6449)
    mean = sum(clvs) / len(clvs) if clvs else None
    se = (math.sqrt(sum((x - mean) ** 2 for x in clvs) / (len(clvs) - 1)) / math.sqrt(len(clvs))) if len(clvs) > 1 else None
    lo = None if se is None else mean - z * se
    hi = None if se is None else mean + z * se
    risk = sum(r["risk"] for r in real)
    roi = sum(r["pnl"] or 0 for r in real) / risk if risk else None
    need = int(c.get("min_puntate", 200))
    reasons = []
    if n < need:
        reasons.append(f"servono {need} puntate chiuse sui prezzi veri, ce ne sono {n}")
    if lo is None or lo <= c.get("clv_minimo", 0.0):
        reasons.append("CLV non ancora positivo nel caso peggiore" if lo is not None else "CLV non misurabile (manca la chiusura)")
    if roi is None or roi <= c.get("roi_minimo", 0.0):
        reasons.append("ROI non positivo" if roi is not None else "ROI non misurabile")
    if n >= need and hi is not None and hi < c.get("bocciatura_clv", 0.0):
        verdict = "BOCCIATA"
    elif not reasons:
        verdict = "PRONTA"
    else:
        verdict = "IN ESAME"
    label = strategy_id + (" · solo lay" if side == "LAY" else " · solo back" if side == "BACK" else "") \
        + (" fresco" if fresh else "")
    return {"strategy_id": label, "verdict": verdict, "n": n, "need": need, "clv": mean, "clv_lo": lo, "clv_hi": hi,
            "roi": roi, "win_rate": sum(1 for r in real if r["outcome"] == "WON") / n if n else None,
            "reasons": reasons, "criteria": c}


def evaluate_all(store, strategy_ids: list[str]) -> list[dict]:
    from .agents.coach import CoachBook
    CoachBook(store)                                   # crea le tabelle di Leo se il database è di una versione precedente
    out = []
    for sid in strategy_ids:
        out.append(evaluate(store, sid))
        if sid.startswith("S10"):                  # il divertimento mescola lay (vantaggio possibile) e back (nessuno): si separano
            out += [evaluate(store, sid, "LAY"), evaluate(store, sid, "LAY", fresh=True), evaluate(store, sid, "BACK")]
    return out


def report(results: list[dict]) -> str:
    pct = lambda x: "—" if x is None else f"{x:+.1%}"
    L = ["| Strategia | Esito | Puntate | CLV (intervallo) | ROI sul rischio | Cosa manca |", "|---|---|---|---|---|---|"]
    for r in results:
        L.append(f"| {r['strategy_id']} | **{r['verdict']}** | {r['n']}/{r['need']} | {pct(r['clv'])} "
                 f"({pct(r['clv_lo'])} … {pct(r['clv_hi'])}) | {pct(r['roi'])} | {'; '.join(r['reasons']) or '—'} |")
    return "\n".join(L) + "\n"
