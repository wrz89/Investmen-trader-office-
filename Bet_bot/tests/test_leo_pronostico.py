import json

from betbot import leo_pronostico as L
from betbot.store import Store


def _snap(home="Inter", away="Lecce"):
    ex = {"home": {"back": 1.5, "lay": 1.52}, "draw": {"back": 4.2, "lay": 4.3}, "away": {"back": 7.0, "lay": 7.4}}
    return {"ts": 1000.0, "matches": {"m1": {"match_id": "m1", "status": "SCHEDULED", "sport": "soccer", "league": "Serie A",
                                              "home": home, "away": away, "kickoff": "1970-01-01T05:00:00+00:00", "exchange": ex}}}


def test_ratings_learn_and_predict_two_way_sport(tmp_path):
    r = L.Ratings()
    assert L.predict(r, "tennis", "Sinner", "Rune") is None            # nessuna partita vista: Leo non si sbilancia
    for _ in range(6):
        L.update_result(r, "tennis", "Sinner", "Rune", "home")
        L.update_result(r, "tennis", "Rune", "Sinner", "away")
    p = L.predict(r, "tennis", "Sinner", "Rune")
    assert p and p["home"] > 0.6 and abs(p["home"] + p["away"] - 1) < 1e-9


def test_update_logs_forecast_and_scores_it(tmp_path):
    st = Store(tmp_path / "l.db")
    r = L.Ratings()
    r.t["soccer"] = {"inter": [1700.0, 50], "lecce": [1450.0, 50]}
    r.W = [[0.1, -0.3, -0.1], [0.5, -0.1, -0.5]]
    st.set(L.KEY, r.dump())
    out = L.update(st, _snap())
    assert out["pronostici"] == 1
    row = st.query("SELECT p_leo, p_mkt, scored FROM leo_pronostici")[0]
    assert json.loads(row["p_leo"])["home"] > 0.4 and row["scored"] == 0
    st.execute("INSERT INTO matches(match_id, sport, league, home, away, kickoff, status, result) "
               "VALUES('m1','soccer','Serie A','Inter','Lecce','x','FINISHED','home')")
    L.update(st, {"ts": 2000.0, "matches": {}})
    done = st.query("SELECT scored, ll_leo, ll_mkt FROM leo_pronostici")[0]
    assert done["scored"] == 1 and done["ll_leo"] > 0 and done["ll_mkt"] > 0
    assert "Leo" in L.text(L.summary(st))


def test_bootstrap_soccer_from_csv(tmp_path):
    rows = ["Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR"]
    import itertools
    teams = ["Alpha", "Beta", "Gamma", "Delta"]
    for i, (h, a) in enumerate(itertools.cycle(itertools.permutations(teams, 2))):
        if i >= 120:
            break
        rows.append(f"{1 + i % 28:02d}/01/2024,{h},{a},{2 if h == 'Alpha' else 1},{0 if h == 'Alpha' else 1},{'H' if h == 'Alpha' else 'D'}")
    (tmp_path / "2324_I1.csv").write_text("\n".join(rows), encoding="utf-8")
    r = L.Ratings()
    assert L.bootstrap_soccer(r, tmp_path) == 120 and r.W is not None
    assert r.elo("soccer", "alpha")[0] > r.elo("soccer", "delta")[0]
