"""Fotografia dell'ufficio sportivo letta dalla dashboard."""
from __future__ import annotations

import json
import time

from .config import REPORTS_DIR, load_settings, load_yaml
from .store import Store


def _j(v):
    return json.loads(v) if v else None


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
        "events": events,
        "metrics": store.get("metrics"),
        "risk_state": store.get("risk_state"),
        "kill_switch": store.get("kill_switch"),
        "limits": load_yaml("risk_limits.yaml"),
        "strategies": store.get("strategies") or [],
        "strategy_params": load_yaml("strategies.yaml"),
        "open_bets": store.query("SELECT * FROM bets WHERE status='OPEN' ORDER BY id DESC"),
        "bets": store.query("SELECT * FROM bets WHERE status!='OPEN' ORDER BY settled_ts DESC, id DESC LIMIT 80"),
        "bankroll_curve": curve,
        "board": store.get("board"),
        "sentiment_flags": store.get("sentiment_flags") or {},
        "news": news,
        "shadow": shadow,
        "reports": [{"day": r["day"], "data": _j(r["data"])} for r in
                    store.query("SELECT day, data FROM daily_reports ORDER BY day DESC LIMIT 14")],
        "backtest": backtest,
        "promotion": load_yaml("promotion_criteria.yaml"),
        "live_gate": store.get("live_gate_text"),
        "settings_view": {"mode": settings.get("mode"), "feed": settings.get("feed", {}).get("provider"),
                          "execution": (settings.get("execution") or {}).get("provider")},
    }
