"""Test della revisione finale: segreti fuori dai messaggi d'errore, certificato Betfair mancante, età delle quote
misurata fino alla valutazione, green-up in attesa di regolamento nel confronto con il saldo vero, migrazione
di un database creato dalla versione precedente."""
import sqlite3
import time

import pytest
import requests

from betbot.feeds.base import FeedError
from betbot.store import now_iso

from test_live import FakeBF, _office, gates_open  # noqa: F401  (gates_open è una fixture)
from test_rischio import _proposal, _snap, office  # noqa: F401  (office è una fixture)


# ── n.30: la chiave di The Odds API non finisce negli eventi ─────────────────
def test_odds_api_error_hides_api_key(monkeypatch, tmp_path):
    from betbot.config import load_settings
    from betbot.feeds import odds_api
    feed = odds_api.OddsApiFeed.__new__(odds_api.OddsApiFeed)
    feed.key, feed.calls, feed.errors = "SEGRETA123", 0, 0
    monkeypatch.setattr(feed, "_budget", lambda: {"used": 0}, raising=False)
    monkeypatch.setattr(feed, "_save_budget", lambda b: None, raising=False)

    def boom(url, params=None, timeout=None):
        raise requests.ConnectionError(f"HTTPSConnectionPool: Max retries exceeded with url: {url}?apiKey={params['apiKey']}")
    monkeypatch.setattr(odds_api.requests, "get", boom)
    with pytest.raises(FeedError) as e:
        feed._get("/sports")
    assert "SEGRETA123" not in str(e.value) and "***" in str(e.value)
    assert e.value.__cause__ is None and e.value.__suppress_context__


# ── n.42: certificato Betfair mancante → BetfairError chiaro, non OSError ─────
def test_betfair_login_missing_certificate_is_a_betfair_error(tmp_path):
    from betbot.feeds.betfair import BetfairClient, BetfairError
    missing = tmp_path / "manca.crt"
    c = BetfairClient({"app_key": "k", "username": "u", "password": "p",
                       "cert_file": f'"{missing}"', "key_file": f' "{tmp_path / "manca.key"}" '})
    assert c.cert == (str(missing), str(tmp_path / "manca.key"))          # virgolette di Windows tolte
    with pytest.raises(BetfairError) as e:
        c.login()
    assert "certificato" in str(e.value) and "manca.crt" in str(e.value)


# ── n.21: l'età delle quote conta anche il tempo passato dopo la lettura ─────
def test_odds_age_counts_time_since_fetch(office):
    state = office.risk.portfolio_state()
    fresh = {**_snap(), "fetched_mono": time.monotonic()}
    assert office.risk.evaluate(_proposal(fair_prob=0.85, edge=0.05), fresh, state)["approved"]
    stale = {**_snap(), "fetched_mono": time.monotonic() - 10_000}
    d = office.risk.evaluate(_proposal(fair_prob=0.85, edge=0.05), stale, state)
    assert not d["approved"] and any("Quote fresche" in r for r in d["reasons"])


def test_run_cycle_marks_snapshot_fetch_time(office):
    import asyncio
    before = time.monotonic()
    assert asyncio.run(office.run_cycle())["ok"]
    assert before <= office.cache["fetched_mono"] <= time.monotonic()      # lo snapshot che il Risk Manager valuta


# ── saldo vero: i green-up non ancora regolati da Betfair non sono un ammanco ──
def _hedged(o, pnl, kickoff=None, match="1.500"):
    if kickoff:
        o.store.execute("INSERT OR REPLACE INTO matches(match_id, kickoff, status) VALUES(?,?,?)",
                        (match, kickoff, "SCHEDULED"))
    o.store.execute("INSERT INTO bets(ts, mode, strategy_id, match_id, market, selection, odds, stake, status, "
                    "settled_ts, payout, pnl) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (now_iso(), "live", "S07_scalping_prepartita_v1", match, "exchange_trade", "home", 2.0, 2.0,
                     "HEDGED", now_iso(), 2.0 + pnl, pnl))


def test_unsettled_green_is_not_a_missing_balance(tmp_path, gates_open):  # noqa: F811
    fake = FakeBF(available=30.0)
    o = _office(tmp_path / "live.db", fake)
    _hedged(o, 0.9, match="1.501")
    _hedged(o, 0.8, match="1.502")
    o.bankroll.cash = o.bankroll.cash + 1.7                    # il bankroll interno ha già i due green-up
    o._sync_live_balance(force=True)
    assert o.store.get("kill_switch") is None
    assert o.store.get("live_balance")["unsettled_green"] == pytest.approx(1.7)


def test_settled_green_missing_from_balance_still_blocks(tmp_path, gates_open):  # noqa: F811
    # un green-up su una partita finita da ore è già stato accreditato: se il saldo non lo contiene, c'è un ammanco
    o = _office(tmp_path / "live.db", FakeBF(available=30.0))
    _hedged(o, 1.7, kickoff="2000-01-01T10:00:00+00:00", match="1.503")
    o.bankroll.cash = o.bankroll.cash + 1.7
    o._sync_live_balance(force=True)
    assert "superiore al saldo vero" in (o.store.get("kill_switch") or "")


# ── migrazione: database creato dalla versione prima delle correzioni ─────────
OLD_ORDERS = """CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, ref TEXT UNIQUE, strategy_id TEXT, market_id TEXT, selection_id TEXT, side TEXT, price REAL, size REAL,
    status TEXT, bet_id TEXT, matched REAL, avg_price REAL, error TEXT, updated TEXT
)"""


def test_old_database_gets_bet_row_id_and_live_office_starts(tmp_path, gates_open):  # noqa: F811
    db = tmp_path / "live.db"
    con = sqlite3.connect(db)
    con.execute(OLD_ORDERS)
    con.execute("INSERT INTO orders(ts, ref, side, price, size, status, bet_id, matched) "
                "VALUES('2026-01-01T00:00:00+00:00','bbvecchio','BACK',1.5,2,'KILLED',NULL,0)")
    con.commit()
    con.close()
    o = _office(db, FakeBF())
    o.startup_checks()
    cols = {r["name"] for r in o.store.query("PRAGMA table_info(orders)")}
    assert "bet_row_id" in cols
    assert o.store.query("SELECT ref FROM orders")[0]["ref"] == "bbvecchio"
    assert o.store.get("kill_switch") is None


# ── simulatore: una partita di tennis sull'1-1 arriva al terzo set e finisce ──
def test_mock_tennis_at_one_set_all_finishes():
    from betbot.config import load_settings
    from betbot.feeds.mock import MockFeed, is_tennis
    st = load_settings()
    st["feed"]["mock"]["seed"] = 11
    feed = MockFeed(st)
    feed.speed = 1.0
    m = None
    for _ in range(400):
        m = feed._new_match()
        if is_tennis(m["sport"]):
            break
    assert is_tennis(m["sport"])
    start = m["kickoff_epoch"]
    feed._tick_match(m, start + 1)
    assert m["status"] == "LIVE"
    m.update(home_score=1, away_score=1, seen_minute=85, minute=85)
    feed._tick_match(m, start + 125 * 60)
    assert m["status"] == "FINISHED" and m["result"] in ("home", "away")
