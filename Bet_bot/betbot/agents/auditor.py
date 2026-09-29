"""AGENTE 8 — AUDITOR. Report giornaliero e verifica del libro scommesse."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ..bankroll import TZ
from ..config import REPORTS_DIR
from ..metrics import summarize
from .base import Agent


class Auditor(Agent):
    key = "auditor"
    name = "Irene"
    role = "Libro scommesse immutabile e report giornaliero"

    def daily_report(self, day: str | None = None) -> dict:
        from ..bankroll import today
        day = day or today()
        start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=TZ)
        a = start.astimezone(timezone.utc).isoformat(timespec="seconds")
        b = (start + timedelta(days=1)).astimezone(timezone.utc).isoformat(timespec="seconds")
        bets = self.store.query("SELECT * FROM bets WHERE mode!='shadow' AND settled_ts >= ? AND settled_ts < ?", (a, b))
        placed = self.store.query("SELECT COUNT(*) n FROM bets WHERE mode!='shadow' AND ts >= ? AND ts < ?", (a, b))[0]["n"]
        vetoes = self.store.query("SELECT COUNT(*) n FROM events WHERE kind='veto' AND ts >= ? AND ts < ?", (a, b))[0]["n"]
        m = summarize(bets)
        report = {"day": day, "placed": placed, "vetoes": vetoes, **{k: m[k] for k in
                  ("bets", "wins", "losses", "win_rate", "avg_odds", "breakeven_win_rate", "staked", "pnl", "roi", "clv_avg")},
                  "by_strategy": {sid: summarize([x for x in bets if x["strategy_id"] == sid])
                                  for sid in sorted({x["strategy_id"] for x in bets})},
                  "metrics": self.store.get("metrics")}
        md = render_markdown(report)
        self.store.execute("INSERT INTO daily_reports(day, data, markdown) VALUES(?,?,?) ON CONFLICT(day) DO UPDATE "
                           "SET data=excluded.data, markdown=excluded.markdown", (day, json.dumps(report, default=str), md))
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (REPORTS_DIR / f"report_{day}.md").write_text(md, encoding="utf-8")
        self.say(f"Report del {day}: {m['bets']} chiuse, P&L {m['pnl']:+.2f} €, ROI {m['roi']:+.1%}.", "ok", "report")
        return report


def render_markdown(r: dict) -> str:
    lines = [f"# Report ufficio sportivo — {r['day']}", "",
             f"- Puntate piazzate: {r['placed']} · chiuse: {r['bets']} · veti: {r['vetoes']}",
             f"- Vinte/perse: {r['wins']}/{r['losses']} · win rate {r['win_rate']:.0%} "
             f"(pareggio a quota media {r['avg_odds']:.2f}: {r['breakeven_win_rate']:.0%})",
             f"- Puntato {r['staked']:.2f} € · P&L {r['pnl']:+.2f} € · ROI {r['roi']:+.2%}",
             "- CLV medio: " + ("—" if r["clv_avg"] is None else f"{r['clv_avg']:+.2%}"), "", "## Per strategia", ""]
    for sid, s in r["by_strategy"].items():
        lines.append(f"- {sid}: {s['bets']} chiuse, win {s['win_rate']:.0%}, P&L {s['pnl']:+.2f} €, ROI {s['roi']:+.1%}")
    return "\n".join(lines) + "\n"
