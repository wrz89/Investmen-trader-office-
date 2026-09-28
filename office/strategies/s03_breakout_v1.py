"""STRATEGY_03 — Breakout da compressione di volatilità (v1).

Idea: dopo fasi di bassa volatilità (bande di Bollinger strette) i movimenti
direzionali tendono a espandersi. Si entra sulla rottura del massimo di
Donchian se la volatilità era compressa, si esce sulla rottura del minimo
di breve o allo stop.
"""
import pandas as pd

from office.indicators import bollinger_bandwidth, donchian_high, donchian_low, ema, rolling_rank

STRATEGY_ID = "STRATEGY_03_v1"
NAME = "Breakout Compressione"
FAMILY = "breakout"
DESCRIPTION = "Rottura Donchian dopo compressione delle bande di Bollinger"
PARAM_GRID = {
    "squeeze_pct": [0.2, 0.3],
    "dc_len": [20, 40],
    "exit_len": [10, 20],
    "stop_atr": [2.0],
}


def generate(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    close = df["close"]
    squeeze = rolling_rank(bollinger_bandwidth(close), 100) < p["squeeze_pct"]
    breakout = close > donchian_high(df, p["dc_len"]).shift(1)
    entry = breakout & squeeze.shift(1).fillna(False).astype(bool) & (close > ema(close, 200))
    exit_ = close < donchian_low(df, p["exit_len"]).shift(1)
    return pd.DataFrame({"entry": entry.fillna(False), "exit": exit_.fillna(False)})
