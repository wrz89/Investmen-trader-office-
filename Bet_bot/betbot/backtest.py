"""Backtest su storico reale e Monte Carlo del rischio.

DATI: football-data.co.uk (gratuito) — per ogni partita risultato, quote 1X2 di
apertura di più bookmaker (Bet365, Bet&Win, Pinnacle, William Hill, 1xBet, …) e
quote di CHIUSURA di Pinnacle (PSCH/PSCD/PSCA), il riferimento per il CLV.
Si accetta anche un CSV generico: date, league, home, away, result (H/D/A o
home/draw/away) e colonne odds_<bookmaker>_<home|draw|away> (+ closing_<home|draw|away>).

COSA SI SIMULA (senza guardare al futuro):
  • la puntata si decide con le sole quote di apertura; quota presa = migliore tra i bookmaker
    nominati (le colonne "Max" non si usano: includono operatori non accessibili dall'Italia);
  • stessi filtri delle strategie live e stesso Risk Manager: Kelly frazionario con tetto,
    esposizione giornaliera, perdita giornaliera, kill switch sul drawdown;
  • bankroll che si reinveste (compounding), in ordine di data;
  • CLV: quota presa contro quota "giusta" di chiusura Pinnacle (margine tolto col metodo potenza).

Strategie confrontate:
  NAIVE_80   la richiesta iniziale "alla lettera": ogni favorito a quota 1,15–1,25, senza edge.
  S01        favoriti con probabilità ≥ 78% E quota migliore sopra il consenso (edge ≥ 1,5%).
  S03        sure bet tra i bookmaker nominati.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .config import REPORTS_DIR, RUNTIME_DIR, load_yaml
from .odds import consensus, remove_margin, surebet
from .agents.risk import stake_for

HISTORY_DIR = RUNTIME_DIR / "history"
FD_URL = "https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"
FD_BOOKS = {"B365": "Bet365", "BW": "Bet&Win", "IW": "Interwetten", "PS": "Pinnacle", "WH": "William Hill",
            "VC": "VC Bet", "1XB": "1xBet", "BFD": "Betfred"}
LEAGUES = {"I1": "Serie A", "I2": "Serie B", "E0": "Premier League", "E1": "Championship", "SP1": "La Liga",
           "D1": "Bundesliga", "F1": "Ligue 1", "N1": "Eredivisie", "P1": "Primeira Liga"}


def download(divs: list[str], seasons: list[str]) -> list[Path]:
    import requests
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for season in seasons:
        for div in divs:
            path = HISTORY_DIR / f"{season}_{div}.csv"
            if not path.exists():
                r = requests.get(FD_URL.format(season=season, div=div), timeout=30)
                r.raise_for_status()
                path.write_bytes(r.content)
            out.append(path)
    return out


def default_seasons(n: int = 6, last_start_year: int = 2024) -> list[str]:
    return [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in range(last_start_year - n + 1, last_start_year + 1)]


def _num(x) -> float | None:
    try:
        v = float(x)
        return v if v > 1.0 and not math.isnan(v) else None
    except (TypeError, ValueError):
        return None


def load(paths: list[Path]) -> list[dict]:
    """Righe normalizzate: {date, league, home, away, result, books, closing}."""
    rows = []
    for path in paths:
        df = pd.read_csv(path, encoding="latin-1", on_bad_lines="skip")
        df.columns = [c.strip().lstrip("﻿").lstrip("ï»¿") for c in df.columns]
        fd = "FTR" in df.columns
        for _, r in df.iterrows():
            if fd:
                if pd.isna(r.get("FTR")) or pd.isna(r.get("HomeTeam")):
                    continue
                books = {}
                for pre, name in FD_BOOKS.items():
                    h, d, a = (_num(r.get(f"{pre}{s}")) for s in "HDA")
                    if h and d and a:
                        books[name] = {"home": h, "draw": d, "away": a}
                ch, cd, ca = (_num(r.get(f"PSC{s}")) for s in "HDA")
                if not (ch and cd and ca):
                    ch, cd, ca = (_num(r.get(f"AvgC{s}")) for s in "HDA")
                closing = {"home": ch, "draw": cd, "away": ca} if ch and cd and ca else None
                date = pd.to_datetime(r["Date"], dayfirst=True, errors="coerce")
                res = {"H": "home", "D": "draw", "A": "away"}[r["FTR"]]
                rows.append({"date": date, "league": LEAGUES.get(r.get("Div"), r.get("Div")), "home": r["HomeTeam"],
                             "away": r["AwayTeam"], "result": res, "books": books, "closing": closing,
                             "score": f"{int(r['FTHG'])}-{int(r['FTAG'])}" if not pd.isna(r.get("FTHG")) else ""})
            else:
                books: dict[str, dict] = {}
                for c in df.columns:
                    if c.startswith("odds_"):
                        _, book, sel = c.split("_", 2)
                        v = _num(r[c])
                        if v:
                            books.setdefault(book, {})[sel] = v
                closing = {s: _num(r.get(f"closing_{s}")) for s in ("home", "draw", "away")}
                res = str(r["result"]).strip()
                res = {"H": "home", "D": "draw", "A": "away"}.get(res, res)
                rows.append({"date": pd.to_datetime(r["date"], errors="coerce"), "league": r.get("league", ""),
                             "home": r["home"], "away": r["away"], "result": res, "books": books,
                             "closing": closing if all(closing.values()) else None, "score": ""})
    rows = [x for x in rows if x["books"] and not pd.isna(x["date"])]
    rows.sort(key=lambda x: x["date"])
    return rows


# ── selezione (stessi filtri delle strategie live) ─────────────────────────────
def pick_naive(row: dict, p: dict) -> list[dict]:
    out = []
    for sel in ("home", "away"):
        prices = [(b[sel], name) for name, b in row["books"].items() if sel in b]
        if not prices:
            continue
        best, book = max(prices)
        if p["odds_min"] <= best <= p["odds_max"]:
            c = consensus(row["books"]).get(sel, {})
            out.append({"selection": sel, "odds": best, "book": book, "fair_prob": c.get("fair_prob", 1 / best),
                        "edge": c.get("edge", 0.0), "flat": True})
    return out


def pick_s01(row: dict, p: dict) -> list[dict]:
    c = consensus(row["books"])
    out = []
    for sel, v in c.items():
        if sel.startswith("_") or v["n_books"] < 3:
            continue
        if p["odds_min"] <= v["best_odds"] <= p["odds_max"] and v["fair_prob"] >= p["min_fair_prob"] \
                and v["edge"] >= p["min_edge"]:
            out.append({"selection": sel, "odds": v["best_odds"], "book": v["best_book"], "fair_prob": v["fair_prob"],
                        "edge": v["edge"]})
    return out


def pick_s03(row: dict, p: dict) -> list[dict]:
    best = {}
    for name, b in row["books"].items():
        for sel, o in b.items():
            if sel not in best or o > best[sel][0]:
                best[sel] = (o, name)
    if len(best) < 3 or max(o for o, _ in best.values()) > p["max_odds"]:
        return []
    arb = surebet(best)
    if not arb or arb["margin"] < p["min_margin"] or arb["margin"] > p.get("max_margin", 0.03):
        return []
    return [{"selection": "home+draw+away", "odds": 1 + arb["margin"], "book": "+".join(sorted(set(arb["books"].values()))),
             "fair_prob": 1.0, "edge": arb["margin"], "legs": True}]


PICKERS = {"NAIVE_80": pick_naive, "S01_favoriti_v1": pick_s01, "S03_surebet_v1": pick_s03}


@dataclass
class Result:
    strategy: str
    bets: pd.DataFrame
    equity: pd.DataFrame
    metrics: dict


def run(rows: list[dict], strategy: str, limits: dict | None = None, params: dict | None = None,
        initial: float = 100.0, kill_switch: bool = True) -> Result:
    limits = dict(limits or load_yaml("risk_limits.yaml"))
    if not kill_switch:                        # per misurare il segnale su tutto lo storico
        limits["max_drawdown"] = 1.01
    sparams = load_yaml("strategies.yaml")
    if strategy == "NAIVE_80":
        p = {"odds_min": 1.15, "odds_max": 1.25, **(params or {})}
    else:
        p = {**sparams.get(strategy, {}), **(params or {})}
    picker = PICKERS[strategy]
    bank, peak, max_dd, kill_date = initial, initial, 0.0, None
    bets, curve = [], []
    by_day: dict = {}
    for row in rows:
        by_day.setdefault(row["date"].date(), []).append(row)
    for day, matches in by_day.items():
        if kill_date:
            break
        day_start, n_day = bank, 0
        todays = []
        for row in matches:
            for pk in picker(row, p):
                todays.append((row, pk))
        open_stakes = 0.0
        placed = []
        for row, pk in todays:
            if n_day >= limits["max_bets_per_day"] or bank <= 0:
                break
            base = bank if bank < initial * limits["floor_pct_of_initial"] else \
                min(bank, initial + max(0.0, bank - initial) * limits["reinvest_fraction"])
            if pk.get("flat"):                                 # NAIVE: puntata fissa, come chiesto "alla lettera"
                stake = round(min(limits["max_stake_pct"], limits["flat_stake_pct"]) * base, 2)
            else:
                stake, _ = stake_for({"fair_prob": pk["fair_prob"], "odds": pk["odds"], "legs": pk.get("legs")}, base, limits)
            stake = min(stake, limits["max_open_exposure_pct"] * bank - open_stakes)
            if stake < limits["min_stake"]:
                continue
            open_stakes += stake
            n_day += 1
            placed.append((row, pk, round(stake, 2)))
        for row, pk, stake in placed:                           # le partite del giorno si chiudono a fine giornata
            won = row["result"] in pk["selection"].split("+")
            pnl = stake * (pk["odds"] - 1) if won else -stake
            bank += pnl
            clv = None
            if row["closing"] and "+" not in pk["selection"]:
                fair_close = remove_margin(row["closing"])[pk["selection"]]
                clv = pk["odds"] * fair_close - 1.0
            bets.append({"date": day, "league": row["league"], "match": f"{row['home']} - {row['away']}",
                         "selection": pk["selection"], "book": pk["book"], "odds": pk["odds"],
                         "fair_prob": round(pk["fair_prob"], 4), "edge": round(pk["edge"], 4), "stake": stake,
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
         "period": f"{rows[0]['date'].date()} → {rows[-1]['date'].date()}" if rows else ""}
    if len(bdf):
        m.update(win_rate=float(bdf["won"].mean()), avg_odds=float(bdf["odds"].mean()),
                 breakeven=float((1 / bdf["odds"]).mean()), staked=float(bdf["stake"].sum()),
                 pnl=float(bdf["pnl"].sum()), roi=float(bdf["pnl"].sum() / bdf["stake"].sum()),
                 clv_avg=float(bdf["clv"].dropna().mean()) if bdf["clv"].notna().any() else None,
                 clv_positive=float((bdf["clv"].dropna() > 0).mean()) if bdf["clv"].notna().any() else None,
                 flat_roi=float(((bdf["odds"] - 1) * bdf["won"] - (~bdf["won"])).mean()))
    return Result(strategy, bdf, edf, m)


def report(results: list[Result], rows: list[dict]) -> str:
    lines = ["# Backtest ufficio sportivo", "",
             f"Partite: {len(rows)} · periodo {rows[0]['date'].date()} → {rows[-1]['date'].date()}" if rows else "", "",
             "| Strategia | Puntate | Win rate | Pareggio | Quota media | ROI | ROI 1 € fisso | CLV medio | Bankroll | Max DD | Kill switch |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        m = r.metrics
        if not m["bets"]:
            lines.append(f"| {m['strategy']} | 0 | — | — | — | — | — | — | {m['final']:.2f} | — | — |")
            continue
        clv = "—" if m.get("clv_avg") is None else f"{m['clv_avg']:+.2%}"
        lines.append(f"| {m['strategy']} | {m['bets']} | {m['win_rate']:.1%} | {m['breakeven']:.1%} | {m['avg_odds']:.2f} | "
                     f"{m['roi']:+.2%} | {m['flat_roi']:+.2%} | {clv} | {m['initial']:.0f} → {m['final']:.2f} | "
                     f"{m['max_drawdown']:.1%} | {m['kill_switch'] or 'no'} |")
    lines += ["", "- **Pareggio** = win rate minimo per non perdere alla quota media (1/quota).",
              "- **ROI 1 € fisso** = rendimento medio di 1 € su ogni segnale, senza sizing: misura il segnale puro.",
              "- **CLV** = quota presa contro quota giusta di chiusura Pinnacle. Positivo in modo stabile = vantaggio reale.",
              "- Le quote di apertura di football-data sono rilevate giorni prima: nella realtà alcune non sarebbero più "
              "disponibili al momento della puntata e i bookmaker limitano i conti vincenti."]
    return "\n".join(lines) + "\n"


def save(results: list[Result], rows: list[dict], tag: str) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    md = report(results, rows)
    path = REPORTS_DIR / f"backtest_{tag}.md"
    path.write_text(md, encoding="utf-8")
    for r in results:
        if len(r.bets):
            r.bets.to_csv(REPORTS_DIR / f"backtest_{tag}_{r.strategy}_puntate.csv", index=False)
            r.equity.to_csv(REPORTS_DIR / f"backtest_{tag}_{r.strategy}_bankroll.csv", index=False)
    return path


# ── Monte Carlo: cosa significa davvero "80% di vincite a quota 1,22" ─────────
def montecarlo(win_prob: float, odds: float, stake_pct: float, n_bets: int = 1000, paths: int = 2000,
               kill_dd: float = 0.12, seed: int = 1) -> dict:
    rng = random.Random(seed)
    finals, killed, dds = [], 0, []
    for _ in range(paths):
        bank = peak = 1.0
        worst = 0.0
        for _ in range(n_bets):
            stake = bank * stake_pct
            bank += stake * (odds - 1) if rng.random() < win_prob else -stake
            peak = max(peak, bank)
            worst = max(worst, 1 - bank / peak)
        finals.append(bank)
        dds.append(worst)
        killed += worst >= kill_dd
    finals.sort()
    return {"ev_per_bet": win_prob * odds - 1, "breakeven": 1 / odds, "median_final": finals[len(finals) // 2],
            "p5_final": finals[int(len(finals) * 0.05)], "p95_final": finals[int(len(finals) * 0.95)],
            "prob_loss": sum(f < 1 for f in finals) / paths, "prob_kill_switch": killed / paths,
            "median_max_dd": sorted(dds)[len(dds) // 2]}
