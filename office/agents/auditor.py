"""AGENTE 7 — AUDITOR.

Registra ogni operazione nel libro immutabile e produce il report giornaliero.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np

from ..account import TZ, today
from ..config import REPORTS_DIR
from ..store import now_iso
from ..validation import profit_factor
from .base import Agent


class Auditor(Agent):
    key = "auditor"
    name = "Auditor"
    role = "Registra ogni operazione e produce il report"

    def record_trade(self, position: dict, exit_fill: dict, exit_reason: str) -> dict:
        qty = position["qty"]
        gross = (exit_fill["avg"] - position["entry_price"]) * qty
        fees = position["entry_fee"] + exit_fill["fee"]
        slippage = position["entry_slippage"] + exit_fill["slippage"]
        net = gross - fees
        row = (
            position["entry_ts"], now_iso(), self.settings["mode"], position["symbol"],
            position["strategy_id"], position["signal"], "LONG", qty,
            position["expected_price"], position["entry_price"], exit_fill["expected"], exit_fill["avg"],
            fees, slippage, gross, net, position["entry_reason"], exit_reason,
        )
        self.store.execute(
            "INSERT INTO trades(ts_open, ts_close, mode, symbol, strategy_id, signal, side, qty, "
            "expected_entry, exec_entry, expected_exit, exec_exit, fees, slippage, pnl_gross, pnl_net, "
            "entry_reason, exit_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
        self.say(f"Registrato trade {position['symbol']} {position['strategy_id']}: lordo {gross:+.4f}, "
                 f"fee {fees:.4f}, netto {net:+.4f} ({exit_reason})", "ok", "trade",
                 payload={"gross": gross, "fees": fees, "net": net, "slippage": slippage})
        return {"gross": gross, "net": net, "fees": fees}

    def mark_equity(self, risk_state: dict) -> None:
        self.store.execute("INSERT INTO equity(ts, equity, cash, exposure) VALUES(?,?,?,?)",
                           (now_iso(), risk_state["equity"], risk_state["cash"], risk_state["total_exposure"]))
        trades_today = self._trades_for(today())
        self.status("ok", f"Libro aggiornato: equity {risk_state['equity']:.2f}, "
                          f"{len(trades_today)} trade chiusi oggi.",
                    stats={"equity": risk_state["equity"], "trades_today": len(trades_today)})

    # ── report giornaliero ───────────────────────────────────
    def _trades_for(self, day: str) -> list[dict]:
        start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=TZ)
        end = start + timedelta(days=1)
        rows = self.store.query("SELECT * FROM trades ORDER BY id")
        return [r for r in rows
                if start <= datetime.fromisoformat(r["ts_close"]).astimezone(TZ) < end]

    def daily_report(self, day: str | None = None) -> dict:
        day = day or today()
        start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=TZ)
        end = start + timedelta(days=1)
        trades = self._trades_for(day)
        eq_rows = [r for r in self.store.query("SELECT * FROM equity ORDER BY ts")
                   if start <= datetime.fromisoformat(r["ts"]).astimezone(TZ) < end]
        initial = self.store.get(f"day_start_equity:{day}") or (eq_rows[0]["equity"] if eq_rows else
                                                                 self.store.get("initial_capital"))
        final = eq_rows[-1]["equity"] if eq_rows else initial
        eq = np.array([initial] + [r["equity"] for r in eq_rows])
        max_dd = float((1 - eq / np.maximum.accumulate(eq)).max()) if len(eq) else 0.0
        net = np.array([t["pnl_net"] for t in trades])
        by_strat: dict[str, float] = {}
        for t in trades:
            by_strat[t["strategy_id"]] = by_strat.get(t["strategy_id"], 0.0) + t["pnl_net"]
        statuses = self.store.query("SELECT * FROM strategy_status ORDER BY strategy_id")
        bounds = (_utc(start), _utc(end))
        errors = self.store.query(
            "SELECT ts, agent, message FROM events WHERE level IN ('ERROR','CRITICAL') AND ts >= ? AND ts < ?",
            bounds)
        anomalies = self.store.query(
            "SELECT ts, message FROM events WHERE kind='anomaly' AND ts >= ? AND ts < ?", bounds)
        blocks = self.store.query(
            "SELECT reasons FROM opportunities WHERE decision='BLOCK' AND ts >= ? AND ts < ?", bounds)
        open_pos = self.store.query("SELECT * FROM positions WHERE is_open=1")
        rs = self.office.last_risk_state or {}

        report = {
            "day": day,
            "capitale_iniziale": initial,
            "capitale_finale": final,
            "pnl_lordo": float(sum(t["pnl_gross"] for t in trades)),
            "commissioni": float(sum(t["fees"] for t in trades)),
            "slippage": float(sum(t["slippage"] for t in trades)),
            "pnl_netto": float(net.sum()) if len(net) else 0.0,
            "numero_trade": len(trades),
            "win_rate": float((net > 0).mean()) if len(net) else None,
            "profit_factor": profit_factor(net) if len(net) else None,
            "max_drawdown": max_dd,
            "strategia_migliore": max(by_strat, key=by_strat.get) if by_strat else None,
            "strategia_peggiore": min(by_strat, key=by_strat.get) if by_strat else None,
            "errori": len(errors),
            "anomalie": len(anomalies),
            "opportunita_bloccate": len(blocks),
            "rischio_attuale": {
                "esposizione": rs.get("total_exposure", 0.0),
                "drawdown": rs.get("drawdown", 0.0),
                "posizioni_aperte": len(open_pos),
                "kill_switch": rs.get("kill_switch"),
            },
            "strategie_attive": [s["strategy_id"] for s in statuses if s["status"] == "PAPER"],
            "strategie_sospese": [s["strategy_id"] for s in statuses if s["status"] == "SUSPENDED"],
            "strategie_rifiutate": [s["strategy_id"] for s in statuses if s["status"] == "REJECTED"],
        }
        report["analisi"] = self._analysis(report, statuses, blocks)
        md = render_markdown(report)
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (REPORTS_DIR / f"report_{day}.md").write_text(md, encoding="utf-8")
        self.store.execute(
            "INSERT INTO daily_reports(day, data, markdown) VALUES(?,?,?) "
            "ON CONFLICT(day) DO UPDATE SET data=excluded.data, markdown=excluded.markdown",
            (day, json.dumps(report, default=str), md))
        self.say(f"Report del {day} pronto: netto {report['pnl_netto']:+.2f}, {len(trades)} trade.",
                 "ok", "report")
        notifier = getattr(self.office, "notifier", None)
        if notifier is not None:
            notifier.daily_report(report)
        return report

    def _analysis(self, r: dict, statuses: list[dict], blocks: list[dict]) -> dict:
        reasons: dict[str, int] = {}
        for b in blocks:
            for reason in json.loads(b["reasons"] or "[]"):
                label = reason.split(":")[0]
                reasons[label] = reasons.get(label, 0) + 1
        top_block = sorted(reasons.items(), key=lambda x: -x[1])[:3]
        worked, failed = [], []
        if r["numero_trade"]:
            (worked if r["pnl_netto"] > 0 else failed).append(
                f"Risultato netto della giornata {r['pnl_netto']:+.2f} su {r['numero_trade']} trade.")
        else:
            worked.append("Nessuna operazione senza vantaggio statistico: il sistema ha rispettato le regole.")
        if r["anomalie"]:
            failed.append(f"{r['anomalie']} anomalie di dati: in quei momenti l'ufficio non ha operato.")
        if top_block:
            failed.append("Motivi di blocco più frequenti: " + ", ".join(f"{k} ({v})" for k, v in top_block))
        research = [s["strategy_id"] for s in statuses if s["status"] in ("RESEARCH", "REJECTED")]
        return {
            "cosa_ha_funzionato": worked,
            "cosa_non_ha_funzionato": failed or ["Nessun problema rilevato."],
            "da_testare": ["Nuove versioni delle strategie rifiutate (es. timeframe 4h, filtri di regime)"]
            if research else ["Proseguire il paper trading fino al campione minimo."],
            "da_sospendere": r["strategie_sospese"] or ["Nessuna"],
            "merita_altro_paper": r["strategie_attive"] or ["Nessuna strategia ha ancora superato la validazione"],
        }


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def render_markdown(r: dict) -> str:
    def eur(x):
        return "—" if x is None else f"{x:+.2f}"

    def pct(x):
        return "—" if x is None else f"{x:.0%}"

    a = r["analisi"]
    lines = [
        f"# Report giornaliero — {r['day']}",
        "",
        "| Voce | Valore |", "|---|---|",
        f"| Capitale iniziale | {r['capitale_iniziale']:.2f} |",
        f"| Capitale finale | {r['capitale_finale']:.2f} |",
        f"| P&L lordo | {eur(r['pnl_lordo'])} |",
        f"| Commissioni | {r['commissioni']:.2f} |",
        f"| Slippage | {r['slippage']:.2f} |",
        f"| P&L netto | {eur(r['pnl_netto'])} |",
        f"| Numero trade | {r['numero_trade']} |",
        f"| Win rate | {pct(r['win_rate'])} |",
        f"| Max drawdown | {r['max_drawdown']:.2%} |",
        f"| Strategia migliore | {r['strategia_migliore'] or '—'} |",
        f"| Strategia peggiore | {r['strategia_peggiore'] or '—'} |",
        f"| Errori | {r['errori']} |",
        f"| Anomalie | {r['anomalie']} |",
        f"| Opportunità bloccate dal Risk Manager | {r['opportunita_bloccate']} |",
        f"| Rischio attuale | esposizione {r['rischio_attuale']['esposizione']:.2f}, "
        f"drawdown {r['rischio_attuale']['drawdown']:.2%} |",
        f"| Strategie attive | {', '.join(r['strategie_attive']) or '—'} |",
        f"| Strategie sospese | {', '.join(r['strategie_sospese']) or '—'} |",
        "",
        "## 1. Cosa ha funzionato", *[f"- {x}" for x in a["cosa_ha_funzionato"]],
        "## 2. Cosa non ha funzionato", *[f"- {x}" for x in a["cosa_non_ha_funzionato"]],
        "## 3. Cosa deve essere testato", *[f"- {x}" for x in a["da_testare"]],
        "## 4. Strategie da sospendere", *[f"- {x}" for x in a["da_sospendere"]],
        "## 5. Strategie che meritano altro paper trading", *[f"- {x}" for x in a["merita_altro_paper"]],
        "",
    ]
    return "\n".join(lines)
