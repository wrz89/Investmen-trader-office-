"""BET_BOT — comandi (da lanciare dentro la cartella Bet_bot).

    python betbot.py avvia                 avvia l'ufficio (ciclo asincrono) + dashboard su http://localhost:8766
    python betbot.py ciclo                 esegue un solo ciclo
    python betbot.py simula --ore 72       simulazione accelerata di tutto l'ufficio sul feed simulato
    python betbot.py backtest             scarica lo storico con i prezzi VERI di Betfair Exchange e confronta le strategie
    python betbot.py rischio --quota 1.22 --vinte 0.80 --puntata 0.02
                                          Monte Carlo: cosa succede al bankroll con quella quota e quel win rate
    python betbot.py replay [--da AAAA-MM-GG] [--a AAAA-MM-GG]
                                          fa girare TUTTE le strategie sui prezzi registrati (feed.record: true)
    python betbot.py diagnosi             controlla installazione, configurazione, Telegram, chiavi e Betfair
    python betbot.py ferma                spegnimento ordinato: niente nuove puntate, trade chiusi, poi uscita
    python betbot.py avvio-automatico on|off   Bet_bot parte da solo quando accedi a Windows
    python betbot.py dashboard [--simulazione | --replay]   apre solo la dashboard
    python betbot.py report                report del giorno
    python betbot.py stato                 riepilogo veloce
    python betbot.py prova-telegram        invia un messaggio di prova
    python betbot.py betfair-verifica      login Betfair e saldo del conto
    python betbot.py reset-kill-switch     riattiva l'ufficio dopo un kill switch (decisione umana)
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import webbrowser


def _write_pid() -> None:
    """runtime/betbot.pid: serve ad aggiorna.bat per chiudere Bet_bot prima di aggiornare."""
    import atexit
    import os
    from betbot.config import RUNTIME_DIR
    pid = RUNTIME_DIR / "betbot.pid"
    pid.parent.mkdir(parents=True, exist_ok=True)
    pid.write_text(str(os.getpid()), encoding="ascii")

    def _cleanup():
        try:
            if pid.read_text(encoding="ascii").strip() == str(os.getpid()):
                pid.unlink()
        except OSError:
            pass
    atexit.register(_cleanup)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    try:
        import pandas, requests, yaml, tzdata  # noqa: F401
    except ImportError as exc:
        print(f"Libreria mancante: {exc.name}. Esegui di nuovo installa.bat.")
        return 1
    p = argparse.ArgumentParser(description="Sports Betting Office")
    p.add_argument("comando", choices=["avvia", "ciclo", "simula", "backtest", "rischio", "dashboard", "report", "stato",
                                       "prova-telegram", "betfair-verifica", "reset-kill-switch", "replay", "diagnosi",
                                       "ferma", "avvio-automatico"])
    p.add_argument("valore", nargs="?", help="avvio-automatico: on | off")
    p.add_argument("--da", help="replay: primo giorno registrato (AAAA-MM-GG)")
    p.add_argument("--a", dest="fino", help="replay: ultimo giorno registrato (AAAA-MM-GG)")
    p.add_argument("--replay", action="store_true", help="dashboard: mostra i dati dell'ultimo replay")
    p.add_argument("--ore", type=float, default=72, help="simula: ore simulate")
    p.add_argument("--seed", type=int, default=7, help="simula: seme del mondo simulato")
    p.add_argument("--campionati", default="", help="backtest: codici football-data (predefiniti: 16 campionati con prezzi Betfair)")
    p.add_argument("--csv", nargs="*", help="backtest: uno o più CSV propri invece del download")
    p.add_argument("--senza-kill", action="store_true", help="backtest: ignora il kill switch per misurare il segnale")
    p.add_argument("--quota", type=float, default=1.22)
    p.add_argument("--vinte", type=float, default=0.80)
    p.add_argument("--puntata", type=float, default=0.02)
    p.add_argument("--simulazione", action="store_true", help="dashboard: mostra i dati dell'ultima simulazione")
    p.add_argument("--giorno", help="report: AAAA-MM-GG")
    p.add_argument("--no-browser", action="store_true")
    a = p.parse_args()

    from betbot.config import RUNTIME_DIR, load_settings
    from betbot.server import office_running, serve
    port = load_settings()["dashboard_port"]

    if a.comando == "dashboard":
        db = RUNTIME_DIR / "simulazione.db" if a.simulazione else RUNTIME_DIR / "replay.db" if a.replay else None
        print(f"Dashboard su http://localhost:{port}  (CTRL+C per chiudere)")
        if not a.no_browser:
            webbrowser.open(f"http://localhost:{port}")
        serve(port, db_path=db)
        return 0

    if a.comando == "rischio":
        from betbot.backtest import montecarlo
        r = montecarlo(a.vinte, a.quota, a.puntata)
        print(f"Quota {a.quota:.2f}, vinte {a.vinte:.0%}, puntata {a.puntata:.1%} del bankroll, commissione 4,5%, 1000 puntate × 2000 scenari\n")
        print(f"  pareggio (win rate minimo)   {r['breakeven']:.1%}")
        print(f"  valore atteso per puntata    {r['ev_per_bet']:+.2%}")
        print(f"  bankroll finale mediano      ×{r['median_final']:.2f}   (5% peggiori ×{r['p5_final']:.2f}, 5% migliori ×{r['p95_final']:.2f})")
        print(f"  probabilità di finire sotto  {r['prob_loss']:.0%}")
        print(f"  probabilità di kill switch   {r['prob_kill_switch']:.0%}  (drawdown ≥ 15%)")
        print(f"  drawdown massimo mediano     {r['median_max_dd']:.1%}")
        return 0

    if a.comando == "ferma":
        import time as _t
        from betbot.config import STOP_FILE
        if not office_running(port):
            print("Bet_bot non è acceso.")
            return 0
        STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
        STOP_FILE.write_text("ferma", encoding="utf-8")
        print("Richiesta di spegnimento inviata: chiudo i trade aperti e spengo (al massimo 2 minuti)…")
        for _ in range(150):
            if not office_running(port):
                print("Bet_bot spento.")
                return 0
            _t.sleep(1)
        print("Bet_bot non si è spento entro 2 minuti: controlla la finestra del bot.")
        return 1

    if a.comando == "avvio-automatico":
        from betbot import system
        if a.valore not in ("on", "off"):
            st = system.autostart_status()
            print(f"Avvio automatico: {'attivo' if st['enabled'] else 'spento'}. Usa: betbot.py avvio-automatico on|off")
            return 0
        st = system.set_autostart(a.valore == "on")
        print("Avvio automatico attivato: Bet_bot partirà ridotto a icona quando accedi a Windows."
              if st["enabled"] else "Avvio automatico disattivato.")
        return 0

    if a.comando == "diagnosi":
        from betbot.diagnostics import run_all
        ok = run_all()
        return 0 if ok else 1

    if a.comando == "replay":
        from betbot.feeds.recorder import recorded_files
        from betbot.simulate import replay
        files = recorded_files(a.da, a.fino)
        if not files:
            print("Nessuna registrazione in runtime/recordings/. Imposta feed.record: true in config/settings.yaml, "
                  "lascia girare Bet_bot sui prezzi veri di betfair.it per qualche giorno, poi rilancia.")
            return 1
        print(f"Replay di {len(files)} giorni registrati con TUTTE le strategie attive (paper)…")
        m = asyncio.run(replay(files))
        print(f"\n{m.get('snapshots', 0)} fotografie · bankroll {m.get('initial', 0):.2f} → {m.get('bankroll', 0):.2f} € · "
              f"{m.get('bets', 0)} chiuse · ROI {m.get('roi', 0):+.2%} · kill switch: {m.get('kill_switch') or 'no'}")
        for sid, s in (m.get("by_strategy") or {}).items():
            wr = "—" if s["win_rate"] is None else f"{s['win_rate']:.0%}"
            print(f"  {sid:<28} {s['bets']:>4} chiuse · vinte {wr} · P&L {s['pnl']:+.2f} € · ROI {s['roi']:+.1%}")
        print("\nGuarda il replay nella dashboard:  python betbot.py dashboard --replay")
        return 0

    if a.comando == "simula":
        from betbot.simulate import main as simulate
        print(f"Simulo {a.ore:.0f} ore di ufficio sul feed simulato (seme {a.seed})…")
        m = simulate(a.ore, a.seed)
        print(f"\nBankroll {m['initial']:.2f} → {m['bankroll']:.2f} € · {m['bets']} chiuse · win {m['win_rate']:.0%} · "
              f"ROI {m['roi']:+.2%} · max DD {m['max_drawdown']:.1%} · kill switch: {m['kill_switch'] or 'no'}")
        for sid, s in m["by_strategy"].items():
            print(f"  {sid:<26} {s['bets']:>4} chiuse · win {s['win_rate']:.0%} · P&L {s['pnl']:+.2f} € · ROI {s['roi']:+.1%}")
        print("\nIl feed simulato serve a collaudare l'ufficio: i suoi profitti NON sono una prova della strategia.")
        print("Guarda la simulazione nella dashboard:  python betbot.py dashboard --simulazione")
        return 0

    if a.comando == "backtest":
        from betbot.backtest import EXCHANGE_DIVS, download, exchange_seasons, load, run, save
        from betbot.backtest_tennis import tennis_paths
        if a.csv:
            from pathlib import Path
            paths = [Path(x) for x in a.csv]
        else:
            divs = [d.strip() for d in a.campionati.split(",") if d.strip()] if a.campionati else EXCHANGE_DIVS
            seasons = exchange_seasons()
            print(f"Scarico lo storico con i prezzi Betfair Exchange: {len(divs)} campionati, stagioni "
                  f"{', '.join(seasons)} (solo la prima volta)…")
            paths = download(divs, seasons)
            try:
                paths += tennis_paths()
            except Exception as exc:
                print(f"Tennis non disponibile ({exc}): continuo col calcio.")
        rows = load(paths)
        print(f"{len(rows)} partite con prezzo Betfair caricate. Simulo le strategie con {load_settings()['capital']['initial']} € "
              "e le regole di betfair.it…")
        results = [run(rows, s, kill_switch=not a.senza_kill) for s in ("NAIVE_80", "S05_favoriti_exchange_v1")]
        path = save(results, rows, "ultimo")
        print(path.read_text(encoding="utf-8"))
        print(f"Report e CSV delle puntate in {path.parent}")
        return 0

    if a.comando in ("avvia", "ciclo") and office_running(port):
        print("L'ufficio sportivo è già acceso: apro la dashboard.")
        if not a.no_browser:
            webbrowser.open(f"http://localhost:{port}")
        return 0

    from betbot.core import SportOffice
    office = SportOffice(connect_feed=a.comando in ("avvia", "ciclo"))

    if a.comando == "ciclo":
        print(asyncio.run(office.run_cycle()))
    elif a.comando == "avvia":
        try:
            serve(port, background=True)
        except OSError:
            print(f"La porta {port} è occupata: Bet_bot è probabilmente già acceso.")
            return 0
        _write_pid()
        print(f"Ufficio sportivo avviato in modalità {office.settings['mode'].upper()} "
              f"(feed {office.settings['feed']['provider']}).")
        print(f"Dashboard: http://localhost:{port}   (CTRL+C per fermare)")
        if not a.no_browser:
            webbrowser.open(f"http://localhost:{port}")
        try:
            asyncio.run(office.run_forever())
        except KeyboardInterrupt:
            office.auditor.daily_report()
            print("\nBet_bot fermato. Report del giorno in runtime/reports/.")
    elif a.comando == "report":
        from betbot.agents.auditor import render_markdown
        print(render_markdown(office.auditor.daily_report(a.giorno)))
    elif a.comando == "stato":
        rs = office.store.get("risk_state") or {}
        m = office.store.get("metrics") or {}
        print(f"Bankroll {office.bankroll.total:.2f} € (capitale {office.bankroll.initial_capital:.2f}, "
              f"profitti {office.bankroll.profits:+.2f}) · drawdown {rs.get('drawdown', 0):.1%} · "
              f"kill switch: {office.store.get('kill_switch') or 'no'}")
        print(f"Chiuse {m.get('bets', 0)} · win {m.get('win_rate', 0):.0%} · ROI {m.get('roi', 0):+.2%} · "
              f"aperte {len(office.bankroll.open_bets())}")
    elif a.comando == "prova-telegram":
        from betbot.notifier import send_now
        send_now("🏟️ <b>Sports Betting Office</b>\nMessaggio di prova: le notifiche funzionano.")
        print("Messaggio inviato.")
    elif a.comando == "betfair-verifica":
        from betbot import local_settings
        from betbot.feeds.betfair import BetfairClient
        c = BetfairClient(local_settings.load()["betfair"])
        c.login()
        f = c.account_funds()
        print(f"Login riuscito. Disponibile: {f.get('availableToBetBalance')} · esposizione: {f.get('exposure')}")
    elif a.comando == "reset-kill-switch":
        reason = office.store.get("kill_switch")
        if not reason:
            print("Il kill switch non è attivo.")
            return 0
        ans = input(f"Kill switch attivo ({reason}). Confermi il reset manuale? [si/no] ")
        if ans.strip().lower() in ("si", "sì", "s"):
            office.store.set("kill_switch", None)
            office.store.set("peak_bankroll", office.bankroll.total)
            office.risk.say("Kill switch resettato manualmente dall'utente.", "ok", "kill_switch", level="WARN")
            print("Reset eseguito.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
