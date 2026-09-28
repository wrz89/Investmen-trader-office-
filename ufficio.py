"""CRYPTO TRADING OFFICE — comandi.

    python ufficio.py ricerca            scarica lo storico e valida le strategie nuove
    python ufficio.py avvia              avvia l'ufficio (paper trading) + dashboard
    python ufficio.py ciclo              esegue un solo ciclo operativo
    python ufficio.py dashboard          apre solo la dashboard
    python ufficio.py report             genera il report giornaliero di oggi
    python ufficio.py stato              riepilogo veloce nel terminale
    python ufficio.py reset-kill-switch  riattiva l'ufficio dopo un kill switch (decisione umana)
"""
from __future__ import annotations

import argparse
import json
import sys
import webbrowser


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
                                            "reset-kill-switch", "snapshot"])
    parser.add_argument("--exchange", help="sovrascrive l'exchange dei dati live (es. kraken)")
    parser.add_argument("--storico", help="sovrascrive l'exchange per lo storico (es. bitstamp)")
    parser.add_argument("--giorno", help="giorno del report, AAAA-MM-GG")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--file", default="snapshot.json")
    args = parser.parse_args()

    from office.config import load_settings
    from office.core import Office
    from office.server import serve

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

    offline = args.comando in ("report", "stato", "reset-kill-switch", "snapshot")
    office = Office(overrides, connect_market=not offline)
    if args.exchange:
        office.derivatives = None

    if args.comando == "ricerca":
        results = office.research(args.storico)
        if not results:
            from office import registry, strategies
            print("Nessuna nuova strategia da validare. Esiti gia' registrati:")
            results = [v for m in strategies.discover() if (v := registry.load_validation(m.STRATEGY_ID))]
        for r in results:
            m = r["metrics"]
            print(f"\n{r['strategy_id']}: {r['verdict']}  (dati {r.get('data_source')}, {r.get('timeframe')})")
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
        serve(port, background=True)
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
    elif args.comando == "snapshot":
        from office.state import build_state
        with open(args.file, "w", encoding="utf-8") as fh:
            json.dump(build_state(office.store), fh, default=str, ensure_ascii=False)
        print(f"Snapshot salvato in {args.file}")


if __name__ == "__main__":
    sys.exit(main())
