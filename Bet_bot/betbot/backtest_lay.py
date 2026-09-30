"""Backtest del LAY DI VALORE (strategia S09) sui prezzi Betfair Exchange di football-data.co.uk.

Segnale onesto: Pinnacle e Betfair presi NELLO STESSO MOMENTO (colonne PS* e BFE*, raccolte il venerdì o il
martedì). La chiusura di Pinnacle (PSC*) serve solo per il CLV. Il file ha solo i prezzi back di Betfair:
il prezzo lay si stima `ticks` gradini sopra (1 = mercato liquido; 2-3 = prudente, più vicino a betfair.it).
"""
from __future__ import annotations

import math

import pandas as pd

from .backtest import EXCHANGE_DIVS, download, exchange_seasons
from .feeds.mock import tick_up
from .odds import remove_margin

MARKETS = {"1X2": (("PSH", "PSD", "PSA"), ("BFEH", "BFED", "BFEA"), ("PSCH", "PSCD", "PSCA")),
           "Over/Under 2,5": (("P>2.5", "P<2.5"), ("BFE>2.5", "BFE<2.5"), ("PC>2.5", "PC<2.5"))}


def _num(v):
    try:
        v = float(v)
        return v if v > 1.0 and not math.isnan(v) else None
    except (TypeError, ValueError):
        return None


def selections(divs: list[str] | None = None, ticks: int = 2) -> pd.DataFrame:
    rows = []
    for path in download(divs or EXCHANGE_DIVS, exchange_seasons()):
        try:
            df = pd.read_csv(path, encoding="latin-1", on_bad_lines="skip")
        except Exception:
            continue
        for _, r in df.iterrows():
            if pd.isna(r.get("FTHG")) or r.get("FTR") not in ("H", "D", "A"):
                continue
            goals = int(r["FTHG"]) + int(r["FTAG"])
            for market, (ps, bf, psc) in MARKETS.items():
                ref, back, close = ([_num(r.get(c)) for c in cols] for cols in (ps, bf, psc))
                if not all(ref) or not all(back):
                    continue
                keys = list(range(len(ref)))
                fair = remove_margin(dict(zip(keys, ref)))
                fair_c = remove_margin(dict(zip(keys, close))) if all(close) else None
                happened = {"H": 0, "D": 1, "A": 2}[r["FTR"]] if market == "1X2" else (0 if goals > 2 else 1)
                for i in keys:
                    rows.append({"div": path.stem.split("_", 1)[1], "season": path.stem.split("_")[0], "market": market,
                                 "p": fair[i], "p_close": fair_c[i] if fair_c else None,
                                 "lay": tick_up(back[i], ticks), "happened": i == happened})
    return pd.DataFrame(rows)


def run(d: pd.DataFrame, lay_min: float = 3.0, lay_max: float = 8.0, min_edge: float = 0.02,
        commission: float = 0.045, market: str | None = "1X2") -> dict:
    x = d[(d.lay >= lay_min) & (d.lay <= lay_max)]
    if market:
        x = x[x.market == market]
    x = x.assign(ev=(1 - x.p) * (1 - commission) - x.p * (x.lay - 1))
    x = x[x.ev >= min_edge * (x.lay - 1)]
    if not len(x):
        return {"bets": 0}
    win = ~x.happened
    pnl = win * (1 - commission) - (~win) * (x.lay - 1)
    r = pnl / (x.lay - 1)
    clv = (x.p_close.dropna() - x.p[x.p_close.notna()])        # >0: l'esito bancato si è accorciato… male per il lay
    return {"bets": len(x), "win_rate": float(win.mean()), "roi_risk": float(pnl.sum() / (x.lay - 1).sum()),
            "se": float(r.std() / math.sqrt(len(x))), "avg_lay": float(x.lay.mean()),
            "pnl_per_backer_eur": float(pnl.mean()), "clv_prob": float(-clv.mean()) if len(clv) else None,
            "seasons": sorted(x.season.unique())}


def report(ticks_list=(1, 2, 3)) -> str:
    lines = ["# Lay di valore (S09) su prezzi Betfair Exchange", "",
             "Si banca un esito quando il lay di Betfair costa meno della quota giusta di Pinnacle presa nello stesso "
             "momento. Commissione 4,5%. ROI calcolato sul rischio (responsabilità del lay).", ""]
    for t in ticks_list:
        d = selections(ticks=t)
        lines += [f"## Lay stimato {t} tick sopra il back", "",
                  "| Mercato | Quote lay | Puntate | Vinte | ROI sul rischio | Errore standard |", "|---|---|---|---|---|---|"]
        for market in ("1X2", "Over/Under 2,5"):
            for lo, hi in ((1.5, 3.0), (3.0, 8.0), (8.0, 15.0)):
                res = run(d, lo, hi, market=market)
                if res["bets"]:
                    lines.append(f"| {market} | {lo}-{hi} | {res['bets']} | {res['win_rate']:.1%} | "
                                 f"{res['roi_risk']:+.2%} | ±{res['se']:.2%} |")
        lines.append("")
    lines += ["Attenzione: i prezzi del file sono del mercato internazionale di Betfair. Il mercato italiano ha "
              "liquidità separata e spread più larghi: la prova vera è il registratore di Bet_bot sui prezzi .it."]
    return "\n".join(lines) + "\n"
