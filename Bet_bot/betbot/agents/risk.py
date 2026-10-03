"""AGENTE 5 — RISK MANAGER. Veto assoluto e calcolo della puntata.

Nessuna puntata arriva al Banco senza APPROVE. Basta una condizione violata
per il BLOCK. I limiti vengono da config/risk_limits.yaml, sigillato
all'avvio: se il file cambia a ufficio acceso, tutto è bloccato.
"""
from __future__ import annotations

from .. import clock
from datetime import datetime
import time

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


def _rounder(p: dict):
    """Arrotondamento della puntata: back a multipli di 0,50 €; lay d'apertura sulla responsabilità, con la puntata
    del backer al centesimo e minimo 0,50 €."""
    from ..execution import round_back_stake, round_lay_liability
    if p.get("side") == "LAY":
        return lambda stake, _min: round_lay_liability(stake, p["odds"])
    return round_back_stake


def lay_min_liability(p: dict) -> float:
    from ..execution import LAY_MIN
    return round(LAY_MIN * (p["odds"] - 1.0), 2)


def stake_for(proposal: dict, base: float, limits: dict, min_stake: float = 0.0) -> tuple[float, float]:
    """(puntata, kelly pieno).

    • Trade su exchange (back→lay con stop): puntata tale che la PERDITA MASSIMA resti sotto
      max_risk_per_trade_pct della base; tetto max_trade_stake_pct; se il minimo dell'exchange
      rispetta comunque il limite di rischio, si usa il minimo.
    • Puntata secca: Kelly frazionario sulla quota netta di commissione, tetto max_stake_pct.
    • Lay d'apertura: stessa regola sulla RESPONSABILITÀ (vince con probabilità 1 − p e incassa (1 − c)/(quota − 1)
      per ogni euro di responsabilità)."""
    round_back_stake = _rounder(proposal)
    if proposal.get("side") == "LAY":
        proposal = {**proposal, "fair_prob": 1.0 - proposal["fair_prob"], "odds": 1.0 + 1.0 / (proposal["odds"] - 1.0),
                    "side": "LAY_EQ"}
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


TRADE_MARKETS = ("exchange_trade", "exchange_win")


def live_limits(limits: dict, mode: str | None) -> dict:
    """Con i soldi veri valgono i tetti `live_*` (se presenti): il paper parte da 30 € e con un tetto a 30 € sarebbe
    fermo dall'inizio, mentre in live il capitale versato è più alto."""
    if mode != "live":
        return limits
    out = dict(limits)
    for key in ("kill_below_bankroll", "max_drawdown_small"):
        if limits.get("live_" + key) is not None:
            out[key] = limits["live_" + key]
    return out


def kill_floor(peak: float, limits: dict) -> float:
    """Soglia del kill switch sotto `small_bankroll`: sale col picco (picco × (1 − max_drawdown_small)) ma non scende
    mai sotto kill_below_bankroll. Con 30 € di picco resta a ~20 €; con 60 € di picco diventa 40,20 €: un profitto
    non si può più restituire tutto (6 € al giorno) senza che scatti il kill switch."""
    trailing = peak * (1.0 - limits.get("max_drawdown_small", 1.0))
    return round(max(limits.get("kill_below_bankroll", 0.0), trailing), 2)


def breakers(value: float, peak: float, day_start: float, limits: dict) -> dict:
    """Circuit breaker come funzione pura: la usano il Risk Manager dal vivo e il backtest, con gli stessi numeri.
    Con 2 € minimi su 30 € le percentuali scatterebbero dopo una o due perdite: sotto `small_bankroll` valgono
    limiti assoluti (soglia in euro che segue il picco, budget di perdita giornaliero in euro), sopra le percentuali."""
    small = peak < limits.get("small_bankroll", 0)
    dd = 1 - value / peak if peak > 0 else 0.0
    floor = kill_floor(peak, limits) if small else None
    kill = None
    if small and value <= floor:
        kill = f"bankroll {value:.2f} € sotto la soglia di {floor:.2f} €"
    elif not small and dd >= limits["max_drawdown"]:
        kill = f"drawdown {dd:.1%} ≥ limite {limits['max_drawdown']:.0%}"
    loss_today = max(0.0, day_start - value)
    budget = limits.get("max_daily_loss_eur", 0.0) if small else limits["max_daily_loss"] * day_start
    return {"small_bankroll": small, "drawdown": dd, "kill": kill, "kill_floor": floor, "loss_today": loss_today,
            "daily_budget": budget, "daily_stop": loss_today > 0 and loss_today >= budget - 1e-9}


def exposure_caps(bankroll: float, limits: dict, min_stake: float, small: bool, nothing_open: bool) -> tuple[float, float]:
    """(tetto del rischio aperto, tetto delle puntate dei trade aperti), in euro.
    Sotto `small_bankroll`, con niente di aperto, i tetti non scendono sotto la puntata minima: UNA puntata da 2 €
    alla volta resta sempre possibile finché il bankroll è sopra il kill switch (a proteggere restano lo stop
    giornaliero e il kill switch). Senza questo, tra 20 e 25 € l'8% del bankroll è sotto i 2 € e il bot gira a vuoto."""
    risk_cap = limits["max_open_risk_pct"] * bankroll
    trade_cap = limits.get("max_open_trade_stake_pct", 1.0) * bankroll
    if small and nothing_open:
        risk_cap, trade_cap = max(risk_cap, min_stake), max(trade_cap, min_stake)
    return risk_cap, trade_cap


def fit_stake(p: dict, stake: float, *, bankroll: float, open_risk: float, open_trade_stakes: float, left_today: float,
              small: bool, nothing_open: bool, limits: dict, min_stake: float) -> float:
    """Taglia la puntata sullo spazio che resta (budget di perdita del giorno, rischio aperto, puntate dei trade aperti)
    invece di mettere il veto: se non ci sta nemmeno la puntata minima restituisce 0. La stessa funzione serve al
    Risk Manager, al backtest e alla Tesoriera, così i tre mostrano la stessa puntata."""
    round_back_stake = _rounder(p)
    rpu = (p.get("exchange") or {}).get("risk_per_unit") or 1.0   # puntata secca o lay (sulla responsabilità): si perde tutto
    risk_cap, trade_cap = exposure_caps(bankroll, limits, min_stake, small, nothing_open)
    room = min(risk_cap - open_risk, left_today) / rpu
    if p.get("exchange"):
        room = min(room, trade_cap - open_trade_stakes)
    if stake > room + 1e-9:
        stake = round_back_stake(max(0.0, room), min_stake)
    return stake


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
        self.limits = live_limits(load_yaml("risk_limits.yaml"), (office.settings or {}).get("mode"))
        self.seal = file_sha256(LIMITS_FILE)
        self.store.set("limits_tampered", False)

    # ── stato e circuit breaker ────────────────────────────────
    def portfolio_state(self) -> dict:
        br = self.office.bankroll
        L = self.limits
        value = br.total
        # il valore di inizio giornata esclude ciò che si è chiuso oggi: se il primo ciclo del giorno regola prima le
        # partite della sera prima, quelle perdite contano comunque nel budget di oggi
        day_start = br.day_start(value - self._pnl_settled_today())
        peak = br.update_peak(value)
        daily = value / day_start - 1 if day_start > 0 else 0.0
        b = breakers(value, peak, day_start, L)
        small, dd = b["small_bankroll"], b["drawdown"]
        kill = self.store.get("kill_switch")
        if not kill and b["kill"]:
            kill = b["kill"]
            self.store.set("kill_switch", kill)
            self.say(f"KILL SWITCH ATTIVATO: {kill}. Nessuna nuova puntata. Reset solo dal PC.", "alert",
                     "kill_switch", level="CRITICAL")
        loss_today, daily_budget = b["loss_today"], b["daily_budget"]
        if b["daily_stop"] and self.store.get("daily_stop_day") != today():
            self.store.set("daily_stop_day", today())
            self.say(f"Circuit breaker: persi {loss_today:.2f} € oggi (limite {daily_budget:.2f} €). "
                     "Nessuna nuova puntata fino a domani.", "blocked", "circuit", level="WARN")
        if file_sha256(LIMITS_FILE) != self.seal and not self.store.get("limits_tampered"):
            self.store.set("limits_tampered", True)
            self.say("Il file dei limiti di rischio è cambiato a bot acceso: blocco ogni nuova puntata. "
                     "Riavvia Bet_bot per sigillare i nuovi limiti.", "alert", "circuit", level="CRITICAL")
        streak, last_loss = self._losing_streak()
        cooldown = self.store.get("cooldown_until") or 0
        # si ricorda QUALE perdita ha fatto scattare la pausa (non la lunghezza della serie): una nuova serie di 6,
        # dopo una vincita, fa scattare una nuova pausa anche se è lunga quanto la precedente
        if streak >= L["max_losing_streak"] and cooldown < clock.now() and self.store.get("cooldown_last_loss") != last_loss:
            cooldown = clock.now() + L["cooldown_minutes"] * 60
            self.store.set("cooldown_until", cooldown)
            self.store.set("cooldown_last_loss", last_loss)
            self.say(f"Circuit breaker: {streak} perdite di fila. Pausa di {L['cooldown_minutes']} minuti.",
                     "blocked", "circuit", level="WARN")
        pause = self.store.get("telegram_pause_until") or 0            # pausa chiesta dal telefono (separata)
        return {"bankroll": value, "cash": br.cash, "open_stakes": br.open_stakes(), "open_risk": self.open_risk(),
                "profits": br.profits, "initial": br.initial_capital, "peak": peak, "drawdown": dd, "daily_pnl": daily,
                "kill_switch": kill, "cooldown_until": cooldown if cooldown > clock.now() else None,
                "telegram_pause_until": pause if pause > clock.now() else None,
                "small_bankroll": small, "kill_floor": b["kill_floor"], "loss_today": loss_today, "daily_budget": daily_budget,
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

    def _pnl_settled_today(self) -> float:
        """P&L delle puntate vere chiuse da mezzanotte (ora italiana)."""
        row = self.store.query("SELECT COALESCE(SUM(pnl), 0) s FROM bets WHERE mode!='shadow' AND status!='OPEN' "
                               "AND settled_ts >= ?", (_day_start_iso(),))
        return float(row[0]["s"] or 0.0) if row else 0.0

    def _losing_streak(self) -> tuple[int, int | None]:
        """(perdite di fila, id della più recente). L'id distingue una serie nuova da quella che ha già dato la pausa."""
        rows = self.store.query("SELECT id, pnl FROM bets WHERE status!='OPEN' AND status!='VOID' AND mode!='shadow' "
                                "ORDER BY settled_ts DESC, id DESC LIMIT 50")
        n = 0
        for r in rows:
            if (r["pnl"] or 0) < 0:
                n += 1
            else:
                break
        return n, (rows[0]["id"] if n else None)

    def next_max_stake(self, state: dict) -> float:
        """Puntata secca massima possibile al prossimo ciclo: tetto per puntata, poi lo stesso taglio di evaluate
        (rischio aperto, budget del giorno) e la liquidità, arrotondata alle regole di betfair.it."""
        from ..execution import round_back_stake
        min_stake = getattr(getattr(self.office, "executor", None), "min_stake", 0.0)
        stake = min(self.limits["max_stake_pct"] * state["stake_base"], state["cash"])
        stake = fit_stake({}, stake, **self._room(state, self.office.bankroll.open_bets(), min_stake))
        return round_back_stake(stake, min_stake)

    def _room(self, state: dict, open_bets: list[dict], min_stake: float) -> dict:
        """Argomenti di fit_stake presi dallo stato del portafoglio."""
        return {"bankroll": state["bankroll"], "open_risk": state.get("open_risk", 0.0),
                "open_trade_stakes": sum(b["stake"] for b in open_bets if b["market"] in TRADE_MARKETS),
                "left_today": max(0.0, state.get("daily_budget", 1e9) - state.get("loss_today", 0.0)),
                "small": bool(state.get("small_bankroll")), "nothing_open": not open_bets,
                "limits": self.limits, "min_stake": min_stake}

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
        fun = bool(p.get("fun"))                 # S10 divertimento: puntata minima fissa, pochi colpi al giorno
        coach = getattr(self.office, "coach", None)
        coach_blocked = None
        if coach is not None:
            ok, label = coach.check(p, snapshot)
            check(ok, label)
            coach_blocked = None if ok else label
        scale = snapshot.get("time_scale") or 1.0
        age = ((snapshot.get("sim_time") or snapshot["ts"]) - (p.get("odds_ts") or 0)) / scale
        # più i secondi VERI passati dalla lettura alla valutazione: con il feed Betfair snapshot e quote hanno lo
        # stesso istante, e senza questo un ciclo lento (sentiment, chiusure, saldo) le farebbe sembrare sempre fresche
        if snapshot.get("fetched_mono") is not None:
            age += max(0.0, time.monotonic() - snapshot["fetched_mono"])
        check(age <= L["max_odds_age_seconds"], f"Quote fresche ({age:.0f} s ≤ {L['max_odds_age_seconds']} s)")
        if not p.get("exchange") and not p.get("legs"):
            if p.get("ref_ts"):
                ref_age = ((snapshot.get("sim_time") or snapshot["ts"]) - p["ref_ts"]) / scale
                limit = L.get("max_reference_age_live_seconds", 300) if p.get("live") else L.get("max_reference_age_seconds", 9000)
                check(ref_age <= limit, f"Quote di riferimento abbastanza recenti ({ref_age / 60:.0f} min ≤ {limit / 60:.0f} min)")
            check(p["edge"] <= L.get("max_plausible_edge", 0.08),
                  f"Vantaggio plausibile (EV {p['edge']:+.1%} oltre il {L.get('max_plausible_edge', 0.08):.0%}: "
                  "probabile errore di dato, squadre abbinate male o riferimento vecchio)")
            if p.get("prob_source") == "table":
                # probabilità da tabella storica (S08): nessun bookmaker da confrontare, quindi niente controlli di
                # consenso; al loro posto tetti dedicati su quota ed EV (a quote così basse una sconfitta cancella
                # decine di vincite, e un EV alto dice che la tabella non descrive questa partita)
                check(p["odds"] <= L.get("max_table_odds", 1.10) + 1e-9,
                      f"Quota ≤ {L.get('max_table_odds', 1.10):.2f} per le probabilità da tabella storica ({p['odds']:.2f})")
                check(p["edge"] <= L.get("max_table_edge", 0.05),
                      f"EV ≤ {L.get('max_table_edge', 0.05):.0%} per le probabilità da tabella storica ({p['edge']:+.1%})")
            elif p.get("prob_source") == "exchange":
                # probabilità dal prezzo medio di Betfair stesso (S10): nessun riferimento esterno, conta il libro stretto
                check(p.get("spread") is not None and p["spread"] <= L.get("fun_max_spread", 0.03) + 1e-9,
                      f"Libro Betfair stretto (spread {(p.get('spread') or 0):.1%} ≤ {L.get('fun_max_spread', 0.03):.0%})")
            elif p.get("ref_source") == "Pinnacle":
                # riferimento sharp unico (S09): il consenso tra bookmaker non serve, conta che Pinnacle ci sia
                check(p["n_books"] >= 1, "Riferimento sharp: Pinnacle senza margine")
            else:
                check(p["n_books"] >= L["min_bookmakers"], f"Almeno {L['min_bookmakers']} bookmaker di riferimento ({p['n_books']})")
                check(p["dispersion"] is not None and p["dispersion"] <= L["max_odds_dispersion"],
                      "Bookmaker di riferimento concordi sulla probabilità")
            min_edge = L.get("fun_min_edge", -0.03) if fun else L["min_edge"]
            check(p["edge"] >= min_edge, f"EV netto ≥ {min_edge:.1%} ({p['edge']:+.2%})")

        open_bets = self.office.bankroll.open_bets()
        is_exchange = bool(p.get("exchange"))
        today_n = self.store.query("SELECT COUNT(*) n FROM bets WHERE mode!='shadow' AND ts >= ? AND market " + ("IN" if is_exchange else "NOT IN")
                                   + " ('exchange_win', 'exchange_trade')", (_day_start_iso(),))[0]["n"]
        day_cap = L["max_exchange_trades_per_day"] if is_exchange else L["max_bets_per_day"]
        check(len(open_bets) < L["max_open_bets"], f"Puntate aperte < {L['max_open_bets']}")
        check(today_n < day_cap, f"Operazioni di oggi < {day_cap} ({'exchange' if is_exchange else 'sport'})")
        check(not any(b["match_id"] == p["match_id"] and b["strategy_id"] == p["strategy_id"] for b in open_bets),
              "Nessuna puntata già aperta sullo stesso evento")
        if fun:
            fun_today = self.store.query("SELECT COUNT(*) n FROM bets WHERE mode!='shadow' AND ts >= ? AND strategy_id=?",
                                         (_day_start_iso(), p["strategy_id"]))[0]["n"]
            fun_open = sum(1 for b in open_bets if b["strategy_id"] == p["strategy_id"])
            check(fun_today < L.get("fun_max_bets_per_day", 10),
                  f"Puntate di divertimento oggi < {L.get('fun_max_bets_per_day', 10)} ({fun_today})")
            check(fun_open < L.get("fun_max_open", 1), f"Puntate di divertimento aperte < {L.get('fun_max_open', 1)}")
        same_league = sum(1 for b in open_bets if b.get("league") == p.get("league"))
        check(same_league < L["max_same_league_open"], "Concentrazione per campionato nei limiti")

        sentiment = getattr(self.office, "sentiment", None)
        verdict = sentiment.verdict(p) if sentiment else {"level": "ok"}
        check(verdict["level"] != "block", "Sentiment e mercato non contrari" +
              (f" ({verdict.get('reason')})" if verdict["level"] == "block" else ""))

        base = state["stake_base"]
        min_stake = getattr(getattr(self.office, "executor", None), "min_stake", 0.0)
        if p.get("side") == "LAY":                               # lay d'apertura: minimo 0,50 € del backer
            min_stake = lay_min_liability(p)
        if fun:                                                 # divertimento: sempre e solo la puntata minima
            stake, k_full = min_stake, 0.0
        elif verdict["level"] == "caution":                     # il dimezzamento va PRIMA dell'arrotondamento a 0,50 €
            stake, k_full = stake_for(p, base * L.get("sentiment_caution_stake_factor", 0.5), L, min_stake)
        else:
            stake, k_full = stake_for(p, base, L, min_stake)
        rpu = (p.get("exchange") or {}).get("risk_per_unit")
        trade_budget = L["max_risk_per_trade_pct"] * base
        if p.get("exchange") and state.get("small_bankroll"):
            # bankroll piccolo: trade sempre alla puntata minima, ma solo se la perdita allo stop resta nel limite per trade
            stake = min_stake if not rpu or min_stake * rpu <= trade_budget + 1e-9 else 0.0
        sized = stake                                                # puntata del sizing, prima dei tagli
        stake = max(0.0, min(stake, state["cash"]))                  # l'exchange blocca subito la puntata sul conto
        # la puntata si RIDUCE allo spazio che resta (budget del giorno, rischio aperto, trade aperti): il veto arriva
        # solo se non ci sta nemmeno la puntata minima
        room = self._room(state, open_bets, min_stake)
        if fun:                                                 # divertimento: tetto del rischio aperto suo (fun_open_risk_pct)
            room["limits"] = {**L, "max_open_risk_pct": L.get("fun_open_risk_pct", L["max_open_risk_pct"])}
        stake = fit_stake(p, stake, **room)
        risk_now = worst_loss(p, stake)
        # con puntata 0 i limiti si verificano sulla puntata minima: così il veto dice quale limite la blocca
        tested = stake if stake > 0 else min_stake
        risk_tested = worst_loss(p, tested)
        risk_cap, trade_cap = exposure_caps(state["bankroll"], room["limits"], min_stake, room["small"], room["nothing_open"])
        check(risk_tested <= room["left_today"] + 1e-9,
              f"Perdita possibile ({risk_tested:.2f} €) entro il budget rimasto oggi ({room['left_today']:.2f} €)")
        if rpu:
            check(risk_tested <= trade_budget + 1e-9,
                  f"Perdita allo stop ({risk_tested:.2f} €) entro il limite per trade ({trade_budget:.2f} €)")
        if p.get("exchange"):
            open_trades = [b for b in open_bets if b["market"] in TRADE_MARKETS]
            if state.get("small_bankroll"):
                check(len(open_trades) < L.get("max_open_trades_small", 1), "Un solo trade aperto con bankroll piccolo")
            check(room["open_trade_stakes"] + tested <= trade_cap + 1e-9,
                  f"Puntate dei trade aperti entro il limite per i salti di prezzo ({trade_cap:.2f} €)")
        check(room["open_risk"] + risk_tested <= risk_cap + 1e-9,
              f"Rischio aperto entro {risk_cap:.2f} € ({room['limits']['max_open_risk_pct']:.0%} del bankroll) "
              f"({room['open_risk'] + risk_tested:.2f} € con questa)")
        on_match = sum(b["stake"] for b in open_bets if b["match_id"] == p["match_id"])
        check(on_match == 0 or on_match + risk_tested <= L["max_exposure_per_match_pct"] * state["bankroll"],
              "Esposizione sulla stessa partita nei limiti")
        check(stake >= min_stake - 1e-9 and stake > 0,
              f"Puntata ≥ minimo exchange {min_stake:.2f} € (calcolata {stake:.2f} €"
              + (f"; il vantaggio non basta per giustificare la puntata minima con questo bankroll: Kelly pieno "
                 f"{k_full:.1%}, servirebbe almeno {min_stake / max(base, 1e-9) / L.get('min_stake_max_kelly_share', 0.5):.1%})"
                 if not p.get("exchange") and not fun and sized < min_stake - 1e-9 else ")"))

        approved = not reasons
        decision = {"approved": approved, "stake": stake if approved else 0.0, "kelly_full": k_full, "risk": risk_now,
                    "sentiment": verdict,
                    "reasons": reasons, "checks": [{"ok": ok, "label": lab} for ok, lab in checks],
                    # solo la lezione di Leo ha fermato la proposta: la si segue in ombra per misurarne l'effetto
                    "coach_blocked": coach_blocked if coach_blocked and reasons == [coach_blocked] else None}
        if approved:
            how = (f"perdita massima {risk_now:.2f} € allo stop" if p.get("exchange")
                   else "divertimento: puntata minima fissa" if fun
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
