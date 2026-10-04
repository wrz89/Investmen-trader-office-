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


def test_manual_cash_out_is_added_to_settled_pnl(tmp_path, monkeypatch):
    from betbot import local_settings
    monkeypatch.setattr(local_settings, "load", lambda: {**local_settings.DEFAULTS})
    from betbot.config import load_settings
    from betbot.core import SportOffice
    from betbot.feeds.mock import MockFeed
    office = SportOffice(db_path=tmp_path / "o.db", feed=MockFeed(load_settings()))
    ins = ("INSERT INTO bets (ts, mode, strategy_id, match_id, market, selection, bookmaker, odds, stake, status, label, extra) "
           "VALUES ('x', 'live', 'S10_divertimento_v2', '1.9', 'h2h', 'home', 'Betfair', 2.0, 4, 'OPEN', 'Celta', ?)")
    office.store.execute(ins, (json.dumps({"betfair": {"bet_id": "B1", "market_id": "1.9", "selection_id": "55"}}),))
    office.store.set("cash", 100.0)
    ex = office.executor
    ex.settled_live = lambda bets: {"B1": {"profit": 4.0, "outcome": "WON", "status": "SETTLED"}}
    ex.manual_profit = lambda m, s, own: -3.2                  # cash out fatto a mano: perde 3,20 €
    office.banco.settle({"matches": {}, "races": {}})
    b = office.store.query("SELECT status, pnl FROM bets WHERE id=1")[0]
    assert b["status"] == "WON" and abs(b["pnl"] - 0.8 * (1 - office.banco._commission({"market": "h2h", "bookmaker": "Betfair"}))) < 0.05
