"""Anteprima: su cosa punterebbe adesso S10 (Divertimento), con i prezzi VERI di betfair.it. Non punta nulla.

Legge una fotografia del mercato come fa il bot (Betfair + Pinnacle se configurato: 1 credito di The Odds API per
sport in stagione), mette in fila le scelte possibili dalla migliore e fa i conti di cosa succede aprendone
1, 2 o 3 insieme: guadagno atteso, probabilità di perderle tutte, perdita massima, stop giornaliero.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from .bankroll import TZ
from .config import load_settings, load_yaml

STAKE = 2.0
SPORT_IT = {"soccer": "Calcio", "tennis": "Tennis", "basketball": "Basket", "americanfootball": "Football am.",
            "baseball": "Baseball"}


def _leg(c: dict, comm: float) -> tuple[float, float, float]:
    """(rischio, probabilità di vincere, vincita netta) di una scelta: back da 2 € o lay con 0,50 € del backer."""
    if c.get("side") == "LAY":
        backer = 0.5
        return backer * (c["odds"] - 1), 1 - c["fair_prob"], backer * (1 - comm)
    return STAKE, c["fair_prob"], STAKE * (c["odds"] - 1) * (1 - comm)


def together(cands: list[dict], k: int, comm: float = 0.045) -> dict:
    """Le prime k scelte aperte insieme (partite diverse, esiti indipendenti)."""
    legs = [_leg(c, comm) for c in cands[:k]]
    ev = sum(w * win - (1 - w) * risk for risk, w, win in legs)
    p_all_lost = p_all_won = 1.0
    for risk, w, win in legs:
        p_all_lost *= 1 - w
        p_all_won *= w
    return {"k": len(legs), "ev": ev, "p_all_lost": p_all_lost, "p_at_least_one_lost": 1 - p_all_won,
            "worst": -sum(r for r, _, _ in legs), "best": sum(win for _, _, win in legs)}


def report(cands: list[dict], bankroll: float | None, limits: dict, now: float, top: int = 10) -> str:
    L = ["Su cosa punterebbe ADESSO la strategia 4fun (nessuna puntata viene fatta):", ""]
    if not cands:
        L.append("Nessuna scelta adatta in questo momento (quote 1,40-3,00, libro stretto e liquido, inizio tra 10 minuti "
                 "e 4 ore). Riprova più tardi: la sera e nei weekend ce ne sono di più.")
        return "\n".join(L)
    L.append(f"{'#':>2}  {'Inizio':6}  {'Sport / campionato':26}  {'Scelta':32}  {'Quota':>5}  {'Vince':>5}  "
             f"{'Fonte':10}  {'Valore':>6}  {'€ al prezzo':>11}  Tipo")
    for i, c in enumerate(cands[:top], 1):
        ko = datetime.fromisoformat(c["kickoff"]).astimezone(TZ)
        src = "Betfair" if c.get("prob_source") == "exchange" else "Pinnacle" if "inn" in str(c.get("ref_source") or "Pinnacle") else "consenso"
        sport = SPORT_IT.get((c.get("sport") or "").split("_")[0], c.get("sport") or "")
        league = f"{sport} / {c.get('league') or ''}"[:26]
        risk, w, _ = _leg(c, 0.045)
        kind = f"LAY rischio {risk:.2f} €" if c.get("side") == "LAY" else "back 2 €"
        L.append(f"{i:>2}  {ko:%H:%M}   {league:26}  {c['label'][:32]:32}  {c['odds']:5.2f}  {w:5.0%}  "
                 f"{src:10}  {c['edge']:+6.1%}  {c['book_eur']:9.0f} €  {kind}")
    L += ["", "Valore = guadagno atteso per euro puntato, commissione compresa (negativo = in media si perde un po').", "",
          "Vince = probabilità che la puntata vinca (per un LAY: che l'esito NON succeda). I LAY di valore vengono prima.", "",
          "Se ne aprissi più di una INSIEME (le migliori della lista):"]
    for k in (1, 2, 3):
        if k > len(cands):
            break
        t = together(cands, k)
        L.append(f"  • {k} insieme: atteso {t['ev']:+.2f} € · almeno una persa {t['p_at_least_one_lost']:.0%} · "
                 f"tutte perse {t['p_all_lost']:.0%} ({t['worst']:.0f} €) · tutte vinte {t['best']:+.2f} €")
    stop = limits.get("max_daily_loss_eur", 6)
    kill = limits.get("kill_below_bankroll", 20)
    L += ["", f"Freni: stop per il giorno dopo {stop:.0f} € persi, bot fermo sotto {kill:.0f} € di saldo"
              + (f" (oggi {bankroll:.2f} €)." if bankroll is not None else ".")]
    return "\n".join(L)


def run(out=print) -> int:
    from .feeds import make_feed
    from .strategies import s10_divertimento_v2 as S10
    settings = load_settings()
    if settings["feed"]["provider"] != "betfair":
        settings["feed"]["provider"] = "betfair"          # l'anteprima guarda sempre i prezzi veri
    feed = make_feed(settings)
    snap = asyncio.run(feed.fetch())
    params = load_yaml("strategies.yaml").get(S10.STRATEGY_ID) or {}
    cands = S10.candidates(snap, params)
    bankroll = None
    try:
        client = getattr(feed, "client", None) or getattr(getattr(feed, "primary", None), "client", None)
        if client is not None:
            bankroll = float(client.account_funds().get("availableToBetBalance") or 0)
    except Exception:
        pass
    out(report(cands, bankroll, load_yaml("risk_limits.yaml"), snap.get("sim_time") or snap["ts"]))
    return 0
