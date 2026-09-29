"""Costruisce la "fotografia" dell'ufficio letta dalla dashboard."""
from __future__ import annotations

import json
import time

from . import registry
from .config import REGISTRY_DIR, load_settings, load_yaml
from .backtest import CostModel
from .shadow import ShadowBook
from .strategies import by_id
from .store import Store


def _j(value):
    return json.loads(value) if value else None


def _accumulation(store: Store) -> dict | None:
    try:
        from .accumulation import Accumulation
        return Accumulation(type("O", (), {"store": store})()).summary()
    except Exception:
        return None


def _funding(store: Store) -> dict | None:
    try:
        from .funding_watch import FundingWatch
        return FundingWatch(type("O", (), {"store": store})()).summary()
    except Exception:
        return None


def build_state(store: Store) -> dict:
    agents = store.query("SELECT * FROM agent_status")
    for a in agents:
        a["stats"] = _j(a["stats"])
    events = store.query("SELECT * FROM events ORDER BY id DESC LIMIT 200")
    for e in events:
        e["payload"] = _j(e["payload"])

    statuses = {r["strategy_id"]: r for r in store.query("SELECT * FROM strategy_status")}
    per_side = CostModel.from_settings(load_settings()).per_side
    strategies = []
    for path in sorted(REGISTRY_DIR.glob("*.json")):
        if path.name.endswith(".validation.json") or registry.is_audit_file(path):
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        v = registry.load_validation(d["strategy_id"])
        audit = None
        if v and registry.needs_cost_audit(v, per_side):
            audit = registry.load_cost_audit(d["strategy_id"], per_side) or {"verdict": "PENDING"}
            audit = {k: audit.get(k) for k in ("verdict", "metrics", "checks", "costs", "audited_at")}
        st = statuses.get(d["strategy_id"], {})
        strategies.append({
            **d,
            "status": st.get("status", "RESEARCH"),
            "status_reason": st.get("reason"),
            "cost_audit": audit,
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
            try:
                sizing = getattr(by_id(st["strategy_id"]), "SIZING", "risk")
            except KeyError:
                sizing = "risk"
            st["shadow"] = shadow.summary(st["strategy_id"], limits["risk_per_trade"],
                                          limits["max_exposure_per_asset"], observe[st["strategy_id"]].get("since"),
                                          sizing)
            st["shadow"]["sizing"] = sizing
            st["shadow"]["review"] = observe[st["strategy_id"]].get("review") or lifecycle.get("observe_review") or {}

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
        "accumulation": _accumulation(store),
        "live_balance": store.get("live_balance"),
        "funding": _funding(store),
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
