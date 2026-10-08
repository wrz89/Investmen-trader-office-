"""Storico dei lay su OddsPapi: lettura delle serie, regola del lay di valore, cache e rapporto."""
import json
from datetime import datetime, timedelta, timezone

from betbot import oddspapi as OP
from betbot import oddspapi_storico as S

START = datetime(2026, 3, 1, 19, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.isoformat()


def entry(dt, price, meta=None, active=True):
    return {"createdAt": iso(dt), "price": price, "limit": 100, "active": active, "exchangeMeta": meta}


def raw_fixture(away_bf=3.9, lay_meta=None):
    t0 = START - timedelta(hours=30)
    pin = {"101": [entry(t0, 1.80)], "102": [entry(t0, 3.70)], "103": [entry(t0, 4.60), entry(START - timedelta(minutes=5), 4.40)]}
    bf = {"101": [entry(t0, 1.85)], "102": [entry(t0, 3.70)], "103": [entry(t0, away_bf, lay_meta)]}
    wrap = lambda d: {"markets": {"101": {"outcomes": {k: {"players": {"0": v}} for k, v in d.items()}}}}
    return {"fixtureId": "id1", "bookmakers": {"pinnacle": wrap(pin), "betfair-ex": wrap(bf)}}


def test_series_and_at():
    ser = S.series(raw_fixture(), "pinnacle", "103")
    assert [round(p, 2) for _, p, _, _ in ser] == [4.6, 4.4]
    assert S.at(ser, START.timestamp() - 3600)[1] == 4.6 and S.at(ser, START.timestamp())[1] == 4.4
    assert S.at(ser, START.timestamp() - 40 * 3600) is None


def test_ticks_and_explicit_lay():
    assert S.add_ticks(3.9) == 4.0 and S.add_ticks(1.99) == 2.02
    assert S._best_lay({"lay": [{"price": 4.1, "size": 20}]}) == 4.1 and S._best_lay({"lay": 4.2}) == 4.2
    assert S._best_lay(None) is None and S._best_lay({"back": 3.9}) is None


def test_lay_di_valore_vinto_e_perso():
    raw = raw_fixture()
    won = S.evaluate_fixture(raw, START.timestamp(), 0)                  # vince la casa: il lay sull'ospite incassa
    assert {r["h"] for r in won} == {24, 6, 1} and all(r["sel"] == "away" and r["win"] for r in won)
    assert abs(won[0]["pnl"] - 0.955) < 1e-9 and won[0]["clv"] is not None and not won[0]["explicit"]
    lost = S.evaluate_fixture(raw, START.timestamp(), 2)                 # vince l'ospite: si perde (lay − 1)
    assert not lost[0]["win"] and abs(lost[0]["pnl"] + (lost[0]["lay"] - 1)) < 1e-9


def test_record_rotto_e_lay_esplicito():
    assert S.evaluate_fixture(raw_fixture(away_bf=2.0), START.timestamp(), 0) == []       # prezzo incoerente con Pinnacle
    rows = S.evaluate_fixture(raw_fixture(lay_meta={"lay": [{"price": 4.2, "size": 10}]}), START.timestamp(), 0)
    assert rows and rows[0]["explicit"] and rows[0]["lay"] == 4.2


def test_summarize_and_report():
    rows = S.evaluate_fixture(raw_fixture(), START.timestamp(), 0) + S.evaluate_fixture(raw_fixture(), START.timestamp(), 2)
    s = S.summarize([r for r in rows if r["h"] == 1])
    assert s["n"] == 2 and 0 < s["win"] <= 1 and s["estimated"] == 1.0
    assert "A 24 ore" in S.report(rows, 2) and "lay stimato" in S.report(rows, 2)


class FakeClient:
    def __init__(self):
        self.calls = []

    def get(self, path, quota=True, **params):
        self.calls.append((path, quota))
        if path == "/tournaments":
            return [{"tournamentId": 23, "tournamentName": "Serie A", "categoryName": "Italy"}]
        if path == "/fixtures":
            if "from" in params:                      # prima variante: l'API non risponde con le date
                return []
            return [{"fixtureId": "id1", "startTime": START.isoformat(), "participant1Name": "AC Milan",
                     "participant2Name": "Inter", "statusId": 2, "hasOdds": False}]
        assert path == "/historical-odds" and quota is False
        full = raw_fixture()["bookmakers"]
        if params["bookmakers"] == "pinnacle":
            return {"fixtureId": "id1", "bookmakers": {"pinnacle": full["pinnacle"]}}
        assert params["bookmakers"] == "betfair-ex" and "outcomeId" in params          # l'API vuole un solo esito
        oid = str(params["outcomeId"])
        outs = full["betfair-ex"]["markets"]["101"]["outcomes"]
        return {"fixtureId": "id1", "bookmakers": {"betfair-ex": {"markets": {"101": {"outcomes": {oid: outs[oid]}}}}}}


def test_run_scarica_una_volta_e_riporta(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(OP, "RUNTIME_DIR", tmp_path)
    c = FakeClient()
    results = [{"date": START.replace(tzinfo=None), "home": "Milan", "away": "Inter", "res": 0}]
    out = []
    assert S.run(out=out.append, client=c, results=results, sleep=lambda s: None) == 0
    assert (tmp_path / "oddspapi_storico" / "id1.json").exists() and "Scaricate 1 partite nuove" in "\n".join(out)
    assert "3 lay" in "\n".join(out) or "lay ·" in "\n".join(out)
    out2 = []
    S.run(out=out2.append, client=c, results=results, sleep=lambda s: None)
    assert sum(1 for p, _ in c.calls if p == "/historical-odds") == 4          # in cache: non si riscarica
