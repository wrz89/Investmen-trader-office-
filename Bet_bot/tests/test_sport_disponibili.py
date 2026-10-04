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


def test_auto_discovery_adds_sports_for_shadow_only(monkeypatch):
    from betbot.feeds import betfair as BF
    from betbot.strategies import s10_divertimento_v1 as V1, s10_misura_v1 as M
    feed = BF.BetfairFeed.__new__(BF.BetfairFeed)
    feed.client = FakeClient()
    feed.available = {"1": "Soccer", "999": "Floorball", "7": "Horse Racing", "2378961": "Politics"}
    feed.sports_tab = BF.sports_table(None)
    feed._discover()
    assert feed.auto_tab == {"xfloorball": ("999", "MATCH_ODDS", 2)}
    # live: solo gli sport vagliati; misura: tutti
    snap = {"ts": 0, "matches": {}}
    assert M.DEFAULTS["all_sports"] is True and "xfloorball" not in V1.SPORTS


def test_live_s10_skips_unvetted_sport_but_misura_measures_it():
    import time
    from datetime import datetime, timedelta, timezone
    from betbot.strategies import s10_divertimento_v2 as V2, s10_misura_v1 as M
    now = time.time()
    ko = (datetime.fromtimestamp(now, timezone.utc) + timedelta(minutes=60)).isoformat()
    ex = {"home": {"back": 2.00, "lay": 2.02, "back_size_best": 50, "lay_size_best": 50},
          "away": {"back": 2.00, "lay": 2.02, "back_size_best": 50, "lay_size_best": 50}}
    snap = {"ts": now, "matches": {"m1": {"match_id": "m1", "sport": "xfloorball", "league": "Liga", "status": "SCHEDULED",
                                          "home": "A", "away": "B", "kickoff": ko, "exchange": ex, "books": {}}}}
    assert V2.propose(snap, {}, {}) == []
    assert len(M.propose(snap, {}, {})) == 1


def test_lay_reference_needs_and_on_demand_budget(monkeypatch, tmp_path):
    import time
    from datetime import datetime, timedelta, timezone
    from betbot.feeds import lay_reference_needs, league_key
    now = time.time()
    ko = lambda mins: (datetime.fromtimestamp(now, timezone.utc) + timedelta(minutes=mins)).isoformat()
    ex = {"away": {"back": 4.2, "lay": 4.3}, "home": {"back": 1.7, "lay": 1.72}}
    snap = {"ts": now, "matches": {
        "a": {"status": "SCHEDULED", "sport": "soccer", "league": "Italian Serie A", "kickoff": ko(120), "exchange": ex},
        "b": {"status": "SCHEDULED", "sport": "soccer", "league": "English Premier League", "kickoff": ko(120), "exchange": ex,
              "ref_ts": now - 600},                                   # riferimento fresco: niente richiesta
        "c": {"status": "SCHEDULED", "sport": "tennis", "league": "ATP", "kickoff": ko(120), "exchange": ex},
        "d": {"status": "SCHEDULED", "sport": "soccer", "league": "Lega sconosciuta", "kickoff": ko(120), "exchange": ex}}}
    assert lay_reference_needs(snap) == {"soccer_italy_serie_a"} and league_key("Brazilian Serie A") is None

    from betbot.feeds import odds_api
    monkeypatch.setattr(odds_api, "api_key", lambda: "k")
    monkeypatch.setattr(odds_api.OddsApiFeed, "_budget", lambda self: {"day": "x", "used": 0, "remaining": 400, "_path": str(tmp_path / "b.json")})
    f = odds_api.OddsApiFeed({"feed": {"sports": [], "odds_api": {"soccer_on_demand": True, "scores_refresh_seconds": 0}}})
    calls = []
    monkeypatch.setattr(f, "_refresh_odds", lambda t, keys=None: calls.append(keys))
    f.request(["soccer_italy_serie_a", "soccer_epl"])
    f._refresh_on_demand(now)
    assert calls == [["soccer_epl"], ["soccer_italy_serie_a"]]
    f.request(["soccer_epl"])
    f._refresh_on_demand(now + 60)                      # entro 40 minuti: niente nuovo credito
    assert len(calls) == 2
