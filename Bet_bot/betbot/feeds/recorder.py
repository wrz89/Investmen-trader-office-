"""Registratore e riproduttore dei dati di mercato.

Con `feed.record: true` Bet_bot salva, a ogni ciclo, una fotografia compatta dei mercati (partite,
book dell'exchange con prezzi e denaro disponibile, quote di riferimento, punteggi, corse) in
runtime/recordings/AAAA-MM-GG.jsonl.gz. Qualche settimana di registrazione dei prezzi VERI di
betfair.it è il modo più onesto per sapere se una strategia funziona sul pool italiano, che ha una
liquidità sua: poi `python betbot.py replay` fa girare tutte le strategie sui giorni registrati, con
le stesse regole dell'exchange simulato (fill-or-kill, liquidità, commissione).
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

from ..config import RUNTIME_DIR
from .base import Feed

REC_DIR = RUNTIME_DIR / "recordings"
KEEP_MATCH = ("match_id", "sport", "league", "home", "away", "kickoff", "status", "minute", "home_score", "away_score",
              "result", "books", "live_books", "exchange", "closing", "odds_ts", "commission", "betfair", "stats")


def compact(snap: dict) -> dict:
    return {"ts": snap["ts"], "sim_time": snap.get("sim_time"), "time_scale": snap.get("time_scale", 1.0),
            "health": snap.get("health"),
            "matches": {k: {f: m.get(f) for f in KEEP_MATCH if m.get(f) is not None} for k, m in snap["matches"].items()},
            "races": snap.get("races") or {}}


class Recorder:
    def __init__(self, folder: Path = REC_DIR):
        self.folder = folder

    def write(self, snap: dict) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        day = datetime.fromtimestamp(snap.get("sim_time") or snap["ts"], timezone.utc).strftime("%Y-%m-%d")
        path = self.folder / f"{day}.jsonl.gz"
        with gzip.open(path, "at", encoding="utf-8") as fh:
            fh.write(json.dumps(compact(snap), separators=(",", ":"), default=str) + "\n")
        return path


class ReplayFeed(Feed):
    """Rilegge le fotografie registrate, una per ciclo, nell'ordine in cui sono state salvate."""
    name = "replay"

    def __init__(self, files: list[Path]):
        super().__init__({})
        self.files = sorted(files)
        self._iter = self._lines()
        self.current: dict | None = None
        self.speed = 1.0
        self.count = 0

    def _lines(self):
        for f in self.files:
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        yield json.loads(line)

    def advance_one(self) -> bool:
        try:
            self.current = next(self._iter)
            self.count += 1
            return True
        except StopIteration:
            return False

    def now(self) -> float:
        return (self.current or {}).get("sim_time") or (self.current or {}).get("ts") or 0.0

    async def fetch(self) -> dict:
        snap = dict(self.current)
        snap["health"] = {**(snap.get("health") or {}), "error_rate": 0.0,
                          "source": f"replay ({(snap.get('health') or {}).get('source', 'registrazione')})"}
        return snap


def recorded_files(since: str | None = None, until: str | None = None) -> list[Path]:
    files = sorted(REC_DIR.glob("*.jsonl.gz")) if REC_DIR.exists() else []
    return [f for f in files if (not since or f.name[:10] >= since) and (not until or f.name[:10] <= until)]
