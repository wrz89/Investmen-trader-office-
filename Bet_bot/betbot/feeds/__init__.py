"""Feed dati: quote (pre-partita e live), punteggi e statistiche, exchange cavalli.

Composizione configurabile in config/sport/settings.yaml → feed:
  provider   mock | odds_api | betfair       fonte principale di partite e quote
  live_stats none | api_football             minuto, punteggio e statistiche live (calcio)
  exchange   none | betfair                  corse di cavalli dall'exchange (se provider ≠ betfair)
"""
from __future__ import annotations

import asyncio

from .base import Feed, FeedError


class CompositeFeed(Feed):
    """Unisce la fonte principale con statistiche live ed exchange. Un errore della
    fonte principale ferma il ciclo (nessuna puntata); quello di una fonte accessoria
    viene solo annotato nello stato di salute."""
    name = "composite"

    def __init__(self, settings: dict, primary: Feed, stats=None, exchange: Feed | None = None):
        super().__init__(settings)
        self.primary, self.stats, self.exchange = primary, stats, exchange
        self.speed = getattr(primary, "speed", 1.0)

    def now(self) -> float:
        return self.primary.now() if hasattr(self.primary, "now") else __import__("time").time()

    async def fetch(self) -> dict:
        snap = await self.primary.fetch()
        notes = []
        if self.stats is not None:
            try:
                await asyncio.to_thread(self.stats.refresh)
                n = self.stats.enrich(snap["matches"])
                notes.append(f"statistiche live su {n} partite")
            except FeedError as exc:
                notes.append(f"statistiche live non disponibili ({exc})")
        if self.exchange is not None:
            try:
                ex = await self.exchange.fetch()
                snap.setdefault("races", {}).update(ex.get("races", {}))
                notes.append(f"{len(ex.get('races', {}))} corse dall'exchange")
            except FeedError as exc:
                notes.append(f"exchange non disponibile ({exc})")
        if notes:
            snap["health"]["source"] += " · " + ", ".join(notes)
        return snap


def make_feed(settings: dict) -> Feed:
    f = settings["feed"]
    provider = f["provider"]
    if provider == "mock":
        from .mock import MockFeed
        primary = MockFeed(settings)
    elif provider == "odds_api":
        from .odds_api import OddsApiFeed
        primary = OddsApiFeed(settings)
    elif provider == "betfair":
        from .betfair import BetfairFeed
        primary = BetfairFeed(settings)
    else:
        raise ValueError(f"feed sconosciuto: {provider}")
    stats = exchange = None
    if f.get("live_stats") == "api_football" and provider != "mock":
        from .. import local_settings
        from .api_football import ApiFootball
        stats = ApiFootball(local_settings.load().get("api_football_key") or "", f.get("api_football"))
    if f.get("exchange") == "betfair" and provider != "betfair":
        from .betfair import BetfairFeed
        exchange = BetfairFeed({**settings, "feed": {**f, "betfair": {**(f.get("betfair") or {}), "soccer": False}}})
    if stats is None and exchange is None:
        return primary
    return CompositeFeed(settings, primary, stats, exchange)
