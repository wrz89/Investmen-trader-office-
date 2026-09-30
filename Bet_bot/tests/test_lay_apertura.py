"""Lay d'apertura (S09): dimensionamento sulla responsabilità, conti del libro, ordini veri e riconciliazione."""
import pytest

from betbot.execution import round_lay_liability
from tests.test_live import SNAP, FakeBF, _office, gates_open  # noqa: F401  (fixture)

LAY_LIVE = {"mode": "live", "execution.provider": "betfair", "execution.lay_apertura": True,
            "live_strategies": ["S09_lay_valore_v1"], "active_strategies": ["S09_lay_valore_v1"], "observe_strategies": []}


def _lay(**kw):
    p = {"strategy_id": "S09_lay_valore_v1", "strategy_status": "ATTIVA", "side": "LAY", "match_id": "1.400",
         "market_id": "1.400", "league": "Serie A", "label": "Inter - Lecce · CONTRO Lecce", "market": "lay_h2h",
         "selection": "LAY:456", "bookmaker": "Betfair", "odds": 4.0, "fair_prob": 0.20, "edge": 0.055,
         "commission": 0.045, "n_books": 1, "dispersion": 0.0, "ref_source": "Pinnacle", "live": False, "odds_ts": 0, "reason": "test"}
    p.update(kw)
    return p


def test_lay_liability_rounding():
    assert round_lay_liability(1.5, 4.0) == 1.5                   # 0,50 € del backer × 3
    assert round_lay_liability(1.49, 4.0) == 0.0                  # sotto il minimo di betfair.it
    assert round_lay_liability(2.0, 4.0) == pytest.approx(1.98)   # 0,66 € × 3


def test_lay_sized_on_liability_and_small(tmp_path):
    o = _office(tmp_path / "p.db", overrides={"execution.lay_apertura": True})
    st = o.risk.portfolio_state()
    d = o.risk.evaluate(_lay(), {"ts": 0, "matches": {}}, st)
    assert d["approved"], d["reasons"]
    assert d["stake"] >= 1.5 and d["risk"] == pytest.approx(d["stake"])       # rischio = responsabilità
    assert d["stake"] <= 0.10 * st["stake_base"] + 1e-9


def test_lay_off_by_default_goes_to_shadow(tmp_path):
    o = _office(tmp_path / "p.db", overrides={"mode": "paper"})
    assert o.banco.place(_lay(), {"stake": 1.5, "kelly_full": 0.1}, "c", SNAP) is None
    assert not o.store.query("SELECT 1 FROM bets WHERE mode!='shadow'")


def test_live_opening_lay_booked_and_settled(tmp_path, gates_open):
    fake = FakeBF()
    o = _office(tmp_path / "live.db", fake, overrides=LAY_LIVE)
    snap = {"health": {"delayed": False}, "races": {},
            "matches": {"1.400": {"exchange": {"456": {"lay": 4.0, "lay_size_best": 50}}}}}
    before = o.bankroll.cash
    bid = o.banco.place(_lay(), {"stake": 1.5, "kelly_full": 0.1}, "c1", snap)
    assert bid
    order = o.store.query("SELECT side, role, size, bet_row_id FROM orders")[0]
    assert order["side"] == "LAY" and order["role"] == "open" and order["size"] == 0.5 and order["bet_row_id"] == bid
    bet = o.store.query("SELECT stake, odds, selection FROM bets WHERE id=?", (bid,))[0]
    assert bet["stake"] == pytest.approx(1.5) and bet["selection"] == "LAY:456"
    assert o.bankroll.cash == pytest.approx(before - 1.5)                      # si blocca la responsabilità
    # un LAY d'apertura NON è un lay di chiusura: al riavvio non chiude la sua stessa puntata
    assert o.banco.close_matched_lays("test") == 0
    o.reconcile_live()
    assert o.store.get("kill_switch") is None
    assert o.store.query("SELECT status FROM bets WHERE id=?", (bid,))[0]["status"] == "OPEN"


def test_paper_lay_settles_both_ways(tmp_path):
    o = _office(tmp_path / "p.db", overrides={"execution.lay_apertura": True})
    b = o.banco
    for i, (result, pnl) in enumerate((("home", 0.5 * 0.955), ("away", -1.5))):
        o.store.execute("INSERT INTO bets(ts, mode, strategy_id, match_id, label, market, selection, bookmaker, odds, "
                        "stake, status, extra) VALUES('t','paper','S09_lay_valore_v1',?,'x','lay_h2h','LAY:away','Betfair',"
                        "4.0,1.5,'OPEN','{\"commission\": 0.045}')", (f"M{i}",))
        b.settle({"matches": {f"M{i}": {"status": "FINISHED", "result": result, "home_score": 1, "away_score": 0}}})
        row = o.store.query("SELECT status, pnl FROM bets WHERE match_id=?", (f"M{i}",))[0]
        assert row["pnl"] == pytest.approx(pnl, abs=1e-3) and row["status"] == ("WON" if pnl > 0 else "LOST")
