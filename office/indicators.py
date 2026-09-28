"""Indicatori tecnici. Tutti causali: il valore alla barra t usa solo dati fino a t."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False, min_periods=length).mean()


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100.0).where(loss.notna())


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def bollinger_bandwidth(series: pd.Series, length: int = 20, mult: float = 2.0) -> pd.Series:
    mid = series.rolling(length).mean()
    std = series.rolling(length).std()
    return (2 * mult * std) / mid


def rolling_rank(series: pd.Series, window: int) -> pd.Series:
    """Percentile (0-1) del valore corrente rispetto alle ultime `window` barre."""
    return series.rolling(window).rank(pct=True)


def donchian_high(df: pd.DataFrame, length: int) -> pd.Series:
    return df["high"].rolling(length).max()


def donchian_low(df: pd.DataFrame, length: int) -> pd.Series:
    return df["low"].rolling(length).min()
