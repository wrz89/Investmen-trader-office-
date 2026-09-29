"""API-Football (API-Sports v3): statistiche live delle partite in corso.

Piano gratuito: 100 richieste al giorno. Per questo:
  • /fixtures?live=all una volta per ciclo al massimo ogni `live_refresh_seconds`;
  • /fixtures/statistics solo per le partite dal minuto `stats_from_minute` in poi,
    massimo `max_stats_per_refresh` partite, ciascuna al massimo ogni 5 minuti.
Le squadre vengono abbinate a quelle del feed quote per nome normalizzato.
"""
from __future__ import annotations

import difflib
import re
import time
import unicodedata

import requests

from .base import FeedError

BASE = "https://v3.football.api-sports.io"
STOP = {"fc", "ac", "as", "ss", "ssc", "cf", "afc", "calcio", "club", "de", "sc", "us", "bc", "cd", "rc", "ud", "sd"}


ALIAS = {"man": "manchester", "utd": "united", "internazionale": "inter", "wolves": "wolverhampton",
         "spurs": "tottenham", "atl": "atletico", "nott": "nottingham", "nottm": "nottingham"}


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return " ".join(ALIAS.get(w, w) for w in s.split() if w not in STOP)


def same_team(a: str, b: str) -> bool:
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    ta, tb = set(na.split()), set(nb.split())
    if ta <= tb or tb <= ta:                     # "Newcastle" ⊂ "Newcastle United"
        return True
    if ta - tb and tb - ta and ta & tb:          # "Manchester United" vs "Manchester City": parole diverse → squadre diverse
        return difflib.SequenceMatcher(None, na, nb).ratio() >= 0.93
    return difflib.SequenceMatcher(None, na, nb).ratio() >= 0.8


def _num(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, str):
        v = v.replace("%", "").strip() or 0
    try:
        return float(v)
    except ValueError:
        return 0.0


class ApiFootball:
    def __init__(self, key: str, cfg: dict | None = None):
        if not key:
            raise FeedError("Manca la chiave di API-Football (Impostazioni → Chiavi API).")
        self.key = key
        self.cfg = {"live_refresh_seconds": 120, "stats_from_minute": 55, "max_stats_per_refresh": 4, **(cfg or {})}
        self.live: list[dict] = []
        self.live_ts = 0.0
        self.stats: dict[int, tuple[float, dict]] = {}
        self.calls = 0
        self.errors = 0

    def _get(self, path: str, **params) -> list:
        self.calls += 1
        try:
            r = requests.get(f"{BASE}{path}", params=params, headers={"x-apisports-key": self.key}, timeout=15)
            body = r.json()
        except (requests.RequestException, ValueError) as exc:
            self.errors += 1
            raise FeedError(f"API-Football non raggiungibile: {exc}") from exc
        if body.get("errors"):
            self.errors += 1
            raise FeedError(f"API-Football: {body['errors']}")
        return body.get("response", [])

    def refresh(self) -> None:
        if time.time() - self.live_ts >= self.cfg["live_refresh_seconds"]:
            self.live, self.live_ts = self._get("/fixtures", live="all"), time.time()
        todo = [f for f in self.live if (f["fixture"]["status"].get("elapsed") or 0) >= self.cfg["stats_from_minute"]
                and time.time() - self.stats.get(f["fixture"]["id"], (0, {}))[0] > 300]
        for f in todo[: self.cfg["max_stats_per_refresh"]]:
            fid = f["fixture"]["id"]
            data = {}
            for team in self._get("/fixtures/statistics", fixture=fid):
                data[team["team"]["name"]] = {s["type"]: _num(s["value"]) for s in team["statistics"]}
            self.stats[fid] = (time.time(), data)

    def enrich(self, matches: dict) -> int:
        """Aggiunge minuto, punteggio e statistiche alle partite LIVE del feed quote."""
        n = 0
        for m in matches.values():
            if m["status"] != "LIVE" or not m["sport"].startswith("soccer"):
                continue
            for f in self.live:
                h, a = f["teams"]["home"]["name"], f["teams"]["away"]["name"]
                if not (same_team(m["home"], h) and same_team(m["away"], a)):
                    continue
                m["minute"] = f["fixture"]["status"].get("elapsed")
                m["home_score"], m["away_score"] = f["goals"]["home"], f["goals"]["away"]
                st = self.stats.get(f["fixture"]["id"], (0, {}))[1]
                if st:
                    m["stats"] = {"home": _pick(st.get(h, {})), "away": _pick(st.get(a, {}))}
                n += 1
                break
        return n


def _pick(s: dict) -> dict:
    return {"shots_on_target": s.get("Shots on Goal", 0.0), "shots": s.get("Total Shots", 0.0),
            "possession": s.get("Ball Possession", 0.0), "red_cards": s.get("Red Cards", 0.0),
            "xg": s.get("expected_goals", 0.0)}
