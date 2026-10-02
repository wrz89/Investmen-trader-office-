"""Multiple virtuali dalle puntate in ombra di S10 misura."""
import pytest

from betbot import multiple as M


def _row(i, odds, status, day="2026-10-02", sport="tennis"):
    return {"ts": f"{day}T{10 + i:02d}:00:00+00:00", "match_id": f"M{i}", "selection": "home", "odds": odds,
            "fair_prob": 1 / odds, "status": status, "sport": sport}


def test_doubles_and_triples_pnl_and_fragile_legs():
    rows = [_row(0, 2.0, "WON"), _row(1, 1.5, "WON"), _row(2, 2.0, "LOST"), _row(3, 1.8, "WON"),
            _row(4, 2.0, "WON"), _row(5, 2.0, "WON"), _row(6, 1.5, "VOID")]
    d = M.evaluate(rows, 2)
    # gruppi: (0,1) vinta 2,0×1,5 = 3,0 → +1,91; (2,3) persa → −1; (4,5) vinta 4,0 → +2,865; (6) incompleto
    assert d["n"] == 3 and d["vinte"] == pytest.approx(2 / 3)
    assert d["roi"] == pytest.approx((2.0 * 0.955 - 1.0 + 3.0 * 0.955) / 3)
    t = M.evaluate(rows, 3)
    assert t["n"] == 2                                   # (0,1,2) persa e (3,4,5) vinta
    assert M.evaluate(rows[:2], 3) == {"n": 0}


def test_same_match_twice_and_text(tmp_path):
    rows = [_row(0, 2.0, "WON"), {**_row(0, 2.0, "WON")}, _row(1, 2.0, "LOST")]
    assert len(M.groups(rows, 2)) == 1                   # la stessa partita non entra due volte
    s = {"singole": {"n": 3, "roi": -0.02}, "doppie": M.evaluate([_row(0, 2.0, "WON"), _row(1, 2.0, "LOST")], 2),
         "triple": {"n": 0}}
    txt = M.text(s)
    assert "Multiple virtuali" in txt and "doppie: 1" in txt and "triple: non ancora" in txt


def test_summary_reads_shadow_bets(tmp_path):
    from betbot.store import Store
    st = Store(tmp_path / "o.db")
    st.execute("INSERT INTO matches(match_id, sport, home, away, kickoff, status) VALUES('M0', 'tennis', 'a', 'b', 'x', 'FINISHED')")
    for i, status in enumerate(("WON", "LOST")):
        st.execute("INSERT INTO shadow_bets(ts, strategy_id, match_id, label, selection, odds, fair_prob, edge, stake, status, pnl) "
                   "VALUES(?, 'S10_misura_v1', ?, 'x', 'home', 2.0, 0.5, 0, 1, ?, 0)",
                   (f"2026-10-02T1{i}:00:00+00:00", f"M{i}", status))
    s = M.summary(st)
    assert s["singole"]["n"] == 2 and s["doppie"]["n"] == 1
