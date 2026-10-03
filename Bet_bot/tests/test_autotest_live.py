"""Test automatici all'avvio, pulsante live della dashboard e riavvio da solo."""
import json

import pytest


@pytest.fixture
def at(tmp_path, monkeypatch):
    from betbot import autotest
    monkeypatch.setattr(autotest, "STATE", tmp_path / "autotest.json")
    monkeypatch.setattr(autotest, "RUNTIME_DIR", tmp_path)
    return autotest


def _settings(provider="betfair", reference="odds_api", **auto):
    return {"feed": {"provider": provider, "reference": reference}, "autotest": auto}


def test_plan_respects_rhythm_and_credits(at, tmp_path):
    names = [n for n, _ in at.plan(_settings())]
    assert names == ["orizzonti", "allenamento", "multiple", "backtest", "sport", "nfl", "test_rapido"]
    assert [n for n, _ in at.plan(_settings(provider="mock"))] == ["orizzonti", "allenamento", "multiple", "backtest", "nfl"]     # niente crediti col mock
    (tmp_path / "odds_api_budget.json").write_text(json.dumps({"remaining": 120}))
    assert "test_rapido" not in [n for n, _ in at.plan(_settings())]                               # pochi crediti
    at._mark("orizzonti")
    for n in ("allenamento", "multiple", "backtest", "nfl"):
        at._mark(n)
    assert [n for n, _ in at.plan(_settings(provider="mock"))] == []                               # già fatti
    assert at.plan(_settings(enabled=False)) == []


def test_start_runs_in_background_and_reports(at, monkeypatch):
    import threading
    said = []
    monkeypatch.setattr(at, "_run_one", lambda name: f"fatto {name}")

    class Now(threading.Thread):
        def start(self):
            self.run()
    monkeypatch.setattr(at.threading, "Thread", Now)
    office = type("O", (), {"settings": _settings(provider="mock"),
                            "coach": type("C", (), {"say": staticmethod(lambda msg, *a, **k: said.append(msg))})()})()
    assert at.start(office) == ["orizzonti", "allenamento", "multiple", "backtest", "nfl"]
    assert any("fatto orizzonti" in m for m in said) and not at.plan(_settings(provider="mock"))


def test_dashboard_live_on_needs_si_and_restarts(monkeypatch, tmp_path):
    from betbot import live_switch, server
    monkeypatch.setattr(live_switch, "RESTART_FILE", tmp_path / "riavvio.richiesta")
    calls = {}

    def fake_enable(ask, out, client=None):
        calls["confirm"] = ask("?")
        if calls["confirm"] != "SI":
            out("Nessuna modifica: resti in paper.")
            return False
        return True
    monkeypatch.setattr(live_switch, "enable", fake_enable)
    with pytest.raises(ValueError, match="resti in paper"):
        server.handle_action("/api/live/on", {"confirm": "si"})
    assert not (tmp_path / "riavvio.richiesta").exists()
    msg = server.handle_action("/api/live/on", {"confirm": "SI"})["message"]
    assert "ACCESE" in msg and (tmp_path / "riavvio.richiesta").exists()
    (tmp_path / "riavvio.richiesta").unlink()
    monkeypatch.setattr(live_switch, "disable", lambda out: True)
    assert "spente" in server.handle_action("/api/live/off", {})["message"] and (tmp_path / "riavvio.richiesta").exists()


def test_office_stops_on_restart_request(tmp_path, monkeypatch):
    import asyncio

    from betbot import core, local_settings
    monkeypatch.setattr(local_settings, "load", lambda: {**local_settings.DEFAULTS})
    from betbot.config import load_settings
    from betbot.feeds.mock import MockFeed
    st = load_settings()
    office = core.SportOffice(db_path=tmp_path / "o.db", feed=MockFeed(st))
    office.settings["cycle_seconds"] = 3600
    office.settings["fast_seconds"] = 0.01
    flag = tmp_path / "riavvio.richiesta"
    monkeypatch.setattr(core, "RESTART_FILE", flag)
    monkeypatch.setattr(core, "STOP_FILE", tmp_path / "ferma.richiesta")
    flag.write_text("live")
    asyncio.run(asyncio.wait_for(office.run_forever(), 30))          # esce da solo: niente attesa di un'ora
    assert flag.exists()                                               # lo cancella `avvia` dopo, per uscire con 3


def test_allenamento_autopsy_on_synthetic_matches():
    from datetime import datetime, timedelta

    from betbot import allenamento as A
    ms = []
    for i in range(60):
        # Betfair paga la casa 2,30 quando Pinnacle la dà a 2,20: S10 la sceglie; il risultato alterna
        ms.append({"date": datetime(2025, 1, 1) + timedelta(days=i), "div": "I1", "season": "2425", "home": f"H{i}",
                   "away": f"A{i}", "res": i % 3, "ps": (2.20, 3.40, 3.40), "psc": (2.10, 3.50, 3.50),
                   "bfe": (2.30, 3.40, 3.40)})
    bets = A.picks(ms)
    s10 = [b for b in bets if b["strategy"] == "S10 divertimento"]
    assert s10 and all(b["cause"] in A.CAUSE_TXT for b in s10)
    r = A.reasoning(s10)
    assert r["n"] == len(s10) and r["ragionamenti"]
    cal = A.calibration(ms)
    assert cal["partite"] == 60 and 0 <= cal["azzeccate"] <= 1
    assert "Allenamento" in A.report({"anni": 1, "partite": 60, "calibrazione": cal, "con_betfair": 60,
                                      "strategie": {"S10 divertimento": r}})


def test_tennis_training_on_synthetic_file(tmp_path):
    import pandas as pd

    from betbot import allenamento as A
    rows = []
    for i in range(80):
        # il favorito (1,60 per Pinnacle) vince 2 volte su 3; Betfair paga un filo meglio
        fav_wins = i % 3 != 0
        w, l = ("Sinner", f"Avv{i}") if fav_wins else (f"Avv{i}", "Sinner")
        psw, psl = (1.60, 2.45) if fav_wins else (2.45, 1.60)
        rows.append({"Date": f"2025-0{1 + i % 9}-{1 + i % 27:02d}", "Winner": w, "Loser": l, "Comment": "Completed",
                     "Series": "ATP250", "Surface": "Hard", "PSW": psw, "PSL": psl, "BFEW": psw + 0.04, "BFEL": psl + 0.06})
    rows.append({"Date": "2025-03-03", "Winner": "X", "Loser": "Y", "Comment": "Retired", "PSW": 1.5, "PSL": 2.6})
    f = tmp_path / "tennis_atp_2025.xlsx"
    pd.DataFrame(rows).to_excel(f, index=False)
    ms = A.tennis_matches([f])
    assert len(ms) == 80 and all(m["bfe"] for m in ms)
    t = A.tennis_report(ms)
    assert 0.6 < t["favorito_vince"] < 0.7 and t["s10"]["n"] > 0
    txt = A.report({"anni": 1, "partite": 0, "calibrazione": {"azzeccate": 0.5, "partite": 1, "attese": 0.5, "fasce": []},
                    "con_betfair": 0, "strategie": {}, "tennis": t})
    assert "Tennis" in txt and "S10 sul tennis" in txt
