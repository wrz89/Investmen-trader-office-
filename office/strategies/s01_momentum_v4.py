"""STRATEGY_01 — Momentum / trend following (v4).

Codice IDENTICO alla v3 (momentum 4h su BTC, ETH, XRP, SOL in USDC).
Cambia solo il test del drawdown Monte Carlo, corretto su decisione dell'utente:
ora simula la size reale del Risk Manager (0,5% di rischio / distanza dello stop,
tetto 20% per asset) invece di un'allocazione fissa del 20%.

Correzione decisa DOPO aver visto il risultato della v3: per questo vale solo
da questa versione in poi e l'hold-out finale (mai visto) resta il giudice.
"""
import pandas as pd

from office.indicators import ema

STRATEGY_ID = "STRATEGY_01_v4"
NAME = "Momentum EMA 4h (size reale)"
FAMILY = "momentum"
DESCRIPTION = "Identica alla v3; drawdown testato con la size reale del Risk Manager"
TIMEFRAME = "4h"
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
