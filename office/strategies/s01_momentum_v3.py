"""STRATEGY_01 — Momentum / trend following (v3).

Codice IDENTICO alla v2 (stessa logica, timeframe 4h, stessi parametri candidati).
Cambia solo l'insieme di asset su cui viene validata, deciso PRIMA di vedere risultati:
BTC, ETH (già visti con la v2) + XRP, SOL (mai testati), coppie in USDC.

Motivazione: la v2 su BTC/EUR ed ETH/EUR (8 anni) ha PF 2,69 ma solo 74 trade
fuori campione (servono 100) e drawdown Monte Carlo 11,8% (massimo 10%).
Test richiesto: la stessa regola deve funzionare anche su asset nuovi,
e su OGNI asset preso singolarmente (criterio aggiunto prima della validazione).
"""
import pandas as pd

from office.indicators import ema

STRATEGY_ID = "STRATEGY_01_v3"
NAME = "Momentum EMA 4h multi-asset"
FAMILY = "momentum"
DESCRIPTION = "Identica alla v2 (momentum 4h), validata su BTC, ETH, XRP e SOL"
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
