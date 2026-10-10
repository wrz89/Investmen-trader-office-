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


def _board_row(**k):
    r = {"match": "Inter - Milan", "league": "Serie A", "kickoff": "2099-10-18T18:45:00+00:00", "sel": "2", "sel_key": "away",
         "back": 5.0, "lay": 5.2, "spread": 0.04, "lay_eur": 200, "market_id": "1.99", "selection_id": 47, "team": "Milan"}
    r.update(k)
    return r


class FakeClient:
    def __init__(self, matched=True):
        self.placed, self.matched = [], matched

    def place(self, market_id, selection_id, side, price, size, fill_or_kill=True, order_ref=None, strategy_ref=None):
        self.placed.append((market_id, selection_id, side, price, size, fill_or_kill, strategy_ref))
        return {"bet_id": "B1", "matched": size if self.matched else 0.0, "avg_price": price, "status": "EXECUTION_COMPLETE"}

    def cleared(self, ids):
        return {"B1": {"profit": -33.0, "outcome": "LOST", "status": "SETTLED"}}


def test_consiglio_per_offerta_freebet(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "RUNTIME_DIR", tmp_path)
    o = M.save_row({"bookmaker": "Sisal", "tipo": "snr", "importo": 10, "quota_min": 2, "stato": "da fare"})
    board = [_board_row(), _board_row(back=1.8, lay=1.82, selection_id=1, sel="1", team="Inter"),
             _board_row(back=4.0, lay=4.1, lay_eur=3, selection_id=2)]        # poca liquidità: escluso
    adv = M.advice(o, board, available=100)
    assert adv["selection_id"] == 47 and adv["est"]["pct"] > 0.7 and "sisal.it" in adv["bookmaker_link"]
    assert adv["betfair_link"].endswith("/1.99")
    assert M.advice(o, board, available=5) is None                     # responsabilità oltre il disponibile
    t = M.advice_text(1, o, adv)
    assert "Milan" in t and "/coperto 1" in t and "https://www.sisal.it" in t


def test_coperto_conferma_e_regolamento(tmp_path, monkeypatch):
    from betbot.store import Store
    monkeypatch.setattr(M, "RUNTIME_DIR", tmp_path)
    st = Store(tmp_path / "m.db")
    o = M.save_row({"bookmaker": "Sisal", "tipo": "snr", "importo": 10, "stato": "da fare"})
    st.set("mb_lay_board", {"ts": time.time(), "rows": [_board_row()]})
    st.set("live_balance", {"available": 100})
    st.set("mb_advice", {o["id"]: M.advice(o, [_board_row()], 100)})
    assert "/conferma" in M.plan_cover(st, 1, 5.0)
    assert "non è collegato" in M.confirm_cover(st, None)
    st.set("mb_advice", {o["id"]: M.advice(o, [_board_row()], 100)})
    M.plan_cover(st, 1, 5.0)
    c = FakeClient()
    msg = M.confirm_cover(st, c)
    assert msg.startswith("✅") and c.placed[0][2] == "LAY" and c.placed[0][5] is True and c.placed[0][6] == "MB"
    assert c.placed[0][4] == pytest.approx(7.76, abs=0.01)
    assert M.load()[0]["stato"] == "in corso" and st.get("mb_pending") is None
    assert "Nessuna copertura" in M.confirm_cover(st, c)                 # la conferma vale una volta sola
    msgs = M.settle_covers(st, c)
    assert msgs and st.get("mb_betfair_pnl") == -33.0


def test_conferma_scaduta_o_prezzo_mosso(tmp_path, monkeypatch):
    from betbot.store import Store
    monkeypatch.setattr(M, "RUNTIME_DIR", tmp_path)
    st = Store(tmp_path / "m.db")
    o = M.save_row({"bookmaker": "Snai", "tipo": "qualificante", "importo": 10, "stato": "da fare"})
    st.set("mb_lay_board", {"ts": time.time(), "rows": [_board_row(back=2.0, lay=2.02)]})
    st.set("mb_advice", {o["id"]: M.advice(o, [_board_row(back=2.0, lay=2.02)], None)})
    M.plan_cover(st, 1, 2.0)
    assert "2 minuti" in M.confirm_cover(st, FakeClient(), now=time.time() + 300)
    M.plan_cover(st, 1, 2.0)
    assert "NON abbinato" in M.confirm_cover(st, FakeClient(matched=False))
    assert "non trovata" in M.plan_cover(st, 5, 2.0)


def test_telegram_offerte_e_coperto(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from betbot.store import Store
    from betbot.telegram_bot import TelegramCommands
    monkeypatch.setattr(M, "RUNTIME_DIR", tmp_path)
    st = Store(tmp_path / "t.db")
    M.save_row({"bookmaker": "Eurobet", "tipo": "snr", "importo": 10, "stato": "da fare"})
    st.set("mb_lay_board", {"ts": time.time(), "rows": [_board_row()]})
    t = TelegramCommands(SimpleNamespace(store=st, settings={}, client=FakeClient()))
    assert "Offerta 1: Eurobet" in t.handle("/offerte") and "eurobet.it" in t.handle("/offerte")
    assert "Scrivi così" in t.handle("/coperto")
    assert "/conferma" in t.handle("/coperto 1 4,9")
    assert t.handle("/conferma").startswith("✅")


def test_lay_di_copertura_perso_non_fa_scattare_il_kill_switch(tmp_path, monkeypatch):
    """Il conto Betfair è condiviso: un lay di copertura perso (soldi vinti sul bookmaker) abbassa il saldo vero.
    Senza toglierlo dal confronto il bot penserebbe a un ammanco e accenderebbe il kill switch."""
    from betbot.core import SportOffice
    monkeypatch.setattr(M, "RUNTIME_DIR", tmp_path)
    o = SportOffice(db_path=tmp_path / "k.db", connect_feed=False)
    o.settings["mode"] = "live"
    o.settings.setdefault("execution", {})["provider"] = "betfair"
    total = o.bankroll.total
    o.store.set("mb_orders", [{"bet_id": "B1", "status": "aperto", "match": "A - B", "team": "B"}])
    o.client = FakeClient()                                     # il lay B1 viene regolato con −33 €
    o.executor._client = type("C", (), {"account_funds": lambda self: {"availableToBetBalance": total - 33.0, "exposure": 0.0}})()
    o._sync_live_balance(force=True)
    assert o.store.get("mb_betfair_pnl") == -33.0 and not o.store.get("kill_switch")

    o.store.set("mb_betfair_pnl", 0)                           # controprova: senza il conteggio del matched betting...
    o.store.set("mb_orders", [])
    o._sync_live_balance(force=True)
    assert o.store.get("kill_switch")                          # ...il kill switch scatterebbe
