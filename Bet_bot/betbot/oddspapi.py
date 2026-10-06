"""Prova di OddsPapi (https://oddspapi.io): quote di Pinnacle e di Betfair Exchange in una sola chiamata.

SOLA LETTURA e NON collegato alle puntate: serve a verificare, con la tua chiave gratuita, tre cose che la
documentazione non dice: (1) i nomi (slug) dei bookmaker Pinnacle e Betfair, (2) gli id dell'esito 1X2 del calcio,
(3) quanto sono FRESCHI i prezzi di Pinnacle (`changedAt`), che è il collo di bottiglia dei lay. Solo dopo questa
prova ha senso usare OddsPapi come riferimento al posto dei 500 crediti di The Odds API.

Verificato con la chiave gratuita il 06/10/2026: la chiave funziona SOLO sulla v4 (https://api.oddspapi.io/v4, parametro
apiKey); la v5 della documentazione è per clienti B2B e risponde 401. Verificati: slug "pinnacle" e "betfair-ex" (c'è anche
"betfair.it"), calcio sportId=10, esito 1X2 tempo regolamentare 101/102/103, Serie A tournamentId 23, Premier 17.
NON verificato (il mio IP era bloccato su /fixtures): il formato di /odds-by-tournaments, letto in modo tollerante.
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

BASE = "https://api.oddspapi.io/v4"
SOCCER = 10
MONTHLY_CAP = 200                       # sotto le 250 dichiarate: il resto è margine per errori
WANTED = (("serie a", "italy"), ("premier league", "england"), ("championship", "england"), ("bundesliga", "germany"),
          ("laliga", "spain"), ("la liga", "spain"), ("ligue 1", "france"), ("serie b", "italy"),
          ("eredivisie", "netherlands"), ("liga portugal", "portugal"), ("primeira liga", "portugal"))


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
            r = self.http.get(f"{self.base}{path}", params={"apiKey": self.key, **{k: v for k, v in params.items() if v is not None}},
                              timeout=20)
        except requests.RequestException as exc:
            raise OddsPapiError(f"OddsPapi non raggiungibile: {hide_secret(exc, self.key)}") from None
        self.last_headers = dict(getattr(r, "headers", {}) or {})
        if r.status_code in (401, 403):
            raise OddsPapiError(f"OddsPapi rifiuta la chiave o l'accesso ({r.status_code}): {hide_secret(r.text[:200], self.key)}")
        if r.status_code == 429:
            ra = self.last_headers.get("Retry-After") or self.last_headers.get("retry-after")
            raise OddsPapiError("OddsPapi: troppe richieste o quota finita (429)"
                                + (f", riprova tra {ra} s" if ra else "") + f". Risposta: {hide_secret(r.text[:300], self.key)}")
        if r.status_code >= 400:
            raise OddsPapiError(f"OddsPapi risponde {r.status_code}: {hide_secret(r.text[:200], self.key)}")
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
        text = (slug + " " + str(b.get("bookmakerName") or b.get("name") or "")).lower()
        if "+" in slug or b.get("cloneOf") or "betfair.it" in slug:      # varianti ritardate/cloni: si usa lo slug originale
            continue
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
        best_market = str(m.get("marketId"))
        if all(best.values()):
            best["market"] = best_market
            return best
    return None


def pick_tournaments(tournaments, wanted=WANTED, limit: int = 4) -> list[dict]:
    """Campionati principali (nome + paese, per non confondere la Premier inglese con quella russa), con partite in programma."""
    out = []
    for t in items(tournaments):
        name = str(t.get("tournamentName") or t.get("name") or "").lower()
        cat = str(t.get("categoryName") or "").lower()
        if not any(name == n and (not c or cat == c) for n, c in wanted):
            continue
        if t.get("futureFixtures") == 0 and t.get("upcomingFixtures") == 0 and t.get("liveFixtures") == 0:
            continue
        out.append(t)
    out.sort(key=lambda t: -(t.get("upcomingFixtures") or 0))
    return out[:limit]


def _ts(v) -> float | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return v / 1000 if v > 1e11 else float(v)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _price_cell(row: dict, market_id: str, outcome_id: str):
    """Cella {price, changedAt} da bookmakerOdds[slug]: markets[mid].outcomes[oid].players[*] (v4)."""
    try:
        out = row["markets"][market_id]["outcomes"][outcome_id]
    except (KeyError, TypeError):
        return None
    players = out.get("players") if isinstance(out, dict) else None
    if isinstance(players, dict) and players:
        return next(iter(players.values()))
    return out if isinstance(out, dict) else None


def fixture_view(fx: dict, slugs: dict, ids: dict, now: float | None = None) -> dict:
    """Per una partita: quota 1/X/2 e età (secondi) della quota per Pinnacle e per Betfair."""
    now = now or time.time()
    parts = fx.get("participants") or {}
    home = fx.get("participant1Name") or parts.get("participant1Name")
    away = fx.get("participant2Name") or parts.get("participant2Name")
    res = {"partita": f"{home} - {away}", "inizio": fx.get("startTime")}
    book = fx.get("bookmakerOdds") or {}
    market_id = ids.get("market") or "101"
    for lst in slugs.values():
        for slug in lst:
            row = book.get(slug)
            if not row:
                continue
            quote, eta = {}, []
            for lab in ("1", "X", "2"):
                cell = _price_cell(row, str(market_id), str(ids[lab]))
                if isinstance(cell, dict) and cell.get("price"):
                    quote[lab] = cell["price"]
                    t = _ts(cell.get("changedAt"))
                    if t:
                        eta.append(now - t)
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
        tid = str(tours[0].get("tournamentId") or tours[0].get("id"))     # un campionato per volta: più id insieme possono dare 429
        books = slugs["pinnacle"][:1] + slugs["betfair"][:1]
        out(f"4/4 Quote di {tid} ({', '.join(books)}; una chiamata per bookmaker)…")
        merged: dict[str, dict] = {}
        for bk in books:                        # l'API vuole ESATTAMENTE un bookmaker per chiamata
            if merged:
                time.sleep(6)                   # pausa tra due chiamate quote: evita il 429 di raffica
            for fx in items(client.get("/odds-by-tournaments", bookmaker=bk, tournamentIds=tid)):
                key = str(fx.get("fixtureId") or id(fx))
                cur = merged.setdefault(key, fx)
                if cur is not fx:
                    cur.setdefault("bookmakerOdds", {}).update(fx.get("bookmakerOdds") or {})
        data = list(merged.values())
        names = {}
        try:                                    # nomi delle squadre (le quote non li contengono): 1 richiesta in più
            for fx in items(client.get("/fixtures", tournamentId=tid)):
                names[str(fx.get("fixtureId"))] = f"{fx.get('participant1Name')} - {fx.get('participant2Name')}"
        except OddsPapiError as exc:
            out(f"    (nomi delle squadre non letti: {exc})")
        n = 0
        etas, etas_near = [], []
        shown_raw: list = []
        now = time.time()
        for fx in items(data)[:200]:
            v = fixture_view(fx, slugs, ids)
            v["partita"] = names.get(str(fx.get("fixtureId")), v["partita"])
            if len(v) <= 2:
                if n == 0 and not shown_raw:
                    shown_raw.append(1)
                    out("    (formato non riconosciuto, prima partita grezza: " + json.dumps(fx, ensure_ascii=False)[:700] + ")")
                continue
            n += 1
            start = _ts(fx.get("startTime"))
            near = start is not None and 0 <= start - now <= 8 * 3600
            if n <= 5 or near:
                out("    " + ("[entro 8 ore] " if near else "") + json.dumps(v, ensure_ascii=False))
            for slug in slugs["pinnacle"]:
                if isinstance(v.get(slug), dict) and v[slug].get("eta_s") is not None:
                    etas.append(v[slug]["eta_s"])
                    if near:
                        etas_near.append(v[slug]["eta_s"])
        used = _budget()["used"]
        out(f"\nRichieste usate in questa prova: {used - used0} · nel mese: {used}/{MONTHLY_CAP} (tetto di sicurezza)")
        if etas:
            etas.sort()
            out(f"Tempo dall'ultimo CAMBIO di quota Pinnacle (non dall'ultimo aggiornamento): mediana {etas[len(etas)//2]} s su {len(etas)} partite")
            if etas_near:
                etas_near.sort()
                out(f"Solo partite che iniziano entro 8 ore: mediana {etas_near[len(etas_near)//2]} s su {len(etas_near)}")
            else:
                out("Nessuna partita entro 8 ore: ripeti la prova il sabato o la domenica, vicino a partite del campionato.")
            out("ATTENZIONE: 'changedAt' è l'ultima volta che il prezzo è CAMBIATO, non l'ultima verifica. Una quota ferma da ore a più giorni "
                "dall'inizio è normale per Pinnacle. Il dato utile è quello delle partite vicine all'inizio.")
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
