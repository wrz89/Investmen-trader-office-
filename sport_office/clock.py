"""Orologio dell'ufficio. Di norma è l'ora reale; la simulazione accelerata
lo collega all'orologio del feed simulato, così limiti giornalieri, pause e
report seguono il tempo della simulazione."""
from __future__ import annotations

import time
from typing import Callable

_source: Callable[[], float] = time.time


def now() -> float:
    return _source()


def set_source(fn: Callable[[], float] | None) -> None:
    global _source
    _source = fn or time.time
