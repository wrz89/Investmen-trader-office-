"""AGENTE 3 — ANALISTA STRATEGIE (sport) e AGENTE 4 — TRADER CAVALLI (exchange).
Fanno girare le strategie sul fotogramma del feed e propongono le puntate.
Non piazzano nulla: ogni proposta passa dal Risk Manager."""
from __future__ import annotations

from .base import Agent


class _StrategyDesk(Agent):
    kinds: tuple = ()

    def propose(self, snapshot: dict, strategies: list[dict]) -> list[dict]:
        out = []
        for s in strategies:
            if s["kind"] not in self.kinds:
                continue
            try:
                props = s["module"].propose(snapshot, s["params"], {"store": self.store})
            except Exception as exc:
                self.log(f"{s['id']}: errore nella strategia ({exc}). Nessuna proposta.", "ERROR", "error")
                continue
            for p in props:
                p["strategy_status"] = s["status"]
            out += props
        if out:
            best = max(out, key=lambda p: p["edge"])
            self.say(f"{len(out)} proposte. Migliore: {best['label']} a {best['odds']:.2f} (edge {best['edge']:+.1%}).",
                     "ok", "proposal", stats={"proposte": len(out)})
        else:
            self.status("idle", "Nessuna occasione che passi i filtri in questo ciclo.", {"proposte": 0})
        return out

    def manage(self, snapshot: dict, strategies: list[dict], open_bets: list[dict]) -> list[dict]:
        actions = []
        for s in strategies:
            if s["kind"] not in self.kinds or not hasattr(s["module"], "manage"):
                continue
            mine = [b for b in open_bets if b["strategy_id"] == s["id"]]
            if mine:
                actions += s["module"].manage(mine, snapshot, s["params"], {"store": self.store})
        return actions


class Analista(_StrategyDesk):
    key = "analista"
    name = "Davide"
    role = "Strategie sport: favoriti, live scalping, sure bet"
    kinds = ("prematch", "live", "arb")


class TraderCavalli(_StrategyDesk):
    key = "cavalli"
    name = "Matteo"
    role = "Exchange cavalli: back-to-lay e green-up prima del via"
    kinds = ("exchange",)
