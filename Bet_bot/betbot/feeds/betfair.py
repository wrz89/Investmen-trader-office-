"""Betfair Exchange (Italia) — client REST JSON-RPC, Stream API e feed.

Perché Betfair: è l'unico operatore con concessione ADM che offre un'API
ufficiale per leggere quote E piazzare puntate. I bookmaker a quota fissa
italiani (Sisal, Snai, Eurobet, Bet365…) non hanno API pubbliche: automatizzarne
il sito viola i termini d'uso e porta alla chiusura del conto. L'ufficio non lo fa.

Regole dell'exchange italiano (documentazione Betfair, verificate il 29/09/2026):
  • pool di liquidità SEPARATO da quello internazionale (regulator MR_IT): prezzi e volumi solo da qui;
  • niente ippica: le corse di cavalli non sono quotate su betfair.it;
  • back minimo 2 € a multipli di 0,50 €; lay pari a una puntata back di almeno 0,50 €;
  • back e lay nella stessa richiesta placeOrders vengono rifiutati: si mandano separati;
  • commissione 4,5% sulla vincita netta di mercato (campo marketBaseRate di ogni mercato);
  • la sessione scade dopo 20 minuti anche se si usa l'API: keepAlive ogni 10 minuti;
  • app key "delayed" gratuita (prezzi in ritardo 1-180 s, può comunque puntare); app key "live" senza
    costo per i conti italiani, dopo la verifica del conto.

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

HORSE_RACING, SOCCER, TENNIS, BASKETBALL = "7", "1", "2", "7522"
# sport → (eventTypeId Betfair, tipo di mercato, esiti attesi)
SPORTS = {"soccer": (SOCCER, "MATCH_ODDS", 3), "tennis": (TENNIS, "MATCH_ODDS", 2), "basketball": (BASKETBALL, "MATCH_ODDS", 2)}


def split_event_name(name: str) -> tuple[str, str] | None:
    """"Inter v Lecce" → (Inter, Lecce); "Boston Celtics @ Miami Heat" (sport USA: ospite @ casa) → (Miami Heat, Boston Celtics)."""
    if " v " in name:
        home, _, away = name.partition(" v ")
        return home.strip(), away.strip()
    if " @ " in name:
        away, _, home = name.partition(" @ ")
        return home.strip(), away.strip()
    return None


class BetfairError(Exception):
    pass


INVALID_APP_KEY_MSG = "app key non valida o non ancora attiva (se l'hai appena creata, Betfair impiega 1-3 minuti)"


def is_invalid_app_key(detail) -> bool:
    d = str(detail or "")
    return "AANGX-0004" in d or "INVALID_APP_KEY" in d


class RequestNotSent(BetfairError):
    """La richiesta NON è partita (login rimandato, credenziali mancanti, sessione assente): per un ordine
    vuol dire "sicuramente non piazzato", non "esito sconosciuto"."""


class BetfairClient:
    """Chiamate sincrone (requests). Il feed le esegue in un thread per non bloccare il loop async."""

    SESSION_TTL = 600                 # betfair.it: la sessione scade dopo 20 minuti → keepAlive ogni 10
    LOGIN_ERRORS = {
        "ITALIAN_CONTRACT_ACCEPTANCE_REQUIRED": "Betfair chiede di accettare le nuove condizioni del contratto: entra su "
                                                "betfair.it dal browser, accetta, poi riavvia Bet_bot.",
        "ITALIAN_PROFILING_ACCEPTANCE_REQUIRED": "Betfair chiede di completare il questionario di profilazione: entra su "
                                                 "betfair.it dal browser, completalo, poi riavvia Bet_bot.",
        "INVALID_USERNAME_OR_PASSWORD": "utente o password Betfair sbagliati.",
        "ACCOUNT_NOW_LOCKED": "conto Betfair bloccato per troppi tentativi: sbloccalo dal sito.",
        "TEMPORARY_BAN_TOO_MANY_REQUESTS": "troppi login in poco tempo: Betfair blocca per 20 minuti.",
        "CERT_AUTH_REQUIRED": "serve il login con certificato: imposta cert_file e key_file.",
        "INVALID_APP_KEY": "app key non valida o non ancora attiva (se l'hai appena creata, Betfair impiega 1-3 minuti).",
    }

    def __init__(self, creds: dict):
        self.app_key = creds.get("app_key") or ""
        self.username = creds.get("username") or ""
        self.password = creds.get("password") or ""
        # percorsi incollati da Esplora risorse ("Copia come percorso") arrivano tra virgolette: si tolgono
        cert, key = ((creds.get(k) or "").strip().strip('"').strip() for k in ("cert_file", "key_file"))
        self.cert = (cert, key) if cert else None
        self.token: str | None = None
        self.token_ts = 0.0
        self.calls = 0
        self.errors = 0
        self.last_login_try = 0.0
        self.http = requests.Session()

    # ── sessione ────────────────────────────────────────────────
    def login(self) -> str:
        if not (self.app_key and self.username and self.password):
            raise BetfairError("Credenziali Betfair incomplete (app key, utente, password).")
        if time.time() - self.last_login_try < 60:
            raise BetfairError("Login Betfair rimandato di un minuto (protezione contro il blocco per troppi tentativi).")
        self.last_login_try = time.time()
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
        except OSError as exc:              # es. "Could not find the TLS certificate file": percorso del certificato
            raise BetfairError(f"Login Betfair non riuscito: certificato non leggibile ({exc}). Controlla i percorsi "
                               f"del certificato nelle impostazioni Betfair della dashboard: "
                               f"{self.cert[0] if self.cert else '—'}, {self.cert[1] if self.cert else '—'}.") from exc
        if not ok:
            raise BetfairError(f"Login Betfair rifiutato: {self.LOGIN_ERRORS.get(err, err)}")
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

    def _headers(self, with_app_key: bool = True) -> dict:
        """with_app_key=False solo per getDeveloperAppKeys/createDeveloperAppKeys: Betfair non la richiede e, con una
        chiave segnaposto o non ancora attiva, la rifiuterebbe (AANGX-0004)."""
        h = {"X-Authentication": self.token or "", "Content-Type": "application/json", "Accept": "application/json"}
        if with_app_key:
            h["X-Application"] = self.app_key
        return h

    READ_METHODS = {"listMarketCatalogue", "listMarketBook", "listEventTypes", "listCurrentOrders", "listClearedOrders",
                    "getAccountFunds"}

    def rpc(self, method: str, params: dict, url: str = BETTING, service: str = "SportsAPING/v1.0",
            _retry: bool = True, with_app_key: bool = True) -> dict | list:
        """Chiamata JSON-RPC. Le LETTURE si ripetono una volta dopo un nuovo login se la sessione è scaduta;
        gli ORDINI mai alla cieca: chi li manda verifica con listCurrentOrders."""
        try:
            return self._rpc(method, params, url, service, with_app_key)
        except BetfairError as exc:
            if _retry and method in self.READ_METHODS and "SESSION" in str(exc):
                self.token = None
                return self._rpc(method, params, url, service, with_app_key)
            raise

    def start_keepalive(self) -> None:
        """Thread che rinnova la sessione ogni 10 minuti anche quando il bot non fa chiamate (stream, notte)."""
        import threading
        if getattr(self, "_ka", None):
            return

        def loop():
            while True:
                time.sleep(self.SESSION_TTL)
                try:
                    if self.token:
                        self.ensure_session()
                except Exception:
                    pass
        self._ka = threading.Thread(target=loop, name="betfair-keepalive", daemon=True)
        self._ka.start()

    def _rpc(self, method: str, params: dict, url: str, service: str, with_app_key: bool = True) -> dict | list:
        try:
            self.ensure_session()
        except BetfairError as exc:                         # nessuna chiamata è ancora partita verso Betfair
            raise RequestNotSent(str(exc)) from exc
        if not self.token:
            raise RequestNotSent("sessione Betfair assente")
        self.calls += 1
        payload = {"jsonrpc": "2.0", "method": f"{service}/{method}", "params": params, "id": 1}
        try:
            r = self.http.post(url, data=json.dumps(payload), headers=self._headers(with_app_key), timeout=15)
            body = r.json()
        except (requests.RequestException, ValueError) as exc:
            self.errors += 1
            raise BetfairError(f"Betfair non raggiungibile: {exc}") from exc
        if "error" in body:
            self.errors += 1
            err = body["error"]
            data = err.get("data") or {}
            detail = ((data.get("APINGException") or data.get("AccountAPINGException") or {}).get("errorCode")
                      or err.get("message"))
            if detail in ("INVALID_SESSION_INFORMATION", "NO_SESSION"):
                self.token = None
            if is_invalid_app_key(detail):
                raise BetfairError(f"Betfair {method}: {INVALID_APP_KEY_MSG}")
            raise BetfairError(f"Betfair {method}: {detail}")
        return body["result"]

    # ── chiavi dell'applicazione (senza X-Application, come da documentazione Betfair) ────────
    def developer_app_keys(self) -> list:
        return self.rpc("getDeveloperAppKeys", {}, ACCOUNT, "AccountAPING/v1.0", with_app_key=False)

    def create_developer_app_keys(self, app_name: str) -> dict:
        return self.rpc("createDeveloperAppKeys", {"appName": app_name}, ACCOUNT, "AccountAPING/v1.0", with_app_key=False)

    # ── lettura ─────────────────────────────────────────────────
    def account_funds(self) -> dict:
        return self.rpc("getAccountFunds", {}, ACCOUNT, "AccountAPING/v1.0")

    def catalogue(self, event_type: str, market_type: str, hours: float, countries: list[str] | None = None,
                  max_results: int = 30, lookback_hours: float = 4.0) -> list[dict]:
        """Mercati da `lookback_hours` fa (partite già iniziate: in-play e posizioni aperte) fino a `hours` avanti."""
        now = datetime.now(timezone.utc)
        mfilter = {"eventTypeIds": [event_type], "marketTypeCodes": [market_type],
                   "marketStartTime": {"from": (now - timedelta(hours=lookback_hours)).isoformat(timespec="seconds").replace("+00:00", "Z"),
                                       "to": (now + timedelta(hours=hours)).isoformat(timespec="seconds").replace("+00:00", "Z")}}
        if countries:
            mfilter["marketCountries"] = countries
        return self.rpc("listMarketCatalogue", {"filter": mfilter, "maxResults": max_results, "sort": "FIRST_TO_START",
                                                "marketProjection": ["EVENT", "RUNNER_DESCRIPTION", "MARKET_START_TIME",
                                                                     "COMPETITION", "MARKET_DESCRIPTION"]})

    def event_types(self) -> dict[str, str]:
        """Sport disponibili per questo conto: {id: nome}. Su betfair.it l'ippica (7) non deve esserci."""
        res = self.rpc("listEventTypes", {"filter": {}})
        return {str(e["eventType"]["id"]): e["eventType"]["name"] for e in res}

    def books(self, market_ids: list[str]) -> list[dict]:
        out = []
        for i in range(0, len(market_ids), 10):          # limite di peso delle richieste: 10 mercati per volta
            out += self.rpc("listMarketBook", {"marketIds": market_ids[i:i + 10],
                                               "priceProjection": {"priceData": ["EX_BEST_OFFERS"],
                                                                   "exBestOffersOverrides": {"bestPricesDepth": 3}}})
        return out

    # ── ordini ─────────────────────────────────────────────────
    def place(self, market_id: str, selection_id: int, side: str, price: float, size: float,
              fill_or_kill: bool = True, ref: str | None = None, order_ref: str | None = None,
              strategy_ref: str | None = None) -> dict:
        """Ordine LIMIT. Con fill_or_kill l'ordine è abbinato subito per intero o annullato:
        niente ordini "appesi" che si abbinano quando il prezzo non conviene più.
        order_ref (customerOrderRef, univoco) permette di ritrovare l'ordine se la risposta si perde."""
        import uuid
        order = {"selectionId": int(selection_id), "side": side, "orderType": "LIMIT",
                 "limitOrder": {"size": round(size, 2), "price": price, "persistenceType": "LAPSE"}}
        if fill_or_kill:
            order["limitOrder"]["timeInForce"] = "FILL_OR_KILL"
        if order_ref:
            order["customerOrderRef"] = order_ref[:32]
        params = {"marketId": market_id, "instructions": [order], "customerRef": uuid.uuid4().hex[:32]}
        if strategy_ref:
            params["customerStrategyRef"] = strategy_ref[:15]
        res = self.rpc("placeOrders", params)
        if res.get("status") != "SUCCESS":
            raise BetfairError(f"placeOrders: {res.get('errorCode')} "
                               f"{[i.get('errorCode') for i in res.get('instructionReports', [])]}")
        rep = res["instructionReports"][0]
        return {"bet_id": rep.get("betId"), "matched": float(rep.get("sizeMatched") or 0.0),
                "avg_price": float(rep.get("averagePriceMatched") or 0.0), "status": rep.get("orderStatus")}


    def cancel(self, market_id: str | None = None, bet_id: str | None = None) -> dict:
        """Annulla gli ordini non abbinati: di un mercato, di una puntata, oppure TUTTI (senza argomenti)."""
        params = {}
        if market_id:
            params["marketId"] = market_id
        if bet_id:
            params["instructions"] = [{"betId": bet_id}]
        return self.rpc("cancelOrders", params)

    def current_orders(self, order_refs: list[str] | None = None, market_ids: list[str] | None = None) -> list[dict]:
        """Ordini ancora in corso (abbinati ma non regolati, o non abbinati)."""
        params: dict = {"orderProjection": "ALL"}
        if order_refs:
            params["customerOrderRefs"] = order_refs
        if market_ids:
            params["marketIds"] = market_ids
        return self.rpc("listCurrentOrders", params).get("currentOrders", [])

    def cleared_by_refs(self, order_refs: list[str]) -> list[dict]:
        """Ordini già regolati o annullati cercati per customerOrderRef (listCurrentOrders non li mostra più):
        [{"betId", "customerOrderRef", "status", "sizeSettled", "priceMatched", "profit"}]."""
        out = []
        for status in ("SETTLED", "VOIDED", "LAPSED", "CANCELLED"):
            res = self.rpc("listClearedOrders", {"betStatus": status, "customerOrderRefs": order_refs,
                                                 "includeItemDescription": False})
            out += [{**o, "status": status} for o in res.get("clearedOrders", [])]
        return out

    def cleared(self, bet_ids: list[str]) -> dict[str, dict]:
        """Esito delle puntate chiuse: {bet_id: {"profit" (lordo), "outcome", "status"}}.
        status: SETTLED (regolata), VOIDED (annullata: rimborso), LAPSED/CANCELLED (mai abbinata: rimborso)."""
        out: dict[str, dict] = {}
        for status in ("SETTLED", "VOIDED", "LAPSED", "CANCELLED"):
            res = self.rpc("listClearedOrders", {"betStatus": status, "betIds": bet_ids, "includeItemDescription": False})
            for o in res.get("clearedOrders", []):
                out[o["betId"]] = {"profit": float(o.get("profit", 0.0)), "outcome": o.get("betOutcome"), "status": status}
        return out


# stati di un runner a mercato regolato che NON sono il vincitore
SETTLED_LOSER = ("LOSER", "REMOVED", "REMOVED_VACANT")


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
        """Miglior prezzo per runner, con le stesse chiavi della lettura REST: anche il denaro AL miglior prezzo
        (per il fill-or-kill) e lo stato del runner dalla marketDefinition (WINNER / LOSER / REMOVED)."""
        m = self.markets.get(market_id) or {"runners": {}, "definition": {}}
        status = {str(r.get("id")): r.get("status") for r in (m.get("definition") or {}).get("runners") or []}
        runners = {str(k): v for k, v in m["runners"].items()}
        for rid in status:                                   # a mercato chiuso un runner può non avere prezzi
            runners.setdefault(rid, {"batb": {}, "batl": {}, "ltp": None})
        out = {}
        for rid, r in runners.items():
            b = [r["batb"][k] for k in sorted(r["batb"])]
            l = [r["batl"][k] for k in sorted(r["batl"])]
            out[rid] = {"back": b[0][0] if b else None, "lay": l[0][0] if l else None,
                        "back_size": sum(s for _, s in b), "lay_size": sum(s for _, s in l),
                        "back_size_best": b[0][1] if b else 0.0, "lay_size_best": l[0][1] if l else 0.0,
                        "ltp": r["ltp"], "status": status.get(rid)}
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
        self.available: dict | None = None
        self.watch_ids: set = set()
        self.saved_cat: dict[str, dict] = {}
        self.data_delayed = True             # finché una lettura REST non dice il contrario: prudenza per il live

    def watch(self, market_ids, saved: dict | None = None) -> None:
        """Mercati con posizioni aperte (vere, paper o in ombra): restano letti anche dopo l'inizio e fino alla
        chiusura, così le puntate si regolano sempre. `saved` = voci di catalogo salvate nel database (dopo un
        riavvio listMarketCatalogue non restituisce più i mercati iniziati da ore o già chiusi)."""
        self.watch_ids = {m for m in market_ids if str(m).startswith("1.")}
        self.saved_cat = {k: v for k, v in (saved or {}).items() if k in self.watch_ids}

    def _refresh_catalogue(self) -> None:
        if time.time() - self.cat_ts < 600 and self.cat:
            return
        old = self.cat
        cat = {}
        if not self.available or HORSE_RACING in self.available:       # su betfair.it l'ippica non c'è
            for m in self.client.catalogue(HORSE_RACING, "WIN", self.cfg.get("race_hours", 2),
                                           self.cfg.get("race_countries", ["GB", "IE"]), 20):
                cat[m["marketId"]] = {**m, "_kind": "race"}
        if self.available is None:
            try:
                self.available = self.client.event_types()
            except BetfairError:
                self.available = {}
        sports = self.cfg.get("sports") or (["soccer"] if self.cfg.get("soccer", True) else [])
        sports = [s for s in sports if not self.available or SPORTS[s][0] in self.available]
        for sport in sports:
            event_type, market_type, _ = SPORTS[sport]
            for m in self.client.catalogue(event_type, market_type, self.cfg.get("soccer_hours", 36), None, 40):
                cat[m["marketId"]] = {**m, "_kind": sport}
        saved = getattr(self, "saved_cat", {})
        for mid in getattr(self, "watch_ids", set()):         # le partite con posizioni aperte non si dimenticano
            if mid not in cat and (mid in old or mid in saved):
                cat[mid] = old.get(mid) or saved[mid]
        self.cat, self.cat_ts = cat, time.time()

    def _prices_rest(self) -> dict[str, dict]:
        out = {}
        ids = list(dict.fromkeys(list(self.cat) + sorted(getattr(self, "watch_ids", set()))))
        books = self.client.books(ids)
        if books:
            self.data_delayed = any(bool(b.get("isMarketDataDelayed")) for b in books)
        for b in books:
            rs = {}
            for r in b.get("runners", []):
                ex = r.get("ex", {})
                atb, atl = ex.get("availableToBack", []), ex.get("availableToLay", [])
                rs[str(r["selectionId"])] = {"back": atb[0]["price"] if atb else None, "lay": atl[0]["price"] if atl else None,
                                            "back_size": sum(x["size"] for x in atb), "lay_size": sum(x["size"] for x in atl),
                                            # per il fill-or-kill conta solo il denaro AL miglior prezzo
                                            "back_size_best": atb[0]["size"] if atb else 0.0,
                                            "lay_size_best": atl[0]["size"] if atl else 0.0,
                                            "ltp": r.get("lastPriceTraded"), "status": r.get("status")}
            out[b["marketId"]] = {"status": b.get("status"), "inplay": b.get("inplay"), "runners": rs,
                                  "delayed": bool(b.get("isMarketDataDelayed"))}
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
                winner = next((rid for rid, v in p["runners"].items() if v.get("status") == "WINNER"), None)
                races[mid] = {"market_id": mid, "venue": cat["event"].get("venue") or cat["event"]["name"],
                              "race": cat.get("marketName", ""), "start": start.isoformat(), "status": status,
                              "seconds_to_off": start.timestamp() - now, "winner": winner,
                              "runners": {rid: {"name": names.get(rid, rid), **v, "wom": _wom(v["back_size"], v["lay_size"])}
                                          for rid, v in p["runners"].items() if (v.get("back") and v.get("lay")) or status == "CLOSED"}}
            else:
                runners = cat.get("runners", [])
                sport = cat["_kind"]
                n_out = SPORTS.get(sport, (None, None, 3))[2]
                names = split_event_name(cat["event"]["name"])
                if len(runners) != n_out or not names:
                    continue
                home, away = names
                order = {}
                for r in runners:                      # abbinamento per nome, non per posizione
                    name = r["runnerName"].strip()
                    order[str(r["selectionId"])] = ("draw" if name.lower() in ("the draw", "pareggio", "draw")
                                                    else "home" if name == home.strip() else "away" if name == away.strip() else None)
                expected = ["away", "draw", "home"] if n_out == 3 else ["away", "home"]
                if sorted(v for v in order.values() if v) != expected:
                    continue                           # nomi non riconosciuti: meglio saltare che invertire casa e ospite
                closed = p["status"] == "CLOSED"
                result = next((order[rid] for rid, v in p["runners"].items() if order.get(rid) and v.get("status") == "WINNER"), None)
                prices_ = {order[rid]: v["back"] for rid, v in p["runners"].items() if order.get(rid) and v.get("back")}
                if len(prices_) < n_out and not closed:
                    continue
                # solo le chiavi presenti: un None copiato qui varrebbe "0 € nel book" per l'exchange simulato
                exchange = {} if closed else {order[rid]: {k: v[k] for k in ("back", "lay", "back_size", "lay_size",
                                                                             "back_size_best", "lay_size_best")
                                                           if v.get(k) is not None}
                                              for rid, v in p["runners"].items() if order.get(rid)}
                # annullato solo se Betfair lo dice: mercato chiuso e OGNI runner con uno stato definitivo diverso da
                # WINNER. Uno stato mancante (o ancora ACTIVE) non basta: si aspetta, non si rimborsa.
                states = [(p["runners"].get(rid) or {}).get("status") for rid in order]
                void = closed and result is None and all(st in SETTLED_LOSER for st in states)
                live = bool(p.get("inplay"))
                label = {"soccer": "Calcio", "tennis": "Tennis", "basketball": "Basket"}.get(sport, sport)
                rate = (cat.get("description") or {}).get("marketBaseRate")
                matches[mid] = {"match_id": mid, "sport": sport, "league": (cat.get("competition") or {}).get("name", label),
                                "commission": rate / 100 if rate else None,
                                "home": home.strip(), "away": away.strip(), "kickoff": start.isoformat(),
                                "status": "FINISHED" if closed else "LIVE" if live else "SCHEDULED",
                                "minute": None, "home_score": None, "away_score": None,
                                # a mercato chiuso l'esito lo dice Betfair: il runner WINNER (annullato: vedi `void`)
                                "result": result if closed else None, "void": void,
                                # "books" restano vuoti: le quote di riferimento arrivano da un'altra fonte (feed.reference)
                                "books": {}, "live_books": {},
                                "exchange": exchange,
                                "closing": None, "odds_ts": now, "betfair": {"market_id": mid, "selection_ids":
                                                                            {v: int(k) for k, v in order.items()}}}
        delayed = any(v.get("delayed") for v in prices.values())
        return {"ts": now, "sim_time": now, "time_scale": 1.0,
                "health": {"error_rate": self.client.errors / max(1, self.client.calls), "delayed": delayed,
                           "source": "Betfair Exchange Italia" + (" (stream)" if self.stream_task else "")
                                     + (" · prezzi in ritardo (app key delayed)" if delayed else "")},
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
                    # lo stream non dice se i prezzi sono ritardati: vale l'ultima lettura REST (all'inizio: sì)
                    prices = {mid: {"status": (self.cache.markets[mid]["definition"] or {}).get("status", "OPEN"),
                                    "inplay": (self.cache.markets[mid]["definition"] or {}).get("inPlay", False),
                                    "runners": self.cache.best(mid), "delayed": self.data_delayed}
                              for mid in ids if mid in self.cache.markets}
            else:
                prices = await asyncio.to_thread(self._prices_rest)
        except BetfairError as exc:
            raise FeedError(str(exc)) from exc
        return self._snapshot(prices)
