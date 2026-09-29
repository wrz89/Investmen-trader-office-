"""Bankroll paper: capitale iniziale, profitti accumulati, puntate aperte,
base di calcolo per il reinvestimento (compounding)."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .store import Store, now_iso

TZ = ZoneInfo("Europe/Rome")


def today() -> str:
    from . import clock
    return datetime.fromtimestamp(clock.now(), TZ).strftime("%Y-%m-%d")


class Bankroll:
    def __init__(self, store: Store, initial_capital: float):
        self.store = store
        untouched = not store.query("SELECT 1 FROM bets WHERE mode!='shadow' LIMIT 1")
        changed = store.get("initial_capital") not in (None, float(initial_capital))
        if store.get("cash") is None or (changed and untouched):
            store.set("cash", float(initial_capital))
            store.set("initial_capital", float(initial_capital))
            store.set("peak_bankroll", float(initial_capital))

    @property
    def cash(self) -> float:
        return float(self.store.get("cash"))

    @cash.setter
    def cash(self, value: float) -> None:
        self.store.set("cash", round(float(value), 6))

    @property
    def initial_capital(self) -> float:
        return float(self.store.get("initial_capital"))

    def open_bets(self) -> list[dict]:
        """Puntate aperte che impegnano il bankroll (le ombre no)."""
        return self.store.query("SELECT * FROM bets WHERE status='OPEN' AND mode!='shadow' ORDER BY id")

    def open_shadow_trades(self) -> list[dict]:
        return self.store.query("SELECT * FROM bets WHERE status='OPEN' AND mode='shadow' ORDER BY id")

    def open_stakes(self) -> float:
        return sum(b["stake"] for b in self.open_bets())

    @property
    def total(self) -> float:
        """Bankroll = liquidità + puntate in gioco (valutate al costo)."""
        return self.cash + self.open_stakes()

    @property
    def profits(self) -> float:
        return self.total - self.initial_capital

    def stake_base(self, reinvest_fraction: float, floor_pct: float) -> float:
        """Base su cui si calcola la puntata (compounding).
        reinvest_fraction=1 → bankroll intero; 0 → solo capitale iniziale.
        Sotto il floor la base è il bankroll stesso (non si insegue la perdita)."""
        total = self.total
        if total < self.initial_capital * floor_pct:
            return total
        return min(total, self.initial_capital + max(0.0, self.profits) * reinvest_fraction)

    def day_start(self, value_now: float) -> float:
        key = f"day_start:{today()}"
        v = self.store.get(key)
        if v is None:
            self.store.set(key, value_now)
            return value_now
        return float(v)

    def update_peak(self, value_now: float) -> float:
        peak = max(float(self.store.get("peak_bankroll", value_now)), value_now)
        self.store.set("peak_bankroll", peak)
        return peak

    def mark(self) -> dict:
        row = {"ts": now_iso(), "bankroll": self.total, "cash": self.cash,
               "open_stakes": self.open_stakes(), "profits": self.profits}
        self.store.execute("INSERT INTO bankroll(ts, bankroll, cash, open_stakes, profits) VALUES(?,?,?,?,?)",
                           tuple(row.values()))
        return row
