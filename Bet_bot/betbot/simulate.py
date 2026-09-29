"""Simulazione accelerata dell'ufficio completo sul feed simulato.

Fa girare gli stessi agenti, lo stesso Risk Manager e lo stesso Banco del
live, ma manda avanti l'orologio del feed invece di aspettare. Utile per
vedere in pochi secondi settimane di "vita" dell'ufficio.
Il database è separato (runtime/sport/simulazione.db) e viene ricreato.
"""
from __future__ import annotations

import asyncio

from .config import RUNTIME_DIR
from .core import SportOffice
from .feeds.mock import MockFeed
from .config import load_settings


async def run(hours: float = 72, step_minutes: float = 2, seed: int | None = 7) -> dict:
    settings = load_settings()
    settings["feed"]["mock"]["seed"] = seed
    feed = MockFeed(settings)
    feed.speed = 1.0                       # il tempo avanza solo con advance()
    db = RUNTIME_DIR / "simulazione.db"
    for suffix in ("", "-wal", "-shm"):
        p = db.with_name(db.name + suffix)
        if p.exists():
            p.unlink()
    from . import clock
    clock.set_source(feed.now)
    office = SportOffice(db_path=db, feed=feed)
    steps = int(hours * 60 / step_minutes)
    for i in range(steps):
        await office.run_cycle()
        feed.advance(step_minutes * 60)
    clock.set_source(None)
    m = office.store.get("metrics") or {}
    m["db"] = str(db)
    m["kill_switch"] = office.store.get("kill_switch")
    return m


def main(hours: float, seed: int | None) -> dict:
    return asyncio.run(run(hours, seed=seed))
