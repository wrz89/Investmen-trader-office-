"""Esecuzione: PAPER, BETFAIR (soldi veri, dietro cancelli) e segnalazione manuale.

Chi decide COSA puntare è sempre il Risk Manager; qui si decide solo DOVE finisce l'ordine.
  • paper   : puntata registrata al prezzo del feed, nessun ordine esterno.
  • betfair : ordine LIMIT fill-or-kill sull'exchange (abbinato subito per intero o annullato).
              Solo per selezioni che hanno un mercato Betfair (cavalli, calcio da feed betfair).
  • manual  : quote di bookmaker senza API → la puntata resta in paper e parte un avviso
              Telegram "da piazzare a mano" (l'ufficio non automatizza siti di terzi).
"""
from __future__ import annotations

from . import local_settings
from .feeds.mock import tick_up


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
            return False, "chiave Betfair non verificata"
        if not bf.get("test_done"):
            return False, "ordine di prova Betfair non ancora fatto"
        if not bf.get("live_enabled"):
            return False, "interruttore 'Puntate reali' spento"
        if strategy_id not in (settings.get("live_strategies") or []):
            return False, f"{strategy_id} non è tra le live_strategies"
        return True, "ok"


class Executor:
    def __init__(self, settings: dict, client=None):
        self.settings = settings
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from .feeds.betfair import BetfairClient
            self._client = BetfairClient(local_settings.load()["betfair"])
        return self._client

    def route(self, p: dict, snapshot: dict) -> str:
        ok, _ = Gates.live_allowed(self.settings, p["strategy_id"])
        if ok and self._betfair_target(p, snapshot):
            return "live"
        if p["bookmaker"] not in ("Exchange", "Betfair") and (self.settings.get("execution") or {}).get("manual_alerts"):
            return "manual"
        return "paper"

    def _betfair_target(self, p: dict, snapshot: dict) -> tuple[str, int] | None:
        if p.get("exchange") and p.get("market_id", "").startswith("1."):          # id di mercato Betfair reale
            return p["market_id"], int(p["selection"])
        m = snapshot.get("matches", {}).get(p["match_id"]) or {}
        bf = m.get("betfair")
        if bf and p["bookmaker"] == "Betfair" and p["selection"] in bf["selection_ids"]:
            return bf["market_id"], bf["selection_ids"][p["selection"]]
        return None

    def place(self, p: dict, stake: float, snapshot: dict) -> dict:
        """→ {"ok", "mode", "odds", "stake", "ref", "error"}"""
        mode = self.route(p, snapshot)
        if mode != "live":
            return {"ok": True, "mode": mode, "odds": p["odds"], "stake": stake, "ref": None}
        min_stake = (self.settings.get("execution") or {}).get("betfair_min_stake", 2.0)
        if stake < min_stake:
            return {"ok": False, "mode": mode, "error": f"puntata {stake:.2f} € sotto il minimo Betfair {min_stake:.2f} €"}
        market_id, sel_id = self._betfair_target(p, snapshot)
        try:
            r = self.client.place(market_id, sel_id, "BACK", p["odds"], stake, fill_or_kill=True, ref=p["strategy_id"])
        except Exception as exc:
            return {"ok": False, "mode": mode, "error": str(exc)}
        if r["matched"] <= 0:
            return {"ok": False, "mode": mode, "error": f"non abbinata a {p['odds']:.2f} (fill-or-kill annullato)"}
        return {"ok": True, "mode": mode, "odds": r["avg_price"] or p["odds"], "stake": r["matched"],
                "ref": {"bet_id": r["bet_id"], "market_id": market_id, "selection_id": sel_id}}

    def hedge(self, bet: dict, lay_price: float, urgent: bool) -> dict:
        """Chiude un back con un lay. In paper al prezzo richiesto; su Betfair fill-or-kill,
        e se è urgente (time-to-jump) accetta fino a 2 tick peggio pur di chiudere."""
        if bet["mode"] != "live":
            return {"ok": True, "price": lay_price}
        import json
        ref = (json.loads(bet["extra"]) if bet.get("extra") else {}).get("betfair") or {}
        price = tick_up(lay_price, 2) if urgent else lay_price
        size = bet["stake"] * bet["odds"] / price
        try:
            r = self.client.place(ref["market_id"], ref["selection_id"], "LAY", price, size, fill_or_kill=True,
                                  ref=f"hedge-{bet['id']}")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if r["matched"] <= 0:
            return {"ok": False, "error": f"lay non abbinato a {price:.2f}"}
        return {"ok": True, "price": r["avg_price"] or price}

    def settled_live(self, bets: list[dict]) -> dict[str, dict]:
        import json
        ids = [(json.loads(b["extra"]) or {}).get("betfair", {}).get("bet_id") for b in bets if b.get("extra")]
        ids = [i for i in ids if i]
        return self.client.cleared(ids) if ids else {}
