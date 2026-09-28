"""STRATEGY_01 — Momentum / trend following (v1).

Idea: in crypto i trend tendono a persistere più di quanto il rumore
suggerisca. Si entra quando la media veloce incrocia al rialzo la lenta
e il prezzo è sopra la media di lungo periodo (filtro di regime).
Si esce quando la media veloce torna sotto la lenta, o allo stop.
"""
import pandas as pd

from office.indicators import ema

STRATEGY_ID = "STRATEGY_01_v1"
NAME = "Momentum EMA"
FAMILY = "momentum"
DESCRIPTION = "Incrocio EMA veloce/lenta con filtro trend EMA200, stop ATR"
PARAM_GRID = {
    "fast": [12, 20, 30],
    "slow": [50, 100],
    "stop_atr": [2.0, 3.0],
}


def generate(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    close = df["close"]
    fast, slow, trend = ema(close, p["fast"]), ema(close, p["slow"]), ema(close, 200)
    cross_up = (fast > slow) & (fast.shift(1) <= slow.shift(1))
    entry = cross_up & (close > trend)
    exit_ = fast < slow
    return pd.DataFrame({"entry": entry.fillna(False), "exit": exit_.fillna(False)})
