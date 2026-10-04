"""Puntate "aperte" nel bot: cosa dice Betfair di ciascuna (sola lettura, non cambia nulla).

Per ogni puntata vera ancora OPEN nel registro mostra: se il bot ha l'id Betfair, se l'ordine è ancora in corso su
Betfair, se risulta regolata (con esito e profitto) o annullata. Così si capisce se è il bot a non averla chiusa o se è
Betfair a non averla ancora regolata. NB: le puntate dell'exchange si vedono nella sezione "Exchange" di betfair.it
(Le mie scommesse / Mercati aperti), NON nella sezione "Sport" che è un'altra cosa (il bookmaker Betfair Sport).
"""
from __future__ import annotations

import json

from . import local_settings
from .config import DB_LIVE_PATH
from .store import Store


def check(store=None, client=None) -> list[dict]:
    store = store or Store(DB_LIVE_PATH)
    rows = store.query("SELECT id, ts, label, strategy_id, selection, odds, stake, extra FROM bets WHERE status='OPEN' AND mode='live' ORDER BY id")
    if client is None and rows:
        from .feeds.betfair import BetfairClient
        client = BetfairClient(local_settings.load()["betfair"])
        client.login()
    out = []
    for b in rows:
        ref = (json.loads(b["extra"]) if b["extra"] else {}).get("betfair") or {}
        r = {"id": b["id"], "label": b["label"], "ts": b["ts"], "selection": b["selection"], "stake": b["stake"],
             "bet_id": ref.get("bet_id"), "market_id": ref.get("market_id"), "stato": "?", "dettaglio": ""}
        if not r["bet_id"]:
            r.update(stato="SENZA ID BETFAIR", dettaglio="il bot non ha l'id dell'ordine: non può regolarla da solo")
        else:
            try:
                cur = [o for o in client.current_orders(market_ids=[r["market_id"]]) if o.get("betId") == r["bet_id"]] if r["market_id"] else []
                if cur:
                    r.update(stato="ANCORA IN CORSO su Betfair", dettaglio=f"stato {cur[0].get('status')}, abbinato {cur[0].get('sizeMatched')} €")
                else:
                    c = client.cleared([r["bet_id"]]).get(r["bet_id"])
                    if c:
                        r.update(stato="REGOLATA su Betfair", dettaglio=f"{c['status']} · esito {c['outcome']} · profitto {c['profit']:+.2f} € "
                                 "(il bot la chiude al prossimo ciclo)")
                    else:
                        r.update(stato="NON TROVATA su Betfair", dettaglio="né in corso né regolata: controlla il conto a mano")
            except Exception as exc:
                r.update(stato="ERRORE", dettaglio=str(exc))
        out.append(r)
    return out


def run(out=print) -> int:
    try:
        rows = check()
    except Exception as exc:
        out(f"Non riesco a leggere Betfair: {exc}")
        return 1
    if not rows:
        out("Nessuna puntata vera aperta nel registro del bot.")
        return 0
    out(f"Puntate vere aperte nel bot: {len(rows)}\n")
    for r in rows:
        out(f"#{r['id']} {r['label']} ({r['selection']}, {r['stake']:.2f} €) → {r['stato']}")
        if r["dettaglio"]:
            out("    " + r["dettaglio"])
    out("\nLe puntate dell'exchange si vedono su betfair.it nella sezione EXCHANGE (Le mie scommesse), non in 'Sport'.")
    return 0
