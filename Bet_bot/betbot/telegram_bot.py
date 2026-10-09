"""Comandi da Telegram: controllare Bet_bot dal telefono.

Accetta messaggi SOLO dalla chat collegata nelle Impostazioni: chiunque altro scriva
al bot viene ignorato. I comandi possono solo informare o FRENARE:

  /stato     bankroll, profitti, drawdown, puntate aperte, blocchi attivi
  /aperte    elenco delle puntate in gioco
  /oggi      riepilogo della giornata
  /stop      kill switch immediato: nessuna nuova puntata, ordini non abbinati annullati (le posizioni
             aperte continuano a essere gestite). Il reset si fa solo dal PC.
  /chiudi    chiude subito tutti i trade aperti (uscita urgente)
  /pausa N   niente nuove puntate per N minuti (predefinito 60)
  /riprendi  toglie la pausa (NON il kill switch)
  /coperture partite di betfair.it dove coprire un bonus costa meno (matched betting, NON pronostici)
  /aiuto     questo elenco

Gira in un thread separato con long polling (getUpdates): un errore di rete non ferma mai il bot.
"""
from __future__ import annotations

import threading
import time

from . import clock, local_settings, notifier
from .store import now_iso

HELP = ("Comandi Bet_bot:\n/stato · bankroll e blocchi\n/aperte · puntate in gioco\n/oggi · riepilogo del giorno\n"
        "/stop · kill switch immediato (reset solo dal PC)\n/chiudi · chiude subito i trade aperti\n/pausa 60 · niente nuove puntate per 60 minuti\n"
        "/riprendi · toglie la pausa\n/coperture · dove coprire un bonus su betfair.it (matched betting)\n/aiuto · questo elenco")


def _eur(v) -> str:
    return "—" if v is None else f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


class TelegramCommands:
    def __init__(self, office):
        self.office = office
        self.store = office.store
        self.offset = self.store.get("telegram_offset") or 0
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None

    # ── ciclo di ascolto ─────────────────────────────────────
    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._loop, name="telegram-commands", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            ch = notifier.channel()
            if not ch:
                self._stop.wait(30)
                continue
            try:
                updates = notifier.call(ch["token"], "getUpdates",
                                        {"offset": self.offset, "timeout": 25, "allowed_updates": ["message"]}, timeout=35)
            except Exception:
                self._stop.wait(15)
                continue
            for u in updates:
                self.offset = max(self.offset, u["update_id"] + 1)
                try:
                    self.store.set("telegram_offset", self.offset)
                except Exception:
                    pass                                  # es. database occupato: l'offset resta in memoria
                msg = u.get("message") or {}
                if str((msg.get("chat") or {}).get("id")) != str(ch["chat_id"]):
                    continue                              # solo la chat del proprietario
                try:
                    reply = self.handle(msg.get("text") or "")
                except Exception as exc:          # un comando andato storto non deve spegnere /stop e /chiudi
                    reply = f"Errore nel comando: {exc}"
                    try:
                        self.store.event("direttore", f"Comando Telegram non eseguito: {exc}", "WARN", "error")
                    except Exception:
                        pass
                if reply:
                    try:
                        notifier.call(ch["token"], "sendMessage", {"chat_id": ch["chat_id"], "text": reply})
                    except Exception:
                        pass

    # ── comandi ──────────────────────────────────────────────
    def handle(self, text: str) -> str | None:
        parts = text.strip().split()
        if not parts or not parts[0].startswith("/"):
            return None
        cmd = parts[0].split("@")[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        if cmd in ("/start", "/aiuto", "/help"):
            return HELP
        if cmd == "/stato":
            return self._stato()
        if cmd == "/aperte":
            bets = self.office.bankroll.open_bets()
            if not bets:
                return "Nessuna puntata aperta."
            return "Puntate aperte:\n" + "\n".join(f"#{b['id']} {b['label']} · {_eur(b['stake'])} a {b['odds']:.2f} ({b['mode']})"
                                                   for b in bets[:15])
        if cmd == "/oggi":
            r = self.office.auditor.daily_report()
            return (f"Oggi: {r['placed']} piazzate, {r['bets']} chiuse, vinte {r['wins']} / perse {r['losses']}, "
                    f"P&L {_eur(r['pnl'])}, ROI {r['roi']:+.1%}.")
        if cmd == "/stop":
            if not self.store.get("kill_switch"):
                self.store.set("kill_switch", f"fermato da Telegram il {now_iso()}")
                self.office.risk.say("KILL SWITCH ATTIVATO da Telegram: nessuna nuova puntata. Le posizioni aperte "
                                     "vengono gestite fino alla chiusura. Reset solo dal PC.", "alert", "kill_switch",
                                     level="CRITICAL")
            cancelled = ""
            if self.office.settings.get("mode") == "live" and self.office.client is not None:
                try:
                    self.office.client.cancel()                     # annulla ogni ordine non abbinato
                    cancelled = " Ordini non abbinati annullati su Betfair."
                except Exception as exc:
                    cancelled = f" Annullamento ordini non riuscito: {exc}."
            return ("Fermato. Nessuna nuova puntata." + cancelled + " Per chiudere subito i trade aperti: /chiudi. "
                    "Per ripartire: dal PC, betbot.py reset-kill-switch.")
        if cmd == "/chiudi":
            trades = [b for b in self.office.bankroll.open_bets() if b["market"] in ("exchange_trade", "exchange_win")]
            if not trades:
                return "Nessun trade aperto da chiudere."
            self.store.set("close_all_requested", True)
            return f"Chiudo {len(trades)} trade aperti al prossimo passaggio (entro pochi secondi)."
        if cmd == "/pausa":
            minutes = int(arg) if arg.isascii() and arg.isdigit() else 60     # "²".isdigit() è True, int("²") no
            minutes = max(1, min(minutes, 24 * 60))
            until = clock.now() + minutes * 60
            self.store.set("telegram_pause_until", until)
            self.office.risk.say(f"Pausa chiesta da Telegram: nessuna nuova puntata per {minutes} minuti.", "blocked",
                                 "circuit", level="WARN")
            return f"In pausa per {minutes} minuti."
        if cmd == "/coperture":
            from .matched import telegram_text
            return telegram_text((self.store.get("mb_lay_board") or {}).get("rows") or [], html=False)
        if cmd == "/riprendi":
            self.store.set("telegram_pause_until", 0)             # solo la pausa del telefono: i freni automatici restano
            self.office.risk.log("Pausa tolta da Telegram.", "INFO", "circuit")
            return "Pausa tolta." + (" Attenzione: il kill switch è ancora attivo (reset solo dal PC)."
                                     if self.store.get("kill_switch") else "")
        return "Comando sconosciuto. Scrivi /aiuto."

    def _stato(self) -> str:
        rs = self.store.get("risk_state") or {}
        m = self.store.get("metrics") or {}
        blocks = []
        if self.store.get("kill_switch"):
            blocks.append(f"KILL SWITCH: {self.store.get('kill_switch')}")
        if (self.store.get("cooldown_until") or 0) > clock.now():
            blocks.append("in pausa")
        wr = m.get("win_rate")
        lb = self.store.get("live_balance") or {}
        real = f"\nSaldo Betfair: {_eur(lb.get('total'))} (disponibile {_eur(lb.get('available'))})" if lb else ""
        return (f"Modalità {self.office.settings.get('mode', 'paper').upper()}{real}\n"
                f"Bankroll {_eur(rs.get('bankroll'))} (capitale {_eur(rs.get('initial'))}, profitti {_eur(rs.get('profits'))})\n"
                f"Drawdown {rs.get('drawdown', 0):.1%} · aperte {len(self.office.bankroll.open_bets())}\n"
                f"Chiuse {m.get('bets', 0)} · vinte {'—' if wr is None else f'{wr:.0%}'} · ROI {m.get('roi', 0):+.1%}\n"
                + ("Blocchi: " + "; ".join(blocks) if blocks else "Nessun blocco attivo."))
