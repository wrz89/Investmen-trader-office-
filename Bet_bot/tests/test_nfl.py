"""Football americano (NFL): feed Betfair a due esiti, dead heat, riferimento Pinnacle e test rapido."""
import pytest

from betbot import test_rapido as T


@pytest.fixture
def office(tmp_path, monkeypatch):
    from betbot import local_settings
    monkeypatch.setattr(local_settings, "load", lambda: {**local_settings.DEFAULTS})
    from betbot.config import load_settings
    from betbot.core import SportOffice
    from betbot.feeds.mock import MockFeed
    st = load_settings()
    st["feed"]["mock"]["seed"] = 5
    return SportOffice(db_path=tmp_path / "o.db", feed=MockFeed(st))


def _nfl_feed(status="OPEN", winners=()):
    from betbot.feeds.betfair import BetfairFeed
    f = BetfairFeed.__new__(BetfairFeed)
    f.client = type("C", (), {"errors": 0, "calls": 1})()
    f.stream_task = None
    # sport USA: "ospite @ casa"
    f.cat = {"1.9": {"_kind": "americanfootball", "marketStartTime": "2030-01-01T18:00:00Z",
                     "event": {"name": "Kansas City Chiefs @ Buffalo Bills"}, "competition": {"name": "NFL"},
                     "runners": [{"selectionId": 11, "runnerName": "Kansas City Chiefs"},
                                 {"selectionId": 22, "runnerName": "Buffalo Bills"}]}}
    rs = {"11": {"back": 2.3, "lay": 2.34, "back_size": 50, "lay_size": 40},
          "22": {"back": 1.75, "lay": 1.77, "back_size": 60, "lay_size": 55}}
    for rid in rs:
        rs[rid]["status"] = "WINNER" if rid in winners else ("LOSER" if status == "CLOSED" else "ACTIVE")
    return f, {"1.9": {"status": status, "inplay": False, "runners": rs}}


def test_betfair_nfl_two_way_market_mapped_by_name():
    f, prices = _nfl_feed()
    m = f._snapshot(prices)["matches"]["1.9"]
    assert m["sport"] == "americanfootball" and m["league"] == "NFL"
    assert m["home"] == "Buffalo Bills" and m["away"] == "Kansas City Chiefs"
    assert m["exchange"]["home"]["back"] == 1.75 and m["exchange"]["away"]["lay"] == 2.34 and "draw" not in m["exchange"]


def test_betfair_nfl_tie_is_dead_heat_not_void():
    f, prices = _nfl_feed("CLOSED", winners=("11", "22"))
    m = f._snapshot(prices)["matches"]["1.9"]
    assert m["status"] == "FINISHED" and m["result"] == "tie" and not m["void"]
    f, prices = _nfl_feed("CLOSED", winners=("22",))
    assert f._snapshot(prices)["matches"]["1.9"]["result"] == "home"


def test_dead_heat_net_back_and_lay():
    from betbot.agents.banco import dead_heat_net
    # back 10 € a 1,80: metà vince 5 × 0,80 = 4, metà perde 5 → −1
    assert dead_heat_net(10, 1.8, lay=False, comm=0.045) == pytest.approx(-1.0)
    # back 10 € a 3,00: metà vince 5 × 2 = 10, metà perde 5 → +5 meno la commissione
    assert dead_heat_net(10, 3.0, lay=False, comm=0.045) == pytest.approx(5 * 0.955)
    # lay a 1,80 con responsabilità 8 € (backer 10 €): il contrario del back → +1 meno la commissione
    assert dead_heat_net(8, 1.8, lay=True, comm=0.045) == pytest.approx(0.955)
    assert dead_heat_net(20, 3.0, lay=True, comm=0.045) == pytest.approx(-5.0)


def test_paper_bet_on_nfl_tie_settles_as_dead_heat(office):
    import json
    bid = office.store.execute(
        "INSERT INTO bets (ts, mode, strategy_id, match_id, market, selection, bookmaker, odds, stake, "
        "fair_prob, edge, status, extra) VALUES (datetime('now'), 'paper', 'S05_favoriti_exchange_v2', 'NFL1', "
        "'h2h', 'home', 'Betfair', 1.8, 10, 0.6, 0.02, 'OPEN', ?)", (json.dumps({"sport": "americanfootball_nfl"}),)).lastrowid
    snap = {"ts": 0, "sim_time": 0, "matches": {"NFL1": {"match_id": "NFL1", "sport": "americanfootball", "status": "FINISHED",
                                                         "result": "tie", "void": False, "home_score": 20, "away_score": 20}},
            "races": {}}
    office.banco.settle(snap)
    b = office.store.query("SELECT * FROM bets WHERE id=?", (bid,))[0]
    assert b["status"] == "LOST" and b["pnl"] == pytest.approx(-1.0)


def test_odds_api_nfl_tie_and_out_of_season_sports_cost_nothing(monkeypatch):
    import asyncio
    from betbot.feeds import odds_api
    monkeypatch.setattr(odds_api, "api_key", lambda: "k")
    calls = []

    def fake_get(self, path, **params):
        calls.append(path)
        if path == "/sports":
            return [{"key": "americanfootball_nfl", "active": True}, {"key": "basketball_nba", "active": False}]
        if path.endswith("/scores"):
            return [{"id": "g1", "home_team": "Buffalo Bills", "away_team": "Kansas City Chiefs", "completed": True,
                     "commence_time": "2030-01-01T18:00:00Z",
                     "scores": [{"name": "Buffalo Bills", "score": "20"}, {"name": "Kansas City Chiefs", "score": "20"}]}]
        return []
    monkeypatch.setattr(odds_api.OddsApiFeed, "_get", fake_get)
    f = odds_api.OddsApiFeed({"feed": {"sports": ["americanfootball_nfl", "basketball_nba"], "odds_api": {}}})
    snap = asyncio.run(f.fetch())
    assert not any("basketball_nba" in c for c in calls)           # NBA fuori stagione: nessun credito
    assert snap["matches"]["g1"]["result"] == "tie"


def test_matteo_follows_american_football():
    from betbot.state import strategy_agent
    assert strategy_agent("S05_favoriti_exchange_v2", "americanfootball_nfl") == "cavalli"
    assert strategy_agent("S05_favoriti_exchange_v2", "soccer_italy_serie_a") == "analista"


def test_s05_does_not_bet_nfl_until_measured():
    from betbot.strategies.s05_favoriti_exchange_v2 import _family
    assert _family("americanfootball_nfl") is None and _family("americanfootball") is None


# ── test rapido con il football americano ─────────────────────────────────
KO = 10_000.0 + 3 * 3600
clock = {"t": 10_000.0}


def _sources():
    def pinnacle(sp):
        if sp == "americanfootball_nfl":        # ospite giusto 40% all'inizio, 36% alla chiusura
            return ([{"home": "Buffalo Bills", "away": "Kansas City Chiefs", "start": KO,
                      "fair": {"home": 0.6 if clock["t"] < KO - 7200 else 0.64,
                               "away": 0.4 if clock["t"] < KO - 7200 else 0.36}}], "400")
        return ([{"home": "Inter", "away": "Lecce", "start": KO, "fair": {"home": 0.6, "draw": 0.2, "away": 0.2}}], "400")

    def betfair(h):
        if clock["t"] >= KO:
            return []
        return [{"market_id": "1.1", "sport": "soccer", "home": "Inter", "away": "Lecce", "start": KO, "league": "Serie A",
                 "sel": {"home": {"back": 1.64, "lay": 1.66}, "draw": {"back": 4.9, "lay": 5.0}, "away": {"back": 4.9, "lay": 5.0}}},
                {"market_id": "1.9", "sport": "americanfootball", "home": "Buffalo Bills", "away": "Kansas City Chiefs",
                 "start": KO, "league": "NFL",
                 # lay dell'ospite a 2,3 quando il giusto è 2,5 (40%): EV positivo, e alla chiusura scende al 36%
                 "sel": {"home": {"back": 1.62, "lay": 1.64}, "away": {"back": 2.26, "lay": 2.3}}}]
    return {"sports": lambda: ["soccer_italy_serie_a", "americanfootball_nfl"],
            "events": lambda sp: [{"commence_time": "2026-10-01T00:00:00Z"}], "pinnacle": pinnacle, "betfair": betfair}


def test_test_rapido_measures_nfl_separately(monkeypatch, tmp_path):
    monkeypatch.setattr(T, "OUT_DIR", tmp_path)
    monkeypatch.setattr(T, "_ts", lambda iso: KO)
    clock["t"] = 10_000.0

    def sleep(s):
        clock["t"] += s
    data = T.collect(None, "k", hours=6, credits=50, out=lambda *a: None, sleep=sleep, now=lambda: clock["t"],
                     sources=_sources())
    assert "americanfootball_nfl" in data["sports"]
    res = T.analyse(data)
    nfl = [r for r in res["rows"] if r["sport"] == "americanfootball"]
    assert {r["side"] for r in nfl} == {"home", "away"}
    away = [r for r in nfl if r["side"] == "away"][0]
    assert away["ev_lay"] > 0 and away["clv_lay"] > 0
    assert res["sport"]["americanfootball"]["partite"] == 1 and res["sport"]["americanfootball"]["valore"]["n"] >= 1
    assert res["s09"]["n"] == 0                                           # nessun lay di calcio conveniente qui
    md = T.report(res)
    assert "football americano" in md and "Football americano: lay e back" in md


def test_pinnacle_two_way_only_for_american_football(monkeypatch):
    class R:
        status_code, headers = 200, {"x-requests-remaining": "10"}

        def json(self):
            return [{"home_team": "Buffalo Bills", "away_team": "Kansas City Chiefs", "commence_time": "2030-01-01T18:00:00Z",
                     "bookmakers": [{"markets": [{"key": "h2h", "outcomes": [
                         {"name": "Buffalo Bills", "price": 1.6}, {"name": "Kansas City Chiefs", "price": 2.45}]}]}]}]
    monkeypatch.setattr(T.requests, "get", lambda *a, **k: R())
    rows, _ = T.pinnacle_odds("k", "americanfootball_nfl")
    assert len(rows) == 1 and set(rows[0]["fair"]) == {"home", "away"}
    assert abs(sum(rows[0]["fair"].values()) - 1) < 1e-6
    rows, _ = T.pinnacle_odds("k", "soccer_epl")          # nel calcio senza pareggio il mercato non vale
    assert rows == []


def test_one_rejected_sport_does_not_blind_the_feed():
    from betbot.feeds.betfair import BetfairError, BetfairFeed
    f = BetfairFeed.__new__(BetfairFeed)
    f.cat, f.cat_ts, f.available = {}, 0, {"1": "Soccer", "7524": "Ice Hockey"}
    f.cfg = {"sports": ["soccer", "icehockey"], "soccer_hours": 36}
    f.watch_ids, f.saved_cat = set(), {}

    class C:
        def catalogue(self, event_type, market_type, hours, countries, n, **k):
            if event_type == "7524":
                raise BetfairError("INVALID_INPUT_DATA")
            return [{"marketId": "1.1", "event": {"name": "Inter v Lecce"}, "runners": []}]
    f.client = C()
    f._refresh_catalogue()
    assert list(f.cat) == ["1.1"] and f.bad_sports == {"icehockey": 1}
    f.client = type("D", (), {"catalogue": lambda *a, **k: (_ for _ in ()).throw(BetfairError("tutto giù"))})()
    f.cat_ts = 0
    with pytest.raises(BetfairError, match="tutto giù"):                    # se NIENTE funziona, l'errore esce
        f._refresh_catalogue()
