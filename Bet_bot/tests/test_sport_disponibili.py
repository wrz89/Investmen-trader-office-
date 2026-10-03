"""Scoperta degli sport di betfair.it e sport aggiunti a mano."""
from betbot import sport_disponibili as S
from betbot.feeds.betfair import sports_table


class FakeClient:
    def event_types(self):
        return {"1": "Soccer", "468328": "Handball", "999": "Floorball", "5": "Rugby Union"}

    def market_types(self, tid):
        return {"MATCH_ODDS": {"1": 40, "468328": 6, "999": 3, "5": 0}[tid]}

    def catalogue(self, tid, mt, hours, countries, n, lookback_hours=0):
        return [{"runners": [1, 2] if tid == "999" else [1, 2, 3]}]


def test_discovery_lists_new_sports_and_suggests_config():
    st = {"feed": {"betfair": {"sports": ["soccer"]}}}
    rows = S.check(client=FakeClient(), settings=st)
    txt = S.report(rows)
    assert "Floorball" in txt and "NON ancora letto" in txt and "extra_sports" in txt and 'id: "999"' in txt
    assert "Rugby" not in txt.split("Per far leggere")[0]          # 0 mercati: non elencato
    assert any(r["chiave"] == "handball" for r in rows)


def test_extra_sports_merge():
    t = sports_table({"floorball": {"id": 999, "esiti": 2, "nome": "Floorball"}})
    assert t["floorball"] == ("999", "MATCH_ODDS", 2) and "soccer" in t
    assert sports_table({"rotto": {}}).get("rotto") is None
