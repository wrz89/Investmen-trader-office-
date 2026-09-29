"""Collegamento REALE a Bybit EU, usato SOLO dal piano di accumulo.

Può soltanto COMPRARE a pronti le monete del piano (config/accumulation.yaml).
In questo file non esiste codice per vendere, prelevare, trasferire fondi,
usare derivati o leva: anche se la chiave lo permettesse, l'ufficio non saprebbe farlo.

La chiave si accetta solo se:
  • ha il permesso di trading spot;
  • NON ha il permesso di prelievo;
  • è legata a un indirizzo IP (quello del tuo PC).
Chiave e segreto stanno in runtime/local_settings.json, sul tuo PC: mai su GitHub.
"""
from __future__ import annotations

from . import local_settings


class LiveError(Exception):
    pass


def parse_permissions(resp: dict) -> dict:
    """Legge la risposta di Bybit /v5/user/query-api."""
    r = resp.get("result", resp) or {}
    perms = r.get("permissions") or {}
    flat = [str(p) for v in perms.values() for p in (v or [])]
    withdraw = bool(perms.get("Withdraw")) or any("withdraw" in p.lower() for p in flat)
    spot = any(p in ("SpotTrade", "Spot") for p in flat) or bool(perms.get("Spot"))
    derivatives = any(bool(perms.get(k)) for k in ("ContractTrade", "Options", "Derivatives"))
    ips = [ip for ip in (r.get("ips") or []) if ip]
    transfer = "AccountTransfer" in flat                  # solo tra i TUOI conti Bybit (Fondi → Trading)
    return {"read_only": str(r.get("readOnly")) == "1", "spot_trade": spot, "withdraw": withdraw,
            "derivatives": derivatives, "ips": ips, "ip_bound": bool(ips) and ips != ["*"], "transfer": transfer}


def key_problems(p: dict) -> list[str]:
    problems = []
    if p["withdraw"]:
        problems.append("la chiave può PRELEVARE: cancellala su Bybit e creane una senza il permesso di prelievo")
    if p["read_only"] or not p["spot_trade"]:
        problems.append("manca il permesso di trading Spot (serve 'Lettura e scrittura' con solo 'Spot - Trade')")
    if not p["ip_bound"]:
        problems.append("la chiave non è legata a un IP: impostala su 'Solo IP consentiti' con l'IP del tuo PC")
    return problems


class LiveExchange:
    def __init__(self, settings: dict, allowed: list[str], factory=None):
        self.allowed = set(allowed)
        creds = (local_settings.load().get("bybit") or {})
        self.key, self.secret = creds.get("key") or "", creds.get("secret") or ""
        self.hostname = settings["exchange"].get("live_hostname") or "bybit.eu"
        self._factory = factory
        self._ex = None

    @property
    def configured(self) -> bool:
        return bool(self.key and self.secret)

    @property
    def ex(self):
        if self._ex is None:
            if not self.configured:
                raise LiveError("chiave API di Bybit non inserita (Impostazioni)")
            if self._factory:
                self._ex = self._factory(self.key, self.secret, self.hostname)
            else:
                import ccxt
                self._ex = ccxt.bybit({"apiKey": self.key, "secret": self.secret, "enableRateLimit": True,
                                       "timeout": 15000, "requests_trust_env": True, "hostname": self.hostname,
                                       "options": {"defaultType": "spot", "adjustForTimeDifference": True,
                                                   "recvWindow": 10000}})
                # Bybit rifiuta le richieste con l'orario del PC anche solo 1 secondo avanti (errore 10002):
                # si misura la differenza con l'orologio di Bybit e la si compensa.
                try:
                    self._ex.load_time_difference()
                except Exception:
                    pass
        return self._ex

    # ── controlli ───────────────────────────────────────────
    def verify(self) -> dict:
        p = parse_permissions(self.ex.privateGetV5UserQueryApi())
        problems = key_problems(p)
        return {**p, "problems": problems, "ok": not problems, "eur_free": self.eur_free(),
                "eur_funding": self.funding_eur()}

    def eur_free(self) -> float:
        bal = self.ex.fetch_balance()
        return float((bal.get("free") or {}).get("EUR") or 0.0)

    def funding_eur(self) -> float:
        try:
            bal = self.ex.fetch_balance({"type": "funding"})
            return float((bal.get("free") or {}).get("EUR") or 0.0)
        except Exception:
            return 0.0

    def ensure_eur(self, eur: float) -> float:
        """Euro disponibili per comprare. Se nel conto di trading non bastano e la chiave ha il permesso
        'trasferimento tra conti', sposta SOLO la differenza dal conto Fondi (sempre dentro il tuo Bybit)."""
        free = self.eur_free()
        if free >= eur:
            return free
        missing = round(eur - free + 0.01, 2)
        if not (0 < missing <= 1000) or self.funding_eur() < missing:
            return free
        try:
            self.ex.transfer("EUR", missing, "funding", "unified")
        except Exception:
            return free                                   # permesso assente: si avvisa l'utente
        return self.eur_free()

    def _check(self, symbol: str, eur: float) -> None:
        if symbol not in self.allowed:
            raise LiveError(f"{symbol} non è nel piano di accumulo: ordine rifiutato")
        if not (0 < eur <= 1000):
            raise LiveError(f"importo {eur} € fuori dai limiti di sicurezza")

    # ── solo acquisti ───────────────────────────────────────
    def limit_buy(self, symbol: str, eur: float, price: float) -> str:
        """Ordine limite post-only (commissione maker): non viene mai eseguito come taker."""
        self._check(symbol, eur)
        ex = self.ex
        amount = float(ex.amount_to_precision(symbol, eur / price))
        px = float(ex.price_to_precision(symbol, price))
        if amount * px > eur * 1.001:
            raise LiveError("arrotondamento oltre l'importo previsto")
        return ex.create_order(symbol, "limit", "buy", amount, px, {"postOnly": True})["id"]

    def market_buy(self, symbol: str, eur: float) -> str:
        """Acquisto a mercato di un importo in euro (non in quantità)."""
        self._check(symbol, eur)
        return self.ex.create_market_buy_order_with_cost(symbol, eur)["id"]

    def order(self, symbol: str, order_id: str) -> dict:
        return self.ex.fetch_order(order_id, symbol, {"acknowledged": True})

    def cancel(self, symbol: str, order_id: str) -> None:
        self.ex.cancel_order(order_id, symbol)


def fill_of(order: dict, symbol: str) -> dict:
    """Quantità netta ricevuta, euro spesi e commissione in euro da un ordine (anche parziale)."""
    base, quote = symbol.split("/")
    filled, cost = float(order.get("filled") or 0), float(order.get("cost") or 0)
    fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
    fee_base = sum(float(f.get("cost") or 0) for f in fees if f.get("currency") == base)
    fee_quote = sum(float(f.get("cost") or 0) for f in fees if f.get("currency") == quote)
    avg = float(order.get("average") or (cost / filled if filled else 0))
    return {"qty": max(0.0, filled - fee_base), "eur": cost + fee_quote,
            "fee_eur": fee_quote + fee_base * avg, "avg": avg}
