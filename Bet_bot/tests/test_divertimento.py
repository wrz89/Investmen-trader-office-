"""S10 Divertimento (2 € fissi, max 5 al giorno, una aperta) e passaggio al live con conferma scritta."""
from datetime import datetime, timezone

import pytest
import yaml

from betbot.strategies import s10_divertimento_v1 as S10

T0 = 1_900_000_000.0


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def _match(mid, sport, ex, books=None, mins=60, league="Serie A", status="SCHEDULED"):
    return {"match_id": mid, "sport": sport, "league": league, "home": "Casa", "away": "Ospite", "kickoff": _iso(T0 + mins * 60),
            "status": status, "exchange": ex, "books": books or {}, "commission": 0.045, "odds_ts": T0}


def _snap(*ms):
    return {"ts": T0, "sim_time": T0, "matches": {m["match_id"]: m for m in ms}}


BOOK = lambda back, lay, size=50.0: {"back": back, "lay": lay, "back_size_best": size, "back_size": size}


def test_picks_the_best_priced_selection_across_sports():
    tennis = _match("T1", "tennis", {"home": BOOK(1.80, 1.82), "away": BOOK(2.20, 2.24)}, league="ATP Parigi")
    # NFL con Pinnacle: casa giusta al 60% (1,667), Betfair la paga 1,72 → EV positivo, vince la scelta
    nfl = _match("N1", "americanfootball", {"home": BOOK(1.72, 1.74), "away": BOOK(2.44, 2.5)},
                 books={"Pinnacle": {"home": 1.64, "away": 2.42}, "Bet365": {"home": 1.62, "away": 2.40}}, league="NFL")
    out = S10.propose(_snap(tennis, nfl), {}, {})
    assert [p["match_id"] for p in out] == ["N1", "T1"]                   # la migliore prima, una per partita
    p = out[0]
    assert p["match_id"] == "N1" and p["selection"] == "home" and p["fun"] and p["edge"] > 0
    assert p["strategy_id"] == "S10_divertimento_v1"


def test_uses_betfair_mid_price_when_no_reference_and_skips_bad_books():
    ok = _match("T1", "tennis", {"home": BOOK(1.80, 1.82), "away": BOOK(2.20, 2.24)}, league="ATP Parigi")
    p = S10.propose(_snap(ok), {}, {})[0]
    assert p["prob_source"] == "exchange" and -0.03 < p["edge"] < 0 and p["spread"] < 0.02
    wide = _match("T2", "tennis", {"home": BOOK(1.80, 1.95), "away": BOOK(2.0, 2.3)})
    thin = _match("T3", "tennis", {"home": BOOK(1.80, 1.82, size=4), "away": BOOK(2.20, 2.24, size=4)})
    itf = _match("T4", "tennis", {"home": BOOK(1.80, 1.82), "away": BOOK(2.20, 2.24)}, league="ITF M25 Antalya")
    late = _match("T5", "tennis", {"home": BOOK(1.80, 1.82), "away": BOOK(2.20, 2.24)}, mins=5)
    fav = _match("T6", "tennis", {"home": BOOK(1.10, 1.11), "away": BOOK(10.0, 10.5)})
    assert S10.propose(_snap(wide, thin, itf, late, fav), {}, {}) == []


@pytest.fixture
def office(tmp_path, monkeypatch):
    from betbot import local_settings
    monkeypatch.setattr(local_settings, "load", lambda: {**local_settings.DEFAULTS})
    from betbot.config import load_settings
    from betbot.core import SportOffice
    from betbot.feeds.mock import MockFeed
    st = load_settings()
    st["feed"]["mock"]["seed"] = 5
    return SportOffice(db_path=tmp_path / "o.db", feed=MockFeed(st))


def _fun(**kw):
    p = {"strategy_id": "S10_divertimento_v1", "strategy_status": "ATTIVA", "match_id": "M1", "market_id": "M1",
         "league": "ATP", "label": "Sinner - Alcaraz · Sinner", "market": "h2h", "selection": "home", "bookmaker": "Betfair",
         "odds": 1.8, "fair_prob": 0.545, "edge": -0.012, "commission": 0.045, "n_books": 0, "dispersion": None,
         "prob_source": "exchange", "spread": 0.011, "fun": True, "live": False, "odds_ts": 1000.0, "reason": "test"}
    p.update(kw)
    return p


def _rsnap():
    return {"ts": 1000.0, "sim_time": 1000.0, "time_scale": 1.0, "races": {},
            "matches": {"M1": {"match_id": "M1", "status": "SCHEDULED", "home": "Sinner", "away": "Alcaraz",
                               "exchange": {"home": {"back": 1.8, "lay": 1.82, "back_size": 500.0, "lay_size": 300.0}}}},
            "health": {"error_rate": 0, "source": "mock"}}


def test_risk_allows_fun_at_minimum_stake_but_not_bad_prices(office):
    state = office.risk.portfolio_state()
    d = office.risk.evaluate(_fun(), _rsnap(), state)
    assert d["approved"] and d["stake"] == 2.0, d["reasons"]
    assert not office.risk.evaluate(_fun(edge=-0.04), _rsnap(), state)["approved"]          # perde troppo
    assert not office.risk.evaluate(_fun(spread=0.05), _rsnap(), state)["approved"]         # libro largo
    # una S05 con lo stesso EV negativo resta vietata: la regola vale solo per il divertimento
    assert not office.risk.evaluate(_fun(fun=False, strategy_id="S05_favoriti_exchange_v2"), _rsnap(), state)["approved"]


def test_fun_caps_per_day_and_open(office):
    from betbot.store import now_iso
    ins = ("INSERT INTO bets (ts, mode, strategy_id, match_id, league, market, selection, bookmaker, odds, stake, status) "
           "VALUES (?, 'paper', 'S10_divertimento_v1', ?, 'X', 'h2h', 'home', 'Betfair', 1.8, 2, ?)")
    office.store.execute(ins, (now_iso(), "A", "OPEN"))
    d = office.risk.evaluate(_fun(), _rsnap(), office.risk.portfolio_state())
    assert not d["approved"] and any("aperte" in r for r in d["reasons"])
    office.store.execute("UPDATE bets SET status='LOST', pnl=-2 WHERE match_id='A'")
    for mid in ("B", "C", "D", "E"):
        office.store.execute(ins, (now_iso(), mid, "WON"))
    d = office.risk.evaluate(_fun(), _rsnap(), office.risk.portfolio_state())
    assert not d["approved"] and any("oggi < 5" in r for r in d["reasons"])


# ── vai_live / torna_paper ────────────────────────────────────────────────
class FakeClient:
    def __init__(self, balance=30.0):
        self.balance, self.placed, self.cancelled = balance, [], []

    def login(self):
        pass

    def account_funds(self):
        return {"availableToBetBalance": self.balance}

    def catalogue(self, *a, **k):
        return [{"marketId": "1.5", "event": {"name": "Inter v Lecce"}, "runners": [{"selectionId": 7}]}]

    def place(self, market_id, selection_id, side, price, size, **k):
        self.placed.append((market_id, side, price, size))
        return {"bet_id": "B1"}

    def cancel(self, market_id, bet_id):
        self.cancelled.append(bet_id)

    def current_orders(self, order_refs=None):
        return []


@pytest.fixture
def local(tmp_path, monkeypatch):
    from betbot import config, live_switch, local_settings
    store = {**local_settings.DEFAULTS, "betfair": {**local_settings.DEFAULTS["betfair"], "app_key": "k", "username": "u"}}
    monkeypatch.setattr(local_settings, "load", lambda: store)
    monkeypatch.setattr(local_settings, "save", lambda s: store.update(s))
    over = tmp_path / "impostazioni.yaml"
    over.write_text("feed:\n  reference: odds_api\n", encoding="utf-8")
    monkeypatch.setattr(live_switch, "LOCAL_OVERRIDE", over)
    return store, over


def test_live_needs_the_written_yes(local):
    from betbot import live_switch
    store, over = local
    c = FakeClient()
    assert not live_switch.enable(ask=lambda q: "si", out=lambda *a: None, client=c)
    assert not store["betfair"]["live_enabled"] and "mode" not in over.read_text() and not c.placed


def test_live_on_and_off(local):
    from betbot import live_switch
    store, over = local
    c = FakeClient()
    said = []
    assert live_switch.enable(ask=lambda q: "SI", out=said.append, client=c)
    assert c.placed == [("1.5", "BACK", 1000.0, 2.0)] and c.cancelled == ["B1"]        # ordine di prova annullato
    bf = store["betfair"]
    assert bf["live_enabled"] and bf["test_done"] and bf["verified"]
    cfg = yaml.safe_load(over.read_text(encoding="utf-8"))
    assert cfg["mode"] == "live" and cfg["execution"]["provider"] == "betfair"
    assert cfg["live_strategies"] == ["S10_divertimento_v1"] and cfg["feed"]["reference"] == "odds_api"   # il resto resta
    assert any("30.00 €" in x for x in said)
    from betbot.execution import Gates
    assert live_switch.disable(out=lambda *a: None)
    cfg = yaml.safe_load(over.read_text(encoding="utf-8"))
    assert cfg["mode"] == "paper" and cfg["live_strategies"] == [] and not store["betfair"]["live_enabled"]
    assert Gates.live_allowed(cfg, "S10_divertimento_v1")[0] is False


def test_live_refused_without_money(local):
    from betbot import live_switch
    store, over = local
    assert not live_switch.enable(ask=lambda q: "SI", out=lambda *a: None, client=FakeClient(balance=1.0))
    assert not store["betfair"]["live_enabled"]


def test_stale_reference_falls_back_to_betfair_price():
    m = _match("N1", "americanfootball", {"home": BOOK(1.72, 1.74), "away": BOOK(2.44, 2.5)},
               books={"Pinnacle": {"home": 1.64, "away": 2.42}, "Bet365": {"home": 1.62, "away": 2.40}}, league="NFL")
    m["ref_ts"] = T0 - 4 * 3600                                          # Pinnacle di 4 ore fa
    p = S10.propose(_snap(m), {}, {})[0]
    assert p["prob_source"] == "exchange" and p["ref_ts"] is None


def test_delayed_prices_go_live_only_for_fun_backs(monkeypatch):
    from betbot import execution
    monkeypatch.setattr(execution.Gates, "live_allowed", staticmethod(lambda s, sid: (True, "ok")))
    monkeypatch.setattr(execution, "exchange_target", lambda p, snap: True)
    ex = execution.Executor({"mode": "live", "execution": {}})
    snap = {"health": {"delayed": True}}
    assert ex.route({"strategy_id": "S10_divertimento_v1", "fun": True}, snap) == "live"
    assert ex.route({"strategy_id": "S05_favoriti_exchange_v2"}, snap) == "shadow"
    assert ex.route({"strategy_id": "S10_divertimento_v1", "fun": True, "side": "LAY"}, snap) == "shadow"
    assert ex.route({"strategy_id": "S05_favoriti_exchange_v2"}, {"health": {}}) == "live"


def test_preview_lists_candidates_and_the_cost_of_opening_many():
    from betbot import anteprima as A
    ms = [_match(f"T{i}", "tennis", {"home": BOOK(1.80 + i / 100, 1.82 + i / 100), "away": BOOK(2.2, 2.24)},
                 league="ATP Parigi") for i in range(4)]
    cands = S10.candidates(_snap(*ms))
    assert len(cands) == 4 and cands[0]["edge"] >= cands[-1]["edge"] and cands[0]["kickoff"]
    t1, t3 = A.together(cands, 1), A.together(cands, 3)
    assert t3["worst"] == -6.0 and t3["p_all_lost"] < t1["p_all_lost"] and t3["ev"] < t1["ev"] < 0
    txt = A.report(cands, 30.0, {"max_daily_loss_eur": 4, "kill_below_bankroll": 20}, T0)
    assert "3 insieme" in txt and "Tennis / ATP Parigi" in txt and "30.00 €" in txt
    assert "Nessuna scelta" in A.report([], None, {}, T0)
