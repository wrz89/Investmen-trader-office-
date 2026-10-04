"""S10 Divertimento (2 € fissi, max 10 al giorno, una aperta) e passaggio al live con conferma scritta."""
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
    assert d["approved"] and 2.0 <= d["stake"] <= 5.0, d["reasons"]       # 5 € fissi, tagliati al 20% del bankroll (30 €)
    assert not office.risk.evaluate(_fun(edge=-0.04), _rsnap(), state)["approved"]          # perde troppo
    assert not office.risk.evaluate(_fun(spread=0.05), _rsnap(), state)["approved"]         # libro largo
    # una S05 con lo stesso EV negativo resta vietata: la regola vale solo per il divertimento
    assert not office.risk.evaluate(_fun(fun=False, strategy_id="S05_favoriti_exchange_v2"), _rsnap(), state)["approved"]


def test_fun_skips_market_moving_against(office, monkeypatch):
    monkeypatch.setattr(office.sentiment, "verdict", lambda p: {"level": "caution", "reason": "mercato in uscita: probabilità -4.4%"})
    d = office.risk.evaluate(_fun(), _rsnap(), office.risk.portfolio_state())
    assert not d["approved"] and any("Mercato non in uscita" in r for r in d["reasons"])


def test_fun_caps_per_day_and_open(office):
    from betbot.store import now_iso
    ins = ("INSERT INTO bets (ts, mode, strategy_id, match_id, league, market, selection, bookmaker, odds, stake, status) "
           "VALUES (?, 'paper', 'S10_divertimento_v1', ?, 'X', 'h2h', 'home', 'Betfair', 1.8, 2, ?)")
    for mid in ("A", "A2", "A3", "A4", "A5", "A6"):                 # sei aperte insieme: il massimo
        office.store.execute(ins, (now_iso(), mid, "OPEN"))
    d = office.risk.evaluate(_fun(), _rsnap(), office.risk.portfolio_state())
    assert not d["approved"] and any("aperte < 6" in r for r in d["reasons"])
    office.store.execute("UPDATE bets SET status='LOST', pnl=-2 WHERE status='OPEN'")
    for mid in "BCDEFGHIJKLMNOPQRST":                               # 19 + 6 chiuse = oltre le 20 di oggi
        office.store.execute(ins, (now_iso(), mid, "WON"))
    d = office.risk.evaluate(_fun(), _rsnap(), office.risk.portfolio_state())
    assert not d["approved"] and any("oggi < 20" in r for r in d["reasons"])


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
    assert cfg["live_strategies"] == ["S10_divertimento_v2"] and cfg["feed"]["reference"] == "odds_api"   # il resto resta
    assert any("30.00 €" in x for x in said)
    from betbot.execution import Gates
    assert live_switch.disable(out=lambda *a: None)
    cfg = yaml.safe_load(over.read_text(encoding="utf-8"))
    assert cfg["mode"] == "paper" and cfg["live_strategies"] == [] and not store["betfair"]["live_enabled"]
    assert Gates.live_allowed(cfg, "S10_divertimento_v2")[0] is False


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
    assert ex.route({"strategy_id": "S10_divertimento_v2", "fun": True, "side": "LAY"}, snap) == "live"     # lay fill-or-kill
    assert ex.route({"strategy_id": "S09_lay_valore_v1", "side": "LAY"}, snap) == "shadow"
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


# ── S10 v2: prima il lay di valore ────────────────────────────────────────
def _lay_match(ref_ts=None):
    ex = {"home": {"back": 1.70, "lay": 1.72, "back_size_best": 80, "lay_size_best": 60},
          "draw": {"back": 3.9, "lay": 4.0, "back_size_best": 40, "lay_size_best": 40},
          "away": {"back": 4.6, "lay": 4.7, "back_size_best": 30, "lay_size_best": 30}}
    # Pinnacle dà l'ospite a 5,6 (≈17%): bancarlo a 4,7 ha valore
    m = _match("L1", "soccer_italy_serie_a", ex, books={"Pinnacle": {"home": 1.62, "draw": 4.2, "away": 5.6}})
    m["ref_ts"] = ref_ts or T0 - 600
    return m


LOOSE = {"min_book_eur": 10.0, "min_ev": -0.03}       # le soglie di prima: qui si prova la logica, non la stretta


def test_v2_live_is_strict_on_price_and_liquidity():
    from betbot.strategies import s10_divertimento_v2 as V2
    thin = _match("T2", "tennis", {"home": BOOK(1.80, 1.82), "away": BOOK(2.20, 2.24)}, league="ATP Parigi")
    assert V2.DEFAULTS["min_ev"] == -0.025 and V2.DEFAULTS["min_book_eur"] == 100.0
    assert all(p["edge"] >= -0.025 for p in V2.propose(_snap(thin), {}, {}))


def test_v2_prefers_value_lays_then_backs():
    from betbot.strategies import s10_divertimento_v2 as V2
    tennis = _match("T1", "tennis", {"home": BOOK(1.80, 1.82), "away": BOOK(2.20, 2.24)}, league="ATP Parigi")
    out = V2.propose(_snap(tennis, _lay_match()), LOOSE, {})
    assert out[0]["side"] == "LAY" and out[0]["selection"] == "LAY:away" and out[0]["fun"]
    assert out[0]["strategy_id"] == "S10_divertimento_v2" and out[0]["edge"] >= 0.02
    assert out[1]["match_id"] == "T1" and out[1].get("side") != "LAY"
    stale = V2.propose(_snap(_lay_match(ref_ts=T0 - 5 * 3600)), {}, {})            # Pinnacle vecchio: niente lay
    assert all(p.get("side") != "LAY" for p in stale)


def test_risk_sizes_fun_lay_at_minimum_backer(office):
    from betbot.strategies import s10_divertimento_v2 as V2
    p = V2.propose(_snap(_lay_match()), {}, {})[0]
    p = {**p, "strategy_status": "ATTIVA", "odds_ts": 1000.0, "ref_ts": 1000.0}
    snap = {"ts": 1000.0, "sim_time": 1000.0, "time_scale": 1.0, "races": {}, "health": {"error_rate": 0, "source": "mock"},
            "matches": {"L1": {"match_id": "L1", "status": "SCHEDULED", "home": "Casa", "away": "Ospite",
                               "exchange": {"away": {"back": 4.6, "lay": 4.7, "back_size": 300.0, "lay_size": 300.0}}}}}
    d = office.risk.evaluate(p, snap, office.risk.portfolio_state())
    assert d["approved"], d["reasons"]
    assert 0.5 * (4.7 - 1) - 0.02 <= d["stake"] <= 5.0                            # rischio fino a 5 €, mai sotto 0,50 € del backer


def test_live_settings_move_v1_to_v2(tmp_path, monkeypatch):
    from betbot import config
    over = tmp_path / "impostazioni.yaml"
    over.write_text("mode: live\nlive_strategies: [S10_divertimento_v1]\n", encoding="utf-8")
    monkeypatch.setattr(config, "LOCAL_OVERRIDE", over)
    assert config.load_settings()["live_strategies"] == ["S10_divertimento_v2"]


def test_deposit_adds_capital_not_profit(tmp_path):
    from betbot import live_switch
    from betbot.bankroll import Bankroll
    from betbot.store import Store
    st = Store(tmp_path / "live.db")
    br = Bankroll(st, 30.0)
    st.set("day_start:" + __import__("betbot.bankroll", fromlist=["today"]).today(), 30.0)
    c = FakeClient(balance=60.0)
    said = []
    assert not live_switch.deposit(ask=lambda q: "no", out=said.append, client=c, store=st)
    assert br.total == 30.0
    assert live_switch.deposit(ask=lambda q: "SI", out=said.append, client=c, store=st)
    assert br.total == 60.0 and br.initial_capital == 60.0 and br.profits == 0.0
    assert float(st.get("peak_bankroll")) == 60.0
    assert any("30.00" in x for x in said)                           # nuovo stop in live: picco 60 × 0,5
    assert not live_switch.deposit(ask=lambda q: "SI", out=said.append, client=c, store=st)    # niente di nuovo


def test_misura_shadows_every_suitable_match_without_daily_cap():
    from betbot.strategies import s10_misura_v1 as M
    ms = [_match(f"T{i}", "tennis", {"home": BOOK(1.80, 1.82), "away": BOOK(2.20, 2.24)}, league="ATP Parigi")
          for i in range(8)]
    out = M.propose(_snap(*ms, _lay_match()), {}, {"store": None})
    assert len(out) == 9 and out[0]["side"] == "LAY"                # prima il lay di valore, poi una per partita
    assert all(p["strategy_id"] == "S10_misura_v1" and not p.get("fun") for p in out)


def test_leo_uses_betfair_close_when_pinnacle_is_stale():
    import json

    from betbot.agents.coach import exchange_fair
    f = exchange_fair({"home": {"back": 1.80, "lay": 1.82}, "away": {"back": 2.20, "lay": 2.24}})
    assert abs(sum(f.values()) - 1) < 1e-9 and f["home"] > f["away"]
    assert exchange_fair({"home": {"back": 1.8, "lay": 2.5}, "away": {"back": 2.2, "lay": 2.3}}) is None   # libro largo
    _ = json


def test_v2_lay_risk_never_above_two_euros():
    from betbot.strategies import s10_divertimento_v2 as V2
    ex = {"home": {"back": 1.60, "lay": 1.62, "back_size_best": 80, "lay_size_best": 60},
          "draw": {"back": 4.2, "lay": 4.3, "back_size_best": 40, "lay_size_best": 40},
          "away": {"back": 9.0, "lay": 9.4, "back_size_best": 30, "lay_size_best": 30}}
    # Pinnacle dà l'ospite a 14,0 (7%): bancarlo a 9,4 avrebbe valore, ma il rischio sarebbe 4,20 € → escluso
    m = _match("L2", "soccer_italy_serie_a", ex, books={"Pinnacle": {"home": 1.55, "draw": 4.6, "away": 14.0}})
    m["ref_ts"] = T0 - 600
    assert all(p.get("side") != "LAY" for p in V2.propose(_snap(m), {}, {}))
    assert all(0.5 * (p["odds"] - 1) <= 2.0 + 1e-9 for p in V2.propose(_snap(_lay_match()), {}, {}) if p.get("side") == "LAY")


def test_v2_live_skips_national_teams_but_misura_keeps_them():
    from betbot.strategies import s10_divertimento_v2 as V2
    from betbot.strategies import s10_misura_v1 as M
    ex = {"home": BOOK(1.80, 1.82), "away": BOOK(2.20, 2.24)}
    nat = _match("N1", "tennis", ex, league="International Friendlies")
    club = _match("C1", "tennis", ex, league="ATP Parigi")
    assert [p["match_id"] for p in V2.propose(_snap(nat, club), LOOSE, {})] == ["C1"]
    assert {p["match_id"] for p in M.propose(_snap(nat, club), {}, {})} == {"N1", "C1"}


def test_fun_allows_three_open_with_sixty_euros(office):
    from betbot.store import now_iso
    office.bankroll.add_capital(30.0)                                   # 60 €: rischio aperto fino a 6 €
    ins = ("INSERT INTO bets (ts, mode, strategy_id, match_id, league, market, selection, bookmaker, odds, stake, status) "
           "VALUES (?, 'paper', 'S10_divertimento_v2', ?, 'X', 'h2h', 'home', 'Betfair', 1.8, 2, 'OPEN')")
    office.store.execute("UPDATE kv SET value=value")                   # (nessun effetto: solo per chiarezza)
    for mid in ("A", "B"):
        office.store.execute(ins, (now_iso(), mid))
    st = office.risk.portfolio_state()
    d = office.risk.evaluate(_fun(strategy_id="S10_divertimento_v2"), _rsnap(), st)
    d = office.risk.evaluate(_fun(strategy_id="S10_divertimento_v2"), _rsnap(), st)
    assert d["approved"] and 2.0 <= d["stake"] <= 5.0, d["reasons"]       # con 60 € e il 20%: 12 € di rischio aperto
    for mid in ("C", "D", "E", "F"):                                      # sei aperte: 12 € di rischio = il tetto del 20%
        office.store.execute(ins, (now_iso(), mid))
    d = office.risk.evaluate(_fun(strategy_id="S10_divertimento_v2"), _rsnap(), office.risk.portfolio_state())
    assert not d["approved"]                                              # la settima no


def test_v2_lay_needs_fresh_pinnacle():
    from betbot.strategies import s10_divertimento_v2 as V2
    old = V2.propose(_snap(_lay_match(ref_ts=T0 - 2 * 3600)), {}, {})              # Pinnacle di 2 ore fa
    assert all(p.get("side") != "LAY" for p in old)
    fresh = V2.propose(_snap(_lay_match(ref_ts=T0 - 30 * 60)), {}, {})
    assert any(p.get("side") == "LAY" for p in fresh)


def test_misura_is_wider_than_live_for_lays():
    from betbot.strategies import s10_divertimento_v2 as V2
    from betbot.strategies import s10_misura_v1 as M
    ex = {"home": {"back": 1.60, "lay": 1.62, "back_size_best": 80, "lay_size_best": 60},
          "draw": {"back": 4.2, "lay": 4.3, "back_size_best": 40, "lay_size_best": 40},
          "away": {"back": 6.8, "lay": 7.0, "back_size_best": 30, "lay_size_best": 30}}
    m = _match("L3", "soccer_italy_serie_a", ex, books={"Pinnacle": {"home": 1.55, "draw": 4.6, "away": 18.0}})
    m["ref_ts"] = T0 - 2 * 3600                                          # Pinnacle di 2 ore fa, lay a 9,4
    assert all(p.get("side") != "LAY" for p in V2.propose(_snap(m), {}, {}))        # live: no
    lays = [p for p in M.propose(_snap(m), {}, {"store": None}) if p.get("side") == "LAY"]
    assert lays and lays[0]["odds"] > 5 and lays[0]["ref_ts"] == m["ref_ts"]        # misura: sì, con l'età registrata


def test_deposit_accepts_lowercase_si_and_explains_other_answers(tmp_path):
    from betbot import live_switch
    from betbot.bankroll import Bankroll
    from betbot.store import Store
    st = Store(tmp_path / "l.db")
    Bankroll(st, 30.0, allow_reset=False)
    c = type("C", (), {"account_funds": lambda self: {"availableToBetBalance": 60.0, "exposure": 0.0}})()
    said = []
    assert not live_switch.deposit(ask=lambda q: "ok", out=said.append, client=c, store=st)
    assert any("NESSUNA modifica" in x and "ok" in x for x in said)
    assert live_switch.deposit(ask=lambda q: "si", out=said.append, client=c, store=st)
    assert Bankroll(st, 0, allow_reset=False).total == 60.0


def test_backs_capped_per_day_so_lays_keep_the_budget(office):
    from betbot.store import now_iso
    from betbot.strategies import s10_divertimento_v2 as V2
    tennis = _match("T1", "tennis", {"home": BOOK(1.80, 1.82, 500), "away": BOOK(2.20, 2.24, 500)}, league="ATP Parigi")
    assert [p for p in V2.propose(_snap(tennis), LOOSE, {"store": office.store}) if p.get("side") != "LAY"]
    ins = ("INSERT INTO bets (ts, mode, strategy_id, match_id, league, market, selection, bookmaker, odds, stake, status) "
           "VALUES (?, 'live', 'S10_divertimento_v2', ?, 'X', 'h2h', 'home', 'Betfair', 1.8, 4, 'WON')")
    for i in range(8):
        office.store.execute(ins, (now_iso(), f"B{i}"))
    assert not [p for p in V2.propose(_snap(tennis), LOOSE, {"store": office.store}) if p.get("side") != "LAY"]


def test_stuck_open_bet_does_not_occupy_a_fun_slot(office, monkeypatch):
    import time
    from betbot import clock
    from betbot.agents.risk import _stuck
    from betbot.store import now_iso
    now = time.time()
    monkeypatch.setattr(clock, "now", lambda: now)
    from datetime import datetime, timezone
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat()
    office.store.execute("INSERT INTO matches(match_id, kickoff) VALUES('OLD', ?)", (iso(now - 10 * 3600),))
    office.store.execute("INSERT INTO matches(match_id, kickoff) VALUES('NEW', ?)", (iso(now - 1 * 3600),))
    assert _stuck(office.store, {"match_id": "OLD"}) and not _stuck(office.store, {"match_id": "NEW"})
