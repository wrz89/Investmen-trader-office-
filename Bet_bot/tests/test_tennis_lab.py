"""Laboratorio tennis: nomi, statistiche senza sguardi al futuro, confronto con il mercato."""
from datetime import datetime, timedelta

from betbot import tennis_lab as T


def test_chiave_dei_nomi():
    assert T._key("Jannik Sinner") == T._key("Sinner J.") == "sinner j"
    assert T._key("Alex de Minaur") == T._key("De Minaur A.") == "de minaur a"


def _m(d, w, l, surface="Hard"):
    return {"tour": "ATP", "date": d, "surface": surface, "tourney": "T", "round": "R32", "match_num": 1, "w": w, "l": l,
            "minutes": 100, "w_rank": 10, "l_rank": 50, "w_age": 25, "l_age": 25, "w_sv": (80, 40, 15), "l_sv": (80, 35, 12)}


def test_statistiche_e_confronto_col_mercato():
    d0 = datetime(2015, 1, 5)
    ms = []
    for i in range(3000):                      # "Forte" batte "Debole" quasi sempre; il mercato lo sa (quota 1,25)
        d = d0 + timedelta(days=i)
        ms.append(_m(d, "Mario Forte", "Luca Debole") if i % 5 else _m(d, "Luca Debole", "Mario Forte"))
    rows = T.build(ms)
    assert rows and all(len(r["x"]) == 6 for r in rows)
    wf = T.walk_forward(rows, first_test=2019)
    assert wf["n"] > 0 and wf["accuracy"] > 0.7
    odds = [{"date": m["date"] + timedelta(days=1), "w": "Forte M." if m["w"] == "Mario Forte" else "Debole L.",
             "l": "Debole L." if m["w"] == "Mario Forte" else "Forte M.",
             "ps": (1.25, 4.5) if m["w"] == "Mario Forte" else (4.5, 1.25), "bfe": None} for m in ms]
    assert T.attach_odds(rows, odds) > 0
    ev = T.evaluate_vs_market(rows)
    txt = T.report(wf, ev, len(ms))
    assert ev["n"] > 0 and "bets" in ev and ("PEGGIO" in txt or "MEGLIO" in txt)
