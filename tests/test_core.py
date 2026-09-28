"""Test di sicurezza del motore: niente sguardi al futuro, costi corretti,
registro immutabile, veto del Risk Manager."""
import sqlite3

import numpy as np
import pandas as pd
import pytest

from office.backtest import CostModel, simulate
from office.store import Store
from office.validation import max_drawdown, profit_factor


def arrays(opens, lows, entry, exit_, atr=1.0, stop_atr=2.0, max_hold=None):
    n = len(opens)
    return {"ts": np.arange(n), "open": np.array(opens, float), "low": np.array(lows, float),
            "close": np.array(opens, float), "atr": np.full(n, atr),
            "entry": np.array(entry, bool), "exit": np.array(exit_, bool),
            "stop_atr": stop_atr, "max_hold": max_hold}


def test_entry_happens_at_next_open():
    p = arrays([100, 101, 102, 103, 104], [99, 100, 101, 102, 103],
               [True, False, False, False, False], [False, False, True, False, False])
    trades = simulate(p, 0, 5, CostModel(0, 0, 0))
    assert trades[0]["entry"] == 101          # segnale su barra 0 → ingresso all'apertura della barra 1
    assert trades[0]["exit"] == 103           # uscita su barra 2 → apertura barra 3


def test_stop_gap_exits_at_open_not_stop():
    p = arrays([100, 100, 90, 90], [100, 99, 89, 89], [True, False, False, False], [False] * 4)
    trades = simulate(p, 0, 4, CostModel(0, 0, 0))
    assert trades[0]["reason"] == "stop" and trades[0]["exit"] == 90   # stop 98, apre a 90


def test_costs_reduce_net_return():
    p = arrays([100, 100, 110, 110], [100, 100, 110, 110], [True, False, False, False],
               [False, True, False, False])
    t = simulate(p, 0, 4, CostModel(0.001, 3, 4))[0]
    assert t["gross"] == pytest.approx(0.10)
    assert t["net"] < t["gross"] - 0.002


def test_metrics():
    r = np.array([0.02, -0.01, 0.03, -0.02])
    assert profit_factor(r) == pytest.approx(0.05 / 0.03)
    assert max_drawdown(np.array([0.1, -0.5])) == pytest.approx(0.5)


def test_trade_log_is_immutable(tmp_path):
    s = Store(tmp_path / "t.db")
    s.execute("INSERT INTO trades(symbol, pnl_net) VALUES('BTC/EUR', 1.0)")
    with pytest.raises(sqlite3.IntegrityError):
        s.execute("UPDATE trades SET pnl_net=99")
    with pytest.raises(sqlite3.IntegrityError):
        s.execute("DELETE FROM trades")


def test_risk_manager_blocks_unvalidated_strategy(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFICE_RUNTIME_DIR", str(tmp_path))
    import importlib
    import office.config
    importlib.reload(office.config)
    import office.core
    importlib.reload(office.core)
    office_ = office.core.Office(connect_market=False)
    office_.market = type("M", (), {"amount_to_precision": lambda self, s, a: round(a, 6),
                                    "market_info": lambda self, s: {"min_cost": 5, "min_amount": 0}})()
    office_.cycle_id = "test"
    snapshot = {"symbols": {"BTC/EUR": {"ok": True, "anomalies": [], "bid": 100, "ask": 100.01,
                                        "spread_bps": 1, "depth_ask": 1e6, "data_age_s": 1}},
                "correlations": {}, "health": {"error_rate": 0}}
    opp = {"id": "x", "symbol": "BTC/EUR", "direction": "LONG", "price": 100.01, "strategy_id": "S",
           "strategy_status": "REJECTED", "stop": 98, "risk_pct": 0.02, "net_pct": None,
           "fees_pct": 0.002, "slippage_pct": 0.0006}
    decision = office_.risk.evaluate(opp, snapshot, office_.account, None)
    assert not decision["approved"]
    assert any("Strategia autorizzata" in r for r in decision["reasons"])


def test_strategy_timeframes():
    from office.strategies import by_id, timeframe_of
    assert timeframe_of(by_id("STRATEGY_01_v1"), "1h") == "1h"
    assert timeframe_of(by_id("STRATEGY_01_v2"), "1h") == "4h"


def test_version_split():
    from office.agents.portfolio_manager import _split
    assert _split("STRATEGY_01_v12") == ("STRATEGY_01", 12)


def test_rotation_holds_one_asset_at_a_time():
    from office.strategies import by_id
    rot = by_id("STRATEGY_04_v1")
    ts = pd.date_range("2024-01-01", periods=1500, freq="4h", tz="UTC").astype("int64") // 10**6
    rng = np.random.default_rng(0)
    data = {}
    for i, s in enumerate(["A/USDC", "B/USDC", "C/USDC"]):
        close = 100 * np.exp(np.cumsum(rng.normal(0.0005 * (i + 1), 0.01, len(ts))))
        data[s] = pd.DataFrame({"ts": ts, "open": close, "high": close * 1.01, "low": close * 0.99,
                                "close": close, "volume": 1.0})
    sig = rot.generate_multi(data, {"lookback": 84, "trend": 100, "stop_atr": 4.0})
    entries = sum(sig[s]["entry"].astype(int) for s in data)
    assert entries.max() <= 1                       # mai più di un asset scelto per ribilanciamento
    rebal = pd.to_datetime(ts, unit="ms", utc=True)
    assert all(rebal[i].weekday() == 0 and rebal[i].hour == 0 for i in np.flatnonzero(entries.to_numpy()))
