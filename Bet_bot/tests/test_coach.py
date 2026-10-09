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


def test_stale_pinnacle_close_falls_back_to_betfair_close(office):
    st = office.store
    for ref_age, expect in ((300.0, "betfair"), (20.0, "pinnacle")):
        sid = st.execute("INSERT INTO shadow_bets(ts, strategy_id, match_id, label, selection, odds, fair_prob, edge, stake, "
                         "status, pnl) VALUES('2026-10-02T10:00:00+00:00', 'S10_misura_v1', 'M', 'A - B · A', 'home', 2.0, "
                         "0.5, 0.0, 1, 'WON', 0.955)").lastrowid
        track = {"p_close": 0.50, "p_close_bf": 0.55, "ref_age_min": ref_age}
        st.execute("INSERT INTO coach_entries(ts, src, row_id, strategy_id, match_id, selection, side, odds, fair_prob, edge, "
                   "features, track) VALUES('2026-10-02T10:00:00+00:00', 'shadow_bets', ?, 'S10_misura_v1', 'M', 'home', "
                   "'BACK', 2.0, 0.5, 0.0, '{}', ?)", (sid, json.dumps(track)))
        office.coach.review()
        les = st.query("SELECT clv, features FROM coach_lessons ORDER BY id DESC LIMIT 1")[0]
        assert json.loads(les["features"])["close_src"] == expect
        assert les["clv"] == pytest.approx(2.0 * (0.55 if expect == "betfair" else 0.50) - 1)


def test_rules_learned_on_s10_misura_also_brake_s10_with_real_money(office):
    st = office.store
    st.execute("INSERT INTO coach_rules(created, updated, strategy_id, kind, feature, value, n, clv, clv_hi, active, evidence) "
               "VALUES('x', 'x', 'S10_misura_v1', 'blocca', 'sport', 'darts', 60, -0.06, -0.02, 1, 'test')")
    snap = {"ts": 1000.0, "sim_time": 1000.0, "matches": {"M1": {"kickoff": "2030-01-01T10:00:00+00:00", "exchange": {}}}}
    p = {"strategy_id": "S10_divertimento_v2", "match_id": "M1", "selection": "home", "odds": 1.8, "sport": "darts",
         "edge": -0.01, "league": "PDC"}
    ok, label = office.coach.check(p, snap)
    assert not ok
    ok, _ = office.coach.check({**p, "sport": "tennis"}, snap)
    assert ok
    ok, _ = office.coach.check({**p, "strategy_id": "S05_favoriti_exchange_v2"}, snap)
    assert ok                                             # altre strategie: la regola non le tocca


def test_correggi_rule_uses_strategy_threshold_for_fun(tmp_path, monkeypatch):
    """Una correzione piccola non deve bloccare il divertimento (EV già negativo per scelta, soglia −3%)."""
    from betbot import local_settings
    monkeypatch.setattr(local_settings, "load", lambda: {**local_settings.DEFAULTS})
    from betbot.config import load_settings
    from betbot.core import SportOffice
    from betbot.feeds.mock import MockFeed
    st = load_settings()
    office = SportOffice(db_path=tmp_path / "o.db", feed=MockFeed(st))
    office.store.execute("INSERT INTO coach_rules(created, updated, strategy_id, kind, feature, value, n, adjust, active) "
                         "VALUES('x','x','S10_misura_v1','correggi','probabilita','tutte',80,0.005,1)")
    p = {"strategy_id": "S10_divertimento_v2", "match_id": "m", "selection": "home", "odds": 2.0, "fair_prob": 0.51,
         "commission": 0.045, "fun": True, "league": "Serie A", "sport": "soccer", "edge": -0.02}
    ok, why = office.coach.check(p, {"ts": 0, "matches": {}})
    assert ok, why
    p["fair_prob"] = 0.48                                  # EV corretto ≈ −7%: sotto −3%, si blocca
    ok, why = office.coach.check(p, {"ts": 0, "matches": {}})
    assert not ok and "sotto" in why


def test_perche_counts_veto_reasons(tmp_path):
    import json
    from betbot import perche
    from betbot.store import Store
    st = Store(tmp_path / "p.db")
    for r in ("Rischio aperto entro 2.40 € (x)", "Rischio aperto entro 2.97 € (y)", "Kill switch non attivo"):
        st.event("risk", "VETO", "INFO", "veto", {"reasons": [r]})
    c = perche.check(st)
    assert dict(c["vetoes"])["Rischio aperto entro … €"] == 2 and "Perché il bot non punta" in perche.text(c)


def test_segmento_fonte_riferimento():
    from betbot.agents.coach import Coach
    assert Coach.segment_values({"ref_src": "oddspapi"})["fonte_rif"] == "OddsPapi"
    assert Coach.segment_values({})["fonte_rif"] == "standard"


def _lesson_side(o, clv, side, sport="soccer"):
    f = {"league": "Serie B", "odds": 1.3 if side == "BACK" else 4.0, "minutes_before": 90, "edge": 0.03, "source": "Pinnacle",
         "sport": sport, "side": side, "liquidity": 50}
    o.store.execute("INSERT INTO coach_lessons(ts, strategy_id, label, outcome, pnl, p_entry, clv, cause, features) "
                    "VALUES('t','S10_divertimento_v1','x','LOST',-1,0.8,?,'smentita',?)", (clv, json.dumps(f)))


def test_rules_are_learned_per_side_and_do_not_block_lays(office):
    """190 back negativi non devono bloccare i lay: 'sport = soccer' vale solo per il lato BACK."""
    c = Coach(office)
    for _ in range(c.min_n + 5):
        _lesson_side(office, -0.03, "BACK")
    c.learn()
    rules = {(r["feature"], r["value"]) for r in office.store.query("SELECT feature, value FROM coach_rules WHERE active=1")}
    assert ("sport_back", "soccer") in rules and not any(f.endswith("_lay") for f, _ in rules)
    snap = {"matches": {"M1": {"kickoff": "2099-01-01T12:00:00+00:00", "books": {"Pinnacle": {}}, "exchange": {}}}, "ts": 0}
    back = {"strategy_id": "S10_divertimento_v1", "match_id": "M1", "selection": "home", "sport": "soccer", "side": "BACK",
            "league": "Serie B", "odds": 1.3, "edge": 0.03}
    lay = {**back, "selection": "LAY:home", "side": "LAY", "odds": 4.0}
    assert c.check(back, snap)[0] is False
    assert c.check(lay, snap)[0] is True


def test_legacy_rules_without_side_are_retired(office):
    c = Coach(office)
    office.store.execute("INSERT INTO coach_rules(created, updated, strategy_id, kind, feature, value, n, clv, clv_hi, active, evidence) "
                         "VALUES('t','t','S10_divertimento_v1','blocca','sport','soccer',150,-0.009,-0.002,1,'{}')")
    c.learn()
    row = office.store.query("SELECT active FROM coach_rules WHERE feature='sport' AND value='soccer'")[0]
    assert row["active"] == 0


def test_perche_counts_lays_blocked_by_leo(tmp_path):
    from betbot import perche
    from betbot.agents.coach import CoachBook
    from betbot.store import Store
    from betbot.store import now_iso
    st = Store(tmp_path / "p.db")
    CoachBook(st)
    assert "nessuno" in perche.text(perche.check(st))
    st.execute("INSERT INTO coach_entries(ts, src, row_id, strategy_id, match_id, selection, side, odds, features, track, blocked_by) "
               "VALUES(?, 'shadow_bets', 1, 'S10_divertimento_v1', 'M', 'LAY:home', 'LAY', 4.0, '{}', '{}', ?)",
               (now_iso(), "Lezione di Leo: sport = soccer (CLV -0.9% su 147 puntate)"))
    txt = perche.text(perche.check(st))
    assert "Lay bloccati dalle lezioni di Leo (ultimi 14 giorni): 1 proposte" in txt and "sport = soccer" in txt


def test_regole_di_misura_trovano_posto_accanto_a_quelle_di_v1(office):
    """Con 12 regole massime quelle di S10 misura (che frenano il 4fun live) restavano fuori: ora devono esserci tutte."""
    c = Coach(office)
    assert c.max_rules >= 40
    for sid in ("S10_divertimento_v1", "S10_misura_v1"):
        for odds, n in ((1.5, 1), (1.9, 2), (2.6, 3)):
            for _ in range(c.min_n + 5):
                f = {"league": f"L{odds}", "odds": odds, "minutes_before": 90 + n * 100, "edge": 0.01 * n, "source": "Pinnacle",
                     "sport": "soccer", "side": "BACK", "liquidity": 10 ** n}
                office.store.execute("INSERT INTO coach_lessons(ts, strategy_id, label, outcome, pnl, p_entry, clv, cause, features) "
                                     "VALUES('t',?,'x','LOST',-1,0.5,-0.03,'smentita',?)", (sid, json.dumps(f)))
    c.learn()
    ids = {r["strategy_id"] for r in office.store.query("SELECT strategy_id FROM coach_rules WHERE active=1")}
    assert ids == {"S10_divertimento_v1", "S10_misura_v1"}
