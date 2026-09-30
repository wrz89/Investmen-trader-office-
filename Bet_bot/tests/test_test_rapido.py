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


# ── partenza automatica, Ctrl+C, tennis ───────────────────────────────────
def test_best_start_waits_for_the_busy_window():
    now = 0.0
    kick = [30 * 3600 + i * 600 for i in range(12)]          # 12 partite tra 30 e 32 ore: adesso nessuna
    p = T.best_start(kick, 6, now)
    assert p["now"] == 0 and p["max"] == 12 and p["n"] >= 0.8 * 12
    assert 24 * 3600 <= p["start"] <= 30 * 3600
    assert T.best_start([2 * 3600, 3 * 3600], 6, now)["start"] == now      # partite subito: si parte adesso
    assert T.best_start([], 6, now)["max"] == 0


def test_interrupted_collection_keeps_what_it_has(monkeypatch, tmp_path):
    monkeypatch.setattr(T, "OUT_DIR", tmp_path)
    monkeypatch.setattr(T, "_ts", lambda iso: KO)
    clock["t"] = 10_000.0
    n = {"sleeps": 0}

    def sleep(s):
        n["sleeps"] += 1
        clock["t"] += s
        if n["sleeps"] >= 3:
            raise KeyboardInterrupt
    said = []
    data = T.collect(None, "k", hours=6, credits=50, out=said.append, sleep=sleep, now=lambda: clock["t"],
                     sources=_sources())
    assert data["bf"] and list(tmp_path.glob("*.json.gz")) and any("Interrotto" in x for x in said)


def test_tennis_names_in_reverse_order_are_matched_and_swapped():
    bf = {"sport": "tennis", "home": "Jannik Sinner", "away": "Carlos Alcaraz", "start": 1000.0}
    pin = {"home": "Carlos Alcaraz", "away": "Jannik Sinner", "start": 1000.0 + 2 * 3600,    # orario indicativo
           "fair": {"home": 0.45, "away": 0.55}}
    m = T.match_pin(bf, [pin])
    assert m and m["fair"] == {"home": 0.55, "away": 0.45}
    assert T.match_pin({**bf, "sport": "soccer"}, [pin]) is None          # nel calcio 20 minuti di tolleranza
    assert T.family("tennis_atp_paris_masters") == "tennis" and T.family(None) == "soccer"
