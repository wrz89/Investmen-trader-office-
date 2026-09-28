"""AGENTE 1 — PORTFOLIO MANAGER.

Coordina l'ufficio: decide quali strategie sono autorizzate, confronta le
performance, sospende quelle inefficienti. Non può scavalcare il Risk Manager.
"""
from __future__ import annotations

import numpy as np

from .. import registry
from ..validation import profit_factor
from .base import Agent

# RESEARCH → (validazione) → PAPER | REJECTED ; PAPER → SUSPENDED
# LIVE non è mai assegnato automaticamente: richiede una decisione umana.


class PortfolioManager(Agent):
    key = "portfolio_manager"
    name = "Portfolio Manager"
    role = "Coordina, autorizza e sospende le strategie"

    def authorize(self, entries: list[dict]) -> list[dict]:
        out = []
        for e in entries:
            sid = e["module"].STRATEGY_ID
            validation = registry.load_validation(sid)
            current = self.store.strategy_status(sid)
            if e["registry"]["status"] == "tampered":
                new, reason = "BLOCKED", "codice modificato senza nuova versione"
            elif validation is None:
                new, reason = "RESEARCH", "in attesa di validazione"
            elif validation["verdict"] == "REJECTED":
                new, reason = "REJECTED", "validazione fuori campione non superata"
            elif current in ("SUSPENDED", "BLOCKED"):
                new, reason = current, "sospesa: riattivazione solo manuale"
            else:
                new, reason = "PAPER", "validata: autorizzata al paper trading"
            if new != current:
                self.store.set_strategy_status(sid, new, reason)
                self.say(f"{sid} → {new} ({reason})", "working", "authorize")
            out.append({"module": e["module"], "status": new, "validation": validation})
        return out

    def review(self, strategies_state: list[dict]) -> None:
        g = self.office.gates
        for st in strategies_state:
            if st["status"] != "PAPER":
                continue
            sid = st["module"].STRATEGY_ID
            trades = self.store.query("SELECT * FROM trades WHERE strategy_id=? AND mode='paper'", (sid,))
            if len(trades) < g["paper_min_trades_for_review"]:
                continue
            net = np.array([t["pnl_net"] for t in trades])
            pf = profit_factor(net)
            model_slip = self.settings["costs"]["slippage_bps"] / 10_000
            real_slip = np.mean([abs(t["slippage"]) / (t["qty"] * t["exec_entry"]) / 2 for t in trades])
            if pf < g["paper_suspend_profit_factor"]:
                reason = f"PF paper {pf:.2f} < {g['paper_suspend_profit_factor']} su {len(trades)} trade"
            elif model_slip > 0 and real_slip > g["paper_max_slippage_vs_model"] * model_slip:
                reason = f"slippage reale {real_slip * 1e4:.1f}bp > {g['paper_max_slippage_vs_model']}× modello"
            else:
                continue
            self.store.set_strategy_status(sid, "SUSPENDED", reason)
            self.say(f"Sospendo {sid}: {reason}", "alert", "suspend", level="WARN")

    def summary(self, strategies_state: list[dict], risk_state: dict, n_opps: int, n_fills: int) -> None:
        active = [s["module"].STRATEGY_ID for s in strategies_state if s["status"] == "PAPER"]
        state = "alert" if risk_state.get("kill_switch") else ("ok" if active else "idle")
        if risk_state.get("kill_switch"):
            msg = f"Ufficio FERMO: kill switch ({risk_state['kill_switch']})."
        elif not active:
            msg = ("Nessuna strategia ha superato la validazione: capitale fermo, come da regole. "
                   f"Opportunità viste: {n_opps}.")
        else:
            msg = (f"{len(active)} strategie attive in paper. Equity {risk_state['equity']:.2f}, "
                   f"esposizione {risk_state['total_exposure']:.2f}. Opportunità {n_opps}, eseguite {n_fills}.")
        self.status(state, msg, stats={"active": active, "equity": risk_state.get("equity")})
