"""Test dell'ufficio sportivo: matematica delle quote, registro immutabile, veto e sizing,
esecuzione e cancelli per i soldi veri, sentiment, stream Betfair, backtest senza sguardi al futuro."""
import asyncio
import sqlite3

import pytest

from betbot.odds import consensus, kelly, remove_margin, surebet
from betbot.store import Store


# ── quote ───────────────────────────────────────────────────
def test_remove_margin_sums_to_one_and_power_favours_favourite():
    prices = {"home": 1.22, "draw": 6.0, "away": 12.0}
    power, prop = remove_margin(prices), remove_margin(prices, "proportional")
    assert sum(power.values()) == pytest.approx(1.0)
    assert power["home"] > prop["home"]            # il metodo potenza toglie meno margine al favorito


def test_kelly_is_zero_without_edge_and_positive_with_edge():
    assert kelly(0.80, 1.20) == 0.0                # EV = 0,96 − 1 < 0
    assert kelly(0.85, 1.25) == pytest.approx((0.85 * 0.25 - 0.15) / 0.25)


def test_surebet_detection():
    assert surebet({"home": (2.10, "A"), "away": (2.10, "B")})["margin"] == pytest.approx(0.05)
    assert surebet({"home": (1.90, "A"), "away": (1.90, "B")}) is None


def test_consensus_weights_sharp_book():
    c = consensus({"Pinnacle": {"home": 1.25, "draw": 6.5, "away": 13}, "Soft": {"home": 1.40, "draw": 5.0, "away": 8}})
    assert c["home"]["best_book"] == "Soft" and c["home"]["fair_prob"] > 0.74


# ── libro scommesse ────────────────────────────────────────
def test_bets_are_immutable_and_settle_once(tmp_path):
    s = Store(tmp_path / "s.db")
    s.execute("INSERT INTO bets(strategy_id, match_id, selection, odds, stake) VALUES('S','M','home',1.2,1)")
    with pytest.raises(sqlite3.IntegrityError):
        s.execute("UPDATE bets SET odds=5")
    with pytest.raises(sqlite3.IntegrityError):
        s.execute("DELETE FROM bets")
    s.execute("UPDATE bets SET status='WON', pnl=0.2")
    with pytest.raises(sqlite3.IntegrityError):
        s.execute("UPDATE bets SET status='LOST'")


# ── ufficio ────────────────────────────────────────────────
@pytest.fixture
def office(tmp_path, monkeypatch):
    from betbot import local_settings
    monkeypatch.setattr(local_settings, "load", lambda: {**local_settings.DEFAULTS})
    from betbot.core import SportOffice
    from betbot.feeds.mock import MockFeed
    from betbot.config import load_settings
    st = load_settings()
    st["feed"]["mock"]["seed"] = 5
    feed = MockFeed(st)
    feed.speed = 1.0
    o = SportOffice(db_path=tmp_path / "o.db", feed=feed)
    return o


def _proposal(**kw):
    p = {"strategy_id": "S05_favoriti_exchange_v1", "strategy_status": "ATTIVA", "match_id": "M1", "market_id": "M1",
         "league": "Serie A", "label": "Inter - Lecce · Inter", "market": "h2h", "selection": "home", "bookmaker": "Betfair",
         "odds": 1.25, "fair_prob": 0.85, "edge": 0.0625, "commission": 0.05, "n_books": 5, "dispersion": 0.01,
         "live": False, "odds_ts": 1000.0, "reason": "test"}
    p.update(kw)
    return p


def _snap(back=1.25, back_size=5000.0):
    return {"ts": 1000.0, "sim_time": 1000.0, "time_scale": 1.0, "races": {},
            "matches": {"M1": {"match_id": "M1", "status": "SCHEDULED", "home": "Inter", "away": "Lecce",
                               "exchange": {"home": {"back": back, "lay": 1.26, "back_size": back_size, "lay_size": 3000.0}}}},
            "health": {"error_rate": 0, "source": "mock"}}


def test_min_stake_with_30_eur_only_when_edge_is_strong(office):
    """Con 30 € la puntata minima di 2 € (6,7%) passa solo se resta ≤ metà del Kelly pieno."""
    state = office.risk.portfolio_state()
    strong = office.risk.evaluate(_proposal(fair_prob=0.85, edge=0.05), _snap(), state)
    assert strong["approved"] and strong["stake"] == 2.0
    weak = office.risk.evaluate(_proposal(fair_prob=0.815, edge=0.012), _snap(), state)
    assert not weak["approved"] and any("vantaggio non basta" in r for r in weak["reasons"])
    by = {s["id"]: s for s in office.direttore.strategies()}
    assert by["S05_favoriti_exchange_v1"]["status"] == "ATTIVA"
    assert by["S04_greenup_cavalli_v2"]["status"] == "OSSERVAZIONE"             # niente ippica su betfair.it


def test_back_stakes_follow_italian_rules():
    from betbot.execution import round_back_stake
    assert round_back_stake(2.99) == 2.5 and round_back_stake(3.0) == 3.0 and round_back_stake(1.99) == 0.0


def test_back_bet_approved_and_capped_when_bankroll_is_enough(office):
    office.bankroll.cash = 300.0
    state = office.risk.portfolio_state()
    d = office.risk.evaluate(_proposal(), _snap(), state)
    assert d["approved"]
    assert 2.0 <= d["stake"] <= office.risk.limits["max_stake_pct"] * state["stake_base"] + 1e-9
    assert (d["stake"] * 2) == int(d["stake"] * 2)                              # multipli di 0,50 €


def test_trade_is_sized_on_worst_loss():
    from betbot.agents.risk import stake_for, worst_loss
    from betbot.strategies.s04_greenup_cavalli_v2 import DEFAULTS, trade_plan
    plan = trade_plan(4.0, DEFAULTS)
    limits = {"max_risk_per_trade_pct": 0.015, "max_trade_stake_pct": 0.25, "max_stake_pct": 0.02, "kelly_fraction": 0.25}
    p = {"exchange": {"risk_per_unit": plan["risk_per_unit"]}}
    stake, _ = stake_for(p, 30.0, limits, 2.0)
    assert 2.0 <= stake <= 7.5
    assert worst_loss(p, stake) <= 0.015 * 30 + 1e-9                 # perdita massima ≤ 0,45 €
    assert 0.5 < plan["breakeven_hit_rate"] < 0.9 and plan["gain_per_unit"] > 0


def test_paper_exchange_fill_or_kill_rules():
    from betbot.execution import PaperExchange
    ex = PaperExchange(1.5)
    book = {"back": 2.0, "lay": 2.02, "back_size": 30.0, "lay_size": 5.0}
    assert ex.place("BACK", 2.0, 10.0, book)["ok"]                    # prezzo ok, 30 € ≥ 15 €
    assert not ex.place("BACK", 2.02, 10.0, book)["ok"]               # chiedo più di quanto offre il book
    assert not ex.place("BACK", 2.0, 25.0, book)["ok"]                # liquidità insufficiente
    assert not ex.place("LAY", 2.02, 10.0, book)["ok"]                # sul lay ci sono solo 5 €
    assert not ex.place("BACK", 2.0, 1.0, None)["ok"]


def test_risk_vetoes(office):
    state = office.risk.portfolio_state()
    assert not office.risk.evaluate(_proposal(strategy_status="OSSERVAZIONE"), _snap(), state)["approved"]
    assert not office.risk.evaluate(_proposal(edge=0.001), _snap(), state)["approved"]
    assert not office.risk.evaluate(_proposal(odds_ts=0.0), _snap(), state)["approved"]      # quote vecchie
    office.store.set("kill_switch", "test")
    d = office.risk.evaluate(_proposal(), _snap(), office.risk.portfolio_state())
    assert not d["approved"] and any("Kill switch" in r for r in d["reasons"])


def test_kill_switch_triggers_on_drawdown(office):
    office.bankroll.cash = office.bankroll.initial_capital * 0.8
    st = office.risk.portfolio_state()
    assert st["kill_switch"] and "drawdown" in st["kill_switch"]
    assert any("KILL SWITCH" in m for m in office.notifier.sent)                  # arriva anche su Telegram


def test_paper_bet_notifies_and_settles_with_commission(office):
    office.bankroll.cash = 300.0
    state = office.risk.portfolio_state()
    p = _proposal()
    d = office.risk.evaluate(p, _snap(), state)
    assert office.banco.place(p, d, "c1", _snap())
    assert any("Puntata #1" in m and "PAPER" in m for m in office.notifier.sent)     # Telegram a ogni giocata
    before = office.bankroll.total
    snap = _snap()
    snap["matches"]["M1"] = {"match_id": "M1", "status": "FINISHED", "result": "home", "home_score": 2, "away_score": 0,
                             "closing": {"home": 1.20}}
    assert office.banco.settle(snap) == 1
    bet = office.store.query("SELECT * FROM bets")[0]
    assert bet["status"] == "WON" and bet["pnl"] == pytest.approx(d["stake"] * 0.25 * (1 - p["commission"]))
    assert bet["clv"] == pytest.approx(1.25 / 1.20 - 1)
    assert office.bankroll.total == pytest.approx(before + bet["pnl"])


def test_exchange_green_up_math():
    from betbot.agents.banco import exchange_green
    assert exchange_green(10, 4.0, 3.8, 0.05) == pytest.approx(10 * (4 / 3.8 - 1) * 0.95)
    assert exchange_green(10, 4.0, 4.2, 0.05) == pytest.approx(10 * (4 / 4.2 - 1))


def test_live_gates_closed_by_default(monkeypatch):
    from betbot import local_settings
    from betbot.execution import Gates
    monkeypatch.setattr(local_settings, "load", lambda: {**local_settings.DEFAULTS})
    ok, why = Gates.live_allowed({"mode": "live", "execution": {"provider": "betfair"}, "live_strategies": ["X"]}, "X")
    assert not ok and "verificato" in why
    assert not Gates.live_allowed({"mode": "paper"}, "X")[0]


def test_sentiment_market_move_blocks(office):
    rows = []
    for ts, price in (("2026-01-01T10:00:00+00:00", 1.25), ("2026-01-01T10:30:00+00:00", 1.40)):
        for book in ("A", "B", "C"):
            rows.append((ts, "M9", book, "h2h", "home", price, 0))
            rows.append((ts, "M9", book, "h2h", "away", 9.0, 0))
            rows.append((ts, "M9", book, "h2h", "draw", 5.5, 0))
    office.store.executemany("INSERT INTO odds(ts, match_id, bookmaker, market, selection, price, live) VALUES(?,?,?,?,?,?,?)", rows)
    from betbot import clock
    from datetime import datetime
    clock.set_source(lambda: datetime.fromisoformat("2026-01-01T10:45:00+00:00").timestamp())
    try:
        move = office.sentiment.market_move("M9", "home")
        v = office.sentiment.verdict(_proposal(match_id="M9"))
    finally:
        clock.set_source(None)
    assert move < -0.06 and v["level"] == "block"


def test_sentiment_classifier_sensitivity():
    from betbot.agents.sentiment import classify, split_publisher, team_in_title
    from betbot.config import load_yaml
    cfg = load_yaml("sentiment.yaml")["news"]
    assert classify("Inter, Lautaro out per infortunio: salta la partita", cfg)["severity"] == "high"
    assert classify("Napoli, Lukaku rientra dall'infortunio", cfg)["severity"] == "neutral"
    assert classify("Inter, il punto sugli infortunati: chi torna", cfg)["severity"] == "none"
    assert split_publisher("Titolo qualsiasi - La Gazzetta dello Sport", "x") == ("Titolo qualsiasi", "La Gazzetta dello Sport")
    assert team_in_title("Inter", "Inter, novità") and not team_in_title("Inter", "Internazionali di tennis")


def test_betfair_stream_cache_applies_deltas():
    from betbot.feeds.betfair import MarketCache
    c = MarketCache()
    c.apply({"op": "mcm", "mc": [{"id": "1.2", "img": True, "rc": [{"id": 11, "batb": [[0, 3.5, 100], [1, 3.45, 50]],
                                                                    "batl": [[0, 3.55, 80]], "ltp": 3.5}]}]})
    c.apply({"op": "mcm", "mc": [{"id": "1.2", "rc": [{"id": 11, "batb": [[0, 3.6, 20], [1, 0, 0]]}]}]})
    b = c.best("1.2")["11"]
    assert b["back"] == 3.6 and b["lay"] == 3.55 and b["back_size"] == 20


def test_team_matching():
    from betbot.feeds.api_football import same_team
    assert same_team("Manchester City", "Man City") and same_team("AC Milan", "Milan")
    assert not same_team("Man United", "Man City") and not same_team("Real Madrid", "Atletico Madrid")


def test_backtest_uses_exchange_prices_commission_and_italian_stakes():
    import pandas as pd
    from betbot.backtest import montecarlo, run
    rows = []
    for i in range(30):
        rows.append({"date": pd.Timestamp("2025-01-01") + pd.Timedelta(days=i), "sport": "soccer", "league": "X",
                     "home": "A", "away": "B", "result": "home" if i % 5 else "away", "score": "",
                     "books": {"Pinnacle": {"home": 1.22, "draw": 7.0, "away": 14.0},
                               "Bet365": {"home": 1.20, "draw": 6.5, "away": 13.0}},
                     "exchange": {"home": 1.35, "draw": 7.4, "away": 15.0},
                     "exchange_close": {"home": 1.30, "draw": 7.6, "away": 16.0}, "closing": None})
    r = run(rows, "S05_favoriti_exchange_v1", initial=30.0, commission=0.045, min_stake=2.0)
    assert r.metrics["bets"] == 30 and r.metrics["win_rate"] == pytest.approx(0.8)
    assert (r.bets["odds"] == 1.35).all()                              # prezzo Betfair pre-partita, mai la chiusura
    assert ((r.bets["stake"] * 2) % 1 == 0).all() and (r.bets["stake"] >= 2).all()   # 2 € a multipli di 0,50
    won = r.bets[r.bets["won"]].iloc[0]
    assert won["pnl"] == pytest.approx(won["stake"] * 0.35 * 0.955)   # commissione sulla vincita
    assert r.bets["clv"].iloc[0] == pytest.approx(1.35 / 1.30 - 1)
    naive = run(rows, "NAIVE_80", initial=30.0, commission=0.045)
    assert naive.metrics["bets"] == 0                                   # 1,35 fuori dalla fascia 1,15–1,25
    mc = montecarlo(0.80, 1.20, 0.02, n_bets=200, paths=200)
    assert mc["ev_per_bet"] < 0 and mc["breakeven"] > 0.83

def test_short_simulation_runs_and_books_balance(tmp_path, monkeypatch):
    monkeypatch.setenv("BETBOT_RUNTIME_DIR", str(tmp_path))
    import importlib
    import betbot.config as cfg
    importlib.reload(cfg)
    import betbot.simulate as sim
    importlib.reload(sim)
    m = asyncio.run(sim.run(hours=8, step_minutes=3, seed=3))
    s = Store(m["db"])
    pnl = sum(r["pnl"] or 0 for r in s.query("SELECT pnl FROM bets WHERE status!='OPEN'"))
    open_stakes = sum(r["stake"] for r in s.query("SELECT stake FROM bets WHERE status='OPEN'"))
    assert float(s.get("cash")) + open_stakes == pytest.approx(m["initial"] + pnl, abs=1e-3)
    importlib.reload(cfg)


def test_odds_api_feed_parsing_and_throttling(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from betbot.feeds import odds_api
    monkeypatch.setattr(odds_api, "api_key", lambda: "k")
    ko = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat().replace("+00:00", "Z")
    calls = []

    def fake_get(self, path, **params):
        calls.append(path)
        if path.endswith("/odds"):
            return [{"id": "e1", "home_team": "Inter", "away_team": "Lecce", "commence_time": ko, "sport_title": "Serie A",
                     "bookmakers": [{"title": b, "markets": [{"key": "h2h", "outcomes": [
                         {"name": "Inter", "price": 1.25}, {"name": "Lecce", "price": 12.0}, {"name": "Draw", "price": 6.5}]}]}
                                    for b in ("Pinnacle", "Bet365", "Unibet")]}]
        return []
    monkeypatch.setattr(odds_api.OddsApiFeed, "_get", fake_get)
    settings = {"feed": {"sports": ["soccer_italy_serie_a"], "odds_api": {"odds_refresh_seconds": 3600}}}
    f = odds_api.OddsApiFeed(settings)
    snap = asyncio.run(f.fetch())
    m = snap["matches"]["e1"]
    assert m["status"] == "SCHEDULED" and m["books"]["Bet365"] == {"home": 1.25, "away": 12.0, "draw": 6.5}
    n = len(calls)
    asyncio.run(f.fetch())
    assert len(calls) == n                                     # entro l'intervallo nessuna nuova richiesta


def test_betfair_soccer_runners_mapped_by_name():
    from betbot.feeds.betfair import BetfairFeed
    f = BetfairFeed.__new__(BetfairFeed)
    f.client = type("C", (), {"errors": 0, "calls": 1})()
    f.stream_task = None
    f.cat = {"1.5": {"_kind": "soccer", "marketStartTime": "2030-01-01T18:00:00Z", "event": {"name": "Inter v Lecce"},
                     "competition": {"name": "Serie A"},
                     "runners": [{"selectionId": 3, "runnerName": "The Draw"}, {"selectionId": 2, "runnerName": "Lecce"},
                                 {"selectionId": 1, "runnerName": "Inter"}]}}
    prices = {"1.5": {"status": "OPEN", "inplay": False, "runners": {
        "1": {"back": 1.25, "lay": 1.26, "back_size": 1, "lay_size": 1}, "2": {"back": 13.0, "lay": 13.5, "back_size": 1, "lay_size": 1},
        "3": {"back": 6.6, "lay": 6.8, "back_size": 1, "lay_size": 1}}}}
    m = f._snapshot(prices)["matches"]["1.5"]
    assert m["books"] == {}                                     # niente auto-riferimento: il "giusto" arriva da fuori
    assert m["exchange"]["home"]["back"] == 1.25 and m["exchange"]["away"]["back"] == 13.0 and m["exchange"]["draw"]["back"] == 6.6
    assert m["betfair"]["selection_ids"] == {"home": 1, "away": 2, "draw": 3}


def test_telegram_sends_on_bet_and_breaker(monkeypatch):
    import threading
    from betbot import local_settings, notifier
    sent = []
    monkeypatch.setattr(local_settings, "load", lambda: {**local_settings.DEFAULTS})
    monkeypatch.setattr(notifier, "channel", lambda: {"token": "1:x", "chat_id": "42"})
    monkeypatch.setattr(notifier, "call", lambda token, method, payload=None, timeout=10: sent.append(payload["text"]))

    class Now(threading.Thread):                    # thread eseguito subito, per il test
        def start(self):
            self.run()
    monkeypatch.setattr(notifier.threading, "Thread", Now)
    n = notifier.Notifier()
    n.on_event("Pietro · Banco", "INFO", "bet", "[PAPER] Puntata #1: 2,00 € su Inter")
    n.on_event("Bruno · Risk Manager", "WARN", "circuit", "Circuit breaker: 6 perdite di fila")
    n.on_event("Sara · Quote", "INFO", "scan", "routine")
    assert len(sent) == 2 and "Puntata #1" in sent[0] and "Circuit breaker" in sent[1]


def test_telegram_commands_can_only_inform_or_brake(office):
    from betbot.telegram_bot import TelegramCommands
    tc = TelegramCommands(office)
    office.store.set("risk_state", office.risk.portfolio_state())
    assert "Bankroll" in tc.handle("/stato")
    assert "Comandi" in tc.handle("/aiuto")
    assert tc.handle("ciao") is None
    tc.handle("/pausa 30")
    assert office.risk.portfolio_state()["cooldown_until"]
    tc.handle("/riprendi")
    assert not office.risk.portfolio_state()["cooldown_until"]
    tc.handle("/stop")
    assert office.store.get("kill_switch")
    d = office.risk.evaluate(_proposal(), _snap(), office.risk.portfolio_state())
    assert not d["approved"]


def test_reference_odds_are_merged_onto_betfair_matches():
    from betbot.feeds import merge_reference
    bf = {"1.9": {"match_id": "1.9", "home": "Inter", "away": "Lecce", "kickoff": "2030-01-01T18:00:00+00:00",
                  "books": {}, "live_books": {}, "exchange": {"home": {"back": 1.3}}, "odds_ts": 100.0}}
    ref = {"e1": {"home": "FC Internazionale", "away": "US Lecce", "kickoff": "2030-01-01T18:30:00+00:00",
                  "books": {"Pinnacle": {"home": 1.28, "draw": 5.8, "away": 11.0},
                            "Betfair": {"home": 1.31, "draw": 6.0, "away": 12.0}},
                  "live_books": {}, "closing": None, "odds_ts": 90.0}}
    assert merge_reference(bf, ref) == 1
    assert set(bf["1.9"]["books"]) == {"Pinnacle"}                  # gli exchange non sono un riferimento esterno


def test_s05_needs_value_net_of_commission_and_high_probability():
    from betbot.strategies.s05_favoriti_exchange_v1 import propose
    m = {"match_id": "M1", "sport": "tennis_atp", "league": "ATP", "home": "Sinner", "away": "X", "status": "SCHEDULED",
         "kickoff": "2030-01-01T18:00:00+00:00",
         "books": {"Pinnacle": {"home": 1.20, "away": 4.8}, "Bet365": {"home": 1.18, "away": 4.5}}, "odds_ts": 1.0,
         "exchange": {"home": {"back": 1.30, "lay": 1.31, "back_size": 500, "lay_size": 500}}}
    snap = {"ts": 0, "sim_time": 1893520800 - 3600 * 5, "matches": {"M1": m}}
    props = propose(snap, {}, {})
    assert len(props) == 1 and props[0]["selection"] == "home" and props[0]["edge"] > 0.01 and props[0]["fair_prob"] >= 0.75
    m["exchange"]["home"]["back"] = 1.22                             # sotto il giusto dopo la commissione: niente
    assert propose(snap, {}, {}) == []


def test_recorder_and_replay_roundtrip(tmp_path):
    from betbot.feeds.mock import MockFeed
    from betbot.feeds.recorder import Recorder, ReplayFeed
    from betbot.config import load_settings
    s = load_settings()
    s["feed"]["mock"]["seed"] = 2
    f = MockFeed(s)
    f.speed = 1.0
    rec = Recorder(tmp_path)
    for _ in range(5):
        rec.write(f.snapshot())
        f.advance(120)
    rp = ReplayFeed(sorted(tmp_path.glob("*.jsonl.gz")))
    n = 0
    while rp.advance_one():
        snap = asyncio.run(rp.fetch())
        assert snap["matches"] and "replay" in snap["health"]["source"]
        n += 1
    assert n == 5


def test_trade_strategy_exits_before_kickoff():
    from betbot.strategies.s07_scalping_prepartita_v1 import manage
    bet = {"id": 1, "match_id": "M1", "selection": "home", "odds": 2.0}
    m = {"status": "SCHEDULED", "kickoff": "2030-01-01T18:00:00+00:00",
         "exchange": {"home": {"back": 2.0, "lay": 2.02, "back_size": 100, "lay_size": 100}}}
    snap = {"ts": 0, "sim_time": 1893520800 - 120, "matches": {"M1": m}}           # 2 minuti all'inizio
    acts = manage([bet], snap, {}, {})
    assert acts and acts[0]["action"] == "hedge" and "inizio" in acts[0]["reason"]
