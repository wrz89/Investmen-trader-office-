"""AGENTE 5 — RISK MANAGER. Veto assoluto e calcolo della puntata.

Nessuna puntata arriva al Banco senza APPROVE. Basta una condizione violata
per il BLOCK. I limiti vengono da config/sport/risk_limits.yaml, sigillato
all'avvio: se il file cambia a ufficio acceso, tutto è bloccato.
"""
from __future__ import annotations

from .. import clock
from datetime import datetime

from ..bankroll import today
from ..config import CONFIG_DIR, file_sha256, load_yaml
from ..odds import kelly
from .base import Agent

LIMITS_FILE = CONFIG_DIR / "risk_limits.yaml"


def stake_for(proposal: dict, base: float, limits: dict) -> tuple[float, float]:
    """(puntata, kelly pieno). Kelly frazionario con tetto; flat per arbitraggio ed exchange."""
    cap = limits["max_stake_pct"] * base
    if proposal.get("legs") or proposal.get("exchange"):
        return round(min(cap, limits["flat_stake_pct"] * base * 2), 2), 0.0
    k = kelly(proposal["fair_prob"], proposal["odds"])
    if limits.get("sizing") == "flat":
        return (round(min(cap, limits["flat_stake_pct"] * base), 2) if k > 0 else 0.0), k
    return round(min(cap, k * limits["kelly_fraction"] * base), 2), k


class RiskManager(Agent):
    key = "risk"
    name = "Bruno"
    role = "Veto assoluto, sizing Kelly frazionario, circuit breaker"

    def __init__(self, office):
        super().__init__(office)
        self.limits = load_yaml("risk_limits.yaml")
        self.seal = file_sha256(LIMITS_FILE)
        self.store.set("limits_tampered", False)

    # ── stato e circuit breaker ────────────────────────────────
    def portfolio_state(self) -> dict:
        br = self.office.bankroll
        value = br.total
        day_start = br.day_start(value)
        peak = br.update_peak(value)
        dd = 1 - value / peak if peak > 0 else 0.0
        daily = value / day_start - 1 if day_start > 0 else 0.0
        kill = self.store.get("kill_switch")
        if dd >= self.limits["max_drawdown"] and not kill:
            kill = f"drawdown {dd:.1%} ≥ limite {self.limits['max_drawdown']:.0%}"
            self.store.set("kill_switch", kill)
            self.say(f"KILL SWITCH ATTIVATO: {kill}. Nessuna nuova puntata. Reset solo manuale.", "alert",
                     "kill_switch", level="CRITICAL")
        if daily <= -self.limits["max_daily_loss"] and self.store.get("daily_stop_day") != today():
            self.store.set("daily_stop_day", today())
            self.say(f"Circuit breaker: perdita del giorno {daily:.1%} oltre il limite del "
                     f"{self.limits['max_daily_loss']:.0%}. Nessuna nuova puntata fino a domani.", "blocked", "circuit",
                     level="WARN")
        if file_sha256(LIMITS_FILE) != self.seal and not self.store.get("limits_tampered"):
            self.store.set("limits_tampered", True)
            self.say("Il file dei limiti di rischio è cambiato a ufficio acceso: blocco ogni nuova puntata. "
                     "Riavvia l'ufficio per sigillare i nuovi limiti.", "alert", "circuit", level="CRITICAL")
        streak = self._losing_streak()
        cooldown = self.store.get("cooldown_until") or 0
        if streak >= self.limits["max_losing_streak"] and cooldown < clock.now() and not self.store.get("cooldown_streak_seen") == streak:
            cooldown = clock.now() + self.limits["cooldown_minutes"] * 60
            self.store.set("cooldown_until", cooldown)
            self.store.set("cooldown_streak_seen", streak)
            self.say(f"Circuit breaker: {streak} perdite di fila. Pausa di {self.limits['cooldown_minutes']} minuti.",
                     "blocked", "circuit", level="WARN")
        return {"bankroll": value, "cash": br.cash, "open_stakes": br.open_stakes(), "profits": br.profits,
                "initial": br.initial_capital, "peak": peak, "drawdown": dd, "daily_pnl": daily,
                "kill_switch": kill, "cooldown_until": cooldown if cooldown > clock.now() else None,
                "losing_streak": streak, "stake_base": br.stake_base(self.limits["reinvest_fraction"],
                                                                      self.limits["floor_pct_of_initial"])}

    def _losing_streak(self) -> int:
        rows = self.store.query("SELECT pnl FROM bets WHERE status!='OPEN' AND status!='VOID' ORDER BY settled_ts DESC, id DESC LIMIT 50")
        n = 0
        for r in rows:
            if (r["pnl"] or 0) < 0:
                n += 1
            else:
                break
        return n

    # ── valutazione ────────────────────────────────────────────
    def evaluate(self, p: dict, snapshot: dict, state: dict) -> dict:
        L = self.limits
        reasons: list[str] = []
        checks: list[tuple[bool, str]] = []

        def check(ok: bool, label: str) -> None:
            checks.append((ok, label))
            if not ok:
                reasons.append(label)

        check(file_sha256(LIMITS_FILE) == self.seal, "Limiti di rischio integri (file non modificato)")
        check(not state["kill_switch"], "Kill switch non attivo")
        check(not state["cooldown_until"], "Nessuna pausa per serie negativa")
        check(state["daily_pnl"] > -L["max_daily_loss"], f"Perdita del giorno sotto il {L['max_daily_loss']:.0%}")
        check(p["strategy_status"] == "ATTIVA", "Strategia attiva (non in osservazione)")
        scale = snapshot.get("time_scale") or 1.0
        age = ((snapshot.get("sim_time") or snapshot["ts"]) - (p.get("odds_ts") or 0)) / scale
        check(age <= L["max_odds_age_seconds"], f"Quote fresche ({age:.0f} s ≤ {L['max_odds_age_seconds']} s)")
        if not p.get("exchange") and not p.get("legs"):
            check(p["n_books"] >= L["min_bookmakers"], f"Almeno {L['min_bookmakers']} bookmaker ({p['n_books']})")
            check(p["dispersion"] <= L["max_odds_dispersion"], "Bookmaker concordi sulla probabilità")
        check(p["edge"] >= L["min_edge"] or bool(p.get("exchange")), f"Edge ≥ {L['min_edge']:.1%} ({p['edge']:+.2%})")

        open_bets = self.office.bankroll.open_bets()
        is_exchange = bool(p.get("exchange"))
        today_n = self.store.query("SELECT COUNT(*) n FROM bets WHERE ts >= ? AND market " + ("=" if is_exchange else "!=")
                                   + " 'exchange_win'", (_day_start_iso(),))[0]["n"]
        day_cap = L["max_exchange_trades_per_day"] if is_exchange else L["max_bets_per_day"]
        check(len(open_bets) < L["max_open_bets"], f"Puntate aperte < {L['max_open_bets']}")
        check(today_n < day_cap, f"Operazioni di oggi < {day_cap} ({'exchange' if is_exchange else 'sport'})")
        check(not any(b["match_id"] == p["match_id"] and b["strategy_id"] == p["strategy_id"] for b in open_bets),
              "Nessuna puntata già aperta sullo stesso evento")
        same_league = sum(1 for b in open_bets if b.get("league") == p.get("league"))
        check(same_league < L["max_same_league_open"], "Concentrazione per campionato nei limiti")

        sentiment = getattr(self.office, "sentiment", None)
        verdict = sentiment.verdict(p) if sentiment else {"level": "ok"}
        check(verdict["level"] != "block", "Sentiment e mercato non contrari" +
              (f" ({verdict.get('reason')})" if verdict["level"] == "block" else ""))

        base = state["stake_base"]
        stake, k_full = stake_for(p, base, L)
        if verdict["level"] == "caution":
            stake = round(stake * L.get("sentiment_caution_stake_factor", 0.5), 2)
        room_total = L["max_open_exposure_pct"] * state["bankroll"] - state["open_stakes"]
        on_match = sum(b["stake"] for b in open_bets if b["match_id"] == p["match_id"])
        room_match = L["max_exposure_per_match_pct"] * state["bankroll"] - on_match
        stake = round(max(0.0, min(stake, room_total, room_match, state["cash"])), 2)
        check(stake >= L["min_stake"], f"Puntata ≥ minimo {L['min_stake']:.2f} € (calcolata {stake:.2f} €)")

        approved = not reasons
        decision = {"approved": approved, "stake": stake if approved else 0.0, "kelly_full": k_full,
                    "sentiment": verdict,
                    "reasons": reasons, "checks": [{"ok": ok, "label": lab} for ok, lab in checks]}
        if approved:
            how = ("puntata fissa: trading/arbitraggio" if p.get("exchange") or p.get("legs")
                   else f"Kelly pieno {k_full:.1%} × {L['kelly_fraction']:.2f}")
            self.say(f"APPROVO {p['label']} a {p['odds']:.2f}: puntata {stake:.2f} € ({how}, base {base:.2f} €)"
                     + (f" · sentiment: {verdict['reason']}" if verdict["level"] == "caution" else "") + ".",
                     "ok", "approve", payload=decision)
        elif p["strategy_status"] == "ATTIVA":
            self.say(f"VETO {p['label']}: {reasons[0]}" + (f" (+{len(reasons) - 1})" if len(reasons) > 1 else ""),
                     "blocked", "veto", payload=decision)
        return decision


def _day_start_iso() -> str:
    from datetime import timezone
    from ..bankroll import TZ
    d = datetime.strptime(today(), "%Y-%m-%d").replace(tzinfo=TZ)
    return d.astimezone(timezone.utc).isoformat(timespec="seconds")
