from betbot.qualita import score


def _p(**kw):
    p = {"book_eur": 500.0, "spread": 0.01, "ref_ts": 1000.0, "league": "English Premier League", "sport": "soccer",
         "kickoff": "1970-01-01T00:50:00+00:00"}
    p.update(kw)
    return p


def test_quality_orders_good_and_bad_cases():
    snap = {"ts": 1000.0 + 600, "sim_time": 1000.0 + 600, "time_scale": 1.0}
    good, _ = score(_p(ref_ts=1000.0 + 500), snap)
    thin, _ = score(_p(book_eur=40.0, spread=0.03, ref_ts=None, league="Paraguayan Primera Division", kickoff="1970-01-01T05:00:00+00:00"), snap)
    assert good >= 85 and thin <= 30 and good > thin


def test_risk_gate_and_reason_for_fun(tmp_path, monkeypatch):
    from tests.test_divertimento import _fun, _rsnap
    import tests.test_divertimento as T
    # il gate si prova nei test di divertimento: qui si controlla solo che il punteggio sia sempre 0-100
    q, parts = score(_fun(book_eur=500, spread=0.011), _rsnap())
    assert 0 <= q <= 100 and set(parts) == {"liquidità", "spread", "riferimento", "campionato", "anticipo"}
