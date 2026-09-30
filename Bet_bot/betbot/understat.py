"""Dati Understat (xG di squadra e formazioni con i numeri di ogni giocatore) per la palestra di Leo.

Campionati: Premier League, Liga, Bundesliga, Serie A, Ligue 1. Tutto finisce in una cache sul PC
(runtime/understat/) e si scarica una volta sola, con richieste lente (una ogni `pause` secondi).
"""
from __future__ import annotations

import gzip
import json
import time
from pathlib import Path

import requests

from .config import RUNTIME_DIR

CACHE = RUNTIME_DIR / "understat"
LEAGUES = {"E0": "EPL", "SP1": "La_liga", "D1": "Bundesliga", "I1": "Serie_A", "F1": "Ligue_1"}
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "X-Requested-With": "XMLHttpRequest",
           "Accept-Encoding": "gzip, deflate"}


def _get(url: str, referer: str) -> dict:
    r = requests.get(url, headers={**HEADERS, "Referer": referer}, timeout=30)
    r.raise_for_status()
    raw = r.content
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def league_season(div: str, year: int, refresh: bool = False) -> dict | None:
    """Partite di una stagione (year = anno di inizio): id, squadre, gol, xG, data."""
    name = LEAGUES.get(div)
    if not name:
        return None
    f = CACHE / f"{div}_{year}.json.gz"
    if f.exists() and not refresh:
        return json.loads(gzip.decompress(f.read_bytes()))
    d = _get(f"https://understat.com/getLeagueData/{name}/{year}", f"https://understat.com/league/{name}/{year}")
    out = {"dates": d.get("dates") or []}
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_bytes(gzip.compress(json.dumps(out).encode()))
    return out


def _fetch_rosters(match_id: str) -> dict:
    d = _get(f"https://understat.com/getMatchData/{match_id}", f"https://understat.com/match/{match_id}")
    keep = ("player_id", "player", "position", "positionOrder", "time", "goals", "xG", "xA", "xGChain", "xGBuildup",
            "shots", "key_passes", "yellow_card", "red_card", "roster_in", "roster_out")
    return {side: [{k: p.get(k) for k in keep} for p in (d.get("rosters") or {}).get(side, {}).values()] for side in ("h", "a")}


def _shard(mid: str) -> Path:
    return CACHE / "partite" / f"{int(mid) // 1000}.json.gz"


def _save_shards(buf: dict) -> None:
    by: dict[Path, dict] = {}
    for mid, v in buf.items():
        by.setdefault(_shard(mid), {})[mid] = v
    for f, new in by.items():
        data = json.loads(gzip.decompress(f.read_bytes())) if f.exists() else {}
        data.update(new)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(gzip.compress(json.dumps(data).encode()))


def match_rosters(match_id: str) -> dict | None:
    """Formazioni della partita: per ogni giocatore minuti, xG, xA, xGChain, tiri, passaggi chiave, cartellini, titolare."""
    f = _shard(match_id)
    data = json.loads(gzip.decompress(f.read_bytes())) if f.exists() else {}
    if match_id in data:
        return data[match_id]
    out = _fetch_rosters(match_id)
    _save_shards({match_id: out})
    return out


def download_all(years: list[int], rosters: bool = True, pause: float = 0.6, workers: int = 3, log=print) -> dict:
    """Scarica (o ritrova in cache) stagioni e formazioni. Riprende da dove si era fermato."""
    n_new = n_err = 0
    seasons = {}
    for div in LEAGUES:
        for y in years:
            try:
                seasons[(div, y)] = league_season(div, y)
            except Exception as exc:
                log(f"Understat {div} {y}: {exc}")
                n_err += 1
    if rosters:
        todo = [m["id"] for s in seasons.values() if s for m in s["dates"] if m.get("isResult")]
        have = set()
        for shard in (CACHE / "partite").glob("*.json.gz") if (CACHE / "partite").exists() else []:
            have |= set(json.loads(gzip.decompress(shard.read_bytes())))
        todo = [m for m in todo if m not in have]
        log(f"Formazioni da scaricare: {len(todo)} partite (circa {len(todo) * 1.3 / workers / 60:.0f} minuti, una volta sola).")
        from concurrent.futures import ThreadPoolExecutor

        def one(mid):
            time.sleep(pause)
            try:
                return mid, _fetch_rosters(mid)
            except Exception as exc:
                return mid, exc

        buf = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:        # poche richieste in parallelo, sempre lente
            for i, (mid, r) in enumerate(pool.map(one, todo), 1):
                if isinstance(r, Exception):
                    n_err += 1
                    if n_err > 100:
                        log(f"Troppi errori da Understat ({r}): mi fermo, riprendo la prossima volta.")
                        break
                    continue
                buf[mid] = r
                n_new += 1
                if i % 250 == 0:
                    _save_shards(buf)
                    buf = {}
                    log(f"  … {i}/{len(todo)}")
        _save_shards(buf)
    return {"seasons": len([s for s in seasons.values() if s]), "new_matches": n_new, "errors": n_err}


def load_rosters() -> dict:
    out = {}
    for shard in (CACHE / "partite").glob("*.json.gz") if (CACHE / "partite").exists() else []:
        out.update(json.loads(gzip.decompress(shard.read_bytes())))
    return out
