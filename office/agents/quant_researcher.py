"""AGENTE 4 — QUANTITATIVE RESEARCHER.

Backtest, walk-forward, out-of-sample, Monte Carlo, drawdown, win rate,
expectancy, Sharpe, profit factor, sensibilità ai costi. Una strategia
non viene approvata solo perché "ha guadagnato nel backtest".
"""
from __future__ import annotations

from datetime import datetime, timezone

from .. import registry
from ..backtest import CostModel
from ..market import DataError, MarketData
from ..validation import validate
from .base import Agent


class QuantResearcher(Agent):
    key = "quant_researcher"
    name = "Quant Researcher"
    role = "Valida le strategie fuori campione"

    def research(self, entries: list[dict], history_exchange: str) -> list[dict]:
        s = self.settings
        pending = [e for e in entries if registry.load_validation(e["module"].STRATEGY_ID) is None
                   and e["registry"]["status"] != "tampered"]
        if not pending:
            self.say("Nessuna nuova versione da validare.", "idle", "research")
            return []

        self.say(f"Scarico {s['research']['history_days']} giorni di storico da {history_exchange}…",
                 "working", "research")
        md = MarketData(history_exchange, s["exchange"].get("options") if history_exchange == s["exchange"]["history"]
                        else None, timeout_ms=30000)
        datasets = {}
        for symbol in s["universe"]:
            try:
                datasets[symbol] = md.history(symbol, s["timeframe"], s["research"]["history_days"])
            except DataError as exc:
                self.say(f"Storico {symbol} non disponibile: {exc}", "alert", "error", level="ERROR")
                return []
            self.log(f"Storico {symbol}: {len(datasets[symbol])} candele {s['timeframe']}", kind="research")

        costs = CostModel.from_settings(s)
        gates = self.office.gates
        alloc = self.office.risk.limits["max_exposure_per_asset"]
        results = []
        for entry in pending:
            module = entry["module"]
            self.status("working", f"Walk-forward su {module.STRATEGY_ID} ({module.NAME})…")
            result = validate(module, datasets, costs, gates, s["research"], alloc, registry.total_trials())
            result["data_source"] = history_exchange
            result["timeframe"] = s["timeframe"]
            result["validated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            registry.save_validation(module.STRATEGY_ID, result)
            failed = [c["label"] for c in result["checks"] if not c["passed"]]
            m = result["metrics"]
            summary = (f"{module.STRATEGY_ID}: {result['verdict']} — {m['trades']} trade OOS, "
                       f"PF {m['profit_factor']:.2f}, Sharpe {m['sharpe_annual']:.2f}, "
                       f"netto medio {m['expectancy_net'] * 100:+.2f}%/trade")
            if failed:
                summary += f". Non superati: {', '.join(failed[:3])}" + ("…" if len(failed) > 3 else "")
            self.say(summary, "ok" if result["verdict"] == "PASSED" else "blocked", "validation",
                     payload={"strategy_id": module.STRATEGY_ID, "verdict": result["verdict"]})
            results.append(result)
        passed = sum(r["verdict"] == "PASSED" for r in results)
        self.status("ok" if passed else "idle",
                    f"Validazione completata: {passed}/{len(results)} strategie approvate per il paper trading.")
        return results
