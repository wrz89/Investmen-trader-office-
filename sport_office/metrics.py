"""Metriche di performance: ROI, win rate, drawdown, CLV, serie."""
from __future__ import annotations

import math


def max_drawdown(series: list[float]) -> float:
    peak, worst = -math.inf, 0.0
    for v in series:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, 1 - v / peak)
    return worst


def summarize(bets: list[dict], initial_capital: float | None = None) -> dict:
    """bets: dizionari con stake, pnl, status (WON/LOST/VOID/CASHOUT/OPEN), clv, odds."""
    settled = [b for b in bets if b.get("status") not in (None, "OPEN")]
    decided = [b for b in settled if b["status"] in ("WON", "LOST", "CASHOUT", "HEDGED") and (b.get("pnl") or 0) != 0]
    staked = sum(b["stake"] for b in settled)
    pnl = sum(b.get("pnl") or 0.0 for b in settled)
    wins = sum(1 for b in decided if (b.get("pnl") or 0) > 0)
    losses = sum(1 for b in decided if (b.get("pnl") or 0) < 0)
    clvs = [b["clv"] for b in settled if b.get("clv") is not None]
    curve, running = [], initial_capital or 0.0
    for b in sorted(settled, key=lambda x: x.get("settled_ts") or ""):
        running += b.get("pnl") or 0.0
        curve.append(running)
    streak, worst_streak, cur = 0, 0, 0
    for b in sorted(decided, key=lambda x: x.get("settled_ts") or ""):
        cur = cur + 1 if (b.get("pnl") or 0) < 0 else 0
        worst_streak = max(worst_streak, cur)
    streak = cur
    # quota fissa fino alla fine, singole (niente arbitraggi): qui win rate e pareggio sono confrontabili
    fixed = [b for b in decided if b["status"] in ("WON", "LOST") and "+" not in (b.get("selection") or "")]
    avg_odds = sum(b["odds"] for b in fixed) / len(fixed) if fixed else 0.0
    single_wr = sum(1 for b in fixed if b["status"] == "WON") / len(fixed) if fixed else None
    return {
        "bets": len(settled), "open": sum(1 for b in bets if b.get("status") == "OPEN"),
        "wins": wins, "losses": losses,
        "win_rate": wins / len(decided) if decided else 0.0,
        "breakeven_win_rate": sum(1 / b["odds"] for b in fixed) / len(fixed) if fixed else 0.0,
        "single_win_rate": single_wr, "single_bets": len(fixed),
        "avg_odds": avg_odds, "staked": staked, "pnl": pnl,
        "roi": pnl / staked if staked else 0.0,
        "roi_on_capital": pnl / initial_capital if initial_capital else 0.0,
        "clv_avg": sum(clvs) / len(clvs) if clvs else None,
        "clv_positive_share": sum(1 for c in clvs if c > 0) / len(clvs) if clvs else None,
        "max_drawdown": max_drawdown(curve) if initial_capital else 0.0,
        "losing_streak": streak, "worst_losing_streak": worst_streak,
    }
