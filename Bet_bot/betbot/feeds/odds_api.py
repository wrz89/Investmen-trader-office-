"""Integrazione con The Odds API (https://the-odds-api.com).

Piano gratuito: 500 richieste/mese. Ogni sport interrogato costa 1 richiesta per
regione×mercato; i punteggi 2 richieste. Per non esaurirle, le quote si riscaricano
ogni `odds_refresh_seconds` e i punteggi ogni `scores_refresh_seconds` (settings →
feed.odds_api); tra un download e l'altro il ciclo rilegge l'ultima fotografia, che
però "invecchia": il Risk Manager accetta puntate solo su quote fresche, cioè subito
dopo un download. Con 4 sport: quote ogni 2 ore + punteggi ogni 6 ore ≈ 500 richieste/mese.

Le chiamate HTTP sono bloccanti (requests) e vengono eseguite in un thread
(asyncio.to_thread) così il ciclo async non si ferma.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import requests

from ..config import api_key
from .base import Feed, FeedError

BASE = "https://api.the-odds-api.com/v4"


class OddsApiFeed(Feed):
    name = "odds_api"

    def __init__(self, settings: dict):
        super().__init__(settings)
        self.key = api_key()
        if not self.key:
            raise FeedError("Manca la chiave di The Odds API: variabile ODDS_API_KEY o "
                            "runtime/sport/local_settings.json {\"odds_api_key\": \"...\"}.")
        self.calls = 0
        self.errors = 0
        self.remaining = None
        self.closing: dict[str, dict] = {}       # match_id → ultimo consenso pre-partita (per il CLV)
        self.last_books: dict[str, dict] = {}
        cfg = settings["feed"].get("odds_api") or {}
        self.odds_every = float(cfg.get("odds_refresh_seconds", 7200))
        self.scores_every = float(cfg.get("scores_refresh_seconds", 21600))
        self.odds_ts = 0.0
        self.scores_ts = 0.0
        self.matches: dict[str, dict] = {}
        self.budget_note = ""

    # ── budget delle richieste (piano gratuito: 500 al mese) ────────────────────────
    def _budget(self) -> dict:
        import json
        from ..config import RUNTIME_DIR
        path = RUNTIME_DIR / "odds_api_budget.json"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if data.get("day") != today:
            data = {"day": today, "used": 0, "remaining": data.get("remaining")}
        data["_path"] = str(path)
        return data

    def _save_budget(self, data: dict) -> None:
        import json
        from pathlib import Path
        p = Path(data.pop("_path"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data), encoding="utf-8")

    def budget_ok(self, cost: int) -> tuple[bool, str]:
        cfg = self.settings["feed"].get("odds_api") or {}
        b = self._budget()
        per_day = int(cfg.get("max_credits_per_day", 15))
        floor = int(cfg.get("min_remaining", 50))
        if b.get("remaining") is not None and int(b["remaining"]) - cost < floor:
            return False, f"crediti The Odds API quasi finiti ({b['remaining']} rimasti)"
        if b["used"] + cost > per_day:
            return False, f"budget del giorno raggiunto ({b['used']}/{per_day} crediti)"
        return True, ""

    def _get(self, path: str, **params) -> list | dict:
        self.calls += 1
        try:
            r = requests.get(f"{BASE}{path}", params={"apiKey": self.key, **params}, timeout=15)
            self.remaining = r.headers.get("x-requests-remaining")
            b = self._budget()
            b["used"] += int(float(r.headers.get("x-requests-last") or 0))
            if self.remaining is not None:
                b["remaining"] = int(float(self.remaining))
            self._save_budget(b)
            if r.status_code == 401:
                raise FeedError("The Odds API rifiuta la chiave (401).")
            if r.status_code == 429:
                raise FeedError("The Odds API: quota di richieste esaurita (429).")
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            self.errors += 1
            raise FeedError(f"The Odds API non raggiungibile: {exc}") from exc

    def _fetch_sync(self) -> dict:
        now = time.time()
        self.budget_note = ""
        n_sports = max(1, len(self._sport_keys(now)))
        if now - self.odds_ts >= self.odds_every or getattr(self, "force", False):
            self.force = False
            ok, why = self.budget_ok(n_sports)
            if ok:
                self._refresh_odds(now)
            else:
                self.budget_note = why
            self.odds_ts = now
        if now - self.scores_ts >= self.scores_every:
            ok, why = self.budget_ok(2 * n_sports)
            if ok:
                self._refresh_scores(now)
            else:
                self.budget_note = self.budget_note or why
            self.scores_ts = now
        for m in self.matches.values():               # il passare del tempo cambia lo stato anche senza download
            if m["status"] == "SCHEDULED" and datetime.fromisoformat(m["kickoff"]).timestamp() <= now:
                m["status"], m["live_books"] = "LIVE", {}
                if m["match_id"] not in self.closing and m.get("books"):
                    from ..odds import consensus
                    c = consensus(m["books"])           # ultima quota pre-partita vista = chiusura per il CLV
                    self.closing[m["match_id"]] = {k: round(v["fair_odds"], 3) for k, v in c.items() if not k.startswith("_")}
                m["closing"] = self.closing.get(m["match_id"])
        return {"ts": now, "sim_time": now, "time_scale": 1.0,
                "health": {"error_rate": self.errors / max(1, self.calls),
                           "source": f"The Odds API (richieste rimaste: {self.remaining})"
                                     + (f" · riferimento fermo: {self.budget_note}" if self.budget_note else "")},
                "matches": {k: dict(v) for k, v in self.matches.items()}, "races": {}}

    def _sport_keys(self, now: float) -> list[str]:
        """Chiavi fisse (feed.sports, solo quelle con quote reali) + quelle attive dei gruppi in feed.reference_groups.
        L'elenco degli sport di The Odds API non consuma richieste del piano; si aggiorna ogni 6 ore."""
        f = self.settings["feed"]
        keys = [k for k in f.get("sports", []) if not k.startswith(("tennis_atp", "tennis_wta")) or k.count("_") > 1]
        groups = set(f.get("reference_groups") or [])
        if groups:
            if now - getattr(self, "_groups_ts", 0) > 6 * 3600:
                try:
                    self._group_keys = [s["key"] for s in self._get("/sports") if s.get("group") in groups
                                        and s.get("active") and not s.get("has_outrights")]
                    self._groups_ts = now
                except FeedError:
                    self._group_keys = getattr(self, "_group_keys", [])
            keys += [k for k in getattr(self, "_group_keys", []) if k not in keys]
        return keys

    def _refresh_odds(self, now: float) -> None:
        f = self.settings["feed"]
        matches = self.matches
        for sport in self._sport_keys(now):
            events = self._get(f"/sports/{sport}/odds", regions=f.get("regions", "eu"),
                               markets=f.get("markets", "h2h"), oddsFormat="decimal")
            for ev in events:
                kickoff = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
                live = kickoff.timestamp() <= now
                books: dict[str, dict] = {}
                for bk in ev.get("bookmakers", []):
                    for mk in bk.get("markets", []):
                        if mk["key"] != "h2h":
                            continue
                        prices = {}
                        for o in mk["outcomes"]:
                            sel = "home" if o["name"] == ev["home_team"] else "away" if o["name"] == ev["away_team"] else "draw"
                            if sel == "draw" and o["name"].lower() not in ("draw", "tie", "pareggio"):
                                continue
                            prices[sel] = float(o["price"])
                        if len(prices) >= 2:
                            books[bk["title"]] = prices
                mid = ev["id"]
                if not live:
                    self.last_books[mid] = books                     # l'ultima quota pre-partita vista
                elif mid not in self.closing and mid in self.last_books:
                    from ..odds import consensus
                    c = consensus(self.last_books[mid])
                    self.closing[mid] = {k: round(v["fair_odds"], 3) for k, v in c.items() if not k.startswith("_")}
                prev = matches.get(mid, {})
                matches[mid] = {"match_id": mid, "sport": sport, "league": ev.get("sport_title", sport),
                                "home": ev["home_team"], "away": ev["away_team"], "kickoff": kickoff.isoformat(),
                                "status": "LIVE" if live else "SCHEDULED", "minute": prev.get("minute"),
                                "home_score": prev.get("home_score"), "away_score": prev.get("away_score"), "result": None,
                                "books": {} if live else books, "live_books": books if live else {},
                                "closing": self.closing.get(mid), "odds_ts": now}
        # dimentica le partite finite da più di 3 giorni
        cutoff = now - 3 * 86400
        self.matches = {k: m for k, m in matches.items()
                        if datetime.fromisoformat(m["kickoff"]).timestamp() > cutoff}

    def _refresh_scores(self, now: float) -> None:
        matches = self.matches
        for sport in self._sport_keys(now):
            # punteggi e risultati degli ultimi 3 giorni (2 richieste per sport)
            try:
                scores = self._get(f"/sports/{sport}/scores", daysFrom=3)
            except FeedError:
                scores = []
            for sc in scores:
                mid = sc["id"]
                m = matches.get(mid) or {"match_id": mid, "sport": sport, "league": sc.get("sport_title", sport),
                                         "home": sc["home_team"], "away": sc["away_team"],
                                         "kickoff": sc["commence_time"], "status": "SCHEDULED", "books": {},
                                         "live_books": {}, "closing": self.closing.get(mid), "odds_ts": now,
                                         "minute": None, "home_score": None, "away_score": None, "result": None}
                if sc.get("scores"):
                    for s in sc["scores"]:
                        val = int(float(s["score"])) if s.get("score") not in (None, "") else None
                        if s["name"] == sc["home_team"]:
                            m["home_score"] = val
                        elif s["name"] == sc["away_team"]:
                            m["away_score"] = val
                if sc.get("completed"):
                    m["status"] = "FINISHED"
                    h, a = m.get("home_score"), m.get("away_score")
                    if h is not None and a is not None:
                        m["result"] = "home" if h > a else "away" if a > h else "draw"
                matches[mid] = m

    async def fetch(self) -> dict:
        return await asyncio.to_thread(self._fetch_sync)
