"""AGENTE 6 — PIETRO · BANCO SCOMMESSE (esecuzione e notifica).

Piazza solo puntate con APPROVE del Risk Manager, tramite l'Executor:
  paper → exchange simulato con le stesse regole di Betfair (fill-or-kill, liquidità, commissione);
  live  → ordine fill-or-kill su Betfair Exchange (solo con tutti i cancelli aperti).
Esegue cash-out e green-up chiesti dalle strategie, chiude le puntate a partita
finita e calcola il CLV (quota presa contro quota "giusta" alla chiusura).
Ogni puntata e ogni chiusura partono anche su Telegram (Notifier).
"""
from __future__ import annotations

import json

from ..feeds.mock import tick_up
from ..store import now_iso
from .base import Agent
from ..state import bet_agent, strategy_agent

CASHOUT_MARGIN = 0.05
TRADE_MARKETS = ("exchange_win", "exchange_trade")      # trade back→lay: si chiudono con un lay, non col risultato       # il bookmaker trattiene ~5% sul cash-out
MODE_LABEL = {"paper": "PAPER · exchange simulato", "live": "SOLDI VERI · Betfair", "shadow": "OMBRA · nessun capitale"}


def exchange_green(stake: float, back: float, lay: float, commission: float) -> float:
    """Profitto (uguale su ogni esito) di un back a `back` chiuso con un lay a `lay`."""
    pnl = stake * (back / lay - 1.0)
    return pnl * (1 - commission) if pnl > 0 else pnl


def lay_close_outcomes(stake: float, back: float, lay_size: float, lay_avg: float, commission: float) -> tuple[float, float]:
    """Risultato netto (se la selezione vince, se perde) di un back chiuso con un lay di puntata e prezzo medio VERI.
    Coincidono solo se lay_size = stake × back / lay_avg; altrimenti resta una piccola parte scoperta."""
    win = stake * (back - 1.0) - lay_size * (lay_avg - 1.0)
    lose = lay_size - stake
    net = lambda x: x * (1 - commission) if x > 0 else x
    return net(win), net(lose)


class Banco(Agent):
    key = "banco"
    name = "Pietro"
    role = "Esegue le puntate approvate, cash-out, green-up, chiusure e notifiche"

    def place(self, p: dict, decision: dict, cycle_id: str, snapshot: dict) -> int | None:
        if getattr(self.office, "shutting_down", False):  # spegnimento in corso: solo chiusure, nessuna puntata nuova
            return None
        if self.office.executor.route(p, snapshot) == "shadow":
            self.shadow(p, snapshot)                      # mai scalare il bankroll vero per una puntata in ombra
            return None
        if self.store.query("SELECT 1 FROM orders WHERE status IN ('PENDING', 'UNKNOWN', 'UNCONFIRMED') LIMIT 1"):
            self.say(f"Puntata su {p['label']} rimandata: c'è un ordine vero ancora da confermare su Betfair.",
                     "blocked", "exec_fail", level="WARN")
            return None
        res = self.office.executor.place(p, decision["stake"], snapshot)
        if not res["ok"]:
            if res.get("unknown"):
                # soldi veri, esito ignoto: forse la puntata è abbinata su Betfair ma non nel registro. Si blocca
                # tutto (anche un secondo ordine uguale al ciclo dopo); tra 2 minuti l'ordine si cerca di nuovo.
                self.store.set("kill_switch", f"esito sconosciuto della puntata su {p['label']} (ordine {res.get('order_ref')})")
                self.say(f"KILL SWITCH: non so se la puntata su {p['label']} è stata abbinata ({res['error']}). "
                         "Nessuna nuova puntata; tra 2 minuti la cerco di nuovo su Betfair. Controlla 'Le mie scommesse' "
                         "su betfair.it, poi resetta dal PC.", "alert", "kill_switch", level="CRITICAL")
                return None
            self.say(f"Puntata NON piazzata su {p['label']}: {res['error']}.", "blocked", "exec_fail", level="WARN")
            return None
        br = self.office.bankroll
        stake, odds, mode = res["stake"], res["odds"], res["mode"]
        br.cash = br.cash - stake
        extra = {k: p[k] for k in ("legs", "exchange", "commission", "sport") if p.get(k)}
        if res.get("ref"):
            extra["betfair"] = res["ref"]
        cur = self.store.execute(
            "INSERT INTO bets(ts, cycle_id, mode, strategy_id, match_id, league, label, market, selection, bookmaker, odds, "
            "fair_prob, edge, stake, kelly_full, live, reason, extra) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now_iso(), cycle_id, mode, p["strategy_id"], p["match_id"], p.get("league"), p["label"], p["market"],
             p["selection"], p["bookmaker"], odds, p["fair_prob"], p["edge"], stake, decision["kelly_full"],
             int(p["live"]), p["reason"], json.dumps(extra) if extra else None))
        if (res.get("ref") or {}).get("order_ref"):     # l'ordine BACK sa a quale puntata del libro appartiene
            self.store.execute("UPDATE orders SET bet_row_id=? WHERE ref=?", (cur.lastrowid, res["ref"]["order_ref"]))
        legs = ""
        if p.get("legs"):
            legs = " · " + ", ".join(f"{l['selection']} {stake * l['weight']:.2f} € a {l['odds']:.2f} ({l['bookmaker']})"
                                     for l in p["legs"])
        sent = decision.get("sentiment") or {}
        note = f" · sentiment: {sent['reason']}" if sent.get("level") == "caution" else ""
        self.say(f"[{MODE_LABEL[mode]}] Puntata #{cur.lastrowid}: {stake:.2f} € su {p['label']} a {odds:.2f} "
                 f"({p['bookmaker']}), prob. stimata {p['fair_prob']:.0%}, EV {p['edge']:+.1%}{legs}{note}.",
                 "ok", "bet", payload={"bet_id": cur.lastrowid, "strategy": p["strategy_id"], "mode": mode,
                                            "agent": strategy_agent(p["strategy_id"], p.get("sport"))})
        return cur.lastrowid

    def shadow(self, p: dict, snapshot: dict | None = None) -> None:
        """Strategia in osservazione (o non ammessa ai soldi veri in live).
        • Trade back→lay: si apre un TRADE OMBRA vero e proprio (2 €, regole dell'exchange simulato), gestito e
          chiuso come gli altri ma senza toccare il bankroll: è l'unico modo onesto di misurare uno scalping.
        • Puntata secca: si registra 1 € virtuale, regolato sul risultato con la commissione."""
        if p.get("exchange") and snapshot is not None:
            if self.store.query("SELECT 1 FROM bets WHERE status='OPEN' AND mode='shadow' AND strategy_id=? AND match_id=?",
                                (p["strategy_id"], p["match_id"])):
                return
            from ..execution import book_for
            stake = self.office.executor.min_stake
            r = self.office.executor.paper.place("BACK", p["odds"], stake, book_for(p, snapshot))
            if not r["ok"]:
                return
            extra = {k: p[k] for k in ("exchange", "commission", "sport") if p.get(k)}
            self.store.execute(
                "INSERT INTO bets(ts, cycle_id, mode, strategy_id, match_id, league, label, market, selection, bookmaker, "
                "odds, fair_prob, edge, stake, kelly_full, live, reason, extra) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (now_iso(), "ombra", "shadow", p["strategy_id"], p["match_id"], p.get("league"), p["label"], p["market"],
                 p["selection"], p["bookmaker"], r["price"], p["fair_prob"], p["edge"], stake, 0.0, int(p["live"]),
                 p["reason"], json.dumps(extra)))
            self.log(f"Ombra · {p['strategy_id']}: trade virtuale {p['label']} a {r['price']:.2f} (nessun capitale).",
                     "INFO", "shadow")
            return
        if self.store.query("SELECT 1 FROM shadow_bets WHERE status='OPEN' AND strategy_id=? AND match_id=?",
                            (p["strategy_id"], p["match_id"])):
            return
        self.store.execute("INSERT INTO shadow_bets(ts, strategy_id, match_id, label, selection, odds, fair_prob, edge, stake) "
                           "VALUES(?,?,?,?,?,?,?,?,1)", (now_iso(), p["strategy_id"], p["match_id"], p["label"],
                                                         p["selection"], p["odds"], p["fair_prob"], p["edge"]))

    def _close(self, bet: dict, status: str, payout: float, reason: str, closing: float | None = None) -> float:
        pnl = round(payout - bet["stake"], 4)
        clv = (bet["odds"] / closing - 1.0) if closing else None
        self.store.execute("UPDATE bets SET status=?, settled_ts=?, payout=?, pnl=?, closing_odds=?, clv=?, settle_reason=? "
                           "WHERE id=? AND status='OPEN'", (status, now_iso(), round(payout, 4), pnl, closing, clv, reason, bet["id"]))
        if bet["mode"] == "shadow":                     # ombra: si misura, ma il bankroll non si tocca
            self.log(f"Ombra · #{bet['id']} {bet['label']}: {status} {pnl:+.2f} € virtuali ({reason}).", "INFO",
                     "shadow_settle", payload={"bet_id": bet["id"], "pnl": pnl, "strategy": bet["strategy_id"]})
            return pnl
        self.office.bankroll.cash = self.office.bankroll.cash + payout
        icon = "✅" if pnl > 0 else "❌" if pnl < 0 else "➖"
        self.say(f"{icon} #{bet['id']} {bet['label']}: {status} {pnl:+.2f} € ({reason})"
                 + (f", CLV {clv:+.1%}" if clv is not None else "") + f". Bankroll {self.office.bankroll.total:.2f} €.",
                 "ok", "settle", payload={"bet_id": bet["id"], "pnl": pnl, "status": status, "strategy": bet["strategy_id"],
                                  "agent": bet_agent(bet),
                                          "stake": bet["stake"], "odds": bet["odds"], "label": bet["label"]})
        return pnl

    def _close_hedged(self, bet: dict, lay_size: float, lay_avg: float, reason: str) -> float:
        """Chiusura di una puntata VERA con il lay abbinato su Betfair (puntata e prezzo medio reali). Se i due esiti
        non coincidono si registra il PEGGIORE: il bankroll interno non supera mai quello vero."""
        win, lose = lay_close_outcomes(bet["stake"], bet["odds"], lay_size, lay_avg, self._commission(bet))
        pnl = min(win, lose)
        if abs(win - lose) >= 0.005:
            reason += (f" · lay {lay_size:.2f} € a {lay_avg:.2f}: {win:+.2f} € se vince, {lose:+.2f} € se perde "
                       "(registrato il caso peggiore)")
        return self._close(bet, "HEDGED", bet["stake"] + pnl, reason)

    def _lay_orders(self, bet_id: int, statuses: tuple = ("MATCHED", "PENDING", "UNKNOWN", "UNCONFIRMED")) -> list[dict]:
        marks = ",".join("?" * len(statuses))
        return self.store.query(f"SELECT * FROM orders WHERE bet_row_id=? AND side='LAY' AND status IN ({marks}) ORDER BY id",
                                (bet_id, *statuses))

    def _close_from_lays(self, bet: dict, reason: str) -> float:
        lays = [o for o in self._lay_orders(bet["id"], ("MATCHED",)) if (o["matched"] or 0) > 0]
        size = sum(o["matched"] for o in lays)
        avg = sum(o["matched"] * (o["avg_price"] or o["price"]) for o in lays) / size
        if len(lays) > 1:                                 # non dovrebbe mai succedere: posizione da guardare a mano
            self.store.set("kill_switch", f"{len(lays)} lay abbinati per la stessa puntata #{bet['id']}")
            self.say(f"KILL SWITCH: la puntata #{bet['id']} ha {len(lays)} lay di chiusura abbinati su Betfair. "
                     "Controlla la posizione su betfair.it, poi resetta dal PC.", "alert", "kill_switch", level="CRITICAL")
        return self._close_hedged(bet, size, avg, reason)

    def close_matched_lays(self, reason: str) -> int:
        """Puntate vere ancora OPEN con un lay di chiusura GIÀ abbinato su Betfair (crash tra l'abbinamento e la
        chiusura nel registro, oppure esito ritrovato dopo una risposta persa): si chiudono con puntata e prezzo
        medio di quel lay, SENZA mandare un nuovo ordine."""
        rows = self.store.query("SELECT DISTINCT o.bet_row_id AS id FROM orders o JOIN bets b ON b.id = o.bet_row_id "
                                "WHERE o.side='LAY' AND o.status='MATCHED' AND o.matched > 0 AND b.status='OPEN' "
                                "AND b.mode='live'")
        for r in rows:
            bet = self.store.query("SELECT * FROM bets WHERE id=?", (r["id"],))[0]
            self._close_from_lays(bet, reason)
        return len(rows)

    def apply(self, actions: list[dict]) -> int:
        n = 0
        for a in actions:
            rows = self.store.query("SELECT * FROM bets WHERE id=? AND status='OPEN'", (a["bet_id"],))
            if not rows:
                continue
            bet = rows[0]
            extra = json.loads(bet["extra"]) if bet.get("extra") else {}
            urgent = bool(a.get("urgent")) or any(w in a["reason"] for w in ("time-to-jump", "corsa partita", "stop", "inizio"))
            if a["action"] == "hedge" or (a["action"] == "cashout" and bet["mode"] == "live"):
                if bet["mode"] == "live":
                    lays = self._lay_orders(bet["id"])
                    if any(o["status"] == "MATCHED" for o in lays):      # già chiusa su Betfair: niente secondo lay
                        self._close_from_lays(bet, a["reason"] + " (lay già abbinato su Betfair)")
                        n += 1
                        continue
                    if lays:                                  # lay dall'esito incerto: si aspetta la verifica
                        self.status("blocked", f"#{bet['id']}: chiusura in verifica su Betfair, nessun nuovo lay "
                                               "finché non si sa com'è andata.")
                        continue
                lay = a["price"] if a["action"] == "hedge" else tick_up(a["price"])
                # un'uscita urgente riprova subito, allargando il prezzo di 2 tick alla volta (massimo 3 tentativi),
                # ma senza inseguire oltre il prezzo peggiore del piano su cui il Risk Manager ha dimensionato il
                # trade (se il prezzo visto è già oltre, un solo tentativo; il giro dopo riprova col prezzo nuovo)
                worst = (extra.get("exchange") or {}).get("worst")
                cap = max(worst, tick_up(lay, 2)) if worst else None
                res, capped = None, False
                for attempt in range(3 if urgent else 1):
                    ask = tick_up(lay, 2 * attempt) if attempt else lay
                    if attempt and cap and tick_up(ask, 2) > cap + 1e-9:
                        capped = True
                        break
                    res = self.office.executor.hedge(bet, ask, urgent, self.office.cache)
                    if res["ok"] or res.get("unknown") or res.get("unconfirmed"):
                        break                             # mai un secondo lay finché il primo non è chiarito
                if not res["ok"]:
                    if res.get("unknown"):
                        self.store.set("kill_switch", f"esito sconosciuto della chiusura di #{bet['id']}")
                        self.say(f"KILL SWITCH: non so se la chiusura di #{bet['id']} è andata ({res['error']}). "
                                 "Controlla su betfair.it.", "alert", "kill_switch", level="CRITICAL")
                    elif bet["mode"] != "shadow":
                        self.say(f"#{bet['id']}: chiusura non riuscita ({res['error']})"
                                 + (f"; non inseguo oltre il prezzo peggiore del piano ({worst:.2f})" if capped else "")
                                 + ". Riprovo.", "alert", "exec_fail", level="WARN")
                    continue
                if res.get("size"):                       # soldi veri: puntata e prezzo medio del lay abbinato
                    self._close_hedged(bet, res["size"], res["price"], a["reason"])
                else:
                    comm = extra.get("commission") or (extra.get("exchange") or {}).get("commission") or self.office.executor.commission
                    pnl = exchange_green(bet["stake"], bet["odds"], res["price"], comm)
                    self._close(bet, "HEDGED", bet["stake"] + pnl, a["reason"])
            elif a["action"] == "cashout":
                value = bet["stake"] * bet["odds"] / a["price"] * (1 - CASHOUT_MARGIN)
                self._close(bet, "CASHOUT", value, a["reason"])
            n += 1
        return n

    def _commission(self, bet: dict) -> float:
        extra = json.loads(bet["extra"]) if bet.get("extra") else {}
        return extra.get("commission") or (extra.get("exchange") or {}).get("commission") or self.office.executor.commission

    def settle(self, snapshot: dict) -> int:
        n = 0
        open_bets = self.office.bankroll.open_bets() + self.office.bankroll.open_shadow_trades()
        live = [b for b in open_bets if b["mode"] == "live"]
        if live:
            try:
                cleared = self.office.executor.settled_live(live)
            except Exception as exc:
                cleared = {}
                self.log(f"Esiti Betfair non disponibili ({exc}). Riprovo al prossimo ciclo.", "WARN", "exec_fail")
            for b in live:
                ref = (json.loads(b["extra"]) or {}).get("betfair", {})
                c = cleared.get(ref.get("bet_id"))
                if not c:
                    continue
                if c["status"] != "SETTLED":             # annullata, scaduta o cancellata: rimborso della puntata
                    self._close(b, "VOID", b["stake"], f"Betfair: {c['status'].lower()}")
                elif c["profit"] > 0:
                    self._close(b, "WON", b["stake"] + c["profit"] * (1 - self._commission(b)),
                                f"regolata da Betfair ({c['outcome']}), commissione sulla vincita")
                else:
                    self._close(b, "LOST", b["stake"] + c["profit"], f"regolata da Betfair ({c['outcome']})")
                n += 1
        for bet in open_bets:
            if bet["mode"] == "live":
                continue
            m = snapshot["matches"].get(bet["match_id"])
            race = (snapshot.get("races") or {}).get(bet["match_id"])
            if m is None and race is None:
                row = self.store.query("SELECT * FROM matches WHERE match_id=?", (bet["match_id"],))
                m = row[0] if row else None
            if race is not None:                          # trade sui cavalli rimasto aperto a corsa finita
                if race.get("status") != "CLOSED" or not race.get("winner"):
                    continue
                result, closing, score = race["winner"], None, "corsa"
            else:
                if not m or m.get("status") != "FINISHED":
                    continue
                if m.get("void"):
                    self._close(bet, "VOID", bet["stake"], "mercato annullato da Betfair")
                    n += 1
                    continue
                if not m.get("result"):
                    continue
                result = m["result"]
                closing = (m.get("closing") or {}).get(bet["selection"]) if isinstance(m.get("closing"), dict) else None
                score = f"{m.get('home_score')}-{m.get('away_score')}"
            won = result in bet["selection"].split("+")
            comm = self._commission(bet) if bet["bookmaker"] in ("Betfair", "Exchange") else 0.0
            payout = bet["stake"] + bet["stake"] * (bet["odds"] - 1) * (1 - comm) if won else 0.0
            why = f"risultato {score}" + (", trade non chiuso in tempo: vale il risultato" if bet["market"] in TRADE_MARKETS else "")
            self._close(bet, "WON" if won else "LOST", payout,
                        why + (f", commissione {comm:.1%} sulla vincita" if won and comm else ""), closing)
            n += 1
        for sb in self.store.query("SELECT * FROM shadow_bets WHERE status='OPEN'"):
            m = snapshot["matches"].get(sb["match_id"])
            if m and m["status"] == "FINISHED" and (m.get("result") or m.get("void")):
                if m.get("void"):
                    self.store.execute("UPDATE shadow_bets SET status='VOID', settled_ts=?, pnl=0 WHERE id=?", (now_iso(), sb["id"]))
                    continue
                won = m["result"] in sb["selection"].split("+")
                net = (sb["odds"] - 1) * (1 - self.office.executor.commission)       # ombra: 1 € su exchange
                self.store.execute("UPDATE shadow_bets SET status=?, settled_ts=?, pnl=? WHERE id=?",
                                   ("WON" if won else "LOST", now_iso(), net if won else -1.0, sb["id"]))
        if not n:
            k = len(self.office.bankroll.open_bets())
            self.status("idle", f"{k} {'puntata' if k == 1 else 'puntate'} in gioco, nessuna da chiudere.")
        return n
