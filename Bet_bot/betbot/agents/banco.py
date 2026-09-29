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

CASHOUT_MARGIN = 0.05
TRADE_MARKETS = ("exchange_win", "exchange_trade")      # trade back→lay: si chiudono con un lay, non col risultato       # il bookmaker trattiene ~5% sul cash-out
MODE_LABEL = {"paper": "PAPER · exchange simulato", "live": "SOLDI VERI · Betfair"}


def exchange_green(stake: float, back: float, lay: float, commission: float) -> float:
    """Profitto (uguale su ogni esito) di un back a `back` chiuso con un lay a `lay`."""
    pnl = stake * (back / lay - 1.0)
    return pnl * (1 - commission) if pnl > 0 else pnl


class Banco(Agent):
    key = "banco"
    name = "Pietro"
    role = "Esegue le puntate approvate, cash-out, green-up, chiusure e notifiche"

    def place(self, p: dict, decision: dict, cycle_id: str, snapshot: dict) -> int | None:
        res = self.office.executor.place(p, decision["stake"], snapshot)
        if not res["ok"]:
            self.say(f"Puntata NON piazzata su {p['label']}: {res['error']}.", "blocked", "exec_fail", level="WARN")
            return None
        br = self.office.bankroll
        stake, odds, mode = res["stake"], res["odds"], res["mode"]
        br.cash = br.cash - stake
        extra = {k: p[k] for k in ("legs", "exchange", "commission") if p.get(k)}
        if res.get("ref"):
            extra["betfair"] = res["ref"]
        cur = self.store.execute(
            "INSERT INTO bets(ts, cycle_id, mode, strategy_id, match_id, league, label, market, selection, bookmaker, odds, "
            "fair_prob, edge, stake, kelly_full, live, reason, extra) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now_iso(), cycle_id, mode, p["strategy_id"], p["match_id"], p.get("league"), p["label"], p["market"],
             p["selection"], p["bookmaker"], odds, p["fair_prob"], p["edge"], stake, decision["kelly_full"],
             int(p["live"]), p["reason"], json.dumps(extra) if extra else None))
        legs = ""
        if p.get("legs"):
            legs = " · " + ", ".join(f"{l['selection']} {stake * l['weight']:.2f} € a {l['odds']:.2f} ({l['bookmaker']})"
                                     for l in p["legs"])
        sent = decision.get("sentiment") or {}
        note = f" · sentiment: {sent['reason']}" if sent.get("level") == "caution" else ""
        self.say(f"[{MODE_LABEL[mode]}] Puntata #{cur.lastrowid}: {stake:.2f} € su {p['label']} a {odds:.2f} "
                 f"({p['bookmaker']}), prob. stimata {p['fair_prob']:.0%}, EV {p['edge']:+.1%}{legs}{note}.",
                 "ok", "bet", payload={"bet_id": cur.lastrowid, "strategy": p["strategy_id"], "mode": mode})
        return cur.lastrowid

    def shadow(self, p: dict) -> None:
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
        self.office.bankroll.cash = self.office.bankroll.cash + payout
        icon = "✅" if pnl > 0 else "❌" if pnl < 0 else "➖"
        self.say(f"{icon} #{bet['id']} {bet['label']}: {status} {pnl:+.2f} € ({reason})"
                 + (f", CLV {clv:+.1%}" if clv is not None else "") + f". Bankroll {self.office.bankroll.total:.2f} €.",
                 "ok", "settle", payload={"bet_id": bet["id"], "pnl": pnl, "status": status, "strategy": bet["strategy_id"],
                                          "stake": bet["stake"], "odds": bet["odds"], "label": bet["label"]})
        return pnl

    def apply(self, actions: list[dict]) -> int:
        n = 0
        for a in actions:
            rows = self.store.query("SELECT * FROM bets WHERE id=? AND status='OPEN'", (a["bet_id"],))
            if not rows:
                continue
            bet = rows[0]
            extra = json.loads(bet["extra"]) if bet.get("extra") else {}
            urgent = any(w in a["reason"] for w in ("time-to-jump", "corsa partita", "stop"))
            if a["action"] == "hedge" or (a["action"] == "cashout" and bet["mode"] == "live"):
                lay = a["price"] if a["action"] == "hedge" else tick_up(a["price"])
                res = self.office.executor.hedge(bet, lay, urgent, self.office.cache)
                if not res["ok"]:
                    self.say(f"#{bet['id']}: chiusura non riuscita ({res['error']}). Riprovo al prossimo ciclo.",
                             "alert", "exec_fail", level="WARN")
                    continue
                comm = (extra.get("exchange") or {}).get("commission", self.office.executor.commission)
                pnl = exchange_green(bet["stake"], bet["odds"], res["price"], comm)
                self._close(bet, "HEDGED", bet["stake"] + pnl, a["reason"])
            elif a["action"] == "cashout":
                value = bet["stake"] * bet["odds"] / a["price"] * (1 - CASHOUT_MARGIN)
                self._close(bet, "CASHOUT", value, a["reason"])
            n += 1
        return n

    def settle(self, snapshot: dict) -> int:
        n = 0
        open_bets = self.office.bankroll.open_bets()
        live = [b for b in open_bets if b["mode"] == "live" and b["market"] not in TRADE_MARKETS]
        if live:
            try:
                cleared = self.office.executor.settled_live(live)
            except Exception as exc:
                cleared = {}
                self.log(f"Esiti Betfair non disponibili ({exc}). Riprovo al prossimo ciclo.", "WARN", "exec_fail")
            for b in live:
                ref = (json.loads(b["extra"]) or {}).get("betfair", {})
                c = cleared.get(ref.get("bet_id"))
                if c:
                    self._close(b, "WON" if c["profit"] > 0 else "LOST", b["stake"] + c["profit"],
                                f"regolata da Betfair ({c['outcome']})")
                    n += 1
        for bet in open_bets:
            if bet["mode"] == "live" or bet["market"] in TRADE_MARKETS:
                continue
            m = snapshot["matches"].get(bet["match_id"])
            if m is None:
                row = self.store.query("SELECT * FROM matches WHERE match_id=?", (bet["match_id"],))
                m = row[0] if row else None
            if not m or m.get("status") != "FINISHED" or not m.get("result"):
                continue
            closing = (m.get("closing") or {}).get(bet["selection"]) if isinstance(m.get("closing"), dict) else None
            won = m["result"] in bet["selection"].split("+")
            extra = json.loads(bet["extra"]) if bet.get("extra") else {}
            comm = extra.get("commission", self.office.executor.commission) if bet["bookmaker"] in ("Betfair", "Exchange") else 0.0
            payout = bet["stake"] + bet["stake"] * (bet["odds"] - 1) * (1 - comm) if won else 0.0
            self._close(bet, "WON" if won else "LOST", payout,
                        f"risultato {m.get('home_score')}-{m.get('away_score')}"
                        + (f", commissione {comm:.0%} sulla vincita" if won and comm else ""), closing)
            n += 1
        for sb in self.store.query("SELECT * FROM shadow_bets WHERE status='OPEN'"):
            m = snapshot["matches"].get(sb["match_id"])
            if m and m["status"] == "FINISHED" and m.get("result"):
                won = m["result"] in sb["selection"].split("+")
                net = (sb["odds"] - 1) * (1 - self.office.executor.commission)       # ombra: 1 € su exchange
                self.store.execute("UPDATE shadow_bets SET status=?, settled_ts=?, pnl=? WHERE id=?",
                                   ("WON" if won else "LOST", now_iso(), net if won else -1.0, sb["id"]))
        if not n:
            self.status("idle", f"{len(self.office.bankroll.open_bets())} puntate in gioco, nessuna da chiudere.")
        return n
