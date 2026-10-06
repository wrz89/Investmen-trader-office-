"""Riferimento Pinnacle di scorta da OddsPapi (piano gratuito, chiave v4).

Si usa SOLO dove The Odds API non ha dato un Pinnacle fresco (crediti finiti, tetto giornaliero o intervallo minimo):
`CompositeFeed` richiede i campionati che hanno ancora un lay possibile senza riferimento fresco, e qui si scarica
Pinnacle (1 chiamata per campionato, più i nomi delle squadre, tenuti 6 ore). Stessi freni dei crediti di The Odds API:
intervallo minimo, tetto al giorno per campionato e tetto di richieste al giorno; il tetto mensile è in oddspapi.py.

Limite dichiarato: OddsPapi non dice quando Pinnacle ha *verificato* il prezzo (changedAt è l'ultimo cambio), quindi
l'età del riferimento è quella dello scarico, non del prezzo. Per questo ogni partita porta `ref_src: "oddspapi"`.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone

from .. import oddspapi as OP
from ..config import oddspapi_key
from .base import Feed, FeedError

# chiave di The Odds API (vedi feeds.LEAGUE_KEYS) → (nome del campionato, paese) su OddsPapi
TOURNAMENTS = {
    "soccer_italy_serie_a": ("serie a", "italy"), "soccer_italy_serie_b": ("serie b", "italy"),
    "soccer_epl": ("premier league", "england"), "soccer_efl_champ": ("championship", "england"),
    "soccer_spain_la_liga": ("laliga", "spain"), "soccer_germany_bundesliga": ("bundesliga", "germany"),
    "soccer_france_ligue_one": ("ligue 1", "france"), "soccer_netherlands_eredivisie": ("eredivisie", "netherlands"),
    "soccer_portugal_primeira_liga": ("liga portugal", "portugal"),
}


class OddsPapiRefFeed(Feed):
    name = "oddspapi"

    def __init__(self, settings: dict):
        super().__init__(settings)
        key = oddspapi_key()
        if not key:
            raise FeedError("Manca la chiave di OddsPapi (oddspapi.bat la chiede e la salva).")
        cfg = (settings["feed"].get("oddspapi") or {})
        self.client = OP.OddsPapiClient(key)
        self.min_gap = float(cfg.get("min_gap_seconds", 2400))
        self.max_per_league_day = int(cfg.get("max_per_league_day", 4))
        self.max_requests_day = int(cfg.get("max_requests_per_day", 8))
        self.matches: dict[str, dict] = {}
        self.pending: set[str] = set()
        self.key_ts: dict[str, float] = {}
        self.key_day: dict[str, tuple[str, int]] = {}
        self.req_day = ("", 0)
        self.tour_ids: dict[str, str] = {}
        self.tour_ts = 0.0
        self.names: dict[str, tuple[float, dict]] = {}       # tournamentId → (ts, {fixtureId: (casa, trasferta)})
        self.odds_ts = 0.0
        self.note = ""
        self.ids = {"1": "101", "X": "102", "2": "103", "market": "101"}

    def request(self, keys) -> None:
        self.pending |= {k for k in keys if k in TOURNAMENTS}

    def _spend(self, n: int = 1) -> bool:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        day, used = self.req_day
        if day != today:
            day, used = today, 0
        if used + n > self.max_requests_day:
            self.note = f"tetto di {self.max_requests_day} richieste al giorno raggiunto"
            return False
        self.req_day = (day, used + n)
        return True

    def _tournament_ids(self, now: float) -> None:
        if self.tour_ids and now - self.tour_ts < 86400:
            return
        if not self._spend():
            return
        found = {}
        for t in OP.items(self.client.get("/tournaments", sportId=OP.SOCCER)):
            name = str(t.get("tournamentName") or "").lower()
            cat = str(t.get("categoryName") or "").lower()
            for k, (n, c) in TOURNAMENTS.items():
                if name == n and cat == c:
                    found[k] = str(t.get("tournamentId"))
        self.tour_ids, self.tour_ts = found, now

    def _refresh(self, key: str, now: float) -> None:
        tid = self.tour_ids.get(key)
        if not tid:
            return
        ts, names = self.names.get(tid, (0.0, {}))
        if now - ts > 6 * 3600 or not names:
            if not self._spend():
                return
            names = {str(f.get("fixtureId")): (f.get("participant1Name"), f.get("participant2Name"))
                     for f in OP.items(self.client.get("/fixtures", tournamentId=tid))}
            self.names[tid] = (now, names)
        if not self._spend():
            return
        league = f"{TOURNAMENTS[key][1].title()} {TOURNAMENTS[key][0].title()}"
        for fx in OP.items(self.client.get("/odds-by-tournaments", bookmaker="pinnacle", tournamentIds=tid)):
            fid = str(fx.get("fixtureId"))
            home, away = names.get(fid, (None, None))
            start = OP._ts(fx.get("startTime"))
            row = (fx.get("bookmakerOdds") or {}).get("pinnacle")
            if not (home and away and start and row and start > now):
                continue
            prices = {}
            for sel, lab in (("home", "1"), ("draw", "X"), ("away", "2")):
                cell = OP._price_cell(row, self.ids["market"], self.ids[lab])
                if not isinstance(cell, dict) or not cell.get("price") or cell.get("active") is False:
                    prices = {}
                    break
                prices[sel] = float(cell["price"])
            if len(prices) != 3 or min(prices.values()) <= 1.0:
                continue                                   # serve l'1X2 completo e attivo, altrimenti niente riferimento
            self.matches["op" + fid] = {
                "match_id": "op" + fid, "sport": "soccer", "league": league, "home": home, "away": away,
                "kickoff": datetime.fromtimestamp(start, timezone.utc).isoformat(), "status": "SCHEDULED",
                "minute": None, "home_score": None, "away_score": None, "result": None,
                "books": {"Pinnacle": prices}, "live_books": {}, "closing": None, "odds_ts": now, "ref_src": "oddspapi"}
        self.odds_ts = now

    def _fetch_sync(self) -> dict:
        now = time.time()
        self.note = ""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        todo, self.pending = sorted(self.pending), set()
        if todo:
            try:
                self._tournament_ids(now)
                for key in todo:
                    day, n = self.key_day.get(key, (today, 0))
                    if day != today:
                        day, n = today, 0
                    if now - self.key_ts.get(key, 0) < self.min_gap or n >= self.max_per_league_day:
                        continue
                    self._refresh(key, now)
                    self.key_ts[key] = now
                    self.key_day[key] = (day, n + 1)
            except OP.OddsPapiError as exc:
                self.note = str(exc)
        self.matches = {k: m for k, m in self.matches.items()
                        if datetime.fromisoformat(m["kickoff"]).timestamp() > now - 3 * 3600}
        return {"ts": now, "sim_time": now, "time_scale": 1.0,
                "health": {"error_rate": 0.0, "source": "OddsPapi" + (f" · fermo: {self.note}" if self.note else "")},
                "matches": {k: dict(v) for k, v in self.matches.items()}, "races": {}}

    async def fetch(self) -> dict:
        return await asyncio.to_thread(self._fetch_sync)
