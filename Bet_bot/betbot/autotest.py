"""Test automatici: partono da soli quando si lancia avvia.bat, in sottofondo, senza fermare il bot.

Ognuno ha il suo ritmo (settings.yaml → autotest), così non si ripete a ogni riavvio e non brucia crediti:
  • orizzonti (dalle registrazioni, gratis)                    → al più una volta al giorno;
  • allenamento dei ragazzi (storico football-data, gratis)   → una volta a settimana;
  • test rapido (6 ore, ~150 crediti di The Odds API)          → una volta a settimana, solo con il feed Betfair e
    almeno `test_rapido_min_crediti` crediti rimasti; aspetta da solo la finestra con più partite.
L'esame e il bollettino li fa già Leo nel ciclo. Ogni risultato finisce nei report e nel registro della dashboard
(e quindi nel bollettino del mattino).
"""
from __future__ import annotations

import json
import threading
import time
import traceback

from .config import RUNTIME_DIR

STATE = RUNTIME_DIR / "autotest.json"
DEFAULTS = {"enabled": True, "orizzonti_ore": 24, "allenamento_giorni": 7, "test_rapido_giorni": 7,
            "test_rapido_min_crediti": 250}


def _state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _mark(name: str) -> None:
    s = _state()
    s[name] = time.time()
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s), encoding="utf-8")


def due(name: str, every_s: float, now: float | None = None) -> bool:
    return (now or time.time()) - _state().get(name, 0) >= every_s


def credits_left() -> int | None:
    try:
        v = json.loads((RUNTIME_DIR / "odds_api_budget.json").read_text(encoding="utf-8")).get("remaining")
        return None if v is None else int(v)
    except (OSError, ValueError):
        return None


def plan(settings: dict, now: float | None = None) -> list[tuple[str, str]]:
    """[(test, motivo)] da lanciare adesso."""
    cfg = {**DEFAULTS, **(settings.get("autotest") or {})}
    if not cfg["enabled"]:
        return []
    out = []
    if due("orizzonti", cfg["orizzonti_ore"] * 3600, now):
        out.append(("orizzonti", "prezzi registrati: a che ora conviene entrare"))
    if due("allenamento", cfg["allenamento_giorni"] * 86400, now):
        out.append(("allenamento", "le strategie rigiocano gli ultimi anni"))
    if settings["feed"]["provider"] == "betfair" and settings["feed"].get("reference") == "odds_api" \
            and due("test_rapido", cfg["test_rapido_giorni"] * 86400, now):
        left = credits_left()
        if left is None or left >= cfg["test_rapido_min_crediti"]:
            out.append(("test_rapido", "6 ore di betfair.it contro Pinnacle, nella finestra con più partite"))
    return out


def _run_one(name: str) -> str:
    from .config import REPORTS_DIR
    log = REPORTS_DIR / f"{name}.log"
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    def out(*a):
        lines.append(" ".join(str(x) for x in a))
        log.write_text("\n".join(lines[-400:]) + "\n", encoding="utf-8")
    if name == "orizzonti":
        from . import orizzonti
        r = orizzonti.run(out=out)
        return r.get("verdetto") if r else "nessuna registrazione ancora"
    if name == "allenamento":
        from . import allenamento
        r = allenamento.main(5, out=out)
        return "; ".join(f"{k}: ROI {v['roi']:+.1%} su {v['n']}" for k, v in r["strategie"].items() if v.get("n"))
    if name == "test_rapido":
        from . import test_rapido
        r = test_rapido.run(out=out)
        if not r:
            return "non partito: " + (lines[-1] if lines else "motivo sconosciuto")
        return f"S09 {r.get('verdetto')}; " + "; ".join(f"{k}: {v['verdetto']}" for k, v in (r.get("sport") or {}).items())
    raise ValueError(name)


def start(office) -> list[str]:
    """Chiamata da `avvia`: lancia in sottofondo i test che sono in scadenza. Restituisce i nomi lanciati."""
    todo = plan(office.settings)
    if not todo:
        return []
    say = office.coach.say if hasattr(office, "coach") else (lambda *a, **k: None)
    say("Test automatici all'avvio: " + ", ".join(f"{n} ({why})" for n, why in todo) + ". Girano in sottofondo.",
        "working", "autotest", level="INFO")

    def worker(name: str) -> None:
        if name != "test_rapido":
            _mark(name)                                # segnato subito: un riavvio non lo rilancia
        try:
            res = _run_one(name)
            if name == "test_rapido" and not str(res).startswith("non partito"):
                _mark(name)                            # solo se ha davvero misurato (può aspettare giorni la finestra)
            say(f"Test automatico {name} finito: {res}. Dettagli in runtime/reports/{name}.md", "ok", "autotest",
                level="INFO")
        except Exception as exc:
            say(f"Test automatico {name} non riuscito: {exc}", "alert", "autotest", level="WARN",
                payload={"traceback": traceback.format_exc()[-1500:]})

    for name, _ in todo:
        threading.Thread(target=worker, args=(name,), daemon=True, name=f"autotest-{name}").start()
    return [n for n, _ in todo]
