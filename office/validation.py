"""Validazione statistica delle strategie (Quantitative Researcher).

Procedura:
  1. lo storico viene diviso in SVILUPPO (80%) e HOLD-OUT finale (20%);
  2. sullo sviluppo si esegue un walk-forward: per ogni finestra i parametri
     si scelgono SOLO sul training e si misurano sul test successivo;
  3. le metriche si calcolano solo sui trade fuori campione, al netto dei costi;
  4. stress sui costi, Monte Carlo del drawdown, stabilità dei parametri,
     Deflated Sharpe Ratio (correzione per i test multipli);
  5. solo se tutto è superato si guarda l'hold-out, una sola volta.
"""
from __future__ import annotations

import math
from collections import Counter
from statistics import NormalDist

import numpy as np
import pandas as pd

from .backtest import CostModel, prepare, signals, simulate, with_costs
from .strategies import param_combinations

YEAR_MS = 365.25 * 86_400_000
WARMUP_BARS = 250
_N = NormalDist()


# ── metriche ──────────────────────────────────────────────────
def profit_factor(r: np.ndarray) -> float:
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    if losses == 0:
        return 99.0 if wins > 0 else 0.0
    return float(min(wins / losses, 99.0))


def equity_curve(r: np.ndarray, alloc: float = 1.0) -> np.ndarray:
    return np.concatenate([[1.0], np.cumprod(1 + alloc * r)])


def max_drawdown(r: np.ndarray, alloc: float = 1.0) -> float:
    if len(r) == 0:
        return 0.0
    eq = equity_curve(r, alloc)
    return float((1 - eq / np.maximum.accumulate(eq)).max())


def mc_drawdown(r: np.ndarray, alloc: float, n: int = 2000, q: float = 0.95, seed: int = 7) -> float:
    """Drawdown al percentile q rimescolando l'ordine dei trade."""
    if len(r) < 2:
        return 0.0
    rng = np.random.default_rng(seed)
    dds = [max_drawdown(rng.permutation(r), alloc) for _ in range(n)]
    return float(np.quantile(dds, q))


def sharpe_per_trade(r: np.ndarray) -> float:
    if len(r) < 2 or r.std(ddof=1) == 0:
        return 0.0
    return float(r.mean() / r.std(ddof=1))


def deflated_sharpe(r: np.ndarray, trial_srs: list[float], n_trials: int) -> float:
    """Bailey & López de Prado (2014): probabilità che lo Sharpe osservato
    superi quello atteso dal migliore di `n_trials` tentativi casuali."""
    t = len(r)
    if t < 3:
        return 0.0
    sr = sharpe_per_trade(r)
    n = max(n_trials, 2)
    var = float(np.var(trial_srs, ddof=1)) if len(trial_srs) > 1 else sr ** 2
    gamma = 0.5772156649
    sr0 = math.sqrt(max(var, 1e-12)) * (
        (1 - gamma) * _N.inv_cdf(1 - 1 / n) + gamma * _N.inv_cdf(1 - 1 / (n * math.e))
    )
    s = pd.Series(r)
    skew, kurt = float(s.skew()), float(s.kurt()) + 3.0
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if not np.isfinite(denom) or denom <= 0:
        return 0.0
    return float(_N.cdf((sr - sr0) * math.sqrt(t - 1) / math.sqrt(denom)))


def summarize(trades: list[dict], years: float) -> dict:
    net = np.array([t["net"] for t in trades])
    gross = np.array([t["gross"] for t in trades])
    n = len(net)
    if n == 0:
        return {"trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "expectancy_net": 0.0,
                "avg_gross": 0.0, "avg_win": 0.0, "avg_loss": 0.0, "sharpe_annual": 0.0,
                "max_dd_full": 0.0, "total_net": 0.0}
    per_year = n / years if years > 0 else n
    return {
        "trades": n,
        "win_rate": float((net > 0).mean()),
        "profit_factor": profit_factor(net),
        "expectancy_net": float(net.mean()),
        "avg_gross": float(gross.mean()),
        "avg_win": float(net[net > 0].mean()) if (net > 0).any() else 0.0,
        "avg_loss": float(net[net <= 0].mean()) if (net <= 0).any() else 0.0,
        "sharpe_annual": sharpe_per_trade(net) * math.sqrt(per_year),
        "max_dd_full": max_drawdown(net, 1.0),
        "total_net": float(equity_curve(net)[-1] - 1),
    }


# ── walk-forward ──────────────────────────────────────────────
def _objective(trades: list[dict]) -> float:
    if len(trades) < 10:
        return -np.inf
    net = np.array([t["net"] for t in trades])
    return float(net.mean() * math.sqrt(len(net)))


def sized_returns(trades: list[dict], net: np.ndarray, risk_per_trade: float, cap: float) -> np.ndarray:
    """Rendimento sul CAPITALE di ogni trade, con la size che userebbe il Risk Manager:
    rischio per trade / distanza dello stop, mai oltre il tetto per asset."""
    alloc = np.array([min(risk_per_trade / max(t.get("stop_dist", 0.0), 1e-6), cap) for t in trades])
    return alloc * net


def validate(strategy, datasets: dict[str, pd.DataFrame], costs: CostModel, gates: dict,
             research: dict, alloc: float, n_trials_total: int, timeframe: str | None = None,
             risk_per_trade: float | None = None, extra_checks=None) -> dict:
    min_trades = (gates.get("min_oos_trades_by_timeframe") or {}).get(timeframe, gates["min_oos_trades"])
    combos = param_combinations(strategy.PARAM_GRID)
    symbols = list(datasets)

    # confini temporali comuni
    # gli asset con storico più corto entrano nelle finestre da quando esistono
    t_first = min(int(df["ts"].iloc[min(WARMUP_BARS, len(df) - 1)]) for df in datasets.values())
    t_last = min(int(df["ts"].iloc[-1]) for df in datasets.values())
    t_hold = int(t_first + (t_last - t_first) * (1 - research["holdout_fraction"]))
    windows_n = research["walk_forward_windows"]
    train_len = (t_hold - t_first) * research["train_fraction"]
    test_len = (t_hold - t_first - train_len) / windows_n

    def idx(df: pd.DataFrame, t: float) -> int:
        return int(np.searchsorted(df["ts"].to_numpy(), t))

    prepared = {}
    for ci, combo in enumerate(combos):
        sigs = signals(strategy, datasets, combo)
        for s in symbols:
            prepared[(ci, s)] = prepare(datasets[s], strategy, combo, sigs[s])

    def run(ci: int, t0: float, t1: float, cm: CostModel = costs) -> list[dict]:
        out = []
        for s in symbols:
            df = datasets[s]
            for tr in simulate(prepared[(ci, s)], idx(df, t0), idx(df, t1), cm):
                out.append({**tr, "symbol": s})
        return out

    # 1) walk-forward
    oos: list[dict] = []
    windows = []
    for w in range(windows_n):
        tr0 = t_first + w * test_len
        tr1 = tr0 + train_len
        te1 = tr1 + test_len
        scores = [_objective(run(ci, tr0, tr1)) for ci in range(len(combos))]
        best = int(np.argmax(scores))
        test_trades = run(best, tr1, te1)
        for t in test_trades:
            t["window"] = w
        oos.extend(test_trades)
        net_w = np.array([t["net"] for t in test_trades])
        windows.append({
            "window": w + 1,
            "train": [int(tr0), int(tr1)], "test": [int(tr1), int(te1)],
            "params": combos[best], "trades": len(test_trades),
            "net_return": float(equity_curve(net_w)[-1] - 1) if len(net_w) else 0.0,
        })

    oos.sort(key=lambda t: t["exit_ts"])
    oos_years = (t_hold - (t_first + train_len)) / YEAR_MS
    m = summarize(oos, oos_years)
    net = np.array([t["net"] for t in oos])

    # 2) robustezza
    stressed = with_costs(oos, costs.scaled(gates["cost_stress_multiplier"])) if oos else np.array([])
    dev_runs = [run(ci, t_first, t_hold) for ci in range(len(combos))]
    dev_means = [np.mean([t["net"] for t in tr]) if tr else -1.0 for tr in dev_runs]
    trial_srs = [sharpe_per_trade(np.array([t["net"] for t in tr])) for tr in dev_runs if len(tr) > 2]
    stability = float(np.mean([x > 0 for x in dev_means]))
    windows_with_trades = [w for w in windows if w["trades"] > 0]
    profitable_windows = (np.mean([w["net_return"] > 0 for w in windows_with_trades])
                          if windows_with_trades else 0.0)
    risk_based = gates.get("mc_sizing") == "risk_based" and risk_per_trade
    equity_r = sized_returns(oos, net, risk_per_trade, alloc) if risk_based else alloc * net
    mc_dd = mc_drawdown(equity_r, 1.0)
    dsr = deflated_sharpe(net, trial_srs, max(n_trials_total, len(combos)))

    def check(key, label, value, threshold, passed, fmt="num"):
        return {"key": key, "label": label, "value": value, "threshold": threshold,
                "passed": bool(passed), "fmt": fmt}

    checks = [
        check("oos_trades", "Trade fuori campione", m["trades"], min_trades,
              m["trades"] >= min_trades, "int"),
        check("profit_factor", "Profit factor netto", m["profit_factor"], gates["min_profit_factor"],
              m["profit_factor"] >= gates["min_profit_factor"]),
        check("sharpe", "Sharpe annuo netto", m["sharpe_annual"], gates["min_sharpe_annual"],
              m["sharpe_annual"] >= gates["min_sharpe_annual"]),
        check("gross_edge", "Guadagno lordo medio vs costo giro", m["avg_gross"],
              gates["min_gross_edge_vs_costs"] * costs.round_trip,
              m["avg_gross"] >= gates["min_gross_edge_vs_costs"] * costs.round_trip, "pct"),
        check("windows", "Finestre walk-forward in utile", float(profitable_windows),
              gates["min_profitable_windows"], profitable_windows >= gates["min_profitable_windows"], "pct"),
        check("cost_stress", f"Utile con costi × {gates['cost_stress_multiplier']}",
              float(stressed.mean()) if len(stressed) else 0.0, 0.0,
              len(stressed) > 0 and stressed.mean() > 0, "pct"),
        check("mc_dd", "Drawdown Monte Carlo 95°" + (" (size reale)" if risk_based else ""), mc_dd, gates["max_mc_drawdown_95"],
              len(net) > 0 and mc_dd <= gates["max_mc_drawdown_95"], "pct"),
        check("stability", "Stabilità parametri", stability, gates["min_param_stability"],
              stability >= gates["min_param_stability"], "pct"),
        check("dsr", "Deflated Sharpe (test multipli)", dsr, gates["min_deflated_sharpe"],
              dsr >= gates["min_deflated_sharpe"], "pct"),
    ]
    by_symbol = {s: summarize([t for t in oos if t["symbol"] == s], oos_years) for s in symbols}
    if gates.get("require_every_symbol_profitable"):
        worst = min(by_symbol, key=lambda s: (by_symbol[s]["trades"] > 0, by_symbol[s]["profit_factor"]))
        wpf = by_symbol[worst]["profit_factor"] if by_symbol[worst]["trades"] else 0.0
        checks.append(check("every_symbol", f"Ogni asset in utile (peggiore: {worst})", wpf, 1.0,
                            all(v["trades"] > 0 and v["profit_factor"] >= 1.0 for v in by_symbol.values())))
    if extra_checks is not None:
        checks.extend(extra_checks(oos))
    wf_passed = all(c["passed"] for c in checks)

    # 3) hold-out: si consuma solo se il walk-forward è superato
    chosen = Counter(tuple(sorted(w["params"].items())) for w in windows).most_common(1)[0][0]
    chosen_params = dict(chosen)
    holdout = {"evaluated": False, "note": "Non eseguito: cancello walk-forward non superato. Hold-out preservato."}
    if wf_passed:
        ci = combos.index(chosen_params)
        ho_trades = run(ci, t_hold, t_last + 1)
        ho = summarize(ho_trades, (t_last - t_hold) / YEAR_MS)
        holdout = {"evaluated": True, **ho, "note": "Hold-out consumato: non riutilizzabile per questa versione."}
        checks.append(check("holdout_pf", "Hold-out: profit factor", ho["profit_factor"],
                            gates["holdout_min_profit_factor"],
                            ho["trades"] > 0 and ho["profit_factor"] >= gates["holdout_min_profit_factor"]))

    passed = all(c["passed"] for c in checks)
    curve = equity_curve(equity_r, 1.0)
    step = max(1, len(curve) // 120)
    return {
        "strategy_id": strategy.STRATEGY_ID,
        "verdict": "PASSED" if passed else "REJECTED",
        "checks": checks,
        "metrics": m,
        "chosen_params": chosen_params,
        "windows": windows,
        "holdout": holdout,
        "n_combos": len(combos),
        "n_trials_total": max(n_trials_total, len(combos)),
        "period": {"start": t_first, "holdout_start": t_hold, "end": t_last},
        "costs": {"per_side": costs.per_side, "round_trip": costs.round_trip},
        "alloc": alloc,
        "sizing": "risk_based" if risk_based else "fixed",
        "avg_alloc": float(np.mean(equity_r[net != 0] / net[net != 0])) if len(net) else 0.0,
        "equity_dd_history": max_drawdown(equity_r, 1.0),
        "equity_oos": [round(float(x), 5) for x in curve[::step]],
        "exit_reasons": dict(Counter(t["reason"] for t in oos)),
        "by_symbol": by_symbol,
        "universe": symbols,
    }


def run_trades(strategy, datasets: dict, params: dict, t0: float, t1: float, costs: CostModel) -> list[dict]:
    """Trade di una combinazione di parametri su un intervallo di tempo (tutti gli asset)."""
    sigs = signals(strategy, datasets, params)
    out = []
    for s, df in datasets.items():
        ts = df["ts"].to_numpy()
        p = prepare(df, strategy, params, sigs[s])
        out += [{**t, "symbol": s} for t in simulate(p, int(np.searchsorted(ts, t0)), int(np.searchsorted(ts, t1)), costs)]
    return sorted(out, key=lambda t: t["exit_ts"])


def reconstruct_oos(strategy, validation: dict, datasets: dict, costs: CostModel) -> list[dict]:
    """Ricostruisce i trade fuori campione di una validazione salvata (finestre e parametri scelti)."""
    trades = []
    for w in validation["windows"]:
        trades += run_trades(strategy, datasets, w["params"], w["test"][0], w["test"][1], costs)
    return sorted(trades, key=lambda t: t["exit_ts"])
