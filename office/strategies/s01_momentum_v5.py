"""STRATEGY_01 — Momentum / trend following (v5).

Codice IDENTICO a v3 e v4 (momentum 4h su BTC, ETH, XRP, SOL in USDC).
La v4 falliva solo il drawdown Monte Carlo con size reale: 10,8% contro il 10%.
Come stabilito prima di vederne il risultato, non si alza la soglia: si riduce
il rischio per trade del Risk Manager da 0,5% a 0,35% (config/risk_limits.yaml).
Se il cancello walk-forward è superato, decide l'hold-out finale (feb 2025 - set 2026).
"""
import pandas as pd

from office.indicators import ema

STRATEGY_ID = "STRATEGY_01_v5"
NAME = "Momentum EMA 4h (rischio 0,35%)"
FAMILY = "momentum"
DESCRIPTION = "Identica a v3/v4; rischio per trade ridotto a 0,35%"
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
