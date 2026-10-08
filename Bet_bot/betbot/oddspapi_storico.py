"""Storico dei lay di valore sui prezzi di OddsPapi (Pinnacle + Betfair Exchange, da gennaio 2026).

Per ogni partita di calcio già finita (campionati di feeds/oddspapi_ref.TOURNAMENTS) scarica la serie storica dei
prezzi di `pinnacle` e `betfair-ex` (/v4/historical-odds, 1 chiamata ogni 5 s, nessuna quota secondo la doc), la tiene in
runtime/oddspapi_storico/ e, abbinando il risultato di football-data, rigioca la regola del lay di valore del 4fun a 24, 6
e 1 ora dall'inizio: lay a quota 3-5 (e 3-8) con valore atteso ≥ 2% contro il Pinnacle di quel momento.
Misure: percentuale di vinte (contro l'attesa), ROI sul rischio e CLV contro il Pinnacle alla chiusura.

Ogni lancio lavora RUN_SECONDS secondi (4 chiamate da 5 s per partita: Pinnacle con tutti gli esiti, poi l'exchange un esito
alla volta, perché l'API lo impone) su partite scelte in ordine casuale: anche un lancio parziale è un campione. Il resto resta
in cache: rilancia per aggiungere partite.
Limiti: è l'exchange internazionale, non betfair.it; se la serie dell'exchange non porta un prezzo lay esplicito il lay è
stimato 2 tick sopra il prezzo (come nel backtest di football-data) e il rapporto lo dice. Formato di
`exchangeMeta` NON verificato: al primo lancio si stampa un esempio grezzo.
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone

from . import oddspapi as OP
from .config import RUNTIME_DIR, oddspapi_key
from .odds import exchange_prices_sane, remove_margin
from .strategies.s09_lay_valore_v1 import lay_ev

RUN_SECONDS = 1500                          # ogni lancio lavora al massimo ~25 minuti (4 chiamate da 5 s per partita)
START = "2026-01-01T00:00:00Z"
HORIZONS = (24, 6, 1)                       # ore prima dell'inizio
COMMISSION = 0.045
GAP_HISTORY = 5.2                           # la documentazione dichiara 5000 ms tra due chiamate
GAP_FIXTURES = 2.2
MARKET = "101"
OUT = {"home": "101", "draw": "102", "away": "103"}


def _dir():
    d = RUNTIME_DIR / "oddspapi_storico"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _iso_ts(v) -> float | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return v / 1000 if v > 1e11 else float(v)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def add_ticks(price: float, n: int = 2) -> float:
    """Scala dei prezzi Betfair: n scalini sopra `price`."""
    ladder = [(2, .01), (3, .02), (4, .05), (6, .1), (10, .2), (20, .5), (30, 1), (50, 2), (100, 5), (1001, 10)]
    for _ in range(n):
        step = next(s for lim, s in ladder if price < lim)
        price = round(price + step, 2)
    return price


def _best_lay(meta) -> float | None:
    """Miglior prezzo lay da exchangeMeta (formato non verificato: si provano le forme più probabili)."""
    if not isinstance(meta, dict):
        return None
    for k in ("lay", "layPrice", "bestLay", "availableToLay", "lays"):
        v = meta.get(k)
        if isinstance(v, (int, float)) and v > 1:
            return float(v)
        if isinstance(v, list) and v:
            first = v[0]
            if isinstance(first, dict):
                p = first.get("price") or first.get("odds")
                if isinstance(p, (int, float)) and p > 1:
                    return float(p)
            elif isinstance(first, (list, tuple)) and first and isinstance(first[0], (int, float)) and first[0] > 1:
                return float(first[0])
        if isinstance(v, dict):
            p = v.get("price")
            if isinstance(p, (int, float)) and p > 1:
                return float(p)
    return None


def series(raw: dict, slug: str, outcome_id: str) -> list[tuple[float, float, bool, dict | None]]:
    """[(ts, price, active, exchangeMeta)] in ordine di tempo per un esito del mercato 1X2 di un bookmaker."""
    try:
        players = raw["bookmakers"][slug]["markets"][MARKET]["outcomes"][outcome_id]["players"]
    except (KeyError, TypeError):
        return []
    entries = next(iter(players.values()), []) if isinstance(players, dict) else []
    out = []
    for e in entries or []:
        t = _iso_ts(e.get("createdAt"))
        if t is not None and e.get("price"):
            out.append((t, float(e["price"]), e.get("active") is not False, e.get("exchangeMeta")))
    return sorted(out, key=lambda x: x[0])


def at(ser: list, t: float):
    """Ultima voce attiva con ts ≤ t."""
    last = None
    for row in ser:
        if row[0] > t:
            break
        if row[2]:
            last = row
    return last


def snapshot(raw: dict, slug: str, t: float):
    """({esito: (prezzo, meta)}) con tutti e tre gli esiti, oppure None."""
    got = {}
    for sel, oid in OUT.items():
        row = at(series(raw, slug, oid), t)
        if not row:
            return None
        got[sel] = (row[1], row[3])
    return got


def fetch_fixture(client, fid: str, sleep=time.sleep) -> dict | None:
    """Serie storica di una partita: 1 chiamata per Pinnacle (tutti gli esiti) + 1 per ogni esito dell'exchange
    ('betfair-ex' vuole esattamente un bookmaker e un outcomeId). None se Pinnacle non ha prezzi."""
    sleep(GAP_HISTORY)
    pin = client.get("/historical-odds", quota=False, fixtureId=fid, bookmakers="pinnacle")
    if not series(pin, "pinnacle", OUT["home"]):
        return None
    merged = {"fixtureId": fid, "bookmakers": {"pinnacle": pin["bookmakers"]["pinnacle"]}}
    for oid in OUT.values():
        sleep(GAP_HISTORY)
        r = client.get("/historical-odds", quota=False, fixtureId=fid, bookmakers="betfair-ex", outcomeId=oid)
        bf = ((r.get("bookmakers") or {}).get("betfair-ex") or {}).get("markets", {}).get(MARKET, {}).get("outcomes", {})
        if oid in bf:
            merged["bookmakers"].setdefault("betfair-ex", {"markets": {MARKET: {"outcomes": {}}}})
            merged["bookmakers"]["betfair-ex"]["markets"][MARKET]["outcomes"][oid] = bf[oid]
    return merged


def evaluate_fixture(raw: dict, start: float, res: int, horizons=HORIZONS) -> list[dict]:
    """Casi di lay (uno per orizzonte: l'esito col valore migliore) di una partita finita. res: 0 casa, 1 pari, 2 ospite."""
    sels = ["home", "draw", "away"]
    close = snapshot(raw, "pinnacle", start)
    p_close = remove_margin({k: v[0] for k, v in close.items()}) if close else None
    rows = []
    for h in horizons:
        t = start - h * 3600
        pin, bf = snapshot(raw, "pinnacle", t), snapshot(raw, "betfair-ex", t)
        if not pin or not bf:
            continue
        pin_p = [pin[k][0] for k in sels]
        if not exchange_prices_sane([bf[k][0] for k in sels], pin_p):
            continue                                    # record rotto: come nel backtest di football-data
        fair = remove_margin({k: pin[k][0] for k in sels})
        best = None
        for k in sels:
            explicit = _best_lay(bf[k][1])
            lay = explicit if explicit else add_ticks(bf[k][0])
            ev = lay_ev(fair[k], lay, COMMISSION)
            risk_ev = ev / (lay - 1)
            if best is None or risk_ev > best["edge"]:
                best = {"sel": k, "lay": lay, "explicit": bool(explicit), "p": fair[k], "edge": risk_ev}
        if not best or best["lay"] < 3.0 or best["lay"] > 8.0 or best["edge"] < 0.02:
            continue
        happened = sels.index(best["sel"]) == res
        pnl = -(best["lay"] - 1) if happened else (1 - COMMISSION)
        clv = (1 / (best["lay"] * p_close[best["sel"]]) - 1) if p_close and p_close[best["sel"]] > 0 else None
        rows.append({**best, "h": h, "pnl": pnl, "risk": best["lay"] - 1, "win": not happened, "clv": clv})
    return rows


def _mean_se(xs: list[float]):
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return (xs[0] if xs else None), None
    m = sum(xs) / len(xs)
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
    return m, sd / math.sqrt(len(xs))


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    roi, roi_se = _mean_se([r["pnl"] / r["risk"] for r in rows])
    clv, clv_se = _mean_se([r["clv"] for r in rows])
    return {"n": n, "win": sum(r["win"] for r in rows) / n, "exp": sum(1 - r["p"] for r in rows) / n,
            "roi": roi, "roi_se": roi_se, "clv": clv, "clv_se": clv_se,
            "estimated": sum(1 for r in rows if not r["explicit"]) / n}


def _fmt(s: dict) -> str:
    if not s["n"]:
        return "nessun caso"
    pc = lambda x, se=None: "—" if x is None else (f"{x:+.1%}" if se is None else f"{x:+.1%} ± {2 * se:.1%}")
    return (f"{s['n']} lay · vinti {s['win']:.1%} (attesi {s['exp']:.1%}) · ROI sul rischio {pc(s['roi'], s['roi_se'])} · "
            f"CLV {pc(s['clv'], s['clv_se'])}" + (f" · lay stimato (non esplicito) nel {s['estimated']:.0%}" if s["estimated"] else ""))


def report(cases: list[dict], n_fixtures: int) -> str:
    L = [f"Lay di valore sullo storico OddsPapi (gennaio-oggi): {n_fixtures} partite con prezzi di Pinnacle ed exchange.", ""]
    for h in HORIZONS:
        a = [c for c in cases if c["h"] == h]
        L.append(f"A {h} ore dall'inizio, quota 3-8: {_fmt(summarize(a))}")
        L.append(f"   solo quota 3-5 (4fun):   {_fmt(summarize([c for c in a if c['lay'] <= 5.0]))}")
    L += ["", "Attenzione: i casi dello stesso weekend e delle stesse squadre non sono indipendenti, e l'exchange è quello internazionale.",
          "L'intervallo è ± 2 errori standard: se attraversa lo zero non si può dire nulla."]
    return "\n".join(L)


def _results():
    from .palestra import load_matches
    return load_matches(1)


def _match_result(fx: dict, matches: list[dict]):
    from .feeds.api_football import same_team
    start = _iso_ts(fx.get("startTime"))
    if start is None:
        return None
    for m in matches:
        if abs(m["date"].replace(tzinfo=timezone.utc).timestamp() - start) > 86400:
            continue
        if same_team(m["home"], fx.get("participant1Name") or "") and same_team(m["away"], fx.get("participant2Name") or ""):
            return m["res"]
    return None


def run(out=print, client=None, results=None, sleep=time.sleep) -> int:
    key = oddspapi_key()
    if client is None:
        if not key:
            out("Manca la chiave di OddsPapi: lancia prima oddspapi.bat.")
            return 1
        client = OP.OddsPapiClient(key)
    try:
        from .feeds.oddspapi_ref import TOURNAMENTS
        tours = OP.items(client.get("/tournaments", sportId=OP.SOCCER))
        ids = {}
        for t in tours:
            name, cat = str(t.get("tournamentName") or "").lower(), str(t.get("categoryName") or "").lower()
            if (name, cat) in TOURNAMENTS.values():
                ids[str(t.get("tournamentId"))] = f"{cat} {name}"
        fixtures = []
        until = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        t_start, t_now = _iso_ts(START), time.time()
        variants = [("con stato e date", {"statusId": 2, "from": START, "to": until}),
                    ("solo stato", {"statusId": 2}), ("senza filtri", {})]
        chosen = None                                   # la variante che funziona si trova sul primo campionato e poi si riusa
        for tid in ids:
            for name, params in (variants if chosen is None else [chosen]):
                sleep(GAP_FIXTURES)
                got = OP.items(client.get("/fixtures", tournamentId=tid, **params))
                keep = [f for f in got if (t_start <= (_iso_ts(f.get("startTime")) or 0) < t_now)
                        and f.get("statusId") in (2, None)]
                out(f"  {ids[tid]}: {len(got)} partite restituite ({name}), {len(keep)} finite da gennaio")
                if keep or chosen is not None:
                    chosen = chosen or (name, params)
                    fixtures += keep
                    break
                if got and name == variants[-1][0]:
                    out("    esempio grezzo: " + json.dumps(got[0], ensure_ascii=False)[:400])
        out(f"Campionati: {len(ids)} · partite finite da gennaio: {len(fixtures)}")
        import hashlib
        order = lambda f: hashlib.md5(str(f["fixtureId"]).encode()).hexdigest()      # ordine casuale ma ripetibile
        todo = [f for f in sorted(fixtures, key=order) if not (_dir() / f"{f['fixtureId']}.json").exists()]
        new, t0 = 0, time.time()
        for fx in todo:
            if time.time() - t0 > RUN_SECONDS:
                break
            try:
                raw = fetch_fixture(client, fx["fixtureId"], sleep)
            except OP.OddsPapiError as exc:
                if "429" in str(exc):
                    out("Limite di velocità: mi fermo, rilancia tra qualche minuto.")
                    break
                out(f"  {fx['fixtureId']}: {exc}")
                out("  Errore dell'API: mi fermo (controlla il messaggio qui sopra).")
                break
            if raw is not None and new == 0:
                out("Esempio grezzo (per controllare il formato): " + json.dumps(raw, ensure_ascii=False)[:600])
            (_dir() / f"{fx['fixtureId']}.json").write_text(json.dumps({"fx": fx, "raw": raw or {}}), encoding="utf-8")
            new += 1
            if new % 10 == 0:
                out(f"  … {new} partite scaricate in questo lancio")
        out(f"Scaricate {new} partite nuove, ne restano {max(0, len(todo) - new)} (rilancia per continuare).")
    except OP.OddsPapiError as exc:
        out(f"\n{exc}")
        return 1
    cases, n_fx = [], 0
    matches = results if results is not None else _results()      # una volta sola, dopo i download
    for p in _dir().glob("*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        res = _match_result(d["fx"], matches)
        start = _iso_ts(d["fx"].get("startTime"))
        if res is None or start is None:
            continue
        rows = evaluate_fixture(d["raw"], start, res)
        n_fx += 1
        cases += rows
    out("\n" + report(cases, n_fx))
    return 0
