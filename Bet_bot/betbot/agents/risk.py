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


def kelly_net(prob: float, price: float, commission: float = 0.0) -> float:
    """Kelly pieno su exchange: la vincita netta per 1 € è (quota − 1) × (1 − commissione)."""
    b = (price - 1.0) * (1.0 - commission)
    if b <= 0 or prob <= 0:
        return 0.0
    return max(0.0, (prob * b - (1.0 - prob)) / b)


def worst_loss(p: dict, stake: float) -> float:
    """Perdita massima possibile: per un trade è la distanza dallo stop (con scivolamento), altrimenti la puntata."""
    ex = p.get("exchange") or {}
    return stake * ex["risk_per_unit"] if ex.get("risk_per_unit") else stake


def stake_for(proposal: dict, base: float, limits: dict, min_stake: float = 0.0) -> tuple[float, float]:
    """(puntata, kelly pieno).

    • Trade su exchange (back→lay con stop): puntata tale che la PERDITA MASSIMA resti sotto
      max_risk_per_trade_pct della base; tetto max_trade_stake_pct; se il minimo dell'exchange
      rispetta comunque il limite di rischio, si usa il minimo.
    • Puntata secca: Kelly frazionario sulla quota netta di commissione, tetto max_stake_pct."""
    from ..execution import round_back_stake
    ex = proposal.get("exchange") or {}
    if ex.get("risk_per_unit"):
        budget = limits["max_risk_per_trade_pct"] * base
        stake = min(budget / ex["risk_per_unit"], limits["max_trade_stake_pct"] * base)
        if stake < min_stake and min_stake * ex["risk_per_unit"] <= budget + 1e-9:
            stake = min_stake
        return round_back_stake(stake, min_stake), 0.0
    k = kelly_net(proposal["fair_prob"], proposal["odds"], proposal.get("commission", 0.0))
    cap = limits["max_stake_pct"] * base
    if limits.get("sizing") == "flat":
        return (round_back_stake(min(cap, limits["flat_stake_pct"] * base), min_stake) if k > 0 else 0.0), k
    stake = min(cap, k * limits["kelly_fraction"] * base)
    # Bankroll piccolo: la puntata minima (2 €) è ammessa solo se il vantaggio è così netto che resta
    # comunque al massimo `min_stake_max_kelly_share` del Kelly pieno (cioè si punta ancora "meno" di Kelly).
    share = limits.get("min_stake_max_kelly_share", 0.5)
    if stake < min_stake and min_stake <= share * k * base + 1e-9 and min_stake <= cap + 1e-9:
        stake = min_stake
    return round_back_stake(stake, min_stake), k


def unlock_bankroll(kind: str, limits: dict, min_stake: float) -> float:
    """Bankroll da cui una strategia può puntare senza superare max_stake_pct con la puntata minima.
    I trade con stop partono da subito; le puntate secche anche, ma sotto questa soglia passano solo
    quando il vantaggio è netto (regola del Kelly in stake_for)."""
    if kind in ("exchange", "trade"):
        return 0.0
    return round(min_stake / limits["max_stake_pct"], 2) if limits.get("max_stake_pct") else 0.0


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
        L = self.limits
        value = br.total
        day_start = br.day_start(value)
        peak = br.update_peak(value)
        dd = 1 - value / peak if peak > 0 else 0.0
        daily = value / day_start - 1 if day_start > 0 else 0.0
        # Con 2 € minimi su 30 € le percentuali scatterebbero dopo una o due perdite: sotto `small_bankroll`
        # valgono limiti assoluti (budget di perdita), sopra le percentuali.
        small = peak < L.get("small_bankroll", 0)
        kill = self.store.get("kill_switch")
        if not kill:
            if small and value <= L.get("kill_below_bankroll", 0):
                kill = f"bankroll {value:.2f} € sotto la soglia di {L['kill_below_bankroll']:.0f} €"
            elif not small and dd >= L["max_drawdown"]:
                kill = f"drawdown {dd:.1%} ≥ limite {L['max_drawdown']:.0%}"
            if kill:
                self.store.set("kill_switch", kill)
                self.say(f"KILL SWITCH ATTIVATO: {kill}. Nessuna nuova puntata. Reset solo dal PC.", "alert",
                         "kill_switch", level="CRITICAL")
        loss_today = max(0.0, day_start - value)
        daily_budget = L.get("max_daily_loss_eur", 0.0) if small else L["max_daily_loss"] * day_start
        if loss_today > 0 and loss_today >= daily_budget - 1e-9 and self.store.get("daily_stop_day") != today():
            self.store.set("daily_stop_day", today())
            self.say(f"Circuit breaker: persi {loss_today:.2f} € oggi (limite {daily_budget:.2f} €). "
                     "Nessuna nuova puntata fino a domani.", "blocked", "circuit", level="WARN")
        if file_sha256(LIMITS_FILE) != self.seal and not self.store.get("limits_tampered"):
            self.store.set("limits_tampered", True)
            self.say("Il file dei limiti di rischio è cambiato a bot acceso: blocco ogni nuova puntata. "
                     "Riavvia Bet_bot per sigillare i nuovi limiti.", "alert", "circuit", level="CRITICAL")
        streak = self._losing_streak()
        cooldown = self.store.get("cooldown_until") or 0
        if streak >= L["max_losing_streak"] and cooldown < clock.now() and not self.store.get("cooldown_streak_seen") == streak:
            cooldown = clock.now() + L["cooldown_minutes"] * 60
            self.store.set("cooldown_until", cooldown)
            self.store.set("cooldown_streak_seen", streak)
            self.say(f"Circuit breaker: {streak} perdite di fila. Pausa di {L['cooldown_minutes']} minuti.",
                     "blocked", "circuit", level="WARN")
        pause = self.store.get("telegram_pause_until") or 0            # pausa chiesta dal telefono (separata)
        return {"bankroll": value, "cash": br.cash, "open_stakes": br.open_stakes(), "open_risk": self.open_risk(),
                "profits": br.profits, "initial": br.initial_capital, "peak": peak, "drawdown": dd, "daily_pnl": daily,
                "kill_switch": kill, "cooldown_until": cooldown if cooldown > clock.now() else None,
                "telegram_pause_until": pause if pause > clock.now() else None,
                "small_bankroll": small, "loss_today": loss_today, "daily_budget": daily_budget,
                "daily_stop": self.store.get("daily_stop_day") == today(),
                "losing_streak": streak, "stake_base": br.stake_base(L["reinvest_fraction"], L["floor_pct_of_initial"])}

    def open_risk(self) -> float:
        """Somma delle perdite massime delle puntate aperte (trade: fino allo stop; puntate secche: tutta la puntata)."""
        import json
        tot = 0.0
        for b in self.office.bankroll.open_bets():
            ex = (json.loads(b["extra"]) if b.get("extra") else {}).get("exchange") or {}
            tot += b["stake"] * ex["risk_per_unit"] if ex.get("risk_per_unit") else b["stake"]
        return tot

    def _losing_streak(self) -> int:
        rows = self.store.query("SELECT pnl FROM bets WHERE status!='OPEN' AND status!='VOID' AND mode!='shadow' "
                                "ORDER BY settled_ts DESC, id DESC LIMIT 50")
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
        check(not state.get("daily_stop"), f"Stop giornaliero non attivo (persi oggi {state.get('loss_today', 0):.2f} €)")
        check(not state.get("telegram_pause_until"), "Nessuna pausa chiesta da Telegram")
        check(p["strategy_status"] == "ATTIVA", "Strategia attiva (non in osservazione)")
        scale = snapshot.get("time_scale") or 1.0
        age = ((snapshot.get("sim_time") or snapshot["ts"]) - (p.get("odds_ts") or 0)) / scale
        check(age <= L["max_odds_age_seconds"], f"Quote fresche ({age:.0f} s ≤ {L['max_odds_age_seconds']} s)")
        if not p.get("exchange") and not p.get("legs"):
            if p.get("ref_ts"):
                ref_age = ((snapshot.get("sim_time") or snapshot["ts"]) - p["ref_ts"]) / scale
                limit = L.get("max_reference_age_live_seconds", 300) if p.get("live") else L.get("max_reference_age_seconds", 9000)
                check(ref_age <= limit, f"Quote di riferimento abbastanza recenti ({ref_age / 60:.0f} min ≤ {limit / 60:.0f} min)")
            check(p["edge"] <= L.get("max_plausible_edge", 0.08),
                  f"Vantaggio plausibile (EV {p['edge']:+.1%} oltre il {L.get('max_plausible_edge', 0.08):.0%}: "
                  "probabile errore di dato, squadre abbinate male o riferimento vecchio)")
            check(p["n_books"] >= L["min_bookmakers"], f"Almeno {L['min_bookmakers']} bookmaker di riferimento ({p['n_books']})")
            check(p["dispersion"] <= L["max_odds_dispersion"], "Bookmaker di riferimento concordi sulla probabilità")
            check(p["edge"] >= L["min_edge"], f"EV netto ≥ {L['min_edge']:.1%} ({p['edge']:+.2%})")

        open_bets = self.office.bankroll.open_bets()
        is_exchange = bool(p.get("exchange"))
        today_n = self.store.query("SELECT COUNT(*) n FROM bets WHERE mode!='shadow' AND ts >= ? AND market " + ("IN" if is_exchange else "NOT IN")
                                   + " ('exchange_win', 'exchange_trade')", (_day_start_iso(),))[0]["n"]
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
        min_stake = getattr(getattr(self.office, "executor", None), "min_stake", 0.0)
        if verdict["level"] == "caution":                       # il dimezzamento va PRIMA dell'arrotondamento a 0,50 €
            stake, k_full = stake_for(p, base * L.get("sentiment_caution_stake_factor", 0.5), L, min_stake)
        else:
            stake, k_full = stake_for(p, base, L, min_stake)
        if p.get("exchange") and state.get("small_bankroll"):
            stake = min_stake                                          # bankroll piccolo: trade sempre alla puntata minima
        stake = max(0.0, min(stake, state["cash"]))                  # l'exchange blocca subito la puntata sul conto
        left_today = max(0.0, state.get("daily_budget", 1e9) - state.get("loss_today", 0.0))
        if not p.get("exchange") and stake > left_today:
            from ..execution import round_back_stake
            stake = round_back_stake(left_today, min_stake)            # la puntata secca non supera il budget del giorno
        risk_now = worst_loss(p, stake)
        check(risk_now <= max(0.0, state.get("daily_budget", 1e9) - state.get("loss_today", 0.0)) + 1e-9,
              f"Perdita possibile ({risk_now:.2f} €) entro il budget rimasto oggi")
        if p.get("exchange"):
            open_trades = [b for b in open_bets if b["market"] in ("exchange_trade", "exchange_win")]
            if state.get("small_bankroll"):
                check(len(open_trades) < L.get("max_open_trades_small", 1), "Un solo trade aperto con bankroll piccolo")
            check(sum(b["stake"] for b in open_trades) + stake <= L.get("max_open_trade_stake_pct", 1.0) * state["bankroll"] + 1e-9,
                  "Puntate dei trade aperti entro il limite per i salti di prezzo")
        check(state.get("open_risk", 0.0) + risk_now <= L["max_open_risk_pct"] * state["bankroll"] + 1e-9,
              f"Rischio aperto ≤ {L['max_open_risk_pct']:.0%} del bankroll "
              f"({state.get('open_risk', 0.0) + risk_now:.2f} € con questa)")
        on_match = sum(b["stake"] for b in open_bets if b["match_id"] == p["match_id"])
        check(on_match == 0 or on_match + risk_now <= L["max_exposure_per_match_pct"] * state["bankroll"],
              "Esposizione sulla stessa partita nei limiti")
        check(stake >= min_stake - 1e-9 and stake > 0,
              f"Puntata ≥ minimo exchange {min_stake:.2f} € (calcolata {stake:.2f} €"
              + (f"; il vantaggio non basta per giustificare la puntata minima con questo bankroll: Kelly pieno "
                 f"{k_full:.1%}, servirebbe almeno {min_stake / max(base, 1e-9) / L.get('min_stake_max_kelly_share', 0.5):.1%})"
                 if not p.get("exchange") else ")"))

        approved = not reasons
        decision = {"approved": approved, "stake": stake if approved else 0.0, "kelly_full": k_full, "risk": risk_now,
                    "sentiment": verdict,
                    "reasons": reasons, "checks": [{"ok": ok, "label": lab} for ok, lab in checks]}
        if approved:
            how = (f"perdita massima {risk_now:.2f} € allo stop" if p.get("exchange")
                   else f"Kelly netto {k_full:.1%} × {L['kelly_fraction']:.2f}")
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
