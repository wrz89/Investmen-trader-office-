"""Il bollettino di Leo: ogni mattina, cosa funziona e cosa no, sui dati VERI.

Leo mette insieme in un messaggio (Telegram, dashboard, runtime/reports/bollettino.md):
  1. soldi veri: le puntate di S10 (e di chi è in live) negli ultimi 7 giorni e in totale;
  2. la classifica delle strategie sull'esame per il live (prezzi veri di betfair.it, anche quelle in ombra), con
     CLV e margine d'errore: chi è davanti, chi è bocciata, quante puntate mancano;
  3. le misure: ultimo test rapido e ultimo `orizzonti` (se fatti), giorni di prezzi registrati;
  4. la proposta: se una strategia supera l'esame e non è in live, propone di metterla al posto del divertimento.
     Il bot non cambia MAI da solo le strategie con soldi veri: la decisione resta dell'utente.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from .bankroll import TZ
from .config import REPORTS_DIR
from .esame import EARLY_MIN

SEND_HOUR = 8                        # ora italiana del bollettino


def _pct(x) -> str:
    return "n.d." if x is None else f"{x:+.1%}"


def _read_json(name: str) -> dict | None:
    try:
        return json.loads((REPORTS_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def real_money(store, days: int = 7) -> dict:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")   # ts delle puntate in UTC
    rows = store.query("SELECT strategy_id, status, pnl, stake, ts FROM bets WHERE mode='live' AND status NOT IN ('OPEN')")
    out = {}
    for label, rr in (("settimana", [r for r in rows if (r["ts"] or "") >= since]), ("totale", rows)):
        n = len(rr)
        won = sum(1 for r in rr if (r["pnl"] or 0) > 0)
        stake = sum(r["stake"] or 0 for r in rr)
        out[label] = {"n": n, "vinte": won, "pnl": sum(r["pnl"] or 0 for r in rr), "roi": (sum(r["pnl"] or 0 for r in rr) / stake)
                      if stake else None}
    out["aperte"] = store.query("SELECT COUNT(*) n FROM bets WHERE mode='live' AND status='OPEN'")[0]["n"]
    return out


def build(store, settings: dict) -> dict:
    from .esame import evaluate_all
    from .feeds.recorder import recording_status
    ids = list(dict.fromkeys((settings.get("active_strategies") or []) + (settings.get("observe_strategies") or [])))
    exam = evaluate_all(store, ids)
    order = {"PRONTA": 0, "IN ESAME": 1, "BOCCIATA": 2}
    exam.sort(key=lambda r: (order[r["verdict"]], -(r["clv_lo"] if r["clv_lo"] is not None else -9), -r["n"]))
    live = set(settings.get("live_strategies") or []) if settings.get("mode") == "live" else set()
    proposals = [r["strategy_id"] for r in exam if r["verdict"] == "PRONTA" and r["strategy_id"].split(" · ")[0] not in live]
    from .multiple import summary as multiple_summary
    return {"ts": time.time(), "live": sorted(live), "soldi_veri": real_money(store), "esame": exam,
            "multiple": multiple_summary(store),
            "test_rapido": _read_json("test_rapido.json"), "orizzonti": _read_json("orizzonti.json"),
            "registrazioni": recording_status(), "proposte": proposals}


def text(b: dict) -> str:
    L = ["📋 Bollettino di Leo", ""]
    sv = b["soldi_veri"]
    if b["live"] or sv["totale"]["n"]:
        w, t = sv["settimana"], sv["totale"]
        L += [f"Soldi veri ({', '.join(b['live']) or 'live spento'}):",
              f"• 7 giorni: {w['n']} puntate, {w['vinte']} vinte, {w['pnl']:+.2f} € (ROI {_pct(w['roi'])})",
              f"• totale: {t['n']} puntate, {t['pnl']:+.2f} € · aperte ora: {sv['aperte']}", ""]
    else:
        L += ["Soldi veri: live spento (paper).", ""]
    L.append("Classifica sui prezzi veri di betfair.it (esame per il live):")
    base_n = {r["strategy_id"]: r["n"] for r in b["esame"] if " · " not in r["strategy_id"]}
    empty = []
    for r in b["esame"]:
        sid = r["strategy_id"]
        if r["n"] == 0:                                    # niente dati: una riga sola in fondo
            empty.append(sid.replace("_favoriti_exchange", "").replace("_divertimento", " 4fun").replace("_lay_valore", " lay"))
            continue
        if " · solo back" in sid and base_n.get(sid.split(" · ")[0]) == r["n"]:
            continue                                       # identica alla riga della strategia: inutile ripeterla
        ci = "" if r["clv_lo"] is None else f" [{_pct(r['clv_lo'])} … {_pct(r['clv_hi'])}]"
        L.append(f"• {sid}: {r['verdict']} · {r['n']}/{r['need']} puntate · CLV {_pct(r['clv'])}{ci} · "
                 f"ROI {_pct(r['roi'])}")
    if empty:
        L.append("• ancora senza puntate sui prezzi veri: " + ", ".join(empty))
    lays = [r for r in b["esame"] if ("lay" in r["strategy_id"].lower() or "S09" in r["strategy_id"]) and r["n"] > 0]
    if lays:                                           # i lay sono l'unica ipotesi con un vantaggio: si guardano prima dei 200 casi
        L += ["", "Lay, lettura anticipata (non è un verdetto: servono 200 casi):"]
        for r in lays:
            txt = {"POSITIVA": "intervallo sopra zero: promettente", "NEGATIVA": "intervallo sotto zero: il lay non funziona",
                   "INCERTA": "intervallo attraversa lo zero: non si può dire"}.get(r.get("early"))
            L.append(f"• {r['strategy_id']}: {r['n']} casi" + (f" · {txt}" if txt else f" · ancora meno di 20 casi (ne servono {EARLY_MIN})")
                     + ("" if r["clv"] is None else f" · CLV {_pct(r['clv'])}"))
    tr, oz, rec = b.get("test_rapido"), b.get("orizzonti"), b.get("registrazioni") or {}
    mu = b.get("multiple")
    if mu and (mu["doppie"]["n"] or mu["triple"]["n"]):
        L += ["", "Multiple virtuali (ombra):"]
        for k in ("doppie", "triple"):
            d = mu[k]
            if d["n"]:
                L.append(f"• {k}: {d['n']} · vinte {d['vinte']:.0%} (attese {d['attese']:.0%}) · ROI {_pct(d['roi'])} ± {2 * d['se']:.0%}"
                         f" (singole {_pct(mu['singole']['roi'])} su {mu['singole']['n']})")
    L += ["", "Misure:"]
    if tr:
        extra = "; ".join(f"{k}: {v['verdetto']}" for k, v in (tr.get("sport") or {}).items())
        L.append(f"• test rapido del {datetime.fromtimestamp(tr['ts'], TZ):%d/%m}: S09 {tr.get('verdetto')}"
                 + (f"; {extra}" if extra else ""))
    else:
        L.append("• test rapido: non ancora fatto (test_rapido.bat)")
    L.append(f"• orizzonti: {oz['verdetto']} ({oz['partite']} partite)" if oz else "• orizzonti: non ancora fatto (orizzonti.bat)")
    L.append(f"• prezzi registrati: {rec.get('days', 0)} giorni su {rec.get('target_days', 28)} che servono")
    L.append("")
    if b["proposte"]:
        L.append("Proposta: " + ", ".join(b["proposte"]) + " ha superato l'esame. Se vuoi metterla con i soldi veri al "
                 "posto del 4fun, dimmelo: il bot non lo fa da solo.")
    else:
        L.append("Nessuna strategia ha ancora superato l'esame: si continua a misurare, il 4fun resta com'è.")
    return "\n".join(L)


def due(store, now: datetime | None = None) -> bool:
    now = now or datetime.now(TZ)
    return now.hour >= SEND_HOUR and store.get("bollettino_day") != now.strftime("%Y-%m-%d")


def save(b: dict) -> str:
    t = text(b)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "bollettino.md").write_text(t + "\n", encoding="utf-8")
    return t
