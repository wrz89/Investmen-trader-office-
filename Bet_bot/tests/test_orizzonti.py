"""Orizzonti d'ingresso dalle registrazioni e S05 v3 (filtri sul movimento dei prezzi)."""
import gzip
import json
from datetime import datetime, timezone

from betbot import orizzonti as O

KO = 1_800_000_000.0


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def _snap(t, home_back, pin_home, ref_ts, size=25.0):
    """Partita di calcio: la casa su Betfair a `home_back`, Pinnacle con la casa a `pin_home`."""
    ex = {"home": {"back": home_back, "lay": round(home_back * 1.02, 2), "back_size_best": size},
          "draw": {"back": 4.0, "lay": 4.1, "back_size_best": size}, "away": {"back": 5.0, "lay": 5.2, "back_size_best": size}}
    return {"ts": t, "sim_time": t, "matches": {"1.1": {
        "match_id": "1.1", "sport": "soccer", "league": "Serie A", "home": "Inter", "away": "Lecce", "kickoff": _iso(KO),
        "status": "SCHEDULED", "exchange": ex, "ref_ts": ref_ts,
        "books": {"Pinnacle": {"home": pin_home, "draw": 4.2, "away": 5.4}, "Bet365": {"home": 1.9, "draw": 4.0, "away": 5.0}}}}}


def _write(tmp_path, snaps):
    f = tmp_path / "2027-01-15.jsonl.gz"
    with gzip.open(f, "wt", encoding="utf-8") as fh:
        for s in snaps:
            fh.write(json.dumps(s) + "\n")
    return [f]


def test_horizons_measure_value_that_the_market_confirms(tmp_path):
    # 24 ore prima: Betfair paga la casa 2,30, Pinnacle (fresco) la dà a 2,05 → valore.
    # Alla chiusura Pinnacle scende a 1,95 e Betfair a 2,00: il prezzo preso a 24 ore batte la chiusura.
    snaps = [_snap(KO - 24 * 3600, 2.30, 2.05, KO - 24 * 3600 - 600),
             _snap(KO - 3600, 2.10, 2.00, KO - 3700),
             _snap(KO - 300, 2.00, 1.95, KO - 900)]
    res = O.analyse(O.scan(_write(tmp_path, snaps)))
    assert res["partite"] == 1
    g = res["gruppi"]["calcio"]
    assert set(g) == {24, 1}
    assert g[24]["valore"]["n"] == 1 and g[24]["valore"]["clv"] > 0.1
    assert g[24]["clv_bf"][0] is not None and g[24]["liquidita_mediana"] == 25.0
    md = O.report(res, days=1)
    assert "Quando conviene entrare" in md and "| 24 |" in md


def test_stale_pinnacle_is_not_used_and_missing_close_is_skipped(tmp_path):
    # Pinnacle vecchio di 10 ore a 3 ore dall'inizio: niente gruppo "valore"
    snaps = [_snap(KO - 3 * 3600, 2.30, 2.05, KO - 13 * 3600), _snap(KO - 600, 2.0, 1.95, KO - 13 * 3600)]
    res = O.analyse(O.scan(_write(tmp_path, snaps)))
    assert res["gruppi"]["calcio"][3]["valore"]["n"] == 0
    # bot spento 2 ore prima dell'inizio: chiusura mai vista, la partita non conta
    res = O.analyse(O.scan(_write(tmp_path, [_snap(KO - 3 * 3600, 2.3, 2.05, KO - 3 * 3600)])))
    assert res["partite"] == 0
    assert "NESSUN ORIZZONTE" in res["verdetto"]


def test_run_without_recordings(monkeypatch, tmp_path):
    from betbot.feeds import recorder
    monkeypatch.setattr(recorder, "REC_DIR", tmp_path / "vuota")
    said = []
    assert O.run(out=said.append) == {} and "Nessuna registrazione" in said[0]


# ── S05 v3 ────────────────────────────────────────────────────────────────
def _fav(t, back, pin_home, size=20.0):
    ko = t + 3600
    return {"ts": t, "sim_time": t, "matches": {"M1": {
        "match_id": "M1", "sport": "soccer_italy_serie_a", "league": "Serie A", "home": "Inter", "away": "Lecce",
        "kickoff": _iso(ko), "status": "SCHEDULED", "commission": 0.045,
        "exchange": {"home": {"back": back, "lay": round(back + 0.01, 2), "back_size": size, "back_size_best": size}},
        "books": {"Pinnacle": {"home": pin_home, "draw": 7.5, "away": 15.0},
                  "Bet365": {"home": pin_home, "draw": 7.0, "away": 14.0}}}}}


def test_s05_v3_skips_when_the_fair_price_moved():
    from betbot.strategies import s05_favoriti_exchange_v3 as V3
    V3._SEEN.clear()
    t0 = 1_900_000_000.0
    V3.observe(_fav(t0, 1.40, 1.40))                               # prima lettura: favorito sotto l'80%
    # dieci minuti dopo il riferimento crolla a 1,18: notizia → Betfair a 1,30 "sembra" un regalo, ma si salta
    moved = _fav(t0 + 600, 1.30, 1.18)
    assert V3.propose(moved, {}, {}) == []
    from betbot.strategies import s05_favoriti_exchange_v2 as V2
    assert len(V2.propose(moved, {}, {})) == 1                     # la v2 l'avrebbe presa
    V3._SEEN.clear()
    V3.observe(_fav(t0, 1.30, 1.20))
    out = V3.propose(_fav(t0 + 60, 1.30, 1.20), {}, {})
    assert len(out) == 1 and out[0]["strategy_id"] == "S05_favoriti_exchange_v3" and "movimenti" in out[0]["reason"]


def test_s05_v3_needs_a_deeper_book_than_v2():
    from betbot.strategies import s05_favoriti_exchange_v2 as V2
    from betbot.strategies import s05_favoriti_exchange_v3 as V3
    V3._SEEN.clear()
    snap = _fav(1_900_000_000.0, 1.30, 1.20, size=4.0)             # 4 € al miglior prezzo
    assert len(V2.propose(snap, {}, {})) == 1 and V3.propose(snap, {}, {}) == []
