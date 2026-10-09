"""Matched betting: formule di copertura, partite adatte, registro dei bonus, pagina e API."""
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from betbot import matched as M


def test_qualificante_e_freebet_come_da_manuale():
    q = M.qualifying(2.0, 10, 2.04)
    assert q["lay_stake"] == 10.03 and q["liability"] == 10.43 and q["result"] == pytest.approx(-0.43, abs=0.01)
    assert abs(q["if_back_wins"] - q["if_lay_wins"]) < 0.02              # risultato uguale nei due casi
    f = M.freebet(5.0, 10, 5.2)
    assert f["lay_stake"] == 7.76 and f["result"] == pytest.approx(7.41, abs=0.01) and 0.73 < f["pct"] < 0.75
    sr = M.freebet(3.0, 10, 3.1, stake_returned=True)
    assert sr["result"] == pytest.approx(9.38, abs=0.01)
    with pytest.raises(ValueError):
        M.qualifying(1.0, 10, 2.0)


def test_lay_board_tiene_solo_lay_stretti_e_liquidi():
    now = time.time()
    ko = datetime.fromtimestamp(now + 3 * 3600, timezone.utc).isoformat()
    snap = {"ts": now, "matches": {
        "a": {"status": "SCHEDULED", "sport": "soccer", "home": "Inter", "away": "Milan", "league": "Serie A", "kickoff": ko,
              "exchange": {"home": {"back": 2.0, "lay": 2.02, "lay_size_best": 300},       # buono
                           "draw": {"back": 3.4, "lay": 3.7, "lay_size_best": 300},        # spread largo
                           "away": {"back": 4.0, "lay": 4.1, "lay_size_best": 5}}},        # poca liquidità
        "b": {"status": "SCHEDULED", "sport": "tennis", "home": "A", "away": "B", "kickoff": ko,
              "exchange": {"home": {"back": 2.0, "lay": 2.02, "lay_size_best": 300}}}}}
    rows = M.lay_board(snap)
    assert [(r["match"], r["sel"]) for r in rows] == [("Inter - Milan", "1")] and rows[0]["spread"] == 0.01


def test_registro_bonus(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "RUNTIME_DIR", tmp_path)
    r = M.save_row({"bookmaker": "Sisal", "offerta": "freebet 5", "deposito": "10", "profitto": "3,5", "stato": "chiuso"})
    M.save_row({"bookmaker": "Snai", "deposito": 50, "stato": "in corso"})
    assert M.summary() == {"n": 2, "chiusi": 1, "profitto": 3.5, "bloccato": 50.0, "aperti": 1}
    M.save_row({"id": r["id"], "bookmaker": "Sisal", "profitto": 4, "stato": "chiuso"})
    assert M.summary()["profitto"] == 4.0
    M.delete_row(r["id"])
    assert M.summary()["n"] == 1
    with pytest.raises(ValueError):
        M.save_row({"bookmaker": ""})


def test_pagina_e_api(tmp_path, monkeypatch):
    from betbot import server
    from betbot.store import Store
    monkeypatch.setattr(M, "RUNTIME_DIR", tmp_path)
    assert "Matched betting" in (server.DASHBOARD_FILE.parent / "matched.html").read_text(encoding="utf-8")
    out = server.handle_action("/api/matched/bonus", {"bookmaker": "Eurobet", "stato": "da fare"})
    assert "Eurobet" in out["message"] and M.load()[0]["bookmaker"] == "Eurobet"
    st = Store(tmp_path / "s.db")
    st.set("mb_lay_board", {"ts": 1, "rows": [{"match": "x"}]})
    assert st.get("mb_lay_board")["rows"][0]["match"] == "x"


def test_messaggio_telegram():
    rows = [{"match": "Inter - Milan & Co", "league": "Serie A", "kickoff": "2026-10-18T18:45:00+00:00", "sel": "1",
             "back": 2.0, "lay": 2.02, "spread": 0.01, "lay_eur": 320}]
    t = M.telegram_text(rows)
    assert "<b>" in t and "Milan &amp; Co" in t and "Non sono pronostici" in t and "lay 2.02" in t
    assert "<b>" not in M.telegram_text(rows, html=False) and "nessuna partita" in M.telegram_text([])


def test_comando_coperture(tmp_path):
    from types import SimpleNamespace
    from betbot.store import Store
    from betbot.telegram_bot import TelegramCommands
    st = Store(tmp_path / "t.db")
    st.set("mb_lay_board", {"rows": [{"match": "A - B", "league": "L", "kickoff": "2026-10-18T18:45:00+00:00", "sel": "X",
                                      "back": 3.3, "lay": 3.35, "spread": 0.015, "lay_eur": 80}]})
    t = TelegramCommands(SimpleNamespace(store=st, settings={}))
    assert "A - B" in t.handle("/coperture")
