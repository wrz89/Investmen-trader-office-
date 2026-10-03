"""Perché il bot non punta: tutti i freni in un colpo d'occhio (CLI `perche`, dashboard, bollettino).

Legge solo il database: kill switch, pause, stop del giorno, limiti del giorno, regole di Leo attive e i motivi
dei veti delle ultime ore (il primo motivo di ogni veto, contato). Non sblocca nulla da solo: dice cosa fare.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

HOURS = 12


def _since(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


def check(store, hours: float = HOURS) -> dict:
    since = _since(hours)
    rs = store.get("risk_state") or {}
    vetoes = Counter()
    for e in store.query("SELECT payload FROM events WHERE kind='veto' AND ts >= ?", (since,)):
        try:
            reasons = (json.loads(e["payload"]) or {}).get("reasons") or []
        except (TypeError, ValueError):
            reasons = []
        if reasons:
            key = str(reasons[0])
            for cut in (" (", ": ", " ≤", " <"):            # "Quote fresche (12 s ≤ 60 s)" → "Quote fresche"
                if cut in key and not key.startswith("Lezione di Leo"):
                    key = key.split(cut)[0]
                    break
            vetoes[re.sub(r"\d+(?:[.,]\d+)?", "…", key)] += 1   # "entro 2.40 €" e "entro 2.97 €" sono lo stesso motivo
    approved = store.query("SELECT COUNT(*) n FROM events WHERE kind='approve' AND ts >= ?", (since,))[0]["n"]
    last = store.query("SELECT ts FROM bets WHERE mode!='shadow' ORDER BY id DESC LIMIT 1")
    try:
        rules = store.query("SELECT strategy_id, kind, feature, value, n, clv, adjust FROM coach_rules WHERE active=1")
    except Exception:                                   # database senza Leo (mai acceso)
        rules = []
    blocks = []
    if store.get("kill_switch"):
        blocks.append(("Kill switch attivo: " + str(store.get("kill_switch")),
                       "Controlla il conto su Betfair, poi: python betbot.py reset-kill-switch (dal PC)."))
    if rs.get("cooldown_until"):
        blocks.append(("Pausa per serie negativa fino alle "
                       + datetime.fromtimestamp(rs["cooldown_until"]).strftime("%H:%M"), "Riparte da sola."))
    if rs.get("daily_stop"):
        ev = store.query("SELECT ts, message FROM events WHERE kind='circuit' AND message LIKE 'Circuit breaker: persi%' "
                         "ORDER BY id DESC LIMIT 1")
        when = ""
        if ev:
            try:
                when = " alle " + datetime.fromisoformat(ev[0]["ts"]).astimezone().strftime("%H:%M") + f" ({ev[0]['message'].split(': ', 1)[1].split('.')[0]})"
            except (ValueError, IndexError):
                when = ""
        blocks.append((f"Stop del giorno scattato{when}. Ora persi {rs.get('loss_today', 0):.2f} € su un limite di "
                       f"{rs.get('daily_budget', 0):.2f} €: lo stop resta fino a mezzanotte anche se nel frattempo si recupera",
                       "Riparte da solo domani."))
    if store.get("limits_tampered"):
        blocks.append(("File dei limiti cambiato a bot acceso", "Riavvia il bot (chiudi la finestra e lancia avvia.bat)."))
    if (store.get("telegram_pause_until") or 0) > time.time():
        blocks.append(("Pausa chiesta da Telegram", "Scrivi /riprendi al bot su Telegram o aspetta."))
    # puntate vere aperte: se sono al tetto (3 del divertimento, 4 in tutto) il bot non propone nulla e non ci sono
    # veti. Una puntata aperta da più di 6 ore dopo l'inizio è probabilmente "incastrata" (mercato non chiuso).
    from .config import load_yaml
    lim = load_yaml("risk_limits.yaml")
    opened = store.query("SELECT b.id, b.label, b.strategy_id, b.ts, m.kickoff FROM bets b LEFT JOIN matches m ON m.match_id=b.match_id "
                         "WHERE b.status='OPEN' AND b.mode!='shadow' ORDER BY b.id")
    stuck = []
    for o in opened:
        try:
            ko = datetime.fromisoformat(str(o["kickoff"]).replace("Z", "+00:00")).timestamp() if o["kickoff"] else None
        except ValueError:
            ko = None
        if ko and time.time() - ko > 6 * 3600:
            stuck.append(o)
    from .agents.risk import _day_start_iso
    today_n = store.query("SELECT COUNT(*) n FROM bets WHERE mode!='shadow' AND strategy_id LIKE 'S10_divertimento%' AND ts >= ?",
                          (_day_start_iso(),))[0]["n"]
    fun_open = sum(1 for o in opened if str(o["strategy_id"]).startswith("S10_divertimento"))
    if fun_open >= lim.get("fun_max_open", 3):
        blocks.append((f"{fun_open} puntate del divertimento già aperte (tetto {lim.get('fun_max_open', 3)})",
                       "Riparte quando una si chiude." + (" ATTENZIONE: " + ", ".join(f"#{o['id']} {o['label']}" for o in stuck)
                                                          + " risultano aperte da più di 6 ore dopo l'inizio: controlla su Betfair "
                                                          "('Le mie scommesse') se sono state regolate." if stuck else "")))
    if today_n >= lim.get("fun_max_bets_per_day", 10):
        blocks.append((f"Già {today_n} puntate del divertimento oggi (tetto {lim.get('fun_max_bets_per_day', 10)})",
                       "Riparte da sola domani."))
    return {"ts": time.time(), "hours": hours, "blocks": blocks, "open": len(opened), "stuck": len(stuck), "today": today_n, "vetoes": vetoes.most_common(6), "approved": approved,
            "last_bet": last[0]["ts"] if last else None, "rules": rules,
            "bankroll": rs.get("bankroll"), "kill_floor": rs.get("kill_floor")}


def text(c: dict) -> str:
    L = [f"Perché il bot non punta (ultime {c['hours']:.0f} ore)", ""]
    if c["blocks"]:
        L.append("FRENI ATTIVI (bloccano tutto):")
        L += [f"• {what} → {fix}" for what, fix in c["blocks"]]
        L.append("")
    else:
        L += ["Nessun freno generale attivo (kill switch, pause, stop del giorno): il bot cerca e valuta.", ""]
    L.append(f"Puntate approvate: {c['approved']} · ultima puntata vera: {c['last_bet'] or 'mai'} · aperte ora: "
             f"{c['open']} · divertimento oggi: {c['today']}")
    if c["vetoes"]:
        L.append("Motivi dei veti (il primo di ogni proposta bocciata):")
        L += [f"• {n}× {why}" for why, n in c["vetoes"]]
    else:
        L.append("Nessun veto: probabilmente non ci sono partite adatte in questo momento (quote 1,40-3,00, libro "
                 "stretto, 10-240 minuti all'inizio) o è già al limite di puntate del giorno / aperte.")
    if c["rules"]:
        L += ["", "Lezioni di Leo attive (possono solo frenare):"]
        for r in c["rules"]:
            L.append(f"• {r['strategy_id']}: " + (f"{r['feature']} = {r['value']} (CLV {r['clv']:+.1%} su {r['n']})"
                     if r["kind"] == "blocca" else f"probabilità corrette di −{(r['adjust'] or 0):.1%}"))
    return "\n".join(L)


def run(out=print) -> int:
    from .config import DB_LIVE_PATH, DB_PATH, load_settings
    from .store import Store
    st = load_settings()
    out(text(check(Store(DB_LIVE_PATH if st.get("mode") == "live" else DB_PATH))))
    return 0
