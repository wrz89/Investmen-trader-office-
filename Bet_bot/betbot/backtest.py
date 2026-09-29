"""Backtest su prezzi REALI di Betfair Exchange e Monte Carlo del rischio.

DATI GRATUITI
  • Calcio — football-data.co.uk: dal 2024/25 ogni partita ha le quote Betfair Exchange (BFEH/BFED/BFEA,
    rilevate 1-4 giorni prima: venerdì pomeriggio o martedì) e quelle di chiusura (BFECH/BFECD/BFECA),
    più le quote di diversi bookmaker, usati come riferimento per la probabilità "giusta".
    Attenzione: sono prezzi del pool internazionale (.com), non di quello italiano, e la liquidità
    di pochi giorni prima è bassa: sono un limite superiore di ciò che il bot troverebbe su betfair.it.
  • Tennis — tennis-data.co.uk (ATP e WTA): quote di più bookmaker per partita (vedi load_tennis).
  • CSV generico: date, league, home, away, result (H/D/A o home/draw/away), colonne
    odds_<bookmaker>_<home|draw|away> per il riferimento e exchange_<home|draw|away> per il prezzo Betfair.

COSA SI SIMULA (senza guardare al futuro)
  • la decisione usa solo i prezzi pre-partita; si punta al prezzo Betfair (quota back);
  • commissione 4,5% sulla vincita netta, puntate da 2 € a multipli di 0,50 € (regole di betfair.it);
  • stesso Risk Manager del bot: 1/4 di Kelly netto, puntata minima solo con vantaggio netto,
    tetto per puntata, massimo di puntate al giorno, kill switch sul drawdown;
  • bankroll che si reinveste (compounding) partendo dal capitale di settings.yaml (30 €);
  • CLV: quota presa contro la quota Betfair di chiusura (se c'è), altrimenti contro Pinnacle di chiusura.

Strategie confrontate
  NAIVE_80                  l'idea di partenza "alla lettera": ogni favorito a quota 1,15–1,25, 2 € fissi.
  S05_favoriti_exchange_v1  favoriti con probabilità giusta ≥ 75% E prezzo Betfair netto sopra il giusto.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from .agents.risk import kelly_net, stake_for
from .config import REPORTS_DIR, RUNTIME_DIR, load_settings, load_yaml
from .odds import consensus, remove_margin

HISTORY_DIR = RUNTIME_DIR / "history"
FD_URL = "https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"
FD_BOOKS = {"B365": "Bet365", "BW": "Bet&Win", "IW": "Interwetten", "PS": "Pinnacle", "WH": "William Hill",
            "VC": "VC Bet", "1XB": "1xBet", "BFD": "Betfred"}
LEAGUES = {"I1": "Serie A", "I2": "Serie B", "E0": "Premier League", "E1": "Championship", "SP1": "La Liga",
           "SP2": "La Liga 2", "D1": "Bundesliga", "D2": "2. Bundesliga", "F1": "Ligue 1", "F2": "Ligue 2",
           "N1": "Eredivisie", "P1": "Primeira Liga", "B1": "Jupiler League", "T1": "Süper Lig", "G1": "Super League",
           "SC0": "Scottish Premiership"}
EXCHANGE_DIVS = ["I1", "I2", "E0", "E1", "SP1", "SP2", "D1", "D2", "F1", "F2", "N1", "P1", "B1", "T1", "G1", "SC0"]


def exchange_seasons(today: date | None = None) -> list[str]:
    """Stagioni con i prezzi Betfair Exchange su football-data: dal 2024/25 a quella in corso."""
    today = today or date.today()
    last = today.year if today.month >= 7 else today.year - 1
    return [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in range(2024, last + 1)]


def download(divs: list[str], seasons: list[str], refresh_current: bool = True) -> list[Path]:
    """Scarica (una volta sola) i CSV. La stagione in corso si riscarica, al massimo una volta al giorno."""
    import time

    import requests
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    current = exchange_seasons()[-1]
    out = []
    for season in seasons:
        for div in divs:
            path = HISTORY_DIR / f"{season}_{div}.csv"
            stale = refresh_current and season == current and path.exists() and time.time() - path.stat().st_mtime > 86400
            if not path.exists() or stale:
                r = requests.get(FD_URL.format(season=season, div=div), timeout=30)
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                path.write_bytes(r.content)
            out.append(path)
    return out


def _num(x) -> float | None:
    try:
        v = float(x)
        return v if v > 1.0 and not math.isnan(v) else None
    except (TypeError, ValueError):
        return None


def _triple(r, prefix: str) -> dict | None:
    h, d, a = (_num(r.get(f"{prefix}{s}")) for s in "HDA")
    return {"home": h, "draw": d, "away": a} if h and d and a else None


def load(paths: list[Path]) -> list[dict]:
    """Righe normalizzate: {date, sport, league, home, away, result, books, exchange, exchange_close, closing, score}."""
    rows = []
    for path in paths:
        try:
            if str(path).endswith((".xlsx", ".xls")):
                df = pd.read_excel(path)
            else:
                df = pd.read_csv(path, encoding="latin-1", on_bad_lines="skip")
        except Exception:
            continue
        df.columns = [str(c).strip().lstrip("﻿").lstrip("ï»¿") for c in df.columns]
        if "FTR" in df.columns:
            rows += _load_football_data(df)
        elif "Winner" in df.columns and "Loser" in df.columns:
            rows += load_tennis_df(df)
        elif "result" in df.columns:
            rows += _load_generic(df)
    rows = [x for x in rows if x["exchange"] and x["books"] and not pd.isna(x["date"])]
    rows.sort(key=lambda x: x["date"])
    return rows


def _load_football_data(df: pd.DataFrame) -> list[dict]:
    out = []
    for _, r in df.iterrows():
        if pd.isna(r.get("FTR")) or pd.isna(r.get("HomeTeam")):
            continue
        books = {name: t for pre, name in FD_BOOKS.items() if (t := _triple(r, pre))}
        out.append({"date": pd.to_datetime(r["Date"], dayfirst=True, errors="coerce"), "sport": "soccer",
                    "league": LEAGUES.get(r.get("Div"), r.get("Div")), "home": r["HomeTeam"], "away": r["AwayTeam"],
                    "result": {"H": "home", "D": "draw", "A": "away"}[r["FTR"]], "books": books,
                    "exchange": _triple(r, "BFE"), "exchange_close": _triple(r, "BFEC"),
                    "closing": _triple(r, "PSC") or _triple(r, "AvgC"),
                    "score": f"{int(r['FTHG'])}-{int(r['FTAG'])}" if not pd.isna(r.get("FTHG")) else ""})
    return out


def load_tennis_df(df: pd.DataFrame) -> list[dict]:
    """tennis-data.co.uk: una riga per partita con Winner/Loser e quote W/L di più bookmaker.
    Per non sapere in anticipo chi vince, "home" è il giocatore col ranking migliore (colonne WRank/LRank)."""
    # "Avg" = media di mercato di tennis-data: dal 2025 Pinnacle manca, la media è il riferimento più stabile
    books_map = {"B365": "Bet365", "PS": "Pinnacle", "EX": "Expekt", "LB": "Ladbrokes", "SJ": "Stan James", "UB": "Unibet",
                 "Avg": "Media di mercato"}
    out = []
    for _, r in df.iterrows():
        comment = str(r.get("Comment", "Completed")).strip()
        if comment not in ("Completed", "nan", "Retired"):
            continue                                   # walkover e squalifiche: scommesse annullate
        if comment == "Retired":
            # regola Betfair: ritiro prima della fine del 1° set = annullata; dopo, vince chi passa il turno
            try:
                first_set_done = max(float(r.get("W1")), float(r.get("L1"))) >= 6
            except (TypeError, ValueError):
                first_set_done = False
            if not first_set_done:
                continue
        w, l = r.get("Winner"), r.get("Loser")
        try:
            wr, lr = float(r.get("WRank")), float(r.get("LRank"))
        except (TypeError, ValueError):
            wr, lr = 1.0, 2.0
        home_is_w = wr <= lr
        books = {}
        for pre, name in books_map.items():
            ow, ol = _num(r.get(f"{pre}W")), _num(r.get(f"{pre}L"))
            if ow and ol:
                books[name] = {"home": ow, "away": ol} if home_is_w else {"home": ol, "away": ow}
        ex = None
        bw, bl = _num(r.get("BFEW")), _num(r.get("BFEL"))
        if bw and bl:
            ex = {"home": bw, "away": bl} if home_is_w else {"home": bl, "away": bw}
        out.append({"date": pd.to_datetime(r.get("Date"), dayfirst=True, errors="coerce"), "sport": "tennis",
                    "league": f"{r.get('Tournament', '')} ({r.get('Series', r.get('Tier', ''))})",
                    "home": w if home_is_w else l, "away": l if home_is_w else w,
                    "result": "home" if home_is_w else "away", "books": books, "exchange": ex,
                    "exchange_close": None, "closing": None, "score": ""})
    return out


def _load_generic(df: pd.DataFrame) -> list[dict]:
    out = []
    for _, r in df.iterrows():
        books: dict[str, dict] = {}
        for c in df.columns:
            if c.startswith("odds_"):
                _, book, sel = c.split("_", 2)
                if (v := _num(r[c])):
                    books.setdefault(book, {})[sel] = v
        ex = {s: _num(r.get(f"exchange_{s}")) for s in ("home", "draw", "away") if _num(r.get(f"exchange_{s}"))}
        closing = {s: _num(r.get(f"closing_{s}")) for s in ("home", "draw", "away") if _num(r.get(f"closing_{s}"))}
        res = str(r["result"]).strip()
        out.append({"date": pd.to_datetime(r["date"], errors="coerce"), "sport": str(r.get("sport", "soccer")),
                    "league": r.get("league", ""), "home": r["home"], "away": r["away"],
                    "result": {"H": "home", "D": "draw", "A": "away"}.get(res, res), "books": books,
                    "exchange": ex or None, "exchange_close": None, "closing": closing or None, "score": ""})
    return out


# ── selezione (stesse regole delle strategie live) ─────────────────────────────
def pick_naive(row: dict, p: dict) -> list[dict]:
    """Il favorito secondo il riferimento, se il prezzo Betfair è tra 1,15 e 1,25. Nessun controllo di valore."""
    c = consensus(row["books"])
    fav = max(((s, v) for s, v in c.items() if not s.startswith("_")), key=lambda kv: kv[1]["fair_prob"], default=None)
    if not fav or fav[0] not in row["exchange"]:
        return []
    price = row["exchange"][fav[0]]
    if not (p["odds_min"] <= price <= p["odds_max"]):
        return []
    return [{"selection": fav[0], "odds": price, "fair_prob": fav[1]["fair_prob"], "flat": True}]


def pick_s05(row: dict, p: dict) -> list[dict]:
    c = consensus(row["books"])
    out = []
    for sel, v in c.items():
        if sel.startswith("_") or sel not in row["exchange"] or v["n_books"] < p.get("min_reference_books", 2):
            continue
        price = row["exchange"][sel]
        ev = v["fair_prob"] * (price - 1) * (1 - p["commission"]) - (1 - v["fair_prob"])
        if p["odds_min"] <= price <= p["odds_max"] and v["fair_prob"] >= p["min_fair_prob"] and ev >= p["min_edge"]:
            out.append({"selection": sel, "odds": price, "fair_prob": v["fair_prob"], "edge": ev})
    return out


PICKERS = {"NAIVE_80": pick_naive, "S05_favoriti_exchange_v1": pick_s05}


@dataclass
class Result:
    strategy: str
    bets: pd.DataFrame
    equity: pd.DataFrame
    metrics: dict


def run(rows: list[dict], strategy: str, limits: dict | None = None, params: dict | None = None,
        initial: float | None = None, kill_switch: bool = True, commission: float | None = None,
        min_stake: float | None = None) -> Result:
    settings = load_settings()
    ex = settings.get("execution") or {}
    commission = float(ex.get("commission", 0.045)) if commission is None else commission
    min_stake = float(ex.get("min_stake", 2.0)) if min_stake is None else min_stake
    initial = float(settings["capital"]["initial"]) if initial is None else initial
    limits = dict(limits or load_yaml("risk_limits.yaml"))
    if not kill_switch:
        limits["max_drawdown"] = 1.01
    if strategy == "NAIVE_80":
        p = {"odds_min": 1.15, "odds_max": 1.25, **(params or {})}
    else:
        p = {**load_yaml("strategies.yaml").get(strategy, {}), **(params or {})}
    p["commission"] = commission
    picker = PICKERS[strategy]
    bank, peak, max_dd, kill_date = initial, initial, 0.0, None
    bets, curve = [], []
    by_day: dict = {}
    for row in rows:
        by_day.setdefault(row["date"].date(), []).append(row)
    for day, matches in by_day.items():
        if kill_date or bank < min_stake:
            break
        todays = [(row, pk) for row in matches for pk in picker(row, p)]
        placed, open_risk = [], 0.0
        for row, pk in todays:
            if len(placed) >= limits["max_bets_per_day"]:
                break
            base = bank if bank < initial * limits["floor_pct_of_initial"] else \
                min(bank, initial + max(0.0, bank - initial) * limits["reinvest_fraction"])
            if pk.get("flat"):
                stake = min_stake                               # "alla lettera": sempre la puntata minima
            else:
                stake, _ = stake_for({"fair_prob": pk["fair_prob"], "odds": pk["odds"], "commission": commission},
                                     base, limits, min_stake)
            if not stake or open_risk + stake > limits["max_open_risk_pct"] * bank + 1e-9 and not pk.get("flat"):
                continue
            if open_risk + stake > bank:
                continue
            open_risk += stake
            placed.append((row, pk, stake))
        for row, pk, stake in placed:                           # le partite del giorno si chiudono a fine giornata
            won = row["result"] == pk["selection"]
            pnl = stake * (pk["odds"] - 1) * (1 - commission) if won else -stake
            bank += pnl
            clv = None
            if row.get("exchange_close") and row["exchange_close"].get(pk["selection"]):
                clv = pk["odds"] / row["exchange_close"][pk["selection"]] - 1.0
            elif row.get("closing"):
                clv = pk["odds"] * remove_margin(row["closing"])[pk["selection"]] - 1.0
            bets.append({"date": day, "sport": row["sport"], "league": row["league"], "match": f"{row['home']} - {row['away']}",
                         "selection": pk["selection"], "odds": pk["odds"], "fair_prob": round(pk["fair_prob"], 4),
                         "kelly": round(kelly_net(pk["fair_prob"], pk["odds"], commission), 4), "stake": stake,
                         "won": won, "pnl": round(pnl, 4), "bank": round(bank, 4), "clv": clv, "score": row["score"]})
        peak = max(peak, bank)
        dd = 1 - bank / peak if peak else 0
        max_dd = max(max_dd, dd)
        curve.append({"date": day, "bank": bank, "drawdown": dd})
        if dd >= limits["max_drawdown"]:
            kill_date = day
    bdf, edf = pd.DataFrame(bets), pd.DataFrame(curve)
    m = {"strategy": strategy, "bets": len(bdf), "initial": initial, "final": round(bank, 2),
         "return": bank / initial - 1, "max_drawdown": max_dd, "kill_switch": str(kill_date) if kill_date else None,
         "commission": commission, "matches": len(rows),
         "period": f"{rows[0]['date'].date()} → {rows[-1]['date'].date()}" if rows else ""}
    if len(bdf):
        m.update(win_rate=float(bdf["won"].mean()), avg_odds=float(bdf["odds"].mean()),
                 breakeven=float((1 / (1 + (bdf["odds"] - 1) * (1 - commission))).mean()), staked=float(bdf["stake"].sum()),
                 pnl=float(bdf["pnl"].sum()), roi=float(bdf["pnl"].sum() / bdf["stake"].sum()),
                 clv_avg=float(bdf["clv"].dropna().mean()) if bdf["clv"].notna().any() else None,
                 flat_roi=float(((bdf["odds"] - 1) * (1 - commission) * bdf["won"] - (~bdf["won"])).mean()))
    return Result(strategy, bdf, edf, m)


def report(results: list[Result], rows: list[dict], title: str = "Backtest Bet_bot su prezzi Betfair Exchange") -> str:
    lines = [f"# {title}", ""]
    if rows:
        sports = sorted({r["sport"] for r in rows})
        lines += [f"Partite con prezzo Betfair: {len(rows)} ({', '.join(sports)}) · periodo "
                  f"{rows[0]['date'].date()} → {rows[-1]['date'].date()} · commissione "
                  f"{results[0].metrics['commission']:.1%} · capitale {results[0].metrics['initial']:.0f} €", ""]
    lines += ["| Strategia | Puntate | Vinte | Pareggio | Quota media | ROI | ROI 1 € fisso | CLV medio | Bankroll | Max DD | Kill switch |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        m = r.metrics
        if not m["bets"]:
            lines.append(f"| {m['strategy']} | 0 | — | — | — | — | — | — | {m['initial']:.0f} → {m['final']:.2f} | — | — |")
            continue
        clv = "—" if m.get("clv_avg") is None else f"{m['clv_avg']:+.2%}"
        lines.append(f"| {m['strategy']} | {m['bets']} | {m['win_rate']:.1%} | {m['breakeven']:.1%} | {m['avg_odds']:.2f} | "
                     f"{m['roi']:+.2%} | {m['flat_roi']:+.2%} | {clv} | {m['initial']:.0f} → {m['final']:.2f} | "
                     f"{m['max_drawdown']:.1%} | {m['kill_switch'] or 'no'} |")
    lines += ["", "- **Pareggio** = win rate minimo per non perdere alla quota media, commissione compresa.",
              "- **ROI 1 € fisso** = rendimento medio di 1 € su ogni segnale, senza sizing: misura il segnale puro.",
              "- **CLV** = quota presa contro la quota Betfair di chiusura. Positivo in modo stabile = vantaggio reale.",
              "- Prezzi del pool internazionale rilevati giorni prima: su betfair.it (pool separato) i numeri veri si "
              "misurano col registratore (`feed.record: true`) e `python betbot.py replay`."]
    return "\n".join(lines) + "\n"


def save(results: list[Result], rows: list[dict], tag: str, title: str | None = None) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    md = report(results, rows, title or "Backtest Bet_bot su prezzi Betfair Exchange")
    path = REPORTS_DIR / f"backtest_{tag}.md"
    path.write_text(md, encoding="utf-8")
    for r in results:
        if len(r.bets):
            r.bets.to_csv(REPORTS_DIR / f"backtest_{tag}_{r.strategy}_puntate.csv", index=False)
            r.equity.to_csv(REPORTS_DIR / f"backtest_{tag}_{r.strategy}_bankroll.csv", index=False)
    return path


# ── Monte Carlo: cosa significa davvero "80% di vincite a quota 1,22" ─────────
def montecarlo(win_prob: float, odds: float, stake_pct: float, n_bets: int = 1000, paths: int = 2000,
               kill_dd: float = 0.15, seed: int = 1, commission: float = 0.045) -> dict:
    rng = random.Random(seed)
    net = (odds - 1) * (1 - commission)
    finals, killed, dds = [], 0, []
    for _ in range(paths):
        bank = peak = 1.0
        worst = 0.0
        for _ in range(n_bets):
            stake = bank * stake_pct
            bank += stake * net if rng.random() < win_prob else -stake
            peak = max(peak, bank)
            worst = max(worst, 1 - bank / peak)
        finals.append(bank)
        dds.append(worst)
        killed += worst >= kill_dd
    finals.sort()
    return {"ev_per_bet": win_prob * net - (1 - win_prob), "breakeven": 1 / (1 + net), "median_final": finals[len(finals) // 2],
            "p5_final": finals[int(len(finals) * 0.05)], "p95_final": finals[int(len(finals) * 0.95)],
            "prob_loss": sum(f < 1 for f in finals) / paths, "prob_kill_switch": killed / paths,
            "median_max_dd": sorted(dds)[len(dds) // 2]}
