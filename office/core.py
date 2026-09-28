"""L'ufficio: collega i 7 agenti e fa girare il ciclo operativo.

MARKET DATA → SCANNER → OPPORTUNITY → STRATEGY EVALUATION → QUANT VALIDATION
→ RISK CHECK → EXECUTION → TRADE → AUDIT → PERFORMANCE ANALYSIS → STRATEGY UPDATE
"""
from __future__ import annotations

import time
import traceback
import uuid

from .account import PaperAccount, today
from .agents.auditor import Auditor
from .agents.execution import Execution
from .agents.market_scanner import MarketScanner
from .agents.portfolio_manager import PortfolioManager
from .agents.quant_researcher import QuantResearcher
from .agents.risk_manager import RiskManager
from .agents.strategy_researcher import StrategyResearcher
from .config import DB_PATH, ensure_dirs, load_settings, load_yaml
from .market import DataError, MarketData
from . import local_settings, system
from .notifier import Notifier
from .store import Store
from .strategies import timeframe_of


class Office:
    def __init__(self, overrides: dict | None = None, connect_market: bool = True):
        ensure_dirs()
        self.settings = load_settings(overrides)
        self.gates = load_yaml("quant_gates.yaml")
        self.retired = (load_yaml("strategy_lifecycle.yaml").get("retired") or {})
        self.store = Store(DB_PATH)
        self.cycle_id = None
        self.last_risk_state: dict | None = None
        ex = self.settings["exchange"]
        self.market = MarketData(ex["data"], ex.get("options")) if connect_market else None
        dctx = ex.get("derivatives_context")
        self.derivatives = MarketData(dctx, timeout_ms=5000) if (dctx and connect_market) else None
        self.account = PaperAccount(self.store, self.settings["capital"]["initial"])
        self.notifier = Notifier(self.store)

        self.pm = PortfolioManager(self)
        self.scanner = MarketScanner(self)
        self.researcher = StrategyResearcher(self)
        self.quant = QuantResearcher(self)
        self.risk = RiskManager(self)
        self.execution = Execution(self)
        self.auditor = Auditor(self)
        self.agents = [self.pm, self.scanner, self.researcher, self.quant,
                       self.risk, self.execution, self.auditor]
        self.store.set("office_meta", {
            "mode": self.settings["mode"], "exchange": ex["data"], "universe": self.settings["universe"],
            "timeframe": self.settings["timeframe"], "currency": self.settings["capital"]["currency"],
            "initial_capital": self.account.initial_capital, "cycle_seconds": self.settings["cycle_seconds"],
            "agents": [{"key": a.key, "name": a.name, "role": a.role} for a in self.agents],
        })
        for a in self.agents:
            if not self.store.query("SELECT 1 FROM agent_status WHERE agent=?", (a.key,)):
                a.status("idle", "In attesa del primo ciclo.")

    # ── ricerca (offline) ────────────────────────────────────
    def research(self, history_exchange: str | None = None) -> list[dict]:
        entries = self.researcher.sync_registry()
        results = self.quant.research(entries, history_exchange or self.settings["exchange"]["history"])
        self.pm.authorize(entries)
        return results

    # ── ciclo operativo ──────────────────────────────────────
    def run_cycle(self) -> dict:
        self.cycle_id = uuid.uuid4().hex[:8]
        self.store.set("cycle", {"id": self.cycle_id, "started": time.time(), "running": True})
        self.pm.say(f"Avvio ciclo {self.cycle_id}.", "working", "cycle")

        entries = self.researcher.sync_registry()
        strategies_state = self.pm.authorize(entries)

        try:
            snapshot = self.scanner.scan()
        except DataError as exc:
            self.pm.say(f"Dati non disponibili: NON OPERO in questo ciclo ({exc}).", "alert", "no_trade",
                        level="ERROR")
            self._end_cycle()
            return {"ok": False}

        prices = {s: v["bid"] for s, v in snapshot["symbols"].items() if v.get("bid")}

        # 1) gestione posizioni aperte (uscite prima degli ingressi)
        by_id = {s["module"].STRATEGY_ID: s for s in strategies_state}
        for pos in self.account.open_positions():
            self._manage_position(pos, snapshot, by_id)

        # 2) nuove opportunità
        opps = self.scanner.opportunities(snapshot, strategies_state)
        fills = 0
        for opp in opps:
            st = by_id[opp["strategy_id"]]
            if st["validation"]:
                m = st["validation"]["metrics"]
                self.quant.say(f"{opp['strategy_id']}: su {m['trades']} trade fuori campione win rate "
                               f"{m['win_rate']:.0%}, netto medio {m['expectancy_net'] * 100:+.2f}% "
                               f"— esito validazione {st['validation']['verdict']}.", "ok", "quant_check")
            else:
                self.quant.say(f"{opp['strategy_id']}: nessuna validazione disponibile.", "blocked", "quant_check")
            decision = self.risk.evaluate(opp, snapshot, self.account, st["validation"])
            if decision["approved"]:
                if self.execution.buy(opp, decision, snapshot, self.account):
                    fills += 1
        if not opps:
            self.risk.status("ok", "Nessun ordine da valutare. Limiti e kill switch sotto controllo.")
            self.execution.status("idle", "Nessun ordine approvato: resto fermo.")

        # 3) audit, performance, aggiornamento strategie
        risk_state = self.risk.portfolio_state(self.account, prices)
        self.last_risk_state = risk_state
        self.store.set("risk_state", risk_state)
        self.auditor.mark_equity(risk_state)
        self.pm.review(strategies_state)
        self.pm.summary(strategies_state, risk_state, len(opps), fills)
        self._daily_report_if_needed()
        self._end_cycle()
        return {"ok": True, "opportunities": len(opps), "fills": fills}

    def _manage_position(self, pos: dict, snapshot: dict, by_id: dict) -> None:
        info = snapshot["symbols"].get(pos["symbol"], {})
        st = by_id.get(pos["strategy_id"])
        tf = timeframe_of(st["module"], self.settings["timeframe"]) if st else self.settings["timeframe"]
        df = self.scanner.candles_for(snapshot, pos["symbol"], tf) if info.get("bid") else None
        if df is None:
            self.execution.say(f"Posizione {pos['symbol']}: dati mancanti, non posso gestirla ora.",
                               "alert", "error", level="ERROR")
            return
        entry_ms = _iso_to_ms(pos["entry_ts"])
        since_entry = df[df["ts"] >= entry_ms]
        reason, stop_px = None, None
        hit = since_entry[since_entry["low"] <= pos["stop"]]
        if len(hit):
            reason, stop_px = "stop loss", min(pos["stop"], float(hit.iloc[0]["open"]))
        elif info["bid"] <= pos["stop"]:
            reason = "stop loss"
        else:
            if st:
                params = (st["validation"] or {}).get("chosen_params") or {}
                if params:
                    sig = st["module"].generate(df, params)
                    bars_held = len(since_entry)
                    if len(since_entry) and bool(sig["exit"].iloc[-1]):
                        reason = "segnale di uscita della strategia"
                    elif params.get("max_hold") and bars_held >= params["max_hold"]:
                        reason = f"uscita a tempo ({bars_held} candele)"
        if not reason:
            return
        fill = self.execution.sell(pos, snapshot, self.account, reason, stop_px)
        self.auditor.record_trade(pos, fill, reason)

    def _daily_report_if_needed(self) -> None:
        last = self.store.get("last_report_day")
        day = today()
        if last is None:
            self.store.set("last_report_day", day)
        elif last != day:
            self.auditor.daily_report(last)
            self.store.set("last_report_day", day)

    def _end_cycle(self) -> None:
        cycle = self.store.get("cycle", {})
        cycle.update({"running": False, "ended": time.time(),
                      "next": time.time() + self.settings["cycle_seconds"]})
        self.store.set("cycle", cycle)

    def run_forever(self) -> None:
        while True:
            system.keep_awake(local_settings.load().get("keep_awake", True))
            try:
                self.run_cycle()
            except Exception as exc:          # un errore non deve mai portare a operare "alla cieca"
                self.pm.say(f"Errore nel ciclo: {exc}. Nessuna operazione fino al prossimo ciclo.",
                            "alert", "error", level="ERROR",
                            payload={"traceback": traceback.format_exc()[-2000:]})
                self._end_cycle()
            time.sleep(self.settings["cycle_seconds"])


def _iso_to_ms(ts: str) -> int:
    from datetime import datetime
    return int(datetime.fromisoformat(ts).timestamp() * 1000)
