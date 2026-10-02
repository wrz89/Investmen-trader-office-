"""Multiple VIRTUALI (doppie e triple), solo come misura: nessun ordine, nessun soldo.

Betfair Exchange non ha le multiple: qui si combinano, dopo il risultato, le puntate singole che S10 misura ha già
registrato in ombra. Per ogni giorno si prendono le puntate back di partite diverse, in ordine di orario, e si
raggruppano a due a due (doppie) e a tre a tre (triple). Quota della multipla = prodotto delle quote; si vince solo se
vincono tutte le gambe (una gamba annullata esce dal prodotto). Si paga la commissione una volta sulla vincita netta.
Serve a rispondere a due domande con numeri veri: quanto più oscilla il risultato, e quale gamba rompe più spesso la
multipla (sport e fascia di quota), cioè dove le singole sono più fragili.
"""
from __future__ import annotations

import math
from collections import defaultdict

COMM = 0.045
STRATEGY = "S10_misura_v1"


def _band(o: float) -> str:
    return "1,40-1,80" if o < 1.8 else "1,80-2,40" if o < 2.4 else "2,40-3,00" if o < 3.0 else "3+"


def groups(rows: list[dict], size: int) -> list[list[dict]]:
    """Gruppi di `size` gambe dello stesso giorno, partite diverse, in ordine di orario (l'ultimo gruppo incompleto salta)."""
    by_day = defaultdict(list)
    for r in rows:
        by_day[(r["ts"] or "")[:10]].append(r)
    out = []
    for day in sorted(by_day):
        legs, seen = [], set()
        for r in sorted(by_day[day], key=lambda r: r["ts"]):
            if r["match_id"] in seen:
                continue
            seen.add(r["match_id"])
            legs.append(r)
        out += [legs[i:i + size] for i in range(0, len(legs) - size + 1, size)]
    return out


def evaluate(rows: list[dict], size: int) -> dict:
    res, broke = [], defaultdict(lambda: [0, 0])          # (sport, fascia) → [volte che ha rotto la multipla, gambe totali]
    for g in groups(rows, size):
        live = [r for r in g if r["status"] in ("WON", "LOST")]
        if len(live) < size:
            continue                                      # una gamba annullata o ancora aperta: non si conta
        odds = math.prod(r["odds"] for r in live)
        won = all(r["status"] == "WON" for r in live)
        pnl = (odds - 1) * (1 - COMM) if won else -1.0
        res.append({"pnl": pnl, "won": won, "p": math.prod(max(0.01, min(0.99, r["fair_prob"] or 0.5)) for r in live)})
        for r in live:
            k = (r.get("sport") or "?", _band(r["odds"]))
            broke[k][1] += 1
            if won is False and r["status"] == "LOST":
                broke[k][0] += 1
    n = len(res)
    if not n:
        return {"n": 0}
    m = sum(x["pnl"] for x in res) / n
    se = math.sqrt(sum((x["pnl"] - m) ** 2 for x in res) / (n - 1)) / math.sqrt(n) if n > 1 else float("nan")
    worst = sorted(((k, v[0], v[1]) for k, v in broke.items() if v[1] >= 10), key=lambda x: -x[1] / x[2])[:3]
    return {"n": n, "vinte": sum(x["won"] for x in res) / n, "attese": sum(x["p"] for x in res) / n, "roi": m, "se": se,
            "gambe_fragili": [{"sport": k[0], "fascia": k[1], "rotte": a, "gambe": b} for k, a, b in worst]}


def load(store) -> list[dict]:
    return store.query(
        "SELECT s.ts, s.match_id, s.selection, s.odds, s.fair_prob, s.status, m.sport FROM shadow_bets s "
        "LEFT JOIN matches m ON m.match_id = s.match_id WHERE s.strategy_id=? AND s.selection NOT LIKE 'LAY:%' "
        "AND s.status IN ('WON','LOST','VOID') ORDER BY s.ts", (STRATEGY,))


def summary(store) -> dict:
    rows = load(store)
    singles = [r for r in rows if r["status"] in ("WON", "LOST")]
    n = len(singles)
    roi = None
    if n:
        pnl = [((r["odds"] - 1) * (1 - COMM) if r["status"] == "WON" else -1.0) for r in singles]
        roi = sum(pnl) / n
    return {"singole": {"n": n, "roi": roi}, "doppie": evaluate(rows, 2), "triple": evaluate(rows, 3)}


def text(s: dict) -> str:
    pct = lambda x: "n.d." if x is None else f"{x:+.1%}"
    L = ["Multiple virtuali (ombra, solo misura):",
         f"• singole: {s['singole']['n']} puntate, ROI {pct(s['singole']['roi'])}"]
    for k, name in (("doppie", "doppie"), ("triple", "triple")):
        d = s[k]
        if not d["n"]:
            L.append(f"• {name}: non ancora abbastanza puntate chiuse")
            continue
        L.append(f"• {name}: {d['n']} · vinte {d['vinte']:.0%} (attese {d['attese']:.0%}) · ROI {pct(d['roi'])} ± {2 * d['se']:.0%}")
        for g in d["gambe_fragili"]:
            L.append(f"   gamba che rompe più spesso: {g['sport']} quota {g['fascia']} ({g['rotte']} su {g['gambe']})")
    L.append("Una multipla moltiplica anche il costo: se le singole perdono l'1-3% in media, una doppia perde circa il doppio.")
    return "\n".join(L)
