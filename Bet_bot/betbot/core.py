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
from .config import DB_LIVE_PATH, DB_PATH, STOP_FILE, ensure_dirs, load_settings
from .feeds import make_feed
from .feeds.base import FeedError
from . import local_settings
from .notifier import Notifier
from .store import Store


class SportOffice:
    def __init__(self, overrides: dict | None = None, db_path=None, feed=None, connect_feed: bool = True):
        ensure_dirs()
        self.settings = load_settings(overrides)
        live = self.settings.get("mode") == "live"
        # soldi veri e soldi finti non si mescolano mai: due database, due bankroll
        self.store = Store(db_path or (DB_LIVE_PATH if live else DB_PATH))
        self.client = None
        uses_betfair = self.settings["feed"]["provider"] == "betfair" or (self.settings.get("execution") or {}).get("provider") == "betfair"
        if uses_betfair and feed is None and connect_feed:
            from .feeds.betfair import BetfairClient
            self.client = BetfairClient(local_settings.load()["betfair"])      # una sola sessione per tutto il bot
        initial = self.settings["capital"]["initial"]
        if live and self.store.get("cash") is None and self.client is not None:
            try:                                                                  # in live si parte dal saldo vero
                initial = float(self.client.account_funds().get("availableToBetBalance") or initial)
            except Exception:
                pass
        self.bankroll = Bankroll(self.store, initial)
        self.feed = feed or (make_feed(self.settings) if connect_feed else None)
        if self.client is not None:
            for f in (self.feed, getattr(self.feed, "primary", None)):
                if f is not None and hasattr(f, "client") and f.__class__.__name__ == "BetfairFeed":
                    f.client = self.client
            self.client.start_keepalive()
        self.cache: dict | None = None
        # nella simulazione (db dedicato) le notifiche restano in memoria: nessun messaggio al telefono
        self.notifier = Notifier(self.store, enabled=db_path is None)
        self.executor = Executor(self.settings, client=self.client, store=self.store)
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
        self._watch_open_markets()
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
        open_bets = self.bankroll.open_bets() + self.bankroll.open_shadow_trades()   # anche i trade ombra vanno gestiti
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
                self.banco.shadow(p, snap)
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

    def _watch_open_markets(self) -> None:
        """Le partite con posizioni aperte restano nel feed fino alla chiusura del mercato."""
        feed = getattr(self.feed, "primary", self.feed)
        if hasattr(feed, "watch"):
            ids = {b["match_id"] for b in self.store.query("SELECT match_id FROM bets WHERE status='OPEN'")}
            ids |= {b["match_id"] for b in self.store.query("SELECT match_id FROM shadow_bets WHERE status='OPEN'")}
            feed.watch(ids)

    def reconcile_live(self) -> None:
        """All'avvio in live: ogni ordine vero abbinato su Betfair deve corrispondere a una puntata del registro.
        Un ordine sconosciuto (crash a metà invio, ordine messo a mano) blocca le nuove puntate finché non lo guardi."""
        if self.settings.get("mode") != "live" or self.client is None:
            return
        try:
            current = self.client.current_orders()
        except Exception as exc:
            self.risk.say(f"Riconciliazione ordini non riuscita ({exc}). Riprovo al prossimo avvio.", "alert", "reconcile",
                          level="WARN")
            return
        known = {o["bet_id"] for o in self.store.query("SELECT bet_id FROM orders WHERE bet_id IS NOT NULL")}
        unknown = [o for o in current if o.get("betId") not in known and float(o.get("sizeMatched") or 0) > 0]
        pending = self.store.query("SELECT ref FROM orders WHERE status IN ('PENDING', 'UNKNOWN')")
        if unknown or pending:
            what = []
            if unknown:
                what.append(f"{len(unknown)} ordini abbinati su Betfair che il bot non conosce")
            if pending:
                what.append(f"{len(pending)} ordini dal esito incerto")
            reason = "riconciliazione: " + " e ".join(what)
            self.store.set("kill_switch", reason)
            self.risk.say(f"KILL SWITCH: {reason}. Controlla 'Le mie scommesse' su betfair.it, poi resetta dal PC.",
                          "alert", "kill_switch", level="CRITICAL", payload={"unknown": unknown[:10]})
        else:
            self.risk.log(f"Riconciliazione ordini: {len(current)} ordini su Betfair, tutti noti al registro.", "INFO", "reconcile")

    async def manage_trades_fast(self) -> int:
        """Ciclo veloce per i trade aperti (back→lay): stop e uscite a tempo non possono aspettare 60 secondi."""
        trades = [b for b in self.bankroll.open_bets() + self.bankroll.open_shadow_trades()
                  if b["market"] in ("exchange_trade", "exchange_win")]
        if not trades:
            return 0
        try:
            snap = await self.feed.fetch()
        except FeedError:
            return 0
        self.cache = snap
        if self.store.get("close_all_requested"):
            self.store.set("close_all_requested", False)
            actions = [{"bet_id": b["id"], "action": "hedge", "urgent": True, "reason": "chiusura chiesta da Telegram",
                        "price": ((snap.get("matches", {}).get(b["match_id"]) or {}).get("exchange", {}).get(b["selection"], {})
                                  or {}).get("lay") or b["odds"]} for b in trades if b["mode"] != "shadow"]
            return self.banco.apply(actions)
        strategies = [s for s in self.direttore.strategies() if s["kind"] == "exchange"]
        actions = self.cavalli.manage(snap, strategies, trades)
        return self.banco.apply(actions)

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
        """Il loop: un ciclo ogni cycle_seconds; tra un ciclo e l'altro, ogni `fast_seconds`, solo la gestione dei
        trade aperti. Un errore non porta mai a puntare "alla cieca"."""
        from . import system
        from .telegram_bot import TelegramCommands
        TelegramCommands(self).start()            # comandi dal telefono: /stato /stop /pausa …
        self.reconcile_live()
        n_open = len(self.bankroll.open_bets())
        self.direttore.say(f"Bet_bot avviato in modalità {self.settings['mode'].upper()}: bankroll "
                           f"{self.bankroll.total:.2f} €, {n_open} posizioni aperte da riprendere.", "ok", "startup",
                           level="WARN" if n_open else "INFO")
        fast = float(self.settings.get("fast_seconds", 5))
        STOP_FILE.unlink(missing_ok=True)
        while True:
            system.keep_awake(local_settings.load().get("keep_awake", True))
            started = time.monotonic()
            try:
                await self.run_cycle()
            except Exception as exc:
                self.direttore.say(f"Errore nel ciclo: {exc}. Nessuna puntata fino al prossimo ciclo.", "alert",
                                   "error", level="ERROR", payload={"traceback": traceback.format_exc()[-2000:]})
                self._end_cycle()
            while time.monotonic() - started < self.settings["cycle_seconds"]:
                if STOP_FILE.exists():
                    await self.shutdown()
                    return
                await asyncio.sleep(fast)
                try:
                    await self.manage_trades_fast()
                except Exception as exc:
                    self.cavalli.log(f"Gestione veloce dei trade: errore {exc}", "WARN", "error")

    async def shutdown(self, timeout_s: float = 120.0) -> None:
        """Spegnimento ordinato: niente nuove puntate, trade aperti chiusi (urgenti), poi uscita."""
        self.store.set("kill_switch", self.store.get("kill_switch") or "spegnimento richiesto")
        self.direttore.say("Spegnimento richiesto: nessuna nuova puntata, chiudo i trade aperti.", "alert", "shutdown",
                           level="WARN")
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            trades = [b for b in self.bankroll.open_bets() if b["market"] in ("exchange_trade", "exchange_win")]
            if not trades:
                break
            try:
                snap = await self.feed.fetch()
                self.cache = snap
                self.banco.apply([{"bet_id": b["id"], "action": "hedge", "urgent": True,
                                   "price": ((snap.get("matches", {}).get(b["match_id"]) or {}).get("exchange", {})
                                            .get(b["selection"], {}) or {}).get("lay") or b["odds"],
                                   "reason": "spegnimento: chiusura urgente"} for b in trades])
            except Exception:
                pass
            await asyncio.sleep(3)
        self.store.set("kill_switch", None if self.store.get("kill_switch") == "spegnimento richiesto" else self.store.get("kill_switch"))
        left = len(self.bankroll.open_bets())
        self.direttore.say(f"Bet_bot spento. Posizioni ancora aperte: {left} (si riprendono al prossimo avvio).",
                           "idle", "shutdown", level="WARN")
        STOP_FILE.unlink(missing_ok=True)

