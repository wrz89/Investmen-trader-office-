"""Test del gruppo live: ordini veri dall'esito incerto, riconciliazione, saldo Betfair, Stream API, spegnimento."""
import asyncio
import json
import sqlite3
import time

import pytest

from betbot.store import Store


# ── strumenti comuni ─────────────────────────────────────────
class FakeBF:
    """Client Betfair finto. Abbina tutto al prezzo chiesto (un LAY anche a `lay_at`, se più basso: Betfair abbina
    al miglior prezzo disponibile); può perdere la risposta e far fallire la verifica."""

    def __init__(self, available=30.0, exposure=0.0):
        self.orders, self.n = {}, 0
        self.lose_response = self.fail_lookup = False
        self.lay_at = None
        self.available, self.exposure = available, exposure

    def place(self, market_id, selection_id, side, price, size, fill_or_kill=True, ref=None, order_ref=None,
              strategy_ref=None):
        self.n += 1
        avg = min(price, self.lay_at) if side == "LAY" and self.lay_at else price
        self.orders[order_ref] = {"betId": f"B{self.n}", "sizeMatched": size, "averagePriceMatched": avg,
                                  "status": "EXECUTION_COMPLETE", "side": side, "price": price}
        if self.lose_response:
            raise TimeoutError("risposta persa")
        return {"bet_id": f"B{self.n}", "matched": size, "avg_price": avg, "status": "EXECUTION_COMPLETE"}

    def current_orders(self, order_refs=None, market_ids=None):
        if self.fail_lookup:
            raise ConnectionError("rete giù")
        return [o for r, o in self.orders.items() if not order_refs or r in order_refs]

    def cleared_by_refs(self, order_refs):
        return []

    def cleared(self, bet_ids):
        return {}

    def account_funds(self):
        return {"availableToBetBalance": self.available, "exposure": self.exposure}

    def start_keepalive(self):
        pass

    def lays(self):
        return [o for o in self.orders.values() if o["side"] == "LAY"]


LIVE = {"mode": "live", "execution.provider": "betfair", "live_strategies": ["S05_favoriti_exchange_v2"]}
SNAP = {"health": {"delayed": False}, "matches": {}, "races": {}}


@pytest.fixture
def gates_open(monkeypatch):
    from betbot import local_settings
    ls = {**local_settings.DEFAULTS, "betfair": {**local_settings.DEFAULTS["betfair"], "verified": True,
                                                 "test_done": True, "live_enabled": True}}
    monkeypatch.setattr(local_settings, "load", lambda: ls)
    return ls


def _office(db, fake=None, overrides=None, **kw):
    from betbot.config import load_settings
    from betbot.core import SportOffice
    from betbot.feeds.mock import MockFeed
    ov = overrides or LIVE
    st = load_settings(ov)
    st["feed"]["mock"]["seed"] = 5
    feed = MockFeed(st)
    feed.speed = 1.0
    o = SportOffice(overrides=ov, db_path=db, feed=feed, **kw)
    if fake is not None:
        o.executor._client = fake
        o.client = fake
    return o


def _p(**kw):
    p = {"strategy_id": "S05_favoriti_exchange_v2", "strategy_status": "ATTIVA", "match_id": "1.300", "market_id": "1.300",
         "league": "Serie A", "label": "X - Y · X", "market": "h2h", "selection": "123", "bookmaker": "Betfair",
         "odds": 1.3, "fair_prob": 0.85, "edge": 0.05, "commission": 0.045, "n_books": 3, "dispersion": 0.0,
         "live": False, "odds_ts": 0, "reason": "test"}
    p.update(kw)
    return p


def _later(monkeypatch, seconds=300):
    from betbot import clock
    monkeypatch.setattr(clock, "_source", lambda: time.time() + seconds)


# ── n.13: live senza strategie ammesse → nessun euro vero si muove ─────────────
def test_live_without_live_strategies_never_touches_bankroll(tmp_path, monkeypatch):
    from betbot import local_settings
    monkeypatch.setattr(local_settings, "load", lambda: {**local_settings.DEFAULTS})
    o = _office(tmp_path / "live.db", overrides={"mode": "live", "live_strategies": [],
                                              "observe_strategies": ["S07_scalping_prepartita_v1", "S09_lay_valore_v1"]})
    before = o.bankroll.total

    async def cycles():
        return [await o.run_cycle() for _ in range(5)]

    res = asyncio.run(cycles())
    assert all(r["ok"] for r in res) and sum(r["proposals"] for r in res) > 0      # proposte ci sono state
    assert sum(r["placed"] for r in res) == 0
    assert o.bankroll.total == before and not o.bankroll.open_bets()
    assert not o.store.query("SELECT 1 FROM bets WHERE mode!='shadow'")
    assert not o.store.query("SELECT 1 FROM events WHERE level='ERROR'")


# ── n.14/23: BACK vero con esito sconosciuto ──────────────────
def test_unknown_back_sets_kill_switch_then_resolver_finds_it_matched(tmp_path, gates_open, monkeypatch):
    fake = FakeBF()
    o = _office(tmp_path / "live.db", fake)
    fake.lose_response = fake.fail_lookup = True
    assert o.banco.place(_p(), {"stake": 2.0, "kelly_full": 0.3}, "c1", SNAP) is None
    assert "esito sconosciuto" in o.store.get("kill_switch")                  # niente secondo ordine al ciclo dopo
    assert o.store.query("SELECT status FROM orders")[0]["status"] == "UNKNOWN"
    assert o.bankroll.cash == 30.0
    fake.lose_response = fake.fail_lookup = False
    assert o.resolve_orders() == 0                                            # meno di 2 minuti: si aspetta
    _later(monkeypatch)
    assert o.resolve_orders() == 1
    row = o.store.query("SELECT * FROM orders")[0]
    assert row["status"] == "MATCHED" and row["bet_id"] == "B1" and row["matched"] == 2.0
    assert "abbinato su Betfair" in o.store.get("kill_switch") and "B1" in o.store.get("kill_switch")
    # reset manuale dal PC dopo la verifica: al riavvio la stessa discrepanza non blocca di nuovo
    o.store.set("kill_switch", None)
    o2 = _office(tmp_path / "live.db", fake)
    o2.reconcile_live()
    assert o2.store.get("kill_switch") is None


def test_unknown_order_not_on_betfair_becomes_not_found(tmp_path, gates_open, monkeypatch):
    fake = FakeBF()
    o = _office(tmp_path / "live.db", fake)
    fake.fail_lookup = True
    fake.lose_response = True
    o.banco.place(_p(), {"stake": 2.0, "kelly_full": 0.3}, "c1", SNAP)
    fake.orders.clear()                                        # un fill-or-kill non abbinato sparisce da Betfair
    fake.fail_lookup = fake.lose_response = False
    o.store.set("kill_switch", None)
    o.reconcile_live()                                         # ancora giovane: esito incerto → blocco
    assert "esito incerto" in o.store.get("kill_switch")
    _later(monkeypatch)
    o.store.set("kill_switch", None)
    o.reconcile_live()                                         # dopo 2 minuti: NOT_FOUND, nessun blocco
    assert o.store.query("SELECT status FROM orders")[0]["status"] == "NOT_FOUND"
    assert o.store.get("kill_switch") is None


def test_order_not_sent_when_login_is_postponed(tmp_path, gates_open):
    from betbot.execution import Executor
    from betbot.feeds.betfair import BetfairClient
    c = BetfairClient({"app_key": "k", "username": "u", "password": "p"})
    c.last_login_try = time.time()                             # login appena tentato: rimandato di un minuto
    posts = []
    c.http.post = lambda *a, **k: posts.append(a) or (_ for _ in ()).throw(AssertionError("POST inviato"))
    settings = {"mode": "live", "execution": {"provider": "betfair", "min_stake": 2.0, "commission": 0.045},
                "live_strategies": ["S05_favoriti_exchange_v2"]}
    ex = Executor(settings, client=c, store=Store(tmp_path / "live.db"))
    r = ex.place(_p(), 2.0, SNAP)
    assert not r["ok"] and not r.get("unknown") and "non inviato" in r["error"]
    assert posts == []
    assert ex.store.query("SELECT status FROM orders")[0]["status"] == "NOT_SENT"


# ── n.15: LAY abbinato e crash prima della chiusura nel registro ────────────────
def test_matched_lay_after_crash_closes_bet_without_new_order(tmp_path, gates_open):
    fake = FakeBF()
    o = _office(tmp_path / "live.db", fake)
    bid = o.banco.place(_p(), {"stake": 2.0, "kelly_full": 0.3}, "c1", SNAP)
    assert o.store.query("SELECT bet_row_id FROM orders WHERE side='BACK'")[0]["bet_row_id"] == bid
    bet = o.store.query("SELECT * FROM bets WHERE id=?", (bid,))[0]
    o.executor.hedge(bet, 1.25, urgent=False)                  # LAY abbinato su Betfair… poi il processo muore
    assert o.store.query("SELECT bet_row_id FROM orders WHERE side='LAY'")[0]["bet_row_id"] == bid
    o2 = _office(tmp_path / "live.db", fake)                   # riavvio
    o2.reconcile_live()
    row = o2.store.query("SELECT * FROM bets WHERE id=?", (bid,))[0]
    assert row["status"] == "HEDGED" and row["pnl"] == pytest.approx((2.0 * 1.3 / 1.25 - 2.0) * 0.955, abs=0.01)
    assert o2.store.get("kill_switch") is None
    o2.banco.apply([{"bet_id": bid, "action": "hedge", "price": 1.25, "reason": "target"}])
    assert len(fake.lays()) == 1                               # nessun secondo LAY


def test_matched_lay_is_reused_by_banco_instead_of_sending_another(tmp_path, gates_open):
    fake = FakeBF()
    o = _office(tmp_path / "live.db", fake)
    bid = o.banco.place(_p(), {"stake": 2.0, "kelly_full": 0.3}, "c1", SNAP)
    bet = o.store.query("SELECT * FROM bets WHERE id=?", (bid,))[0]
    o.executor.hedge(bet, 1.25, urgent=False)
    assert o.banco.apply([{"bet_id": bid, "action": "hedge", "price": 1.25, "reason": "target"}]) == 1
    assert len(fake.lays()) == 1
    assert o.store.query("SELECT status FROM bets WHERE id=?", (bid,))[0]["status"] == "HEDGED"


def test_lay_with_unknown_outcome_blocks_new_lays_until_resolved(tmp_path, gates_open, monkeypatch):
    fake = FakeBF()
    o = _office(tmp_path / "live.db", fake)
    bid = o.banco.place(_p(), {"stake": 2.0, "kelly_full": 0.3}, "c1", SNAP)
    fake.lose_response = fake.fail_lookup = True
    o.banco.apply([{"bet_id": bid, "action": "hedge", "price": 1.25, "reason": "stop loss"}])
    assert "chiusura" in o.store.get("kill_switch")
    fake.lose_response = fake.fail_lookup = False
    for _ in range(3):                                         # il ciclo veloce riprova: nessun nuovo LAY
        o.banco.apply([{"bet_id": bid, "action": "hedge", "price": 1.25, "reason": "stop loss"}])
    assert len(fake.lays()) == 1
    _later(monkeypatch)
    o.resolve_orders()                                         # il LAY risulta abbinato: la puntata si chiude
    assert o.store.query("SELECT status FROM bets WHERE id=?", (bid,))[0]["status"] == "HEDGED"
    assert len(fake.lays()) == 1


def test_back_matched_without_bet_row_triggers_kill_switch_once(tmp_path, gates_open):
    fake = FakeBF()
    o = _office(tmp_path / "live.db", fake)
    o.store.execute("INSERT INTO orders(ts, ref, strategy_id, market_id, selection_id, side, price, size, status, bet_id, "
                    "matched, avg_price) VALUES('2026-01-01T00:00:00+00:00','bbX','S05','1.9','5','BACK',1.3,2,'MATCHED',"
                    "'B77',2,1.3)")
    o.reconcile_live()
    assert "senza puntata nel registro" in o.store.get("kill_switch")
    o.store.set("kill_switch", None)                           # verificato su betfair.it e resettato dal PC
    o.reconcile_live()
    assert o.store.get("kill_switch") is None


def test_orders_table_migration_adds_bet_row_id(tmp_path):
    path = tmp_path / "vecchio.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, ref TEXT UNIQUE, strategy_id TEXT, "
                "market_id TEXT, selection_id TEXT, side TEXT, price REAL, size REAL, status TEXT, bet_id TEXT, "
                "matched REAL, avg_price REAL, error TEXT, updated TEXT)")
    con.execute("INSERT INTO orders(ref, side, status) VALUES('bb1', 'BACK', 'MATCHED')")
    con.commit()
    con.close()
    s = Store(path)
    assert s.query("SELECT ref, bet_row_id FROM orders") == [{"ref": "bb1", "bet_row_id": None}]
    Store(path)                                                # la seconda apertura non rifà la migrazione


# ── n.16/22: Stream API ──────────────────────────────────────
CAT = {"1.100": {"marketId": "1.100", "_kind": "soccer", "marketStartTime": "2030-09-29T18:00:00.000Z",
                 "event": {"name": "Inter v Lecce"}, "competition": {"name": "Serie A"},
                 "description": {"marketBaseRate": 4.5},
                 "runners": [{"selectionId": 1, "runnerName": "Inter"}, {"selectionId": 2, "runnerName": "Lecce"},
                             {"selectionId": 3, "runnerName": "The Draw"}]}}


def _stream_feed():
    from betbot.config import load_settings
    from betbot.feeds.betfair import BetfairFeed
    st = load_settings()
    st["feed"]["betfair"]["stream"] = True
    f = BetfairFeed(st, creds={"app_key": "x", "username": "u", "password": "p"})
    f.cat = json.loads(json.dumps(CAT))
    return f


def _stream_prices(f, cache):
    return {mid: {"status": (cache.markets[mid]["definition"] or {}).get("status", "OPEN"), "inplay": False,
                  "runners": cache.best(mid)} for mid in f.cat if mid in cache.markets}


def test_stream_closed_market_uses_runner_status_not_void():
    from betbot.feeds.betfair import MarketCache
    f = _stream_feed()
    c = MarketCache()
    c.apply({"op": "mcm", "mc": [{"id": "1.100", "img": True, "marketDefinition": {"status": "CLOSED", "runners": [
        {"id": 1, "status": "WINNER"}, {"id": 2, "status": "LOSER"}, {"id": 3, "status": "LOSER"}]},
        "rc": [{"id": 1, "ltp": 1.01}]}]})
    m = f._snapshot(_stream_prices(f, c))["matches"]["1.100"]
    assert m["status"] == "FINISHED" and m["result"] == "home" and not m["void"]
    # stato dei runner non ancora arrivato: né vincitore né annullato, si aspetta
    c.apply({"op": "mcm", "mc": [{"id": "1.100", "img": True, "marketDefinition": {"status": "CLOSED"}}]})
    c.markets["1.100"]["runners"] = {1: {"batb": {}, "batl": {}, "ltp": None}}
    m = f._snapshot(_stream_prices(f, c))["matches"]["1.100"]
    assert m["result"] is None and not m["void"]
    # annullato solo se Betfair lo dice per ogni runner
    c.apply({"op": "mcm", "mc": [{"id": "1.100", "marketDefinition": {"status": "CLOSED", "runners": [
        {"id": 1, "status": "REMOVED"}, {"id": 2, "status": "REMOVED"}, {"id": 3, "status": "REMOVED"}]}}]})
    assert f._snapshot(_stream_prices(f, c))["matches"]["1.100"]["void"]


def test_stream_book_has_best_level_liquidity_for_paper_exchange():
    from betbot.execution import PaperExchange, best_size
    from betbot.feeds.betfair import MarketCache
    f = _stream_feed()
    c = MarketCache()
    c.apply({"op": "mcm", "mc": [{"id": "1.100", "img": True, "marketDefinition": {"status": "OPEN"},
             "rc": [{"id": 1, "batb": [[0, 1.30, 900], [1, 1.29, 50]], "batl": [[0, 1.31, 800]]},
                    {"id": 2, "batb": [[0, 12, 300]], "batl": [[0, 13, 300]]},
                    {"id": 3, "batb": [[0, 6, 300]], "batl": [[0, 6.2, 300]]}]}]})
    book = f._snapshot(_stream_prices(f, c))["matches"]["1.100"]["exchange"]["home"]
    assert book["back_size_best"] == 900 and book["lay_size_best"] == 800 and None not in book.values()
    assert PaperExchange(1.5).place("BACK", 1.30, 2.0, book)["ok"]
    assert PaperExchange(1.5).place("LAY", 1.31, 2.0, book)["ok"]
    assert best_size({"lay_size": 500}, "lay") == 500                         # manca il dato: ripiego sul totale
    assert best_size({"lay_size_best": 0.0, "lay_size": 500}, "lay") == 0.0   # 0 € al miglior prezzo resta 0


def test_stream_prices_keep_delayed_flag_from_rest(monkeypatch):
    from betbot.feeds import betfair as bfmod

    async def no_stream(*a, **k):
        await asyncio.sleep(3600)

    monkeypatch.setattr(bfmod, "stream_markets", no_stream)
    f = _stream_feed()
    f._refresh_catalogue = lambda: None
    f.cache.apply({"op": "mcm", "mc": [{"id": "1.100", "img": True, "marketDefinition": {"status": "OPEN"},
                   "rc": [{"id": 1, "batb": [[0, 1.3, 900]], "batl": [[0, 1.31, 800]]},
                          {"id": 2, "batb": [[0, 12, 300]], "batl": [[0, 13, 300]]},
                          {"id": 3, "batb": [[0, 6, 300]], "batl": [[0, 6.2, 300]]}]}]})
    assert asyncio.run(f.fetch())["health"]["delayed"]         # finché una lettura REST non dice il contrario
    f.data_delayed = False
    assert not asyncio.run(f.fetch())["health"]["delayed"]


# ── n.17: dopo un riavvio i mercati con posizioni aperte restano nel feed ────────
def test_restart_keeps_catalogue_of_open_positions(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from betbot import local_settings
    from betbot.config import load_settings
    from betbot.core import SportOffice
    from betbot.feeds.betfair import BetfairFeed
    monkeypatch.setattr(local_settings, "load", lambda: {**local_settings.DEFAULTS})
    ko = (datetime.now(timezone.utc) + timedelta(minutes=60)).isoformat().replace("+00:00", "Z")
    cat = {**CAT["1.100"], "marketStartTime": ko}
    del cat["_kind"]

    class Client:
        errors, calls = 0, 1

        def __init__(self, closed):
            self.closed, self.asked = closed, []

        def event_types(self):
            return {"1": "Soccer"}

        def catalogue(self, event_type, *a, **k):
            return [] if (self.closed or event_type != "1") else [cat]          # i mercati chiusi non tornano

        def books(self, ids):
            self.asked += ids
            if "1.100" not in ids:
                return []
            if self.closed:
                rs = [{"selectionId": 1, "status": "WINNER", "ex": {}}, {"selectionId": 2, "status": "LOSER", "ex": {}},
                      {"selectionId": 3, "status": "LOSER", "ex": {}}]
                return [{"marketId": "1.100", "status": "CLOSED", "inplay": False, "runners": rs}]
            ex = lambda b, l: {"availableToBack": [{"price": b, "size": 500}], "availableToLay": [{"price": l, "size": 500}]}
            return [{"marketId": "1.100", "status": "OPEN", "inplay": False, "runners": [
                {"selectionId": 1, "ex": ex(1.3, 1.31)}, {"selectionId": 2, "ex": ex(12, 13)},
                {"selectionId": 3, "ex": ex(6, 6.2)}]}]

    st = load_settings()
    st["feed"]["provider"] = "betfair"
    st["feed"]["betfair"]["sports"] = ["soccer"]
    f1 = BetfairFeed(st, creds={})
    f1.client = Client(closed=False)
    o = SportOffice(db_path=tmp_path / "p.db", feed=f1)
    snap = asyncio.run(o.quote.scan())
    o.banco.place(_p(market_id="1.100", match_id="1.100", selection="home", odds_ts=snap["ts"]),
                  {"stake": 2.0, "kelly_full": 0.3}, "c1", snap)
    o._watch_open_markets()                                    # come a fine ciclo
    assert o.store.get("bf_cat:1.100")["marketId"] == "1.100"
    f2 = BetfairFeed(st, creds={})
    f2.client = Client(closed=True)
    o2 = SportOffice(db_path=tmp_path / "p.db", feed=f2)       # riavvio: partita finita, Inter vincente
    o2._watch_open_markets()
    o2.banco.settle(asyncio.run(o2.quote.scan()))
    assert "1.100" in f2.client.asked
    assert o2.store.query("SELECT status FROM bets")[0]["status"] == "WON"
    o2._watch_open_markets()
    assert o2.store.get("bf_cat:1.100") is None                # nessuna posizione: voce tolta


# ── n.18: bankroll interno contro saldo vero ─────────────────
def test_live_balance_compares_total_bankroll(tmp_path, gates_open):
    fake = FakeBF(available=10.0)
    o = _office(tmp_path / "live.db", fake)
    assert o.bankroll.total == 30.0
    o._sync_live_balance(force=True)
    assert "superiore al saldo vero" in o.store.get("kill_switch")
    o.store.set("kill_switch", None)
    fake.available = 80.0                                      # altri soldi tuoi sul conto: solo un'informazione
    o._sync_live_balance(force=True)
    o._sync_live_balance(force=True)
    assert o.store.get("kill_switch") is None
    assert len(o.store.query("SELECT 1 FROM events WHERE kind='live_sync' AND message LIKE '%in più%'")) == 1


def test_startup_checks_compare_balance_and_realign_is_only_downward(tmp_path, gates_open):
    fake = FakeBF(available=25.0)
    o = _office(tmp_path / "live.db", fake)
    o.startup_checks()
    assert "superiore al saldo vero" in o.store.get("kill_switch")
    assert o.realign_live_bankroll() == pytest.approx(25.0) and o.bankroll.total == pytest.approx(25.0)
    fake.available = 60.0
    assert o.realign_live_bankroll() is None and o.bankroll.total == pytest.approx(25.0)


# ── n.19/28: in live il bankroll non torna a capital.initial ──────────────────
def test_live_bankroll_is_not_reset_to_capital_initial(tmp_path, gates_open, monkeypatch):
    from betbot.feeds import betfair as bfmod
    fake = FakeBF(available=47.3)
    monkeypatch.setattr(bfmod, "BetfairClient", lambda creds: fake)
    from betbot.core import SportOffice
    db = tmp_path / "live.db"
    o = SportOffice(overrides=LIVE, db_path=db)                # primo avvio: saldo vero
    assert o.bankroll.total == pytest.approx(47.3)
    o.store.execute("INSERT INTO bets(mode, strategy_id, match_id, selection, odds, stake) "
                    "VALUES('shadow','S07','1.1','home',2,2)")
    stato = SportOffice(overrides=LIVE, db_path=db, connect_feed=False)       # `betbot.py stato`: niente Betfair
    assert stato.bankroll.total == pytest.approx(47.3) and stato.bankroll.initial_capital == pytest.approx(47.3)
    o.store.execute("INSERT INTO bets(mode, strategy_id, match_id, selection, odds, stake, status, pnl) "
                    "VALUES('live','S05','1.2','home',1.3,2,'LOST',-2)")
    o.bankroll.cash = 45.3
    fake.available = 99.0                                      # dopo la prima puntata vera: mai più riallineato
    again = SportOffice(overrides=LIVE, db_path=db)
    assert again.bankroll.total == pytest.approx(45.3)


# ── n.20: chiusura con la puntata e il prezzo medio VERI del lay ───────────────
def test_live_hedge_books_worst_real_outcome(tmp_path, gates_open):
    from betbot.agents.banco import lay_close_outcomes
    fake = FakeBF()
    o = _office(tmp_path / "live.db", fake)
    bid = o.banco.place(_p(odds=3.0), {"stake": 2.0, "kelly_full": 0.3}, "c1", SNAP)
    fake.lay_at = 3.3                                          # limite urgente 3,4 ma Betfair abbina a 3,3
    o.banco.apply([{"bet_id": bid, "action": "hedge", "urgent": True, "price": 3.3, "reason": "stop loss"}])
    lay = fake.lays()[0]
    assert lay["price"] == pytest.approx(3.4) and lay["sizeMatched"] == pytest.approx(round(2 * 3.0 / 3.3, 2))
    win, lose = lay_close_outcomes(2.0, 3.0, lay["sizeMatched"], lay["averagePriceMatched"], 0.045)
    row = o.store.query("SELECT * FROM bets WHERE id=?", (bid,))[0]
    assert row["status"] == "HEDGED" and row["pnl"] == pytest.approx(min(win, lose), abs=1e-4)
    assert abs(win - lose) < 0.02                              # lay calcolato sul prezzo visto: quasi pareggiato


# ── n.25: l'uscita urgente non insegue oltre il prezzo peggiore del piano ──────
def _trade(o, worst):
    extra = {"exchange": {"worst": worst, "risk_per_unit": 0.0476, "commission": 0.045}} if worst else {}
    cur = o.store.execute("INSERT INTO bets(ts, mode, strategy_id, match_id, market, selection, bookmaker, odds, stake, "
                          "extra) VALUES('2026-01-01T00:00:00+00:00','paper','S07','M7','exchange_trade','home','Betfair',"
                          "2.0,2.0,?)", (json.dumps(extra),))
    return cur.lastrowid


def test_urgent_exit_stops_at_plan_worst_price(tmp_path, monkeypatch):
    from betbot import local_settings
    from betbot.feeds.mock import tick_up
    monkeypatch.setattr(local_settings, "load", lambda: {**local_settings.DEFAULTS})
    o = _office(tmp_path / "p.db", overrides={"mode": "paper"})
    limits = []

    def spy(bet, lay_price, urgent, snapshot=None):
        limits.append(tick_up(lay_price, 2) if urgent else lay_price)
        return {"ok": False, "error": "lay non abbinabile"}

    monkeypatch.setattr(o.executor, "hedge", spy)
    for seen, expected in ((2.06, [2.10]), (2.02, [2.06, 2.10]), (2.20, [2.24])):
        limits.clear()
        o.banco.apply([{"bet_id": _trade(o, 2.10), "action": "hedge", "urgent": True, "price": seen, "reason": "stop loss"}])
        assert limits == pytest.approx(expected)
    limits.clear()
    o.banco.apply([{"bet_id": _trade(o, None), "action": "hedge", "urgent": True, "price": 2.06, "reason": "stop loss"}])
    assert len(limits) == 3                                    # senza piano (nessun prezzo peggiore): come prima


# ── n.21: prezzi riletti se il ciclo è stato lento ─────────────────────────────
def test_slow_cycle_refetches_prices_before_new_bets(tmp_path, monkeypatch):
    from betbot import local_settings
    monkeypatch.setattr(local_settings, "load", lambda: {**local_settings.DEFAULTS})
    o = _office(tmp_path / "p.db", overrides={"mode": "paper"})
    calls = []
    real = o.feed.fetch

    async def counting():
        calls.append(1)
        return await real()

    o.feed.fetch = counting
    o.risk.limits["max_odds_age_seconds"] = -1                 # ogni ciclo "troppo lento"
    assert asyncio.run(o.run_cycle())["ok"]
    assert len(calls) == 2
    assert o.store.query("SELECT 1 FROM events WHERE message LIKE 'Ciclo lento%'")


# ── n.29: lo spegnimento non lascia un kill switch permanente ──────────────────
def test_interrupted_shutdown_leaves_no_kill_switch(tmp_path, monkeypatch):
    from betbot import local_settings
    monkeypatch.setattr(local_settings, "load", lambda: {**local_settings.DEFAULTS})
    db = tmp_path / "p.db"
    o = _office(db, overrides={"mode": "paper"})
    _trade(o, 2.10)
    monkeypatch.setattr(o.banco, "apply", lambda actions: 0)  # il lay di chiusura non si abbina

    async def interrupted():
        try:
            await asyncio.wait_for(o.shutdown(), 0.3)          # processo ucciso a metà spegnimento
        except asyncio.TimeoutError:
            pass

    asyncio.run(interrupted())
    snap = {"ts": 0, "sim_time": 0, "races": {}, "health": {"source": "mock"},
            "matches": {"M1": {"match_id": "M1", "status": "SCHEDULED",
                               "exchange": {"home": {"back": 1.3, "lay": 1.31, "back_size": 900, "lay_size": 900}}}}}
    p = _p(market_id="M1", match_id="M1", selection="home")
    assert o.banco.place(p, {"stake": 2.0, "kelly_full": 0.3}, "c", snap) is None          # spegnimento in corso
    o.shutting_down = False
    assert o.banco.place(p, {"stake": 2.0, "kelly_full": 0.3}, "c", snap)                  # controllo: altrimenti passa
    o2 = _office(db, overrides={"mode": "paper"})
    assert o2.risk.portfolio_state()["kill_switch"] is None
    o2.store.set("kill_switch", "spegnimento richiesto")      # database di una versione precedente
    o2.startup_checks()
    assert o2.store.get("kill_switch") is None
    o2.store.set("kill_switch", "fermato da Telegram")        # un kill switch vero resta
    o2.startup_checks()
    assert o2.store.get("kill_switch") == "fermato da Telegram"


# ── extra: The Odds API, un book vale solo se ogni esito è stato riconosciuto ───
def test_odds_api_skips_book_with_unrecognised_outcome(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from betbot.feeds import odds_api
    monkeypatch.setattr(odds_api, "api_key", lambda: "k")
    ko = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat().replace("+00:00", "Z")
    draw = {"Pinnacle": "Draw", "Bet365": "X", "Unibet": "Draw"}          # "X": nome del pareggio non capito

    def fake_get(self, path, **params):
        if path.endswith("/odds"):
            return [{"id": "e1", "home_team": "Inter", "away_team": "Lecce", "commence_time": ko, "sport_title": "Serie A",
                     "bookmakers": [{"title": b, "markets": [{"key": "h2h", "outcomes": [
                         {"name": "Inter", "price": 1.25}, {"name": "Lecce", "price": 12.0}, {"name": d, "price": 6.5}]}]}
                                    for b, d in draw.items()]}]
        return []
    monkeypatch.setattr(odds_api.OddsApiFeed, "_get", fake_get)
    f = odds_api.OddsApiFeed({"feed": {"sports": ["soccer_italy_serie_a"], "odds_api": {}}})
    books = asyncio.run(f.fetch())["matches"]["e1"]["books"]
    assert set(books) == {"Pinnacle", "Unibet"}                # niente 1X2 scambiato per un mercato a due esiti


# ── risposta persa e ordine non ancora visibile: "da confermare", mai un secondo ordine ─────────────
def test_lost_response_not_yet_visible_blocks_new_bets_until_resolved(tmp_path, gates_open, monkeypatch):
    class LateBF(FakeBF):
        """La richiesta arriva a Betfair, ma l'ordine compare solo qualche secondo dopo la verifica."""
        def place(self, *a, **k):
            try:
                return super().place(*a, **k)
            finally:
                self.hidden = dict(self.orders)
                self.orders.clear()

    fake = LateBF()
    fake.lose_response = True
    o = _office(tmp_path / "live.db", fake)
    assert o.banco.place(_p(), {"stake": 2.0, "kelly_full": 0.3}, "c1", SNAP) is None
    assert o.store.query("SELECT status FROM orders")[0]["status"] == "UNCONFIRMED"
    assert o.store.get("kill_switch") is None                 # un timeout di rete non blocca tutto…
    fake.lose_response = False
    assert o.banco.place(_p(), {"stake": 2.0, "kelly_full": 0.3}, "c2", SNAP) is None
    assert fake.n == 1                                         # …ma nessun secondo ordine finché non è chiarito
    fake.orders.update(fake.hidden)                            # l'ordine era partito davvero
    _later(monkeypatch)
    o.resolve_orders()
    assert o.store.query("SELECT status FROM orders")[0]["status"] == "MATCHED"
    assert o.store.get("kill_switch")                          # posizione vera fuori registro → blocco
