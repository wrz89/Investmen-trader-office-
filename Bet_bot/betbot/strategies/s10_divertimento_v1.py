"""S10 — Divertimento: poche puntate minime al giorno, al prezzo più "giusto" che c'è, su qualunque sport.

Non cerca un vantaggio (le analisi non ne hanno trovati di solidi): serve a puntare qualcosa ogni giorno spendendo
il meno possibile. In media perde circa la commissione e mezzo spread (1-3% della puntata); il resto è fortuna.
  • tutti gli sport che il feed Betfair legge (calcio, tennis, basket, football americano, baseball), pre-partita,
    da 4 ore a 10 minuti dall'inizio;
  • quote 1,40-3,00 (si vince tra 1 volta su 3 e 2 su 3: niente favoritissimi né colpi improbabili);
  • libro liquido: back e lay vicini (spread ≤ 3%) e almeno 10 € al miglior prezzo;
  • probabilità giusta da Pinnacle / bookmaker di riferimento se ci sono, altrimenti dal prezzo medio di Betfair
    stesso (margine tolto); si sceglie l'esito con il valore atteso migliore, purché non sotto −3% (la sola commissione a quota 1,80 vale già −2%);
  • tennis: esclusi Challenger, ITF, UTR e doppi (integrità).
Fino a 3 proposte per ciclo (partite diverse, la migliore prima). Puntata fissa al minimo (2 €), al massimo 10 al giorno e una aperta alla volta: lo decide il
Risk Manager (fun_* in risk_limits.yaml), con tutti gli altri freni (kill switch a 20 €, stop giornaliero).
"""
from __future__ import annotations

import re
from datetime import datetime

from ..odds import consensus
from .s05_favoriti_exchange_v1 import ev_net

STRATEGY_ID = "S10_divertimento_v1"
NAME = "Divertimento"
KIND = "prematch"

DEFAULTS = {"odds_min": 1.40, "odds_max": 3.00, "max_spread": 0.03, "min_book_eur": 10.0, "min_minutes_before": 10,
            "max_minutes_before": 240, "min_ev": -0.03, "commission": 0.045, "max_ref_age_s": 9000, "max_proposals": 3,
            "exclude": "challenger|itf|utr|m15|m25|w15|w35|w50|w75|w100|doppio|doubles"}
SPORTS = ("soccer", "tennis", "basketball", "americanfootball", "baseball", "icehockey", "volleyball", "darts", "snooker",
          "tabletennis")


def exchange_fair(ex: dict) -> dict | None:
    """Probabilità dal prezzo medio tra back e lay di ogni esito, normalizzate (serve il libro completo)."""
    mids = {}
    for sel, b in ex.items():
        if not b.get("back") or not b.get("lay") or b["lay"] < b["back"]:
            return None
        mids[sel] = (b["back"] + b["lay"]) / 2
    tot = sum(1 / v for v in mids.values())
    return {s: (1 / v) / tot for s, v in mids.items()} if mids else None


def _at_limit(store) -> bool:
    """Già una puntata aperta, o già quelle del giorno: non si propone nulla (niente veti a ogni ciclo)."""
    if store is None:
        return False
    from ..agents.risk import _day_start_iso
    from ..config import load_yaml
    lim = load_yaml("risk_limits.yaml")
    rows = store.query("SELECT status, ts FROM bets WHERE mode!='shadow' AND strategy_id=? AND (status='OPEN' OR ts >= ?)",
                       (STRATEGY_ID, _day_start_iso()))
    return (sum(1 for r in rows if r["status"] == "OPEN") >= lim.get("fun_max_open", 1)
            or sum(1 for r in rows if r["ts"] >= _day_start_iso()) >= lim.get("fun_max_bets_per_day", 10))


def propose(snapshot: dict, params: dict, ctx: dict) -> list[dict]:
    q = {**DEFAULTS, **params}
    if _at_limit((ctx or {}).get("store")):
        return []
    return candidates(snapshot, q)[:q["max_proposals"]]


def candidates(snapshot: dict, params: dict | None = None) -> list[dict]:
    """Tutte le puntate possibili (una per partita, la migliore prima): la usa anche l'anteprima."""
    q = {**DEFAULTS, **(params or {})}
    now = snapshot.get("sim_time") or snapshot["ts"]
    cands = []
    for m in snapshot["matches"].values():
        ex = m.get("exchange") or {}
        if m.get("status") != "SCHEDULED" or not ex or not (m.get("sport") or "").startswith(SPORTS):
            continue
        if (m.get("sport") or "").startswith("tennis") and re.search(q["exclude"], (m.get("league") or "").lower()):
            continue
        mins = (datetime.fromisoformat(m["kickoff"]).timestamp() - now) / 60
        if not (q["min_minutes_before"] <= mins <= q["max_minutes_before"]):
            continue
        books = m.get("books") or {}
        if m.get("ref_ts") and now - m["ref_ts"] > q["max_ref_age_s"]:
            books = {}                               # riferimento vecchio: il Risk Manager lo rifiuterebbe, si usa Betfair
        ref = {k: v for k, v in consensus(books).items() if not k.startswith("_")} if books else {}
        own = exchange_fair(ex)
        comm = m.get("commission") or q["commission"]
        for sel, b in ex.items():
            back, lay = b.get("back"), b.get("lay")
            if not back or not lay or not (q["odds_min"] <= back <= q["odds_max"]):
                continue
            spread = lay / back - 1
            if spread > q["max_spread"] or (b.get("back_size_best") or b.get("back_size") or 0) < q["min_book_eur"]:
                continue
            r = ref.get(sel)
            if r:
                fair, source, n_books, disp = r["fair_prob"], "riferimento", r["n_books"], r["dispersion"]
            elif own:
                fair, source, n_books, disp = own[sel], "exchange", 0, None
            else:
                continue
            ev = ev_net(fair, back, comm)
            if ev < q["min_ev"]:
                continue
            cands.append((ev, m, sel, back, fair, source, n_books, disp, spread, comm, mins, b, books))
    # le migliori di partite diverse: se il Risk Manager ne ferma una (Leo, sentiment, campionato) passa la successiva;
    # con una sola puntata aperta alla volta ne viene piazzata al massimo una
    out, seen = [], set()
    for c in sorted(cands, key=lambda c: -c[0]):
        if c[1]["match_id"] in seen:
            continue
        seen.add(c[1]["match_id"])
        out.append(_proposal(*c))
    return out


def _proposal(ev, m, sel, back, fair, source, n_books, disp, spread, comm, mins, b, books) -> dict:
    name = m["home"] if sel == "home" else m["away"] if sel == "away" else "Pareggio"
    pin_only = source == "riferimento" and n_books == 1 and any("pinnacle" in k.lower() for k in books)
    return {"strategy_id": STRATEGY_ID, "match_id": m["match_id"],
             "market_id": (m.get("betfair") or {}).get("market_id") or m["match_id"],
             "league": m.get("league"), "sport": m.get("sport"), "home": m["home"], "away": m["away"],
             "label": f"{m['home']} - {m['away']} · {name}", "market": "h2h", "selection": sel, "bookmaker": "Betfair",
             "odds": back, "fair_prob": fair, "edge": ev, "commission": comm, "n_books": n_books, "dispersion": disp,
             "ref_source": "Pinnacle" if pin_only else None, "prob_source": "exchange" if source == "exchange" else None,
             "spread": spread, "fun": True, "live": False, "odds_ts": m.get("odds_ts"), "kickoff": m["kickoff"],
             "book_eur": b.get("back_size_best") or b.get("back_size") or 0.0,
             "ref_ts": m.get("ref_ts") if source == "riferimento" else None,
             "reason": f"Divertimento: {name} a {back:.2f} ({m.get('league') or m.get('sport')}), probabilità giusta "
                       f"{fair:.0%} dal {source}, valore atteso {ev:+.1%}, spread {spread:.1%}, "
                       f"{(b.get('back_size_best') or b.get('back_size') or 0):.0f} € al prezzo, inizio tra {mins:.0f} min"}
