"""Test del gruppo rischio: puntata ridotta invece del veto, una puntata da 2 € sempre possibile sotto i 100 €,
kill switch che segue il picco, stop giornaliero, pausa per serie negativa, stessi freni nel backtest,
metriche con commissione, consenso solo su mercati completi, S06 e S08."""
import os
import time
from datetime import datetime

import pandas as pd
import pytest

from betbot.bankroll import TZ, today
from betbot.store import now_iso


@pytest.fixture
def office(tmp_path, monkeypatch):
    from betbot import local_settings
    monkeypatch.setattr(local_settings, "load", lambda: {**local_settings.DEFAULTS})
    from betbot.config import load_settings
    from betbot.core import SportOffice
    from betbot.feeds.mock import MockFeed
    st = load_settings()
    st["feed"]["mock"]["seed"] = 5
    feed = MockFeed(st)
    feed.speed = 1.0
    return SportOffice(db_path=tmp_path / "o.db", feed=feed)


def _proposal(**kw):
    p = {"strategy_id": "S05_favoriti_exchange_v2", "strategy_status": "ATTIVA", "match_id": "M1", "market_id": "M1",
         "league": "Serie A", "label": "Inter - Lecce · Inter", "market": "h2h", "selection": "home", "bookmaker": "Betfair",
         "odds": 1.25, "fair_prob": 0.85, "edge": 0.0625, "commission": 0.045, "n_books": 5, "dispersion": 0.01,
         "live": False, "odds_ts": 1000.0, "reason": "test"}
    p.update(kw)
    return p


def _trade(rpu: float, **kw):
    return _proposal(strategy_id="S07_scalping_prepartita_v1", market="exchange_trade", odds=2.0, n_books=1,
                     dispersion=0.0, exchange={"risk_per_unit": rpu, "target": 2.1, "stop": 1.9, "worst": 1.8,
                                               "gain_per_unit": 0.05}, **kw)


def _snap(back=1.25):
    return {"ts": 1000.0, "sim_time": 1000.0, "time_scale": 1.0, "races": {},
            "matches": {"M1": {"match_id": "M1", "status": "SCHEDULED", "home": "Inter", "away": "Lecce",
                               "exchange": {"home": {"back": back, "lay": back + 0.01, "back_size": 5000.0,
                                                     "lay_size": 3000.0}}}},
            "health": {"error_rate": 0, "source": "mock"}}


def _new_day(office, bankroll: float) -> None:
    """Bankroll impostato a inizio giornata (niente perdite di oggi)."""
    office.bankroll.cash = bankroll
    office.store.set(f"day_start:{today()}", bankroll)


def _open_bet(office, stake: float = 2.0, match: str = "M9") -> int:
    cur = office.store.execute("INSERT INTO bets(ts, mode, strategy_id, match_id, market, selection, odds, stake) "
                               "VALUES(?,?,?,?,?,?,?,?)", (now_iso(), "paper", "S05_favoriti_exchange_v2", match, "h2h",
                                                           "home", 1.3, stake))
    office.bankroll.cash = office.bankroll.cash - stake
    return cur.lastrowid


def _settle(office, bet_id: int, pnl: float) -> None:
    office.store.execute("UPDATE bets SET status=?, settled_ts=?, payout=?, pnl=? WHERE id=?",
                         ("WON" if pnl > 0 else "LOST", now_iso(), 2.0 + pnl, pnl, bet_id))
    office.bankroll.cash = office.bankroll.cash + 2.0 + pnl


# ── n.1: la puntata si riduce allo spazio che resta, il veto solo sotto i 2 € ──────────────────
def test_strong_edge_is_reduced_not_vetoed(office):
    """Con 30 € il tetto per puntata (10%) dà 3 €, il rischio aperto (8%) ne ammette 2,40: si punta 2 €."""
    st = office.risk.portfolio_state()
    edge = 0.995 * (1 + 0.05 * 0.955) - 1
    d = office.risk.evaluate(_proposal(odds=1.05, fair_prob=0.995, edge=edge), _snap(1.05), st)
    assert d["approved"] and d["stake"] == 2.0


def test_low_odds_trades_are_reduced_to_the_price_jump_cap(office):
    """Sopra i 100 € i trade a quota bassa (rischio per unità piccolo) si riducono al 15% invece del veto."""
    _new_day(office, 150.0)
    st = office.risk.portfolio_state()
    for rpu in (0.04, 0.06, 0.08):
        d = office.risk.evaluate(_trade(rpu), _snap(2.0), st)
        assert d["approved"], d["reasons"]
        assert d["stake"] <= office.risk.limits["max_open_trade_stake_pct"] * 150 + 1e-9
        assert (d["stake"] * 2) == int(d["stake"] * 2)


def test_fit_stake_and_treasurer_show_the_real_maximum(office):
    from betbot.agents.risk import fit_stake
    L = office.risk.limits
    args = dict(bankroll=30.0, open_risk=0.0, open_trade_stakes=0.0, left_today=4.0, small=True, nothing_open=True,
                limits=L, min_stake=2.0)
    assert fit_stake({}, 3.0, **args) == 2.0                                   # 2,40 € di spazio → 2 € (passi da 0,50)
    assert fit_stake({}, 3.0, **{**args, "open_risk": 2.0, "nothing_open": False}) == 0.0   # niente spazio: veto
    m = office.tesoriere.update(office.risk.portfolio_state())
    assert m["next_max_stake"] == 2.0                                          # non più i 3 € del solo tetto al 10%


def test_backtest_reduces_stake_instead_of_skipping():
    from betbot.backtest import run
    rows = [{"date": pd.Timestamp("2025-01-01") + pd.Timedelta(days=i), "sport": "soccer", "league": "X",
             "home": "A", "away": "B", "result": "home", "score": "",
             "books": {"Pinnacle": {"home": 1.12, "draw": 9.0, "away": 20.0}, "Bet365": {"home": 1.11, "draw": 8.5, "away": 19.0}},
             "exchange": {"home": 1.40, "draw": 9.5, "away": 21.0}, "exchange_close": None, "closing": None}
            for i in range(5)]
    r = run(rows, "S05_favoriti_exchange_v1", initial=30.0, commission=0.045, min_stake=2.0)
    assert r.metrics["bets"] == 5 and r.bets["stake"].iloc[0] == 2.0            # prima: 3 € > 2,40 € → saltate
    before = r.bets["bank"] - r.bets["pnl"]                                     # bankroll al momento della puntata
    assert (r.bets["stake"] <= 0.08 * before + 1e-9).all()                      # sempre entro il rischio aperto


# ── n.2: sotto i 100 € una puntata da 2 € alla volta resta possibile fino al kill switch ────────
@pytest.mark.parametrize("bankroll", [24.0, 21.0])
def test_one_min_stake_bet_possible_between_20_and_25(office, bankroll):
    _new_day(office, bankroll)
    edge = 0.80 * (1 + 0.35 * 0.955) - 1
    st = office.risk.portfolio_state()
    assert not st["kill_switch"]
    d = office.risk.evaluate(_proposal(odds=1.35, fair_prob=0.80, edge=edge), _snap(1.35), st)
    assert d["approved"] and d["stake"] == 2.0
    _open_bet(office)                                                          # una aperta: la seconda no
    d = office.risk.evaluate(_proposal(match_id="M2", odds=1.35, fair_prob=0.80, edge=edge), _snap(1.35),
                             office.risk.portfolio_state())
    assert not d["approved"] and any("Rischio aperto" in r for r in d["reasons"])


# ── n.3: una nuova serie di 6 perdite fa scattare una nuova pausa ────────────────────────────
def test_second_losing_streak_triggers_new_pause(office, monkeypatch):
    from betbot import clock
    t = [time.time()]
    monkeypatch.setattr(clock, "_source", lambda: t[0])
    office.bankroll.cash = 90.0                                                 # nessun kill switch di mezzo

    def streak(n):
        for _ in range(n):
            t[0] += 60
            _settle(office, _open_bet(office), -2.0)

    streak(6)
    assert office.risk.portfolio_state()["cooldown_until"]
    t[0] += 3 * 3600                                                            # pausa finita
    t[0] += 60
    _settle(office, _open_bet(office), 0.5)
    assert not office.risk.portfolio_state()["cooldown_until"]
    streak(6)
    st = office.risk.portfolio_state()
    assert st["losing_streak"] == 6 and st["cooldown_until"]


# ── n.4: sotto i 100 € la soglia del kill switch segue il picco ───────────────────────────────
def test_kill_floor_follows_peak():
    from betbot.agents.risk import breakers, kill_floor
    L = {"kill_below_bankroll": 20.0, "max_drawdown_small": 0.33, "small_bankroll": 100, "max_drawdown": 0.15,
         "max_daily_loss_eur": 4.0, "max_daily_loss": 0.05}
    assert kill_floor(30.0, L) == pytest.approx(20.1, abs=0.01)                 # a 30 € resta ~20 €
    assert kill_floor(25.0, L) == 20.0                                          # mai sotto kill_below_bankroll
    assert kill_floor(60.0, L) == pytest.approx(40.2)
    assert breakers(21.0, 99.0, 21.0, L)["kill"]                                # prima: −79% senza stop
    assert not breakers(70.0, 99.0, 70.0, L)["kill"]


def test_kill_switch_trailing_in_portfolio_state(office):
    _new_day(office, 60.0)
    office.risk.portfolio_state()                                               # picco 60 €
    _new_day(office, 41.0)
    assert not office.risk.portfolio_state()["kill_switch"]
    _new_day(office, 39.5)
    st = office.risk.portfolio_state()
    assert st["kill_switch"] and "sotto la soglia" in st["kill_switch"] and st["kill_floor"] == pytest.approx(39.96)


# ── n.5: le perdite chiuse nel primo ciclo del giorno contano per lo stop giornaliero ──────────
def test_losses_settled_before_first_state_of_day_count(office, monkeypatch):
    from betbot import clock
    t = [datetime(2026, 9, 28, 23, 30, tzinfo=TZ).timestamp()]
    monkeypatch.setattr(clock, "_source", lambda: t[0])
    office.risk.portfolio_state()
    ids = [_open_bet(office, match=f"M{i}") for i in range(2)]
    assert office.risk.portfolio_state()["loss_today"] == 0.0                   # aperte, valutate al costo
    t[0] = datetime(2026, 9, 29, 8, 0, tzinfo=TZ).timestamp()
    for i in ids:                                                               # si chiudono prima dello stato
        office.store.execute("UPDATE bets SET status='LOST', settled_ts=?, payout=0, pnl=-2.0 WHERE id=?", (now_iso(), i))
    st = office.risk.portfolio_state()
    assert st["bankroll"] == pytest.approx(26.0)
    assert st["loss_today"] == pytest.approx(4.0) and st["daily_stop"]


# ── n.6: consenso solo sui bookmaker che quotano tutto il mercato ──────────────────────────────
def test_consensus_ignores_incomplete_books():
    from betbot.odds import consensus
    books = {"Pinnacle": {"home": 1.34, "draw": 5.2, "away": 9.5}, "Bet365": {"home": 1.33, "draw": 5.0, "away": 9.0},
             "WH": {"home": 1.32, "draw": 5.0, "away": 9.0}, "Uni": {"home": 1.33, "draw": 5.25, "away": 8.5}}
    full = consensus(books)
    with_partial = consensus({**books, "Monco": {"home": 1.31, "away": 9.0}})  # 1X2 senza la X
    assert with_partial["home"]["fair_prob"] == pytest.approx(full["home"]["fair_prob"])
    assert with_partial["home"]["n_books"] == 4
    tennis = consensus({"A": {"home": 1.5, "away": 2.7}, "B": {"home": 1.52, "away": 2.6}})
    assert tennis["home"]["n_books"] == 2                                       # mercati a 2 esiti invariati


# ── n.7: sotto i 100 € il limite di perdita per trade vale anche alla puntata minima ───────────
def test_small_bankroll_trade_respects_per_trade_loss(office):
    _new_day(office, 22.0)
    st = office.risk.portfolio_state()
    ok = office.risk.evaluate(_trade(0.15), _snap(2.0), st)
    assert ok["approved"] and ok["stake"] == 2.0 and ok["risk"] <= 0.015 * 22 + 1e-9
    d = office.risk.evaluate(_trade(0.17), _snap(2.0), st)                      # 0,34 € allo stop > 0,33 €
    assert not d["approved"] and any("limite per trade" in r for r in d["reasons"])


# ── n.8 e n.11: metriche ───────────────────────────────────────────────────────────────────────
def test_breakeven_includes_commission():
    from betbot.metrics import summarize
    bets = [{"stake": 2.0, "odds": 1.25, "status": "WON" if i < 805 else "LOST", "selection": "home",
             "pnl": 2 * 0.25 * 0.955 if i < 805 else -2.0, "settled_ts": f"{i:05d}"} for i in range(1000)]
    m = summarize(bets, 30.0)
    assert m["pnl"] < 0 and m["win_rate"] < m["breakeven_win_rate"]           # in perdita = sotto il pareggio
    assert m["breakeven_win_rate"] == pytest.approx(1 / (1 + 0.25 * 0.955))
    zero = summarize([{**bets[0], "extra": '{"commission": 0.02}'}])
    assert zero["breakeven_win_rate"] == pytest.approx(1 / (1 + 0.25 * 0.98))  # commissione salvata in extra


def test_drawdown_curve_starts_from_capital():
    from betbot.metrics import summarize
    bets = [{"stake": 2.0, "odds": 1.25, "status": "LOST", "pnl": -2.0, "selection": "home", "settled_ts": "1"},
            {"stake": 2.0, "odds": 1.5, "status": "WON", "pnl": 1.0, "selection": "home", "settled_ts": "2"},
            {"stake": 2.0, "odds": 1.5, "status": "WON", "pnl": 1.0, "selection": "home", "settled_ts": "3"}]
    assert summarize(bets, 30.0)["max_drawdown"] == pytest.approx(2 / 30)      # 30 → 28: prima risultava 0
    assert summarize(bets)["max_drawdown_eur"] == pytest.approx(2.0)           # per strategia: in euro


# ── n.9: il backtest usa gli stessi freni del live ────────────────────────────────────────────
def _losing_rows(losses: int, days: int = 10):
    return [{"date": pd.Timestamp("2025-01-01") + pd.Timedelta(days=i), "sport": "soccer", "league": "X",
             "home": "A", "away": "B", "result": "away" if i < losses else "home", "score": "",
             "books": {"Pinnacle": {"home": 1.22, "draw": 7.0, "away": 14.0}, "Bet365": {"home": 1.20, "draw": 6.5, "away": 13.0}},
             "exchange": {"home": 1.35, "draw": 7.4, "away": 15.0}, "exchange_close": None, "closing": None}
            for i in range(days)]


def test_backtest_small_bankroll_uses_live_breakers():
    from betbot.backtest import run
    r = run(_losing_rows(3), "S05_favoriti_exchange_v1", initial=30.0, commission=0.045, min_stake=2.0)
    assert r.metrics["kill_switch"] is None and r.metrics["bets"] == 10        # 24 € dopo 3 perdite: si continua
    r = run(_losing_rows(5), "S05_favoriti_exchange_v1", initial=30.0, commission=0.045, min_stake=2.0)
    assert r.metrics["kill_switch"] == "2025-01-05" and "sotto la soglia" in r.metrics["kill_reason"]
    r = run(_losing_rows(5), "S05_favoriti_exchange_v1", initial=30.0, commission=0.045, min_stake=2.0,
            kill_switch=False)
    assert r.metrics["kill_switch"] is None and r.metrics["bets"] == 10


# ── n.10: S06 non chiude per un punteggio mancante ──────────────────────────────────────────────
def test_s06_does_not_hedge_on_missing_score():
    from betbot.strategies.s06_live_exchange_v1 import manage
    bet = [{"id": 1, "match_id": "M", "selection": "home", "odds": 1.2}]
    m = {"match_id": "M", "status": "LIVE", "home_score": None, "away_score": None, "minute": 80,
         "exchange": {"home": {"back": 1.2, "lay": 1.22}}}
    assert manage(bet, {"matches": {"M": m}}, {}, {}) == []
    lost = manage(bet, {"matches": {"M": {**m, "home_score": 1, "away_score": 1}}}, {}, {})
    assert lost and lost[0]["action"] == "hedge"


# ── n.12: S08 non inventa bookmaker; il Risk Manager usa tetti dedicati ─────────────────────────
def test_s08_table_probability_is_declared(office):
    from betbot.strategies.s08_basket_quarto_quarto_v1 import propose
    snap = {"matches": {"B1": {"match_id": "B1", "sport": "basketball_nba", "status": "LIVE", "minute": 37,
                               "home_score": 80, "away_score": 62, "league": "NBA", "home": "A", "away": "B",
                               "exchange": {"home": {"back": 1.03, "lay": 1.04}}}}}
    props = propose(snap, {}, {})
    assert props and props[0]["n_books"] == 0 and props[0]["prob_source"] == "table"
    st = office.risk.portfolio_state()
    edge = 0.995 * (1 + 0.05 * 0.955) - 1
    ok = office.risk.evaluate(_proposal(odds=1.05, fair_prob=0.995, edge=edge, n_books=0, dispersion=None,
                                        prob_source="table", live=True), _snap(1.05), st)
    assert ok["approved"]
    assert not any("bookmaker di riferimento" in c["label"] for c in ok["checks"])
    edge = 0.995 * (1 + 0.15 * 0.955) - 1
    d = office.risk.evaluate(_proposal(odds=1.15, fair_prob=0.995, edge=edge, n_books=0, dispersion=None,
                                       prob_source="table", live=True), _snap(1.15), st)
    assert not d["approved"] and any("tabella storica" in r for r in d["reasons"])


# ── n.41: backtest offline con lo storico già scaricato ─────────────────────────────────────────
def test_download_uses_cached_copy_when_offline(tmp_path, monkeypatch):
    import requests

    from betbot import backtest
    monkeypatch.setattr(backtest, "HISTORY_DIR", tmp_path)

    def offline(*a, **k):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(requests, "get", offline)
    season = backtest.exchange_seasons()[-1]
    cached = tmp_path / f"{season}_I1.csv"
    cached.write_text("Div,Date\n", encoding="utf-8")
    old = time.time() - 3 * 86400                                               # stagione in corso "vecchia"
    os.utime(cached, (old, old))
    assert backtest.download(["I1", "E0"], [season]) == [cached]               # E0 mai scaricato: saltato
