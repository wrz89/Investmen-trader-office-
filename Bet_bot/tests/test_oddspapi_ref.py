"""Riferimento Pinnacle di scorta da OddsPapi: solo dove manca un riferimento fresco."""
import asyncio
import time
from datetime import datetime, timezone

from betbot import oddspapi as OP
from betbot.feeds import CompositeFeed, oddspapi_ref as R
from betbot.feeds.base import Feed

KICK = time.time() + 3600


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


class FakeClient:
    def __init__(self, *a, **k):
        self.calls = []

    def get(self, path, **params):
        self.calls.append(path)
        if path == "/tournaments":
            return [{"tournamentId": 23, "tournamentName": "Serie A", "categoryName": "Italy"},
                    {"tournamentId": 203, "tournamentName": "Premier League", "categoryName": "Russia"}]
        if path == "/fixtures":
            return [{"fixtureId": "id9", "participant1Name": "Milan", "participant2Name": "Inter"}]
        assert path == "/odds-by-tournaments" and params["bookmaker"] == "pinnacle"
        cell = lambda p: {"players": {"0": {"price": p, "active": True}}}
        return [{"fixtureId": "id9", "startTime": iso(KICK), "bookmakerOdds": {"pinnacle": {"markets": {"101": {"outcomes": {
            "101": cell(2.1), "102": cell(3.4), "103": cell(3.6)}}}}}}]


class Primary(Feed):
    async def fetch(self):
        now = time.time()
        return {"ts": now, "sim_time": now, "health": {"error_rate": 0, "source": "betfair"}, "races": {},
                "matches": {"b1": {"match_id": "b1", "sport": "soccer", "league": "Italian Serie A", "home": "AC Milan",
                                   "away": "Inter", "kickoff": iso(KICK), "status": "SCHEDULED",
                                   "exchange": {"home": {"back": 3.5, "lay": 3.55}}, "books": {}, "ref_ts": None}}}


def feed(monkeypatch):
    monkeypatch.setattr(R, "oddspapi_key", lambda: "k")
    monkeypatch.setattr(R.OP, "OddsPapiClient", FakeClient)
    return R.OddsPapiRefFeed({"feed": {}})


def test_scorta_riempie_solo_chi_non_ha_riferimento(monkeypatch):
    extra = feed(monkeypatch)
    snap = asyncio.run(CompositeFeed({"feed": {}}, Primary({}), None, None, extra).fetch())
    m = snap["matches"]["b1"]
    assert m["books"]["Pinnacle"] == {"home": 2.1, "draw": 3.4, "away": 3.6}
    assert m["ref_src"] == "oddspapi" and time.time() - m["ref_ts"] < 30
    assert "OddsPapi su 1 partite" in snap["health"]["source"]


def test_intervallo_minimo_e_tetto_giornaliero(monkeypatch):
    extra = feed(monkeypatch)
    extra.max_requests_day = 3
    extra.request({"soccer_italy_serie_a"})
    extra._fetch_sync()
    n = len(extra.client.calls)
    assert n == 3                                           # tornei + nomi + quote
    extra.request({"soccer_italy_serie_a"})
    extra._fetch_sync()
    assert len(extra.client.calls) == n                      # intervallo di 40 minuti: nessuna nuova chiamata


def test_chiave_mancante_non_rompe_il_feed(monkeypatch):
    monkeypatch.setattr(R, "oddspapi_key", lambda: "")
    from betbot.feeds import make_feed
    import betbot.feeds as F
    assert make_feed.__name__ and F.FeedError
    try:
        R.OddsPapiRefFeed({"feed": {}})
        assert False
    except F.FeedError:
        pass


def test_crediti_piu_larghi_nel_weekend():
    from datetime import datetime
    from betbot.feeds.odds_api import credits_per_day
    cfg = {"max_credits_per_day": 15, "max_credits_weekday": 8, "max_credits_weekend": 25}
    assert credits_per_day(cfg, datetime(2026, 10, 7)) == 8            # mercoledì
    assert credits_per_day(cfg, datetime(2026, 10, 9)) == 25           # venerdì
    assert credits_per_day(cfg, datetime(2026, 10, 11)) == 25          # domenica
    assert credits_per_day({"max_credits_per_day": 15}, datetime(2026, 10, 7)) == 15
