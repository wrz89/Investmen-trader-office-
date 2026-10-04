"""AGENTE 9 — GIORGIA · ANALISTA SENTIMENT.

Due fonti, con pesi diversi:
  1. MERCATO: variazione della probabilità implicita della selezione negli ultimi 90 minuti. Soldi informati che
     puntano contro = quota della nostra squadra che sale. È il segnale più affidabile.
     Fonte: il prezzo di BETFAIR (medio tra back e lay, margine tolto), che cambia a ogni ciclo. Le quote dei
     bookmaker di riferimento servono solo se il prezzo Betfair non c'è: col piano gratuito di The Odds API si
     aggiornano ogni 2-6 ore, e in 90 minuti restano ferme (il segnale non scattava mai).
  2. NOTIZIE (SPENTE di serie dal 30/09/2026, news.enabled in sentiment.yaml): nessuna prova che valgano qualcosa
     (la palestra di Leo: assenze e notizie sono già nelle quote quando escono i titoli). titoli pubblici (Google News RSS, IT+EN) sulle squadre delle
     partite candidate. Classificazione con regole trasparenti, prima le frasi
     che smentiscono ("rientra", "recuperato"), poi quelle gravi e medie.

Sensibilità (evita di reagire a ogni titolo):
  • una notizia conta solo se nel TITOLO c'è il nome della squadra;
  • grave + ≥ 2 testate diverse → VETO; grave + 1 testata → CAUTELA (puntata dimezzata),
    che diventa VETO se anche il mercato si muove contro;
  • media → solo registro (cautela se confermata da ≥ 2 testate);
  • il blocco scade al fischio d'inizio o dopo block_hours;
  • notizie positive non aumentano mai nulla.
Ogni notizia viene salvata con la probabilità di consenso del momento, per
misurare in futuro se le segnalazioni avevano valore.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from datetime import datetime, timezone

import requests

from .. import clock
from ..config import load_yaml
from ..odds import consensus, remove_margin
from ..store import now_iso
from .base import Agent

SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE, ts TEXT, published TEXT, source TEXT,
    publisher TEXT, team TEXT, match_id TEXT, title TEXT, link TEXT, severity TEXT, words TEXT, fair_prob REAL
);
"""
UA = {"User-Agent": "Mozilla/5.0 (SportsBettingOffice sentiment desk)"}


def _rx(words: list[str]) -> re.Pattern:
    return re.compile(r"(?<!\w)(" + "|".join(re.escape(w.strip()) for w in words) + r")(?!\w)", re.IGNORECASE)


def classify(title: str, cfg: dict) -> dict:
    """Gravità di un titolo: neutral (smentita/rientro), high, medium, none."""
    if cfg.get("ignore") and _rx(cfg["ignore"]).search(title):
        return {"severity": "none", "words": []}
    for level, key in (("neutral", "neutralizers"), ("high", "high_risk"), ("medium", "medium_risk")):
        hits = sorted({m.lower() for m in _rx(cfg[key]).findall(title)})
        if hits:
            return {"severity": level, "words": hits}
    return {"severity": "none", "words": []}


def team_in_title(team: str, title: str) -> bool:
    return bool(re.search(r"(?<!\w)" + re.escape(team) + r"(?!\w)", title, re.IGNORECASE))


def split_publisher(title: str, fallback: str) -> tuple[str, str]:
    """Google News scrive i titoli come "Titolo - Testata": separa la testata per contare le fonti diverse."""
    head, sep, tail = title.rpartition(" - ")
    return (head.strip(), tail.strip()) if sep and 1 < len(tail) < 60 else (title, fallback)


class Sentiment(Agent):
    key = "sentiment"
    name = "Giorgia"
    role = "Sentiment: movimento delle quote e notizie (può solo frenare)"

    def __init__(self, office):
        super().__init__(office)
        self.cfg = load_yaml("sentiment.yaml")
        self.bf_history: dict[tuple[str, str], list[tuple[float, float]]] = {}   # (partita, esito) → [(t, prob)]
        with self.store._lock:
            self.store.conn.executescript(SCHEMA)
            self.store.conn.commit()

    # ── 1. sentiment di mercato ────────────────────────────────
    def observe_exchange(self, snapshot: dict) -> None:
        """A ogni ciclo: probabilità dal prezzo medio di Betfair per ogni esito pre-partita (libro completo e stretto)."""
        t = clock.now()
        keep = t - (self.cfg["market"]["lookback_minutes"] + 30) * 60
        for m in snapshot.get("matches", {}).values():
            ex = m.get("exchange") or {}
            if m.get("status") != "SCHEDULED" or not ex:
                continue
            mids = {}
            for sel, b in ex.items():
                if b.get("back") and b.get("lay") and b["back"] <= b["lay"] <= b["back"] * 1.1:
                    mids[sel] = (b["back"] + b["lay"]) / 2
            if len(mids) != len(ex) or len(mids) < 2:
                continue
            tot = sum(1 / v for v in mids.values())
            for sel, v in mids.items():
                h = self.bf_history.setdefault((m["match_id"], sel), [])
                h.append((t, (1 / v) / tot))
                while h and h[0][0] < keep:
                    h.pop(0)
        live_ids = set(snapshot.get("matches", {}))
        for k in [k for k in self.bf_history if k[0] not in live_ids]:
            del self.bf_history[k]

    def market_move(self, match_id: str, selection: str) -> float | None:
        """Variazione relativa della probabilità nel periodo (negativa = mercato contro): prima dal prezzo Betfair,
        altrimenti dal consenso dei bookmaker registrato."""
        mc = self.cfg["market"]
        h = [x for x in self.bf_history.get((match_id, selection), [])
             if x[0] >= clock.now() - mc["lookback_minutes"] * 60]
        if len(h) >= mc["min_points"] and h[-1][0] - h[0][0] >= 30 * 60 and h[0][1] > 0:
            return h[-1][1] / h[0][1] - 1.0
        since = datetime.fromtimestamp(clock.now() - mc["lookback_minutes"] * 60, timezone.utc).isoformat(timespec="seconds")
        rows = self.store.query("SELECT ts, bookmaker, selection, price FROM odds WHERE match_id=? AND ts>=? AND live=0 "
                                "ORDER BY ts", (match_id, since))
        by_ts: dict[str, dict[str, dict[str, float]]] = {}
        for r in rows:
            by_ts.setdefault(r["ts"], {}).setdefault(r["bookmaker"], {})[r["selection"]] = r["price"]
        series = []
        for ts, books in by_ts.items():
            probs = [remove_margin(p).get(selection) for p in books.values() if len(p) >= 2]
            probs = [x for x in probs if x]
            if probs:
                series.append(sum(probs) / len(probs))
        if len(series) < mc["min_points"] or series[0] <= 0:
            return None
        return series[-1] / series[0] - 1.0

    # ── verdetto per il Risk Manager ───────────────────────────
    def verdict(self, p: dict) -> dict:
        if p.get("exchange") or p.get("legs"):
            return {"level": "ok"}                    # trading cavalli e arbitraggio: il sentiment non si applica
        level, reasons = "ok", []
        mc = self.cfg["market"]
        move = None if p.get("live") else self.market_move(p["match_id"], p["selection"])
        if move is not None and move <= -mc["block_drop"]:
            level, reasons = "block", [f"mercato contro: probabilità {move:+.1%} in {mc['lookback_minutes']} min"]
        elif move is not None and move <= -mc["caution_drop"]:
            level, reasons = "caution", [f"mercato in uscita: probabilità {move:+.1%}"]
        flag = self._team_flag(p)
        if flag:
            if flag["level"] == "block" or (flag["level"] == "caution" and level != "ok"):
                level = "block"                       # notizia + mercato che conferma
            elif level == "ok":
                level = "caution"
            reasons.append(flag["reason"])
        return {"level": level, "reason": "; ".join(reasons), "market_move": move}

    def _team_flag(self, p: dict) -> dict | None:
        m = (self.office.cache or {}).get("matches", {}).get(p["match_id"])
        if not m:
            return None
        flags = self.store.get("sentiment_flags") or {}
        now = clock.now()
        teams = [m["home"], m["away"]] if p["selection"] == "draw" else [m.get(p["selection"])]
        for t in teams:
            f = flags.get((t or "").lower())
            if f and f["until"] > now:
                if p["selection"] == "draw":           # sul pareggio una notizia vale al massimo cautela
                    return {"level": "caution", "reason": f"{t}: {f['reason']}"}
                return {"level": f["level"], "reason": f"{t}: {f['reason']}"}
        return None

    # ── 2. notizie ──────────────────────────────────────────────
    def run(self, snapshot: dict) -> None:
        ncfg = self.cfg["news"]
        self.observe_exchange(snapshot)
        if not ncfg.get("enabled", False):
            self._status("notizie spente: guardo il movimento del prezzo Betfair a ogni proposta")
            return
        if snapshot["health"]["source"].startswith("mock") and not ncfg.get("enabled_with_mock_feed"):
            self._status("notizie spente col feed simulato; attivo il sentiment di mercato")
            return
        last = self.store.get("sentiment_last_fetch") or 0
        if clock.now() - last < ncfg["refresh_minutes"] * 60:
            self._status()
            return
        self.store.set("sentiment_last_fetch", clock.now())
        teams = self._candidate_teams(snapshot)
        read, failed = 0, 0
        for team, match in teams:
            try:
                read += self._read_team(team, match)
            except Exception:
                failed += 1
        self._status(f"lette {read} notizie su {len(teams)} squadre" + (f", {failed} fonti non raggiungibili" if failed else ""))

    def _candidate_teams(self, snapshot: dict) -> list[tuple[str, dict]]:
        """Squadre delle partite in cui c'è un favorito (quelle che le strategie potrebbero giocare)."""
        ncfg = self.cfg["news"]
        seen = self.store.get("sentiment_team_seen") or {}
        now = clock.now()
        cands = []
        for m in snapshot["matches"].values():
            if m["status"] != "SCHEDULED" or not m.get("books"):
                continue
            c = consensus(m["books"])
            fav = max((v["fair_prob"] for k, v in c.items() if not k.startswith("_")), default=0)
            if fav < 0.6:
                continue
            ko = datetime.fromisoformat(m["kickoff"]).timestamp()
            for t in (m["home"], m["away"]):
                if now - seen.get(t, 0) >= ncfg["team_cache_minutes"] * 60:
                    cands.append((ko, t, m))
        cands.sort(key=lambda x: x[0])
        out = [(t, m) for _, t, m in cands[: ncfg["max_teams_per_refresh"]]]
        for t, _ in out:
            seen[t] = now
        self.store.set("sentiment_team_seen", seen)
        return out

    def _read_team(self, team: str, match: dict) -> int:
        from ..feeds.rss import parse_feed
        ncfg = self.cfg["news"]
        items = []
        for feed in ncfg["feeds"]:
            url = feed["url"].replace("{team}", urllib.parse.quote(team))
            r = requests.get(url, headers=UA, timeout=12)
            r.raise_for_status()
            items += [{**it, "source": feed["name"]} for it in parse_feed(r.content)]
        c = consensus(match["books"]) if match.get("books") else {}
        side = "home" if team == match["home"] else "away"
        fair = (c.get(side) or {}).get("fair_prob")
        n = 0
        now = datetime.now(timezone.utc)
        for it in items:
            if it["published"] and (now - it["published"]).total_seconds() > ncfg["max_age_hours"] * 3600:
                continue
            title, publisher = split_publisher(it["title"], it["source"])
            if not team_in_title(team, title):
                continue
            cls = classify(title, ncfg)
            uid = hashlib.sha1((it["link"] or it["title"]).encode()).hexdigest()
            cur = self.store.execute(
                "INSERT OR IGNORE INTO news(uid, ts, published, source, publisher, team, match_id, title, link, severity, "
                "words, fair_prob) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (uid, now_iso(), it["published"].isoformat() if it["published"] else None, it["source"], publisher, team,
                 match["match_id"], title, it["link"], cls["severity"], json.dumps(cls["words"]), fair))
            n += cur.rowcount
        self._evaluate_team(team, match)
        return n

    def _evaluate_team(self, team: str, match: dict) -> None:
        """Conta le testate DIVERSE con notizie gravi/medie non smentite nelle ultime ore e decide il flag."""
        ncfg = self.cfg["news"]
        rows = self.store.query("SELECT publisher, severity, title, published FROM news WHERE team=? AND match_id=? "
                                "ORDER BY COALESCE(published, ts) DESC", (team, match["match_id"]))
        if rows and rows[0]["severity"] == "neutral":
            self._clear(team, f"ultima notizia rassicurante: «{rows[0]['title'][:90]}»")
            return
        high = {r["publisher"] for r in rows if r["severity"] == "high"}
        med = {r["publisher"] for r in rows if r["severity"] == "medium"}
        top = next((r["title"] for r in rows if r["severity"] in ("high", "medium")), "")
        level = None
        if len(high) >= ncfg["confirm_sources_block"]:
            level = "block"
        elif high or len(med) >= ncfg["confirm_sources_block"]:
            level = "caution"
        if not level:
            return
        ko = datetime.fromisoformat(match["kickoff"]).timestamp()
        until = min(ko, clock.now() + ncfg["block_hours"] * 3600)
        flags = self.store.get("sentiment_flags") or {}
        prev = flags.get(team.lower(), {}).get("level")
        flags[team.lower()] = {"level": level, "until": until, "team": team, "match_id": match["match_id"],
                               "reason": f"«{top[:100]}» ({len(high)} testate gravi, {len(med)} medie)",
                               "sources": sorted(high | med)}
        self.store.set("sentiment_flags", flags)
        if prev != level:
            what = "VETO sulle puntate a favore" if level == "block" else "CAUTELA (puntate dimezzate)"
            self.say(f"{team}: {what} fino al fischio d'inizio. {flags[team.lower()]['reason']}.",
                     "alert" if level == "block" else "working", "sentiment", level="WARN",
                     payload=flags[team.lower()])

    def _clear(self, team: str, reason: str) -> None:
        flags = self.store.get("sentiment_flags") or {}
        if flags.pop(team.lower(), None):
            self.store.set("sentiment_flags", flags)
            self.log(f"{team}: allarme rientrato ({reason}).", "INFO", "sentiment")

    def _status(self, extra: str = "") -> None:
        now = clock.now()
        flags = {k: v for k, v in (self.store.get("sentiment_flags") or {}).items() if v["until"] > now}
        self.store.set("sentiment_flags", flags)
        day = self.store.query("SELECT COUNT(*) n FROM news WHERE ts >= ?",
                               (datetime.fromtimestamp(now - 86400, timezone.utc).isoformat(timespec="seconds"),))[0]["n"]
        since = datetime.fromtimestamp(now - 86400, timezone.utc).isoformat(timespec="seconds")
        try:                                           # il lavoro vero di oggi: puntate frenate dal prezzo che si muove contro
            braked = self.store.query("SELECT COUNT(*) n FROM events WHERE kind='veto' AND ts >= ? AND "
                                      "(payload LIKE '%Sentiment e mercato%' OR payload LIKE '%Mercato non in uscita%')", (since,))[0]["n"]
        except Exception:
            braked = 0
        stats = {"flags": len(flags), "news_24h": day, "frenate_24h": braked}
        tail = f" Freni dal movimento del prezzo Betfair nelle ultime 24 ore: {braked} veti."
        if flags:
            lst = ", ".join(f"{v['team']} ({'veto' if v['level'] == 'block' else 'cautela'})" for v in flags.values())
            self.status("alert", f"Segnalazioni attive: {lst}." + (f" {extra}." if extra else ""), stats)
        else:
            self.status("ok", "Nessuna notizia segnalata (le notizie sono spente). Controllo il movimento delle quote a ogni proposta."
                        + tail + (f" ({extra})" if extra else ""), stats)
