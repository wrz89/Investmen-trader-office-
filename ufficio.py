"""CRYPTO TRADING OFFICE — comandi.

    python ufficio.py ricerca            scarica lo storico e valida le strategie nuove
    python ufficio.py avvia              avvia l'ufficio (paper trading) + dashboard
    python ufficio.py ciclo              esegue un solo ciclo operativo
    python ufficio.py dashboard          apre solo la dashboard
    python ufficio.py report             genera il report giornaliero di oggi
    python ufficio.py stato              riepilogo veloce nel terminale
    python ufficio.py reset-kill-switch  riattiva l'ufficio dopo un kill switch (decisione umana)
    python ufficio.py mercati            elenca le coppie EUR/USDC più liquide dell'exchange
    python ufficio.py nexus              rapporto di Nexus (capitale di 30 € con libro separato)
    python ufficio.py nexus --spesa ID --euro X --nota "..."     registra una spesa autorizzata
    python ufficio.py nexus --incasso ID --euro X --nota "..."   registra un incasso
    python ufficio.py nexus --trazione ID --valore N             aggiorna la metrica di trazione
"""
from __future__ import annotations

import argparse
import json
import sys
import webbrowser


def _write_pid() -> None:
    """runtime/office.pid: serve ad aggiorna.bat per chiudere l'ufficio acceso (anche quello nascosto
    dell'avvio automatico) prima di aggiornare, così non resta in memoria la versione vecchia."""
    import atexit
    import os
    from office.config import RUNTIME_DIR
    pid_file = RUNTIME_DIR / "office.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="ascii")

    def _cleanup():
        try:
            if pid_file.read_text(encoding="ascii").strip() == str(os.getpid()):
                pid_file.unlink()
        except OSError:
            pass
    atexit.register(_cleanup)


def main() -> None:
    # console di Windows: mai bloccarsi per un carattere non stampabile
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    try:
        import ccxt, numpy, pandas, yaml, tzdata  # noqa: F401
    except ImportError as exc:
        print(f"Libreria mancante: {exc.name}. Esegui di nuovo installa.bat e controlla che finisca senza errori.")
        return 1
    parser = argparse.ArgumentParser(description="Crypto Trading Office")
    parser.add_argument("comando", choices=["ricerca", "avvia", "ciclo", "dashboard", "report", "stato",
                                            "reset-kill-switch", "snapshot", "mercati", "nexus"])
    parser.add_argument("--exchange", help="sovrascrive l'exchange dei dati live (es. kraken)")
    parser.add_argument("--storico", help="sovrascrive l'exchange per lo storico (es. bitstamp)")
    parser.add_argument("--giorno", help="giorno del report, AAAA-MM-GG")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--file", default="snapshot.json")
    parser.add_argument("--spesa", metavar="ID", help="nexus: registra una spesa per l'ipotesi ID")
    parser.add_argument("--incasso", metavar="ID", help="nexus: registra un incasso dall'ipotesi ID")
    parser.add_argument("--trazione", metavar="ID", help="nexus: aggiorna la trazione dell'ipotesi ID")
    parser.add_argument("--euro", type=float, help="nexus: importo in euro")
    parser.add_argument("--valore", type=float, help="nexus: valore della metrica di trazione")
    parser.add_argument("--nota", default="", help="nexus: nota per il libro")
    args = parser.parse_args()

    from office.config import load_settings
    from office.core import Office
    from office.server import office_running, serve

    overrides = {"exchange.data": args.exchange, "exchange.history": args.storico}
    if args.exchange:
        overrides["exchange.derivatives_context"] = None

    if args.comando == "dashboard":
        port = load_settings()["dashboard_port"]
        print(f"Dashboard su http://localhost:{port}  (CTRL+C per chiudere)")
        if not args.no_browser:
            webbrowser.open(f"http://localhost:{port}")
        serve(port)
        return

    if args.comando == "mercati":
        return list_markets(args.exchange or load_settings()["exchange"]["data"],
                            load_settings()["exchange"]["history"])

    port = load_settings()["dashboard_port"]
    if args.comando in ("avvia", "ciclo") and office_running(port):
        # un solo ufficio alla volta: due uffici accesi raddoppierebbero le operazioni
        print("L'ufficio è già acceso (magari dall'avvio automatico): apro la dashboard.")
        if not args.no_browser:
            webbrowser.open(f"http://localhost:{port}")
        return 0

    offline = args.comando in ("report", "stato", "reset-kill-switch", "snapshot", "nexus")
    office = Office(overrides, connect_market=not offline)
    if args.exchange:
        office.derivatives = None

    if args.comando == "ricerca":
        results = office.research(args.storico)
        if not results:
            from office import registry, strategies
            print("Nessuna nuova strategia da validare. Esiti gia' registrati:")
            from office.backtest import CostModel
            per_side = CostModel.from_settings(load_settings()).per_side
            results = [v for m in strategies.discover() if (v := registry.load_validation(m.STRATEGY_ID))]
            results += [a for m in strategies.discover() if (a := registry.load_cost_audit(m.STRATEGY_ID, per_side))]
        for r in results:
            m = r["metrics"]
            label = " · VERIFICA COSTI REALI" if r.get("audit_of") else ""
            print(f"\n{r['strategy_id']}{label}: {r['verdict']}  (dati {r.get('data_source')}, {r.get('timeframe')}, "
                  f"costi {r['costs']['per_side'] * 100:.2f}% per lato)")
            print(f"  trade OOS {m['trades']} | PF {m['profit_factor']:.2f} | Sharpe {m['sharpe_annual']:.2f} | "
                  f"win {m['win_rate']:.0%} | netto medio {m['expectancy_net'] * 100:+.3f}%")
            for c in r["checks"]:
                print(f"   {'✔' if c['passed'] else '✘'} {c['label']}: {c['value']:.4g} (soglia {c['threshold']})")
        if not results:
            print("Nessuna strategia validata finora.")
    elif args.comando == "ciclo":
        print(office.run_cycle())
    elif args.comando == "avvia":
        port = office.settings["dashboard_port"]
        try:
            serve(port, background=True)
        except OSError:
            print(f"La porta {port} è occupata: l'ufficio è probabilmente già acceso. Chiudo questo avvio.")
            return 1
        _write_pid()
        print(f"Ufficio avviato in modalità {office.settings['mode'].upper()}.")
        print(f"Dashboard: http://localhost:{port}   (CTRL+C per fermare)")
        if not args.no_browser:
            webbrowser.open(f"http://localhost:{port}")
        try:
            office.run_forever()
        except KeyboardInterrupt:
            office.auditor.daily_report()
            print("\nUfficio fermato. Report del giorno salvato in runtime/reports/.")
    elif args.comando == "report":
        report = office.auditor.daily_report(args.giorno)
        from office.agents.auditor import render_markdown
        print(render_markdown(report))
    elif args.comando == "stato":
        rs = office.store.get("risk_state") or {}
        print(f"Equity: {rs.get('equity', office.account.cash):.2f} | cash {office.account.cash:.2f} | "
              f"kill switch: {office.store.get('kill_switch') or 'no'}")
        for s in office.store.query("SELECT * FROM strategy_status ORDER BY strategy_id"):
            print(f"  {s['strategy_id']}: {s['status']} — {s['reason']}")
    elif args.comando == "reset-kill-switch":
        reason = office.store.get("kill_switch")
        if not reason:
            print("Il kill switch non è attivo.")
            return
        answer = input(f"Kill switch attivo ({reason}). Confermi il reset manuale? [si/no] ")
        if answer.strip().lower() in ("si", "sì", "s"):
            office.store.set("kill_switch", None)
            office.store.set("peak_equity", office.store.get("risk_state", {}).get("equity"))
            office.risk.say("Kill switch resettato manualmente dall'utente.", "ok", "kill_switch", level="WARN")
            print("Reset eseguito.")
    elif args.comando == "nexus":
        from office.nexus import render_report
        n = office.nexus
        try:
            if args.spesa:
                n.spend(args.spesa, args.euro or 0, args.nota)
            elif args.incasso:
                n.income(args.incasso, args.euro or 0, args.nota)
            elif args.trazione:
                n.record_traction(args.trazione, args.valore or 0)
        except ValueError as exc:
            print(f"Rifiutato: {exc}")
            return 1
        print(render_report(n.decide()))
    elif args.comando == "snapshot":
        from office.state import build_state
        with open(args.file, "w", encoding="utf-8") as fh:
            json.dump(build_state(office.store), fh, default=str, ensure_ascii=False)
        print(f"Snapshot salvato in {args.file}")


def list_markets(exchange_id: str, history_id: str) -> None:
    """Coppie spot in EUR/USDC ordinate per volume, con spread e disponibilità di storico."""
    from office.market import MarketData
    md = MarketData(exchange_id, timeout_ms=20000)
    markets = md.load_markets()
    symbols = [s for s, m in markets.items()
               if m.get("spot") and m.get("active", True) and m.get("quote") in ("EUR", "USDC")
               and m.get("base") not in ("USDC", "USDT", "EURC", "EURT", "DAI", "PYUSD", "FDUSD", "EURI")]
    tickers = md.ex.fetch_tickers(symbols)
    try:
        hist = set(MarketData(history_id, timeout_ms=20000).load_markets())
    except Exception:
        hist = set()
    rows = []
    for s, t in tickers.items():
        bid, ask = t.get("bid"), t.get("ask")
        if not bid or not ask:
            continue
        spread = (ask - bid) / ((ask + bid) / 2) * 10_000
        vol = t.get("quoteVolume") or 0
        rows.append((vol, s, spread))
    rows.sort(reverse=True)
    print(f"Coppie spot su {exchange_id} (storico su {history_id}: 'si' se disponibile)\n")
    print(f"{'coppia':<14}{'volume 24h':>16}{'spread bp':>11}  storico")
    for vol, s, spread in rows[:25]:
        print(f"{s:<14}{vol:>16,.0f}{spread:>11.1f}  {'si' if s in hist else 'no'}")


if __name__ == "__main__":
    sys.exit(main())
