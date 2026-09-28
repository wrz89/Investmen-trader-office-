"""STRATEGY_04 — Rotazione sul più forte (v1). Strategia DIVERSIFICANTE.

Regole fissate il 28/09/2026 PRIMA di qualsiasi test:
  • ogni lunedì (candela 4h delle 00:00 UTC) si classificano BTC, ETH, XRP, SOL
    per rendimento sulle ultime `lookback` candele;
  • si tiene SOLO il migliore, e solo se è sopra la sua media esponenziale `trend`;
  • se nessuno è in trend si resta in USDC; tra un lunedì e l'altro non si fa nulla
    (tranne lo stop di emergenza a 4 ATR).
Perché dovrebbe diversificare rispetto alla v5: decide una volta a settimana,
guarda la forza RELATIVA tra asset e non gli incroci di medie, e tiene un solo asset.
Massimo 2 tentativi (v1, v2). Deve superare anche il criterio di diversificazione.
"""
import pandas as pd

from office.indicators import ema

STRATEGY_ID = "STRATEGY_04_v1"
NAME = "Rotazione sul più forte"
FAMILY = "rotation"
DESCRIPTION = "Ogni lunedì tiene solo l'asset più forte dei 4 se in trend, altrimenti USDC"
TIMEFRAME = "4h"
DIVERSIFIER_OF = "STRATEGY_01_v5"
PARAM_GRID = {
    "lookback": [84, 168],       # 2 o 4 settimane di candele 4h
    "trend": [100, 200],
    "stop_atr": [4.0],
}


def generate_multi(datasets: dict, p: dict) -> dict:
    closes = pd.DataFrame({s: df.set_index("ts")["close"] for s, df in datasets.items()}).sort_index()
    closes = closes.ffill(limit=2)
    mom = closes / closes.shift(p["lookback"]) - 1
    trend = closes.apply(lambda c: ema(c.dropna(), p["trend"]).reindex(c.index))
    score = mom.where(closes > trend)
    has_best = score.notna().any(axis=1)
    best = score.fillna(-1e9).idxmax(axis=1).where(has_best)
    when = pd.to_datetime(closes.index, unit="ms", utc=True)
    rebalance = pd.Series((when.weekday == 0) & (when.hour == 0), index=closes.index)
    out = {}
    for s, df in datasets.items():
        target_is_s = (best == s)
        entry = (rebalance & target_is_s).reindex(df["ts"]).fillna(False).to_numpy()
        exit_ = (rebalance & ~target_is_s).reindex(df["ts"]).fillna(False).to_numpy()
        out[s] = pd.DataFrame({"entry": entry.astype(bool), "exit": exit_.astype(bool)}, index=df.index)
    return out
