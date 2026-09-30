"""S09 v1 — Lay di valore sul calcio: si punta CONTRO un esito che su Betfair costa troppo poco.

Idea (backtest del 30/09/2026 su 16 campionati, stagioni 2024/25 e 2025/26, prezzi Betfair Exchange):
quando il prezzo lay di Betfair è PIÙ BASSO della quota giusta di Pinnacle (senza margine, presa nello
stesso momento), bancare quell'esito ha valore atteso positivo. Con la commissione del 4,5%, il lay stimato 2 tick
sopra il back e SCARTATI i record con prezzi Betfair incoerenti (10,8%): quote 3-8 → 95 lay, 79% vinte,
ROI +5,8% ± 5,7% sul rischio. Positivo ma non significativo: la prima stima (+7%) era gonfiata dai record rotti.
Il lay vince quando l'esito NON succede: per questo la percentuale di vincita è alta con le quote alte.

Rischio: si perde (quota lay − 1) × puntata del backer se l'esito succede. La fascia di quota predefinita
(3-8) tiene la perdita massima piccola rispetto a 30 € e la percentuale di vincita intorno all'80%.
Non ancora provata sul mercato italiano (liquidità separata, spread più larghi): parte IN OSSERVAZIONE e il
bot non la manda mai ai soldi veri finché il lay d'apertura non è supportato dall'esecuzione.
"""
from __future__ import annotations

from datetime import datetime

from ..odds import consensus, remove_margin

STRATEGY_ID = "S09_lay_valore_v1"
NAME = "Lay di valore (calcio)"
KIND = "prematch"

DEFAULTS = {
    "min_edge": 0.02,            # EV netto ≥ 2% del RISCHIO (responsabilità del lay)
    "commission": 0.045,
    "lay_min": 3.0, "lay_max": 8.0,
    "backer_stake": 0.50,        # minimo di betfair.it per un lay
    "min_lay_eur": 2.0,          # liquidità minima al miglior prezzo lay
    "max_minutes_before": 2880, "min_minutes_before": 15,
    "reference": "Pinnacle",     # riferimento sharp; senza Pinnacle serve il consenso di almeno min_books
    "min_books": 4,
}


def lay_ev(p: float, lay: float, commission: float) -> float:
    """Valore atteso per 1 € di puntata del backer: si incassa (1−c) se l'esito non succede, si paga (lay−1) se succede."""
    return (1 - p) * (1 - commission) - p * (lay - 1)


def fair_probs(m: dict, q: dict) -> tuple[dict | None, str | None, dict]:
    """(probabilità giuste, fonte, dispersione per selezione)."""
    books = m.get("books") or {}
    ref = books.get(q["reference"])
    if ref and len(ref) >= 3:
        return remove_margin(ref), q["reference"], {}
    if len(books) >= q["min_books"]:
        c = consensus(books)
        sels = {s: v for s, v in c.items() if not s.startswith("_")}
        return ({s: v["fair_prob"] for s, v in sels.items()}, f"consenso di {len(books)} book",
                {s: v.get("dispersion") for s, v in sels.items()})
    return None, None, {}


def propose(snapshot: dict, params: dict, ctx: dict) -> list[dict]:
    q = {**DEFAULTS, **params}
    now = snapshot.get("sim_time") or snapshot["ts"]
    out = []
    for m in snapshot["matches"].values():
        if not (m.get("sport") or "").startswith("soccer") or m["status"] != "SCHEDULED":
            continue
        ex = m.get("exchange") or {}
        if not ex:
            continue
        mins = (datetime.fromisoformat(m["kickoff"]).timestamp() - now) / 60
        if not (q["min_minutes_before"] <= mins <= q["max_minutes_before"]):
            continue
        probs, source, disp = fair_probs(m, q)
        if not probs or len(probs) != 3:
            continue
        comm = m.get("commission") or q["commission"]
        best = None
        for sel, p in probs.items():
            b = ex.get(sel) or {}
            lay = b.get("lay")
            if not lay or not (q["lay_min"] <= lay <= q["lay_max"]):
                continue
            if (b.get("lay_size_best") or b.get("lay_size") or 0) < q["min_lay_eur"]:
                continue
            ev = lay_ev(p, lay, comm)
            edge = ev / (lay - 1)                           # rendimento atteso sul rischio
            if edge >= q["min_edge"] and (best is None or edge > best[2]):
                best = (sel, lay, edge, p)
        if not best:
            continue
        sel, lay, edge, p = best
        name = m["home"] if sel == "home" else m["away"] if sel == "away" else "Pareggio"
        out.append({"strategy_id": STRATEGY_ID, "side": "LAY", "match_id": m["match_id"],
                    "market_id": (m.get("betfair") or {}).get("market_id") or m["match_id"],
                    "league": m["league"], "sport": m.get("sport"), "home": m["home"], "away": m["away"],
                    "label": f"{m['home']} - {m['away']} · CONTRO {name}", "market": "lay_h2h", "selection": f"LAY:{sel}",
                    "bookmaker": "Betfair", "odds": lay, "fair_prob": p, "edge": edge, "commission": comm,
                    "n_books": len(m.get("books") or {}), "dispersion": 0.0 if source == q["reference"] else disp.get(sel),
                    "ref_source": source, "live": False,
                    "odds_ts": m.get("odds_ts"), "ref_ts": m.get("ref_ts"),
                    "reason": f"Contro {name}: probabilità giusta {p:.0%} ({source}), lay Betfair {lay:.2f}; "
                              f"vince il {1 - p:.0%} delle volte, rischio {(lay - 1) * q['backer_stake']:.2f} € "
                              f"per {q['backer_stake']:.2f} € di incasso, EV sul rischio {edge:+.1%}, "
                              f"inizio tra {mins:.0f} min"})
    return out
