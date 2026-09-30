"""Test rapido: raccolta con fonti finte (6 ore simulate in un attimo) e analisi del CLV."""
from betbot import test_rapido as T

KO = 10_000.0 + 3 * 3600          # la partita inizia 3 ore dopo l'avvio


def _sources():
    def pinnacle(sp):
        # Pinnacle all'inizio: ospite giusto a 5,0 (20%); alla chiusura scende al 17% (quota giusta ~5,9)
        return ([{"home": "Inter", "away": "Lecce", "start": KO,
                  "fair": {"home": 0.6, "draw": 0.2, "away": 0.2 if clock["t"] < KO - 7200 else 0.17}}], "400")

    def betfair(h):
        if clock["t"] >= KO:
            return []
        return [{"market_id": "1.1", "home": "Inter", "away": "Lecce", "start": KO, "league": "Serie A",
                 "sel": {"home": {"back": 1.64, "lay": 1.66}, "draw": {"back": 4.9, "lay": 5.0},
                         "away": {"back": 4.3, "lay": 4.4}}}]       # lay dell'ospite a 4,4 < 5,0 giusto: segnale S09
    return {"sports": lambda: ["soccer_italy_serie_a"],
            "events": lambda sp: [{"commence_time": "2026-10-01T00:00:00Z"}],
            "pinnacle": pinnacle, "betfair": betfair}


clock = {"t": 10_000.0}


def test_collect_and_analyse_without_real_waiting(monkeypatch, tmp_path):
    monkeypatch.setattr(T, "OUT_DIR", tmp_path)
    monkeypatch.setattr(T, "_ts", lambda iso: KO)
    clock["t"] = 10_000.0

    def sleep(s):
        clock["t"] += s
    data = T.collect(None, "k", hours=6, credits=50, out=lambda *a: None, sleep=sleep, now=lambda: clock["t"],
                     sources=_sources())
    assert data["bf"] and data["pin"] and list(tmp_path.glob("*.json.gz"))
    res = T.analyse(data)
    assert res["matches"] == 1 and res["s09"]["n"] == 1
    away = [r for r in res["rows"] if r["side"] == "away"][0]
    assert away["clv_lay"] > 0            # bancato a 4,4 un esito che alla chiusura vale ~5,9: il mercato ci ha dato ragione
    assert "Verdetto" in T.report(res)


def test_no_verdict_with_too_few_matches():
    res = T.analyse({"start": 0, "end": 1, "bf": [], "pin": [], "sports": []})
    assert res["verdetto"] == "NON ANCORA CHIARO" and res["s09"]["n"] == 0
