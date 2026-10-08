"""Calendario delle partite del mese, per sport e categoria (campionato).

Fonte: Betfair (listCompetitions + listMarketCatalogue, solo lettura) quando il conto è collegato; altrimenti le partite
che il bot ha già visto (tabella matches del database). Le risposte restano in memoria 20 minuti. Nessuna puntata.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

TTL = 1200
DAYS = 31
PAGE = 200                          # massimo di mercati per richiesta (Betfair)
MAX_PAGES = 15                      # fino a 3.000 partite per sport e campionato
_cache: dict = {}
_client = None


def _cached(key, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def sports_list(extra: dict | None = None) -> list[dict]:
    from .feeds.betfair import SPORT_LABELS, sports_table
    return [{"key": k, "label": SPORT_LABELS.get(k, k)} for k in sports_table(extra)]


def _betfair():
    """Client Betfair della dashboard (login col certificato, una volta); None se il conto non è collegato."""
    global _client
    if _client is not None:
        return _client
    from . import local_settings
    from .feeds.betfair import BetfairClient
    bf = local_settings.load().get("betfair") or {}
    if not (bf.get("app_key") and bf.get("username")):
        return None
    c = BetfairClient(bf)
    c.login()
    _client = c
    return c


def competitions(sport: str, store=None, client=None) -> dict:
    from .feeds.betfair import sports_table
    tab = sports_table(None)
    if sport not in tab:
        return {"source": "none", "items": [], "note": "sport non valido"}

    def load():
        try:
            c = client or _betfair()
            if c is not None:
                comps = c.competitions(tab[sport][0])
                items = [{"id": cid, "name": name} for cid, name in comps.items()]
                return {"source": "betfair", "items": sorted(items, key=lambda x: x["name"])}
        except Exception as exc:
            note = f"Betfair non raggiungibile ({exc}): mostro le partite già viste dal bot"
        else:
            note = "Betfair non collegato: mostro le partite già viste dal bot"
        rows = store.query("SELECT DISTINCT league FROM matches WHERE sport LIKE ? AND league IS NOT NULL ORDER BY league",
                           (sport + "%",)) if store else []
        return {"source": "db", "items": [{"id": r["league"], "name": r["league"]} for r in rows], "note": note}
    return _cached(("comp", sport), load)


def matches(sport: str, comp: str | None = None, store=None, client=None) -> dict:
    from .feeds.betfair import sports_table, split_event_name
    tab = sports_table(None)
    if sport not in tab:
        return {"source": "none", "matches": [], "note": "sport non valido"}

    def load():
        now = datetime.now(timezone.utc)
        try:
            c = client or _betfair()
            if c is not None:
                et, mt, _ = tab[sport]
                cat, seen, offset, truncated = [], set(), 0.0, False
                for _page in range(MAX_PAGES):                 # pagine per orario d'inizio: il limite è 200 mercati a richiesta
                    page = c.catalogue(et, mt, DAYS * 24, None, PAGE, lookback_hours=-offset,
                                       competition_ids=[comp] if comp else None)
                    fresh = [m for m in page if (m.get("marketId") or (m["marketStartTime"], (m.get("event") or {}).get("name"))) not in seen]
                    for m in fresh:
                        seen.add(m.get("marketId") or (m["marketStartTime"], (m.get("event") or {}).get("name")))
                    cat += fresh
                    if len(page) < PAGE:
                        break
                    last = max(datetime.fromisoformat(m["marketStartTime"].replace("Z", "+00:00")) for m in page)
                    new_offset = (last - now).total_seconds() / 3600
                    offset = max(new_offset, offset + 1 / 60)    # sempre avanti, anche se 200 mercati partono insieme
                else:
                    truncated = True
                out = []
                for m in cat:
                    teams = split_event_name((m.get("event") or {}).get("name", "")) or ((m.get("event") or {}).get("name", "?"), "")
                    out.append({"start": m["marketStartTime"], "home": teams[0].strip(), "away": teams[1].strip(),
                                "league": (m.get("competition") or {}).get("name")})
                note = (f"Mostro le prime {len(out)} partite dei prossimi 31 giorni: scegli un campionato per vederle tutte."
                        if truncated else "")
                return {"source": "betfair", "matches": out, "note": note}
        except Exception as exc:
            note = f"Betfair non raggiungibile ({exc}): mostro le partite già viste dal bot"
        else:
            note = "Betfair non collegato: mostro le partite già viste dal bot"
        if not store:
            return {"source": "none", "matches": [], "note": note}
        q = "SELECT kickoff, home, away, league FROM matches WHERE sport LIKE ? AND kickoff >= ? AND kickoff <= ?"
        args = [sport + "%", (now - timedelta(hours=3)).isoformat(), (now + timedelta(days=DAYS)).isoformat()]
        if comp:
            q += " AND league=?"
            args.append(comp)
        rows = store.query(q + " ORDER BY kickoff LIMIT 500", tuple(args))
        return {"source": "db", "note": note, "matches": [{"start": r["kickoff"], "home": r["home"], "away": r["away"],
                                                            "league": r["league"]} for r in rows]}
    return _cached(("m", sport, comp), load)
