"""SPORTS BETTING OFFICE — comandi.

    python sport.py avvia                 avvia l'ufficio (ciclo asincrono) + dashboard su http://localhost:8766
    python sport.py ciclo                 esegue un solo ciclo
    python sport.py simula --ore 72       simulazione accelerata di tutto l'ufficio sul feed simulato
    python sport.py backtest              scarica lo storico reale (football-data.co.uk) e confronta le strategie
    python sport.py rischio --quota 1.22 --vinte 0.80 --puntata 0.02
                                          Monte Carlo: cosa succede al bankroll con quella quota e quel win rate
    python sport.py dashboard [--simulazione]   apre solo la dashboard (anche sui dati della simulazione)
    python sport.py report                report del giorno
    python sport.py stato                 riepilogo veloce
    python sport.py prova-telegram        invia un messaggio di prova
    python sport.py betfair-verifica      login Betfair e saldo del conto
    python sport.py reset-kill-switch     riattiva l'ufficio dopo un kill switch (decisione umana)
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import webbrowser


def _write_pid() -> None:
    """runtime/sport/sport.pid: serve ad aggiorna.bat per chiudere l'ufficio sportivo prima di aggiornare."""
    import atexit
    import os
    from sport_office.config import RUNTIME_DIR
    pid = RUNTIME_DIR / "sport.pid"
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
                                       "prova-telegram", "betfair-verifica", "reset-kill-switch"])
    p.add_argument("--ore", type=float, default=72, help="simula: ore simulate")
    p.add_argument("--seed", type=int, default=7, help="simula: seme del mondo simulato")
    p.add_argument("--campionati", default="I1,I2,E0,E1,SP1,D1,F1,N1,P1", help="backtest: codici football-data")
    p.add_argument("--stagioni", type=int, default=10, help="backtest: quante stagioni fino al 2024/25")
    p.add_argument("--csv", nargs="*", help="backtest: uno o più CSV propri invece del download")
    p.add_argument("--senza-kill", action="store_true", help="backtest: ignora il kill switch per misurare il segnale")
    p.add_argument("--quota", type=float, default=1.22)
    p.add_argument("--vinte", type=float, default=0.80)
    p.add_argument("--puntata", type=float, default=0.02)
    p.add_argument("--simulazione", action="store_true", help="dashboard: mostra i dati dell'ultima simulazione")
    p.add_argument("--giorno", help="report: AAAA-MM-GG")
    p.add_argument("--no-browser", action="store_true")
    a = p.parse_args()

    from sport_office.config import RUNTIME_DIR, load_settings
    from sport_office.server import office_running, serve
    port = load_settings()["dashboard_port"]

    if a.comando == "dashboard":
        db = RUNTIME_DIR / "simulazione.db" if a.simulazione else None
        print(f"Dashboard su http://localhost:{port}  (CTRL+C per chiudere)")
        if not a.no_browser:
            webbrowser.open(f"http://localhost:{port}")
        serve(port, db_path=db)
        return 0

    if a.comando == "rischio":
        from sport_office.backtest import montecarlo
        r = montecarlo(a.vinte, a.quota, a.puntata)
        print(f"Quota {a.quota:.2f}, vinte {a.vinte:.0%}, puntata {a.puntata:.1%} del bankroll, 1000 puntate × 2000 scenari\n")
        print(f"  pareggio (win rate minimo)   {r['breakeven']:.1%}")
        print(f"  valore atteso per puntata    {r['ev_per_bet']:+.2%}")
        print(f"  bankroll finale mediano      ×{r['median_final']:.2f}   (5% peggiori ×{r['p5_final']:.2f}, 5% migliori ×{r['p95_final']:.2f})")
        print(f"  probabilità di finire sotto  {r['prob_loss']:.0%}")
        print(f"  probabilità di kill switch   {r['prob_kill_switch']:.0%}  (drawdown ≥ 12%)")
        print(f"  drawdown massimo mediano     {r['median_max_dd']:.1%}")
        return 0

    if a.comando == "simula":
        from sport_office.simulate import main as simulate
        print(f"Simulo {a.ore:.0f} ore di ufficio sul feed simulato (seme {a.seed})…")
        m = simulate(a.ore, a.seed)
        print(f"\nBankroll {m['initial']:.2f} → {m['bankroll']:.2f} € · {m['bets']} chiuse · win {m['win_rate']:.0%} · "
              f"ROI {m['roi']:+.2%} · max DD {m['max_drawdown']:.1%} · kill switch: {m['kill_switch'] or 'no'}")
        for sid, s in m["by_strategy"].items():
            print(f"  {sid:<26} {s['bets']:>4} chiuse · win {s['win_rate']:.0%} · P&L {s['pnl']:+.2f} € · ROI {s['roi']:+.1%}")
        print("\nIl feed simulato serve a collaudare l'ufficio: i suoi profitti NON sono una prova della strategia.")
        print("Guarda la simulazione nella dashboard:  python sport.py dashboard --simulazione")
        return 0

    if a.comando == "backtest":
        from sport_office.backtest import default_seasons, download, load, run, save
        if a.csv:
            from pathlib import Path
            paths = [Path(x) for x in a.csv]
        else:
            divs = [d.strip() for d in a.campionati.split(",") if d.strip()]
            print(f"Scarico lo storico: {len(divs)} campionati × {a.stagioni} stagioni (solo la prima volta)…")
            paths = download(divs, default_seasons(a.stagioni))
        rows = load(paths)
        print(f"{len(rows)} partite caricate. Simulo le strategie…")
        results = [run(rows, s, kill_switch=not a.senza_kill) for s in ("NAIVE_80", "S01_favoriti_v1", "S03_surebet_v1")]
        path = save(results, rows, "ultimo")
        print(path.read_text(encoding="utf-8"))
        print(f"Report e CSV delle puntate in {path.parent}")
        return 0

    if a.comando in ("avvia", "ciclo") and office_running(port):
        print("L'ufficio sportivo è già acceso: apro la dashboard.")
        if not a.no_browser:
            webbrowser.open(f"http://localhost:{port}")
        return 0

    from sport_office.core import SportOffice
    office = SportOffice(connect_feed=a.comando in ("avvia", "ciclo"))

    if a.comando == "ciclo":
        print(asyncio.run(office.run_cycle()))
    elif a.comando == "avvia":
        try:
            serve(port, background=True)
        except OSError:
            print(f"La porta {port} è occupata: l'ufficio è probabilmente già acceso.")
            return 1
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
            print("\nUfficio fermato. Report del giorno in runtime/sport/reports/.")
    elif a.comando == "report":
        from sport_office.agents.auditor import render_markdown
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
        from sport_office.notifier import send_now
        send_now("🏟️ <b>Sports Betting Office</b>\nMessaggio di prova: le notifiche funzionano.")
        print("Messaggio inviato.")
    elif a.comando == "betfair-verifica":
        from sport_office import local_settings
        from sport_office.feeds.betfair import BetfairClient
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
