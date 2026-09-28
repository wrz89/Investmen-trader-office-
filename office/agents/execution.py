"""AGENTE 6 — EXECUTION AGENT.

Esegue SOLO ordini approvati dal Risk Manager. In questa versione esiste
solo l'esecuzione PAPER: prezzi e order book reali, soldi finti.
Il riempimento è simulato in modo prudente: l'ordine "consuma" l'order
book livello per livello (prezzo medio peggiore del best) e paga in più
lo slippage stimato di configurazione.
"""
from __future__ import annotations

from ..store import now_iso
from .base import Agent


def walk_book(levels: list[tuple[float, float]], qty: float) -> tuple[float, float]:
    """Prezzo medio e quantità riempita consumando i livelli del book."""
    remaining, cost = qty, 0.0
    for price, size in levels:
        take = min(remaining, size)
        cost += take * price
        remaining -= take
        if remaining <= 1e-12:
            break
    filled = qty - max(remaining, 0.0)
    return (cost / filled if filled > 0 else 0.0), filled


class Execution(Agent):
    key = "execution"
    name = "Execution Agent"
    role = "Esegue solo ordini approvati e verifica i fill"

    def _costs(self):
        c = self.settings["costs"]
        return c["taker_fee"], c["slippage_bps"] / 10_000

    # ── ingresso ─────────────────────────────────────────────
    def buy(self, opp: dict, decision: dict, snapshot: dict, account) -> dict | None:
        if not decision.get("approved"):
            self.say("Ordine rifiutato: manca l'autorizzazione del Risk Manager.", "blocked", "exec_refused",
                     level="WARN")
            return None
        info = snapshot["symbols"][opp["symbol"]]
        fee_rate, slip = self._costs()
        qty = decision["qty"]

        # 7 verifiche pre-ordine
        est_px, _ = walk_book(info["asks"], qty)
        est_px *= 1 + slip
        pre = [
            ("prezzo", info["ask"] > 0, f"ask {info['ask']:,.2f}"),
            ("spread", info["spread_bps"] <= self.office.risk.limits["max_spread_bps"],
             f"{info['spread_bps']:.1f} bp"),
            ("liquidità", decision["notional"] <= info["depth_ask"], f"book ±0,5%: {info['depth_ask']:,.0f}"),
            ("commissione", True, f"{decision['notional'] * fee_rate:.4f}"),
            ("slippage stimato", True, f"{(est_px / info['ask'] - 1) * 10_000:.1f} bp"),
            ("profitto netto atteso", (decision["net_eur"] or 0) > 0, f"{decision['net_eur']:+.4f}"
             if decision["net_eur"] is not None else "n/d"),
            ("autorizzazione Risk Manager", decision["approved"], "APPROVE"),
        ]
        if not all(ok for _, ok, _ in pre):
            failed = next(name for name, ok, _ in pre if not ok)
            self.say(f"Ordine annullato in pre-verifica: {failed}", "blocked", "exec_refused",
                     payload={"pre_checks": pre}, level="WARN")
            return None

        self.status("working", f"Invio BUY {qty} {opp['symbol']} (paper)…")
        avg, filled = walk_book(info["asks"], qty)
        avg *= 1 + slip
        filled = min(filled, qty)                    # mai più della size approvata
        notional = filled * avg
        fee = notional * fee_rate
        if notional + fee > account.cash:
            self.say("Liquidità insufficiente al momento del fill: ordine annullato.", "blocked",
                     "exec_refused", level="WARN")
            return None
        account.cash = account.cash - notional - fee
        slippage_cost = (avg - opp["price"]) * filled
        self.store.execute(
            "INSERT INTO positions(strategy_id, symbol, qty, entry_price, expected_price, entry_ts, stop, "
            "entry_fee, entry_slippage, signal, entry_reason, is_open) VALUES(?,?,?,?,?,?,?,?,?,?,?,1)",
            (opp["strategy_id"], opp["symbol"], filled, avg, opp["price"], now_iso(), decision["stop"],
             fee, slippage_cost, opp["signal"],
             f"{opp['signal']}; prob. {opp['probability']:.0%}; netto atteso {opp['net_pct'] * 100:+.2f}%"),
        )
        fill = {"qty": filled, "avg": avg, "fee": fee, "slippage": slippage_cost, "notional": notional}
        self.say(f"FILL BUY {filled} {opp['symbol']} @ {avg:,.2f} · fee {fee:.4f} · slippage "
                 f"{(avg / opp['price'] - 1) * 10_000:.1f} bp", "ok", "fill", payload=fill)
        return fill

    # ── uscita ───────────────────────────────────────────────
    def sell(self, position: dict, snapshot: dict, account, reason: str,
             stop_price: float | None = None) -> dict:
        info = snapshot["symbols"][position["symbol"]]
        fee_rate, slip = self._costs()
        qty = position["qty"]
        expected = info["bid"]
        if stop_price is not None:
            # stop colpito in una candela già chiusa: si simula lo stop lato exchange
            avg = stop_price * (1 - slip)
            expected = stop_price
        else:
            avg, filled = walk_book(info["bids"], qty)
            if filled < qty:                          # book insufficiente: prudenza
                avg = avg * filled / qty + info["bid"] * 0.99 * (qty - filled) / qty
            avg *= 1 - slip
        proceeds = qty * avg
        fee = proceeds * fee_rate
        account.cash = account.cash + proceeds - fee
        self.store.execute("UPDATE positions SET is_open=0 WHERE id=?", (position["id"],))
        fill = {"qty": qty, "avg": avg, "fee": fee, "expected": expected,
                "slippage": (expected - avg) * qty}
        self.say(f"FILL SELL {qty} {position['symbol']} @ {avg:,.2f} ({reason})", "ok", "fill", payload=fill)
        return fill
