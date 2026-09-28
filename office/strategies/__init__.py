"""Libreria delle strategie.

Regola di versionamento: ogni versione è un file separato e NON va mai
modificato dopo la registrazione (s01_momentum_v1.py, s01_momentum_v2.py, ...).
Il registro salva l'impronta SHA-256 del file: se il codice di una versione
già registrata cambia, la strategia viene bloccata.

Ogni modulo espone:
    STRATEGY_ID, NAME, FAMILY, DESCRIPTION
    PARAM_GRID      dizionario parametro -> valori da esplorare
    generate(df, params) -> DataFrame con colonne booleane `entry` ed `exit`
                            calcolate a candela CHIUSA (si agisce all'apertura
                            della candela successiva)
    params obbligatori: stop_atr (stop = ingresso - stop_atr × ATR14)
    params facoltativi: max_hold (uscita forzata dopo N candele)
    TIMEFRAME facoltativo (es. "4h"); se assente vale il timeframe di settings.yaml
"""
from __future__ import annotations

import importlib
import itertools
import pkgutil
import re
from pathlib import Path

_PATTERN = re.compile(r"^s\d{2}_[a-z_]+_v\d+$")


def discover() -> list:
    modules = []
    for info in pkgutil.iter_modules([str(Path(__file__).parent)]):
        if _PATTERN.match(info.name):
            modules.append(importlib.import_module(f"{__name__}.{info.name}"))
    return sorted(modules, key=lambda m: m.STRATEGY_ID)


def by_id(strategy_id: str):
    for m in discover():
        if m.STRATEGY_ID == strategy_id:
            return m
    raise KeyError(strategy_id)


def param_combinations(grid: dict) -> list[dict]:
    keys = list(grid)
    return [dict(zip(keys, values)) for values in itertools.product(*(grid[k] for k in keys))]


def timeframe_of(module, default: str) -> str:
    return getattr(module, "TIMEFRAME", None) or default
