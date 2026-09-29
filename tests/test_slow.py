import numpy as np
import pandas as pd

from office.backtest import CostModel, prepare, simulate
from office.config import load_yaml
from office.strategies import by_id, universe_of
from office.validation import bar_returns, validate_allocation


def _df(closes):
    closes = np.asarray(closes, float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    ts = np.arange(len(closes)) * 86_400_000 + 1_500_000_000_000
    return pd.DataFrame({"ts": ts, "open": opens, "high": np.maximum(opens, closes) * 1.001,
                         "low": np.minimum(opens, closes) * 0.999, "close": closes, "volume": 1.0})


def test_bar_returns_match_trade():
    df = _df([100, 100, 110, 121, 121, 110])
    p = {"ts": df["ts"].to_numpy(), "open": df["open"].to_numpy(float), "close": df["close"].to_numpy(float)}
    trade = {"entry_ts": int(df["ts"][2]), "exit_ts": int(df["ts"][4]), "entry": 100.0, "exit": 121.0}
    r, bh = bar_returns(p, [trade], 0, 6, 0.0)
    assert np.isclose(np.prod(1 + r) - 1, 121 / 100 - 1)          # tutto il guadagno del trade, niente di più
    assert r[0] == r[1] == r[4] == r[5] == 0.0
    assert np.isclose(np.prod(1 + bh) - 1, 110 / 100 - 1)


def test_strategy_06_is_slow_and_btc_only():
    m = by_id("STRATEGY_06_v1")
    assert m.SIZING == "allocation" and m.TIMEFRAME == "1d"
    assert universe_of(m, ["BTC/USDC", "ETH/USDC"]) == ["BTC/USDC"]
    df = _df(np.r_[np.linspace(100, 50, 400), np.linspace(50, 150, 400)])
    sig = m.generate(df, {"sma": 200, "band": 0.02, "stop_atr": 6.0})
    trades = simulate(prepare(df, m, {"sma": 200, "band": 0.02, "stop_atr": 6.0}, sig), 0, len(df), CostModel())
    assert 1 <= len(trades) <= 3                                    # un trend = pochi scambi


def test_validate_allocation_runs_on_synthetic_cycles():
    rng = np.random.default_rng(1)
    x = np.arange(3000)
    closes = 100 * np.exp(0.6 * np.sin(x / 160) + np.cumsum(rng.normal(0, 0.01, 3000)))
    gates = load_yaml("quant_gates.yaml")
    research = {"holdout_fraction": 0.2, "walk_forward_windows": 6, "train_fraction": 0.4}
    v = validate_allocation(by_id("STRATEGY_06_v1"), {"BTC/USDC": _df(closes)}, CostModel(0.0025, 3, 6),
                            gates, research, 0.2, 100, "1d")
    keys = {c["key"] for c in v["checks"]}
    assert {"sharpe_bh", "dd_vs_bh", "dsr", "mc_dd"} <= keys
    assert v["sizing"] == "allocation" and v["metrics"]["max_dd_buy_hold"] > 0
