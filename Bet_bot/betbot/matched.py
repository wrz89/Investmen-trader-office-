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
            bf = m.get("betfair") or {}
            out.append({"match": f"{m.get('home')} - {m.get('away')}", "league": m.get("league"), "kickoff": m["kickoff"],
                        "sel": names.get(sel, sel), "sel_key": sel, "back": back, "lay": lay, "spread": round(spread, 4),
                        "lay_eur": round(size, 0), "market_id": bf.get("market_id"),
                        "selection_id": (bf.get("selection_ids") or {}).get(sel),
                        "team": {"home": m.get("home"), "away": m.get("away")}.get(sel, "Pareggio")})
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
FIELDS = ("bookmaker", "offerta", "tipo", "importo", "quota_min", "deposito", "bonus", "rollover", "scadenza", "profitto",
          "stato", "note")
TIPI = ("snr", "qualificante", "sr")
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
        if k in ("deposito", "bonus", "rollover", "profitto", "importo", "quota_min"):
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
    if row["tipo"] not in TIPI:
        row["tipo"] = "snr"
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


# ── consigli per un'offerta del registro (Telegram) ─────────────────────────────
BOOKMAKER_SITES = {"sisal": "sisal.it", "snai": "snai.it", "eurobet": "eurobet.it", "goldbet": "goldbet.it",
                   "lottomatica": "lottomatica.it", "better": "lottomatica.it", "bet365": "bet365.it",
                   "william": "williamhill.it", "betflag": "betflag.it", "planetwin": "planetwin365.it", "netbet": "netbet.it",
                   "starcasin": "starcasino.it", "betsson": "betsson.it", "bwin": "bwin.it", "leovegas": "leovegas.it",
                   "888": "888sport.it", "admiral": "admiralbet.it", "betway": "betway.it", "stanley": "stanleybet.it",
                   "sportium": "sportium.it", "domus": "domusbet.it", "codere": "codere.it", "vincitu": "vincitu.it",
                   "quigioco": "quigioco.it", "gioco digitale": "giocodigitale.it", "dazn": "daznbet.it",
                   "eplay": "eplay24.it", "pokerstars": "pokerstars.it", "stake": "stake.it", "marathon": "marathonbet.it"}


def bookmaker_link(name: str) -> str:
    n = (name or "").lower()
    site = next((v for k, v in BOOKMAKER_SITES.items() if k in n), None)
    if site:
        return f"https://www.{site}"
    from urllib.parse import quote
    return "https://www.google.com/search?q=" + quote(f"{name} scommesse")


def betfair_link(market_id: str | None) -> str | None:
    return f"https://www.betfair.it/exchange/plus/football/market/{market_id}" if market_id else None


def estimate(tipo: str, qb: float, amount: float, ql: float, c: float = COMMISSION) -> dict:
    if tipo == "qualificante":
        return qualifying(qb, amount, ql, c)
    return freebet(qb, amount, ql, c, stake_returned=(tipo == "sr"))


def advice(offer: dict, board: list[dict], available: float | None = None, c: float = COMMISSION) -> dict | None:
    """La partita migliore per coprire QUESTA offerta: quota lay nella fascia giusta per il tipo, spread stretto, abbastanza
    soldi al prezzo lay e responsabilità entro il saldo disponibile. La stima usa il back di Betfair come quota del
    bookmaker (quella vera di solito è un po' più bassa: il risultato peggiora di poco)."""
    tipo = offer.get("tipo") or "snr"
    amount = float(offer.get("importo") or offer.get("bonus") or 0)
    if amount <= 0:
        return None
    qmin = float(offer.get("quota_min") or 0)
    lo, hi = {"qualificante": (max(qmin, 1.5), max(qmin, 1.5) + 1.5),
              "sr": (max(qmin, 2.0), max(qmin, 2.0) + 2.5)}.get(tipo, (max(qmin, 3.0), max(qmin, 3.0) + 4.0))
    best = None
    for r in board:
        if not (lo <= r["back"] <= hi) or not r.get("market_id") or r.get("selection_id") is None:
            continue
        try:
            e = estimate(tipo, r["back"], amount, r["lay"], c)
        except ValueError:
            continue
        if e["lay_stake"] > r["lay_eur"] or (available is not None and e["liability"] > available) or e["lay_stake"] < 0.5:
            continue
        if best is None or e["result"] > best["est"]["result"]:
            best = {"row": r, "est": e}
    if not best:
        return None
    r = best["row"]
    return {"offer_id": offer.get("id"), "tipo": tipo, "amount": amount, "match": r["match"], "league": r.get("league"),
            "kickoff": r["kickoff"], "team": r.get("team"), "sel": r["sel"], "sel_key": r.get("sel_key"),
            "back": r["back"], "lay": r["lay"], "market_id": r["market_id"], "selection_id": r["selection_id"],
            "est": best["est"], "bookmaker": offer.get("bookmaker"), "bookmaker_link": bookmaker_link(offer.get("bookmaker")),
            "betfair_link": betfair_link(r["market_id"])}


TIPO_NOMI = {"snr": "freebet (puntata non restituita)", "sr": "freebet (puntata restituita)", "qualificante": "scommessa qualificante"}


def advice_text(n: int, offer: dict, adv: dict | None) -> str:
    """Messaggio Telegram (testo semplice) con partita, quota da cercare, link e cosa scrivere dopo."""
    head = f"🧮 Offerta {n}: {offer.get('bookmaker')} · {offer.get('offerta') or TIPO_NOMI.get(offer.get('tipo'), '')}"
    if not adv:
        return (head + "\nOggi nessuna partita adatta su betfair.it (quota giusta, lay stretto, soldi al prezzo, responsabilità "
                "entro il saldo). Ti riscrivo quando c'è.")
    try:
        when = datetime.fromisoformat(adv["kickoff"]).astimezone().strftime("%a %d/%m %H:%M")
    except ValueError:
        when = adv["kickoff"]
    e = adv["est"]
    return (f"{head}\n"
            f"Partita: {adv['match']} ({adv.get('league') or ''}), {when}\n"
            f"Punta su: {adv['team']} (esito {adv['sel']}) · {TIPO_NOMI.get(adv['tipo'])} da {adv['amount']:.2f} €\n"
            f"Cerca sul bookmaker una quota vicina a {adv['back']:.2f} (minimo {max(adv['back'] - 0.15, 1.01):.2f}).\n"
            f"Stima: su Betfair banco {e['lay_stake']:.2f} € a {adv['lay']:.2f}, responsabilità {e['liability']:.2f} €, "
            f"risultato {e['result']:+.2f} € ({e['pct']:.0%}).\n"
            f"Bookmaker: {adv['bookmaker_link']}\n"
            f"Betfair: {adv['betfair_link']}\n"
            f"Dopo aver puntato scrivi: /coperto {n} QUOTA_PRESA   (es. /coperto {n} {adv['back']:.2f})")


# ── copertura su Betfair: /coperto (piano) → /conferma (lay vero) → regolamento ──
PENDING_S = 120          # la conferma vale 2 minuti
BOARD_MAX_AGE_S = 300    # prezzi del bot più vecchi di 5 minuti: non si piazza


def active_offers(rows: list[dict] | None = None) -> list[dict]:
    rows = load() if rows is None else rows
    return [r for r in rows if r.get("stato") in ("da fare", "in corso") and float(r.get("importo") or 0) > 0]


def _num(s: str) -> float:
    return float(str(s).replace(",", "."))


def _current(store, market_id, selection_id) -> tuple[dict | None, float | None]:
    board = store.get("mb_lay_board") or {}
    for r in board.get("rows") or []:
        if r.get("market_id") == market_id and r.get("selection_id") == selection_id:
            return r, board.get("ts")
    return None, board.get("ts")


def plan_cover(store, n: int, qb: float, amount: float | None = None, c: float = COMMISSION) -> str:
    offers = active_offers()
    if not 1 <= n <= len(offers):
        return f"Offerta {n} non trovata. Scrivi /offerte per l'elenco."
    offer = offers[n - 1]
    adv = (store.get("mb_advice") or {}).get(offer["id"])
    if not adv:
        return "Per questa offerta non ho ancora una partita consigliata: aspetta il mio messaggio o scrivi /offerte."
    row, ts = _current(store, adv["market_id"], adv["selection_id"])
    ql = (row or {}).get("lay") or adv["lay"]
    amount = amount or float(offer.get("importo") or 0)
    e = estimate(adv["tipo"], qb, amount, ql, c)
    store.set("mb_pending", {"offer_id": offer["id"], "n": n, "tipo": adv["tipo"], "qb": qb, "amount": amount, "lay": ql,
                             "lay_stake": e["lay_stake"], "liability": e["liability"], "market_id": adv["market_id"],
                             "selection_id": adv["selection_id"], "match": adv["match"], "team": adv["team"],
                             "expires": time.time() + PENDING_S})
    return (f"Copertura per {offer['bookmaker']} ({adv['match']}, {adv['team']}):\n"
            f"hai preso {qb:.2f} con {amount:.2f} € ({TIPO_NOMI.get(adv['tipo'])}).\n"
            f"Banco su Betfair {e['lay_stake']:.2f} € a {ql:.2f} · responsabilità {e['liability']:.2f} € · "
            f"risultato garantito {e['result']:+.2f} €.\n"
            f"Scrivi /conferma entro 2 minuti per piazzare il lay (soldi veri). Se non confermi non succede niente.")


def confirm_cover(store, client, c: float = COMMISSION, now: float | None = None) -> str:
    now = now or time.time()
    p = store.get("mb_pending")
    if not p or p.get("expires", 0) < now:
        store.set("mb_pending", None)
        return "Nessuna copertura da confermare (o sono passati più di 2 minuti): riscrivi /coperto."
    if client is None:
        return "Il conto Betfair non è collegato al bot: copertura NON piazzata. Fai il lay a mano con il calcolatore."
    row, ts = _current(store, p["market_id"], p["selection_id"])
    if not row or not ts or now - ts > BOARD_MAX_AGE_S:
        return ("Prezzi di Betfair non aggiornati per questa partita: copertura NON piazzata. Riprova tra un minuto "
                "o fai il lay a mano.")
    ql = row["lay"]
    e = estimate(p["tipo"], p["qb"], p["amount"], ql, c)
    avail = (store.get("live_balance") or {}).get("available")
    if avail is not None and e["liability"] > float(avail):
        return f"Responsabilità {e['liability']:.2f} € oltre il disponibile su Betfair ({float(avail):.2f} €): NON piazzo."
    if e["lay_stake"] > row.get("lay_eur", 0):
        return f"Al prezzo {ql:.2f} ci sono solo {row.get('lay_eur', 0):.0f} €: NON piazzo, fai il lay a mano su un'altra quota."
    ref = "mb-" + uuid.uuid4().hex[:12]
    store.set("mb_pending", None)
    res = client.place(p["market_id"], p["selection_id"], "LAY", ql, e["lay_stake"], fill_or_kill=True,
                       order_ref=ref, strategy_ref="MB")
    if (res.get("matched") or 0) < e["lay_stake"] * 0.99:
        return (f"Lay NON abbinato a {ql:.2f} (il prezzo si è mosso). Nessun soldo impegnato. Riscrivi /coperto {p['n']} "
                f"{p['qb']:.2f} per ricalcolare, oppure fallo a mano.")
    orders = store.get("mb_orders") or []
    orders.append({"bet_id": res.get("bet_id"), "ref": ref, "offer_id": p["offer_id"], "market_id": p["market_id"],
                   "selection_id": p["selection_id"], "match": p["match"], "team": p["team"], "price": res.get("avg_price") or ql,
                   "stake": e["lay_stake"], "liability": e["liability"], "expected": e["result"], "ts": now, "status": "aperto"})
    store.set("mb_orders", orders)
    rows = load()
    for r in rows:
        if r.get("id") == p["offer_id"]:
            r["stato"] = "in corso"
            r["note"] = (r.get("note") or "") + f" lay {e['lay_stake']:.2f}@{ql:.2f}"
    _save(rows)
    return (f"✅ Copertura fatta: bancato {e['lay_stake']:.2f} € a {ql:.2f} su {p['match']} ({p['team']}). "
            f"Risultato garantito circa {e['result']:+.2f} €. Ti scrivo quando Betfair la regola.")


def settle_covers(store, client, c: float = COMMISSION) -> list[str]:
    """Esito dei lay di copertura regolati da Betfair: aggiorna il totale del conto Betfair dovuto al matched betting
    (serve al controllo del saldo del bot, che altrimenti scambierebbe un lay perso per un ammanco)."""
    orders = store.get("mb_orders") or []
    open_ = [o for o in orders if o.get("status") == "aperto" and o.get("bet_id")]
    msgs = []
    if open_ and client is not None:
        res = client.cleared([o["bet_id"] for o in open_])
        for o in open_:
            r = res.get(o["bet_id"])
            if not r:
                continue
            gross = r["profit"] if r["status"] == "SETTLED" else 0.0
            o["pnl"] = round(gross * (1 - c) if gross > 0 else gross, 2)
            o["status"] = "chiuso"
            import html as _h
            msgs.append(f"Copertura regolata: {_h.escape(o['match'])} ({_h.escape(str(o['team']))}) · su Betfair {o['pnl']:+.2f} €. "
                        "Ricorda di segnare nel registro anche l'esito sul bookmaker.")
        store.set("mb_orders", orders)
    store.set("mb_betfair_pnl", round(sum(o.get("pnl") or 0 for o in orders if o.get("status") == "chiuso"), 2))
    return msgs
