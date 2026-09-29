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
from ..portfolio import diversification
from ..strategies import by_id
from ..validation import reconstruct_oos, validate
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
        results = self._cost_audits(entries, history_exchange)
        if not pending:
            if not results:
                self.say("Nessuna nuova versione da validare.", "idle", "research")
            return results

        md = MarketData(history_exchange, s["exchange"].get("options") if history_exchange == s["exchange"]["history"]
                        else None, timeout_ms=30000)
        costs = CostModel.from_settings(s)
        gates = self.office.gates
        alloc = self.office.risk.limits["max_exposure_per_asset"]

        # ogni strategia può avere il proprio timeframe: storico scaricato una volta per timeframe
        by_tf: dict[str, list[dict]] = {}
        for e in pending:
            by_tf.setdefault(timeframe_of(e["module"], s["timeframe"]), []).append(e)

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

    def _datasets(self, md, tf: str) -> dict | None:
        s = self.settings
        datasets = {}
        for symbol in s["universe"]:
            hist_symbol = (s["research"].get("history_symbols") or {}).get(symbol, symbol)
            try:
                datasets[symbol] = md.history(hist_symbol, tf, s["research"]["history_days"])
            except DataError as exc:
                self.say(f"Storico {symbol} {tf} non disponibile: {exc}", "alert", "error", level="ERROR")
                return None
            if len(datasets[symbol]) < 1000:
                self.say(f"Storico {symbol} {tf} insufficiente: verifica rinviata.", "alert", "error", level="ERROR")
                return None
        return datasets

    def _cost_audits(self, entries: list[dict], history_exchange: str) -> list[dict]:
        """Riverifica con i costi ATTUALI le strategie approvate con costi più bassi.

        Stessa procedura e stesso numero di tentativi della validazione originale:
        cambia solo il costo. L'esito può solo bocciare, mai promuovere."""
        s = self.settings
        costs = CostModel.from_settings(s)
        todo = []
        for e in entries:
            sid = e["module"].STRATEGY_ID
            v = registry.load_validation(sid)
            if (v and v["verdict"] == "PASSED" and sid not in self.office.retired
                    and registry.needs_cost_audit(v, costs.per_side)
                    and registry.load_cost_audit(sid, costs.per_side) is None):
                todo.append((e["module"], v))
        if not todo:
            return []
        md = MarketData(history_exchange, s["exchange"].get("options") if history_exchange == s["exchange"]["history"]
                        else None, timeout_ms=30000)
        out, cache = [], {}
        for module, v in todo:
            tf = v.get("timeframe") or timeframe_of(module, s["timeframe"])
            if tf not in cache:
                self.say(f"Costi cambiati: riverifico le strategie approvate con {costs.per_side * 100:.2f}% per lato "
                         f"(prima {v['costs']['per_side'] * 100:.2f}%).", "working", "research")
                cache[tf] = self._datasets(md, tf)
            if cache[tf] is None:
                continue
            self.status("working", f"Verifica costi reali su {module.STRATEGY_ID}…")
            r = validate(module, cache[tf], costs, self.office.gates, s["research"],
                         self.office.risk.limits["max_exposure_per_asset"], v["n_trials_total"], tf,
                         self.office.risk.limits["risk_per_trade"], None)
            r.update({"strategy_id": module.STRATEGY_ID, "data_source": history_exchange, "timeframe": tf,
                      "audit_of": "validation", "validated_costs": v["costs"],
                      "audited_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            registry.save_cost_audit(module.STRATEGY_ID, costs.per_side, r)
            m = r["metrics"]
            self.say(f"{module.STRATEGY_ID} con i costi reali: {r['verdict']} — {m['trades']} trade OOS, "
                     f"PF {m['profit_factor']:.2f}, netto medio {m['expectancy_net'] * 100:+.2f}%/trade",
                     "ok" if r["verdict"] == "PASSED" else "blocked", "validation",
                     payload={"strategy_id": module.STRATEGY_ID, "verdict": r["verdict"], "cost_audit": True})
            out.append(r)
        return out

    def _diversification(self, module, datasets, costs, tf, alloc):
        """Per le strategie diversificanti: confronto con la strategia di riferimento sugli stessi mesi."""
        ref_id = getattr(module, "DIVERSIFIER_OF", None)
        cfg = self.office.gates.get("diversification")
        if not ref_id or not cfg:
            return None
        ref_val = registry.load_validation(ref_id)
        risk = self.office.risk.limits["risk_per_trade"]
        if ref_val is None or ref_val["verdict"] != "PASSED" or timeframe_of(by_id(ref_id), tf) != tf:
            return lambda oos: [{"key": "div_ref", "label": f"Riferimento {ref_id} disponibile", "value": 0,
                                 "threshold": 1, "passed": False, "fmt": "int"}]
        ref_trades = reconstruct_oos(by_id(ref_id), ref_val, datasets, costs)

        def checks(oos):
            d = diversification(ref_trades, oos, risk, alloc)
            corr = d["correlation"] if d["correlation"] == d["correlation"] else 1.0   # NaN = nessuna prova
            out = [{"key": "div_corr", "label": f"Correlazione mensile con {ref_id}", "value": corr,
                    "threshold": cfg["max_monthly_correlation"], "passed": corr <= cfg["max_monthly_correlation"],
                    "fmt": "num"}]
            if cfg.get("require_portfolio_sharpe_improvement"):
                out.append({"key": "div_sharpe", "label": f"Sharpe {ref_id} + nuova > {ref_id} da sola",
                            "value": d["sharpe_combined"], "threshold": d["sharpe_ref"],
                            "passed": d["sharpe_combined"] > d["sharpe_ref"], "fmt": "num"})
            return out
        return checks

    def _validate_one(self, module, datasets, costs, gates, alloc, tf, history_exchange) -> dict:
        s = self.settings
        self.status("working", f"Walk-forward su {module.STRATEGY_ID} ({module.NAME}, {tf})…")
        result = validate(module, datasets, costs, gates, s["research"], alloc, registry.total_trials() + self.extra_trials, tf,
                          self.office.risk.limits["risk_per_trade"],
                          self._diversification(module, datasets, costs, tf, alloc))
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
