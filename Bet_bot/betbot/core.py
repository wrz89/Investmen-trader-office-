"""L'ufficio sportivo: collega gli 8 agenti e fa girare il ciclo asincrono.

FEED (Sara) → STRATEGIE (Davide, Matteo) → RISK (Bruno) → BANCO (Pietro)
→ CHIUSURE e CLV (Pietro) → BANKROLL e compounding (Anna) → REPORT (Irene)
→ RIEPILOGO (Carlo)

Ordine di un ciclo:
  1. snapshot del feed (se i dati non sono affidabili: NESSUNA puntata)
  2. chiusura delle puntate finite; cash-out / green-up richiesti dalle strategie
  3. stato del bankroll + circuit breaker (kill switch, perdita giornaliera, serie negativa)
  4. nuove proposte → veto/sizing → piazzamento (le strategie in osservazione vanno "in ombra")
  5. metriche, equity, report giornaliero
"""
from __future__ import annotations

import asyncio
import time
import traceback
import uuid

from .agents.analista import Analista, TraderCavalli
from .agents.auditor import Auditor
from .agents.banco import Banco
from .agents.direttore import Direttore
from .agents.quote import Quote
from .agents.risk import RiskManager
from .agents.sentiment import Sentiment
from .agents.tesoriere import Tesoriere
from .bankroll import Bankroll, today
from .execution import Executor, Gates
from .config import DB_PATH, ensure_dirs, load_settings
from .feeds import make_feed
from .feeds.base import FeedError
from . import local_settings
from .notifier import Notifier
from .store import Store


class SportOffice:
    def __init__(self, overrides: dict | None = None, db_path=None, feed=None, connect_feed: bool = True):
        ensure_dirs()
        self.settings = load_settings(overrides)
        self.store = Store(db_path or DB_PATH)
        self.bankroll = Bankroll(self.store, self.settings["capital"]["initial"])
        self.feed = feed or (make_feed(self.settings) if connect_feed else None)
        self.cache: dict | None = None
        # nella simulazione (db dedicato) le notifiche restano in memoria: nessun messaggio al telefono
        self.notifier = Notifier(self.store, enabled=db_path is None)
        self.executor = Executor(self.settings)
        self.direttore = Direttore(self)
        self.quote = Quote(self)
        self.analista = Analista(self)
        self.cavalli = TraderCavalli(self)
        self.sentiment = Sentiment(self)
        self.risk = RiskManager(self)
        self.banco = Banco(self)
        self.tesoriere = Tesoriere(self)
        self.auditor = Auditor(self)
        self.agents = [self.direttore, self.quote, self.analista, self.cavalli, self.sentiment,
                       self.risk, self.banco, self.tesoriere, self.auditor]
        self.store.set("office_meta", {
            "mode": self.settings["mode"], "feed": self.settings["feed"]["provider"],
            "execution": (self.settings.get("execution") or {}).get("provider", "paper"),
            "live_strategies": self.settings.get("live_strategies") or [],
            "currency": self.settings["capital"]["currency"], "initial_capital": self.bankroll.initial_capital,
            "cycle_seconds": self.settings["cycle_seconds"],
            "agents": [{"key": a.key, "name": a.name, "role": a.role} for a in self.agents]})
        for a in self.agents:
            if not self.store.query("SELECT 1 FROM agent_status WHERE agent=?", (a.key,)):
                a.status("idle", "In attesa del primo ciclo.")

    async def run_cycle(self) -> dict:
        cycle_id = uuid.uuid4().hex[:8]
        self.store.set("cycle", {"id": cycle_id, "started": time.time(), "running": True})
        self.direttore.status("working", f"Ciclo {cycle_id} in corso.")
        strategies = self.direttore.strategies()
        self._live_gate_notice(strategies)
        try:
            snap = await self.quote.scan()
        except FeedError as exc:
            self.quote.say(f"Dati non disponibili: NESSUNA puntata in questo ciclo ({exc}).", "alert", "no_data",
                           level="ERROR")
            self._end_cycle()
            return {"ok": False, "error": str(exc)}

        try:
            self.sentiment.run(snap)          # il sentiment non deve mai fermare il ciclo
        except Exception as exc:
            self.sentiment.say(f"Errore nella lettura del sentiment: {exc}. Nessun effetto sulle puntate.", "alert",
                               "sentiment", level="WARN")

        # 2) chiusure e gestione delle puntate aperte
        settled = self.banco.settle(snap)
        open_bets = self.bankroll.open_bets()
        actions = self.analista.manage(snap, strategies, open_bets) + self.cavalli.manage(snap, strategies, open_bets)
        settled += self.banco.apply(actions)

        # 3) stato e circuit breaker (in live anche il confronto col saldo vero di Betfair)
        self._sync_live_balance()
        state = self.risk.portfolio_state()

        # 4) nuove proposte
        proposals = self.analista.propose(snap, strategies) + self.cavalli.propose(snap, strategies)
        proposals.sort(key=lambda p: -p["edge"])
        placed = 0
        for p in proposals:
            if p["strategy_status"] != "ATTIVA":
                self.banco.shadow(p)
                continue
            decision = self.risk.evaluate(p, snap, state)
            if decision["approved"]:
                if self.banco.place(p, decision, cycle_id, snap):
                    placed += 1
                state = self.risk.portfolio_state()
        if not proposals:
            self.risk.status("ok", "Nessuna proposta da valutare. Limiti e circuit breaker sotto controllo.")

        # 5) metriche e report
        state = self.risk.portfolio_state()
        self.store.set("risk_state", state)
        self.tesoriere.update(state)
        self._daily_report_if_needed()
        self.direttore.summary(len(proposals), placed, settled, state)
        self._end_cycle()
        return {"ok": True, "proposals": len(proposals), "placed": placed, "settled": settled,
                "bankroll": state["bankroll"]}

    def _sync_live_balance(self) -> None:
        """In live, ogni 10 cicli: saldo Betfair (disponibile + esposizione) contro il bankroll interno. Se il bot
        crede di avere più soldi di quelli veri (oltre 1 €), blocca le nuove puntate: meglio fermarsi che puntare
        su un conto diverso da come lo immagina."""
        if self.settings.get("mode") != "live" or (self.settings.get("execution") or {}).get("provider") != "betfair":
            return
        n = (self.store.get("live_sync_counter") or 0) + 1
        self.store.set("live_sync_counter", n)
        if n % 10 != 1:
            return
        try:
            funds = self.executor.client.account_funds()
        except Exception as exc:
            self.tesoriere.log(f"Saldo Betfair non leggibile ({exc}). Riprovo tra 10 cicli.", "WARN", "live_sync")
            return
        real = float(funds.get("availableToBetBalance") or 0) + abs(float(funds.get("exposure") or 0))
        self.store.set("live_balance", {"available": funds.get("availableToBetBalance"), "exposure": funds.get("exposure"),
                                        "total": real, "ts": time.time()})
        live_open = sum(b["stake"] for b in self.bankroll.open_bets() if b["mode"] == "live")
        if live_open > real + 1.0:
            if not self.store.get("kill_switch"):
                self.store.set("kill_switch", f"saldo Betfair {real:.2f} € inferiore alle puntate reali aperte {live_open:.2f} €")
                self.risk.say(f"KILL SWITCH: il saldo vero su Betfair ({real:.2f} €) non copre le puntate reali che il bot "
                              f"crede aperte ({live_open:.2f} €). Controlla il conto dal sito.", "alert", "kill_switch",
                              level="CRITICAL")

    def _live_gate_notice(self, strategies: list[dict]) -> None:
        """In modalità live, scrive (una volta) quali strategie useranno davvero soldi veri e perché le altre no."""
        if self.settings.get("mode") != "live":
            return
        status = {s["id"]: Gates.live_allowed(self.settings, s["id"]) for s in strategies}
        text = "; ".join(f"{sid}: {'SOLDI VERI' if ok else 'paper (' + why + ')'}" for sid, (ok, why) in status.items())
        if self.store.get("live_gate_text") != text:
            self.store.set("live_gate_text", text)
            self.direttore.say(f"Modalità LIVE richiesta. {text}.", "alert", "live_gate", level="WARN")

    def _daily_report_if_needed(self) -> None:
        last, day = self.store.get("last_report_day"), today()
        if last is None:
            self.store.set("last_report_day", day)
        elif last != day:
            self.auditor.daily_report(last)
            self.store.set("last_report_day", day)

    def _end_cycle(self) -> None:
        c = self.store.get("cycle", {})
        c.update({"running": False, "ended": time.time(), "next": time.time() + self.settings["cycle_seconds"]})
        self.store.set("cycle", c)

    async def run_forever(self) -> None:
        """Il loop: un ciclo ogni cycle_seconds. Un errore non porta mai a puntare "alla cieca"."""
        from . import system
        from .telegram_bot import TelegramCommands
        TelegramCommands(self).start()            # comandi dal telefono: /stato /stop /pausa …
        while True:
            system.keep_awake(local_settings.load().get("keep_awake", True))
            started = time.monotonic()
            try:
                await self.run_cycle()
            except Exception as exc:
                self.direttore.say(f"Errore nel ciclo: {exc}. Nessuna puntata fino al prossimo ciclo.", "alert",
                                   "error", level="ERROR", payload={"traceback": traceback.format_exc()[-2000:]})
                self._end_cycle()
            await asyncio.sleep(max(1.0, self.settings["cycle_seconds"] - (time.monotonic() - started)))

