"""Perché il bot non punta: tutti i freni in un colpo d'occhio (CLI `perche`, dashboard, bollettino).

Legge solo il database: kill switch, pause, stop del giorno, limiti del giorno, regole di Leo attive e i motivi
dei veti delle ultime ore (il primo motivo di ogni veto, contato). Non sblocca nulla da solo: dice cosa fare.
"""
from __future__ import annotations

import collections
import json
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

HOURS = 12


def _since(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


def lay_diagnosis(snap: dict, settings: dict) -> dict:
    """Fotografia dei lay di questo ciclo: quante partite di calcio, quante hanno un esito a quota 2,9-5,3, quante di queste
    hanno Pinnacle fresco (≤ 90 min), quanti lay di valore passano i filtri, e i crediti di The Odds API usati oggi."""
    import json
    from .config import RUNTIME_DIR
    from .strategies import s10_divertimento_v2 as V2
    now = snap.get("sim_time") or snap["ts"]
    q = V2.DEFAULTS
    soccer = [m for m in snap.get("matches", {}).values() if m.get("status") == "SCHEDULED" and m.get("exchange")
              and str(m.get("sport", "")).startswith("soccer")]
    in_win = [m for m in soccer if 0 <= datetime.fromisoformat(m["kickoff"]).timestamp() - now <= q["max_minutes_before"] * 60]
    cand = [m for m in in_win if any(q["lay_min"] - 0.1 <= (b.get("back") or 0) <= q["lay_max"] + 0.3 for b in m["exchange"].values())]
    fresh = [m for m in cand if m.get("ref_ts") and now - m["ref_ts"] <= q["lay_max_ref_age_s"]]
    lays = V2.lay_candidates(snap, q)
    try:
        b = json.loads((RUNTIME_DIR / "odds_api_budget.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        b = {}
    from .feeds import league_key
    from .feeds.odds_api import credits_per_day
    fcfg = (settings.get("feed") or {})
    extra = {"enabled": fcfg.get("reference_extra") == "oddspapi",
             "note": next((p.strip() for p in str((snap.get("health") or {}).get("source", "")).split("·") if "OddsPapi" in p), None),
             "fresh": sum(1 for m in fresh if m.get("ref_src") == "oddspapi")}
    try:
        ob = json.loads((RUNTIME_DIR / "oddspapi_budget.json").read_text(encoding="utf-8"))
        extra["used_month"] = ob.get("used")
    except (OSError, ValueError):
        extra["used_month"] = None
    cov, unc = collections.Counter(), collections.Counter()
    for m in cand:
        (cov if league_key(m.get("league")) else unc)[m.get("league") or "?"] += 1
    return {"ts": time.time(), "covered": cov.most_common(5), "uncovered": unc.most_common(6),
            "soccer": len(soccer), "in_window": len(in_win), "candidates": len(cand), "fresh_ref": len(fresh),
            "lays": len(lays), "best_edge": max((p["edge"] for p in lays), default=None),
            "credits_used_today": b.get("used"), "credits_left": b.get("remaining"),
            "credits_day_cap": credits_per_day((fcfg.get("odds_api") or {})), "oddspapi": extra}


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
    try:                                                # lay bloccati dalle lezioni di Leo negli ultimi 14 giorni
        lay_leo = store.query("SELECT blocked_by, COUNT(*) n, MAX(ts) last FROM coach_entries WHERE side='LAY' AND ts >= ? "
                              "AND blocked_by LIKE 'Lezione di Leo%' GROUP BY blocked_by ORDER BY n DESC", (_since(14 * 24),))
    except Exception:
        lay_leo = []
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
    fun_open = sum(1 for o in opened if str(o["strategy_id"]).startswith("S10_divertimento") and o not in stuck)
    if fun_open >= lim.get("fun_max_open", 3):
        blocks.append((f"{fun_open} puntate 4fun già aperte (tetto {lim.get('fun_max_open', 3)})",
                       "Riparte quando una si chiude." + (" ATTENZIONE: " + ", ".join(f"#{o['id']} {o['label']}" for o in stuck)
                                                          + " risultano aperte da più di 6 ore dopo l'inizio: controlla su Betfair "
                                                          "('Le mie scommesse') se sono state regolate." if stuck else "")))
    if today_n >= lim.get("fun_max_bets_per_day", 10):
        blocks.append((f"Già {today_n} puntate 4fun oggi (tetto {lim.get('fun_max_bets_per_day', 10)})",
                       "Riparte da sola domani."))
    return {"ts": time.time(), "hours": hours, "blocks": blocks, "open": len(opened), "stuck": len(stuck), "today": today_n, "vetoes": vetoes.most_common(6), "approved": approved,
            "last_bet": last[0]["ts"] if last else None, "rules": rules,
            "lay_leo": [dict(r) for r in lay_leo], "bankroll": rs.get("bankroll"), "kill_floor": rs.get("kill_floor"), "lay_diag": store.get("lay_diag")}


def text(c: dict) -> str:
    L = [f"Perché il bot non punta (ultime {c['hours']:.0f} ore)", ""]
    if c["blocks"]:
        L.append("FRENI ATTIVI (bloccano tutto):")
        L += [f"• {what} → {fix}" for what, fix in c["blocks"]]
        L.append("")
    else:
        L += ["Nessun freno generale attivo (kill switch, pause, stop del giorno): il bot cerca e valuta.", ""]
    L.append(f"Puntate approvate: {c['approved']} · ultima puntata vera: {c['last_bet'] or 'mai'} · aperte ora: "
             f"{c['open']} · 4fun oggi: {c['today']}")
    if c["vetoes"]:
        L.append("Motivi dei veti (il primo di ogni proposta bocciata):")
        L += [f"• {n}× {why}" for why, n in c["vetoes"]]
    else:
        L.append("Nessun veto: probabilmente non ci sono partite adatte in questo momento (quote 1,40-3,00, libro "
                 "stretto, 10-240 minuti all'inizio) o è già al limite di puntate del giorno / aperte.")
    d = c.get("lay_diag")
    if d:
        L += ["", "Lay (ultimo ciclo): " + f"{d['soccer']} partite di calcio, {d['in_window']} entro 4 ore, {d['candidates']} con un esito a "
              f"quota 3-5, di cui {d['fresh_ref']} con Pinnacle fresco → {d['lays']} lay di valore"
              + (f" (miglior EV {d['best_edge']:+.1%})" if d.get("best_edge") is not None else "")
              + f". Crediti Odds API: {d['credits_used_today']} usati oggi su {d.get('credits_day_cap', '?')}, {d['credits_left']} rimasti."]
        x = d.get("oddspapi") or {}
        L.append("  Pinnacle di scorta (OddsPapi): " + ("spento" if not x.get("enabled") else
                 f"acceso · richieste del mese {x.get('used_month') if x.get('used_month') is not None else '?'}/200 · "
                 f"{x.get('fresh', 0)} partite fresche da lì" + (f" · ultimo stato: {x['note']}" if x.get("note") else "")))
        if d.get("covered") or d.get("uncovered"):
            L.append("  Con Pinnacle disponibile: " + (", ".join(f"{n} ({k})" for n, k in d.get("covered", [])) or "nessun campionato")
                     + " · senza Pinnacle (non coperti, nessun lay possibile): "
                     + (", ".join(f"{n} ({k})" for n, k in d.get("uncovered", [])) or "—"))
        if d["candidates"] and not d["fresh_ref"]:
            L.append("  → ci sono partite adatte ma Pinnacle è vecchio o assente: il riferimento viene richiesto da solo (max "
                     "1 credito per campionato ogni 40 minuti). Se resta così per ore, controlla i crediti o il campionato.")
    ll = c.get("lay_leo") or []
    L += ["", "Lay bloccati dalle lezioni di Leo (ultimi 14 giorni): " + (
        f"{sum(r['n'] for r in ll)} proposte, ultima {str(ll[0]['last'])[:16]} — " + "; ".join(f"{r['n']}× {r['blocked_by'].replace('Lezione di Leo: ', '')[:70]}" for r in ll[:3])
        if ll else "nessuno")]
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
