"""Feed simulato: un "mondo" di partite e corse con probabilità vere nascoste.

Serve a far girare l'intero ufficio senza chiave API e senza soldi:
  • bookmaker virtuali con margine e rumore diversi (uno "sharp", altri "soft");
  • partite che passano davvero da SCHEDULED → LIVE → FINISHED con gol e minuti;
  • quote live che seguono punteggio e tempo residuo;
  • corse di cavalli su un exchange simulato con book back/lay in tick, weight of money,
    prezzi che si muovono e la corsa che "parte" (time-to-jump).
Il tempo della simulazione è accelerato (settings.feed.mock.speed).
"""
from __future__ import annotations

import math
import random
import time
from datetime import datetime, timezone

from .base import Feed

LEAGUES = {
    "soccer_italy_serie_a": ("Serie A", ["Inter", "Milan", "Juventus", "Napoli", "Roma", "Atalanta", "Lazio",
                                         "Fiorentina", "Bologna", "Torino", "Udinese", "Genoa"]),
    "soccer_epl": ("Premier League", ["Man City", "Arsenal", "Liverpool", "Chelsea", "Tottenham", "Newcastle",
                                      "Aston Villa", "Brighton", "Everton", "Fulham", "Brentford", "Burnley"]),
    "soccer_spain_la_liga": ("La Liga", ["Real Madrid", "Barcelona", "Atletico", "Sevilla", "Betis", "Villarreal",
                                         "Valencia", "Sociedad", "Girona", "Getafe", "Osasuna", "Alaves"]),
    "basketball_nba": ("NBA", ["Celtics", "Nuggets", "Bucks", "Suns", "Lakers", "Warriors", "Heat", "Knicks",
                               "Thunder", "Mavericks", "Pistons", "Hornets"]),
    "tennis_atp": ("ATP", ["Sinner", "Alcaraz", "Djokovic", "Zverev", "Medvedev", "Fritz", "Musetti", "Rune",
                           "Draper", "Cobolli", "Darderi", "Arnaldi", "Shelton", "Tsitsipas"]),
    "tennis_wta": ("WTA", ["Sabalenka", "Swiatek", "Gauff", "Paolini", "Rybakina", "Pegula", "Andreeva", "Zheng",
                           "Bronzetti", "Cocciaretto", "Keys", "Navarro"]),
}


def is_tennis(sport: str) -> bool:
    return sport.startswith("tennis")


def two_way(sport: str) -> bool:
    return sport.startswith(("tennis", "basketball"))


def bo3(q: float) -> float:
    """Probabilità di vincere al meglio dei 3 set, dato q = probabilità di vincere un set."""
    return q * q * (3 - 2 * q)


def set_prob(p_match: float) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if bo3(mid) < p_match else (lo, mid)
    return (lo + hi) / 2
BOOKMAKERS = ["Pinnacle", "Bet365", "Unibet", "Betway", "William Hill", "Sisal", "Snai", "Eurobet"]
VENUES = ["Ascot", "Newmarket", "Cheltenham", "York", "Goodwood", "Kempton"]

# scala prezzi Betfair (tick)
TICKS = [(1.01, 2, 0.01), (2, 3, 0.02), (3, 4, 0.05), (4, 6, 0.1), (6, 10, 0.2), (10, 20, 0.5),
         (20, 30, 1), (30, 50, 2), (50, 100, 5), (100, 1000, 10)]


def tick_round(price: float) -> float:
    price = min(max(price, 1.01), 1000)
    for lo, hi, step in TICKS:
        if price < hi:
            return round(round((price - lo) / step) * step + lo, 2)
    return 1000.0


def tick_up(price: float, n: int = 1) -> float:
    for _ in range(n):
        for lo, hi, step in TICKS:
            if price < hi - 1e-9:
                price = tick_round(price + step)
                break
    return price


def tick_down(price: float, n: int = 1) -> float:
    for _ in range(n):
        for lo, hi, step in reversed(TICKS):
            if price > lo + 1e-9:
                price = tick_round(price - step)
                break
    return price


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


class MockFeed(Feed):
    name = "mock"

    def __init__(self, settings: dict):
        super().__init__(settings)
        m = settings["feed"].get("mock", {})
        self.rng = random.Random(m.get("seed"))
        self.speed = float(m.get("speed", 30))
        self.match_minutes = int(m.get("match_minutes", 90))
        self.n_books = int(m.get("bookmakers", 6))
        self.margin = float(m.get("margin", 0.05))
        self.per_cycle = int(m.get("matches_per_cycle", 6))
        self.exchange_update = float(m.get("exchange_update", 0.35))       # prob. che l'exchange si aggiorni a ogni tick
        self.exchange_liquidity = float(m.get("exchange_liquidity", 400))  # € tipici sul miglior prezzo (pool italiano: pochi)
        self.real_start = time.time()
        self.sim_start = time.time()
        self.books = BOOKMAKERS[: self.n_books]
        # profilo di ogni bookmaker: margine e rumore (Pinnacle = sharp)
        self.profiles = {b: {"margin": self.margin * (0.5 if b == "Pinnacle" else self.rng.uniform(0.9, 1.4)),
                             "noise": 0.01 if b == "Pinnacle" else self.rng.uniform(0.015, 0.035)} for b in self.books}
        self.matches: dict[str, dict] = {}
        self.races: dict[str, dict] = {}
        self._counter = 0
        self.errors = 0
        self.calls = 0

    # ── tempo simulato ──────────────────────────────────────────
    def now(self) -> float:
        return self.sim_start + (time.time() - self.real_start) * self.speed

    def advance(self, seconds: float) -> None:
        """Usato dalla simulazione offline: manda avanti l'orologio senza aspettare."""
        self.sim_start += seconds

    # ── partite ─────────────────────────────────────────────────
    @staticmethod
    def _probs_from_strength(sport: str, strength: float) -> dict[str, float]:
        if two_way(sport):
            ph = 1 / (1 + math.exp(-strength))
            return {"home": ph, "away": 1 - ph}
        ph = 1 / (1 + math.exp(-(strength + 0.25)))         # con fattore campo
        draw = max(0.08, 0.27 - 0.07 * abs(strength))
        ph = ph * (1 - draw)
        return {"home": ph, "draw": draw, "away": 1 - ph - draw}

    def _true_probs(self, sport: str) -> tuple[dict[str, float], float]:
        # differenza di forza home−away (a volte squilibri netti)
        sd = 1.6 if is_tennis(sport) else 0.9 if two_way(sport) else 1.3     # nel tennis i favoriti netti sono frequenti
        strength = self.rng.gauss(0, sd)
        return self._probs_from_strength(sport, strength), strength

    def _new_match(self) -> dict:
        sport = self.rng.choice(self.settings["feed"]["sports"])
        league, teams = LEAGUES.get(sport, ("Altro", ["A", "B", "C", "D"]))
        home, away = self.rng.sample(teams, 2)
        self._counter += 1
        kickoff = self.now() + self.rng.uniform(0.25, 6.0) * 3600
        truth, strength = self._true_probs(sport)
        return {"match_id": f"M{self._counter:05d}", "sport": sport, "league": league, "home": home, "away": away,
                "strength": strength,
                "kickoff_epoch": kickoff, "kickoff": _iso(kickoff), "status": "SCHEDULED", "minute": 0,
                "home_score": 0, "away_score": 0, "result": None, "truth": truth, "closing": None,
                "books": {}, "live_books": {}, "exchange": {}, "odds_ts": None,
                "goal_rate": 0.0 if two_way(sport) else 2.7 / self.match_minutes,
                "seen_minute": 0}

    def _quote(self, probs: dict[str, float], book: str, live: bool = False) -> dict[str, float]:
        """Quote di un bookmaker: stima rumorosa della verità + margine col "metodo potenza"
        (implicita_i = p_i^k, con k < 1 tale che la somma sia 1 + margine). Come nei mercati veri,
        il margine pesa di più sulle quote alte (favourite-longshot bias) e meno sui favoriti."""
        prof = self.profiles[book]
        noisy = {k: max(0.005, p * math.exp(self.rng.gauss(0, prof["noise"] * (1.5 if live else 1)))) for k, p in probs.items()}
        total = sum(noisy.values())
        noisy = {k: v / total for k, v in noisy.items()}
        target = 1 + prof["margin"] * (1.3 if live else 1.0)
        lo, hi = 0.5, 1.0
        for _ in range(40):                                   # bisezione su k
            k = (lo + hi) / 2
            if sum(v ** k for v in noisy.values()) > target:
                lo = k
            else:
                hi = k
        return {s: round(max(1.01, 1 / (v ** k)), 2) for s, v in noisy.items()}

    def _live_probs(self, m: dict) -> dict[str, float]:
        """Probabilità in-play semplificate: gol residui attesi ~ Poisson, forza dalle prob. iniziali."""
        t = m["truth"]
        remaining = max(0.0, (self.match_minutes - m["minute"]) / self.match_minutes)
        lead = m["home_score"] - m["away_score"]
        if is_tennis(m["sport"]):                            # tennis: set vinti e probabilità di vincere un set
            q, h, a = set_prob(t["home"]), m["home_score"], m["away_score"]
            ph = {(0, 0): bo3(q), (1, 0): 1 - (1 - q) ** 2, (0, 1): q * q, (1, 1): q}.get((h, a), 1.0 if h > a else 0.0)
            ph = min(0.995, max(0.005, ph))
            return {"home": ph, "away": 1 - ph}
        if "draw" not in t:                                  # basket: logit su vantaggio e tempo
            edge = (t["home"] - 0.5) * 4 * remaining + lead / (8 * max(remaining, 0.05) ** 0.5)
            ph = 1 / (1 + math.exp(-edge))
            return {"home": ph, "away": 1 - ph}
        exp_h = 2.7 * remaining * (t["home"] + 0.5 * t["draw"]) / 0.5 * 0.5
        exp_a = 2.7 * remaining * (t["away"] + 0.5 * t["draw"]) / 0.5 * 0.5
        ph = pd_ = pa = 0.0
        for gh in range(7):
            for ga in range(7):
                p = (math.exp(-exp_h) * exp_h ** gh / math.factorial(gh)) * (math.exp(-exp_a) * exp_a ** ga / math.factorial(ga))
                final = lead + gh - ga
                if final > 0:
                    ph += p
                elif final == 0:
                    pd_ += p
                else:
                    pa += p
        s = ph + pd_ + pa
        return {"home": ph / s, "draw": pd_ / s, "away": pa / s}

    def _tick_match(self, m: dict, now: float) -> None:
        if m["status"] == "FINISHED":
            return
        if m["status"] == "SCHEDULED":
            if now >= m["kickoff_epoch"]:
                m["status"] = "LIVE"
                from ..odds import remove_margin
                fair = remove_margin(self._quote(m["truth"], "Pinnacle"))
                m["closing"] = {k: round(1 / v, 3) for k, v in fair.items()}
            else:
                # notizie (formazioni, infortuni): ogni tanto la forza vera cambia
                if self.rng.random() < 0.015:
                    m["strength"] += self.rng.gauss(0, 0.35)
                    m["truth"] = self._probs_from_strength(m["sport"], m["strength"])
                # il bookmaker sharp aggiorna subito, quelli soft in ritardo: le quote "vecchie"
                # rimaste in giro dopo una notizia sono la fonte vera del valore
                for b in self.books:
                    if not m["books"].get(b) or b == "Pinnacle" or self.rng.random() < 0.25:
                        m["books"][b] = self._quote(m["truth"], b)
                # l'exchange (Betfair) segue la verità con un po' di ritardo, come i book soft
                if not m.get("exchange") or self.rng.random() < self.exchange_update:
                    m["exchange"] = self._exchange_book(m["truth"])
                m["odds_ts"] = now
                return
        minute = min(self.match_minutes, int((now - m["kickoff_epoch"]) / 60))
        for _ in range(minute - m["seen_minute"]):        # gol minuto per minuto
            if m["goal_rate"] and self.rng.random() < m["goal_rate"]:
                t = m["truth"]
                if self.rng.random() < (t["home"] + 0.5 * t.get("draw", 0)):
                    m["home_score"] += 1
                else:
                    m["away_score"] += 1
        if is_tennis(m["sport"]):                         # tennis: un set ogni 40 minuti, al meglio dei 3
            q = set_prob(m["truth"]["home"])
            for mm in range(m["seen_minute"] + 1, minute + 1):
                if mm % 40 == 0 and max(m["home_score"], m["away_score"]) < 2:
                    if self.rng.random() < q:
                        m["home_score"] += 1
                    else:
                        m["away_score"] += 1
        elif not m["goal_rate"]:                           # basket: punteggio dal ritmo
            t = m["truth"]
            pts = int((minute - m["seen_minute"]) * 4.8)
            for _ in range(pts):
                if self.rng.random() < 0.5 + (t["home"] - 0.5) * 0.4:
                    m["home_score"] += 1
                else:
                    m["away_score"] += 1
        st = m.setdefault("stats", {"home": {"shots_on_target": 0, "red_cards": 0}, "away": {"shots_on_target": 0, "red_cards": 0}})
        for _ in range(0 if two_way(m["sport"]) else minute - m["seen_minute"]):
            for side in ("home", "away"):
                share = m["truth"][side] + 0.5 * m["truth"].get("draw", 0)
                if self.rng.random() < 9.0 / self.match_minutes * share:
                    st[side]["shots_on_target"] += 1
                if self.rng.random() < 0.12 / self.match_minutes:
                    st[side]["red_cards"] += 1
        m["seen_minute"] = m["minute"] = minute
        tennis_done = is_tennis(m["sport"]) and max(m["home_score"], m["away_score"]) >= 2
        if tennis_done or (minute >= self.match_minutes and not is_tennis(m["sport"])) or minute >= 150:
            m["status"] = "FINISHED"
            m["live_books"] = {}
            m["exchange"] = {}
            if m["home_score"] > m["away_score"]:
                m["result"] = "home"
            elif m["home_score"] < m["away_score"]:
                m["result"] = "away"
            else:
                m["result"] = "draw" if "draw" in m["truth"] else self.rng.choice(["home", "away"])
            return
        probs = self._live_probs(m)
        m["live_books"] = {b: self._quote(probs, b, live=True) for b in self.books[:4]}
        m["exchange"] = self._exchange_book(probs, live=True)
        m["odds_ts"] = now

    def _exchange_book(self, probs: dict[str, float], live: bool = False) -> dict[str, dict]:
        """Book di un exchange: per ogni esito miglior quota da puntare (back), da bancare (lay, 1-2 tick sopra)
        e denaro disponibile. Niente margine del bookmaker: il costo è la commissione sulla vincita."""
        out = {}
        for sel, p in probs.items():
            noisy = max(0.01, min(0.995, p * math.exp(self.rng.gauss(0, 0.012 if not live else 0.02))))
            fair = 1 / noisy
            back = tick_round(fair * self.rng.uniform(0.985, 1.0))
            lay = tick_up(back, 1 if self.rng.random() < 0.7 else 2)
            scale = self.exchange_liquidity * (0.3 if live else 1.0)
            out[sel] = {"back": back, "lay": lay, "back_size": round(scale * self.rng.uniform(0.2, 1.5), 0),
                        "lay_size": round(scale * self.rng.uniform(0.2, 1.5), 0)}
        return out

    # ── corse (exchange) ───────────────────────────────────────
    def _new_race(self) -> dict:
        self._counter += 1
        n = self.rng.randint(6, 10)
        weights = [self.rng.lognormvariate(0, 0.9) for _ in range(n)]
        s = sum(weights)
        start = self.now() + self.rng.uniform(4, 25) * 60
        runners = {}
        for i, w in enumerate(weights):
            p = w / s
            fair = 1 / p
            back = tick_round(fair * self.rng.uniform(0.97, 1.03))
            runners[f"R{i + 1}"] = {"name": f"Cavallo {i + 1}", "truth": p, "back": back, "lay": tick_up(back),
                                    "back_size": round(self.rng.uniform(50, 800), 0), "lay_size": round(self.rng.uniform(50, 800), 0),
                                    "wom": 0.5, "ltp": back, "momentum": self.rng.gauss(0, 0.004)}
        return {"market_id": f"H{self._counter:05d}", "venue": self.rng.choice(VENUES), "race": f"{n} partenti",
                "start_epoch": start, "start": _iso(start), "status": "OPEN", "runners": runners, "winner": None}

    def _tick_race(self, r: dict, now: float) -> None:
        if r["status"] == "CLOSED":
            return
        if now >= r["start_epoch"]:
            if r["status"] == "OPEN":
                r["status"] = "INPLAY"
                ids = list(r["runners"])
                r["winner"] = self.rng.choices(ids, weights=[r["runners"][i]["truth"] for i in ids])[0]
                r["end_epoch"] = now + 150
            elif now >= r.get("end_epoch", now):
                r["status"] = "CLOSED"
            return
        for run in r["runners"].values():
            # il peso del denaro spinge il prezzo: molto denaro in back (wom alto) → prezzo scende
            run["back_size"] = max(10, run["back_size"] * math.exp(self.rng.gauss(0, 0.25)))
            run["lay_size"] = max(10, run["lay_size"] * math.exp(self.rng.gauss(0, 0.25)))
            run["wom"] = run["back_size"] / (run["back_size"] + run["lay_size"])
            run["momentum"] = 0.7 * run["momentum"] + self.rng.gauss(0, 0.004) - (run["wom"] - 0.5) * 0.01
            drift = math.exp(run["momentum"] + self.rng.gauss(0, 0.01))
            # sotto i 2 minuti dal via il mercato è più nervoso
            if r["start_epoch"] - now < 120:
                drift *= math.exp(self.rng.gauss(0, 0.02))
            back = tick_round(run["back"] * drift)
            run["back"], run["lay"], run["ltp"] = back, tick_up(back), back

    # ── snapshot ────────────────────────────────────────────────
    async def fetch(self) -> dict:
        return self.snapshot()

    def snapshot(self) -> dict:
        self.calls += 1
        now = self.now()
        scheduled = [m for m in self.matches.values() if m["status"] == "SCHEDULED"]
        while len(scheduled) < self.per_cycle:
            m = self._new_match()
            self.matches[m["match_id"]] = m
            scheduled.append(m)
        if sum(1 for r in self.races.values() if r["status"] == "OPEN") < 2:
            r = self._new_race()
            self.races[r["market_id"]] = r
        for m in self.matches.values():
            self._tick_match(m, now)
        for r in self.races.values():
            self._tick_race(r, now)
        # pulizia: dimentica partite/corse finite da più di 6 ore simulate
        self.matches = {k: m for k, m in self.matches.items()
                        if not (m["status"] == "FINISHED" and now - m["kickoff_epoch"] > 6 * 3600 + self.match_minutes * 60)}
        self.races = {k: r for k, r in self.races.items() if not (r["status"] == "CLOSED" and now - r["start_epoch"] > 3600)}
        return {
            "ts": time.time(), "sim_time": now,
            "health": {"error_rate": self.errors / max(1, self.calls), "source": "mock (bookmaker simulato)"},
            "matches": {k: {kk: vv for kk, vv in m.items() if kk not in ("truth", "goal_rate", "seen_minute", "kickoff_epoch", "strength")}
                        for k, m in self.matches.items()},
            "races": {k: {"market_id": r["market_id"], "venue": r["venue"], "race": r["race"], "start": r["start"],
                          "status": r["status"], "seconds_to_off": r["start_epoch"] - now, "winner": r["winner"],
                          "runners": {i: {kk: vv for kk, vv in run.items() if kk not in ("truth", "momentum")}
                                      for i, run in r["runners"].items()}}
                      for k, r in self.races.items()},
        }
