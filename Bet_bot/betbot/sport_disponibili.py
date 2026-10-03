"""Quali sport ha davvero betfair.it sul TUO conto, con quante partite (Match Odds) e se Bet_bot già li legge.

Sola lettura: login con il certificato, `listEventTypes` e `listMarketTypes` (nessun ordine, nessun credito di The Odds
API). Per ogni sport dice quanti mercati "Match Odds" ci sono e quanti esiti hanno (2 = testa a testa, 3 = col pareggio),
e stampa le righe da incollare in runtime/impostazioni.yaml per gli sport che non sono ancora nell'elenco.
"""
from __future__ import annotations

import re

from . import local_settings
from .feeds.betfair import SPORTS, BetfairClient, sports_table


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())[:20] or "sport"


def check(client=None, settings: dict | None = None) -> list[dict]:
    from .config import load_settings
    st = settings or load_settings()
    bf = ((st.get("feed") or {}).get("betfair")) or {}
    known = sports_table(bf.get("extra_sports"))
    by_id = {v[0]: k for k, v in known.items()}
    if client is None:
        client = BetfairClient(local_settings.load()["betfair"])
        client.login()
    rows = []
    for tid, name in sorted(client.event_types().items(), key=lambda kv: kv[1]):
        try:
            types = client.market_types(tid)
        except Exception as exc:
            rows.append({"id": tid, "nome": name, "errore": str(exc)})
            continue
        n = types.get("MATCH_ODDS", 0)
        esiti = None
        if n:
            try:
                cat = client.catalogue(tid, "MATCH_ODDS", 168, None, 1, lookback_hours=0)
                esiti = len(cat[0].get("runners", [])) if cat else None
            except Exception:
                esiti = None
        rows.append({"id": tid, "nome": name, "match_odds": n, "esiti": esiti, "chiave": by_id.get(tid),
                     "letto": tid in by_id and by_id[tid] in (bf.get("sports") or [])})
    return rows


def report(rows: list[dict]) -> str:
    L = ["Sport su betfair.it (mercati Match Odds aperti ora):", ""]
    todo = []
    for r in sorted(rows, key=lambda r: -(r.get("match_odds") or 0)):
        if r.get("errore"):
            L.append(f"• {r['nome']} (id {r['id']}): non leggibile ({r['errore']})")
            continue
        if not r["match_odds"]:
            continue
        stato = "LETTO da Bet_bot" if r["letto"] else ("noto ma non attivo in `sports`" if r["chiave"] else "NON ancora letto")
        L.append(f"• {r['nome']} (id {r['id']}): {r['match_odds']} mercati, {r['esiti'] or '?'} esiti · {stato}")
        if not r["chiave"] and r["esiti"] in (2, 3):
            todo.append(r)
    nuovi = [r for r in rows if r.get("chiave") and not r.get("letto") and r.get("match_odds")]
    if todo or nuovi:
        L += ["", "Per far leggere (registrare e misurare in ombra) gli sport che mancano, in runtime/impostazioni.yaml:", "",
              "feed:", "  betfair:"]
        if nuovi:
            L.append("    sports: [" + ", ".join(sorted(set(["soccer"] + [r["chiave"] for r in nuovi] +
                                                          [k for k in SPORTS if k != "soccer"]))) + "]")
        if todo:
            L.append("    extra_sports:")
            for r in todo:
                L.append(f"      {_key(r['nome'])}: {{id: \"{r['id']}\", esiti: {r['esiti']}, nome: \"{r['nome']}\"}}")
            L.append("    # poi aggiungi le chiavi di extra_sports anche all'elenco `sports`")
    return "\n".join(L)


def run(out=print) -> int:
    try:
        rows = check()
    except Exception as exc:
        out(f"Non riesco a leggere gli sport: {exc}. Controlla prima collega_betfair.bat.")
        return 1
    out(report(rows))
    return 0
