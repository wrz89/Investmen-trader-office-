"""Motore di backtest barra per barra.

Convenzioni (identiche a quelle del ciclo live, per evitare divergenze):
  • i segnali sono calcolati a candela CHIUSA t;
  • l'ordine viene eseguito all'APERTURA della candela t+1;
  • lo stop è controllato sul minimo di ogni candela; se il prezzo apre
    già sotto lo stop (gap) l'uscita avviene all'apertura, non allo stop;
  • se ingresso e stop cadono nella stessa candela, si assume lo scenario
    peggiore (stop colpito);
  • ogni lato paga commissione + metà spread + slippage.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .indicators import atr as atr_indicator


@dataclass(frozen=True)
class CostModel:
    fee_rate: float = 0.001        # per lato
    slippage_bps: float = 3.0      # per lato
    spread_bps: float = 4.0        # spread intero (si paga metà per lato)

    @property
    def per_side(self) -> float:
        return self.fee_rate + (self.slippage_bps + self.spread_bps / 2) / 10_000

    @property
    def round_trip(self) -> float:
        return 2 * self.per_side

    def scaled(self, k: float) -> "CostModel":
        return CostModel(self.fee_rate * k, self.slippage_bps * k, self.spread_bps * k)

    @classmethod
    def from_settings(cls, settings: dict) -> "CostModel":
        c = settings["costs"]
        return cls(c["taker_fee"], c["slippage_bps"], c["default_spread_bps"])


def prepare(df: pd.DataFrame, strategy, params: dict) -> dict:
    """Pre-calcola segnali e ATR come array numpy (una volta per combinazione)."""
    sig = strategy.generate(df, params)
    return {
        "ts": df["ts"].to_numpy(),
        "open": df["open"].to_numpy(float),
        "low": df["low"].to_numpy(float),
        "close": df["close"].to_numpy(float),
        "atr": atr_indicator(df, 14).to_numpy(float),
        "entry": sig["entry"].to_numpy(bool),
        "exit": sig["exit"].to_numpy(bool),
        "stop_atr": float(params["stop_atr"]),
        "max_hold": params.get("max_hold"),
    }


def simulate(p: dict, start: int, end: int, costs: CostModel) -> list[dict]:
    """Simula i trade sulle barre [start, end). Restituisce la lista dei trade."""
    o, lo, c, a = p["open"], p["low"], p["close"], p["atr"]
    entry_sig, exit_sig = p["entry"], p["exit"]
    stop_mult, max_hold = p["stop_atr"], p["max_hold"]
    fee = costs.per_side
    trades: list[dict] = []

    in_pos = False
    entry_px = stop = 0.0
    entry_i = 0
    start = max(start, 1)

    def close_trade(i: int, px: float, reason: str) -> None:
        gross = px / entry_px - 1
        net = (px * (1 - fee)) / (entry_px * (1 + fee)) - 1
        trades.append({
            "entry_ts": int(p["ts"][entry_i]), "exit_ts": int(p["ts"][i]),
            "entry": entry_px, "exit": px, "gross": gross, "net": net,
            "bars": i - entry_i, "reason": reason,
        })

    for i in range(start, end):
        if in_pos:
            timeout = max_hold is not None and i - entry_i >= max_hold
            if exit_sig[i - 1] or timeout:
                close_trade(i, o[i], "timeout" if timeout and not exit_sig[i - 1] else "segnale")
                in_pos = False
            elif lo[i] <= stop:
                close_trade(i, min(o[i], stop), "stop")
                in_pos = False
            continue

        if entry_sig[i - 1] and not np.isnan(a[i - 1]) and i < end - 1:
            entry_px = o[i]
            stop = entry_px - stop_mult * a[i - 1]
            entry_i = i
            in_pos = True
            if lo[i] <= stop:                      # scenario peggiore nella stessa candela
                close_trade(i, stop, "stop")
                in_pos = False

    if in_pos:                                     # chiusura forzata a fine periodo
        close_trade(end - 1, c[end - 1], "fine_periodo")
    return trades


def with_costs(trades: list[dict], costs: CostModel) -> np.ndarray:
    """Ricalcola i rendimenti netti di una lista di trade con un altro modello di costo."""
    f = costs.per_side
    return np.array([(t["exit"] * (1 - f)) / (t["entry"] * (1 + f)) - 1 for t in trades])
