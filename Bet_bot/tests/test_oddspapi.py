"""Prova OddsPapi: lettura tollerante delle risposte e tetto mensile."""
import json
import time
from datetime import datetime, timezone

from betbot import oddspapi as O


class Resp:
    def __init__(self, data, code=200):
        self._d, self.status_code, self.headers, self.text = data, code, {}, json.dumps(data)

    def json(self):
        return self._d


class FakeHttp:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, headers=None, timeout=0):
        path = url.split("/v4", 1)[1]
        self.calls.append((path, params))
        return Resp(self.routes[path])


NOW_MS = int(time.time() * 1000)
ROUTES = {
    "/bookmakers": [{"slug": "pinnacle", "name": "Pinnacle"}, {"slug": "betfair-ex", "bookmakerName": "BetFair Exchange", "cloneOf": None},
                    {"slug": "betfair.it", "bookmakerName": "Betfair IT", "cloneOf": "betfair-spb"},
                    {"slug": "pinnacle+5", "bookmakerName": "Pinnacle +5 sec"},
                    {"slug": "bet365", "name": "bet365"}],
    "/markets": [{"marketId": 111, "marketType": "moneyline", "period": "result",
                  "outcomes": [{"outcomeId": 111, "outcomeName": "1"}, {"outcomeId": 112, "outcomeName": "2"}]},
                 {"marketId": 101, "marketType": "1x2", "period": "fulltime", "handicap": 0.0,
                  "outcomes": [{"outcomeId": 101, "outcomeName": "1"}, {"outcomeId": 102, "outcomeName": "X"},
                               {"outcomeId": 103, "outcomeName": "2"}]}],
    "/tournaments": [{"tournamentId": 23, "tournamentName": "Serie A", "categoryName": "Italy", "futureFixtures": 330, "upcomingFixtures": 9},
                     {"tournamentId": 203, "tournamentName": "Premier League", "categoryName": "Russia", "futureFixtures": 100, "upcomingFixtures": 5},
                     {"tournamentId": 17, "tournamentName": "Premier League", "categoryName": "England", "futureFixtures": 0, "upcomingFixtures": 0, "liveFixtures": 0}],
    "/odds-by-tournaments": [{"fixtureId": "id1", "startTime": 1, "participant1Name": "A", "participant2Name": "B",
                              "bookmakerOdds": {
                                  "pinnacle": {"markets": {"101": {"outcomes": {
                                      "101": {"players": {"0": {"price": 2.1, "changedAt": datetime.fromtimestamp(time.time() - 600, timezone.utc).isoformat()}}},
                                      "102": {"players": {"0": {"price": 3.4, "changedAt": NOW_MS - 900_000}}},
                                      "103": {"players": {"0": {"price": 3.6, "changedAt": NOW_MS - 60_000}}}}}}},
                                  "betfair-ex": {"markets": {"101": {"outcomes": {
                                      "101": {"players": {"0": {"price": 2.2, "changedAt": NOW_MS}}}}}}}}}],
}


def test_discovery_helpers():
    assert O.find_slugs(ROUTES["/bookmakers"]) == {"pinnacle": ["pinnacle"], "betfair": ["betfair-ex"]}
    assert O.find_1x2(ROUTES["/markets"]) == {"1": "101", "X": "102", "2": "103", "market": "101"}
    assert [t["tournamentId"] for t in O.pick_tournaments(ROUTES["/tournaments"])] == [23]


def test_fixture_view_age():
    v = O.fixture_view(ROUTES["/odds-by-tournaments"][0], {"pinnacle": ["pinnacle"], "betfair": ["betfair-ex"]},
                       {"1": "101", "X": "102", "2": "103", "market": "101"})
    assert v["pinnacle"]["quote"]["X"] == 3.4 and 890 <= v["pinnacle"]["eta_s"] <= 1500
    assert v["betfair-ex"]["quote"] == {"1": 2.2}


def test_run_end_to_end_uses_header_and_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(O, "RUNTIME_DIR", tmp_path)
    http = FakeHttp(ROUTES)
    out = []
    assert O.run(out=out.append, client=O.OddsPapiClient("k-123", session=http)) == 0
    assert all(p["apiKey"] == "k-123" for _, p in http.calls)
    assert json.loads((tmp_path / "oddspapi_budget.json").read_text())["used"] == 5
    assert "mediana" in "\n".join(out) and (tmp_path / "oddspapi_scoperta.json").exists()


def test_monthly_cap_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(O, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(O, "MONTHLY_CAP", 2)
    c = O.OddsPapiClient("k", session=FakeHttp(ROUTES))
    c.get("/bookmakers"); c.get("/bookmakers")
    try:
        c.get("/bookmakers")
        assert False
    except O.OddsPapiError as exc:
        assert "Tetto mensile" in str(exc)
