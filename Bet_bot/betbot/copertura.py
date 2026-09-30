"""Copertura di The Odds API per il riferimento Pinnacle (serve a S05 e soprattutto a S09).

Una chiamata per campionato (1 credito ciascuna, regione 'eu', mercato 1X2): quante partite, in quante c'è
Pinnacle, e quanti crediti al giorno servirebbero con l'aggiornamento configurato. Il piano gratuito dà 500
crediti al mese, circa 16 al giorno.
"""
from __future__ import annotations

import math

import requests

from .config import api_key, load_settings
from .feeds.odds_api import BASE


def check(sport_keys: list[str] | None = None) -> dict:
    key = api_key()
    if not key:
        return {"error": "manca la chiave di The Odds API (Impostazioni della dashboard)"}
    s = load_settings()
    keys = sport_keys or [k for k in (s["feed"].get("sports") or []) if k.startswith(("soccer", "americanfootball"))]
    cfg = s["feed"].get("odds_api") or {}
    every_h = float(cfg.get("odds_refresh_seconds", 7200)) / 3600
    out, remaining = [], None
    for k in keys:
        try:
            r = requests.get(f"{BASE}/sports/{k}/odds", params={"apiKey": key, "regions": "eu", "markets": "h2h"}, timeout=20)
            remaining = r.headers.get("x-requests-remaining", remaining)
            if r.status_code != 200:
                out.append({"sport": k, "error": f"HTTP {r.status_code}"})
                continue
            ev = r.json()
            pin = sum(1 for e in ev if any(b.get("key") == "pinnacle" for b in e.get("bookmakers", [])))
            betfair = sum(1 for e in ev if any("betfair" in b.get("key", "") for b in e.get("bookmakers", [])))
            books = sorted({b.get("title") for e in ev for b in e.get("bookmakers", [])})
            out.append({"sport": k, "partite": len(ev), "con_pinnacle": pin, "con_betfair": betfair, "bookmaker": books})
        except requests.RequestException as exc:
            out.append({"sport": k, "error": type(exc).__name__})
    per_day = len(keys) * math.ceil(24 / max(every_h, 0.25))
    return {"campionati": out, "crediti_rimasti": remaining, "aggiornamento_ore": every_h,
            "crediti_al_giorno": per_day, "crediti_al_mese": per_day * 30,
            "campionati_col_gratuito": max(0, int(16 // math.ceil(24 / max(every_h, 0.25))))}


def report(res: dict) -> str:
    if res.get("error"):
        return f"Copertura non verificata: {res['error']}.\n"
    L = ["| Campionato | Partite in programma | Con Pinnacle | Con Betfair |", "|---|---|---|---|"]
    for c in res["campionati"]:
        if c.get("error"):
            L.append(f"| {c['sport']} | errore: {c['error']} | | |")
        else:
            L.append(f"| {c['sport']} | {c['partite']} | {c['con_pinnacle']} | {c['con_betfair']} |")
    L += ["", f"Con un aggiornamento ogni {res['aggiornamento_ore']:g} ore servono circa {res['crediti_al_giorno']} crediti al "
              f"giorno ({res['crediti_al_mese']} al mese). Il piano gratuito ne dà circa 16 al giorno: bastano per "
              f"{res['campionati_col_gratuito']} campionati. Crediti rimasti adesso: {res['crediti_rimasti'] or 'n.d.'}.",
          "Senza Pinnacle S09 usa il consenso di almeno 4 bookmaker: funziona, ma il segnale del backtest è stato misurato con Pinnacle."]
    return "\n".join(L) + "\n"
