"""S10 misura — la logica di S10 v2 SOLO IN OMBRA, su tutte le partite adatte (nessun limite al giorno).

Serve a raccogliere il campione in fretta: l'esame per il live vuole 200 puntate chiuse sui prezzi veri di betfair.it
e i soldi veri di S10 ne fanno al massimo 10 al giorno. In ombra non c'è capitale in gioco, ma i prezzi sono quelli
veri e Leo ne fa l'autopsia (CLV contro la chiusura): con decine di partite al giorno il verdetto arriva in settimane,
non in mesi. Non va MAI in live_strategies: è uno strumento di misura.
"""
from __future__ import annotations

from . import s10_divertimento_v2 as V2

STRATEGY_ID = "S10_misura_v1"
NAME = "Misura del divertimento (ombra)"
KIND = "prematch"

# la misura è LARGA di proposito (nessun rischio: è in ombra): lay fino a quota 8 e anche con Pinnacle vecchio fino a
# 2,5 ore, così il campione dei lay cresce. Leo separa i casi per età del riferimento (segmento "eta_riferimento") e
# l'esame ha la riga "solo lay con Pinnacle fresco": si vede se il valore con Pinnacle vecchio è falso.
DEFAULTS = {**V2.DEFAULTS, "max_proposals": 60, "lay_max": 8.0, "lay_max_ref_age_s": 9000}


def propose(snapshot: dict, params: dict, ctx: dict) -> list[dict]:
    q = {**DEFAULTS, **params}
    out = []
    for p in V2.candidates(snapshot, q)[:q["max_proposals"]]:
        p = dict(p, strategy_id=STRATEGY_ID)
        p.pop("fun", None)                      # niente regole "divertimento": è solo una misura in ombra
        out.append(p)
    return out
