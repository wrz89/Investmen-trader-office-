"""S05 v3 — come la v2, più due filtri sul movimento dei prezzi e un libro più profondo.

Cosa cambia dalla v2 (idee raccolte il 30/09/2026 da chi fa bot di scommesse, adattate a betfair.it):
  • quota giusta che si è mossa troppo: se la probabilità di riferimento dell'esito è cambiata più del 6% (relativo)
    da quando la strategia l'ha vista la prima volta, si salta. Un movimento così è una notizia: il "valore" su
    Betfair di solito è il nostro riferimento rimasto indietro (le cause "riferimento" e "notizia" di Leo);
  • prezzo Betfair che si è mosso troppo: stessa soglia sul prezzo back di betfair.it (sui libri sottili un prezzo
    che scappa vuol dire che qualcuno sa qualcosa, o che il libro è vuoto);
  • almeno 6 € al miglior prezzo (3 volte la puntata minima) invece di 3: meno prezzo peggiore sui libri sottili.
La soglia è sulla probabilità e non sulla quota: il "15% sulla quota" che si legge in giro, per un favorito a 1,20,
vorrebbe dire un salto di probabilità enorme che non succede quasi mai. Il 6% relativo, per un favorito all'80%,
è un movimento di circa 5 punti.

Lavora IN OMBRA accanto alla v2 (observe_strategies): si attiva solo se l'esame per il live dice che fa meglio.
"""
from __future__ import annotations

from datetime import datetime

from . import s05_favoriti_exchange_v2 as V2

STRATEGY_ID = "S05_favoriti_exchange_v3"
NAME = "Favoriti su exchange (filtro movimenti)"
KIND = "prematch"

DEFAULTS = {**V2.DEFAULTS, "min_book_eur": 6.0, "max_ref_drift": 0.06, "max_bf_drift": 0.06}

# prima lettura di ogni esito: (match_id, esito) → (istante, probabilità giusta, prezzo back, inizio)
_SEEN: dict[tuple[str, str], tuple[float, float, float, float]] = {}


def _drift(now_v: float, first_v: float) -> float:
    return abs(now_v / first_v - 1) if first_v else 0.0


def observe(snapshot: dict) -> None:
    """Annota la prima lettura di ogni favorito possibile (anche fuori dalla finestra d'ingresso: così il movimento si
    misura dalle ore prima, non dai 2 minuti precedenti)."""
    from ..odds import consensus
    now = snapshot.get("sim_time") or snapshot["ts"]
    for key in [k for k, v in _SEEN.items() if v[3] < now - 3600]:      # partite iniziate da più di un'ora
        del _SEEN[key]
    for m in snapshot["matches"].values():
        if m.get("status") != "SCHEDULED" or not m.get("exchange") or len(m.get("books") or {}) < 2:
            continue
        ko = datetime.fromisoformat(m["kickoff"]).timestamp()
        for sel, v in consensus(m["books"]).items():
            b = (m["exchange"].get(sel) or {}).get("back")
            if sel.startswith("_") or not b:
                continue
            first = _SEEN.get((m["match_id"], sel))
            if first is None or first[0] > now:                          # nuova, o replay ripartito da capo
                _SEEN[(m["match_id"], sel)] = (now, v["fair_prob"], 1 / b, ko)


def propose(snapshot: dict, params: dict, ctx: dict) -> list[dict]:
    q = {**DEFAULTS, **params}
    observe(snapshot)
    out = []
    for p in V2.propose(snapshot, {**q, "sports": params.get("sports") or {}}, ctx):
        first = _SEEN.get((p["match_id"], p["selection"]))
        if first:
            ref = _drift(p["fair_prob"], first[1])
            bf = _drift(1 / p["odds"], first[2])
            if ref > q["max_ref_drift"] or bf > q["max_bf_drift"]:
                continue
            p["reason"] += f"; movimenti dalla prima lettura: riferimento {ref:.1%}, Betfair {bf:.1%}"
        p["strategy_id"] = STRATEGY_ID
        out.append(p)
    return out
