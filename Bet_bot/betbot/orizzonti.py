"""Quando conviene entrare? CLV e liquidità di betfair.it per orizzonte d'ingresso, dalle registrazioni.

Domanda (dalle analisi pubbliche su Pinnacle: CLV ~0,4% entrando 1 ora prima, ~1,1% a 24 ore, ~1,2% a 72 ore):
su betfair.it i prezzi lontani dal fischio d'inizio sono più "sbagliati", e quindi più sfruttabili, di quelli vicini?
E c'è denaro abbastanza per entrare?

Per ogni partita registrata (calcio, football americano, tennis, basket, baseball: prezzi VERI di betfair.it) e per ogni orizzonte (72, 48, 24,
12, 6, 3 e 1 ora prima dell'inizio) si prende la fotografia più vicina a quel momento e si misura, per ogni esito:
  • liquidità: denaro al miglior prezzo back e distanza tra back e lay;
  • CLV del back contro la chiusura di Betfair (ultimo prezzo medio prima dell'inizio, margine tolto): c'è sempre;
  • CLV contro la chiusura di Pinnacle, solo se quella quota era fresca (≤ 90 minuti prima dell'inizio);
  • il gruppo che conta: esiti in cui betfair.it pagava più di Pinnacle (EV netto ≥ 2%, Pinnacle fresco a quell'ora)
    → il mercato poi ci ha dato ragione?
Nessuna puntata e nessun credito consumato: legge solo runtime/recordings/.
"""
from __future__ import annotations

import gzip
import json
import math
from datetime import datetime
from pathlib import Path

from .odds import remove_margin

HORIZONS_H = (72, 48, 24, 12, 6, 3, 1)
COMM = 0.045
PIN = "pinnacle"


def _tol_s(h: float) -> float:
    """Tolleranza attorno all'orizzonte: 10 minuti a 1 ora, 10% dell'orizzonte oltre (7 ore a 72)."""
    return max(600.0, 0.1 * h * 3600)


def _pin_max_age_s(h: float) -> float:
    return max(90 * 60.0, 0.25 * h * 3600)


FAMILIES = (("soccer", "calcio"), ("americanfootball", "football americano"), ("tennis", "tennis"),
            ("basketball", "basket"), ("baseball", "baseball"))


def family(sport: str | None) -> str | None:
    s = sport or ""
    return next((name for pre, name in FAMILIES if s.startswith(pre)), None)


def _pin_prices(books: dict | None) -> dict | None:
    for name, prices in (books or {}).items():
        if PIN in name.lower():
            return prices
    return None


def _fair(prices: dict | None, sels: list[str]) -> dict | None:
    if not prices or any(not prices.get(s) or prices[s] <= 1 for s in sels):
        return None
    try:
        return remove_margin({s: float(prices[s]) for s in sels})
    except Exception:
        return None


def _bf_close_fair(ex: dict, sels: list[str]) -> dict | None:
    """Probabilità "giuste" alla chiusura di Betfair: prezzo medio tra back e lay, margine tolto (proporzionale)."""
    mids = {}
    for s in sels:
        b, l = (ex.get(s) or {}).get("back"), (ex.get(s) or {}).get("lay")
        if not b or not l or l < b or l / b > 1.25:        # libro vuoto o troppo largo: non è un prezzo
            return None
        mids[s] = (b + l) / 2
    tot = sum(1 / v for v in mids.values())
    return {s: (1 / v) / tot for s, v in mids.items()}


def scan(files: list[Path]) -> dict:
    """Legge le registrazioni una riga alla volta e tiene, per ogni partita, solo le fotografie che servono."""
    M: dict[str, dict] = {}
    for f in sorted(files):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                snap = json.loads(line)
                t = snap.get("sim_time") or snap["ts"]
                for mid, m in (snap.get("matches") or {}).items():
                    fam = family(m.get("sport"))
                    if not fam or m.get("status") != "SCHEDULED" or not m.get("exchange") or not m.get("kickoff"):
                        continue
                    ko = datetime.fromisoformat(m["kickoff"]).timestamp()
                    if t >= ko:
                        continue
                    r = M.setdefault(mid, {"match": f"{m.get('home')} - {m.get('away')}", "league": m.get("league"),
                                           "family": fam, "ko": ko, "at": {}, "close": None, "pin": None, "pin_ts": None})
                    r["ko"] = ko                               # l'orario può essere corretto da Betfair
                    pin = _pin_prices(m.get("books"))
                    if pin and pin != r["pin"]:                 # quota di Pinnacle nuova: da qui parte la sua età
                        r["pin"], r["pin_ts"] = pin, m.get("ref_ts") or t
                    elif pin and m.get("ref_ts"):
                        r["pin_ts"] = m["ref_ts"]
                    obs = {"t": t, "ex": m["exchange"], "pin": r["pin"], "pin_ts": r["pin_ts"]}
                    r["close"] = obs                            # l'ultima fotografia prima dell'inizio
                    for h in HORIZONS_H:
                        target = ko - h * 3600
                        d = abs(t - target)
                        if d <= _tol_s(h) and (h not in r["at"] or d < abs(r["at"][h]["t"] - target)):
                            r["at"][h] = obs
    return M


def _mean_ci(v: list[float]):
    if not v:
        return None, None, None
    m = sum(v) / len(v)
    if len(v) < 2:
        return m, None, None
    se = math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1)) / math.sqrt(len(v))
    return m, m - 1.96 * se, m + 1.96 * se


def _median(v: list[float]):
    v = sorted(v)
    return None if not v else v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2


def analyse(M: dict, min_edge: float = 0.02, close_max_before_s: float = 30 * 60) -> dict:
    out = {"partite": 0, "gruppi": {}}
    rows = []
    for mid, r in M.items():
        c = r["close"]
        if not c or r["ko"] - c["t"] > close_max_before_s:     # chiusura non vista (bot spento prima dell'inizio)
            continue
        sels = ["home", "draw", "away"] if r["family"] == "calcio" else ["home", "away"]
        bf_close = _bf_close_fair(c["ex"], sels)
        pin_close = _fair(c["pin"], sels) if c["pin"] and c["pin_ts"] and r["ko"] - c["pin_ts"] <= 90 * 60 else None
        if not bf_close and not pin_close:
            continue
        out["partite"] += 1
        for h, o in r["at"].items():
            pin_h = (_fair(o["pin"], sels) if o["pin"] and o["pin_ts"] is not None
                     and o["t"] - o["pin_ts"] <= _pin_max_age_s(h) else None)
            for s in sels:
                b = o["ex"].get(s) or {}
                back, lay = b.get("back"), b.get("lay")
                if not back or back <= 1:
                    continue
                row = {"family": r["family"], "h": h, "size": b.get("back_size_best") or b.get("back_size") or 0.0,
                       "spread": (lay / back - 1) if lay else None}
                if bf_close:
                    row["clv_bf"] = back * bf_close[s] - 1
                if pin_close:
                    row["clv_pin"] = back * pin_close[s] - 1
                if pin_h and pin_close:
                    p = pin_h[s]
                    row["ev"] = p * (back - 1) * (1 - COMM) - (1 - p)
                    row["gap"] = back * p - 1
                rows.append(row)
    for _, fam in FAMILIES:
        g = {}
        for h in HORIZONS_H:
            rr = [x for x in rows if x["family"] == fam and x["h"] == h]
            if not rr:
                continue
            value = [x["clv_pin"] for x in rr if x.get("ev") is not None and x["ev"] >= min_edge]
            m_bf, lo_bf, hi_bf = _mean_ci([x["clv_bf"] for x in rr if "clv_bf" in x])
            m_pin, lo_pin, hi_pin = _mean_ci([x["clv_pin"] for x in rr if "clv_pin" in x])
            m_v, lo_v, hi_v = _mean_ci(value)
            g[h] = {"esiti": len(rr), "liquidita_mediana": _median([x["size"] for x in rr]),
                    "quota_con_10eur": sum(1 for x in rr if x["size"] >= 10) / len(rr),
                    "spread_mediano": _median([x["spread"] for x in rr if x["spread"] is not None]),
                    "scarto_medio_pin": (sum(abs(x["gap"]) for x in rr if "gap" in x) / max(1, sum(1 for x in rr if "gap" in x))
                                         if any("gap" in x for x in rr) else None),
                    "clv_bf": (m_bf, lo_bf, hi_bf), "clv_pin": (m_pin, lo_pin, hi_pin),
                    "valore": {"n": len(value), "clv": m_v, "lo": lo_v, "hi": hi_v}}
        if g:
            out["gruppi"][fam] = g
    best = None
    for fam, g in out["gruppi"].items():
        for h, v in g.items():
            val = v["valore"]
            if val["n"] >= 20 and val["lo"] is not None and val["lo"] > 0 and (best is None or val["clv"] > best[2]):
                best = (fam, h, val["clv"])
    out["verdetto"] = (f"SEGNALE: {best[0]} a {best[1]} ore dall'inizio" if best else
                       "NESSUN ORIZZONTE CON UN VANTAGGIO SOLIDO (servono almeno 20 esiti di valore con l'intervallo sopra zero)")
    return out


def report(res: dict, days: int | None = None) -> str:
    pct = lambda x: "—" if x is None else f"{x:+.1%}"
    ci = lambda t: "—" if t[0] is None else f"{t[0]:+.1%}" + ("" if t[1] is None else f" ({t[1]:+.1%} … {t[2]:+.1%})")
    L = ["# Quando conviene entrare su betfair.it", "",
         f"{res['partite']} partite con la chiusura registrata" + (f", {days} giorni di registrazioni" if days else "") + ".",
         "", f"**Verdetto: {res['verdetto']}**", "",
         "- *Liquidità*: euro al miglior prezzo back (mediana) e quota di esiti con almeno 10 €.",
         "- *CLV Betfair*: prezzo back contro la chiusura di betfair.it. Di solito negativo quanto lo spread.",
         "- *Valore*: esiti in cui betfair.it pagava più di Pinnacle (EV netto ≥ 2%). Conta il loro CLV contro la "
         "chiusura di Pinnacle: sopra zero con l'intervallo tutto positivo = vantaggio vero a quell'orizzonte.", ""]
    for fam, g in res["gruppi"].items():
        L += [f"## {fam.capitalize()}", "",
              "| Ore prima | Esiti | Liquidità | ≥10 € | Spread | Scarto da Pinnacle | CLV Betfair | Valore: n | Valore: CLV |",
              "|---|---|---|---|---|---|---|---|---|"]
        for h in HORIZONS_H:
            v = g.get(h)
            if not v:
                continue
            liq = "—" if v["liquidita_mediana"] is None else f"{v['liquidita_mediana']:.0f} €"
            sp = "—" if v["spread_mediano"] is None else f"{v['spread_mediano']:.1%}"
            val = v["valore"]
            L.append(f"| {h} | {v['esiti']} | {liq} | {v['quota_con_10eur']:.0%} | {sp} | {pct(v['scarto_medio_pin'])} | "
                     f"{ci(v['clv_bf'])} | {val['n']} | {ci((val['clv'], val['lo'], val['hi']))} |")
        L.append("")
    L += ["Con il riferimento ogni 6 ore (piano gratuito di The Odds API) Pinnacle è fresco solo in parte degli orizzonti: "
          "il gruppo 'valore' cresce con i giorni registrati. Senza liquidità (≥ 10 €) un vantaggio non si può prendere."]
    return "\n".join(L) + "\n"


def run(since: str | None = None, until: str | None = None, out=print) -> dict:
    from .config import REPORTS_DIR
    from .feeds.recorder import recorded_files
    files = recorded_files(since, until)
    if not files:
        out("Nessuna registrazione in runtime/recordings/: serve il feed Betfair con record: true (bot acceso).")
        return {}
    res = analyse(scan(files))
    md = report(res, days=len(files))
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "orizzonti.md").write_text(md, encoding="utf-8")
    out(md)
    return res
