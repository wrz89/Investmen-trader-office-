"""AGENTE 2 — MARKET SCANNER.

Legge prezzo, volume, volatilità, spread, order book, liquidità, momentum,
correlazioni, funding e open interest; controlla la qualità dei dati e
trasforma i segnali delle strategie in opportunità quantificate AL NETTO
dei costi.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from ..indicators import atr
from ..market import DataError
from ..strategies import timeframe_of, universe_of
from .base import Agent


class MarketScanner(Agent):
    key = "market_scanner"
    name = "Market Scanner"
    role = "Monitora il mercato e quantifica le opportunità"

    # ── fotografia del mercato ────────────────────────────────
    def scan(self) -> dict:
        md = self.office.market
        tf = self.settings["timeframe"]
        tf_s = md.ex.parse_timeframe(tf)
        limits = self.office.risk.limits
        self.status("working", f"Scansiono {len(self.settings['universe'])} mercati su {md.exchange_id}…")

        symbols: dict[str, dict] = {}
        returns: dict[str, pd.Series] = {}
        for symbol in self.settings["universe"]:
            info: dict = {"symbol": symbol, "anomalies": [], "ok": False}
            try:
                df = md.candles(symbol, tf, limit=1000)
                tk = md.ticker(symbol)
                ob = md.order_book(symbol, 50)
            except DataError as exc:
                info["anomalies"].append(f"dati non disponibili: {exc}")
                self.log(f"{symbol}: dati non disponibili ({exc})", "ERROR", "error")
                symbols[symbol] = info
                continue

            now = time.time()
            bid = ob["bids"][0][0] if ob["bids"] else None
            ask = ob["asks"][0][0] if ob["asks"] else None
            info["candles"] = df
            if not bid or not ask or len(df) < 250:
                info["anomalies"].append("order book vuoto o storico insufficiente")
                symbols[symbol] = info
                continue

            mid = (bid + ask) / 2
            close = df["close"]
            rets = np.log(close).diff()
            returns[symbol] = pd.Series(rets.to_numpy(), index=df["ts"].to_numpy())
            a = float(atr(df, 14).iloc[-1])
            last_ts = int(df["ts"].iloc[-1])
            candle_age = now - (last_ts / 1000 + tf_s)
            tk_ts = (tk.get("timestamp") or now * 1000) / 1000
            expected = 200
            got = int(((df["ts"].iloc[-1] - df["ts"].iloc[-expected]) / (tf_s * 1000)) + 1)

            info.update({
                "ok": True,
                "bid": bid, "ask": ask, "mid": mid,
                "last": tk.get("last") or mid,
                "spread_bps": (ask - bid) / mid * 10_000,
                "depth_bid": sum(p * q for p, q, *_ in ob["bids"] if p >= mid * 0.995),
                "depth_ask": sum(p * q for p, q, *_ in ob["asks"] if p <= mid * 1.005),
                "asks": [(p, q) for p, q, *_ in ob["asks"]],
                "bids": [(p, q) for p, q, *_ in ob["bids"]],
                "volume_24h": tk.get("quoteVolume") or (tk.get("baseVolume") or 0) * mid,
                "atr": a, "atr_pct": a / mid,
                "momentum_24h": float(close.iloc[-1] / close.iloc[-25] - 1),
                "last_candle_ts": last_ts,
                "candle_age_s": candle_age,
                "data_age_s": max(0.0, now - tk_ts),
            })

            # ── controlli di qualità: in caso di dubbio, NON operare ──
            if candle_age > 2 * tf_s:
                info["anomalies"].append(f"candele non aggiornate ({candle_age / 60:.0f} min)")
            if got - expected > 2:
                info["anomalies"].append(f"buchi nello storico ({got - expected} candele mancanti)")
            sigma = rets.iloc[-200:].std()
            if sigma and abs(rets.iloc[-1]) > 8 * sigma:
                info["anomalies"].append(f"movimento anomalo ({rets.iloc[-1] * 100:+.1f}% in una candela)")
            if abs(info["last"] / mid - 1) > 0.01:
                info["anomalies"].append("prezzo ticker incoerente con l'order book")
            if info["data_age_s"] > limits["max_data_age_seconds"]:
                info["anomalies"].append(f"ticker vecchio di {info['data_age_s']:.0f}s")
            if info["anomalies"]:
                info["ok"] = False
                for an in info["anomalies"]:
                    self.log(f"{symbol}: ANOMALIA — {an}", "WARN", "anomaly")
            symbols[symbol] = info

        # correlazioni sugli ultimi 7 giorni
        correlations = {}
        keys = list(returns)
        for i, s1 in enumerate(keys):
            for s2 in keys[i + 1:]:
                joined = pd.concat([returns[s1], returns[s2]], axis=1, join="inner").iloc[-168:]
                correlations[f"{s1}|{s2}"] = float(joined.corr().iloc[0, 1]) if len(joined) > 20 else None

        context = self._derivatives_context()
        health = md.health()
        snapshot = {
            "ts": time.time(), "exchange": md.exchange_id, "timeframe": tf,
            "symbols": symbols, "correlations": correlations,
            "context": context, "health": health,
        }
        ok = [s for s, v in symbols.items() if v["ok"]]
        state = "ok" if len(ok) == len(symbols) else "alert"
        parts = [f"{s} {v['mid']:,.0f} (spread {v['spread_bps']:.1f}bp)"
                 for s, v in symbols.items() if v.get("mid")]
        self.say(f"Mercato letto: {' · '.join(parts) or 'nessun dato'}", state, "scan",
                 stats=self.public_view(snapshot))
        return snapshot

    def _derivatives_context(self) -> dict:
        dx = self.office.derivatives
        if dx is None:
            return {}
        out = {}
        for symbol in self.settings["universe"]:
            base = symbol.split("/")[0]
            if base in out:
                continue
            out[base] = dx.funding_and_oi(f"{base}/USDT:USDT")
        return out

    @staticmethod
    def public_view(snapshot: dict) -> dict:
        """Versione serializzabile (senza DataFrame e book completi) per dashboard e log."""
        syms = {}
        for s, v in snapshot["symbols"].items():
            syms[s] = {k: v.get(k) for k in (
                "ok", "bid", "ask", "mid", "spread_bps", "depth_bid", "depth_ask", "volume_24h",
                "atr_pct", "momentum_24h", "candle_age_s", "data_age_s", "anomalies")}
            if "candles" in v and len(v["candles"]):
                syms[s]["spark"] = [round(x, 2) for x in v["candles"]["close"].iloc[-48:].tolist()]
        return {"exchange": snapshot["exchange"], "timeframe": snapshot["timeframe"],
                "symbols": syms, "correlations": snapshot["correlations"],
                "context": snapshot["context"], "health": snapshot["health"]}

    # ── candele per il timeframe di ogni strategia ──────────
    def candles_for(self, snapshot: dict, symbol: str, tf: str):
        """Candele chiuse del timeframe richiesto (in cache per il ciclo). None = dati non affidabili."""
        info = snapshot["symbols"].get(symbol, {})
        if tf == snapshot["timeframe"]:
            return info.get("candles")
        cache = snapshot.setdefault("extra_candles", {})
        key = f"{symbol}|{tf}"
        if key not in cache:
            md = self.office.market
            try:
                df = md.candles(symbol, tf, limit=1000)
            except DataError as exc:
                self.log(f"{symbol} {tf}: dati non disponibili ({exc})", "ERROR", "error")
                cache[key] = None
                return None
            tf_s = md.ex.parse_timeframe(tf)
            age = time.time() - (int(df["ts"].iloc[-1]) / 1000 + tf_s) if len(df) else 1e9
            if len(df) < 250 or age > 2 * tf_s:
                msg = f"candele {tf} non aggiornate o insufficienti"
                info.setdefault("anomalies", []).append(msg)
                info["ok"] = False
                self.log(f"{symbol}: ANOMALIA — {msg}", "WARN", "anomaly")
            cache[key] = df
        return cache[key]

    def strategy_signals(self, snapshot: dict, module, params: dict, tf: str) -> dict | None:
        """Segnali di una strategia su tutti gli asset. Per le multi-asset servono i dati di TUTTI:
        se ne manca uno la classifica sarebbe falsata, quindi niente segnali in questo ciclo."""
        own = universe_of(module, list(snapshot["symbols"]))
        data = {s: self.candles_for(snapshot, s, tf) for s in own}
        if hasattr(module, "generate_multi"):
            if any(df is None or len(df) < 250 for df in data.values()):
                self.log(f"{module.STRATEGY_ID}: dati incompleti su almeno un asset, nessun segnale.", "WARN", "anomaly")
                return None
            return {s: (data[s], sig) for s, sig in module.generate_multi(data, params).items()}
        return {s: (df, module.generate(df, params)) for s, df in data.items() if df is not None and len(df) >= 250}

    # ── da segnale a opportunità ─────────────────────────────
    def opportunities(self, snapshot: dict, strategies: list[dict]) -> list[dict]:
        costs = self.settings["costs"]
        seen = set(self.store.get("seen_signals", []))
        opps = []
        for st in strategies:
            module, validation = st["module"], st["validation"]
            params = (validation or {}).get("chosen_params") or _first_combo(module.PARAM_GRID)
            tf = timeframe_of(module, self.settings["timeframe"])
            sigs = self.strategy_signals(snapshot, module, params, tf) or {}
            for symbol, info in snapshot["symbols"].items():
                if not info.get("mid") or symbol not in sigs:
                    continue
                df, sig = sigs[symbol]
                if not bool(sig["entry"].iloc[-1]):
                    continue
                if any(p["symbol"] == symbol and p["strategy_id"] == module.STRATEGY_ID
                       for p in self.office.account.open_positions()):
                    continue            # già investita: il segnale "sopra la media" resta vero ogni giorno
                key = f"{module.STRATEGY_ID}|{symbol}|{int(df['ts'].iloc[-1])}"
                if key in seen:
                    continue            # segnale di questa candela già valutato
                seen.add(key)

                atr_now = float(atr(df, 14).iloc[-1])
                m = (validation or {}).get("metrics", {})
                gross = m.get("avg_gross")
                fees = 2 * costs["taker_fee"]
                slip = info["spread_bps"] / 10_000 + 2 * costs["slippage_bps"] / 10_000
                opp = {
                    "id": key,
                    "symbol": symbol,
                    "direction": "LONG",
                    "price": info["ask"],
                    "strategy_id": module.STRATEGY_ID,
                    "strategy_status": st["status"],
                    "params": params,
                    "timeframe": tf,
                    "stop": info["ask"] - params["stop_atr"] * atr_now,
                    "probability": m.get("win_rate"),
                    "gross_pct": gross,
                    "fees_pct": fees,
                    "slippage_pct": slip,
                    "net_pct": (gross - fees - slip) if gross is not None else None,
                    "risk_pct": params["stop_atr"] * atr_now / info["ask"],
                    "sizing": getattr(module, "SIZING", "risk"),
                    "confidence": _confidence(validation),
                    "signal": f"{module.NAME}: segnale di ingresso sulla candela chiusa",
                }
                opps.append(opp)
                est = (f"netto atteso {opp['net_pct'] * 100:+.2f}%" if opp["net_pct"] is not None
                       else "nessuna stima: strategia non validata")
                self.say(f"Opportunità {symbol} LONG da {module.STRATEGY_ID} ({tf}) @ {info['ask']:,.2f} — {est}",
                         "ok", "opportunity", payload=_clean(opp))
        self.store.set("seen_signals", sorted(seen)[-500:])
        if not opps:
            self.status("ok", "Nessun segnale sulle candele chiuse: niente da proporre.",
                        stats=self.public_view(snapshot))
        return opps


def _first_combo(grid: dict) -> dict:
    return {k: v[0] for k, v in grid.items()}


def _confidence(validation: dict | None) -> int:
    if not validation or validation.get("verdict") != "PASSED":
        return 0
    m = validation["metrics"]
    dsr = next((c["value"] for c in validation["checks"] if c["key"] == "dsr"), 0)
    score = (40 * min(1.0, dsr)
             + 30 * min(1.0, max(0.0, (m["profit_factor"] - 1) / 0.5))
             + 30 * min(1.0, m["trades"] / 400))
    return int(round(score))


def _clean(opp: dict) -> dict:
    return {k: v for k, v in opp.items() if k != "params"} | {"params": dict(opp["params"])}
