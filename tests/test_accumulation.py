import sqlite3
import time
from datetime import datetime

import pytest

from office.accumulation import best_route, due_month, split_amount

CFG = {"enabled": True, "start_month": "2026-10", "day_of_month": 5}
T = {"BTC/EUR": 0.6, "ETH/EUR": 0.3, "SOL/EUR": 0.1}


def test_due_month():
    assert due_month(datetime(2026, 9, 29), CFG, set()) is None          # prima dell'inizio
    assert due_month(datetime(2026, 10, 4), CFG, set()) is None          # prima del giorno 5
    assert due_month(datetime(2026, 10, 5), CFG, set()) == "2026-10"
    assert due_month(datetime(2026, 10, 20), CFG, {"2026-10"}) is None   # già comprato
    assert due_month(datetime(2026, 11, 9), CFG, {"2026-10"}) == "2026-11"  # PC spento il 5: recupera


def test_split_follows_targets_and_rebalances_without_selling():
    legs = split_amount(100, T, {}, 5)
    assert legs == {"BTC/EUR": 60.0, "ETH/EUR": 30.0, "SOL/EUR": 10.0}
    # BTC è salito e pesa troppo: il mese va solo a ETH e SOL, BTC non si vende
    legs = split_amount(70, T, {"BTC/EUR": 300, "ETH/EUR": 60, "SOL/EUR": 20}, 5)
    assert "BTC/EUR" not in legs and abs(sum(legs.values()) - 70) < 0.02
    # una quota sotto il minimo passa all'asset più sotto peso
    legs = split_amount(70, T, {"BTC/EUR": 120, "ETH/EUR": 60, "SOL/EUR": 22}, 5)
    assert all(v >= 5 for v in legs.values()) and abs(sum(legs.values()) - 70) < 0.02


def test_best_route_counts_both_fees():
    r = best_route(60_000, 70_000, 0.8571, 0.0025, 0.0003)             # stessa quotazione sulle due strade
    assert r["best"] == "diretta" and r["gap_bps"] > 20                 # via USDC paga una commissione in più
    r = best_route(60_000, 68_000, 0.8571, 0.0025, 0.0003)             # prezzo in USDC molto più basso
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
              "ETH/EUR": {"ask": 2_300.0, "bid": 2_299.5, "timestamp": now},
              "SOL/EUR": {"ask": 100.0, "bid": 99.97, "timestamp": now},
              "USDC/EUR": {"ask": 0.8571, "bid": 0.857, "timestamp": now}}
    o.market = type("M", (), {"ticker": lambda self, s: quotes[s]})()
    o.accumulation.cfg["start_month"] = datetime.now().strftime("%Y-%m")
    o.accumulation.cfg["day_of_month"] = 1
    return o


SNAP = {"symbols": {"BTC/USDC": {"ask": 70_000.0, "anomalies": []}, "ETH/USDC": {"ask": 2_700.0, "anomalies": []},
                    "SOL/USDC": {"ask": 117.0, "anomalies": []}}, "health": {"error_rate": 0}}


def test_buys_once_per_month_and_log_is_immutable(office_):
    office_.accumulation.run(SNAP)
    office_.accumulation.run(SNAP)
    s = office_.accumulation.summary()
    assert s["months"] == 1 and s["buys"] == 3 and abs(s["eur_in"] - 105) < 0.02   # 70 + rata 140/4
    assert s["next_amount"] == 105                                                  # seconda rata il mese dopo
    with pytest.raises(sqlite3.DatabaseError):
        office_.store.execute("UPDATE accumulation_buys SET eur=1000")


def test_news_alarm_postpones_only_that_asset(office_):
    office_.store.set("news_blocks", {"SOL": {"until": time.time() + 3600, "reason": "Solana exploit"}})
    office_.accumulation.run(SNAP)
    s = office_.accumulation.summary()
    assert s["buys"] == 2 and s["due_now"] is True                      # SOL resta dovuto
    office_.store.set("news_blocks", {})
    office_.accumulation.run(SNAP)
    s = office_.accumulation.summary()
    assert s["buys"] == 3 and s["due_now"] is False
