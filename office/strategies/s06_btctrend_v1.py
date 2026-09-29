"""STRATEGY_06 — BTC con filtro di trend (v1).

Pre-registrata il 29/09/2026, PRIMA di qualsiasi test su questa strategia.
Idea classica e pubblica: stare investiti in BTC quando il prezzo chiude sopra
la sua media mobile di lungo periodo, stare in USDC quando chiude sotto.
Pochissimi scambi l'anno, quindi le commissioni di Bybit EU (0,25%) pesano poco.

- Solo BTC/USDC, candele giornaliere.
- Size fissa: il tetto per asset del Risk Manager (20% del capitale), non il
  rischio per trade: l'uscita è la regola del trend, lo stop è solo di emergenza.
- Si valida con i criteri "strategie lente" di config/quant_gates.yaml.
"""
import pandas as pd

STRATEGY_ID = "STRATEGY_06_v1"
NAME = "BTC sopra la media di lungo periodo"
FAMILY = "trend_filter"
DESCRIPTION = ("Solo BTC, candele giornaliere: investito (20% del capitale) quando BTC chiude sopra la "
               "media mobile, in USDC quando chiude sotto. Pochi scambi l'anno.")
TIMEFRAME = "1d"
UNIVERSE = ["BTC/USDC"]
SIZING = "allocation"
PARAM_GRID = {
    "sma": [150, 200],          # giorni della media mobile
    "band": [0.0, 0.02],        # fascia di tolleranza attorno alla media (riduce i falsi segnali)
    "stop_atr": [6.0],          # stop di emergenza: 6 × ATR giornaliero
}


def generate(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    close = df["close"]
    sma = close.rolling(p["sma"]).mean()
    entry = close > sma * (1 + p["band"])
    exit_ = close < sma * (1 - p["band"])
    return pd.DataFrame({"entry": entry.fillna(False), "exit": exit_.fillna(False)})
