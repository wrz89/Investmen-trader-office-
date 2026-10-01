"""Allenamento dei ragazzi: le strategie rigiocano gli ultimi anni SENZA vedere il risultato, poi Leo fa l'autopsia.

Due parti:
  1. "Azzeccano il risultato?" (5 anni, tutte le partite con Pinnacle): per ogni partita si sceglie l'esito più
     probabile secondo Pinnacle al momento della raccolta (venerdì/martedì) e si confronta con il risultato. La tabella
     di calibrazione dice se "al 70%" si vince davvero 7 volte su 10.
  2. Le strategie sui prezzi VERI di Betfair (colonne BFE di football-data, dal 2024/25, record rotti esclusi):
       S05 favoriti con EV ≥ 2%, S09 lay di valore (lay stimato 2 tick sopra il back), S10 divertimento (max 5 al giorno).
     Prezzo e riferimento sono presi NELLO STESSO MOMENTO; la chiusura di Pinnacle serve solo per l'autopsia.
Autopsia di ogni puntata con le stesse cause di Leo (merito, fortuna, varianza, smentita, notizia), poi i ragionamenti:
dove si concentrano le perdite (campionato, fascia di quota, casa/pareggio/ospite, stagione) e quali regole Leo
proporrebbe. Nessuna puntata, nessun credito: legge solo lo storico di football-data.co.uk.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

from .agents.coach import classify, price_clv_of
from .feeds.mock import tick_up
from .odds import exchange_prices_sane, remove_margin
from .strategies.s05_favoriti_exchange_v1 import ev_net

COMM = 0.045
SIDES = ("home", "draw", "away")
LABEL = {"home": "casa", "draw": "pareggio", "away": "ospite"}
CAUSE_TXT = {"merito": "vinte con merito", "fortuna": "vinte per fortuna", "varianza": "perse per varianza (scelta giusta)",
             "smentita": "perse perché il mercato ci ha smentito", "notizia": "movimento forte (notizia?)",
             "non_valutabile": "senza chiusura"}


def _fair(tr):
    if not tr or any(v is None or v <= 1 for v in tr):
        return None
    try:
        return remove_margin(dict(zip(SIDES, tr)))
    except Exception:
        return None


def _band(o: float) -> str:
    for lo, hi in ((1.0, 1.4), (1.4, 2.0), (2.0, 3.0), (3.0, 5.0), (5.0, 8.0), (8.0, 99)):
        if lo <= o < hi:
            return f"{lo:.1f}-{hi:.1f}" if hi < 99 else f"{lo:.0f}+"
    return "?"


# ── 1. azzeccano il risultato? ────────────────────────────────────────────
def calibration(matches: list[dict]) -> dict:
    bins = defaultdict(lambda: [0, 0.0, 0])            # fascia → [partite, prob attesa, vinte]
    n = hit = 0
    exp = 0.0
    for m in matches:
        f = _fair(m.get("ps"))
        if not f:
            continue
        side = max(f, key=f.get)
        won = SIDES[m["res"]] == side
        n += 1
        hit += won
        exp += f[side]
        b = min(9, int(f[side] * 10))
        bins[b][0] += 1
        bins[b][1] += f[side]
        bins[b][2] += won
    return {"partite": n, "azzeccate": hit / n if n else None, "attese": exp / n if n else None,
            "fasce": [{"fascia": f"{b * 10}-{b * 10 + 10}%", "partite": v[0], "attesa": v[1] / v[0], "vera": v[2] / v[0]}
                      for b, v in sorted(bins.items()) if v[0] >= 30]}


# ── 2. strategie sui prezzi veri di Betfair ───────────────────────────────
def picks(matches: list[dict], s10_per_day: int = 5) -> list[dict]:
    bets, s10_pool = [], defaultdict(list)
    for m in matches:
        f_in, f_cl = _fair(m.get("ps")), _fair(m.get("psc"))
        bfe = m.get("bfe")
        if not f_in or not bfe or not exchange_prices_sane(list(bfe), list(m["ps"])):
            continue
        res = SIDES[m["res"]]
        base = {"div": m["div"], "season": m["season"], "date": m["date"].strftime("%Y-%m-%d"),
                "match": f"{m['home']} - {m['away']}"}
        for i, side in enumerate(SIDES):
            back, p = bfe[i], f_in[side]
            pc = f_cl[side] if f_cl else None
            move = (pc / p - 1) if pc else None
            ev = ev_net(p, back, COMM)
            if 1.10 <= back <= 1.40 and p >= 0.75 and ev >= 0.02:
                bets.append({**base, "strategy": "S05 favoriti", "side": "BACK", "sel": side, "price": back, "p": p,
                             "pc": pc, "move": move, "ev": ev, "won": res == side})
            lay = tick_up(back, 2)
            lay_ev = ((1 - p) * (1 - COMM) - p * (lay - 1)) / (lay - 1)
            if 3.0 <= lay <= 8.0 and lay_ev >= 0.02:
                bets.append({**base, "strategy": "S09 lay di valore", "side": "LAY", "sel": side, "price": lay, "p": 1 - p,
                             "pc": pc, "move": move, "ev": lay_ev, "won": res != side})
            if 1.40 <= back <= 3.0 and ev >= -0.03:
                s10_pool[base["date"]].append({**base, "strategy": "S10 divertimento", "side": "BACK", "sel": side,
                                               "price": back, "p": p, "pc": pc, "move": move, "ev": ev, "won": res == side})
    for day, pool in s10_pool.items():                  # le migliori del giorno, una per partita, massimo 5
        seen = set()
        for b in sorted(pool, key=lambda b: -b["ev"]):
            if b["match"] in seen:
                continue
            seen.add(b["match"])
            bets.append(b)
            if len(seen) >= s10_per_day:
                break
    for b in bets:
        comm = COMM
        if b["side"] == "BACK":
            b["pnl"] = (b["price"] - 1) * (1 - comm) if b["won"] else -1.0
        else:                                           # lay: 1 € di responsabilità
            b["pnl"] = (1 - comm) / (b["price"] - 1) if b["won"] else -1.0
        b["clv"] = price_clv_of(b["side"], b["price"], b["pc"])
        mv = None if b["move"] is None else (b["move"] if b["side"] == "BACK" else -b["move"])
        b["cause"] = classify(b["won"], b["clv"], {}, mv)
        b["band"] = _band(b["price"])
    return bets


def _stats(bs: list[dict]) -> dict:
    n = len(bs)
    if not n:
        return {"n": 0}
    pnl = [b["pnl"] for b in bs]
    m = sum(pnl) / n
    se = math.sqrt(sum((x - m) ** 2 for x in pnl) / (n - 1)) / math.sqrt(n) if n > 1 else float("nan")
    clv = [b["clv"] for b in bs if b["clv"] is not None]
    cm = sum(clv) / len(clv) if clv else None
    cse = (math.sqrt(sum((x - cm) ** 2 for x in clv) / (len(clv) - 1)) / math.sqrt(len(clv))) if len(clv) > 1 else None
    return {"n": n, "vinte": sum(b["won"] for b in bs) / n, "attese": sum(b["p"] for b in bs) / n, "roi": m, "se": se,
            "clv": cm, "clv_se": cse}


def reasoning(bs: list[dict], min_n: int = 30) -> dict:
    s = _stats(bs)
    causes = defaultdict(int)
    for b in bs:
        causes[b["cause"]] += 1
    lost = [b for b in bs if not b["won"]]
    lost_causes = defaultdict(int)
    for b in lost:
        lost_causes[b["cause"]] += 1
    segs = []
    for key, name in (("div", "campionato"), ("band", "quota"), ("sel", "esito"), ("season", "stagione")):
        groups = defaultdict(list)
        for b in bs:
            groups[b[key]].append(b)
        for v, g in groups.items():
            if len(g) >= min_n:
                st = _stats(g)
                segs.append({"dove": f"{name} {LABEL.get(v, v)}", **st, "hi": st["roi"] + 1.645 * st["se"],
                             "lo": st["roi"] - 1.645 * st["se"]})
    rules = [x for x in segs if x["hi"] < 0]            # stessa regola di Leo: evitare solo se sicuri al 90%
    best = [x for x in segs if x["lo"] > 0]
    thoughts = []
    if s["n"]:
        if s["clv"] is not None and s["clv"] + 2 * (s["clv_se"] or 0) < 0:
            thoughts.append("Il mercato alla chiusura ci dà torto in media: la logica sceglie prezzi che poi si "
                            "rivelano peggiori del giusto. Non è sfortuna, è la scelta.")
        elif s["clv"] is not None and s["clv"] - 2 * (s["clv_se"] or 0) > 0:
            thoughts.append("Il mercato alla chiusura ci dà ragione: le perdite sono soprattutto varianza. Serve "
                            "pazienza e un campione più grande, non cambiare logica.")
        else:
            thoughts.append("Alla chiusura il mercato non ci dà né torto né ragione in modo netto: la logica non "
                            "ha un vantaggio misurabile.")
        v, sm = lost_causes.get("varianza", 0), lost_causes.get("smentita", 0) + lost_causes.get("notizia", 0)
        if lost:
            thoughts.append(f"Delle {len(lost)} perse: {v / len(lost):.0%} per varianza (decisione giusta, esito "
                            f"sfortunato), {sm / len(lost):.0%} perché il prezzo non valeva (smentita o notizia).")
        thoughts.append(f"Vinte {s['vinte']:.1%} contro {s['attese']:.1%} attese dalle quote: "
                        + ("in linea, il mercato stima bene." if abs(s["vinte"] - s["attese"]) < 2 * math.sqrt(
                            s["attese"] * (1 - s["attese"]) / s["n"]) else "diverse dalle attese, da capire."))
        if best:
            thoughts.append(f"{len(best)} segmenti sopra zero, ma su {len(segs)} provati: "
                            f"con tante prove qualcuno esce positivo per caso. Vanno confermati sui prezzi veri.")
    return {**s, "cause": dict(causes), "cause_perse": dict(lost_causes), "segmenti": len(segs),
            "regole": sorted(rules, key=lambda x: x["roi"])[:5], "migliori": sorted(best, key=lambda x: -x["roi"])[:3],
            "ragionamenti": thoughts}


# ── 3. tennis (tennis-data.co.uk: ATP e WTA principali, quote Pinnacle e Betfair dal 2025) ─────────────
def tennis_matches(paths=None) -> list[dict]:
    """Partite completate con quote Pinnacle (PSW/PSL) e, se ci sono, Betfair (BFEW/BFEL). Il vincitore è il
    risultato; per le scelte le due parti sono trattate allo stesso modo (nessuno sguardo al risultato)."""
    import pandas as pd
    if paths is None:
        from .backtest_tennis import tennis_paths
        paths = tennis_paths()
    out = []
    for path in paths:
        try:
            df = pd.read_excel(path)
        except Exception:
            continue
        tour = "WTA" if "wta" in Path(path).name.lower() else "ATP"
        for _, r in df.iterrows():
            if str(r.get("Comment", "Completed")).strip().lower() != "completed":
                continue
            ps = (r.get("PSW"), r.get("PSL"))
            bfe = (r.get("BFEW"), r.get("BFEL"))
            try:
                ps = tuple(float(x) for x in ps)
            except (TypeError, ValueError):
                continue
            if any(x != x or x <= 1 for x in ps):
                continue
            try:
                bfe = tuple(float(x) for x in bfe)
                bfe = None if any(x != x or x <= 1 for x in bfe) else bfe
            except (TypeError, ValueError):
                bfe = None
            d = pd.to_datetime(r.get("Date"), errors="coerce")
            if pd.isna(d):
                continue
            out.append({"date": d.to_pydatetime(), "tour": tour, "series": str(r.get("Series") or r.get("Tier") or ""),
                        "surface": str(r.get("Surface") or ""), "w": str(r.get("Winner")), "l": str(r.get("Loser")),
                        "ps": ps, "bfe": bfe})
    return out


def tennis_report(ms: list[dict], per_day: int = 5) -> dict:
    """Calibrazione del favorito di Pinnacle e S10 (back sul prezzo Betfair più giusto) sul tennis. Le quote del file
    sono prese vicino all'inizio: il CLV qui non si misura, conta il ROI (con il suo margine d'errore)."""
    from .strategies.s10_divertimento_v1 import DEFAULTS as S10D
    n = hit = 0
    exp = 0.0
    pool = defaultdict(list)
    for m in ms:
        try:
            f = remove_margin({"w": m["ps"][0], "l": m["ps"][1]})
        except Exception:
            continue
        fav = max(f, key=f.get)
        n += 1
        hit += fav == "w"
        exp += f[fav]
        if not m["bfe"] or not exchange_prices_sane(list(m["bfe"]), list(m["ps"])):
            continue
        for i, side in enumerate(("w", "l")):
            back, p = m["bfe"][i], f[side]
            ev = ev_net(p, back, COMM)
            if S10D["odds_min"] <= back <= S10D["odds_max"] and ev >= S10D["min_ev"]:
                pool[m["date"].strftime("%Y-%m-%d")].append({"strategy": "S10 tennis", "side": "BACK", "price": back,
                                                             "p": p, "pc": None, "ev": ev, "won": side == "w",
                                                             "match": f"{m['w']}-{m['l']}", "div": m["tour"],
                                                             "season": m["date"].strftime("%Y"), "sel": m["surface"] or "?"})
    bets = []
    for day, pl in pool.items():
        seen = set()
        for b in sorted(pl, key=lambda b: -b["ev"]):
            if b["match"] in seen:
                continue
            seen.add(b["match"])
            bets.append(b)
            if len(seen) >= per_day:
                break
    for b in bets:
        b["pnl"] = (b["price"] - 1) * (1 - COMM) if b["won"] else -1.0
        b["clv"] = None
        b["cause"] = classify(b["won"], None, {}, None)
        b["band"] = _band(b["price"])
    return {"partite": n, "favorito_vince": hit / n if n else None, "atteso": exp / n if n else None,
            "s10": reasoning(bets) if bets else {"n": 0}}


def run(years: int = 5, log=print) -> dict:
    from .palestra import load_matches
    matches = load_matches(years)
    cal = calibration(matches)
    bets = picks(matches)
    by = defaultdict(list)
    for b in bets:
        by[b["strategy"]].append(b)
    try:
        tennis = tennis_report(tennis_matches())
    except Exception as exc:                       # da alcuni server tennis-data risponde 403: il calcio va avanti
        tennis = {"errore": str(exc)}
    return {"anni": years, "partite": len(matches), "calibrazione": cal,
            "con_betfair": sum(1 for m in matches if m.get("bfe")),
            "strategie": {k: reasoning(v) for k, v in sorted(by.items())}, "tennis": tennis}


def report(r: dict) -> str:
    pct = lambda x: "—" if x is None else f"{x:+.1%}"
    c = r["calibrazione"]
    L = [f"# Allenamento dei ragazzi: {r['partite']} partite degli ultimi {r['anni']} anni", "",
         "## 1. Azzeccano il risultato?", "",
         f"Scegliendo sempre l'esito più probabile secondo Pinnacle (prima della partita): azzeccate "
         f"**{c['azzeccate']:.1%}** su {c['partite']} partite, attese {c['attese']:.1%}.", "",
         "| Probabilità data | Partite | Attesa | Successo vero |", "|---|---|---|---|"]
    for f in c["fasce"]:
        L.append(f"| {f['fascia']} | {f['partite']} | {f['attesa']:.1%} | {f['vera']:.1%} |")
    L += ["", "Le quote sono tarate bene: quello che danno al 70% vince circa 7 volte su 10. Azzeccare il risultato "
              "non basta: a quelle probabilità la quota paga esattamente il giusto (meno la commissione).", "",
          f"## 2. Le strategie sui prezzi veri di Betfair ({r['con_betfair']} partite con prezzi Betfair)", ""]
    for name, s in r["strategie"].items():
        if not s["n"]:
            continue
        L += [f"### {name}", "",
              f"{s['n']} puntate · vinte {s['vinte']:.1%} (attese {s['attese']:.1%}) · ROI {pct(s['roi'])} ± {2 * s['se']:.1%} · "
              f"CLV {pct(s['clv'])}" + ("" if s["clv_se"] is None else f" ± {2 * s['clv_se']:.1%}"), "",
              "Autopsia (cause di Leo): " + ", ".join(f"{CAUSE_TXT.get(k, k)} {v}" for k, v in
                                                      sorted(s["cause"].items(), key=lambda kv: -kv[1])), "",
              "Ragionamenti:"]
        L += [f"- {t}" for t in s["ragionamenti"]]
        if s["regole"]:
            L += ["", "Leo proporrebbe di evitare (perdita sicura al 90%):"]
            L += [f"- {x['dove']}: {x['n']} puntate, ROI {pct(x['roi'])}" for x in s["regole"]]
        L.append("")
    t = r.get("tennis") or {}
    L += ["## 3. Tennis (ATP e WTA, quote vicino all'inizio)", ""]
    if t.get("errore"):
        L.append(f"Dati del tennis non disponibili: {t['errore']}")
    elif t.get("partite"):
        L.append(f"Favorito di Pinnacle: vince {t['favorito_vince']:.1%} su {t['partite']} partite (atteso {t['atteso']:.1%}).")
        s = t.get("s10") or {}
        if s.get("n"):
            L += ["", f"S10 sul tennis: {s['n']} puntate · vinte {s['vinte']:.1%} (attese {s['attese']:.1%}) · ROI "
                      f"{pct(s['roi'])} ± {2 * s['se']:.1%}. Il CLV qui non si misura: quote del file già vicino all'inizio."]
            L += [f"- {x}" for x in s["ragionamenti"][1:]]
            if s.get("regole"):
                L += ["", "Leo proporrebbe di evitare:"] + [f"- {x['dove']}: {x['n']} puntate, ROI {pct(x['roi'])}"
                                                          for x in s["regole"]]
        else:
            L.append("Nessuna partita con prezzi Betfair utilizzabili.")
    L.append("")
    return "\n".join(L) + "\n"


def main(years: int = 5, out=print) -> dict:
    from .config import REPORTS_DIR
    r = run(years)
    md = report(r)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "allenamento.md").write_text(md, encoding="utf-8")
    (REPORTS_DIR / "allenamento.json").write_text(json.dumps(r, default=str), encoding="utf-8")
    out(md)
    return r
