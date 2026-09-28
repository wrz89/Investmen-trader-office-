"""STRATEGY_01 — Momentum / trend following (v2).

Cosa cambia rispetto alla v1: SOLO il timeframe, da 1h a 4h.
Stessa logica, stessi parametri candidati.

Motivazione (dalla validazione della v1 su dati Bybit, 28/09/2026):
la v1 aveva un guadagno LORDO medio positivo (+0,23% a trade) e parametri
stabili (92% delle combinazioni in utile nello sviluppo), ma il costo di un
giro completo (~0,30%) assorbiva tutto. Su candele 4h ogni trade cattura in
media un movimento più ampio mentre il costo per trade resta lo stesso.
Ipotesi da verificare: il vantaggio lordo supera i costi.
"""
import pandas as pd

from office.indicators import ema

STRATEGY_ID = "STRATEGY_01_v2"
NAME = "Momentum EMA 4h"
FAMILY = "momentum"
DESCRIPTION = "Come la v1 (incrocio EMA con filtro EMA200, stop ATR) ma su candele 4h"
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
