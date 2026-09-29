"""Costruisce la "fotografia" dell'ufficio letta dalla dashboard."""
from __future__ import annotations

import json
import time

from . import registry
from .config import REGISTRY_DIR, load_settings, load_yaml
from .shadow import ShadowBook
from .store import Store


def _j(value):
    return json.loads(value) if value else None


def build_state(store: Store) -> dict:
    agents = store.query("SELECT * FROM agent_status")
    for a in agents:
        a["stats"] = _j(a["stats"])
    events = store.query("SELECT * FROM events ORDER BY id DESC LIMIT 200")
    for e in events:
        e["payload"] = _j(e["payload"])

    statuses = {r["strategy_id"]: r for r in store.query("SELECT * FROM strategy_status")}
    strategies = []
    for path in sorted(REGISTRY_DIR.glob("*.json")):
        if path.name.endswith(".validation.json"):
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        v = registry.load_validation(d["strategy_id"])
        st = statuses.get(d["strategy_id"], {})
        strategies.append({
            **d,
            "status": st.get("status", "RESEARCH"),
            "status_reason": st.get("reason"),
            "validation": None if v is None else {
                k: v.get(k) for k in ("verdict", "checks", "metrics", "chosen_params", "windows",
                                      "holdout", "equity_oos", "data_source", "timeframe",
                                      "validated_at", "period", "costs", "exit_reasons", "by_symbol",
                                      "n_trials_total", "sizing", "avg_alloc", "alloc")
            },
        })

    lifecycle = load_yaml("strategy_lifecycle.yaml")
    observe = lifecycle.get("observe") or {}
    limits = load_yaml("risk_limits.yaml")
    shadow = ShadowBook(store, load_settings()["costs"])
    for st in strategies:
        if st["strategy_id"] in observe:
            st["shadow"] = shadow.summary(st["strategy_id"], limits["risk_per_trade"],
                                          limits["max_exposure_per_asset"], observe[st["strategy_id"]].get("since"))
            st["shadow"]["review"] = lifecycle.get("observe_review") or {}

    equity = store.query("SELECT * FROM equity ORDER BY ts")
    step = max(1, len(equity) // 300)
    opps = store.query("SELECT * FROM opportunities ORDER BY id DESC LIMIT 40")
    for o in opps:
        o["data"], o["reasons"] = _j(o["data"]), _j(o["reasons"])
    reports = store.query("SELECT day, data FROM daily_reports ORDER BY day DESC LIMIT 14")
    for r in reports:
        r["data"] = _j(r["data"])

    try:
        news = store.query("SELECT ts, published, source, title, link, assets, severity, words FROM news "
                           "ORDER BY COALESCE(published, ts) DESC LIMIT 40")
    except Exception:
        news = []
    for n in news:
        n["assets"], n["words"] = _j(n["assets"]) or [], _j(n["words"]) or []

    return {
        "generated": time.time(),
        "news": news,
        "news_blocks": {k: v for k, v in (store.get("news_blocks") or {}).items() if v.get("until", 0) > time.time()},
        "fear_greed": store.get("fear_greed"),
        "plan": load_yaml("investment_plan.yaml"),
        "meta": store.get("office_meta", {}),
        "cycle": store.get("cycle", {}),
        "agents": agents,
        "events": events,
        "strategies": strategies,
        "positions": store.query("SELECT * FROM positions WHERE is_open=1"),
        "trades": store.query("SELECT * FROM trades ORDER BY id DESC LIMIT 50"),
        "equity": equity[::step] + (equity[-1:] if equity and (len(equity) - 1) % step else []),
        "risk_state": store.get("risk_state"),
        "kill_switch": store.get("kill_switch"),
        "limits": load_yaml("risk_limits.yaml"),
        "gates": load_yaml("quant_gates.yaml"),
        "opportunities": opps,
        "reports": reports,
    }
