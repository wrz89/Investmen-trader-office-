"""Lettore dello storico ufficiale Betfair (formato Stream API, piano Basic)."""
import bz2
import json
import tarfile
from datetime import datetime, timezone

from betbot import storico_betfair as SB

KO = datetime(2025, 3, 1, 15, 0, tzinfo=timezone.utc).timestamp()


def _market_file(path, away_ltps, winner="home"):
    runners = [{"id": 1, "name": "Inter", "status": "WINNER" if winner == "home" else "LOSER"},
               {"id": 2, "name": "Lecce", "status": "WINNER" if winner == "away" else "LOSER"},
               {"id": 3, "name": "The Draw", "status": "WINNER" if winner == "draw" else "LOSER"}]
    defn = {"marketType": "MATCH_ODDS", "eventName": "Inter v Lecce", "inPlay": False,
            "marketTime": "2025-03-01T15:00:00.000Z", "runners": runners}
    lines = [{"op": "mcm", "pt": int((KO - 30 * 3600) * 1000), "mc": [{"id": "1.1", "marketDefinition": defn}]}]
    for mins, ltp in away_ltps:
        lines.append({"op": "mcm", "pt": int((KO - mins * 60) * 1000),
                      "mc": [{"id": "1.1", "rc": [{"id": 2, "ltp": ltp}, {"id": 1, "ltp": 1.5}, {"id": 3, "ltp": 4.2}]}]})
    lines.append({"op": "mcm", "pt": int(KO * 1000), "mc": [{"id": "1.1", "marketDefinition": {**defn, "inPlay": True}}]})
    path.write_bytes(bz2.compress("\n".join(json.dumps(x) for x in lines).encode()))


def test_parse_and_price_at_horizons(tmp_path):
    f = tmp_path / "1.1.bz2"
    _market_file(f, [(26 * 60, 6.0), (5 * 60, 5.6), (30, 5.2)])
    m = SB.parse_market(f)
    assert m["home"] == "Inter" and m["result"] == "home" and set(m["series"]) == {"home", "draw", "away"}
    assert SB.price_at(m["series"]["away"], KO - 24 * 3600) == 6.0
    assert SB.price_at(m["series"]["away"], KO - 60 * 60) == 5.6
    assert SB.price_at(m["series"]["away"], KO - 15 * 60) == 5.2


def test_tar_is_extracted_and_analysed(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    _market_file(raw / "1.1.bz2", [(26 * 60, 6.0), (5 * 60, 5.6), (30, 5.2)])
    root = tmp_path / "storico"
    root.mkdir()
    with tarfile.open(root / "basic.tar", "w") as t:
        t.add(raw / "1.1.bz2", arcname="BASIC/2025/Mar/1/123/1.1.bz2")
    fs = SB.files(root)
    assert len(fs) == 1 and fs[0].name == "1.1.bz2"
    m = SB.parse_market(fs[0])
    fd = [{"date": datetime(2025, 3, 1, 15, 0), "home": "Inter", "away": "Lecce", "ps": (1.55, 4.3, 7.0),
           "psc": (1.50, 4.4, 7.5)}]
    pairs = SB.match_fd([m], fd)
    assert len(pairs) == 1
    r = SB.analyse(pairs)
    o = r["orizzonti"]["1 ora"]
    assert o["lay_tutti"]["n"] >= 1 and o["fasce"]["5-8"]["n"] == 1
    assert "Storico Betfair" in SB.report(r)
