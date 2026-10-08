from datetime import datetime, timedelta, timezone

from betbot import calendario
from betbot.store import Store


class C:
    def competitions(self, et):
        return {"81": "Italian Serie A", "7": "English Premier League"}

    def catalogue(self, et, mt, hours, countries, n, lookback_hours=0, competition_ids=None):
        ko = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        return [{"marketStartTime": ko, "event": {"name": "Inter v Milan"}, "competition": {"name": "Italian Serie A"}}]


def test_calendar_from_betfair_and_fallback(tmp_path):
    calendario._cache.clear()
    c = calendario.competitions("soccer", client=C())
    assert c["source"] == "betfair" and {"id": "81", "name": "Italian Serie A"} in c["items"]
    m = calendario.matches("soccer", "81", client=C())
    assert m["source"] == "betfair" and m["matches"][0]["home"] == "Inter" and m["matches"][0]["league"] == "Italian Serie A"
    calendario._cache.clear()
    calendario._client = None
    st = Store(tmp_path / "x.db")
    ko = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    st.execute("INSERT INTO matches(match_id, sport, league, home, away, kickoff) VALUES('m1','soccer','Serie A','Roma','Lazio',?)", (ko,))
    import betbot.calendario as cal
    cal._betfair = lambda: None
    r = cal.matches("soccer", None, store=st)
    assert r["source"] == "db" and r["matches"][0]["home"] == "Roma"


def test_calendar_pages_past_the_200_limit():
    """Con più di 200 partite il calendario va avanti per orario d'inizio invece di fermarsi ai primi 200 (due giorni)."""
    calendario._cache.clear()
    now = datetime.now(timezone.utc)
    allm = [{"marketId": f"1.{i}", "marketStartTime": (now + timedelta(hours=1 + i * 0.1)).isoformat(),
             "event": {"name": f"A{i} v B{i}"}, "competition": {"name": "Lega"}} for i in range(450)]

    class Pager:
        calls = 0

        def catalogue(self, et, mt, hours, countries, n, lookback_hours=0, competition_ids=None):
            Pager.calls += 1
            frm = now - timedelta(hours=lookback_hours)
            return [m for m in allm if datetime.fromisoformat(m["marketStartTime"]) >= frm][:n]
    r = calendario.matches("soccer", None, client=Pager())
    assert len(r["matches"]) == 450 and r["note"] == "" and Pager.calls == 3


def test_feed_reads_more_soccer_markets_than_the_default():
    from betbot.feeds.betfair import BetfairFeed
    f = BetfairFeed.__new__(BetfairFeed)
    f.cat, f.cat_ts, f.available = {}, 0, {"1": "Soccer", "2": "Tennis"}
    f.cfg = {"sports": ["soccer", "tennis"], "soccer_hours": 36, "catalogue_max": {"soccer": 120}, "catalogue_default": 40}
    f.watch_ids, f.saved_cat = set(), {}
    seen = {}

    class C:
        def catalogue(self, event_type, market_type, hours, countries, n, **k):
            seen[event_type] = n
            return []
    f.client = C()
    f._priority_soccer = lambda *a: []
    f._refresh_catalogue()
    assert seen == {"1": 120, "2": 40}
