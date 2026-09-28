"""Conto paper: liquidità, posizioni aperte, esposizione ed equity."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .store import Store

TZ = ZoneInfo("Europe/Rome")


def today() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


class PaperAccount:
    def __init__(self, store: Store, initial_capital: float):
        self.store = store
        if store.get("cash") is None:
            store.set("cash", float(initial_capital))
            store.set("initial_capital", float(initial_capital))
            store.set("peak_equity", float(initial_capital))

    @property
    def cash(self) -> float:
        return float(self.store.get("cash"))

    @cash.setter
    def cash(self, value: float) -> None:
        self.store.set("cash", float(value))

    @property
    def initial_capital(self) -> float:
        return float(self.store.get("initial_capital"))

    def open_positions(self) -> list[dict]:
        return self.store.query("SELECT * FROM positions WHERE is_open=1 ORDER BY id")

    def exposure(self, prices: dict[str, float]) -> dict[str, float]:
        out: dict[str, float] = {}
        for p in self.open_positions():
            px = prices.get(p["symbol"], p["entry_price"])
            out[p["symbol"]] = out.get(p["symbol"], 0.0) + p["qty"] * px
        return out

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(self.exposure(prices).values())

    # ── limiti giornalieri / drawdown ─────────────────────────
    def day_start_equity(self, equity_now: float) -> float:
        key = f"day_start_equity:{today()}"
        value = self.store.get(key)
        if value is None:
            self.store.set(key, equity_now)
            return equity_now
        return float(value)

    def update_peak(self, equity_now: float) -> float:
        peak = max(float(self.store.get("peak_equity", equity_now)), equity_now)
        self.store.set("peak_equity", peak)
        return peak
