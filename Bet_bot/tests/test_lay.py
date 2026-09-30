"""S09 lay di valore: proposta, regolamento in ombra, mai soldi veri."""
from datetime import datetime, timedelta, timezone

from betbot.strategies import s09_lay_valore_v1 as s09


def _snap(lay_away=4.0, pin_away=4.8):
    now = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
    m = {"match_id": "M1", "sport": "soccer_italy_serie_a", "league": "Serie A", "home": "Inter", "away": "Lecce",
         "status": "SCHEDULED", "kickoff": (now + timedelta(hours=3)).isoformat(),
         "books": {"Pinnacle": {"home": 1.62, "draw": 4.3, "away": pin_away}},
         "exchange": {"home": {"back": 1.6, "lay": 1.61, "lay_size_best": 50},
                      "draw": {"back": 4.2, "lay": 4.3, "lay_size_best": 50},
                      "away": {"back": lay_away - 0.1, "lay": lay_away, "lay_size_best": 50}}}
    return {"ts": now.timestamp(), "matches": {"M1": m}}


def test_lay_proposed_when_betfair_lay_below_fair():
    props = s09.propose(_snap(), {}, {})
    assert len(props) == 1 and props[0]["selection"] == "LAY:away" and props[0]["side"] == "LAY"
    p = props[0]["fair_prob"]
    assert s09.lay_ev(p, 4.0, 0.045) > 0 and props[0]["edge"] >= 0.02


def test_no_lay_when_price_is_fair():
    assert s09.propose(_snap(lay_away=5.2), {}, {}) == []


def test_lay_ev_formula():
    # p=0.2, lay 4.0: 0.8 × 0.955 − 0.2 × 3 = 0.164
    assert abs(s09.lay_ev(0.2, 4.0, 0.045) - 0.164) < 1e-9
