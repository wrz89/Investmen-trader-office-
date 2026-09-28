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
from ..strategies import param_combinations, timeframe_of
from ..validation import validate
from .base import Agent


class QuantResearcher(Agent):
    key = "quant_researcher"
    name = "Quant Researcher"
    role = "Valida le strategie fuori campione"

    def research(self, entries: list[dict], history_exchange: str) -> list[dict]:
        s = self.settings
        retired = self.office.retired
        pending = [e for e in entries if registry.load_validation(e["module"].STRATEGY_ID) is None
                   and e["registry"]["status"] != "tampered" and e["module"].STRATEGY_ID not in retired]
        # le versioni ritirate mai validate qui sono state comunque provate: contano come tentativi
        self.extra_trials = sum(len(param_combinations(e["module"].PARAM_GRID)) for e in entries
                                if e["module"].STRATEGY_ID in retired
                                and registry.load_validation(e["module"].STRATEGY_ID) is None)
        if not pending:
            self.say("Nessuna nuova versione da validare.", "idle", "research")
            return []

        md = MarketData(history_exchange, s["exchange"].get("options") if history_exchange == s["exchange"]["history"]
                        else None, timeout_ms=30000)
        costs = CostModel.from_settings(s)
        gates = self.office.gates
        alloc = self.office.risk.limits["max_exposure_per_asset"]

        # ogni strategia può avere il proprio timeframe: storico scaricato una volta per timeframe
        by_tf: dict[str, list[dict]] = {}
        for e in pending:
            by_tf.setdefault(timeframe_of(e["module"], s["timeframe"]), []).append(e)

        results = []
        for tf, group in by_tf.items():
            self.say(f"Scarico {s['research']['history_days']} giorni di storico {tf} da {history_exchange}…",
                     "working", "research")
            datasets = {}
            for symbol in s["universe"]:
                try:
                    hist_symbol = (s["research"].get("history_symbols") or {}).get(symbol, symbol)
                    datasets[symbol] = md.history(hist_symbol, tf, s["research"]["history_days"])
                except DataError as exc:
                    self.say(f"Storico {symbol} {tf} non disponibile: {exc}", "alert", "error", level="ERROR")
                    datasets = None
                    break
                self.log(f"Storico {symbol} (da {hist_symbol}): {len(datasets[symbol])} candele {tf}",
                         kind="research")
                if len(datasets[symbol]) < 1000:
                    # l'universo è deciso prima: niente esclusioni silenziose di un asset
                    self.say(f"Storico {symbol} {tf} insufficiente ({len(datasets[symbol])} candele): "
                             "validazione rinviata.", "alert", "error", level="ERROR")
                    datasets = None
                    break
            if datasets is None:
                continue
            for entry in group:
                results.append(self._validate_one(entry["module"], datasets, costs, gates, alloc, tf,
                                                  history_exchange))
        passed = sum(r["verdict"] == "PASSED" for r in results)
        self.status("ok" if passed else "idle",
                    f"Validazione completata: {passed}/{len(results)} strategie approvate per il paper trading.")
        return results

    def _validate_one(self, module, datasets, costs, gates, alloc, tf, history_exchange) -> dict:
        s = self.settings
        self.status("working", f"Walk-forward su {module.STRATEGY_ID} ({module.NAME}, {tf})…")
        result = validate(module, datasets, costs, gates, s["research"], alloc, registry.total_trials() + self.extra_trials, tf,
                          self.office.risk.limits["risk_per_trade"])
        result["data_source"] = history_exchange
        result["timeframe"] = tf
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
        return result
