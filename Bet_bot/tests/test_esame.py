"""Esame per il live: conta solo i prezzi veri, criteri decisi prima."""
import json
import random

import pytest

from betbot import esame
from betbot.core import SportOffice


@pytest.fixture
def office(tmp_path):
    return SportOffice(db_path=tmp_path / "t.db", connect_feed=False)


def _add(o, n, clv, feed="betfair", won_rate=0.8, sid="S09_lay_valore_v1", seed=1):
    rnd = random.Random(seed)
    for i in range(n):
        cur = o.store.execute("INSERT INTO coach_entries(ts, src, row_id, strategy_id, match_id, selection, side, odds, "
                              "fair_prob, edge, features, track, reviewed) VALUES('t','shadow_bets',?,?,'M','home','LAY',4.0,"
                              "0.2,0.03,?,'{}',1)", (i, sid, json.dumps({"feed": feed})))
        won = rnd.random() < won_rate
        o.store.execute("INSERT INTO coach_lessons(ts, entry_id, strategy_id, outcome, pnl, stake, clv, cause) "
                        "VALUES('t',?,?,?,?,1,?, 'x')", (cur.lastrowid, sid, "WON" if won else "LOST",
                                                          0.955 if won else -3.0, clv + rnd.gauss(0, 0.01)))


def test_simulated_prices_never_count(office):
    _add(office, 300, 0.02, feed="mock", won_rate=0.9)
    r = esame.evaluate(office.store, "S09_lay_valore_v1")
    assert r["n"] == 0 and r["verdict"] == "IN ESAME"


def test_ready_only_with_enough_real_evidence(office):
    _add(office, 150, 0.02, won_rate=0.9)
    assert esame.evaluate(office.store, "S09_lay_valore_v1")["verdict"] == "IN ESAME"     # poche puntate
    _add(office, 100, 0.02, won_rate=0.9, seed=2)
    r = esame.evaluate(office.store, "S09_lay_valore_v1")
    assert r["verdict"] == "PRONTA" and r["n"] == 250 and r["clv_lo"] > 0 and r["roi"] > 0


def test_failed_when_market_disagrees(office):
    _add(office, 220, -0.02, won_rate=0.7)
    assert esame.evaluate(office.store, "S09_lay_valore_v1")["verdict"] == "BOCCIATA"


def test_positive_clv_but_losing_money_is_not_ready(office):
    _add(office, 250, 0.02, won_rate=0.6)                  # lay a 4: con il 60% di vinte si perde
    r = esame.evaluate(office.store, "S09_lay_valore_v1")
    assert r["verdict"] == "IN ESAME" and "ROI non positivo" in r["reasons"]


def test_exam_splits_s10_by_side(tmp_path):
    import json

    from betbot import esame
    from betbot.agents.coach import CoachBook
    from betbot.store import Store
    st = Store(tmp_path / "o.db")
    CoachBook(st)
    for i, (side, clv) in enumerate((("LAY", 0.10), ("LAY", 0.12), ("BACK", -0.01), ("BACK", 0.0))):
        eid = st.execute("INSERT INTO coach_entries(ts, src, row_id, strategy_id, match_id, selection, side, odds, fair_prob, "
                         "edge, features, track, reviewed) VALUES('x', 'shadow_bets', ?, 'S10_misura_v1', ?, 'home', ?, 3.0, "
                         "0.5, 0.02, ?, '{}', 1)", (i, f"M{i}", side, json.dumps({"feed": "betfair"}))).lastrowid
        st.execute("INSERT INTO coach_lessons(ts, entry_id, strategy_id, label, src, outcome, pnl, stake, clv) "
                   "VALUES('x', ?, 'S10_misura_v1', 'x', 'shadow_bets', 'WON', 0.5, 1, ?)", (eid, clv))
    res = {r["strategy_id"]: r for r in esame.evaluate_all(st, ["S10_misura_v1"])}
    assert set(res) == {"S10_misura_v1", "S10_misura_v1 · solo lay", "S10_misura_v1 · solo lay fresco",
                        "S10_misura_v1 · solo lay · Pinnacle OddsPapi", "S10_misura_v1 · solo back"}
    assert res["S10_misura_v1 · solo lay · Pinnacle OddsPapi"]["n"] == 0
    assert res["S10_misura_v1 · solo lay"]["n"] == 2 and res["S10_misura_v1 · solo lay"]["clv"] == 0.11
    assert res["S10_misura_v1 · solo back"]["n"] == 2 and res["S10_misura_v1"]["n"] == 4
