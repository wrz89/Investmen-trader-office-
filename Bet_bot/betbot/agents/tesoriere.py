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
        all_rows = self.store.query("SELECT * FROM bets")
        bets = [b for b in all_rows if b["mode"] != "shadow"]
        shadow_rows = [b for b in all_rows if b["mode"] == "shadow"]
        m = summarize(bets, br.initial_capital)
        worst = max(float(self.store.get("max_drawdown_seen") or 0.0), state["drawdown"])
        self.store.set("max_drawdown_seen", worst)            # incrementale: niente rilettura dell'intera curva
        m["max_drawdown"] = max(m["max_drawdown"], worst)
        by_strategy = {}
        for sid in sorted({b["strategy_id"] for b in bets}):
            by_strategy[sid] = summarize([b for b in bets if b["strategy_id"] == sid])
        shadow = {sid: summarize([b for b in shadow_rows if b["strategy_id"] == sid])
                  for sid in sorted({b["strategy_id"] for b in shadow_rows})}
        L = self.office.risk.limits
        next_cap = self.office.risk.next_max_stake(state)       # stessi tagli del Risk Manager (rischio aperto, budget)
        metrics = {**m, "bankroll": state["bankroll"], "cash": state["cash"], "initial": br.initial_capital,
                   "profits": state["profits"], "stake_base": state["stake_base"], "next_max_stake": next_cap,
                   "drawdown_now": state["drawdown"], "by_strategy": by_strategy, "shadow_by_strategy": shadow,
                   "reinvest_fraction": L["reinvest_fraction"]}
        self.store.set("metrics", metrics)
        self.status("ok", f"Bankroll {state['bankroll']:.2f} € = capitale {br.initial_capital:.2f} + profitti "
                 f"{state['profits']:+.2f}. Base di puntata {state['stake_base']:.2f} € "
                 f"(puntata massima prossimo ciclo {next_cap:.2f} €). ROI {m['roi']:+.1%} su {m['bets']} chiuse.",
                    {"bankroll": round(state["bankroll"], 2), "roi": m["roi"],
                                          "win_rate": m["win_rate"]})
        return metrics
