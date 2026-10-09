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
import json
import time
import traceback
import uuid

from .agents.analista import Analista, TraderCavalli
from .agents.auditor import Auditor
from .agents.banco import Banco
from .agents.coach import Coach
from .agents.direttore import Direttore
from .agents.quote import Quote
from .agents.risk import RiskManager
from .agents.sentiment import Sentiment
from .agents.tesoriere import Tesoriere
from . import clock
from .bankroll import Bankroll, today
from .execution import Executor, Gates
from .config import DB_LIVE_PATH, DB_PATH, RESTART_FILE, STOP_FILE, ensure_dirs, load_settings
from .feeds import make_feed
from .feeds.base import FeedError
from . import local_settings
from .notifier import Notifier
from .store import Store

SHUTDOWN_REASON = "spegnimento richiesto"      # vecchie versioni: lo spegnimento si scriveva come kill switch
ORDER_CHECK_AFTER_S = 120                      # un ordine dall'esito incerto si cerca su Betfair dopo 2 minuti


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
        allow_reset = not live            # in live il bankroll non torna MAI a capital.initial (riavvii, `stato`, …)
        if live and self.client is not None and (
                self.store.get("cash") is None or not self.store.query("SELECT 1 FROM bets WHERE mode!='shadow' LIMIT 1")):
            try:                          # nessuna puntata vera ancora: il bankroll è il saldo vero di Betfair
                balance = self.client.account_funds().get("availableToBetBalance")
                if balance is not None:
                    initial, allow_reset = float(balance), True
            except Exception:
                pass
        self.bankroll = Bankroll(self.store, initial, allow_reset=allow_reset)
        self.shutting_down = False        # spegnimento ordinato in corso: solo in memoria, mai nel database
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
        self.coach = Coach(self)
        self.agents = [self.direttore, self.quote, self.analista, self.cavalli, self.sentiment,
                       self.risk, self.banco, self.tesoriere, self.auditor, self.coach]
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
        self.resolve_orders()                 # ordini veri dall'esito incerto: si chiede a Betfair com'è andata
        self._watch_open_markets()
        try:
            snap = await self.quote.scan()
        except FeedError as exc:
            self.quote.say(f"Dati non disponibili: NESSUNA puntata in questo ciclo ({exc}).", "alert", "no_data",
                           level="ERROR")
            self._end_cycle()
            return {"ok": False, "error": str(exc)}
        fetched = time.monotonic()
        snap["fetched_mono"] = fetched        # il Risk Manager misura l'età delle quote fino al momento della valutazione

        try:                                  # in un thread: le letture RSS non bloccano il loop (stream, ciclo veloce)
            await asyncio.to_thread(self.sentiment.run, snap)     # il sentiment non deve mai fermare il ciclo
        except Exception as exc:
            self.sentiment.say(f"Errore nella lettura del sentiment: {exc}. Nessun effetto sulle puntate.", "alert",
                               "sentiment", level="WARN")

        try:                                  # diagnosi dei lay: quante partite, quanti hanno Pinnacle fresco (per `perche`)
            from .perche import lay_diagnosis
            self.store.set("lay_diag", lay_diagnosis(snap, self.settings))
        except Exception:
            pass
        try:                                  # partite di betfair.it adatte alla copertura (pagina "Matched betting")
            from .matched import lay_board
            rows = lay_board(snap)
            self.store.set("mb_lay_board", {"ts": time.time(), "rows": rows})
            self._matched_telegram(rows)
        except Exception:
            pass
        # 2) chiusure e gestione delle puntate aperte
        settled = self.banco.settle(snap)
        open_bets = self.bankroll.open_bets() + self.bankroll.open_shadow_trades()   # anche i trade ombra vanno gestiti
        actions = self.analista.manage(snap, strategies, open_bets) + self.cavalli.manage(snap, strategies, open_bets)
        settled += self.banco.apply(actions)
        try:
            self.coach.run(snap)              # autopsie e lezioni: l'allenatore non deve mai fermare il ciclo
        except Exception as exc:
            self.coach.log(f"Errore dell'allenatore: {exc}. Nessun effetto sulle puntate.", "WARN", "error")

        # 3) stato e circuit breaker (in live anche il confronto col saldo vero di Betfair)
        self._sync_live_balance()
        state = self.risk.portfolio_state()

        # 4) nuove proposte, mai su prezzi vecchi: se sentiment, chiusure e saldo hanno preso troppo tempo si rileggono
        max_age = float(self.risk.limits.get("max_odds_age_seconds", 180))
        if time.monotonic() - fetched > max_age:
            try:
                snap = await self.quote.scan()
                snap["fetched_mono"] = time.monotonic()
                self.quote.log(f"Ciclo lento (oltre {max_age:.0f} s): prezzi riletti prima delle nuove puntate.",
                               "INFO", "scan")
            except FeedError as exc:
                self.quote.say(f"Prezzi non più freschi e non rileggibili ({exc}): nessuna nuova puntata in questo "
                               "ciclo.", "alert", "no_data", level="WARN")
                snap = None
        proposals = [] if snap is None else self.analista.propose(snap, strategies) + self.cavalli.propose(snap, strategies)
        proposals.sort(key=lambda p: -p["edge"])
        placed = 0
        for p in proposals:
            # in osservazione, oppure in live senza via libera ai soldi veri (o con prezzi ritardati): solo in ombra,
            # prima del Risk Manager, così il bankroll vero non viene mai toccato
            # lay d'apertura spento: solo in ombra. Eccezione: i lay del divertimento (S10 v2), rischio ≤ 2 €
            lay_off = (p.get("side") == "LAY" and not p.get("fun")
                       and not (self.settings.get("execution") or {}).get("lay_apertura"))
            if (p["strategy_status"] != "ATTIVA" or lay_off              # lay d'apertura spento: solo in ombra
                    or self.executor.route(p, snap) == "shadow"):
                self.banco.shadow(p, snap)
                continue
            decision = self.risk.evaluate(p, snap, state)
            if decision.get("coach_blocked"):
                p["_blocked_by"] = decision["coach_blocked"]     # bloccata da una lezione: la si segue in ombra per
                self.banco.shadow(p, snap)                       # misurare se la regola ha davvero evitato perdite
                continue
            if decision["approved"]:
                if self.banco.place(p, decision, cycle_id, snap):
                    placed += 1
                state = self.risk.portfolio_state()
        if not proposals:
            self.risk.status("ok", "Nessuna proposta da valutare. Limiti e circuit breaker sotto controllo.")

        self._watch_open_markets()            # il catalogo delle posizioni appena aperte si salva subito

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
        """Le partite con posizioni aperte restano nel feed fino alla chiusura del mercato. La loro voce di catalogo
        Betfair si salva nel database (kv 'bf_cat:<id>'): dopo un riavvio listMarketCatalogue non restituisce più i
        mercati iniziati da ore o già chiusi, e senza catalogo la puntata non si regolerebbe mai."""
        feed = getattr(self.feed, "primary", self.feed)
        if not hasattr(feed, "watch"):
            return
        ids = {b["match_id"] for b in self.store.query("SELECT match_id FROM bets WHERE status='OPEN'")}
        ids |= {b["match_id"] for b in self.store.query("SELECT match_id FROM shadow_bets WHERE status='OPEN'")}
        cat = getattr(feed, "cat", None) or {}
        saved = {}
        for mid in (i for i in ids if str(i).startswith("1.")):
            key = f"bf_cat:{mid}"
            if mid in cat:
                if self.store.get(key) is None:
                    self.store.set(key, cat[mid])
                saved[mid] = cat[mid]
            elif self.store.get(key) is not None:
                saved[mid] = self.store.get(key)
        for r in self.store.query("SELECT key FROM kv WHERE key LIKE 'bf_cat:%'"):
            if r["key"][len("bf_cat:"):] not in ids:                   # mercato senza più posizioni: voce inutile
                self.store.execute("DELETE FROM kv WHERE key=?", (r["key"],))
        feed.watch(ids, saved)

    def resolve_orders(self, min_age_s: float = ORDER_CHECK_AFTER_S) -> int:
        """Ordini veri rimasti PENDING/UNKNOWN/UNCONFIRMED (risposta persa, crash a metà invio): passati 2 minuti si chiede a
        Betfair com'è andata, per customerOrderRef.
          • non esiste → NOT_FOUND: un fill-or-kill non abbinato sparisce, quindi nessuna posizione;
          • abbinato → MATCHED con bet_id. Un BACK abbinato è una posizione vera che il registro non conosce:
            kill switch con un messaggio chiaro. Un LAY di chiusura abbinato chiude la sua puntata (senza nuovi ordini).
        Se Betfair non risponde la riga resta com'è e si riprova al ciclo dopo."""
        if self.settings.get("mode") != "live" or self.client is None:
            return 0
        from datetime import datetime
        from .store import now_iso
        rows = self.store.query("SELECT * FROM orders WHERE status IN ('PENDING', 'UNKNOWN', 'UNCONFIRMED') ORDER BY id")
        done = 0
        for o in rows:
            try:
                age = clock.now() - datetime.fromisoformat(o["ts"]).timestamp()
            except (TypeError, ValueError):
                age = min_age_s
            if age < min_age_s:
                continue
            try:
                found = self.client.current_orders(order_refs=[o["ref"]])
                if not found and hasattr(self.client, "cleared_by_refs"):    # mercato già regolato
                    found = self.client.cleared_by_refs([o["ref"]])
            except Exception as exc:
                self.risk.log(f"Verifica dell'ordine {o['ref']} non riuscita ({exc}). Riprovo al prossimo ciclo.",
                              "WARN", "reconcile")
                continue
            f = found[0] if found else None
            matched = float((f or {}).get("sizeMatched") or (f or {}).get("sizeSettled") or 0.0)
            if (f or {}).get("status") in ("LAPSED", "CANCELLED"):
                matched = 0.0
            what = f"{o['side']} {o['size']:.2f} € a {o['price']:.2f} sul mercato {o['market_id']}"
            if f is None or matched <= 0:
                status = "NOT_FOUND" if f is None else "KILLED"
                self.store.execute("UPDATE orders SET status=?, matched=0, error=?, updated=? WHERE ref=?",
                                   (status, "verificato su Betfair: non abbinato", now_iso(), o["ref"]))
                self.risk.say(f"Ordine {what} verificato su Betfair: NON abbinato, nessuna posizione aperta. "
                              + ("Il kill switch resta finché non lo resetti dal PC." if self.store.get("kill_switch") else ""),
                              "ok", "reconcile", level="WARN")
                done += 1
                continue
            avg = float(f.get("averagePriceMatched") or f.get("priceMatched") or o["price"])
            self.store.execute("UPDATE orders SET status='MATCHED', bet_id=?, matched=?, avg_price=?, error=?, updated=? "
                               "WHERE ref=?", (f.get("betId"), matched, avg, "verificato su Betfair: abbinato", now_iso(),
                                              o["ref"]))
            done += 1
            if o["side"] == "LAY" and (o.get("role") or "close") == "close":
                closed = self.banco.close_matched_lays("lay di chiusura ritrovato abbinato su Betfair")
                self.risk.say(f"Il lay di chiusura {what} risulta ABBINATO su Betfair (bet {f.get('betId')})"
                              + (": puntata chiusa con quel lay, senza nuovi ordini" if closed else "")
                              + ". Il kill switch resta: controlla su betfair.it e resetta dal PC.",
                              "alert", "reconcile", level="WARN")
                continue
            reason = (f"ordine {o['side']} {o['ref']} abbinato su Betfair (bet {f.get('betId')}, {matched:.2f} € a {avg:.2f}) "
                      "ma assente dal registro delle puntate")
            self.store.set("kill_switch", reason)
            self._remember_reported([f"ref:{o['ref']}"])
            self.risk.say(f"KILL SWITCH: la puntata {what} risulta ABBINATA su Betfair (bet {f.get('betId')}, "
                          f"{matched:.2f} € a {avg:.2f}) ma il bot non l'ha registrata: non la gestisce e non la regola. "
                          "Guardala in 'Le mie scommesse' su betfair.it, poi resetta dal PC.", "alert", "kill_switch",
                          level="CRITICAL")
        return done

    def _remember_reported(self, keys) -> None:
        self.store.set("reconcile_reported", sorted(set(self.store.get("reconcile_reported") or []) | set(keys)))

    def reconcile_live(self) -> None:
        """All'avvio in live: Betfair, tabella orders e libro delle puntate devono raccontare la stessa storia.
          • ordini dall'esito incerto: prima si cercano su Betfair (resolve_orders);
          • LAY di chiusura abbinato ma puntata ancora OPEN (crash prima della chiusura nel registro): si chiude la
            puntata con quel lay, senza mandarne un altro;
          • BACK abbinato senza puntata nel registro, ordine abbinato su Betfair che il bot non conosce (crash a metà,
            ordine messo a mano) o esito ancora incerto: kill switch finché non lo guardi.
        Una discrepanza già segnalata e poi resettata a mano dal PC non riaccende il kill switch a ogni avvio."""
        if self.settings.get("mode") != "live" or self.client is None:
            return
        self.resolve_orders()
        closed = self.banco.close_matched_lays("riavvio: lay di chiusura già abbinato su Betfair, nessun nuovo ordine")
        if closed:
            self.risk.say(f"Riconciliazione: {closed} puntate chiuse con il lay già abbinato su Betfair prima del riavvio.",
                          "ok", "reconcile", level="WARN")
        problems: dict[str, str] = {}
        try:
            current = self.client.current_orders()
        except Exception as exc:
            current = None
            self.risk.say(f"Riconciliazione ordini non riuscita ({exc}). Riprovo al prossimo avvio.", "alert", "reconcile",
                          level="WARN")
        if current is not None:
            known = {o["bet_id"] for o in self.store.query("SELECT bet_id FROM orders WHERE bet_id IS NOT NULL")}
            for o in current:
                if o.get("betId") not in known and float(o.get("sizeMatched") or 0) > 0:
                    problems[f"bf:{o.get('betId')}"] = f"ordine abbinato su Betfair che il bot non conosce (bet {o.get('betId')})"
        booked = set()
        for b in self.store.query("SELECT extra FROM bets WHERE mode='live' AND extra IS NOT NULL"):
            booked.add(((json.loads(b["extra"]) or {}).get("betfair") or {}).get("bet_id"))
        for o in self.store.query("SELECT * FROM orders WHERE (side='BACK' OR role='open') AND status='MATCHED' "
                                  "AND bet_id IS NOT NULL"):
            if o["bet_id"] not in booked:
                problems[f"ref:{o['ref']}"] = (f"{o['side']} d'apertura abbinato su Betfair (bet {o['bet_id']}, {o['matched'] or 0:.2f} € a "
                                               f"{o['avg_price'] or o['price']:.2f}) senza puntata nel registro")
        pending = self.store.query("SELECT ref FROM orders WHERE status IN ('PENDING', 'UNKNOWN', 'UNCONFIRMED')")
        reported = set(self.store.get("reconcile_reported") or [])
        new = [k for k in problems if k not in reported]
        if new or pending:
            what = [problems[k] for k in new[:3]] + ([f"altre {len(new) - 3} discrepanze"] if len(new) > 3 else [])
            if pending:
                what.append(f"{len(pending)} ordini dall'esito incerto (li cerco di nuovo su Betfair tra 2 minuti)")
            reason = "riconciliazione: " + "; ".join(what)
            self.store.set("kill_switch", reason)
            self.risk.say(f"KILL SWITCH: {reason}. Controlla 'Le mie scommesse' su betfair.it, poi resetta dal PC.",
                          "alert", "kill_switch", level="CRITICAL", payload={"problems": list(problems.values())[:10]})
        elif problems:
            self.risk.say(f"Riconciliazione: {len(problems)} discrepanze già segnalate e verificate a mano (kill switch "
                          "resettato dal PC): non blocco di nuovo.", "ok", "reconcile", level="WARN",
                          payload={"problems": list(problems.values())[:10]})
        else:
            self.risk.log(f"Riconciliazione ordini: {len(current or [])} ordini su Betfair, tutti noti al registro.",
                          "INFO", "reconcile")
        if problems:
            self._remember_reported(problems)

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

    def _sync_live_balance(self, force: bool = False) -> None:
        """In live, all'avvio e ogni 10 cicli: saldo Betfair (disponibile + esposizione) contro il bankroll interno
        TOTALE. Se il bot crede di avere più soldi di quelli veri (oltre 1 €), blocca le nuove puntate: meglio
        fermarsi che puntare su un conto diverso da come lo immagina. Il contrario (sul conto c'è di più, per
        esempio altri soldi tuoi) è solo un'informazione: il bot continua a usare il suo bankroll."""
        if self.settings.get("mode") != "live" or (self.settings.get("execution") or {}).get("provider") != "betfair":
            return
        if not force:
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
        total = self.bankroll.total
        pending = self._unsettled_green()
        self.store.set("live_balance", {"available": funds.get("availableToBetBalance"), "exposure": funds.get("exposure"),
                                        "total": real, "bankroll": total, "unsettled_green": pending, "ts": time.time()})
        if total > real + pending + 1.0:
            if not self.store.get("kill_switch"):
                self.store.set("kill_switch", f"bankroll interno {total:.2f} € superiore al saldo vero Betfair {real:.2f} €")
                self.risk.say(f"KILL SWITCH: il bot crede di avere {total:.2f} € ma su Betfair ce ne sono {real:.2f} "
                              "(disponibile + esposizione). Controlla il conto dal sito, poi resetta dal PC.", "alert",
                              "kill_switch", level="CRITICAL")
        elif real > total + 1.0:
            extra = round(real - total, 2)
            last = self.store.get("live_balance_extra")
            if last is None or abs(extra - float(last)) > 1.0:          # si scrive solo quando la differenza cambia
                self.store.set("live_balance_extra", extra)
                self.tesoriere.log(f"Sul conto Betfair ci sono {extra:.2f} € in più del bankroll del bot ({real:.2f} € "
                                   f"contro {total:.2f} €): altri soldi tuoi o vincite non ancora regolate. Solo "
                                   "un'informazione: il bot punta sul suo bankroll.", "INFO", "live_sync")

    def _unsettled_green(self) -> float:
        """Profitti già "chiusi" con un lay (green-up) ma non ancora accreditati da Betfair: finché il mercato non è
        regolato il saldo vero non li contiene (disponibile + esposizione = saldo di prima), mentre il bankroll
        interno sì. Senza contarli, qualche green-up in attesa di regolamento farebbe scattare un kill switch falso.
        Si contano solo le chiusure delle ultime 24 ore su partite iniziate da meno di 6 ore (o senza orario)."""
        from datetime import datetime, timezone
        now = clock.now()
        since = datetime.fromtimestamp(now - 86400, timezone.utc).isoformat(timespec="seconds")
        rows = self.store.query("SELECT b.pnl, m.kickoff FROM bets b LEFT JOIN matches m ON m.match_id = b.match_id "
                                "WHERE b.mode='live' AND b.status='HEDGED' AND b.pnl > 0 AND b.settled_ts >= ?", (since,))
        total = 0.0
        for r in rows:
            try:
                ko = datetime.fromisoformat(str(r["kickoff"]).replace("Z", "+00:00")).timestamp() if r["kickoff"] else None
            except ValueError:
                ko = None
            if ko is None or now - ko < 6 * 3600:
                total += float(r["pnl"])
        return round(total, 2)

    def realign_live_bankroll(self) -> float | None:
        """Decisione umana (dal PC, dopo aver controllato il conto): se il bankroll interno è più alto del saldo vero,
        la liquidità interna scende fino a pareggiarlo. Non lo alza mai: i soldi in più sul conto restano fuori.
        Restituisce il nuovo bankroll, oppure None se il saldo non si legge o non serve."""
        if self.settings.get("mode") != "live":
            return None
        try:
            funds = self.executor.client.account_funds()
        except Exception:
            return None
        # più i green-up in attesa di regolamento: sono soldi già vinti che Betfair accredita a mercato chiuso
        real = float(funds.get("availableToBetBalance") or 0) + abs(float(funds.get("exposure") or 0)) \
            + self._unsettled_green()
        if self.bankroll.total <= real:
            return None
        before = self.bankroll.total
        self.bankroll.cash = max(0.0, real - self.bankroll.open_stakes())
        self.tesoriere.say(f"Bankroll riallineato al saldo vero di Betfair: {before:.2f} € → {self.bankroll.total:.2f} €.",
                           "ok", "live_sync", level="WARN")
        return self.bankroll.total

    def _live_gate_notice(self, strategies: list[dict]) -> None:
        """In modalità live, scrive (una volta) quali strategie useranno davvero soldi veri e perché le altre no."""
        if self.settings.get("mode") != "live":
            return
        status = {s["id"]: Gates.live_allowed(self.settings, s["id"]) for s in strategies}
        text = "; ".join(f"{sid}: {'SOLDI VERI' if ok else 'paper (' + why + ')'}" for sid, (ok, why) in status.items())
        if self.store.get("live_gate_text") != text:
            self.store.set("live_gate_text", text)
            self.direttore.say(f"Modalità LIVE richiesta. {text}.", "alert", "live_gate", level="WARN")

    def _matched_telegram(self, rows: list[dict]) -> None:
        """Una volta al giorno (dalle ore matched.telegram_hour, 9 di default) manda su Telegram le partite dove coprire un
        bonus costa meno. Spento con matched.telegram_daily: false. Solo con il feed vero (non col mondo simulato)."""
        from datetime import datetime as _dt
        cfg = self.settings.get("matched") or {}
        if not cfg.get("telegram_daily", True) or not rows or self.settings["feed"]["provider"] != "betfair":
            return
        now = _dt.now()
        day = now.strftime("%Y-%m-%d")
        if now.hour < int(cfg.get("telegram_hour", 9)) or self.store.get("mb_sent_day") == day:
            return
        if time.time() - (self.store.get("mb_try_ts") or 0) < 3600:      # un tentativo all'ora: Telegram giù non rallenta i cicli
            return
        self.store.set("mb_try_ts", time.time())
        from . import notifier
        from .matched import telegram_text
        try:
            notifier.send_now(telegram_text(rows))
            self.store.set("mb_sent_day", day)
        except Exception:
            pass                                       # Telegram non collegato o giù: si riprova al ciclo dopo

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

    def startup_checks(self) -> None:
        """All'avvio, prima del primo ciclo."""
        if self.store.get("kill_switch") == SHUTDOWN_REASON:
            # resto di uno spegnimento interrotto (vecchie versioni): non è un kill switch vero, si toglie
            self.store.set("kill_switch", None)
            self.direttore.say("Tolto il blocco 'spegnimento richiesto' rimasto da uno spegnimento interrotto.", "ok",
                               "startup", level="WARN")
        self.reconcile_live()
        self._sync_live_balance(force=True)       # in live: bankroll interno contro saldo vero già all'avvio

    async def run_forever(self) -> None:
        """Il loop: un ciclo ogni cycle_seconds; tra un ciclo e l'altro, ogni `fast_seconds`, solo la gestione dei
        trade aperti. Un errore non porta mai a puntare "alla cieca"."""
        from . import system
        from .telegram_bot import TelegramCommands
        TelegramCommands(self).start()            # comandi dal telefono: /stato /stop /pausa …
        self.startup_checks()
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
                if STOP_FILE.exists() or RESTART_FILE.exists():     # riavvio: avvia.bat riparte con le nuove impostazioni
                    await self.shutdown()
                    return
                await asyncio.sleep(fast)
                try:
                    await self.manage_trades_fast()
                except Exception as exc:
                    self.cavalli.log(f"Gestione veloce dei trade: errore {exc}", "WARN", "error")

    async def shutdown(self, timeout_s: float = 120.0) -> None:
        """Spegnimento ordinato: niente nuove puntate, trade aperti chiusi (urgenti), poi uscita.
        Il blocco delle nuove puntate è un flag in memoria, non il kill switch: se lo spegnimento viene interrotto
        (taskkill, finestra chiusa, PC spento) al riavvio non resta nessun blocco permanente."""
        self.shutting_down = True
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
        left = len(self.bankroll.open_bets())
        self.direttore.say(f"Bet_bot spento. Posizioni ancora aperte: {left} (si riprendono al prossimo avvio).",
                           "idle", "shutdown", level="WARN")
        STOP_FILE.unlink(missing_ok=True)

