"""Storico ufficiale di Betfair (historicdata.betfair.com, piano "Basic" gratuito): il lay di valore all'ora VERA.

Il piano Basic dà, per ogni mercato, l'ultimo prezzo scambiato (LTP) minuto per minuto fino al fischio d'inizio, nel
formato dello Stream API (righe JSON in file .bz2, dentro un archivio .tar). È il mercato internazionale di Betfair,
non quello italiano: serve a capire se il vantaggio visto sui prezzi del venerdì c'è ancora vicino all'inizio.

Mettere l'archivio .tar (o i file .bz2, anche in cartelle) in runtime/storico_betfair/ e lanciare
`python betbot.py storico-betfair` (storico_betfair.bat). Per ogni partita di calcio Match Odds si abbina la riga di
football-data (stesso giorno, stessi nomi) e si guarda il prezzo Betfair a 24, 6, 1 ora e 15 minuti dall'inizio
contro la quota GIUSTA di Pinnacle alla chiusura (senza margine):
  • scarto = quanto Betfair paga più (o meno) del giusto a quell'ora, per fascia di quota;
  • lay di tutti gli esiti a quote 3-8 (1 tick sopra l'ultimo prezzo) con il risultato vero: ROI sul rischio;
  • lay "di valore": solo quando il lay costava meno del giusto di Pinnacle del venerdì (la regola di S09).
Nessuna puntata, nessun credito.
"""
from __future__ import annotations

import bz2
import json
import math
import tarfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .config import RUNTIME_DIR
from .feeds.mock import tick_up
from .odds import remove_margin

DIR = RUNTIME_DIR / "storico_betfair"
HORIZONS = ((24 * 60, "24 ore"), (6 * 60, "6 ore"), (60, "1 ora"), (15, "15 minuti"))
COMM = 0.045


def _lines(path: Path):
    opener = bz2.open if path.suffix == ".bz2" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def files(root: Path = DIR) -> list[Path]:
    """I file dei mercati; gli archivi .tar si estraggono una volta sola nella stessa cartella."""
    if not root.exists():
        return []
    for tar in root.glob("*.tar"):
        mark = tar.with_suffix(".estratto")
        if not mark.exists():
            with tarfile.open(tar) as t:
                try:
                    t.extractall(root / tar.stem, filter="data")        # Python recenti: estrazione sicura
                except TypeError:                                     # Python più vecchi: niente percorsi strani
                    safe = [x for x in t.getmembers() if x.isfile() and ".." not in x.name and not x.name.startswith("/")]
                    t.extractall(root / tar.stem, members=safe)
            mark.write_text("ok")
    return sorted(p for p in root.rglob("*") if p.is_file() and (p.suffix == ".bz2" or p.name.startswith("1.")))


def parse_market(path: Path) -> dict | None:
    """Mercato Match Odds: squadre, inizio, vincitore e serie (istante, LTP) di ogni esito PRIMA dell'inizio."""
    defn, series, last_ltp = None, defaultdict(list), {}
    for msg in _lines(path):
        pt = msg.get("pt", 0) / 1000
        for mc in msg.get("mc", []):
            d = mc.get("marketDefinition")
            if d:
                defn = d
            if defn and defn.get("inPlay"):
                continue                                     # dal fischio d'inizio in poi non serve
            for rc in mc.get("rc", []) or []:
                if rc.get("ltp"):
                    last_ltp[rc["id"]] = rc["ltp"]
                    series[rc["id"]].append((pt, float(rc["ltp"])))
    if not defn or defn.get("marketType") != "MATCH_ODDS" or " v " not in defn.get("eventName", ""):
        return None
    home, _, away = defn["eventName"].partition(" v ")
    names = {r["id"]: r.get("name", "") for r in defn.get("runners", [])}
    winner = next((r["id"] for r in defn.get("runners", []) if r.get("status") == "WINNER"), None)
    side = {}
    for rid, nm in names.items():
        side[rid] = "draw" if nm.lower() in ("the draw", "draw", "pareggio") else "home" if nm == home.strip() \
            else "away" if nm == away.strip() else None
    if sorted(v for v in side.values() if v) != ["away", "draw", "home"]:
        return None
    ko = datetime.fromisoformat(defn["marketTime"].replace("Z", "+00:00")).timestamp()
    return {"home": home.strip(), "away": away.strip(), "ko": ko,
            "result": side.get(winner), "series": {side[r]: s for r, s in series.items() if side.get(r)}}


def price_at(serie: list[tuple[float, float]], t: float) -> float | None:
    p = None
    for ts, v in serie:
        if ts > t:
            break
        p = v
    return p


def match_fd(markets: list[dict], fd: list[dict]) -> list[tuple[dict, dict]]:
    from .palestra import _sim
    by_day = defaultdict(list)
    for m in fd:
        by_day[m["date"].date()].append(m)
    out = []
    for mk in markets:
        day = datetime.fromtimestamp(mk["ko"], timezone.utc).date()
        best, score = None, 0.0
        for m in by_day.get(day, []):
            s = _sim(mk["home"], m["home"]) + _sim(mk["away"], m["away"])
            if s > score:
                best, score = m, s
        if best and score >= 1.4:
            out.append((mk, best))
    return out


def _ci(v):
    if len(v) < 2:
        return (sum(v) / len(v) if v else None), None
    m = sum(v) / len(v)
    return m, 2 * math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1)) / math.sqrt(len(v))


def analyse(pairs: list[tuple[dict, dict]]) -> dict:
    sides = ("home", "draw", "away")
    rows = []
    for mk, m in pairs:
        if not m.get("psc") or not mk.get("result"):
            continue
        try:
            pc = remove_margin(dict(zip(sides, m["psc"])))
            po = remove_margin(dict(zip(sides, m["ps"]))) if m.get("ps") else None
        except Exception:
            continue
        for mins, label in HORIZONS:
            t = mk["ko"] - mins * 60
            for s in sides:
                ltp = price_at(mk["series"].get(s, []), t)
                if not ltp or ltp <= 1:
                    continue
                lay = tick_up(ltp, 1)
                happened = mk["result"] == s
                rows.append({"h": label, "side": s, "ltp": ltp, "lay": lay, "gap": ltp * pc[s] - 1,
                             "lay_clv": 1 / (lay * pc[s]) - 1,
                             "lay_pnl": -1.0 if happened else (1 - COMM) / (lay - 1),
                             "value": po is not None and lay * po[s] < 1 / (1 + 0.02)})   # lay sotto il giusto del venerdì
    res = {"partite": len({id(mk) for mk, _ in pairs}), "orizzonti": {}}
    for _, label in HORIZONS:
        rr = [r for r in rows if r["h"] == label]
        bands = {}
        for name, lo, hi in (("1,40-3,00", 1.4, 3.0), ("3-5", 3.0, 5.0), ("5-8", 5.0, 8.0)):
            g = [r for r in rr if lo <= r["ltp"] < hi]
            gm, ge = _ci([r["gap"] for r in g])
            bands[name] = {"n": len(g), "scarto": gm, "err": ge}
        lays = [r for r in rr if 3.0 <= r["lay"] <= 8.0]
        val = [r for r in lays if r["value"]]
        a, ae = _ci([r["lay_pnl"] for r in lays])
        v, ve = _ci([r["lay_pnl"] for r in val])
        c, ce = _ci([r["lay_clv"] for r in val])
        res["orizzonti"][label] = {"fasce": bands, "lay_tutti": {"n": len(lays), "roi": a, "err": ae},
                                   "lay_valore": {"n": len(val), "roi": v, "err": ve, "clv": c, "clv_err": ce}}
    return res


def report(r: dict) -> str:
    pct = lambda x: "—" if x is None else f"{x:+.1%}"
    pm = lambda x, e: pct(x) + ("" if e is None else f" ± {e:.1%}")
    L = [f"# Storico Betfair: {r['partite']} partite abbinate a football-data", "",
         "Scarto = quanto l'ultimo prezzo Betfair paga più (+) o meno (−) della quota giusta di Pinnacle alla chiusura.",
         "Lay di valore = lay a quote 3-8 quando costava meno del giusto di Pinnacle del venerdì (regola di S09).", "",
         "| Quando | Scarto 1,40-3,00 | Scarto 3-5 | Scarto 5-8 | Lay di tutti (3-8): ROI | Lay di valore: n | ROI | CLV |",
         "|---|---|---|---|---|---|---|---|"]
    for label, o in r["orizzonti"].items():
        f = o["fasce"]
        lv = o["lay_valore"]
        L.append(f"| {label} prima | {pm(f['1,40-3,00']['scarto'], f['1,40-3,00']['err'])} | "
                 f"{pm(f['3-5']['scarto'], f['3-5']['err'])} | {pm(f['5-8']['scarto'], f['5-8']['err'])} | "
                 f"{pm(o['lay_tutti']['roi'], o['lay_tutti']['err'])} ({o['lay_tutti']['n']}) | {lv['n']} | "
                 f"{pm(lv['roi'], lv['err'])} | {pm(lv['clv'], lv['clv_err'])} |")
    L += ["", "Se il lay di valore resta positivo anche a 1 ora e 15 minuti dall'inizio, la regola di S09 regge all'ora "
              "vera della puntata. Attenzione: è il mercato internazionale; betfair.it ha meno liquidità."]
    return "\n".join(L) + "\n"


def run(out=print) -> dict:
    from .config import REPORTS_DIR
    from .palestra import load_matches
    fs = files()
    if not fs:
        out(f"Nessun file in {DIR}. Scarica lo storico gratuito da historicdata.betfair.com (istruzioni nel README) e "
            "mettilo in quella cartella.")
        return {}
    markets = [m for m in (parse_market(f) for f in fs) if m]
    out(f"{len(fs)} file letti, {len(markets)} mercati Match Odds di calcio.")
    pairs = match_fd(markets, load_matches(3))
    res = analyse(pairs)
    md = report(res)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "storico_betfair.md").write_text(md, encoding="utf-8")
    out(md)
    return res
