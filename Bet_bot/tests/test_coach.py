"""Leo, l'allenatore: autopsie, lezioni che possono solo frenare, ritiro delle regole smentite."""
import json

import pytest

from betbot.agents.coach import Coach, classify, our_prob
from betbot.core import SportOffice


@pytest.fixture
def office(tmp_path):
    return SportOffice(db_path=tmp_path / "t.db", connect_feed=False)


def test_luck_and_skill_are_separated():
    f = {}
    assert classify(True, 0.02, f, 0.02) == "merito"
    assert classify(True, -0.03, f, 0.03) == "fortuna"          # vinta, ma il mercato ci dava torto
    assert classify(False, 0.01, f, 0.01) == "varianza"         # persa con la decisione giusta: nessun errore
    assert classify(False, -0.03, f, 0.03) == "smentita"
    assert classify(False, -0.02, {"ref_age_min": 90}, 0.02) == "riferimento"
    assert classify(False, -0.08, f, 0.08) == "notizia"
    assert classify(False, None, f, None) == "non_valutabile"


def test_lay_wins_when_outcome_does_not_happen():
    assert our_prob("LAY", 0.2) == pytest.approx(0.8) and our_prob("BACK", 0.2) == 0.2


def _lesson(o, clv, league="Serie B", outcome="LOST", p=0.8):
    f = {"league": league, "odds": 1.3, "minutes_before": 90, "edge": 0.025, "source": "Pinnacle",
         "sport": "soccer", "side": "BACK", "liquidity": 50}
    o.store.execute("INSERT INTO coach_lessons(ts, strategy_id, label, outcome, pnl, p_entry, clv, cause, features) "
                    "VALUES('t','S05_favoriti_exchange_v2','x',?,?,?,?,'smentita',?)",
                    (outcome, -1.0 if outcome == "LOST" else 0.3, p, clv, json.dumps(f)))


def _prop(league):
    return {"strategy_id": "S05_favoriti_exchange_v2", "match_id": "M1", "selection": "home", "league": league,
            "odds": 1.3, "edge": 0.025, "sport": "soccer_italy", "fair_prob": 0.8, "commission": 0.045}


def test_rule_learned_only_with_significant_evidence_and_only_blocks(office):
    c: Coach = office.coach
    snap = {"ts": 0, "matches": {"M1": {"books": {"Pinnacle": {"home": 1.3, "draw": 5, "away": 9}}}}}
    for i in range(3 * c.min_n):                                # Serie A: il mercato ci dà ragione
        _lesson(office, 0.02 + (i % 3) * 0.002, league="Serie A", outcome="WON")
    for i in range(c.min_n - 1):
        _lesson(office, -0.02 - (i % 3) * 0.002, outcome="WON")
    c.learn()
    assert c.check(_prop("Serie B"), snap)[0]                   # 29 puntate: ancora nessuna lezione
    _lesson(office, -0.021, outcome="WON")
    c.learn()
    ok, why = c.check(_prop("Serie B"), snap)
    assert not ok and "Serie B" in why
    assert c.check(_prop("Serie A"), snap)[0]                   # la regola vale solo per il suo segmento


def test_rule_withdrawn_when_new_data_disagree(office):
    c: Coach = office.coach
    for i in range(c.min_n):
        _lesson(office, -0.02 - (i % 3) * 0.002, outcome="WON")
    c.learn()
    assert office.store.query("SELECT active FROM coach_rules WHERE kind='blocca' AND value='Serie B'")[0]["active"] == 1
    for i in range(4 * c.min_n):
        _lesson(office, 0.03 + (i % 3) * 0.002, outcome="WON")
    c.learn()
    assert office.store.query("SELECT active FROM coach_rules WHERE kind='blocca' AND value='Serie B'")[0]["active"] == 0


def test_overconfident_strategy_gets_probability_correction(office):
    c: Coach = office.coach
    for i in range(100):                                        # stima 80%, vince il 65%
        _lesson(office, None, league=f"L{i % 7}", outcome="WON" if i % 20 < 13 else "LOST", p=0.8)
    c.learn()
    r = office.store.query("SELECT adjust, active FROM coach_rules WHERE kind='correggi'")
    assert r and r[0]["active"] == 1 and 0 < r[0]["adjust"] <= 0.05


def test_coach_rule_veto_is_followed_in_shadow_not_placed(office):
    office.store.execute("INSERT INTO coach_rules(created, updated, strategy_id, kind, feature, value, n, clv, clv_hi, active) "
                         "VALUES('t','t','S05_favoriti_exchange_v2','blocca','campionato','Serie B',40,-0.02,-0.01,1)")
    ok, why = office.coach.check(_prop("Serie B"), {"ts": 0, "matches": {}})
    assert not ok and why.startswith("Lezione di Leo")


def test_clv_is_measured_on_the_price_taken():
    from betbot.agents.coach import price_clv_of
    assert price_clv_of("BACK", 2.10, 0.50) == pytest.approx(0.05)        # presa a 2,10, giusta alla chiusura 2,00
    assert price_clv_of("BACK", 1.90, 0.50) == pytest.approx(-0.05)
    assert price_clv_of("LAY", 3.80, 0.25) == pytest.approx(4.0 / 3.8 - 1)  # bancata a 3,80, giusta 4,00: buono
    assert price_clv_of("LAY", 4.20, 0.25) < 0
