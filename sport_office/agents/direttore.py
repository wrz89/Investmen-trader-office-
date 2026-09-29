"""AGENTE 1 — DIRETTORE SPORTIVO. Decide quali strategie sono attive e chiude il ciclo con un riepilogo."""
from __future__ import annotations

from ..config import load_yaml
from ..strategies import discover
from .base import Agent


class Direttore(Agent):
    key = "direttore"
    name = "Carlo"
    role = "Direttore sportivo: attiva le strategie e coordina il ciclo"

    def strategies(self) -> list[dict]:
        params = load_yaml("strategies.yaml")
        active = set(self.settings.get("active_strategies") or [])
        out = []
        for m in discover():
            out.append({"module": m, "id": m.STRATEGY_ID, "name": m.NAME, "kind": m.KIND,
                        "params": params.get(m.STRATEGY_ID, {}),
                        "status": "ATTIVA" if m.STRATEGY_ID in active else "OSSERVAZIONE"})
        self.store.set("strategies", [{k: v for k, v in s.items() if k != "module"} for s in out])
        return out

    def summary(self, proposals: int, placed: int, settled: int, state: dict) -> None:
        msg = (f"Ciclo chiuso: {proposals} proposte, {placed} puntate piazzate, {settled} chiuse. "
               f"Bankroll {state['bankroll']:.2f} € (profitti {state['profits']:+.2f} €).")
        self.say(msg, "alert" if state.get("kill_switch") else "ok", "cycle",
                 stats={"proposte": proposals, "piazzate": placed, "chiuse": settled})
