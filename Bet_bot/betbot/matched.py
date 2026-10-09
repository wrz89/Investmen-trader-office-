"""Matched betting: calcoli di copertura su betfair.it, registro dei bonus e partite adatte a coprirsi.

Il bot NON punta nulla sui siti dei bookmaker (vietato dai loro termini) e non piazza i lay da solo: qui ci sono solo
calcoli, un registro in runtime/bonus.json e l'elenco delle partite di betfair.it con un lay stretto e liquido, preso dai
prezzi che il bot legge già. Commissione di betfair.it: 4,5% sulla vincita netta.

Formule (Qb quota back del bookmaker, Ql quota lay, c commissione):
  qualificante:            L = B·Qb / (Ql − c)      risultato = B·(Qb−1) − L·(Ql−1)  (uguale nei due casi)
  freebet SNR (puntata non restituita): L = F·(Qb−1) / (Ql − c)   profitto = L·(1−c)
  freebet SR  (puntata restituita):     L = F·Qb / (Ql − c)       profitto = L·(1−c)
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime

from .config import RUNTIME_DIR

COMMISSION = 0.045


def _check(qb: float, ql: float, amount: float, c: float) -> None:
    if qb <= 1 or ql <= 1 or amount <= 0:
        raise ValueError("Quote maggiori di 1 e importo maggiore di 0.")
    if ql <= c:
        raise ValueError("Quota lay non valida.")


def qualifying(qb: float, stake: float, ql: float, c: float = COMMISSION) -> dict:
    """Scommessa con soldi propri coperta: risultato quasi uguale qualunque cosa succeda (piccola perdita)."""
    _check(qb, ql, stake, c)
    lay = stake * qb / (ql - c)
    if_back = stake * (qb - 1) - lay * (ql - 1)               # vince l'esito: incassi dal bookmaker, paghi il lay
    if_lay = -stake + lay * (1 - c)                           # non vince: perdi la puntata, incassi il lay
    return {"tipo": "qualificante", "lay_stake": round(lay, 2), "liability": round(lay * (ql - 1), 2),
            "if_back_wins": round(if_back, 2), "if_lay_wins": round(if_lay, 2),
            "result": round(min(if_back, if_lay), 2), "pct": round(min(if_back, if_lay) / stake, 4)}


def freebet(qb: float, free: float, ql: float, c: float = COMMISSION, stake_returned: bool = False) -> dict:
    """Freebet coperta: profitto garantito. SNR = la puntata gratuita non torna indietro (il caso italiano normale)."""
    _check(qb, ql, free, c)
    lay = free * (qb if stake_returned else qb - 1) / (ql - c)
    if_back = free * (qb if stake_returned else qb - 1) - lay * (ql - 1)
    if_lay = lay * (1 - c)
    return {"tipo": "freebet SR" if stake_returned else "freebet SNR", "lay_stake": round(lay, 2),
            "liability": round(lay * (ql - 1), 2), "if_back_wins": round(if_back, 2), "if_lay_wins": round(if_lay, 2),
            "result": round(min(if_back, if_lay), 2), "pct": round(min(if_back, if_lay) / free, 4)}


# ── partite di betfair.it adatte alla copertura ──────────────────────────────────
def lay_board(snap: dict, max_hours: float = 48, min_lay_eur: float = 30.0, max_spread: float = 0.03,
              lay_range: tuple[float, float] = (1.3, 8.0), limit: int = 80) -> list[dict]:
    """Esiti di calcio con lay stretto (back e lay vicini) e soldi al miglior prezzo lay: dove coprirsi costa meno."""
    now = snap.get("sim_time") or snap.get("ts") or time.time()
    names = {"home": "1", "draw": "X", "away": "2"}
    out = []
    for m in (snap.get("matches") or {}).values():
        if m.get("status") != "SCHEDULED" or not str(m.get("sport", "")).startswith("soccer") or not m.get("exchange"):
            continue
        try:
            start = datetime.fromisoformat(m["kickoff"]).timestamp()
        except (KeyError, ValueError):
            continue
        if not 600 <= start - now <= max_hours * 3600:
            continue
        for sel, b in m["exchange"].items():
            back, lay = b.get("back"), b.get("lay")
            size = b.get("lay_size_best") or 0.0
            if not back or not lay or not lay_range[0] <= lay <= lay_range[1] or size < min_lay_eur:
                continue
            spread = lay / back - 1
            if spread > max_spread:
                continue
            out.append({"match": f"{m.get('home')} - {m.get('away')}", "league": m.get("league"), "kickoff": m["kickoff"],
                        "sel": names.get(sel, sel), "back": back, "lay": lay, "spread": round(spread, 4),
                        "lay_eur": round(size, 0)})
    out.sort(key=lambda r: (r["spread"], r["kickoff"]))
    return out[:limit]


def telegram_text(rows: list[dict], n: int = 8, html: bool = True) -> str:
    """Messaggio Telegram: le partite dove coprire un bonus su betfair.it costa meno. NON sono pronostici.
    html=False per le risposte ai comandi (inviate come testo semplice)."""
    import html as _h
    esc = _h.escape if html else (lambda x: x)
    if not rows:
        return "Matched betting: nessuna partita adatta alla copertura nelle prossime 48 ore (lay stretto e liquido)."
    L = ["🧮 " + ("<b>Matched betting: dove coprirsi su betfair.it</b>" if html else "Matched betting: dove coprirsi su betfair.it"),
         "Non sono pronostici: sono le partite col lay più stretto, dove coprire un bonus costa meno.", ""]
    for r in rows[:n]:
        try:
            when = datetime.fromisoformat(r["kickoff"]).astimezone().strftime("%a %H:%M")
        except (KeyError, ValueError):
            when = "?"
        L.append(f"• {when} · {esc(r['match'])} ({esc(r.get('league') or '')}) · esito {r['sel']}: back {r['back']} / lay {r['lay']} "
                 f"· spread {r['spread']:.1%} · {r['lay_eur']:.0f} € al lay")
    L += ["", "Prima il lay su Betfair (abbinato per intero), poi la puntata sul bookmaker. Calcoli: pagina /matched della dashboard."]
    return "\n".join(L)


# ── registro dei bonus (runtime/bonus.json, solo sul tuo PC) ─────────────────────
FIELDS = ("bookmaker", "offerta", "deposito", "bonus", "rollover", "scadenza", "profitto", "stato", "note")
STATI = ("da fare", "in corso", "chiuso", "saltato")


def _path():
    return RUNTIME_DIR / "bonus.json"


def load() -> list[dict]:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(rows: list[dict]) -> None:
    _path().parent.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


def _clean(body: dict) -> dict:
    row = {}
    for k in FIELDS:
        v = body.get(k)
        if k in ("deposito", "bonus", "rollover", "profitto"):
            try:
                row[k] = round(float(str(v or 0).replace(",", ".")), 2)
            except ValueError:
                raise ValueError(f"{k}: numero non valido")
        else:
            row[k] = str(v or "").strip()[:200]
    if not row["bookmaker"]:
        raise ValueError("Scrivi il nome del bookmaker.")
    if row["stato"] not in STATI:
        row["stato"] = "da fare"
    return row


def save_row(body: dict) -> dict:
    rows = load()
    row = _clean(body)
    rid = str(body.get("id") or "")
    for r in rows:
        if r.get("id") == rid:
            r.update(row)
            _save(rows)
            return r
    row["id"] = uuid.uuid4().hex[:10]
    rows.append(row)
    _save(rows)
    return row


def delete_row(rid: str) -> None:
    _save([r for r in load() if r.get("id") != str(rid)])


def summary(rows: list[dict] | None = None) -> dict:
    rows = load() if rows is None else rows
    done = [r for r in rows if r.get("stato") == "chiuso"]
    profit = sum(r.get("profitto") or 0 for r in done)
    blocked = sum(r.get("deposito") or 0 for r in rows if r.get("stato") == "in corso")
    return {"n": len(rows), "chiusi": len(done), "profitto": round(profit, 2), "bloccato": round(blocked, 2),
            "aperti": sum(1 for r in rows if r.get("stato") in ("da fare", "in corso"))}
