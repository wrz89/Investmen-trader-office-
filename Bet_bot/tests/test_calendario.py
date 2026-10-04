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
