"""STRATEGY_05 — Mean reversion in fase laterale, 4h (v1). Strategia DIVERSIFICANTE.

Regole fissate il 28/09/2026 PRIMA di qualsiasi test:
  • si opera solo quando il mercato è laterale: ADX(14) sotto `adx_max`;
  • si compra quando la chiusura scende sotto la banda di Bollinger inferiore
    (20 candele, `band` deviazioni standard) con RSI(14) sotto 35;
  • si esce al ritorno sulla media a 20 candele, dopo 30 candele, o allo stop (2,5 ATR).
Perché dovrebbe diversificare rispetto alla v5: guadagna quando il prezzo oscilla
senza direzione, cioè proprio quando il momentum tende a perdere.
Nota: la mean reversion a 1h (STRATEGY_02_v1) è stata rifiutata per i costi; a 4h i
movimenti sono più ampi rispetto al costo per trade. Massimo 2 tentativi (v1, v2).
"""
import pandas as pd

from office.indicators import adx, rsi

STRATEGY_ID = "STRATEGY_05_v1"
NAME = "Mean reversion in laterale"
FAMILY = "mean_reversion"
DESCRIPTION = "Compra sotto la banda di Bollinger solo con ADX basso (mercato laterale), 4h"
TIMEFRAME = "4h"
DIVERSIFIER_OF = "STRATEGY_01_v5"
PARAM_GRID = {
    "adx_max": [20, 25],
    "band": [2.0, 2.5],
    "stop_atr": [2.5],
    "max_hold": [30],
}


def generate(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    close = df["close"]
    mid = close.rolling(20).mean()
    lower = mid - p["band"] * close.rolling(20).std()
    entry = (adx(df, 14) < p["adx_max"]) & (close < lower) & (rsi(close, 14) < 35)
    exit_ = close > mid
    return pd.DataFrame({"entry": entry.fillna(False), "exit": exit_.fillna(False)})
