import sqlite3
import time
from datetime import datetime

import pytest

from office.accumulation import best_route, due_month

CFG = {"enabled": True, "start_month": "2026-10", "day_of_month": 5}


def test_due_month():
    assert due_month(datetime(2026, 9, 29), CFG, set()) is None          # prima dell'inizio
    assert due_month(datetime(2026, 10, 4), CFG, set()) is None          # prima del giorno 5
    assert due_month(datetime(2026, 10, 5), CFG, set()) == "2026-10"
    assert due_month(datetime(2026, 10, 20), CFG, {"2026-10"}) is None   # già comprato
    assert due_month(datetime(2026, 11, 9), CFG, {"2026-10"}) == "2026-11"  # PC spento il 5: recupera


def test_best_route_counts_both_fees():
    r = best_route(60_000, 70_000, 0.8571, 0.0025, 0.0003)             # stessa quotazione sulle due strade
    assert r["best"] == "diretta" and r["gap_bps"] > 20                 # via USDC paga una commissione in più
    r = best_route(60_000, 68_000, 0.8571, 0.0025, 0.0003)             # BTC/USDC molto più basso
    assert r["best"] == "via USDC"


@pytest.fixture
def office_(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFICE_RUNTIME_DIR", str(tmp_path))
    import importlib
    import office.config
    importlib.reload(office.config)
    import office.core
    importlib.reload(office.core)
    o = office.core.Office(connect_market=False)
    now = time.time() * 1000
    quotes = {"BTC/EUR": {"ask": 60_000.0, "bid": 59_990.0, "timestamp": now},
              "USDC/EUR": {"ask": 0.8571, "bid": 0.857, "timestamp": now}}
    o.market = type("M", (), {"ticker": lambda self, s: quotes[s]})()
    o.accumulation.cfg["start_month"] = datetime.now().strftime("%Y-%m")
    o.accumulation.cfg["day_of_month"] = 1
    return o


SNAP = {"symbols": {"BTC/USDC": {"ask": 70_000.0, "anomalies": []}}, "health": {"error_rate": 0}}


def test_buys_once_per_month_and_log_is_immutable(office_):
    office_.accumulation.run(SNAP)
    office_.accumulation.run(SNAP)
    s = office_.accumulation.summary()
    assert s["buys"] == 1 and s["eur_in"] == 50 and 0 < s["qty"] < 50 / 60_000
    with pytest.raises(sqlite3.DatabaseError):
        office_.store.execute("UPDATE accumulation_buys SET eur=1000")


def test_news_alarm_postpones_never_skips(office_):
    office_.store.set("news_blocks", {"ALL": {"until": time.time() + 3600, "reason": "Bybit hacked"}})
    office_.accumulation.run(SNAP)
    assert office_.accumulation.summary()["buys"] == 0
    office_.store.set("news_blocks", {})
    office_.accumulation.run(SNAP)
    assert office_.accumulation.summary()["buys"] == 1
