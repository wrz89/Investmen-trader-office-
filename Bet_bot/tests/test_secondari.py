import sqlite3
from datetime import date, timedelta

import pytest

from betbot import secondari as S


def test_comp_div_e_tipi():
    assert S.comp_div("English Premier League") == "E0"
    assert S.comp_div("German Bundesliga 2") == "D2" and S.comp_div("German Bundesliga") == "D1"
    assert S.comp_div("Spanish Segunda Division") == "SP2" and S.comp_div("Spanish La Liga") == "SP1"
    assert S.comp_div("Brazilian Serie A") is None
    assert S.market_kind("OVER_UNDER_105_CORNR") == "angoli" and S.market_kind("BOOKING_ODDS") == "cartellini"
    assert S.market_kind("MATCH_ODDS") is None


def test_parse_runner():
    assert S.parse_runner("Over 10.5 Corners") == ("over", 10.5)
    assert S.parse_runner("Under 3.5 Cards") == ("under", 3.5)
    assert S.parse_runner("Over 10 Corners") is None          # linee intere: rimborso, non le usiamo
    assert S.parse_runner("9 or less") is None


def test_binomiale_negativa_vicina_alla_poisson():
    import math
    mu = 4.0
    pois = sum(math.exp(-mu) * mu ** i / math.factorial(i) for i in range(4))
    assert S.nb_cdf(3, mu, 1e6) == pytest.approx(pois, abs=1e-4)
    assert S.p_over(9.5, 10.0, 45) == pytest.approx(1 - S.nb_cdf(9, 10.0, 45))
    assert S.p_over(9.5, 12.0, 45) > S.p_over(9.5, 9.0, 45)


def test_valore_atteso_e_scelta():
    over = {"back": 2.2, "back_size": 10, "lay": 2.3, "lay_size": 10}
    under = {"back": 1.75, "back_size": 10, "lay": 1.85, "lay_size": 10}
    opts = {(o["side"], o["dir"]): o["ev"] for o in S.ev_options(0.55, over, under)}
    assert opts[("over", "back")] == pytest.approx(0.55 * 1.2 * 0.955 - 0.45)
    assert opts[("under", "lay")] == pytest.approx(0.55 * 0.955 - 0.45 * 0.85)
    c = S.pick(0.55, over, under)
    assert c and c["side"] == "over" and c["ev"] >= S.EV_MIN
    assert S.pick(0.45, over, under) is None
    assert S.pick(0.55, dict(over, back_size=1, lay_size=1), dict(under, back_size=1, lay_size=1)) is None   # niente soldi


def test_risultato_per_euro():
    b = {"line": 9.5, "side": "over", "dir": "back", "price": 2.0}
    assert S.outcome_pl(b, 10) == pytest.approx(0.955) and S.outcome_pl(b, 9) == -1
    l = {"line": 3.5, "side": "over", "dir": "lay", "price": 2.5}
    assert S.outcome_pl(l, 5) == pytest.approx(-1.5) and S.outcome_pl(l, 2) == pytest.approx(0.955)


def _rows(n=40):
    rows, d = [], date(2026, 8, 1)
    teams = ["Inter", "Milan", "Juventus", "Napoli"]
    for i in range(n):
        h, a = teams[i % 4], teams[(i + 1 + i // 4) % 4]
        if h == a:
            a = teams[(i + 2) % 4]
        rows.append({"div": "I1", "date": d + timedelta(days=i), "home": h, "away": a, "hc": 5 + i % 3, "ac": 4 + i % 2,
                     "hy": 2, "ay": 2 + i % 2, "hr": 0, "ar": 0, "hf": 12, "af": 13, "ph": 2.0, "pd": 3.4, "pa": 3.8,
                     "o25": 1.9, "u25": 1.95})
    return rows


def test_modello_e_previsione():
    m = S.build_model(_rows())
    assert m["corners"] and m["cards"]
    S_teams = S.find_team(m, "I1", "AC Milan")
    assert S_teams == "I1|Milan"
    p = S.predict(m, "I1", "Inter", "AC Milan", *S._probs(2.0, 3.4, 3.8, 1.9, 1.95))
    assert p and 7 < p["angoli"] < 13 and 2 < p["cartellini"] < 7


def test_regolamento_e_verdetto(tmp_path):
    con = S.db(tmp_path / "s.db")
    with con:
        con.execute("INSERT INTO evento VALUES ('e1',0,'I1','Italian Serie A','Inter','AC Milan','2026-08-05T18:45:00Z',"
                    "0.5,0.25,0.25,0.5,10,4,'Inter','Milan')")
        con.execute("INSERT INTO giocata(market_id, ts, event_id, kind, line, side, dir, price, p_model, ev, mu) "
                    "VALUES ('m1',0,'e1','angoli',9.5,'over','back',2.0,0.6,0.15,10)")
        con.execute("INSERT INTO quote VALUES (1,'m1','e1','2026-08-05T18:45:00Z','angoli',9.5,'over',1.9,5,1.94,5)")
    rows = [{"div": "I1", "date": date(2026, 8, 5), "home": "Inter", "away": "Milan", "hc": 6, "ac": 5, "hy": 1, "ay": 2,
             "hr": 0, "ar": 0}]
    with con:
        assert S.settle(con, rows) == 1
    s = S.summary(con)
    assert s["angoli"]["n"] == 1 and s["angoli"]["roi"] == pytest.approx(0.955)
    assert s["angoli"]["clv"] == pytest.approx(2.0 / 1.92 - 1)
    assert "presto" in S.verdict(s["angoli"])
    assert S.verdict({"n": 300, "roi": 0.08, "se": 0.02}).startswith("PROMETTENTE")
    assert S.verdict({"n": 300, "roi": -0.08, "se": 0.02}) == "BOCCIATA"


class FakeClient:
    def __init__(self):
        self.calls = []

    def market_types(self, et):
        return {"MATCH_ODDS": 10, "OVER_UNDER_25": 10, "OVER_UNDER_105_CORNR": 3, "CORNER_ODDS": 3}

    def competitions(self, et):
        return {"81": "Italian Serie A", "99": "Brazilian Serie A"}

    def rpc(self, method, params):
        self.calls.append((method, params))
        ev = {"id": "e1", "name": "Inter v AC Milan"}
        comp = {"name": "Italian Serie A"}
        ko = "2026-08-30T18:45:00Z"
        return [
            {"marketId": "mo", "marketType": "MATCH_ODDS", "event": ev, "competition": comp, "marketStartTime": ko,
             "runners": [{"selectionId": 1, "runnerName": "Inter"}, {"selectionId": 2, "runnerName": "AC Milan"},
                         {"selectionId": 3, "runnerName": "The Draw"}]},
            {"marketId": "c105", "marketType": "OVER_UNDER_105_CORNR", "event": ev, "competition": comp, "marketStartTime": ko,
             "runners": [{"selectionId": 10, "runnerName": "Under 10.5 Corners"}, {"selectionId": 11, "runnerName": "Over 10.5 Corners"}]},
            {"marketId": "codds", "marketType": "CORNER_ODDS", "event": ev, "competition": comp, "marketStartTime": ko,
             "runners": [{"selectionId": 20, "runnerName": "9 or less"}, {"selectionId": 21, "runnerName": "10 - 12"},
                         {"selectionId": 22, "runnerName": "13 or more"}]},
        ]

    def books(self, ids):
        def rb(sid, b, l):
            return {"selectionId": sid, "ex": {"availableToBack": [{"price": b, "size": 20}], "availableToLay": [{"price": l, "size": 20}]}}
        return [{"marketId": "mo", "status": "OPEN", "runners": [rb(1, 2.0, 2.02), rb(2, 3.8, 3.85), rb(3, 3.4, 3.45)]},
                {"marketId": "c105", "status": "OPEN", "runners": [rb(10, 1.2, 1.25), rb(11, 5.0, 5.2)]},
                {"marketId": "codds", "status": "OPEN", "runners": [rb(20, 3, 3.1), rb(21, 2, 2.1), rb(22, 4, 4.2)]}]


def test_registratore_giro_completo(tmp_path):
    m = S.build_model(_rows())
    reg = S.Registratore(FakeClient(), tmp_path / "r.db")
    new = reg.tick(now=1.0e9, model=m)
    con = sqlite3.connect(tmp_path / "r.db")
    q = con.execute("SELECT kind, line, side FROM quote ORDER BY side").fetchall()
    assert q == [("angoli", 10.5, "over"), ("angoli", 10.5, "under")]         # il mercato a 3 esiti non si registra
    ev = con.execute("SELECT div, home_fd, away_fd FROM evento").fetchall()
    assert ev == [("I1", "Inter", "Milan")]
    g = con.execute("SELECT market_id, dir FROM giocata").fetchall()
    assert new == len(g) and all(x[0] == "c105" for x in g)
    assert reg.tick(now=1.0e9 + 10, model=m) == 0                               # una sola puntata per mercato
    flt = reg.client.calls[0][1]["filter"]
    assert flt["competitionIds"] == ["81"] and "OVER_UNDER_105_CORNR" in flt["marketTypeCodes"]


def test_maybe_tick_senza_client_o_spento():
    S._REG = None
    S.maybe_tick(None, {})
    assert S._REG is None
    S.maybe_tick(FakeClient(), {"secondari": {"attivo": False}})
    assert S._REG is None
