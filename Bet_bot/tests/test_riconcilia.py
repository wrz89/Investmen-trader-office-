import json

from betbot import riconcilia
from betbot.store import Store


class C:
    def current_orders(self, market_ids=None, order_refs=None):
        return [{"betId": "B1", "status": "EXECUTABLE", "sizeMatched": 2.0}] if market_ids == ["1.1"] else []

    def cleared(self, ids):
        return {"B2": {"profit": -2.0, "outcome": "LOSE", "status": "SETTLED"}}


def test_riconcilia_reports_each_open_bet(tmp_path):
    st = Store(tmp_path / "l.db")
    ins = ("INSERT INTO bets (ts, mode, strategy_id, match_id, market, selection, bookmaker, odds, stake, status, label, extra) "
           "VALUES ('x', 'live', 'S10_divertimento_v2', ?, 'h2h', 'home', 'Betfair', 1.8, 2, 'OPEN', ?, ?)")
    st.execute(ins, ("1.1", "A", json.dumps({"betfair": {"bet_id": "B1", "market_id": "1.1"}})))
    st.execute(ins, ("1.2", "B", json.dumps({"betfair": {"bet_id": "B2", "market_id": "1.2"}})))
    st.execute(ins, ("1.3", "C", json.dumps({})))
    r = {x["label"]: x["stato"] for x in riconcilia.check(store=st, client=C())}
    assert r == {"A": "ANCORA IN CORSO su Betfair", "B": "REGOLATA su Betfair", "C": "SENZA ID BETFAIR"}
