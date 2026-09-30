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


def round_lay_liability(liability: float, lay_price: float, min_backer: float = LAY_MIN) -> float:
    """Lay d'apertura: dalla responsabilità (perdita massima) alla puntata del backer arrotondata per difetto al
    centesimo; sotto il minimo di betfair.it (0,50 €) restituisce 0. Il risultato è di nuovo una responsabilità."""
    if lay_price <= 1.0:
        return 0.0
    backer = int(liability / (lay_price - 1.0) * 100 + 1e-9) / 100
    return round(backer * (lay_price - 1.0), 2) if backer >= min_backer - 1e-9 else 0.0


def _plain(p: dict) -> dict:
    """La selezione vera di una proposta lay ('LAY:home' → 'home')."""
    sel = str(p.get("selection", ""))
    return {**p, "selection": sel[4:]} if sel.startswith("LAY:") else p


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
    p = _plain(p)
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
    p = _plain(p)
    mid = p.get("market_id") or ""
    if mid.startswith("1.") and str(p.get("selection", "")).isdigit():
        return mid, int(p["selection"])
    m = (snapshot.get("matches") or {}).get(p.get("match_id")) or {}
    bf = m.get("betfair")
    if bf and p.get("selection") in bf.get("selection_ids", {}):
        return bf["market_id"], bf["selection_ids"][p["selection"]]
    return None


def best_size(book: dict, side: str) -> float:
    """Denaro al miglior prezzo (quello che conta per il fill-or-kill); se il feed non lo dà, il totale del lato.
    Il ripiego vale solo per un valore MANCANTE: 0 € al miglior prezzo resta 0 €."""
    v = book.get(f"{side}_size_best")
    if v is None:
        v = book.get(f"{side}_size")
    return float(v or 0.0)


class PaperExchange:
    """Exchange simulato: stesse regole di abbinamento di Betfair, soldi finti."""

    def __init__(self, liquidity_factor: float = 1.5):
        self.liquidity_factor = liquidity_factor

    def place(self, side: str, price: float, size: float, book: dict | None) -> dict:
        if not book:
            return {"ok": False, "error": "mercato non disponibile nel feed"}
        need = size * self.liquidity_factor
        if side == "BACK":
            best, avail = book.get("back"), best_size(book, "back")
            if not best or best < price - 1e-9:
                return {"ok": False, "error": f"quota da puntare {best or '—'} sotto il prezzo chiesto {price:.2f}"}
        else:
            best, avail = book.get("lay"), best_size(book, "lay")
            if not best or best > price + 1e-9:
                return {"ok": False, "error": f"quota da bancare {best or '—'} sopra il prezzo chiesto {price:.2f}"}
        if avail < need:
            return {"ok": False, "error": f"liquidità insufficiente ({avail:.0f} € nel book, servono {need:.0f} €)"}
        return {"ok": True, "price": price, "matched": round(size, 2)}


class Executor:
    def __init__(self, settings: dict, client=None, store=None):
        self.settings = settings
        self.store = store
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
        """live = soldi veri; paper = exchange simulato; shadow = in modalità live, strategia non ammessa ai
        soldi veri: si segue in ombra, senza toccare il bankroll vero (mai mescolare paper e live)."""
        ok, _ = Gates.live_allowed(self.settings, p["strategy_id"])
        if ok and exchange_target(p, snapshot) and not (snapshot.get("health") or {}).get("delayed"):
            return "live"
        return "shadow" if self.settings.get("mode") == "live" else "paper"

    # ── ordini veri: registrati PRIMA dell'invio, ritrovati dopo un errore ──────────────
    def _send(self, strategy_id: str, market_id: str, sel_id: int, side: str, price: float, size: float,
              bet_row_id: int | None = None, role: str | None = None) -> dict:
        """bet_row_id: per un LAY di chiusura, la puntata del libro che chiude (serve a riconoscerlo dopo un crash).
        role: 'open' apre una posizione (BACK, o LAY d'apertura), 'close' chiude un trade (LAY di chiusura)."""
        role = role or ("close" if side == "LAY" else "open")
        import uuid
        from .feeds.betfair import RequestNotSent
        from .store import now_iso
        ref = f"bb{uuid.uuid4().hex[:20]}"
        if self.store is not None:
            self.store.execute("INSERT INTO orders(ts, ref, strategy_id, market_id, selection_id, side, price, size, status, "
                               "updated, bet_row_id, role) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                               (now_iso(), ref, strategy_id, market_id, str(sel_id), side, price, size, "PENDING", now_iso(),
                                bet_row_id, role))
        try:
            r = self.client.place(market_id, sel_id, side, price, size, fill_or_kill=True, order_ref=ref,
                                  strategy_ref=strategy_id.split("_")[0] + strategy_id.split("_")[-1])
        except RequestNotSent as exc:              # errore PRIMA dell'invio (login rimandato, sessione assente)
            r = {"matched": 0.0, "status": "NOT_SENT", "error": f"ordine non inviato: {exc}"}
        except Exception as exc:
            r = self._recover(ref, str(exc))
        self._update_order(ref, r)
        return {**r, "order_ref": ref}

    def _recover(self, ref: str, error: str) -> dict:
        """Risposta persa (timeout, rete): l'ordine potrebbe essere partito. Lo si cerca su Betfair."""
        try:
            found = self.client.current_orders(order_refs=[ref])
        except Exception as exc:
            return {"matched": 0.0, "status": "UNKNOWN", "error": f"{error}; verifica non riuscita: {exc}"}
        if found:
            o = found[0]
            return {"bet_id": o.get("betId"), "matched": float(o.get("sizeMatched") or 0.0),
                    "avg_price": float(o.get("averagePriceMatched") or 0.0), "status": o.get("status"), "recovered": True}
        # non trovato SUBITO non vuol dire non partito: la richiesta può arrivare a Betfair qualche secondo dopo.
        # Resta "da confermare": nessuna nuova puntata finché il risolutore (dopo 2 minuti) non lo chiarisce.
        return {"matched": 0.0, "status": "UNCONFIRMED", "error": f"{error}; ordine non ancora visibile su Betfair"}

    def _update_order(self, ref: str, r: dict) -> None:
        if self.store is None:
            return
        from .store import now_iso
        self.store.execute("UPDATE orders SET status=?, bet_id=?, matched=?, avg_price=?, error=?, updated=? WHERE ref=?",
                           ("MATCHED" if r.get("matched") else r.get("status") or "KILLED", r.get("bet_id"), r.get("matched"),
                            r.get("avg_price"), r.get("error"), now_iso(), ref))

    def place(self, p: dict, stake: float, snapshot: dict) -> dict:
        """Ordine BACK fill-or-kill (o LAY d'apertura). → {"ok", "mode", "odds", "stake", "ref", "error"}
        Per un lay `stake` è la RESPONSABILITÀ (perdita massima) e anche la risposta la restituisce così."""
        if p.get("side") == "LAY":
            return self._place_lay(p, stake, snapshot)
        mode = self.route(p, snapshot)
        stake = round_back_stake(stake, self.min_stake)
        if not stake:
            return {"ok": False, "mode": mode, "error": f"puntata sotto il minimo dell'exchange {self.min_stake:.2f} €"}
        if mode in ("paper", "shadow"):
            r = self.paper.place("BACK", p["odds"], stake, book_for(p, snapshot))
            if not r["ok"]:
                return {"ok": False, "mode": mode, "error": r["error"]}
            return {"ok": True, "mode": mode, "odds": r["price"], "stake": r["matched"], "ref": None}
        market_id, sel_id = exchange_target(p, snapshot)
        r = self._send(p["strategy_id"], market_id, sel_id, "BACK", p["odds"], stake)
        if r.get("status") == "UNCONFIRMED":
            return {"ok": False, "mode": mode, "unconfirmed": True, "order_ref": r["order_ref"],
                    "error": f"risposta di Betfair persa, ordine non ancora visibile ({r.get('error')}): lo ricontrollo tra 2 minuti"}
        if r.get("status") == "UNKNOWN":
            return {"ok": False, "mode": mode, "unknown": True, "order_ref": r["order_ref"],
                    "error": f"esito dell'ordine sconosciuto ({r.get('error')}): controllo manuale richiesto"}
        if r["matched"] <= 0:
            return {"ok": False, "mode": mode, "error": r.get("error") or f"non abbinata a {p['odds']:.2f} (fill-or-kill annullato)"}
        return {"ok": True, "mode": mode, "odds": r["avg_price"] or p["odds"], "stake": r["matched"],
                "ref": {"bet_id": r["bet_id"], "market_id": market_id, "selection_id": sel_id, "order_ref": r["order_ref"]}}

    def _place_lay(self, p: dict, liability: float, snapshot: dict) -> dict:
        """Lay d'apertura fill-or-kill al prezzo della proposta: la puntata del backer è responsabilità / (quota − 1)."""
        mode = self.route(p, snapshot)
        price = p["odds"]
        backer = int(liability / max(price - 1.0, 1e-9) * 100 + 1e-9) / 100
        if backer < LAY_MIN - 1e-9:
            return {"ok": False, "mode": mode, "error": f"lay sotto il minimo di betfair.it ({LAY_MIN:.2f} € del backer)"}
        if mode in ("paper", "shadow"):
            r = self.paper.place("LAY", price, backer, book_for(p, snapshot))
            if not r["ok"]:
                return {"ok": False, "mode": mode, "error": r["error"]}
            return {"ok": True, "mode": mode, "odds": price, "stake": round(backer * (price - 1.0), 2), "backer": backer,
                    "ref": None}
        market_id, sel_id = exchange_target(p, snapshot)
        r = self._send(p["strategy_id"], market_id, sel_id, "LAY", price, backer, role="open")
        if r.get("status") == "UNCONFIRMED":
            return {"ok": False, "mode": mode, "unconfirmed": True, "order_ref": r["order_ref"],
                    "error": f"risposta di Betfair persa, lay non ancora visibile ({r.get('error')}): lo ricontrollo tra 2 minuti"}
        if r.get("status") == "UNKNOWN":
            return {"ok": False, "mode": mode, "unknown": True, "order_ref": r["order_ref"],
                    "error": f"esito del lay sconosciuto ({r.get('error')}): controllo manuale richiesto"}
        if r["matched"] <= 0:
            return {"ok": False, "mode": mode, "error": r.get("error") or f"lay non abbinato a {price:.2f} (fill-or-kill annullato)"}
        avg = r["avg_price"] or price
        return {"ok": True, "mode": mode, "odds": avg, "stake": round(r["matched"] * (avg - 1.0), 2), "backer": r["matched"],
                "ref": {"bet_id": r["bet_id"], "market_id": market_id, "selection_id": sel_id, "order_ref": r["order_ref"]}}

    def hedge(self, bet: dict, lay_price: float, urgent: bool, snapshot: dict | None = None) -> dict:
        """Chiude un back con un lay della puntata "pareggiata" (stake × back / lay), così il risultato è
        uguale su ogni esito. Se è urgente (stop, time-to-jump) accetta fino a 2 tick peggio pur di chiudere.
        La puntata del lay si calcola sul prezzo VISTO (dove l'ordine di solito si abbina), non sul limite:
        calcolata sul limite resterebbe una parte scoperta ogni volta che Betfair abbina a un prezzo migliore."""
        price = tick_up(lay_price, 2) if urgent else lay_price
        size = max(LAY_MIN, round(bet["stake"] * bet["odds"] / lay_price, 2))
        if bet["mode"] != "live":
            book = book_for({"market_id": bet["match_id"], "match_id": bet["match_id"], "selection": bet["selection"]},
                            snapshot or {})
            if book is None:
                # mercato sparito: niente chiusura "gratis". La posizione resta aperta e si regola sul risultato
                # (il caso peggiore per un trade), come succederebbe sull'exchange vero.
                return {"ok": False, "error": "mercato non più nel feed: la posizione si regola sul risultato"}
            best = book.get("lay")
            avail = best_size(book, "lay")
            if best and best <= price + 1e-9 and avail >= size * self.paper.liquidity_factor:
                return {"ok": True, "price": max(best, lay_price) if not urgent else best}
            return {"ok": False, "error": f"lay non abbinabile a {price:.2f} (miglior quota da bancare {best or '—'})"}
        ref = (json.loads(bet["extra"]) if bet.get("extra") else {}).get("betfair") or {}
        r = self._send(bet["strategy_id"], ref["market_id"], ref["selection_id"], "LAY", price, size, bet_row_id=bet["id"])
        if r.get("status") == "UNKNOWN":
            return {"ok": False, "unknown": True, "error": f"esito del lay sconosciuto ({r.get('error')})"}
        if r.get("status") == "UNCONFIRMED":
            return {"ok": False, "unconfirmed": True, "error": f"lay da confermare ({r.get('error')})"}
        if r["matched"] <= 0:
            return {"ok": False, "error": r.get("error") or f"lay non abbinato a {price:.2f}"}
        # size e prezzo medio VERI: il Banco calcola il risultato di ogni esito da questi, non dal limite
        return {"ok": True, "price": r["avg_price"] or price, "size": r["matched"]}

    def settled_live(self, bets: list[dict]) -> dict[str, dict]:
        ids = [(json.loads(b["extra"]) or {}).get("betfair", {}).get("bet_id") for b in bets if b.get("extra")]
        ids = [i for i in ids if i]
        return self.client.cleared(ids) if ids else {}
