"""Libreria delle strategie: un file per versione (mai sovrascrivere).

Ogni modulo espone:
  STRATEGY_ID, NAME, KIND ("prematch" | "live" | "arb" | "exchange")
  propose(snapshot, params, ctx) -> list[proposal]
  manage(open_bets, snapshot, params, ctx) -> list[action]     (facoltativo)

proposal = {"strategy_id", "match_id" | "market_id", "label", "market", "selection", "bookmaker",
            "odds", "fair_prob", "edge", "live", "reason", "legs": [...] (solo arbitraggio)}
action   = {"bet_id", "action": "cashout" | "hedge", "price", "reason"}
ctx      = {"store", "sim_time", "params"}
"""
from __future__ import annotations

import importlib
import pkgutil


def discover() -> list:
    mods = []
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("s"):
            mods.append(importlib.import_module(f"{__name__}.{info.name}"))
    return sorted(mods, key=lambda m: m.STRATEGY_ID)


def by_id(strategy_id: str):
    for m in discover():
        if m.STRATEGY_ID == strategy_id:
            return m
    raise KeyError(strategy_id)
