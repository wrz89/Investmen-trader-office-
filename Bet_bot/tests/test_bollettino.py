"""Il bollettino di Leo: classifica sui prezzi veri, soldi veri, misure e proposta (mai un cambio automatico)."""
import json
from datetime import datetime

import pytest

from betbot import bollettino as B
from betbot.bankroll import TZ


@pytest.fixture
def office(tmp_path, monkeypatch):
    from betbot import local_settings
    monkeypatch.setattr(local_settings, "load", lambda: {**local_settings.DEFAULTS})
    monkeypatch.setattr(B, "REPORTS_DIR", tmp_path / "reports")
    from betbot.config import load_settings
    from betbot.core import SportOffice
    from betbot.feeds.mock import MockFeed
    st = load_settings()
    st["feed"]["mock"]["seed"] = 5
    return SportOffice(db_path=tmp_path / "o.db", feed=MockFeed(st))


def test_bulletin_ranks_and_proposes_only_what_passed(office, monkeypatch):
    from betbot import esame
    fake = {"S05_favoriti_exchange_v3": ("PRONTA", 230, 0.012, 0.003), "S09_lay_valore_v1": ("BOCCIATA", 210, -0.02, -0.03)}

    def ev(store, sid):
        v, n, clv, lo = fake.get(sid, ("IN ESAME", 12, None, None))
        return {"strategy_id": sid, "verdict": v, "n": n, "need": 200, "clv": clv, "clv_lo": lo,
                "clv_hi": None if lo is None else clv + 0.01, "roi": clv, "win_rate": None, "reasons": [], "criteria": {}}
    monkeypatch.setattr(esame, "evaluate", ev)
    office.store.execute("INSERT INTO bets (ts, mode, strategy_id, match_id, market, selection, bookmaker, odds, stake, status, pnl) "
                         "VALUES ('2099-01-01T10:00:00+00:00', 'live', 'S10_divertimento_v1', 'X', 'h2h', 'home', 'Betfair', "
                         "1.8, 2, 'WON', 1.53)")
    st = {**office.settings, "mode": "live", "live_strategies": ["S10_divertimento_v1"]}
    b = B.build(office.store, st)
    assert b["esame"][0]["strategy_id"] == "S05_favoriti_exchange_v3" and b["esame"][-1]["verdict"] == "BOCCIATA"
    assert b["proposte"] == ["S05_favoriti_exchange_v3"]
    t = B.save(b)
    assert "Bollettino di Leo" in t and "+1.53 €" in t and "non lo fa da solo" in t
    assert "test rapido: non ancora fatto" in t
    assert office.settings.get("live_strategies") in ([], None)            # nessun cambio automatico


def test_bulletin_once_a_day_from_8(office):
    assert not B.due(office.store, datetime(2026, 10, 2, 7, 30, tzinfo=TZ))
    assert B.due(office.store, datetime(2026, 10, 2, 8, 5, tzinfo=TZ))
    office.store.set("bollettino_day", "2026-10-02")
    assert not B.due(office.store, datetime(2026, 10, 2, 20, 0, tzinfo=TZ))
    assert B.due(office.store, datetime(2026, 10, 3, 9, 0, tzinfo=TZ))


def test_leo_sends_the_bulletin_in_his_cycle(office, monkeypatch):
    monkeypatch.setattr(B, "due", lambda store, now=None: True)
    (B.REPORTS_DIR).mkdir(parents=True, exist_ok=True)
    (B.REPORTS_DIR / "test_rapido.json").write_text(json.dumps({"ts": 1.9e9, "verdetto": "NON ANCORA CHIARO",
        "sport": {"tennis": {"verdetto": "SEGNALE ASSENTE"}}}), encoding="utf-8")
    office.coach._bulletin()
    ev = office.store.query("SELECT message FROM events WHERE kind='report' ORDER BY id DESC LIMIT 1")
    assert ev and "Bollettino di Leo" in ev[0]["message"] and "tennis: SEGNALE ASSENTE" in ev[0]["message"]
