"""AGENTE 7 — TESORIERA. Bankroll, reinvestimento (compounding) e metriche in tempo reale."""
from __future__ import annotations

from ..metrics import summarize
from .base import Agent


class Tesoriere(Agent):
    key = "tesoriere"
    name = "Anna"
    role = "Bankroll, reinvestimento dei profitti, ROI e drawdown"

    def update(self, state: dict) -> dict:
        br = self.office.bankroll
        br.mark()
        bets = self.store.query("SELECT * FROM bets")
        m = summarize(bets, br.initial_capital)
        worst = max(float(self.store.get("max_drawdown_seen") or 0.0), state["drawdown"])
        self.store.set("max_drawdown_seen", worst)            # incrementale: niente rilettura dell'intera curva
        m["max_drawdown"] = max(m["max_drawdown"], worst)
        by_strategy = {}
        for sid in sorted({b["strategy_id"] for b in bets}):
            by_strategy[sid] = summarize([b for b in bets if b["strategy_id"] == sid])
        L = self.office.risk.limits
        next_cap = L["max_stake_pct"] * state["stake_base"]
        metrics = {**m, "bankroll": state["bankroll"], "cash": state["cash"], "initial": br.initial_capital,
                   "profits": state["profits"], "stake_base": state["stake_base"], "next_max_stake": next_cap,
                   "drawdown_now": state["drawdown"], "by_strategy": by_strategy,
                   "reinvest_fraction": L["reinvest_fraction"]}
        self.store.set("metrics", metrics)
        self.say(f"Bankroll {state['bankroll']:.2f} € = capitale {br.initial_capital:.2f} + profitti "
                 f"{state['profits']:+.2f}. Base di puntata {state['stake_base']:.2f} € "
                 f"(puntata massima prossimo ciclo {next_cap:.2f} €). ROI {m['roi']:+.1%} su {m['bets']} chiuse.",
                 "ok", "bankroll", stats={"bankroll": round(state["bankroll"], 2), "roi": m["roi"],
                                          "win_rate": m["win_rate"]})
        return metrics
