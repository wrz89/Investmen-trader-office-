"""STRATEGY_02 — Mean reversion in trend (v1).

Idea: dentro un trend rialzista, gli eccessi di vendita di breve periodo
(RSI molto basso) tendono a rientrare. Si compra la debolezza solo se il
prezzo è sopra l'EMA200, si esce al recupero dell'RSI, a tempo o allo stop.
Nessuna media al ribasso: una sola entrata per segnale.
"""
import pandas as pd

from office.indicators import ema, rsi

STRATEGY_ID = "STRATEGY_02_v1"
NAME = "Mean Reversion RSI"
FAMILY = "mean_reversion"
DESCRIPTION = "RSI14 ipervenduto sopra EMA200, uscita su recupero RSI o dopo N candele"
PARAM_GRID = {
    "rsi_entry": [25, 30],
    "rsi_exit": [50, 60],
    "max_hold": [24, 48],
    "stop_atr": [2.5],
}


def generate(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    close = df["close"]
    r = rsi(close, 14)
    entry = (r < p["rsi_entry"]) & (r.shift(1) >= p["rsi_entry"]) & (close > ema(close, 200))
    exit_ = r > p["rsi_exit"]
    return pd.DataFrame({"entry": entry.fillna(False), "exit": exit_.fillna(False)})
