"""AGENTE 2 — OSSERVATRICE QUOTE. Legge il feed, salva partite/quote/punteggi e controlla la qualità dei dati."""
from __future__ import annotations

from ..feeds.base import FeedError
from ..odds import consensus
from .base import Agent


def board(snap: dict, limit: int = 40) -> dict:
    """Vista compatta di partite e corse per il tabellone della dashboard."""
    matches = []
    for m in snap["matches"].values():
        books = m.get("live_books") if m["status"] == "LIVE" else m.get("books")
        ex = m.get("exchange") or {}
        fav = None
        if not books and ex:
            # senza quote di riferimento: favorito e probabilità dal solo exchange (medio tra back e lay)
            mids = {s: 2 / ((b.get("back") or 0) + (b.get("lay") or b.get("back") or 0)) for s, b in ex.items() if b.get("back")}
            tot = sum(mids.values())
            if tot:
                sel = max(mids, key=mids.get)
                fav = {"selection": sel, "name": m["home"] if sel == "home" else m["away"] if sel == "away" else "X",
                       "fair_prob": mids[sel] / tot, "best_odds": ex[sel]["back"], "best_book": "Betfair",
                       "edge": 0.0, "n_books": 0}
        if books:
            c = {k: v for k, v in consensus(books).items() if not k.startswith("_")}
            if c:
                sel, v = max(c.items(), key=lambda kv: kv[1]["fair_prob"])
                exb = (ex.get(sel) or {}).get("back")
                comm = m.get("commission") or 0.045
                fav = {"selection": sel, "name": m["home"] if sel == "home" else m["away"] if sel == "away" else "X",
                       "fair_prob": v["fair_prob"], "best_odds": exb or v["best_odds"], "best_book": "Betfair" if exb else v["best_book"],
                       "edge": (v["fair_prob"] * (exb - 1) * (1 - comm) - (1 - v["fair_prob"])) if exb else v["edge"],
                       "n_books": v["n_books"]}
        matches.append({k: m.get(k) for k in ("match_id", "sport", "league", "home", "away", "kickoff", "status", "minute",
                                              "home_score", "away_score", "result", "stats")} | {"fav": fav})
    order = {"LIVE": 0, "SCHEDULED": 1, "FINISHED": 2}
    matches.sort(key=lambda x: (order.get(x["status"], 3), x["kickoff"] if x["status"] != "FINISHED" else "~" + x["kickoff"]))
    races = []
    for r in snap.get("races", {}).values():
        runners = sorted(r["runners"].items(), key=lambda kv: kv[1].get("back") or 999)[:6]
        races.append({"market_id": r["market_id"], "venue": r["venue"], "race": r["race"], "status": r["status"],
                      "seconds_to_off": r.get("seconds_to_off"), "winner": r.get("winner"),
                      "runners": [{"id": rid, "name": v["name"], "back": v.get("back"), "lay": v.get("lay"),
                                   "wom": v.get("wom")} for rid, v in runners]})
    races.sort(key=lambda r: (r["status"] != "OPEN", r.get("seconds_to_off") or 0))
    return {"ts": snap["ts"], "sim_time": snap.get("sim_time"), "source": snap["health"]["source"],
            "matches": matches[:limit], "races": races[:6]}


class Quote(Agent):
    key = "quote"
    name = "Sara"
    role = "Feed real-time: calendario, quote, punteggi live, corse"

    def _prune(self, keep_days: int = 3) -> None:
        """Lo storico delle quote serve al sentiment di mercato e al CLV: bastano pochi giorni.
        (Il libro scommesse e il registro eventi invece non si cancellano mai.)"""
        import time as _t
        from datetime import datetime, timezone
        from .. import clock
        if _t.time() - (self.office.__dict__.get("_last_prune") or 0) < 3600:
            return
        self.office._last_prune = _t.time()
        cutoff = datetime.fromtimestamp(clock.now() - keep_days * 86400, timezone.utc).isoformat(timespec="seconds")
        self.store.execute("DELETE FROM odds WHERE ts < ?", (cutoff,))

    async def scan(self) -> dict:
        self.status("working", "Leggo quote e punteggi…")
        snap = await self.office.feed.fetch()
        if snap["health"]["error_rate"] > 0.3:
            raise FeedError(f"feed instabile ({snap['health']['error_rate']:.0%} di chiamate fallite)")
        snap.setdefault("time_scale", getattr(self.office.feed, "speed", 1.0))
        rows, open_matches = [], 0
        for m in snap["matches"].values():
            self.store.upsert_match(m)
            if m["status"] == "FINISHED":
                continue
            open_matches += 1
            live = m["status"] == "LIVE"
            for book, prices in (m["live_books"] if live else m["books"]).items():
                for sel, price in prices.items():
                    rows.append({"match_id": m["match_id"], "bookmaker": book, "selection": sel, "price": price,
                                 "live": live})
        for r in snap.get("races", {}).values():
            if r["status"] != "OPEN":
                continue
            for rid, run in r["runners"].items():
                rows.append({"match_id": r["market_id"], "bookmaker": "Exchange", "market": "exchange_win",
                             "selection": rid, "price": run["back"], "live": False})
        if rows:
            self.store.record_odds(rows)
        self._prune()
        self.office.cache = snap                               # cache in memoria (il ruolo di Redis)
        src = snap["health"].get("source", "")
        if ("riferimento fermo" in src or "non disponibili" in src) and self.store.get("ref_alert_day") != snap.get("sim_time", 0) // 86400:
            self.store.set("ref_alert_day", snap.get("sim_time", 0) // 86400)
            self.log(f"Quote di riferimento non aggiornate: {src}. Le puntate secche restano ferme finché tornano.",
                     "ERROR", "no_data")
        # si registrano solo i prezzi veri di Betfair: il mondo simulato e il replay non finiscono nelle registrazioni
        if self.settings["feed"].get("record") and self.settings["feed"].get("provider") == "betfair":
            try:
                from ..feeds.recorder import Recorder
                Recorder().write(snap)                         # registrazione per il replay (runtime/recordings)
            except Exception as exc:
                self.log(f"Registrazione non riuscita: {exc}. Nessun effetto sulle puntate.", "WARN", "record")
        self.store.set("board", board(snap))                   # tabellone per la dashboard
        live = sum(1 for m in snap["matches"].values() if m["status"] == "LIVE")
        races = sum(1 for r in snap.get("races", {}).values() if r["status"] == "OPEN")
        self.status("ok", f"{open_matches} partite in cartellone ({live} live), {races} corse aperte, "
                    f"{len(rows)} quote salvate. Fonte: {snap['health']['source']}.",
                    {"partite": open_matches, "live": live, "corse": races, "quote": len(rows)})
        return snap
