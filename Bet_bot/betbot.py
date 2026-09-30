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
    python betbot.py mercurius            verifica il metodo "alla Mercurius" (modello dei gol proprio) sui prezzi Betfair
    python betbot.py lay                  backtest del lay di valore (S09) sui prezzi Betfair
    python betbot.py nfl                  NFL dal 2012: testa a testa, handicap e totale punti contro le quote di chiusura
    python betbot.py palestra             Leo rivive gli ultimi anni senza sapere i risultati, scommette e impara
    python betbot.py collega-betfair      collegamento guidato: certificato, login, app key creata da sola
    python betbot.py certificato          crea il certificato per il login Betfair (betfair.it) e lo collega al bot
    python betbot.py test-rapido [--ore 6] test in poche ore: betfair.it resta indietro rispetto a Pinnacle? (nessuna puntata)
    python betbot.py esame                esame per il live: quali strategie hanno superato i criteri sui prezzi veri
    python betbot.py copertura            quante partite e quanti crediti servono a The Odds API (e se c'è Pinnacle)
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
                                       "ferma", "avvio-automatico", "mercurius", "lay", "palestra", "esame", "copertura", "certificato", "collega-betfair", "test-rapido", "nfl"])
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
    p.add_argument("--anni", type=int, default=5, help="palestra: stagioni passate da rivivere (più quella in corso)")
    p.add_argument("--senza-giocatori", action="store_true", help="palestra: niente xG e formazioni (Understat)")
    a = p.parse_args()

    from betbot.config import RUNTIME_DIR, load_settings
    from betbot.server import is_addr_in_use, office_running, serve
    port = load_settings()["dashboard_port"]

    if a.comando == "dashboard":
        # senza opzioni: il database della modalità attuale (in live quello dei soldi veri), scelto da serve()
        db = RUNTIME_DIR / "simulazione.db" if a.simulazione else RUNTIME_DIR / "replay.db" if a.replay else None
        try:
            serve(port, background=True, db_path=db)
        except OSError as exc:
            if office_running(port):
                print(f"La porta {port} è usata da Bet_bot acceso: la sua dashboard è su http://localhost:{port}. "
                      "Per vedere la simulazione o il replay spegni prima il bot (python betbot.py ferma).")
            elif is_addr_in_use(exc):
                print(f"La porta {port} è occupata da un'altra finestra (forse un'altra dashboard): chiudila e riprova.")
            else:
                print(f"Non riesco ad aprire la porta {port}: {exc}. Cambia dashboard_port in runtime/impostazioni.yaml.")
            return 1
        print(f"Dashboard su http://localhost:{port}  (CTRL+C per chiudere)")
        if not a.no_browser:
            webbrowser.open(f"http://localhost:{port}")
        try:
            import time as _t
            while True:
                _t.sleep(3600)
        except KeyboardInterrupt:
            pass
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

    if a.comando == "nfl":
        from betbot import backtest_nfl as N
        from betbot.config import REPORTS_DIR
        md = N.report(N.run())
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (REPORTS_DIR / "backtest_nfl.md").write_text(md, encoding="utf-8")
        print(md)
        return 0

    if a.comando == "test-rapido":
        from betbot import test_rapido
        ore = a.ore if a.ore != 72 else 6.0                  # --ore vale 72 di default per simula: qui 6
        return 0 if test_rapido.run(hours=ore) else 1

    if a.comando == "collega-betfair":
        from betbot import collega_betfair
        return 0 if collega_betfair.run() else 1

    if a.comando == "certificato":
        from betbot import certificato as CT
        r = CT.create()
        print(("Certificato creato" if r["created"] else "Certificato già presente") + " e collegato al bot:")
        print(f"  certificato (da caricare su Betfair): {r['crt']}")
        print(f"  chiave privata (resta sul PC, non darla a nessuno): {r['key']}")
        print("\nOra caricalo sul tuo conto: accedi a betfair.it, apri")
        print("  https://myaccount.betfair.it/accountdetails/mysecurity?showAPI=1")
        print("alla voce 'Automated Betting Program Access' (accesso API) premi Modifica, scegli il file .crt qui sopra e")
        print("carica. Poi nella dashboard premi 'Verifica il conto'.")
        return 0

    if a.comando == "copertura":
        from betbot import copertura as C
        print("Controllo The Odds API: una chiamata (1 credito) per ogni campionato di calcio configurato…\n")
        print(C.report(C.check()))
        return 0

    if a.comando == "esame":
        from betbot import esame as E
        from betbot.config import DB_PATH, RUNTIME_DIR, load_settings
        from betbot.store import Store
        st = load_settings()
        ids = list(dict.fromkeys((st.get("active_strategies") or []) + (st.get("observe_strategies") or [])))
        print("Esame per il live (criteri in config/esame_live.yaml, decisi prima di vedere i risultati).")
        print("Contano solo i prezzi veri di betfair.it: paper col feed betfair e replay delle registrazioni.\n")
        for name, path in (("Paper", DB_PATH), ("Replay", RUNTIME_DIR / "replay.db")):
            if path.exists():
                print(f"## {name} ({path.name})\n")
                print(E.report(E.evaluate_all(Store(path), ids)))
        return 0

    if a.comando == "palestra":
        from betbot import palestra as P
        from betbot.config import REPORTS_DIR
        if not a.senza_giocatori:
            from betbot import understat as U
            y = P.seasons_back(a.anni)
            print("Scarico (una volta sola) xG e formazioni da Understat per i 5 grandi campionati…")
            print(U.download_all([2000 + int(s[:2]) for s in y]))
        print("Leo entra in palestra: rivive le partite in ordine di data senza conoscere i risultati…")
        res = P.run(years=a.anni, with_understat=not a.senza_giocatori)
        md = P.report(res)
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (REPORTS_DIR / "palestra.md").write_text(md, encoding="utf-8")
        print(md)
        return 0

    if a.comando == "lay":
        from betbot import backtest_lay as bl
        from betbot.config import REPORTS_DIR
        print("Lay di valore: Betfair contro Pinnacle sui campionati di football-data (qualche minuto)…")
        md = bl.report()
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (REPORTS_DIR / "backtest_lay.md").write_text(md, encoding="utf-8")
        print(md)
        return 0

    if a.comando == "mercurius":
        from betbot import backtest_mercurius as bm
        from betbot.config import REPORTS_DIR
        print("Metodo alla Mercurius: modello Dixon-Coles stimato giornata per giornata, peso scelto sul 2024/25, "
              "prova fuori campione sulle stagioni successive con i prezzi Betfair (qualche minuto)…")
        parts = ["# Metodo alla Mercurius su prezzi Betfair Exchange\n",
                 "Mercurius (Mercurius BI srl, 2017-2021) non ha mai pubblicato il suo algoritmo: qui se ne ricostruisce "
                 "l'architettura dichiarata con strumenti pubblici (modello dei gol proprio → confronto con l'exchange).\n"]
        for title, kw in (("Tutte le quote, EV netto ≥ 2%", {}), ("Solo favoriti ≥ 70%", {"min_prob": 0.70}),
                          ("Tutte le quote, EV netto ≥ 0%", {"min_edge": 0.0})):
            res = bm.run(**kw)
            parts.append(bm.report(res, title))
        md = "\n".join(parts)
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (REPORTS_DIR / "backtest_mercurius.md").write_text(md, encoding="utf-8")
        print(md)
        return 0

    if a.comando == "ferma":
        import time as _t
        from betbot.config import STOP_FILE
        STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
        # il file si crea anche a bot spento: avvia.bat, se sta aspettando 30 s per ripartire dopo un errore,
        # lo trova e non riparte (al prossimo avvio normale il bot lo cancella da solo)
        STOP_FILE.write_text("ferma", encoding="utf-8")
        if not office_running(port):
            print("Bet_bot non è acceso (se avvia.bat stava per riavviarlo, non ripartirà).")
            return 0
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
        from betbot.simulate import DbInUse
        files = recorded_files(a.da, a.fino)
        if not files:
            print("Nessuna registrazione in runtime/recordings/. Imposta feed.record: true in runtime/impostazioni.yaml, "
                  "lascia girare Bet_bot sui prezzi veri di betfair.it per qualche giorno, poi rilancia.")
            return 1
        print(f"Replay di {len(files)} giorni registrati con TUTTE le strategie attive (paper)…")
        try:
            m = asyncio.run(replay(files))
        except DbInUse as exc:
            print(exc)
            return 1
        print(f"\n{m.get('snapshots', 0)} fotografie · bankroll {m.get('initial', 0):.2f} → {m.get('bankroll', 0):.2f} € · "
              f"{m.get('bets', 0)} chiuse · ROI {m.get('roi', 0):+.2%} · kill switch: {m.get('kill_switch') or 'no'}")
        for sid, s in (m.get("by_strategy") or {}).items():
            wr = "—" if s["win_rate"] is None else f"{s['win_rate']:.0%}"
            print(f"  {sid:<28} {s['bets']:>4} chiuse · vinte {wr} · P&L {s['pnl']:+.2f} € · ROI {s['roi']:+.1%}")
        print("\nGuarda il replay nella dashboard:  python betbot.py dashboard --replay")
        return 0

    if a.comando == "simula":
        from betbot.simulate import DbInUse, main as simulate
        print(f"Simulo {a.ore:.0f} ore di ufficio sul feed simulato (seme {a.seed})…")
        try:
            m = simulate(a.ore, a.seed)
        except DbInUse as exc:
            print(exc)
            return 1
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
        results = [run(rows, s, kill_switch=not a.senza_kill) for s in ("NAIVE_80", "S05_favoriti_exchange_v1", "S05_favoriti_exchange_v2")]
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
            # la dashboard legge lo STESSO database dell'ufficio (in live runtime/betbot_live.db)
            serve(port, background=True, db_path=office.store.path, office=True)
        except OSError as exc:
            if is_addr_in_use(exc):
                print(f"La porta {port} è occupata da un altro programma (forse la dashboard di simula.bat o di un "
                      "replay): chiudi quella finestra. Bet_bot NON è partito.")
            else:
                print(f"Non riesco ad aprire la porta {port} ({exc}): cambia dashboard_port in "
                      "runtime/impostazioni.yaml. Bet_bot NON è partito.")
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
            # CTRL+C = spegnimento ordinato come `betbot.py ferma`: niente nuove puntate, trade aperti chiusi
            print("\nChiudo i trade aperti prima di spegnere… (CTRL+C di nuovo per uscire subito)")
            try:
                asyncio.run(office.shutdown())
            except KeyboardInterrupt:
                print("Uscita immediata: eventuali trade aperti si riprendono al prossimo avvio.")
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
