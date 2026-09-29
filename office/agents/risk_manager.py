"""AGENTE 5 — RISK MANAGER. Potere di VETO ASSOLUTO.

Nessun ordine arriva all'Execution Agent senza un APPROVE esplicito.
Basta UNA condizione violata per il BLOCK. I limiti sono letti da
config/risk_limits.yaml e "sigillati" all'avvio: se il file cambia mentre
l'ufficio è acceso, tutto viene bloccato.
"""
from __future__ import annotations

import time

from ..config import CONFIG_DIR, file_sha256, load_yaml
from ..store import now_iso
from .base import Agent

LIMITS_FILE = CONFIG_DIR / "risk_limits.yaml"


class RiskManager(Agent):
    key = "risk_manager"
    name = "Risk Manager"
    role = "Veto assoluto su ogni operazione"

    def __init__(self, office):
        super().__init__(office)
        self.limits = load_yaml("risk_limits.yaml")
        self.seal = file_sha256(LIMITS_FILE)

    # ── stato del portafoglio ────────────────────────────────
    def portfolio_state(self, account, prices: dict) -> dict:
        equity = account.equity(prices)
        day_start = account.day_start_equity(equity)
        peak = account.update_peak(equity)
        exposure = account.exposure(prices)
        daily_pnl = equity / day_start - 1
        drawdown = 1 - equity / peak
        kill = self.store.get("kill_switch")
        if drawdown >= self.limits["max_drawdown"] and not kill:
            kill = f"drawdown {drawdown:.1%} ≥ limite {self.limits['max_drawdown']:.0%}"
            self.store.set("kill_switch", kill)
            self.say(f"KILL SWITCH ATTIVATO: {kill}. Reset solo manuale.", "alert", "kill_switch",
                     level="CRITICAL")
        return {
            "equity": equity, "cash": account.cash, "exposure": exposure,
            "total_exposure": sum(exposure.values()),
            "daily_pnl": daily_pnl, "drawdown": drawdown, "peak": peak,
            "kill_switch": kill, "open_positions": len(account.open_positions()),
        }

    # ── valutazione di un'opportunità ────────────────────────
    def evaluate(self, opp: dict, snapshot: dict, account, validation: dict | None) -> dict:
        L = self.limits
        sym = opp["symbol"]
        info = snapshot["symbols"][sym]
        prices = {s: v["bid"] for s, v in snapshot["symbols"].items() if v.get("bid")}
        pf = self.portfolio_state(account, prices)
        equity = pf["equity"]
        costs = self.settings["costs"]
        checks: list[dict] = []

        def chk(label: str, ok: bool, detail: str) -> None:
            checks.append({"label": label, "ok": bool(ok), "detail": detail})

        sealed = file_sha256(LIMITS_FILE) == self.seal
        chk("Limiti di rischio integri", sealed,
            "file invariato" if sealed else "risk_limits.yaml modificato durante l'esecuzione")
        chk("Kill switch non attivo", not pf["kill_switch"], pf["kill_switch"] or "ok")
        mode = self.settings["mode"]
        chk("Modalità consentita", mode == "paper", f"modalità {mode}"
            + ("" if mode == "paper" else " — live non abilitato in questa versione"))
        chk("Strategia autorizzata", opp["strategy_status"] == "PAPER",
            f"stato {opp['strategy_status']}"
            + ("" if opp["strategy_status"] == "PAPER" else " — solo le strategie validate possono operare"))
        chk("Validazione statistica superata", bool(validation and validation["verdict"] == "PASSED"),
            (validation or {}).get("verdict", "mai validata"))
        validated_on = list((validation or {}).get("by_symbol", {}))
        chk("Asset validato per la strategia", sym in validated_on,
            "ok" if sym in validated_on else f"validata solo su {', '.join(validated_on) or 'nessun asset'}")
        chk("Dati di mercato affidabili", info.get("ok", False) and not info.get("anomalies"),
            "; ".join(info.get("anomalies", [])) or "nessuna anomalia")
        base = sym.split("/")[0]
        news = {k: v for k, v in (self.store.get("news_blocks") or {}).items()
                if k in (base, "ALL") and v.get("until", 0) > time.time()}
        chk("Nessun allarme notizie (Nora)", not news,
            "nessun allarme" if not news else f"«{next(iter(news.values()))['reason']}»")
        err = snapshot["health"].get("error_rate", 0)
        chk("API stabile", err <= L["max_api_error_rate"], f"errori {err:.0%}")
        chk("Dati freschi", info.get("data_age_s", 1e9) <= L["max_data_age_seconds"],
            f"età dati {info.get('data_age_s', 0):.0f}s")
        chk("Spread accettabile", info.get("spread_bps", 1e9) <= L["max_spread_bps"],
            f"{info.get('spread_bps', 0):.1f} bp (max {L['max_spread_bps']})")
        chk("Perdita giornaliera entro limite", pf["daily_pnl"] > -L["max_daily_loss"],
            f"{pf['daily_pnl']:+.2%} (limite -{L['max_daily_loss']:.0%})")
        chk("Drawdown entro limite", pf["drawdown"] < L["max_drawdown"],
            f"{pf['drawdown']:.2%} (limite {L['max_drawdown']:.0%})")
        chk("Numero posizioni", pf["open_positions"] < L["max_open_positions"],
            f"{pf['open_positions']} aperte (max {L['max_open_positions']})")
        dup = any(p["symbol"] == sym and p["strategy_id"] == opp["strategy_id"]
                  for p in account.open_positions())
        chk("Nessuna posizione duplicata", not dup,
            "già in posizione: vietato aggiungere" if dup else "ok")
        long_spot = opp["direction"] == "LONG" or L["short_allowed"]
        chk("Niente leva / niente short", long_spot,
            "solo acquisti spot pagati con liquidità disponibile" if long_spot else "short non autorizzato")

        # ── dimensionamento ─────────────────────────────────
        price = opp["price"]
        stop_dist = max(opp["risk_pct"], 1e-6)
        if opp.get("sizing") == "allocation":
            # strategie lente: size fissa al tetto per asset (come nella validazione)
            risk_notional = L["max_exposure_per_asset"] * equity
        else:
            risk_notional = L["risk_per_trade"] * equity / stop_dist
        correlated = sum(v for s, v in pf["exposure"].items()
                         if s == sym or self._corr(snapshot, s, sym) > L["correlation_threshold"])
        cap_asset = L["max_exposure_per_asset"] * equity - correlated
        cap_total = L["max_total_exposure"] * equity - pf["total_exposure"]
        cap_cash = pf["cash"] / (1 + costs["taker_fee"]) * 0.99
        cap_book = L["max_book_participation"] * info.get("depth_ask", 0)
        caps = {("allocazione" if opp.get("sizing") == "allocation" else "rischio"): risk_notional, "asset/correlazione": cap_asset,
                "esposizione totale": cap_total, "liquidità conto": cap_cash, "order book": cap_book}
        binding = min(caps, key=caps.get)
        notional = max(0.0, caps[binding])
        qty = self.office.market.amount_to_precision(sym, notional / price) if notional > 0 else 0.0
        notional = qty * price
        try:
            minimums = self.office.market.market_info(sym)
        except Exception:
            minimums = {"min_cost": None, "min_amount": None}
        min_cost = minimums.get("min_cost") or 5.0
        min_amount = minimums.get("min_amount") or 0.0
        size_ok = notional >= min_cost and qty >= min_amount and qty > 0
        chk("Size sopra il minimo exchange", size_ok,
            f"{notional:.2f} {self.settings['capital']['currency']} (vincolo: {binding}; minimo {min_cost})")

        # ── vantaggio netto atteso vs costi ─────────────────
        if opp["net_pct"] is None:
            chk("Profitto netto atteso > costi", False, "nessuna stima statistica disponibile")
            net_eur = cost_eur = None
        else:
            cost_eur = notional * (opp["fees_pct"] + opp["slippage_pct"])
            net_eur = notional * opp["net_pct"]
            ok = net_eur >= L["min_net_edge_vs_costs"] * cost_eur
            chk("Profitto netto atteso > costi", ok,
                f"netto {net_eur:+.3f} vs costi {cost_eur:.3f} (richiesto ≥ {L['min_net_edge_vs_costs']}×)")

        failed = [c for c in checks if not c["ok"]]
        decision = {
            "approved": not failed,
            "checks": checks,
            "reasons": [f"{c['label']}: {c['detail']}" for c in failed],
            "qty": qty, "notional": notional, "stop": opp["stop"],
            "binding_cap": binding, "net_eur": net_eur, "cost_eur": cost_eur,
            "equity": equity,
        }
        self.store.execute(
            "INSERT INTO opportunities(ts, cycle_id, strategy_id, symbol, data, decision, reasons) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (now_iso(), self.office.cycle_id, opp["strategy_id"], sym, _json(opp),
             "APPROVE" if not failed else "BLOCK", _json(decision["reasons"])),
        )
        if failed:
            self.say(f"BLOCK {sym} ({opp['strategy_id']}): {failed[0]['label'].lower()} — "
                     f"{failed[0]['detail']}" + (f" (+{len(failed) - 1} altri motivi)" if len(failed) > 1 else ""),
                     "blocked", "veto", payload={"opportunity": opp["id"], "checks": checks,
                                                 "strategy_status": opp["strategy_status"]})
        else:
            self.say(f"APPROVE {sym} {qty} @ {price:,.2f} — rischio {L['risk_per_trade']:.1%} del capitale, "
                     f"stop {opp['stop']:,.2f}", "ok", "approve",
                     payload={"opportunity": opp["id"], "checks": checks})
        return decision

    def check_accumulation(self, q: dict, snapshot: dict, base: str = "BTC") -> tuple[bool, list[str]]:
        """Controlli sull'acquisto mensile del piano di accumulo. Può solo rimandarlo, mai ingrandirlo."""
        L, reasons = self.limits, []
        if file_sha256(LIMITS_FILE) != self.seal:
            reasons.append("limiti di rischio modificati durante l'esecuzione")
        if self.settings["mode"] != "paper":
            reasons.append("modalità live non abilitata in questa versione")
        if not q.get("direct") and not (q.get("usdc_ask") and q.get("usdc_eur")):
            reasons.append("prezzi non disponibili")
        if q.get("spread_bps") is not None and q["spread_bps"] > L["max_spread_bps"]:
            reasons.append(f"spread {q['spread_bps']:.1f} bp oltre il limite")
        if q.get("age_s") is not None and q["age_s"] > L["max_data_age_seconds"]:
            reasons.append(f"prezzo vecchio di {q['age_s']:.0f} s")
        err = snapshot["health"].get("error_rate", 0)
        if err > L["max_api_error_rate"]:
            reasons.append(f"API instabile (errori {err:.0%})")
        ref = snapshot["symbols"].get(f"{base}/USDC") or {}
        if ref.get("anomalies"):
            reasons.append(f"anomalia sui dati {base}: " + "; ".join(ref["anomalies"]))
        news = {k: v for k, v in (self.store.get("news_blocks") or {}).items()
                if k in (base, "ALL") and v.get("until", 0) > time.time()}
        if news:
            reasons.append(f"allarme notizie di Nora: «{next(iter(news.values()))['reason']}»")
        return not reasons, reasons

    @staticmethod
    def _corr(snapshot: dict, s1: str, s2: str) -> float:
        c = snapshot["correlations"]
        return c.get(f"{s1}|{s2}") or c.get(f"{s2}|{s1}") or 0.0


def _json(obj) -> str:
    import json
    return json.dumps(obj, default=str)
