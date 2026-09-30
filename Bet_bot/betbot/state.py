"""Fotografia dell'ufficio sportivo letta dalla dashboard."""
from __future__ import annotations

import json
import time

from .config import REPORTS_DIR, load_settings, load_yaml
from .store import Store


def _j(v):
    return json.loads(v) if v else None


# chi "possiede" le puntate (per le etichette sopra gli omini nell'ufficio 3D):
# Davide = calcio; Matteo = tennis, basket, football americano (e cavalli, che su Betfair.it non ci sono)
STRATEGY_AGENT = {"S04": "cavalli", "S08": "cavalli"}
OTHER_SPORTS = ("tennis", "basketball", "americanfootball", "baseball")


def strategy_agent(strategy_id: str, sport: str | None = None) -> str:
    if (sport or "").startswith(OTHER_SPORTS):
        return "cavalli"
    return next((a for pre, a in STRATEGY_AGENT.items() if (strategy_id or "").startswith(pre)), "analista")


def bet_agent(bet: dict) -> str:
    extra = _j(bet.get("extra")) if isinstance(bet.get("extra"), str) else (bet.get("extra") or {})
    return strategy_agent(bet.get("strategy_id"), (extra or {}).get("sport"))


def _perf(bets: list[dict]) -> dict:
    settled = [b for b in bets if b["status"] != "OPEN"]
    wins = sum(1 for b in settled if (b["pnl"] or 0) > 0)
    losses = sum(1 for b in settled if (b["pnl"] or 0) < 0)
    staked = sum(b["stake"] for b in settled)
    pnl = sum(b["pnl"] or 0 for b in settled)
    last = max(settled, key=lambda b: (b["settled_ts"] or "", b["id"]), default=None)
    return {"bets": len(settled), "open": sum(1 for b in bets if b["status"] == "OPEN"),
            "win_rate": wins / (wins + losses) if wins + losses else None, "roi": pnl / staked if staked else None,
            "pnl": pnl, "last": None if not last else {"status": last["status"], "pnl": last["pnl"], "label": last["label"]}}


def agent_stats(store: Store) -> dict:
    """Numeri da mostrare sopra ogni omino: win rate e ROI per chi punta, contatori per gli altri."""
    bets = store.query("SELECT id, strategy_id, status, stake, pnl, settled_ts, label, extra FROM bets WHERE mode!='shadow'")
    out = {"banco": _perf(bets)}
    for key in ("analista", "cavalli"):
        out[key] = _perf([b for b in bets if bet_agent(b) == key])
    rows = store.query("SELECT kind, COUNT(*) n FROM events WHERE kind IN ('approve', 'veto') GROUP BY kind")
    k = {r["kind"]: r["n"] for r in rows}
    out["risk"] = {"approvals": k.get("approve", 0), "vetoes": k.get("veto", 0)}
    m = store.get("metrics") or {}
    rs = store.get("risk_state") or {}
    out["tesoriere"] = {"bankroll": rs.get("bankroll"), "profits": rs.get("profits"),
                        "roi_on_capital": (rs.get("profits") or 0) / rs["initial"] if rs.get("initial") else None}
    out["direttore"] = {"bankroll": rs.get("bankroll"), "drawdown": rs.get("drawdown"), "kill_switch": store.get("kill_switch")}
    try:
        from .agents.coach import CoachBook
        c = CoachBook(store).summary()
        out["coach"] = {"lessons": c["lessons"], "rules": c["rules"], "clv": c["clv"]}
    except Exception:
        out["coach"] = {"lessons": 0, "rules": 0, "clv": None}
    out["auditor"] = {"bets": m.get("bets"), "roi": m.get("roi"), "clv_avg": m.get("clv_avg")}
    return out


def _recording() -> dict:
    try:
        from .feeds.recorder import recording_status
        st = load_settings()
        return {**recording_status(), "on": bool(st["feed"].get("record")) and st["feed"].get("provider") == "betfair",
                "provider": st["feed"].get("provider")}
    except Exception:
        return {}


def _coach(store: Store) -> dict | None:
    """La 'scuola degli errori' di Leo: autopsie, calibrazione, segmenti e regole apprese."""
    try:
        from .agents.coach import CoachBook
        s = load_settings()
        return CoachBook(store, int((s.get("coach") or {}).get("min_n", 30))).view()
    except Exception:
        return None


def build_state(store: Store) -> dict:
    agents = store.query("SELECT * FROM agent_status")
    for a in agents:
        a["stats"] = _j(a["stats"])
    events = store.query("SELECT * FROM events WHERE kind NOT IN ('scan', 'bankroll') ORDER BY id DESC LIMIT 250")
    for e in events:
        e["payload"] = _j(e["payload"])
    curve = store.query("SELECT ts, bankroll FROM bankroll ORDER BY ts")
    step = max(1, len(curve) // 400)
    curve = curve[::step] + (curve[-1:] if curve and (len(curve) - 1) % step else [])
    try:
        news = store.query("SELECT ts, published, publisher, team, title, link, severity FROM news "
                           "WHERE severity != 'none' ORDER BY COALESCE(published, ts) DESC LIMIT 30")
    except Exception:
        news = []
    shadow = store.query("SELECT strategy_id, COUNT(*) n, SUM(CASE WHEN status='WON' THEN 1 ELSE 0 END) won, "
                         "SUM(COALESCE(pnl,0)) pnl, SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) open "
                         "FROM shadow_bets GROUP BY strategy_id")
    backtest = None
    files = sorted(REPORTS_DIR.glob("backtest_*.md"), key=lambda p: p.stat().st_mtime) if REPORTS_DIR.exists() else []
    if files:
        backtest = {"name": files[-1].stem, "markdown": files[-1].read_text(encoding="utf-8")}
    try:
        settings = load_settings()
    except Exception:
        settings = {}
    return {
        "generated": time.time(),
        "meta": store.get("office_meta", {}),
        "cycle": store.get("cycle", {}),
        "agents": agents,
        "agent_stats": agent_stats(store),
        "events": events,
        "metrics": store.get("metrics"),
        "risk_state": store.get("risk_state"),
        "kill_switch": store.get("kill_switch"),
        "limits": load_yaml("risk_limits.yaml"),
        "strategies": store.get("strategies") or [],
        "strategy_params": load_yaml("strategies.yaml"),
        "open_bets": store.query("SELECT * FROM bets WHERE status='OPEN' AND mode!='shadow' ORDER BY id DESC"),
        "bets": store.query("SELECT * FROM bets WHERE status!='OPEN' AND mode!='shadow' ORDER BY settled_ts DESC, id DESC LIMIT 80"),
        "shadow_trades": store.query("SELECT * FROM bets WHERE mode='shadow' ORDER BY id DESC LIMIT 40"),
        "bankroll_curve": curve,
        "board": store.get("board"),
        "sentiment_flags": store.get("sentiment_flags") or {},
        "news": news,
        "shadow": shadow,
        "reports": [{"day": r["day"], "data": _j(r["data"])} for r in
                    store.query("SELECT day, data FROM daily_reports ORDER BY day DESC LIMIT 14")],
        "backtest": backtest,
        "coach": _coach(store),
        "esame": store.get("esame") or [],
        "registrazione": _recording(),
        "promotion": load_yaml("promotion_criteria.yaml"),
        "live_gate": store.get("live_gate_text"),
        "settings_view": {"mode": settings.get("mode"), "feed": settings.get("feed", {}).get("provider"),
                          "execution": (settings.get("execution") or {}).get("provider")},
    }
