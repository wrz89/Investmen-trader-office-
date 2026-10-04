"""Feed dati di Bet_bot.

Composizione (config/settings.yaml → feed):
  provider   mock | betfair           partite e corse CON il book dell'exchange (dove si punta)
  reference  none | odds_api          quote di riferimento (Pinnacle & co.) per stimare la probabilità giusta
  live_stats none | api_football      minuto, punteggio e statistiche live (calcio)

Il bot punta solo su Betfair. Le quote di riferimento servono a capire se il prezzo Betfair è
"buono": vengono abbinate alle partite Betfair per nome delle squadre e orario d'inizio.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime

from .base import Feed, FeedError

REFERENCE_EXCLUDE = ("betfair", "matchbook", "smarkets", "betdaq")     # exchange: non sono un "metro" esterno


def _needs_fresh_reference(snap: dict, ref_ts: float, max_age_s: float = 1800, window_s: float = 7200,
                           odds_min: float = 1.10, odds_max: float = 1.40) -> bool:
    """True se una partita Betfair inizia entro 2 ore con un favorito in fascia e il riferimento ha più di 30 minuti."""
    now = snap.get("sim_time") or snap["ts"]
    if now - (ref_ts or 0) < max_age_s:
        return False
    for m in snap.get("matches", {}).values():
        if m.get("status") != "SCHEDULED" or not m.get("exchange"):
            continue
        if not 0 <= datetime.fromisoformat(m["kickoff"]).timestamp() - now <= window_s:
            continue
        if any(odds_min <= (b.get("back") or 0) <= odds_max for b in m["exchange"].values()):
            return True
    return False


# campionato di Betfair → chiave di The Odds API (Pinnacle ce l'ha). Chiave sbagliata = 404, e la si smette di provare.
LEAGUE_KEYS = [
    (r"italian serie a|^serie a$", "soccer_italy_serie_a"), (r"italian serie b", "soccer_italy_serie_b"),
    (r"english premier|^premier league$", "soccer_epl"), (r"english championship", "soccer_efl_champ"),
    (r"spanish la liga|^la liga$", "soccer_spain_la_liga"), (r"spanish segunda|la liga 2", "soccer_spain_segunda_division"),
    (r"german bundesliga$|^bundesliga$", "soccer_germany_bundesliga"), (r"german bundesliga 2|2\. bundesliga", "soccer_germany_bundesliga2"),
    (r"french ligue 1|^ligue 1$", "soccer_france_ligue_one"), (r"dutch eredivisie|^eredivisie$", "soccer_netherlands_eredivisie"),
    (r"portuguese primeira|primeira liga", "soccer_portugal_primeira_liga"),
    (r"champions league", "soccer_uefa_champs_league"), (r"europa league", "soccer_uefa_europa_league"),
    (r"conference league", "soccer_uefa_europa_conference_league"), (r"scottish premiership", "soccer_spl"),
    (r"turkish super", "soccer_turkey_super_league"), (r"belgian first|jupiler", "soccer_belgium_first_div"),
]


def league_key(league: str | None) -> str | None:
    import re
    t = (league or "").lower()
    return next((k for rx, k in LEAGUE_KEYS if re.search(rx, t)), None)


def lay_reference_needs(snap: dict, max_age_s: float = 2400, min_back: float = 2.9, max_back: float = 5.3,
                        window_s: tuple[float, float] = (1200, 28800)) -> set[str]:
    """Campionati di calcio in cui c'è un possibile lay (esito a quota 2,9-5,3, libro stretto, inizio tra 20 minuti e
    8 ore) e il riferimento Pinnacle manca o ha più di 40 minuti. Il lay di valore esiste solo contro un Pinnacle
    fresco: prima di questa regola i 15 crediti al giorno si spendevano a tappeto e Pinnacle era quasi sempre vecchio."""
    now = snap.get("sim_time") or snap["ts"]
    keys = set()
    for m in snap.get("matches", {}).values():
        if m.get("status") != "SCHEDULED" or not m.get("exchange") or not str(m.get("sport", "")).startswith("soccer"):
            continue
        if not window_s[0] <= datetime.fromisoformat(m["kickoff"]).timestamp() - now <= window_s[1]:
            continue
        if m.get("ref_ts") and now - m["ref_ts"] <= max_age_s:
            continue
        if any(min_back <= (b.get("back") or 0) <= max_back and (b.get("lay") or 9) / (b.get("back") or 1) - 1 <= 0.05
               for b in m["exchange"].values()):
            k = league_key(m.get("league"))
            if k:
                keys.add(k)
    return keys


def merge_reference(matches: dict, ref_matches: dict, max_kickoff_gap_h: float = 3.0) -> int:
    """Copia su ogni partita dell'exchange le quote dei bookmaker di riferimento della stessa partita."""
    from .api_football import same_team
    n = 0
    for m in matches.values():
        ko = datetime.fromisoformat(m["kickoff"]).timestamp()
        for r in ref_matches.values():
            if abs(datetime.fromisoformat(r["kickoff"]).timestamp() - ko) > max_kickoff_gap_h * 3600:
                continue
            if not (same_team(m["home"], r["home"]) and same_team(m["away"], r["away"])):
                continue
            keep = lambda books: {b: q for b, q in (books or {}).items() if not any(x in b.lower() for x in REFERENCE_EXCLUDE)}
            m["books"], m["live_books"] = keep(r.get("books")), keep(r.get("live_books"))
            m["closing"] = m.get("closing") or r.get("closing")
            if m.get("home_score") is None and r.get("home_score") is not None:
                m["home_score"], m["away_score"] = r["home_score"], r["away_score"]
            if r.get("result") and not m.get("result"):
                m["result"] = r["result"]
            m["ref_ts"] = r.get("odds_ts")          # età del riferimento, controllata a parte dal Risk Manager
            n += 1
            break
    return n


class CompositeFeed(Feed):
    """Unisce la fonte principale con riferimento e statistiche. Un errore della fonte principale ferma il
    ciclo (nessuna puntata); quello di una fonte accessoria viene solo annotato."""
    name = "composite"

    def __init__(self, settings: dict, primary: Feed, stats=None, reference: Feed | None = None):
        super().__init__(settings)
        self.primary, self.stats, self.reference = primary, stats, reference
        self.speed = getattr(primary, "speed", 1.0)

    def now(self) -> float:
        return self.primary.now() if hasattr(self.primary, "now") else time.time()

    async def fetch(self) -> dict:
        snap = await self.primary.fetch()
        notes = []
        if self.reference is not None:
            if _needs_fresh_reference(snap, getattr(self.reference, "odds_ts", 0.0)):
                self.reference.force = True                      # c'è un candidato vicino all'inizio: riferimento su richiesta
            if hasattr(self.reference, "request"):
                self.reference.request(lay_reference_needs(snap))     # riferimento fresco solo dove c'è un lay da verificare
            try:
                ref = await self.reference.fetch()
                n = merge_reference(snap["matches"], ref["matches"])
                notes.append(f"riferimento su {n} partite")
            except FeedError as exc:
                notes.append(f"quote di riferimento non disponibili ({exc})")
        if self.stats is not None:
            try:
                await asyncio.to_thread(self.stats.refresh)
                n = self.stats.enrich(snap["matches"])
                notes.append(f"statistiche live su {n} partite")
            except FeedError as exc:
                notes.append(f"statistiche live non disponibili ({exc})")
        if notes:
            snap["health"]["source"] += " · " + ", ".join(notes)
        return snap


def make_feed(settings: dict) -> Feed:
    f = settings["feed"]
    provider = f["provider"]
    if provider == "mock":
        from .mock import MockFeed
        return MockFeed(settings)
    if provider != "betfair":
        raise ValueError(f"feed sconosciuto: {provider} (Bet_bot usa 'betfair' oppure 'mock')")
    from .betfair import BetfairFeed
    primary = BetfairFeed(settings)
    reference = stats = None
    if f.get("reference") == "odds_api":
        from .odds_api import OddsApiFeed
        reference = OddsApiFeed(settings)
    if f.get("live_stats") == "api_football":
        from .. import local_settings
        from .api_football import ApiFootball
        stats = ApiFootball(local_settings.load().get("api_football_key") or "", f.get("api_football"))
    if stats is None and reference is None:
        return primary
    return CompositeFeed(settings, primary, stats, reference)
