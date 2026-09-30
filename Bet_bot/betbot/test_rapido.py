"""Test rapido (6 ore) dell'ipotesi di S09 sui prezzi VERI di betfair.it, senza aspettare i risultati.

Domanda: quando il lay di betfair.it costa meno della quota giusta di Pinnacle, il mercato poi ci dà ragione?
Per saperlo basta il CLV: si confronta il prezzo di ingresso con la quota giusta di Pinnacle alla CHIUSURA (l'ultima
lettura prima del fischio d'inizio). Il risultato della partita non serve, quindi bastano le partite che iniziano
nelle prossime ore.

  • ogni `bf_every` minuti: prezzi back/lay di betfair.it per le partite che iniziano entro la finestra: calcio (1X2)
    e, testa a testa, football americano, tennis, basket e baseball (così si misura anche in settimana);
  • ogni `pin_every` minuti: quote di Pinnacle (The Odds API, 1 credito per campionato, budget massimo `credits`);
  • alla fine: per ogni selezione, prezzo di ingresso (almeno 45 minuti prima dell'inizio) contro chiusura di Pinnacle.
    Il verdetto su S09 riguarda solo il calcio; ogni altro sport ha un verdetto suo (lay e back con EV ≥ 2%).
Nessuna puntata, nemmeno simulata. Tutto finisce in runtime/test_rapido/ e runtime/reports/test_rapido.md.
"""
from __future__ import annotations

import gzip
import json
import math
import time
from datetime import datetime, timezone

import requests

from .config import REPORTS_DIR, RUNTIME_DIR, api_key
from .feeds.odds_api import BASE
from .odds import remove_margin

OUT_DIR = RUNTIME_DIR / "test_rapido"
COMM = 0.045
PREFERRED = ["soccer_uefa_champs_league", "soccer_uefa_europa_league", "soccer_uefa_europa_conference_league",
             "soccer_italy_serie_a", "americanfootball_nfl", "americanfootball_ncaaf", "soccer_italy_serie_b", "soccer_epl", "soccer_efl_champ", "soccer_england_league1",
             "soccer_spain_la_liga", "soccer_spain_segunda_division", "soccer_germany_bundesliga",
             "soccer_germany_bundesliga2", "soccer_france_ligue_one", "soccer_france_ligue_two",
             "soccer_netherlands_eredivisie", "soccer_portugal_primeira_liga", "soccer_belgium_first_div",
             "soccer_turkey_super_league", "soccer_spl", "soccer_greece_super_league",
             "basketball_nba", "basketball_euroleague", "baseball_mlb"]   # poi i tornei di tennis attivi


SPORT_PREFIXES = ("soccer", "americanfootball", "tennis", "basketball", "baseball")
LABELS = {"soccer": "Calcio", "americanfootball": "Football americano", "tennis": "Tennis", "basketball": "Basket",
          "baseball": "Baseball"}
# tennis: l'orario è indicativo (ordine di gioco), le altre partite iniziano all'ora scritta
START_TOLERANCE_S = {"tennis": 4 * 3600}


def family(sport: str | None) -> str:
    """"americanfootball_nfl" → "americanfootball", "tennis_atp_paris" → "tennis"; senza sport (misure vecchie) → "soccer"."""
    s = sport or ""
    return next((f for f in SPORT_PREFIXES if s.startswith(f)), "soccer")


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


# ── fonti ─────────────────────────────────────────────────────────────────
def odds_api_events(key: str, sport: str) -> list[dict]:
    """Partite in programma di un campionato (endpoint /events: non consuma crediti)."""
    r = requests.get(f"{BASE}/sports/{sport}/events", params={"apiKey": key}, timeout=20)
    return r.json() if r.status_code == 200 else []


def odds_api_sports(key: str) -> list[str]:
    r = requests.get(f"{BASE}/sports", params={"apiKey": key}, timeout=20)
    return [s["key"] for s in r.json() if s.get("active") and not s.get("has_outrights")
            and s["key"].startswith(SPORT_PREFIXES)] if r.status_code == 200 else []


def pinnacle_odds(key: str, sport: str) -> tuple[list[dict], str | None]:
    """Quote testa a testa di Pinnacle (1 credito): 1X2 nel calcio, due esiti negli altri sport.
    → ([{home, away, start, fair: {home, (draw), away}}], crediti rimasti)"""
    r = requests.get(f"{BASE}/sports/{sport}/odds", params={"apiKey": key, "bookmakers": "pinnacle", "markets": "h2h"},
                     timeout=20)
    left = r.headers.get("x-requests-remaining")
    if r.status_code != 200:
        return [], left
    out = []
    for e in r.json():
        for b in e.get("bookmakers", []):
            for mk in b.get("markets", []):
                if mk.get("key") != "h2h":
                    continue
                pr = {o["name"]: o["price"] for o in mk.get("outcomes", [])}
                if e["home_team"] not in pr or e["away_team"] not in pr:
                    continue
                if "Draw" in pr:
                    fair = remove_margin({"home": pr[e["home_team"]], "draw": pr["Draw"], "away": pr[e["away_team"]]})
                elif len(pr) == 2 and family(sport) != "soccer":
                    fair = remove_margin({"home": pr[e["home_team"]], "away": pr[e["away_team"]]})
                else:
                    continue
                if fair:
                    out.append({"home": e["home_team"], "away": e["away_team"], "start": _ts(e["commence_time"]), "fair": fair})
    return out, left


def betfair_snapshot(client, hours: float) -> list[dict]:
    """Mercati Match Odds su betfair.it che iniziano entro `hours` (calcio 1X2, gli altri sport a due esiti):
    miglior back e lay per esito."""
    from .feeds.betfair import SPORTS, BetfairError, split_event_name
    cat = []
    for kind in SPORT_PREFIXES:
        event_type, market_type, _ = SPORTS[kind]
        try:                                     # uno sport che il conto .it non ha non ferma gli altri
            cat += [{**m, "_sport": kind} for m in client.catalogue(event_type, market_type, hours, None,
                                                                   200 if kind in ("soccer", "tennis") else 100,
                                                                   lookback_hours=0)]
        except BetfairError:
            if kind == "soccer":
                raise
    ids = [m["marketId"] for m in cat]
    books = {b["marketId"]: b for b in client.books(ids)} if ids else {}
    out = []
    for m in cat:
        teams = split_event_name((m.get("event") or {}).get("name", ""))
        b = books.get(m["marketId"])
        if not teams or not b or b.get("status") != "OPEN" or b.get("inplay"):
            continue
        names = {r["selectionId"]: r["runnerName"] for r in m.get("runners", [])}
        sel = {}
        for r in b.get("runners", []):
            nm = names.get(r["selectionId"], "")
            side = "draw" if nm.lower() in ("the draw", "pareggio", "draw") else "home" if nm == teams[0] else "away" if nm == teams[1] else None
            ex = r.get("ex") or {}
            back = (ex.get("availableToBack") or [{}])[0]
            lay = (ex.get("availableToLay") or [{}])[0]
            if side:
                sel[side] = {"back": back.get("price"), "back_size": back.get("size"), "lay": lay.get("price"),
                             "lay_size": lay.get("size")}
        expected = {"home", "draw", "away"} if m["_sport"] == "soccer" else {"home", "away"}
        if set(sel) == expected:
            out.append({"market_id": m["marketId"], "sport": m["_sport"], "home": teams[0], "away": teams[1],
                        "start": _ts(m["marketStartTime"]), "league": (m.get("competition") or {}).get("name"), "sel": sel})
    return out


# ── abbinamento Betfair ↔ Pinnacle ─────────────────────────────────────────
def match_pin(bf: dict, pins: list[dict]) -> dict | None:
    """La quota di Pinnacle della stessa partita. Nei testa a testa (tennis soprattutto) le due fonti possono mettere
    i nomi in ordine diverso: si prova anche l'ordine inverso e, se vince quello, casa e ospite si scambiano."""
    from .palestra import _sim
    tol = START_TOLERANCE_S.get(family(bf.get("sport")), 20 * 60)
    best, score = None, 0.0
    for p in pins:
        if abs(p["start"] - bf["start"]) > tol:
            continue
        s = _sim(bf["home"], p["home"]) + _sim(bf["away"], p["away"])
        if s > score:
            best, score = p, s
        if "draw" not in p["fair"]:
            s = _sim(bf["home"], p["away"]) + _sim(bf["away"], p["home"])
            if s > score:
                best, score = {**p, "home": p["away"], "away": p["home"],
                               "fair": {"home": p["fair"]["away"], "away": p["fair"]["home"]}}, s
    return best if score >= 1.2 else None


# ── raccolta ──────────────────────────────────────────────────────────────
def collect(client, key: str, hours: float = 6.0, bf_every: float = 15, pin_every: float = 60, credits: int = 150,
            out=print, sleep=time.sleep, now=time.time, sources=None) -> dict:
    """sources: per i test, {'sports','events','pinnacle','betfair'} al posto delle chiamate vere."""
    src = sources or {"sports": lambda: odds_api_sports(key), "events": lambda sp: odds_api_events(key, sp),
                      "pinnacle": lambda sp: pinnacle_odds(key, sp), "betfair": lambda h: betfair_snapshot(client, h)}
    t0 = now()
    end = t0 + hours * 3600
    active = set(src["sports"]())
    polls = int(hours * 60 // pin_every) + 1
    candidates = [s for s in PREFERRED if s in active] + sorted(s for s in active if s not in PREFERRED)
    sports = []
    for sp in candidates:                        # solo i campionati con partite nella finestra (/events è gratis)
        if len(sports) >= max(1, credits // polls):
            break
        try:
            if any(t0 + 30 * 60 < _ts(e["commence_time"]) < end + 15 * 60 for e in src["events"](sp)):
                sports.append(sp)
        except Exception:
            continue
    out(f"Campionati con partite nelle prossime {hours:g} ore e quote Pinnacle: {len(sports)} "
        f"(budget {credits} crediti, {polls} letture di Pinnacle).")
    bf_obs, pin_obs = [], []
    try:
        _loop(src, sports, end, bf_every, pin_every, bf_obs, pin_obs, out, sleep, now)
    except KeyboardInterrupt:                     # Ctrl+C: si tiene quello che è stato raccolto
        out("  Interrotto: salvo e analizzo quello che è stato raccolto finora.")
    data = {"start": t0, "end": now(), "sports": sports, "bf": bf_obs, "pin": pin_obs}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{datetime.fromtimestamp(t0).strftime('%Y-%m-%d_%H%M')}.json.gz").write_bytes(
        gzip.compress(json.dumps(data).encode()))
    return data


def _loop(src, sports, end, bf_every, pin_every, bf_obs, pin_obs, out, sleep, now) -> None:
    next_bf = next_pin = now()
    left = None
    while now() < end:
        t = now()
        if t >= next_pin and sports:
            for sp in sports:
                try:
                    rows, left = src["pinnacle"](sp)
                    pin_obs += [{**r, "t": t, "sport": sp} for r in rows]
                except Exception as exc:
                    out(f"  Pinnacle {sp}: {exc}")
            next_pin = t + pin_every * 60
        if t >= next_bf:
            try:
                snap = src["betfair"]((end - t) / 3600 + 0.25)
                bf_obs += [{**r, "t": t} for r in snap]
                out(f"  {datetime.now().strftime('%H:%M')} · Betfair: {len(snap)} partite in programma · "
                    f"letture Pinnacle {len(pin_obs)} · crediti rimasti {left or 'n.d.'}")
            except Exception as exc:
                out(f"  Betfair: {exc}")
            next_bf = t + bf_every * 60
        sleep(max(1.0, min(next_bf, next_pin if sports else next_bf) - now()))


# ── quando partire ────────────────────────────────────────────────────────
def best_start(kickoffs: list[float], hours: float, now: float, horizon_h: float = 72, step_min: float = 30,
               lead_min: float = 45) -> dict:
    """La finestra di `hours` ore con più partite misurabili (inizio tra `lead_min` minuti dall'avvio e la fine).
    Si sceglie la PRIMA partenza che arriva ad almeno l'80% del massimo: meglio misurare oggi che aspettare 2 giorni
    per poche partite in più."""
    def count(t0: float) -> int:
        return sum(1 for k in kickoffs if t0 + lead_min * 60 < k <= t0 + hours * 3600)
    starts = [now + i * step_min * 60 for i in range(int(horizon_h * 60 // step_min) + 1)]
    counts = [count(t) for t in starts]
    top = max(counts) if counts else 0
    pick = next((t for t, c in zip(starts, counts) if c >= 0.8 * top), now) if top else now
    return {"now": counts[0] if counts else 0, "start": pick, "n": count(pick), "max": top}


def plan(src: dict, hours: float, now: float) -> dict:
    """Calendario gratuito (/events) di tutti i campionati attivi → la finestra migliore."""
    kickoffs = []
    for sp in src["sports"]():
        try:
            kickoffs += [_ts(e["commence_time"]) for e in src["events"](sp)]
        except Exception:
            continue
    return best_start(kickoffs, hours, now)


def wait_until(start: float, out=print, sleep=time.sleep, now=time.time) -> None:
    """Attesa con un messaggio ogni ora (la finestra resta aperta: Ctrl+C per annullare)."""
    while now() < start - 1:
        left = start - now()
        out(f"  {datetime.now().strftime('%H:%M')} · partenza alle {datetime.fromtimestamp(start).strftime('%H:%M')} "
            f"(tra {left / 3600:.1f} ore)")
        sleep(min(3600.0, max(1.0, left)))


# ── analisi ───────────────────────────────────────────────────────────────
def _mean_ci(v: list[float]) -> tuple[float | None, float | None, float | None]:
    if not v:
        return None, None, None
    m = sum(v) / len(v)
    if len(v) < 2:
        return m, None, None
    se = math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1)) / math.sqrt(len(v))
    return m, m - 1.96 * se, m + 1.96 * se


def analyse(data: dict, min_edge: float = 0.02, lay_min: float = 3.0, lay_max: float = 8.0,
            entry_min_before: float = 45) -> dict:
    by_market: dict[str, list] = {}
    for o in data["bf"]:
        by_market.setdefault(o["market_id"], []).append(o)
    rows = []
    for mid, obs in by_market.items():
        obs.sort(key=lambda o: o["t"])
        start = obs[-1]["start"]
        if data["end"] < start:                       # non ancora iniziata: manca la chiusura
            continue
        pins = [p for p in data["pin"] if p["t"] < start]
        pin_close = None
        for p in sorted(pins, key=lambda p: -p["t"]):
            pin_close = match_pin(obs[-1], [p])      # già nell'ordine casa/ospite di Betfair
            if pin_close:
                break
        if not pin_close or start - pin_close["t"] > 75 * 60:
            continue
        for o in obs:                                  # ingresso: prima lettura Betfair con Pinnacle fresco, ≥45 min prima
            if start - o["t"] < entry_min_before * 60:
                break
            pe = [m for m in (match_pin(o, [p]) for p in pins if abs(p["t"] - o["t"]) <= 20 * 60) if m]
            if not pe:
                continue
            p_in = pe[-1]
            for side in ("home", "draw", "away"):
                s = o["sel"].get(side) or {}
                if not s or side not in p_in["fair"] or side not in pin_close["fair"]:
                    continue
                lay, back = s.get("lay"), s.get("back")
                pi, pc = p_in["fair"][side], pin_close["fair"][side]
                row = {"market": mid, "match": f"{o['home']} - {o['away']}", "league": o.get("league"), "side": side,
                       "sport": family(o.get("sport")),
                       "minutes_before": (start - o["t"]) / 60, "p_in": pi, "p_close": pc}
                if lay and lay > 1:
                    row.update(lay=lay, gap_lay=1 / (lay * pi) - 1, clv_lay=1 / (lay * pc) - 1,
                               ev_lay=((1 - pi) * (1 - COMM) - pi * (lay - 1)) / (lay - 1))
                if back and back > 1:
                    row.update(back=back, clv_back=back * pc - 1, ev_back=pi * (back - 1) * (1 - COMM) - (1 - pi))
                rows.append(row)
            break
    lays = [r for r in rows if "clv_lay" in r]
    soccer_lays = [r for r in lays if r["sport"] == "soccer"]
    s09 = [r for r in soccer_lays if lay_min <= r["lay"] <= lay_max and r["ev_lay"] >= min_edge]
    wide = [r for r in lays if r["ev_lay"] >= 0]
    backs = [r for r in rows if "clv_back" in r and r["ev_back"] >= min_edge]
    res = {"matches": len({r["market"] for r in rows}), "selections": len(rows), "sports": data.get("sports"),
           "hours": (data["end"] - data["start"]) / 3600}
    for name, grp, k in (("tutti_i_lay", lays, "clv_lay"), ("s09", s09, "clv_lay"), ("lay_ev_positivo", wide, "clv_lay"),
                         ("back_ev_positivo", backs, "clv_back")):
        m, lo, hi = _mean_ci([r[k] for r in grp])
        res[name] = {"n": len(grp), "clv": m, "lo": lo, "hi": hi}
    # ogni sport oltre al calcio: il prezzo che sembrava sbagliato (lay o back con EV ≥ 2%), misurato sul suo CLV
    res["sport"] = {}
    for fam in SPORT_PREFIXES[1:]:
        fr = [r for r in rows if r["sport"] == fam]
        if not fr:
            continue
        value = ([r["clv_lay"] for r in fr if "clv_lay" in r and r["ev_lay"] >= min_edge]
                 + [r["clv_back"] for r in fr if "clv_back" in r and r["ev_back"] >= min_edge])
        m, lo, hi = _mean_ci([r["clv_lay"] for r in fr if "clv_lay" in r])
        m2, lo2, hi2 = _mean_ci(value)
        res["sport"][fam] = {"partite": len({r["market"] for r in fr}),
                             "tutti_i_lay": {"n": sum(1 for r in fr if "clv_lay" in r), "clv": m, "lo": lo, "hi": hi},
                             "valore": {"n": len(value), "clv": m2, "lo": lo2, "hi": hi2},
                             "verdetto": ("SEGNALE PRESENTE" if len(value) >= 10 and lo2 is not None and lo2 > 0 else
                                          "SEGNALE ASSENTE" if len(value) >= 10 and hi2 is not None and hi2 < 0 else
                                          "NON ANCORA CHIARO")}
    # il prezzo che sembra "sbagliato" viene corretto dal mercato? pendenza CLV ~ gap
    xs = [(r["gap_lay"], r["clv_lay"]) for r in lays if abs(r["gap_lay"]) < 0.5]
    if len(xs) > 5:
        mx = sum(x for x, _ in xs) / len(xs)
        my = sum(y for _, y in xs) / len(xs)
        vx = sum((x - mx) ** 2 for x, _ in xs)
        res["pendenza"] = sum((x - mx) * (y - my) for x, y in xs) / vx if vx else None
    s = res["s09"]
    if s["n"] >= 10 and s["lo"] is not None and s["lo"] > 0:
        res["verdetto"] = "SEGNALE PRESENTE"
    elif s["n"] >= 10 and s["hi"] is not None and s["hi"] < 0:
        res["verdetto"] = "SEGNALE ASSENTE"
    else:
        res["verdetto"] = "NON ANCORA CHIARO"
    res["rows"] = rows
    return res


def report(res: dict) -> str:
    pct = lambda x: "—" if x is None else f"{x:+.1%}"
    L = ["# Test rapido: betfair.it contro Pinnacle", "",
         f"{res['hours']:.1f} ore di osservazione, {res['matches']} partite iniziate nella finestra, "
         f"{res['selections']} esiti misurati. Campionati: {', '.join(res.get('sports') or []) or '—'}.", "",
         f"**Verdetto su S09: {res['verdetto']}**", "",
         *[f"**Verdetto su {LABELS[f].lower()}: {v['verdetto']}** ({v['partite']} partite misurate)"
           for f, v in (res.get("sport") or {}).items()], "",
         "CLV = quanto il prezzo preso batte la quota giusta di Pinnacle alla chiusura (positivo = il mercato ci ha dato "
         "ragione). Intervallo al 95%.", "",
         "| Gruppo | Esiti | CLV medio | Intervallo |", "|---|---|---|---|"]
    names = {"tutti_i_lay": "Tutti i lay (riferimento: di solito negativo per lo spread)",
             "lay_ev_positivo": "Lay che sembravano convenienti (EV ≥ 0)", "s09": "Regola S09 (calcio, quote 3-8, EV ≥ 2%)",
             "back_ev_positivo": "Back che sembravano convenienti (EV ≥ 2%)"}
    groups = [(label, res.get(k)) for k, label in names.items()]
    for f, v in (res.get("sport") or {}).items():
        groups += [(f"{LABELS[f]}: tutti i lay", v["tutti_i_lay"]), (f"{LABELS[f]}: lay e back con EV ≥ 2%", v["valore"])]
    for label, g in groups:
        g = g or {"n": 0, "clv": None, "lo": None, "hi": None}
        L.append(f"| {label} | {g['n']} | {pct(g['clv'])} | {pct(g['lo'])} … {pct(g['hi'])} |")
    if res.get("pendenza") is not None:
        L += ["", f"Pendenza CLV/scarto: {res['pendenza']:+.2f}. Vicina a 1 = lo scarto Betfair-Pinnacle è un vantaggio vero; "
                  "vicina a 0 = lo scarto sparisce senza premiare chi lo sfrutta."]
    L += ["", "Con 6 ore di dati il risultato è un'indicazione, non una prova: se il verdetto è 'non ancora chiaro', "
              "ripeti il test in un'altra fascia oraria e le misure si sommano."]
    return "\n".join(L) + "\n"


def run(hours: float = 6.0, credits: int = 150, out=print, subito: bool = False) -> dict:
    from . import local_settings
    from .feeds.betfair import BetfairClient
    key = api_key()
    if not key:
        out("Manca la chiave di The Odds API (dashboard → Impostazioni → Chiavi dei dati).")
        return {}
    if not subito:                               # il calendario è gratis: si parte quando ci sono le partite
        pl = plan({"sports": lambda: odds_api_sports(key), "events": lambda sp: odds_api_events(key, sp)}, hours, time.time())
        if not pl["max"]:
            out("Nessuna partita in calendario nei prossimi 3 giorni per i campionati con Pinnacle. Riprova più avanti.")
            return {}
        if pl["now"] >= 0.8 * pl["max"]:
            out(f"Si parte subito: {pl['now']} partite misurabili nelle prossime {hours:g} ore.")
        else:
            out(f"Adesso le partite misurabili nelle prossime {hours:g} ore sono {pl['now']}. La finestra migliore parte "
                f"{datetime.fromtimestamp(pl['start']).strftime('%A %d/%m alle %H:%M')} con {pl['n']} partite.\n"
                "Aspetto e parto da solo: lascia aperta la finestra e il PC acceso (senza sospensione). "
                "Per partire comunque adesso: test_rapido.bat subito")
            try:
                wait_until(pl["start"], out=out)
            except KeyboardInterrupt:
                out("Annullato prima di partire: nessun credito usato.")
                return {}
    client = BetfairClient(local_settings.load()["betfair"])
    client.login()                               # dopo l'attesa: la sessione Betfair è fresca
    out(f"Test rapido: {hours:g} ore di osservazione di betfair.it e Pinnacle. Nessuna puntata. Lascia aperta la finestra.\n"
        "Se lo interrompi (Ctrl+C), analizza comunque quello che ha raccolto fino a quel momento.")
    data = collect(client, key, hours=hours, credits=credits, out=out)
    res = analyse(data)
    md = report(res)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "test_rapido.md").write_text(md, encoding="utf-8")
    out("\n" + md)
    return res
