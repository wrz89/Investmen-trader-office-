"""Palestra di Leo: niente sbirciate al futuro, il modello parte dal mercato, le dinamiche hanno senso."""
from datetime import datetime, timedelta

import numpy as np

from betbot.palestra import FEATS_A, Model, World, dynamics_bands


def _m(day, home, away, hg, ag, div="I1"):
    return {"date": datetime(2025, 1, 1) + timedelta(days=day), "div": div, "season": "2425", "home": home, "away": away,
            "hg": hg, "ag": ag, "res": 0 if hg > ag else 1 if hg == ag else 2,
            "stats": {"HS": 12, "AS": 8, "HST": 5, "AST": 3}}


def test_features_never_see_the_match_result():
    w = World()
    for d in range(6):
        w.update(_m(d * 7, "Inter", f"X{d}", 2, 0))
    m = _m(50, "Inter", "Milan", 0, 5)
    before = w.features(m, True)
    m2 = dict(m, hg=5, ag=0, res=0)                  # stesso momento, risultato opposto
    assert w.features(m2, True) == before            # le caratteristiche non dipendono dal risultato


def test_model_without_evidence_is_the_market():
    mdl = Model(FEATS_A)
    logp = np.log([0.5, 0.3, 0.2])
    p = mdl.predict({k: 0.0 for k in FEATS_A}, logp)
    assert np.allclose(p, [0.5, 0.3, 0.2])


def test_strong_regularisation_keeps_noise_out():
    rng = np.random.default_rng(1)
    F = [{k: float(rng.normal()) for k in FEATS_A} for _ in range(3000)]     # caratteristiche di puro rumore
    logp = np.log(np.tile([0.45, 0.28, 0.27], (3000, 1)))
    y = rng.choice(3, size=3000, p=[0.45, 0.28, 0.27])
    mdl = Model(FEATS_A)
    mdl.fit(F, logp, y, "t")
    assert np.abs(mdl.W).max() < 0.08                # il rumore non sposta il mercato


def test_dynamics_seen_from_the_selection_side():
    f = {"stage": 0.8, "form_h": 2.5, "form_a": 0.8, "rest_h": 3, "rest_a": 7, "absence_h": 0.3, "key_missing_h": 2,
         "absence_a": 0.0, "key_missing_a": 0, "relegation_a": 1.0}
    home, away = dynamics_bands(f, "home"), dynamics_bands(f, "away")
    assert home["forma"] == "migliore" and away["forma"] == "peggiore"
    assert home["riposo"] == "meno riposata" and away["riposo"] == "più riposata"
    assert home["assenze"] == "big assenti" and away["assenze_avversario"] == "avversario decimato"
    assert away["classifica"] == "lotta salvezza" and home["fase_stagione"] == "finale"
