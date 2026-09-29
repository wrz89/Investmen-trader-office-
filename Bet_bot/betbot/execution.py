"""Esecuzione su exchange: PAPER (exchange simulato) e BETFAIR (soldi veri, dietro cancelli).

Bet_bot opera SOLO dove può fare tutto da solo: Betfair Exchange. Chi decide COSA
puntare è sempre il Risk Manager; qui si decide solo se l'ordine va all'exchange
simulato o a quello vero, con le STESSE regole:

  • ordini LIMIT fill-or-kill: abbinati subito per intero al prezzo chiesto (o migliore) oppure annullati;
  • un BACK si abbina se la miglior quota "da puntare" è ≥ del prezzo chiesto e c'è abbastanza denaro;
  • un LAY si abbina se la miglior quota "da bancare" è ≤ del prezzo chiesto e c'è abbastanza denaro;
  • la commissione dell'exchange si paga sulla vincita netta del mercato.

L'exchange simulato è volutamente prudente: abbina solo al prezzo chiesto (mai migliore) e solo
se nel book c'è almeno `paper_liquidity_factor` volte la puntata, perché davanti a noi in coda
ci sono altri ordini e i prezzi delayed arrivano in ritardo.
"""
from __future__ import annotations

import json

from . import local_settings
from .feeds.mock import tick_up


BACK_STEP = 0.50        # betfair.it: puntate back da 2 € in su, solo a multipli di 0,50 €
LAY_MIN = 0.50          # betfair.it: il lay deve corrispondere a una puntata back di almeno 0,50 €


def round_back_stake(stake: float, min_stake: float = 2.0, step: float = BACK_STEP) -> float:
    """Arrotonda PER DIFETTO al multiplo di 0,50 €; sotto il minimo restituisce 0 (niente puntata)."""
    s = int(stake / step + 1e-9) * step
    return round(s, 2) if s >= min_stake - 1e-9 else 0.0


class Gates:
    """Controlla i cancelli per i soldi veri. Restituisce (aperto, motivo)."""

    @staticmethod
    def live_allowed(settings: dict, strategy_id: str) -> tuple[bool, str]:
        if settings.get("mode") != "live":
            return False, "modalità paper"
        if (settings.get("execution") or {}).get("provider") != "betfair":
            return False, "execution.provider non è betfair"
        bf = local_settings.load()["betfair"]
        if not bf.get("verified"):
            return False, "conto Betfair non verificato"
        if not bf.get("test_done"):
            return False, "ordine di prova Betfair non ancora fatto"
        if not bf.get("live_enabled"):
            return False, "interruttore 'Puntate reali' spento"
        if strategy_id not in (settings.get("live_strategies") or []):
            return False, f"{strategy_id} non è tra le live_strategies"
        return True, "ok"


def book_for(p: dict, snapshot: dict) -> dict | None:
    """Miglior prezzo e denaro disponibile per la selezione della proposta/puntata:
    {"back", "lay", "back_size", "lay_size"} oppure None se il mercato non è nel feed."""
    mid, sel = p.get("market_id") or p.get("match_id"), p.get("selection")
    race = (snapshot.get("races") or {}).get(mid)
    if race:
        return (race.get("runners") or {}).get(sel)
    m = (snapshot.get("matches") or {}).get(p.get("match_id"))
    if m and m.get("exchange"):
        return m["exchange"].get(sel)
    return None


def exchange_target(p: dict, snapshot: dict) -> tuple[str, int] | None:
    """Mercato e selezione Betfair reali (id numerici) oppure None (feed simulato)."""
    mid = p.get("market_id") or ""
    if mid.startswith("1.") and str(p.get("selection", "")).isdigit():
        return mid, int(p["selection"])
    m = (snapshot.get("matches") or {}).get(p.get("match_id")) or {}
    bf = m.get("betfair")
    if bf and p.get("selection") in bf.get("selection_ids", {}):
        return bf["market_id"], bf["selection_ids"][p["selection"]]
    return None


class PaperExchange:
    """Exchange simulato: stesse regole di abbinamento di Betfair, soldi finti."""

    def __init__(self, liquidity_factor: float = 1.5):
        self.liquidity_factor = liquidity_factor

    def place(self, side: str, price: float, size: float, book: dict | None) -> dict:
        if not book:
            return {"ok": False, "error": "mercato non disponibile nel feed"}
        need = size * self.liquidity_factor
        if side == "BACK":
            best, avail = book.get("back"), book.get("back_size") or 0.0
            if not best or best < price - 1e-9:
                return {"ok": False, "error": f"quota da puntare {best or '—'} sotto il prezzo chiesto {price:.2f}"}
        else:
            best, avail = book.get("lay"), book.get("lay_size") or 0.0
            if not best or best > price + 1e-9:
                return {"ok": False, "error": f"quota da bancare {best or '—'} sopra il prezzo chiesto {price:.2f}"}
        if avail < need:
            return {"ok": False, "error": f"liquidità insufficiente ({avail:.0f} € nel book, servono {need:.0f} €)"}
        return {"ok": True, "price": price, "matched": round(size, 2)}


class Executor:
    def __init__(self, settings: dict, client=None):
        self.settings = settings
        ex = settings.get("execution") or {}
        self.min_stake = float(ex.get("min_stake", 2.0))
        self.commission = float(ex.get("commission", 0.045))
        self.paper = PaperExchange(float(ex.get("paper_liquidity_factor", 1.5)))
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from .feeds.betfair import BetfairClient
            self._client = BetfairClient(local_settings.load()["betfair"])
        return self._client

    def route(self, p: dict, snapshot: dict) -> str:
        ok, _ = Gates.live_allowed(self.settings, p["strategy_id"])
        return "live" if ok and exchange_target(p, snapshot) else "paper"

    def place(self, p: dict, stake: float, snapshot: dict) -> dict:
        """Ordine BACK fill-or-kill. → {"ok", "mode", "odds", "stake", "ref", "error"}"""
        mode = self.route(p, snapshot)
        stake = round_back_stake(stake, self.min_stake)
        if not stake:
            return {"ok": False, "mode": mode, "error": f"puntata sotto il minimo dell'exchange {self.min_stake:.2f} €"}
        if mode == "paper":
            r = self.paper.place("BACK", p["odds"], stake, book_for(p, snapshot))
            if not r["ok"]:
                return {"ok": False, "mode": mode, "error": r["error"]}
            return {"ok": True, "mode": mode, "odds": r["price"], "stake": r["matched"], "ref": None}
        market_id, sel_id = exchange_target(p, snapshot)
        try:
            r = self.client.place(market_id, sel_id, "BACK", p["odds"], stake, fill_or_kill=True, ref=p["strategy_id"])
        except Exception as exc:
            return {"ok": False, "mode": mode, "error": str(exc)}
        if r["matched"] <= 0:
            return {"ok": False, "mode": mode, "error": f"non abbinata a {p['odds']:.2f} (fill-or-kill annullato)"}
        return {"ok": True, "mode": mode, "odds": r["avg_price"] or p["odds"], "stake": r["matched"],
                "ref": {"bet_id": r["bet_id"], "market_id": market_id, "selection_id": sel_id}}

    def hedge(self, bet: dict, lay_price: float, urgent: bool, snapshot: dict | None = None) -> dict:
        """Chiude un back con un lay della puntata "pareggiata" (stake × back / lay), così il risultato è
        uguale su ogni esito. Se è urgente (stop, time-to-jump) accetta fino a 2 tick peggio pur di chiudere."""
        price = tick_up(lay_price, 2) if urgent else lay_price
        size = max(LAY_MIN, round(bet["stake"] * bet["odds"] / price, 2))
        if bet["mode"] != "live":
            book = book_for({"market_id": bet["match_id"], "match_id": bet["match_id"], "selection": bet["selection"]},
                            snapshot or {})
            if book is None:                              # mercato sparito dal feed: chiusura al prezzo richiesto
                return {"ok": True, "price": lay_price}
            best = book.get("lay")
            if best and best <= price + 1e-9 and (book.get("lay_size") or 0) >= size * self.paper.liquidity_factor:
                return {"ok": True, "price": max(best, lay_price) if not urgent else best}
            return {"ok": False, "error": f"lay non abbinabile a {price:.2f} (miglior quota da bancare {best or '—'})"}
        ref = (json.loads(bet["extra"]) if bet.get("extra") else {}).get("betfair") or {}
        try:
            r = self.client.place(ref["market_id"], ref["selection_id"], "LAY", price, size, fill_or_kill=True,
                                  ref=f"hedge-{bet['id']}")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if r["matched"] <= 0:
            return {"ok": False, "error": f"lay non abbinato a {price:.2f}"}
        return {"ok": True, "price": r["avg_price"] or price}

    def settled_live(self, bets: list[dict]) -> dict[str, dict]:
        ids = [(json.loads(b["extra"]) or {}).get("betfair", {}).get("bet_id") for b in bets if b.get("extra")]
        ids = [i for i in ids if i]
        return self.client.cleared(ids) if ids else {}
