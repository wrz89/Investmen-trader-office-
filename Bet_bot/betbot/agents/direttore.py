"""AGENTE 1 — CARLO · DIRETTORE SPORTIVO.

Decide quali strategie lavorano con soldi (anche finti) e quali restano "in ombra":
  • ATTIVA       elencata in active_strategies e bankroll sufficiente;
  • IN ATTESA    elencata ma con una soglia di bankroll (min_bankroll in strategies.yaml) non ancora raggiunta:
                 lavora in ombra (senza capitale) e si sblocca da sola quando il compounding porta il bankroll
                 alla soglia;
  • OSSERVAZIONE elencata in observe_strategies: solo ombra.
Le strategie ritirate (file vecchi) non girano più ma restano nella libreria per la storia."""
from __future__ import annotations

from ..config import load_yaml
from ..strategies import discover
from .base import Agent


class Direttore(Agent):
    key = "direttore"
    name = "Carlo"
    role = "Direttore sportivo: attiva le strategie e coordina il ciclo"

    def strategies(self) -> list[dict]:
        from .risk import unlock_bankroll
        params = load_yaml("strategies.yaml")
        active = list(self.settings.get("active_strategies") or [])
        observe = list(self.settings.get("observe_strategies") or [])
        limits = self.office.risk.limits if hasattr(self.office, "risk") else load_yaml("risk_limits.yaml")
        min_stake = getattr(getattr(self.office, "executor", None), "min_stake", 0.0)
        bankroll = self.office.bankroll.total
        prev = {s["id"]: s["status"] for s in (self.store.get("strategies") or [])}
        commission = (self.settings.get("execution") or {}).get("commission")
        out = []
        for m in discover():
            sid = m.STRATEGY_ID
            if sid not in active and sid not in observe:
                continue                                        # ritirata: resta in libreria, non gira
            p = dict(params.get(sid, {}))
            if commission is not None:
                p.setdefault("commission", commission)
            p.setdefault("min_stake", min_stake)
            unlock = float(p.get("min_bankroll") or 0.0)
            full_stake_at = unlock_bankroll(m.KIND, limits, min_stake)   # da qui la puntata minima sta sotto max_stake_pct
            if sid in observe:
                status = "OSSERVAZIONE"
            elif bankroll + 1e-9 < unlock:
                status = "IN ATTESA"
            else:
                status = "ATTIVA"
            if prev.get(sid) == "IN ATTESA" and status == "ATTIVA":
                self.say(f"{sid} sbloccata: il bankroll ha raggiunto {bankroll:.2f} € (soglia {unlock:.0f} €). "
                         "Da ora punta con capitale.", "ok", "unlock", level="WARN")
            out.append({"module": m, "id": sid, "name": m.NAME, "kind": m.KIND, "params": p, "status": status,
                        "unlock_at": unlock or None, "full_stake_at": full_stake_at or None})
        self.store.set("strategies", [{k: v for k, v in s.items() if k != "module"} for s in out])
        return out

    def summary(self, proposals: int, placed: int, settled: int, state: dict) -> None:
        msg = (f"Ciclo chiuso: {proposals} proposte, {placed} puntate piazzate, {settled} chiuse. "
               f"Bankroll {state['bankroll']:.2f} € (profitti {state['profits']:+.2f} €).")
        stats = {"proposte": proposals, "piazzate": placed, "chiuse": settled}
        st = "alert" if state.get("kill_switch") else "ok"
        if placed or settled:
            self.say(msg, st, "cycle", stats=stats)          # nel registro solo i cicli con movimenti
        else:
            self.status(st, msg, stats)
