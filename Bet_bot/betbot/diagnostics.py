"""`python betbot.py diagnosi`: controlla tutto quello che serve a Bet_bot, in ordine, con messaggi chiari."""
from __future__ import annotations

import sys


def _line(ok: bool | None, text: str) -> None:
    mark = "OK " if ok else "-- " if ok is None else "NO "
    print(f"  [{mark}] {text}")


def run_all() -> bool:
    print("Diagnosi di Bet_bot\n")
    all_ok = True
    _line(sys.version_info >= (3, 10), f"Python {sys.version.split()[0]} (serve 3.10 o superiore)")
    all_ok &= sys.version_info >= (3, 10)
    for lib in ("pandas", "numpy", "yaml", "requests", "tzdata"):
        try:
            __import__(lib)
            _line(True, f"libreria {lib}")
        except ImportError:
            _line(False, f"libreria {lib} mancante: rilancia installa.bat")
            all_ok = False
    from .config import RUNTIME_DIR, ensure_dirs, load_settings, load_yaml
    try:
        s = load_settings()
        load_yaml("risk_limits.yaml")
        load_yaml("strategies.yaml")
        _line(True, f"configurazione letta: modalità {s['mode'].upper()}, feed {s['feed']['provider']}, "
                    f"capitale {s['capital']['initial']} €")
    except Exception as exc:
        _line(False, f"configurazione non valida: {exc}")
        return False
    try:
        ensure_dirs()
        (RUNTIME_DIR / ".prova").write_text("ok")
        (RUNTIME_DIR / ".prova").unlink()
        _line(True, f"cartella dati scrivibile: {RUNTIME_DIR}")
    except OSError as exc:
        _line(False, f"cartella dati non scrivibile ({exc})")
        all_ok = False

    from . import local_settings, notifier
    ls = local_settings.load()
    if notifier.channel():
        try:
            me = notifier.call(ls["telegram"]["token"], "getMe")
            _line(True, f"Telegram collegato: bot @{me.get('username')}, chat {ls['telegram'].get('chat_name')}")
        except Exception as exc:
            _line(False, f"Telegram non risponde: {exc}")
    else:
        _line(None, "Telegram non collegato (facoltativo): Impostazioni della dashboard")

    feed = s["feed"]
    if feed.get("reference") == "odds_api" or feed["provider"] == "odds_api":
        key = ls.get("odds_api_key")
        if not key:
            _line(False, "manca la chiave di The Odds API (Impostazioni → Chiavi dei dati)")
            all_ok = False
        else:
            try:
                import requests
                r = requests.get("https://api.the-odds-api.com/v4/sports", params={"apiKey": key}, timeout=15)
                _line(r.status_code == 200, f"The Odds API: risposta {r.status_code}, richieste rimaste "
                                            f"{r.headers.get('x-requests-remaining', '?')} (questa verifica non ne consuma)")
            except Exception as exc:
                _line(False, f"The Odds API non raggiungibile: {exc}")

    bf = ls["betfair"]
    if feed["provider"] == "betfair" or (s.get("execution") or {}).get("provider") == "betfair":
        if not (bf.get("app_key") and bf.get("username") and bf.get("password")):
            _line(False, "credenziali Betfair mancanti (Impostazioni → Betfair Exchange Italia)")
            return False
        try:
            from .feeds.betfair import BetfairClient, HORSE_RACING
            c = BetfairClient(bf)
            c.login()
            funds = c.account_funds()
            _line(True, f"Betfair: login riuscito, disponibile {funds.get('availableToBetBalance')} €")
            types = c.event_types()
            _line(True, f"sport disponibili sul conto: {', '.join(sorted(types.values())[:12])}")
            _line(HORSE_RACING not in types or None, "ippica " + ("assente (normale su betfair.it)" if HORSE_RACING not in types
                                                                  else "presente: il conto non sembra italiano"))
            from .feeds.betfair import SOCCER
            cat = c.catalogue(SOCCER, "MATCH_ODDS", 48, None, 1, lookback_hours=0)
            if cat:
                book = c.books([cat[0]["marketId"]])[0]
                delayed = bool(book.get("isMarketDataDelayed"))
                _line(not delayed or None, "app key LIVE: prezzi in tempo reale" if not delayed else
                      "app key DELAYED: prezzi in ritardo fino a 3 minuti. Va bene per il paper; per i soldi veri "
                      "chiedi la app key live (gratuita per i conti italiani)")
        except Exception as exc:
            _line(False, f"Betfair: {exc}")
            all_ok = False
    else:
        _line(None, "Betfair non usato (feed simulato ed esecuzione paper)")
    # la prova sui prezzi veri: registrazione, esame per il live, lay d'apertura
    from .feeds.recorder import recording_status
    rs = recording_status()
    if feed.get("record") and feed["provider"] == "betfair":
        _line(True if rs["days"] >= rs["target_days"] else None,
              f"registrazione dei prezzi veri accesa: {rs['days']} giorni su {rs['target_days']} consigliati"
              + (f" (dal {rs['first']}, {rs['mb']} MB)" if rs["days"] else ""))
    else:
        _line(None, "registrazione dei prezzi veri non attiva: serve feed.provider: betfair (e feed.record: true)")
    try:
        from .config import DB_PATH
        from .esame import evaluate_all
        from .store import Store
        if DB_PATH.exists():
            ids = list(dict.fromkeys((s.get("active_strategies") or []) + (s.get("observe_strategies") or [])))
            for r in evaluate_all(Store(DB_PATH), ids):
                _line(True if r["verdict"] == "PRONTA" else False if r["verdict"] == "BOCCIATA" else None,
                      f"esame per il live {r['strategy_id']}: {r['verdict']} ({r['n']}/{r['need']} puntate sui prezzi veri)")
    except Exception as exc:
        _line(None, f"esame per il live non leggibile ({exc})")
    lay_on = bool((s.get("execution") or {}).get("lay_apertura"))
    _line(None, "lay d'apertura " + ("ACCESO: S09 può fare lay veri se è in live_strategies" if lay_on else
                                     "spento: S09 lavora solo in ombra (si accende dopo l'esame)"))
    print("\nTutto a posto." if all_ok else "\nCi sono punti da sistemare: leggi le righe NO qui sopra.")
    return all_ok
