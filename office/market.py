"""Accesso ai dati di mercato via ccxt, con monitoraggio della salute dell'API."""
from __future__ import annotations

import time
from collections import deque

import ccxt
import pandas as pd

from .config import DATA_DIR

COLUMNS = ["ts", "open", "high", "low", "close", "volume"]


class DataError(Exception):
    """Dati non disponibili o non affidabili: l'ufficio non deve operare."""


class MarketData:
    def __init__(self, exchange_id: str, options: dict | None = None, timeout_ms: int = 10000):
        klass = getattr(ccxt, exchange_id)
        self.exchange_id = exchange_id
        self.ex = klass({
            "enableRateLimit": True,
            "timeout": timeout_ms,
            # rispetta proxy/certificati di sistema (utile su reti aziendali)
            "requests_trust_env": True,
            **(options or {}),
        })
        self._calls: deque = deque(maxlen=50)   # (ok, latency_ms)
        self._markets_loaded = False

    # ── infrastruttura ────────────────────────────────────────
    def _call(self, fn, *args, **kwargs):
        start = time.time()
        try:
            result = fn(*args, **kwargs)
        except ccxt.BaseError as exc:
            self._calls.append((False, (time.time() - start) * 1000))
            raise DataError(f"{self.exchange_id}: {type(exc).__name__}: {str(exc)[:160]}") from exc
        self._calls.append((True, (time.time() - start) * 1000))
        return result

    def health(self) -> dict:
        if not self._calls:
            return {"calls": 0, "error_rate": 0.0, "avg_latency_ms": None}
        errors = sum(1 for ok, _ in self._calls if not ok)
        lat = [ms for ok, ms in self._calls if ok]
        return {
            "calls": len(self._calls),
            "error_rate": errors / len(self._calls),
            "avg_latency_ms": round(sum(lat) / len(lat)) if lat else None,
        }

    def load_markets(self) -> dict:
        if not self._markets_loaded:
            self._call(self.ex.load_markets)
            self._markets_loaded = True
        return self.ex.markets

    def market_info(self, symbol: str) -> dict:
        markets = self.load_markets()
        if symbol not in markets:
            raise DataError(f"{symbol} non quotato su {self.exchange_id}")
        m = markets[symbol]
        return {
            "min_amount": (m.get("limits", {}).get("amount") or {}).get("min"),
            "min_cost": (m.get("limits", {}).get("cost") or {}).get("min"),
        }

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        try:
            self.load_markets()
            return float(self.ex.amount_to_precision(symbol, amount))
        except Exception:
            return float(f"{amount:.8f}")

    # ── dati live ─────────────────────────────────────────────
    def candles(self, symbol: str, timeframe: str, limit: int = 400) -> pd.DataFrame:
        """Solo candele CHIUSE: l'ultima candela in formazione viene scartata."""
        raw = self._call(self.ex.fetch_ohlcv, symbol, timeframe, limit=limit)
        df = to_frame(raw)
        tf_ms = self.ex.parse_timeframe(timeframe) * 1000
        now_ms = self.ex.milliseconds()
        return df[df["ts"] + tf_ms <= now_ms].reset_index(drop=True)

    def ticker(self, symbol: str) -> dict:
        return self._call(self.ex.fetch_ticker, symbol)

    def order_book(self, symbol: str, depth: int = 50) -> dict:
        return self._call(self.ex.fetch_order_book, symbol, depth)

    def funding_and_oi(self, perp_symbol: str) -> dict:
        out = {}
        try:
            fr = self.ex.fetch_funding_rate(perp_symbol)
            out["funding_rate"] = fr.get("fundingRate")
        except Exception:
            out["funding_rate"] = None
        try:
            oi = self.ex.fetch_open_interest(perp_symbol)
            out["open_interest"] = oi.get("openInterestValue") or oi.get("openInterestAmount")
        except Exception:
            out["open_interest"] = None
        return out

    # ── storico per la ricerca ────────────────────────────────
    def history(self, symbol: str, timeframe: str, days: int, progress=None) -> pd.DataFrame:
        """Scarica (e mette in cache su CSV) lo storico, in modo incrementale."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        cache = DATA_DIR / f"{self.exchange_id}_{symbol.replace('/', '-')}_{timeframe}.csv"
        tf_ms = self.ex.parse_timeframe(timeframe) * 1000
        now_ms = self.ex.milliseconds()
        start_ms = now_ms - days * 86_400_000

        df = pd.read_csv(cache) if cache.exists() else pd.DataFrame(columns=COLUMNS)
        if len(df) and df["ts"].min() > start_ms + tf_ms:
            df = pd.DataFrame(columns=COLUMNS)   # la cache non copre il periodo richiesto
        since = int(df["ts"].max()) + tf_ms if len(df) else start_ms

        chunks = [df] if len(df) else []
        while since < now_ms - tf_ms:
            raw = self._call(self.ex.fetch_ohlcv, symbol, timeframe, since=since, limit=1000)
            if not raw:
                if not chunks:                    # asset quotato dopo la data richiesta: vado avanti
                    since += 1000 * tf_ms
                    continue
                break
            chunk = to_frame(raw)
            chunks.append(chunk)
            last = int(chunk["ts"].max())
            if last < since:
                break
            since = last + tf_ms
            if progress:
                progress(symbol, last)

        full = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame(columns=COLUMNS)
        full = full.drop_duplicates("ts").sort_values("ts")
        full = full[full["ts"] + tf_ms <= now_ms].reset_index(drop=True)
        full.to_csv(cache, index=False)
        return full[full["ts"] >= start_ms].reset_index(drop=True)


def to_frame(raw: list) -> pd.DataFrame:
    df = pd.DataFrame(raw, columns=COLUMNS)
    df["ts"] = df["ts"].astype("int64")
    for c in COLUMNS[1:]:
        df[c] = df[c].astype(float)
    return df
