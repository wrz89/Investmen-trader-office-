"""Prova di OddsPapi (https://oddspapi.io): quote di Pinnacle e di Betfair Exchange in una sola chiamata.

SOLA LETTURA e NON collegato alle puntate: serve a verificare, con la tua chiave gratuita, tre cose che la
documentazione non dice: (1) i nomi (slug) dei bookmaker Pinnacle e Betfair, (2) gli id dell'esito 1X2 del calcio,
(3) quanto sono FRESCHI i prezzi di Pinnacle (`changedAt`), che è il collo di bottiglia dei lay. Solo dopo questa
prova ha senso usare OddsPapi come riferimento al posto dei 500 crediti di The Odds API.

Documentazione letta il 06/10/2026: base https://v5.oddspapi.io/en, chiave nell'header X-API-Key, calcio sportId=10,
/fixtures/odds/main?tournamentId=… dà per ogni partita odds[slug][outcomeId] = {price, active, changedAt}.
Il piano gratuito sarebbe di 250 richieste al mese (dal blog del fornitore, NON dalla documentazione): per questo
ogni chiamata è contata in runtime/oddspapi_budget.json e ci si ferma a `MONTHLY_CAP`.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import requests

from .config import RUNTIME_DIR, oddspapi_key
from .notifier import hide_secret

BASE = "https://v5.oddspapi.io/en"
SOCCER = 10
MONTHLY_CAP = 200                       # sotto le 250 dichiarate: il resto è margine per errori
WANTED = ("serie a", "premier league", "championship", "bundesliga", "la liga", "laliga", "ligue 1", "serie b",
          "eredivisie", "primeira liga")


class OddsPapiError(Exception):
    pass


def _budget_path():
    return RUNTIME_DIR / "oddspapi_budget.json"


def _budget() -> dict:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    try:
        data = json.loads(_budget_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if data.get("month") != month:
        data = {"month": month, "used": 0}
    return data


def _count_request() -> None:
    data = _budget()
    data["used"] += 1
    _budget_path().parent.mkdir(parents=True, exist_ok=True)
    _budget_path().write_text(json.dumps(data), encoding="utf-8")


class OddsPapiClient:
    def __init__(self, key: str, base: str = BASE, session=None):
        if not key:
            raise OddsPapiError("Manca la chiave di OddsPapi.")
        self.key = key
        self.base = base
        self.http = session or requests
        self.last_headers: dict = {}

    def get(self, path: str, **params):
        if _budget()["used"] >= MONTHLY_CAP:
            raise OddsPapiError(f"Tetto mensile di {MONTHLY_CAP} richieste raggiunto: mi fermo per non esaurire il piano.")
        _count_request()
        try:
            r = self.http.get(f"{self.base}{path}", params={k: v for k, v in params.items() if v is not None},
                              headers={"X-API-Key": self.key}, timeout=20)
        except requests.RequestException as exc:
            raise OddsPapiError(f"OddsPapi non raggiungibile: {hide_secret(exc, self.key)}") from None
        self.last_headers = dict(getattr(r, "headers", {}) or {})
        if r.status_code in (401, 403):
            raise OddsPapiError(f"OddsPapi rifiuta la chiave o l'accesso ({r.status_code}): {r.text[:200]}")
        if r.status_code == 429:
            raise OddsPapiError("OddsPapi: troppe richieste o quota finita (429).")
        if r.status_code >= 400:
            raise OddsPapiError(f"OddsPapi risponde {r.status_code}: {r.text[:200]}")
        return r.json()


def items(data) -> list[dict]:
    """Elenco di oggetti da una risposta il cui formato non è noto: lista, dict con una lista dentro, o dict di dict."""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for k in ("data", "items", "results", "bookmakers", "markets", "tournaments", "fixtures"):
            if isinstance(data.get(k), list):
                return [x for x in data[k] if isinstance(x, dict)]
        out = []
        for k, v in data.items():
            if isinstance(v, dict):
                out.append({"slug": k, **v})
        return out
    return []


def find_slugs(bookmakers) -> dict:
    """Slug di Pinnacle e dell'exchange Betfair dall'elenco dei bookmaker."""
    found: dict[str, list] = {"pinnacle": [], "betfair": []}
    for b in items(bookmakers):
        slug = str(b.get("slug") or b.get("bookmakerSlug") or "")
        text = (slug + " " + str(b.get("name") or b.get("bookmakerName") or "")).lower()
        for k in found:
            if k in text and slug and slug not in found[k]:
                found[k].append(slug)
    return found


def find_1x2(markets) -> dict | None:
    """Id degli esiti 1, X, 2 del tempo regolamentare (marketType 1x2, handicap 0, 3 esiti)."""
    best = None
    for m in items(markets):
        mt = str(m.get("marketType") or "").lower()
        outs = m.get("outcomes") or []
        if mt != "1x2" or len(outs) != 3:
            continue
        period = str(m.get("period") or "").lower()
        if period not in ("fulltime", "result", "ft", ""):
            continue
        if m.get("handicap") not in (None, 0, 0.0, "0", "0.0"):
            continue
        ids = {str(o.get("outcomeName")).strip().lower(): str(o.get("outcomeId")) for o in outs}
        best = {"1": ids.get("1") or ids.get("home"), "X": ids.get("x") or ids.get("draw"),
                "2": ids.get("2") or ids.get("away")}
        if all(best.values()):
            return best
    return None


def pick_tournaments(tournaments, wanted=WANTED, limit: int = 3) -> list[dict]:
    out = []
    for t in items(tournaments):
        name = str(t.get("tournamentName") or t.get("name") or "").lower()
        if any(w == name or name.startswith(w) for w in wanted):
            out.append(t)
    return out[:limit]


def fixture_view(fx: dict, slugs: dict, ids: dict, now: float | None = None) -> dict:
    """Per una partita: quota 1/X/2 e età (secondi) della quota per Pinnacle e per Betfair."""
    now = now or time.time()
    odds = fx.get("odds") or {}
    res = {"partita": f"{(fx.get('participants') or {}).get('participant1Name')} - "
                      f"{(fx.get('participants') or {}).get('participant2Name')}",
           "inizio": fx.get("startTime")}
    for nome, lst in slugs.items():
        for slug in lst:
            row = odds.get(slug)
            if not row:
                continue
            quote, eta = {}, []
            for lab, oid in ids.items():
                cell = row.get(oid)
                if isinstance(cell, dict) and cell.get("price"):
                    quote[lab] = cell["price"]
                    ch = cell.get("changedAt")
                    if ch:
                        eta.append(now - (ch / 1000 if ch > 1e11 else ch))
            if quote:
                res[slug] = {"quote": quote, "eta_s": round(max(eta)) if eta else None}
    return res


def run(out=print, client: OddsPapiClient | None = None) -> int:
    from . import local_settings
    key = oddspapi_key()
    if client is None:
        if not key:
            import getpass
            out("Incolla la chiave di OddsPapi (non si vede mentre scrivi) e premi Invio:")
            key = getpass.getpass("").strip()
            if not key:
                out("Nessuna chiave: mi fermo.")
                return 1
            s = local_settings.load()
            s["oddspapi_key"] = key
            local_settings.save(s)
            out("Chiave salvata sul tuo PC (cifrata).")
        client = OddsPapiClient(key)
    try:
        used0 = _budget()["used"]
        out("1/4 Bookmaker disponibili con la tua chiave…")
        slugs = find_slugs(client.get("/bookmakers"))
        out(f"    Pinnacle: {slugs['pinnacle'] or 'NON TROVATO'}   Betfair: {slugs['betfair'] or 'NON TROVATO'}")
        out("2/4 Mercati del calcio…")
        ids = find_1x2(client.get("/markets", sportId=SOCCER))
        out(f"    esiti 1X2: {ids or 'NON TROVATI'}")
        out("3/4 Campionati…")
        tours = pick_tournaments(client.get("/tournaments", sportId=SOCCER))
        out("    " + (", ".join(str(t.get("tournamentName") or t.get("name")) for t in tours) or "nessuno riconosciuto"))
        if not (ids and tours and (slugs["pinnacle"] or slugs["betfair"])):
            out("\nMi manca qualcosa per proseguire: guarda sopra cosa è 'NON TROVATO'. Nessuna puntata toccata.")
            return 2
        tid = tours[0].get("tournamentId") or tours[0].get("id")
        books = ",".join(slugs["pinnacle"][:1] + slugs["betfair"][:1])
        out(f"4/4 Quote di {tours[0].get('tournamentName') or tours[0].get('name')} ({books})…")
        data = client.get("/fixtures/odds/main", tournamentId=tid, bookmakers=books)
        n = 0
        etas = []
        for fx in items(data)[:200]:
            v = fixture_view(fx, slugs, ids)
            if len(v) <= 2:
                continue
            n += 1
            if n <= 5:
                out("    " + json.dumps(v, ensure_ascii=False))
            for slug in slugs["pinnacle"]:
                if isinstance(v.get(slug), dict) and v[slug].get("eta_s") is not None:
                    etas.append(v[slug]["eta_s"])
        used = _budget()["used"]
        out(f"\nRichieste usate in questa prova: {used - used0} · nel mese: {used}/{MONTHLY_CAP} (tetto di sicurezza)")
        if etas:
            etas.sort()
            out(f"Età delle quote Pinnacle: mediana {etas[len(etas)//2]} s, massima {etas[-1]} s su {len(etas)} partite")
            out("→ se la mediana è sotto ~1.800 s (30 minuti) OddsPapi è un buon riferimento per i lay.")
        else:
            out("Nessuna quota Pinnacle trovata nelle partite lette (campionato fermo o piano senza Pinnacle).")
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        (RUNTIME_DIR / "oddspapi_scoperta.json").write_text(
            json.dumps({"slugs": slugs, "esiti_1x2": ids, "campionati": [t.get("tournamentName") or t.get("name") for t in tours]},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        return 0
    except OddsPapiError as exc:
        out(f"\n{exc}")
        return 1
