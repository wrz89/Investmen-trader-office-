"""Simulazione accelerata dell'ufficio completo sul feed simulato.

Fa girare gli stessi agenti, lo stesso Risk Manager e lo stesso Banco del
live, ma manda avanti l'orologio del feed invece di aspettare. Utile per
vedere in pochi secondi settimane di "vita" dell'ufficio.
Il database è separato (runtime/simulazione.db) e viene ricreato.
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


async def replay(files, all_active: bool = True) -> dict:
    """Fa girare Bet_bot sui mercati registrati. Con all_active tutte le strategie puntano (in paper),
    comprese quelle in osservazione: serve proprio a decidere quali attivare."""
    from .feeds.recorder import ReplayFeed
    settings = load_settings()
    if all_active:
        settings["active_strategies"] = list(dict.fromkeys((settings.get("active_strategies") or [])
                                                           + (settings.get("observe_strategies") or [])))
        settings["observe_strategies"] = []
    settings["mode"] = "paper"
    feed = ReplayFeed(files)
    db = RUNTIME_DIR / "replay.db"
    for suffix in ("", "-wal", "-shm"):
        p = db.with_name(db.name + suffix)
        if p.exists():
            p.unlink()
    from . import clock
    if not feed.advance_one():
        return {"error": "nessuna registrazione trovata in runtime/recordings/"}
    clock.set_source(feed.now)
    office = SportOffice(db_path=db, feed=feed)
    office.settings.update({k: settings[k] for k in ("active_strategies", "observe_strategies", "mode")})
    while True:
        await office.run_cycle()
        if not feed.advance_one():
            break
    clock.set_source(None)
    m = office.store.get("metrics") or {}
    m.update(db=str(db), snapshots=feed.count, kill_switch=office.store.get("kill_switch"))
    return m
