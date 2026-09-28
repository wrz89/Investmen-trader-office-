"""Analisi di portafoglio: quanto una strategia nuova diversifica rispetto a una esistente."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def monthly_returns(trades: list[dict], risk_per_trade: float, cap: float) -> pd.Series:
    """Rendimento mensile sul capitale, con la size reale del Risk Manager (contabilizzato all'uscita)."""
    if not trades:
        return pd.Series(dtype=float)
    r = pd.Series([min(risk_per_trade / max(t.get("stop_dist", 0.0), 1e-6), cap) * t["net"] for t in trades],
                  index=pd.to_datetime([t["exit_ts"] for t in trades], unit="ms"))
    return (1 + r).groupby(r.index.to_period("M")).prod() - 1


def sharpe_monthly(r: pd.Series) -> float:
    if len(r) < 3 or r.std(ddof=1) == 0:
        return 0.0
    return float(r.mean() / r.std(ddof=1) * math.sqrt(12))


def diversification(ref_trades: list[dict], new_trades: list[dict], risk_per_trade: float, cap: float) -> dict:
    a = monthly_returns(ref_trades, risk_per_trade, cap)
    b = monthly_returns(new_trades, risk_per_trade, cap)
    if a.empty or b.empty:
        return {"correlation": float("nan"), "sharpe_ref": 0.0, "sharpe_combined": 0.0, "months": 0}
    idx = pd.period_range(min(a.index.min(), b.index.min()), max(a.index.max(), b.index.max()), freq="M")
    a, b = a.reindex(idx, fill_value=0.0), b.reindex(idx, fill_value=0.0)
    return {
        "correlation": float(np.corrcoef(a, b)[0, 1]) if a.std() and b.std() else float("nan"),
        "sharpe_ref": sharpe_monthly(a),
        "sharpe_new": sharpe_monthly(b),
        "sharpe_combined": sharpe_monthly(a + b),     # le due strategie operano insieme sullo stesso capitale
        "months": len(idx),
    }
