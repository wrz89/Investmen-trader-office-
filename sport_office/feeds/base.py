"""Interfaccia comune dei feed.

Ogni feed produce, a ogni ciclo, uno SNAPSHOT con questa forma:

{
  "ts": <epoch>, "sim_time": <epoch della simulazione o = ts>, "health": {"error_rate": 0.0, "source": "mock"},
  "matches": {
     match_id: {"match_id", "sport", "league", "home", "away", "kickoff" (ISO),
                "status": "SCHEDULED" | "LIVE" | "FINISHED", "minute", "home_score", "away_score",
                "result": "home" | "draw" | "away" | None,
                "books": {bookmaker: {"home": q, "draw": q, "away": q}},      # quote pre-match (ultime)
                "live_books": {bookmaker: {...}},                              # quote in-play (se LIVE)
                "closing": {"home": q, ...} | None,                            # consenso "giusto" alla partenza
                "odds_ts": <epoch dell'ultima quota>}
  },
  "races": {
     market_id: {"market_id", "venue", "race", "start" (ISO), "status": "OPEN" | "INPLAY" | "CLOSED",
                 "seconds_to_off", "winner": runner_id | None,
                 "runners": {runner_id: {"name", "back", "lay", "back_size", "lay_size", "wom", "ltp"}}}
  }
}
"""
from __future__ import annotations


class FeedError(Exception):
    """Dati non disponibili o non affidabili: l'ufficio NON opera."""


class Feed:
    name = "base"

    def __init__(self, settings: dict):
        self.settings = settings

    async def fetch(self) -> dict:
        raise NotImplementedError

    def close(self) -> None:
        pass
