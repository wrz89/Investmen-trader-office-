"""Betfair Exchange (Italia) — client REST JSON-RPC, Stream API e feed.

Perché Betfair: è l'unico operatore con concessione ADM che offre un'API
ufficiale per leggere quote E piazzare puntate. I bookmaker a quota fissa
italiani (Sisal, Snai, Eurobet, Bet365…) non hanno API pubbliche: automatizzarne
il sito viola i termini d'uso e porta alla chiusura del conto. L'ufficio non lo fa.

Endpoint (documentazione ufficiale Betfair, "Betting On Italian Exchange"):
  login      https://identitysso.betfair.it/api/login            (utente + password + app key)
  certlogin  https://identitysso-cert.betfair.it/api/certlogin   (certificato SSL, consigliato per i bot)
  betting    https://api.betfair.com/exchange/betting/json-rpc/v1 (restituisce i mercati del conto italiano)
  account    https://api.betfair.com/exchange/account/json-rpc/v1
  keepAlive  https://identitysso.betfair.it/api/keepAlive
  stream     stream-api.betfair.com:443, socket SSL, JSON separato da CRLF

App key: la "delayed" è gratuita (quote ritardate, niente puntate vere); la
"live" si attiva dal proprio account Betfair a pagamento una tantum.
"""
from __future__ import annotations

import asyncio
import json
import ssl
import time
from datetime import datetime, timedelta, timezone

import requests

from .base import Feed, FeedError

SSO = "https://identitysso.betfair.it/api"
SSO_CERT = "https://identitysso-cert.betfair.it/api/certlogin"
BETTING = "https://api.betfair.com/exchange/betting/json-rpc/v1"
ACCOUNT = "https://api.betfair.com/exchange/account/json-rpc/v1"
STREAM_HOST, STREAM_PORT = "stream-api.betfair.com", 443

HORSE_RACING, SOCCER = "7", "1"


class BetfairError(Exception):
    pass


class BetfairClient:
    """Chiamate sincrone (requests). Il feed le esegue in un thread per non bloccare il loop async."""

    SESSION_TTL = 3 * 3600            # keepAlive ogni 3 ore (la sessione italiana scade dopo ~4 h di inattività)

    def __init__(self, creds: dict):
        self.app_key = creds.get("app_key") or ""
        self.username = creds.get("username") or ""
        self.password = creds.get("password") or ""
        self.cert = (creds.get("cert_file"), creds.get("key_file")) if creds.get("cert_file") else None
        self.token: str | None = None
        self.token_ts = 0.0
        self.calls = 0
        self.errors = 0
        self.http = requests.Session()

    # ── sessione ────────────────────────────────────────────────
    def login(self) -> str:
        if not (self.app_key and self.username and self.password):
            raise BetfairError("Credenziali Betfair incomplete (app key, utente, password).")
        headers = {"X-Application": self.app_key, "Accept": "application/json",
                   "Content-Type": "application/x-www-form-urlencoded"}
        data = {"username": self.username, "password": self.password}
        try:
            if self.cert:
                r = self.http.post(SSO_CERT, data=data, headers=headers, cert=self.cert, timeout=15)
                body = r.json()
                ok, token, err = body.get("loginStatus") == "SUCCESS", body.get("sessionToken"), body.get("loginStatus")
            else:
                r = self.http.post(f"{SSO}/login", data=data, headers=headers, timeout=15)
                body = r.json()
                ok, token, err = body.get("status") == "SUCCESS", body.get("token"), body.get("error")
        except (requests.RequestException, ValueError) as exc:
            raise BetfairError(f"Login Betfair non riuscito: {exc}") from exc
        if not ok:
            raise BetfairError(f"Login Betfair rifiutato: {err}")
        self.token, self.token_ts = token, time.time()
        return token

    def ensure_session(self) -> None:
        if not self.token:
            self.login()
        elif time.time() - self.token_ts > self.SESSION_TTL:
            try:
                r = self.http.post(f"{SSO}/keepAlive", headers=self._headers(), timeout=10).json()
                if r.get("status") == "SUCCESS":
                    self.token_ts = time.time()
                    return
            except (requests.RequestException, ValueError):
                pass
            self.login()

    def _headers(self) -> dict:
        return {"X-Application": self.app_key, "X-Authentication": self.token or "",
                "Content-Type": "application/json", "Accept": "application/json"}

    def rpc(self, method: str, params: dict, url: str = BETTING, service: str = "SportsAPING/v1.0") -> dict | list:
        self.ensure_session()
        self.calls += 1
        payload = {"jsonrpc": "2.0", "method": f"{service}/{method}", "params": params, "id": 1}
        try:
            r = self.http.post(url, data=json.dumps(payload), headers=self._headers(), timeout=15)
            body = r.json()
        except (requests.RequestException, ValueError) as exc:
            self.errors += 1
            raise BetfairError(f"Betfair non raggiungibile: {exc}") from exc
        if "error" in body:
            self.errors += 1
            err = body["error"]
            detail = (err.get("data") or {}).get("APINGException", {}).get("errorCode") or err.get("message")
            if detail in ("INVALID_SESSION_INFORMATION", "NO_SESSION"):
                self.token = None
            raise BetfairError(f"Betfair {method}: {detail}")
        return body["result"]

    # ── lettura ─────────────────────────────────────────────────
    def account_funds(self) -> dict:
        return self.rpc("getAccountFunds", {}, ACCOUNT, "AccountAPING/v1.0")

    def catalogue(self, event_type: str, market_type: str, hours: float, countries: list[str] | None = None,
                  max_results: int = 30) -> list[dict]:
        now = datetime.now(timezone.utc)
        mfilter = {"eventTypeIds": [event_type], "marketTypeCodes": [market_type],
                   "marketStartTime": {"from": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
                                       "to": (now + timedelta(hours=hours)).isoformat(timespec="seconds").replace("+00:00", "Z")}}
        if countries:
            mfilter["marketCountries"] = countries
        return self.rpc("listMarketCatalogue", {"filter": mfilter, "maxResults": max_results, "sort": "FIRST_TO_START",
                                                "marketProjection": ["EVENT", "RUNNER_DESCRIPTION", "MARKET_START_TIME",
                                                                     "COMPETITION"]})

    def books(self, market_ids: list[str]) -> list[dict]:
        out = []
        for i in range(0, len(market_ids), 10):          # limite di peso delle richieste: 10 mercati per volta
            out += self.rpc("listMarketBook", {"marketIds": market_ids[i:i + 10],
                                               "priceProjection": {"priceData": ["EX_BEST_OFFERS"],
                                                                   "exBestOffersOverrides": {"bestPricesDepth": 3}}})
        return out

    # ── ordini ─────────────────────────────────────────────────
    def place(self, market_id: str, selection_id: int, side: str, price: float, size: float,
              fill_or_kill: bool = True, ref: str | None = None) -> dict:
        """Ordine LIMIT. Con fill_or_kill l'ordine è abbinato subito per intero o annullato:
        niente ordini "appesi" che si abbinano quando il prezzo non conviene più."""
        order = {"selectionId": int(selection_id), "side": side, "orderType": "LIMIT",
                 "limitOrder": {"size": round(size, 2), "price": price, "persistenceType": "LAPSE"}}
        if fill_or_kill:
            order["limitOrder"]["timeInForce"] = "FILL_OR_KILL"
        params = {"marketId": market_id, "instructions": [order]}
        if ref:
            params["customerRef"] = ref[:32]
        res = self.rpc("placeOrders", params)
        if res.get("status") != "SUCCESS":
            raise BetfairError(f"placeOrders: {res.get('errorCode')} "
                               f"{[i.get('errorCode') for i in res.get('instructionReports', [])]}")
        rep = res["instructionReports"][0]
        return {"bet_id": rep.get("betId"), "matched": float(rep.get("sizeMatched") or 0.0),
                "avg_price": float(rep.get("averagePriceMatched") or 0.0), "status": rep.get("orderStatus")}


    def cancel(self, market_id: str, bet_id: str | None = None) -> dict:
        params = {"marketId": market_id}
        if bet_id:
            params["instructions"] = [{"betId": bet_id}]
        return self.rpc("cancelOrders", params)

    def cleared(self, bet_ids: list[str]) -> dict[str, dict]:
        """Esito delle puntate regolate: {bet_id: {"profit", "outcome"}} (WON / LOST / …)."""
        res = self.rpc("listClearedOrders", {"betStatus": "SETTLED", "betIds": bet_ids, "includeItemDescription": False})
        return {o["betId"]: {"profit": float(o.get("profit", 0.0)), "outcome": o.get("betOutcome")}
                for o in res.get("clearedOrders", [])}


# ── Stream API: cache dei mercati aggiornata dai messaggi "mcm" ─────────────────
class MarketCache:
    """Applica i delta dello Stream API. Con EX_BEST_OFFERS arrivano batb/batl come [livello, prezzo, quantità]:
    quantità 0 = livello rimosso. img=true sostituisce l'intero runner o mercato."""

    def __init__(self):
        self.markets: dict[str, dict] = {}
        self.updated = 0.0

    def apply(self, msg: dict) -> None:
        if msg.get("op") != "mcm":
            return
        for mc in msg.get("mc", []) or []:
            mid = mc["id"]
            m = self.markets.setdefault(mid, {"runners": {}, "definition": {}})
            if mc.get("img"):
                m["runners"] = {}
            if mc.get("marketDefinition"):
                m["definition"] = mc["marketDefinition"]
            for rc in mc.get("rc", []) or []:
                r = m["runners"].setdefault(rc["id"], {"batb": {}, "batl": {}, "ltp": None})
                if rc.get("img"):
                    r.update(batb={}, batl={})
                for side in ("batb", "batl"):
                    for level, price, size in rc.get(side, []) or []:
                        if size == 0:
                            r[side].pop(level, None)
                        else:
                            r[side][level] = (price, size)
                if "ltp" in rc:
                    r["ltp"] = rc["ltp"]
        self.updated = time.time()

    def best(self, market_id: str) -> dict:
        m = self.markets.get(market_id) or {"runners": {}}
        out = {}
        for rid, r in m["runners"].items():
            b = [r["batb"][k] for k in sorted(r["batb"])]
            l = [r["batl"][k] for k in sorted(r["batl"])]
            out[str(rid)] = {"back": b[0][0] if b else None, "lay": l[0][0] if l else None,
                             "back_size": sum(s for _, s in b), "lay_size": sum(s for _, s in l), "ltp": r["ltp"]}
        return out


async def stream_markets(client: BetfairClient, market_ids: list[str], cache: MarketCache,
                         stop: asyncio.Event | None = None) -> None:
    """Connessione SSL allo Stream API; aggiorna `cache` finché `stop` non è impostato.
    Autenticazione entro 15 s dall'apertura, heartbeat del server ogni 5 s (conflateMs 0)."""
    await asyncio.to_thread(client.ensure_session)
    reader, writer = await asyncio.open_connection(STREAM_HOST, STREAM_PORT, ssl=ssl.create_default_context())

    async def send(obj: dict) -> None:
        writer.write((json.dumps(obj) + "\r\n").encode())
        await writer.drain()

    try:
        await send({"op": "authentication", "id": 1, "appKey": client.app_key, "session": client.token})
        await send({"op": "marketSubscription", "id": 2, "heartbeatMs": 5000,
                    "marketFilter": {"marketIds": market_ids},
                    "marketDataFilter": {"fields": ["EX_BEST_OFFERS", "EX_LTP", "EX_MARKET_DEF"], "ladderLevels": 3}})
        while not (stop and stop.is_set()):
            line = await asyncio.wait_for(reader.readline(), timeout=30)
            if not line:
                raise FeedError("Stream Betfair chiuso dal server")
            msg = json.loads(line)
            if msg.get("op") == "status" and msg.get("statusCode") == "FAILURE":
                raise FeedError(f"Stream Betfair: {msg.get('errorCode')} {msg.get('errorMessage')}")
            cache.apply(msg)
    finally:
        writer.close()


def _wom(back_size: float, lay_size: float) -> float:
    """Weight of money: quota di denaro in attesa sul lato back (chi vuole puntare) sul totale."""
    tot = (back_size or 0) + (lay_size or 0)
    return (back_size or 0) / tot if tot else 0.5


class BetfairFeed(Feed):
    """Corse di cavalli (WIN, GB/IE) e calcio (MATCH_ODDS) dall'exchange.

    Modalità REST (predefinita): catalogo ogni 10 minuti, prezzi a ogni ciclo.
    Modalità stream (feed.betfair.stream: true): i prezzi dei mercati in catalogo
    arrivano in tempo reale dallo Stream API; il ciclo legge la cache.
    """
    name = "betfair"

    def __init__(self, settings: dict, creds: dict | None = None):
        super().__init__(settings)
        from .. import local_settings
        self.cfg = settings["feed"].get("betfair", {})
        self.client = BetfairClient(creds or local_settings.load()["betfair"])
        self.cat: dict[str, dict] = {}
        self.cat_ts = 0.0
        self.cache = MarketCache()
        self.stream_task: asyncio.Task | None = None
        self.stream_ids: tuple = ()

    def _refresh_catalogue(self) -> None:
        if time.time() - self.cat_ts < 600 and self.cat:
            return
        cat = {}
        for m in self.client.catalogue(HORSE_RACING, "WIN", self.cfg.get("race_hours", 2),
                                       self.cfg.get("race_countries", ["GB", "IE"]), 20):
            cat[m["marketId"]] = {**m, "_kind": "race"}
        if self.cfg.get("soccer", True):
            for m in self.client.catalogue(SOCCER, "MATCH_ODDS", self.cfg.get("soccer_hours", 36), None, 40):
                cat[m["marketId"]] = {**m, "_kind": "soccer"}
        self.cat, self.cat_ts = cat, time.time()

    def _prices_rest(self) -> dict[str, dict]:
        out = {}
        for b in self.client.books(list(self.cat)):
            rs = {}
            for r in b.get("runners", []):
                ex = r.get("ex", {})
                atb, atl = ex.get("availableToBack", []), ex.get("availableToLay", [])
                rs[str(r["selectionId"])] = {"back": atb[0]["price"] if atb else None, "lay": atl[0]["price"] if atl else None,
                                            "back_size": sum(x["size"] for x in atb), "lay_size": sum(x["size"] for x in atl),
                                            "ltp": r.get("lastPriceTraded"), "status": r.get("status")}
            out[b["marketId"]] = {"status": b.get("status"), "inplay": b.get("inplay"), "runners": rs}
        return out

    def _snapshot(self, prices: dict[str, dict]) -> dict:
        now = time.time()
        matches, races = {}, {}
        for mid, cat in self.cat.items():
            p = prices.get(mid)
            if not p:
                continue
            start = datetime.fromisoformat(cat["marketStartTime"].replace("Z", "+00:00"))
            names = {str(r["selectionId"]): r["runnerName"] for r in cat.get("runners", [])}
            if cat["_kind"] == "race":
                status = "CLOSED" if p["status"] == "CLOSED" else "INPLAY" if p.get("inplay") else "OPEN"
                races[mid] = {"market_id": mid, "venue": cat["event"].get("venue") or cat["event"]["name"],
                              "race": cat.get("marketName", ""), "start": start.isoformat(), "status": status,
                              "seconds_to_off": start.timestamp() - now, "winner": None,
                              "runners": {rid: {"name": names.get(rid, rid), **v, "wom": _wom(v["back_size"], v["lay_size"])}
                                          for rid, v in p["runners"].items() if v.get("back") and v.get("lay")}}
            else:
                runners = cat.get("runners", [])
                home, _, away = cat["event"]["name"].partition(" v ")
                if len(runners) != 3 or not away:
                    continue
                order = {}
                for r in runners:                      # abbinamento per nome, non per posizione
                    name = r["runnerName"].strip()
                    order[str(r["selectionId"])] = ("draw" if name.lower() in ("the draw", "pareggio", "draw")
                                                    else "home" if name == home.strip() else "away" if name == away.strip() else None)
                if sorted(v for v in order.values() if v) != ["away", "draw", "home"]:
                    continue                           # nomi non riconosciuti: meglio saltare che invertire casa e ospite
                prices_ = {order[rid]: v["back"] for rid, v in p["runners"].items() if order.get(rid) and v.get("back")}
                if len(prices_) < 3:
                    continue
                live = bool(p.get("inplay"))
                matches[mid] = {"match_id": mid, "sport": "soccer", "league": (cat.get("competition") or {}).get("name", "Calcio"),
                                "home": home.strip(), "away": away.strip(), "kickoff": start.isoformat(),
                                "status": "FINISHED" if p["status"] == "CLOSED" else "LIVE" if live else "SCHEDULED",
                                "minute": None, "home_score": None, "away_score": None, "result": None,
                                "books": {} if live else {"Betfair": prices_}, "live_books": {"Betfair": prices_} if live else {},
                                "closing": None, "odds_ts": now, "betfair": {"market_id": mid, "selection_ids":
                                                                            {v: int(k) for k, v in order.items()}}}
        return {"ts": now, "sim_time": now, "time_scale": 1.0,
                "health": {"error_rate": self.client.errors / max(1, self.client.calls),
                           "source": "Betfair Exchange Italia" + (" (stream)" if self.stream_task else "")},
                "matches": matches, "races": races}

    async def fetch(self) -> dict:
        try:
            await asyncio.to_thread(self._refresh_catalogue)
            if self.cfg.get("stream"):
                ids = tuple(sorted(self.cat))
                if self.stream_task is None or self.stream_task.done() or ids != self.stream_ids:
                    if self.stream_task and not self.stream_task.done():
                        self.stream_task.cancel()
                    self.stream_ids = ids
                    self.stream_task = asyncio.create_task(stream_markets(self.client, list(ids), self.cache))
                if time.time() - self.cache.updated > 30:
                    prices = await asyncio.to_thread(self._prices_rest)       # stream non ancora pronto: REST
                else:
                    prices = {mid: {"status": (self.cache.markets[mid]["definition"] or {}).get("status", "OPEN"),
                                    "inplay": (self.cache.markets[mid]["definition"] or {}).get("inPlay", False),
                                    "runners": self.cache.best(mid)} for mid in ids if mid in self.cache.markets}
            else:
                prices = await asyncio.to_thread(self._prices_rest)
        except BetfairError as exc:
            raise FeedError(str(exc)) from exc
        return self._snapshot(prices)
