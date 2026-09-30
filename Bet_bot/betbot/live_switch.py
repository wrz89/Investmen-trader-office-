"""Passaggio ai soldi veri (e ritorno al paper) con un comando solo: `vai_live.bat` / `torna_paper.bat`.

Accende il live solo con la conferma scritta dell'utente ("SI"). Fa tutto quello che la dashboard chiede a mano:
login con certificato, saldo, ordine di prova (2 € a quota 1000, annullato subito: non costa nulla), interruttore
"Puntate reali", e in runtime/impostazioni.yaml mode live, esecuzione su Betfair e la strategia in live_strategies.
Il bot non deposita né preleva mai: può usare solo il saldo che c'è sul conto.
"""
from __future__ import annotations

import shutil

import yaml

from . import local_settings
from .config import LOCAL_OVERRIDE, load_yaml

LIVE_STRATEGIES = ["S10_divertimento_v1"]


def test_order(client) -> str:
    """Back di 2 € a quota 1000 (non si abbina mai) subito annullato: prova che il conto può piazzare ordini."""
    import uuid

    from .feeds.betfair import SOCCER
    cat = [m for m in client.catalogue(SOCCER, "MATCH_ODDS", 72, None, 10, lookback_hours=0) if m.get("runners")]
    if not cat:
        raise ValueError("Nessuna partita di calcio disponibile ora per la prova: riprova più tardi.")
    m = cat[0]
    ref = f"prova{uuid.uuid4().hex[:12]}"
    # vincita potenziale 2.000 €, sotto il limite ADM di 10.000 €
    r = client.place(m["marketId"], m["runners"][0]["selectionId"], "BACK", 1000.0, 2.0, fill_or_kill=False, order_ref=ref)
    client.cancel(m["marketId"], r["bet_id"])
    left = [o for o in client.current_orders(order_refs=[ref]) if o.get("status") == "EXECUTABLE"]
    if left:
        raise ValueError("L'ordine di prova è ancora aperto su Betfair: annullalo da 'Le mie scommesse' e riprova.")
    return m["event"]["name"]


def _write_override(changes: dict) -> None:
    current = {}
    if LOCAL_OVERRIDE.exists():
        shutil.copyfile(LOCAL_OVERRIDE, LOCAL_OVERRIDE.with_suffix(".yaml.bak"))
        current = yaml.safe_load(LOCAL_OVERRIDE.read_text(encoding="utf-8")) or {}

    def merge(a, b):
        for k, v in b.items():
            if isinstance(v, dict) and isinstance(a.get(k), dict):
                merge(a[k], v)
            else:
                a[k] = v
        return a
    LOCAL_OVERRIDE.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_OVERRIDE.write_text("# Le TUE impostazioni (aggiorna.bat non le tocca). Scritte anche da vai_live / torna_paper.\n"
                              + yaml.safe_dump(merge(current, changes), allow_unicode=True, sort_keys=False),
                              encoding="utf-8")


def enable(ask=input, out=print, client=None) -> bool:
    s = local_settings.load()
    bf = s["betfair"]
    if not bf.get("app_key") or not bf.get("username"):
        out("Betfair non è collegato: lancia prima collega_betfair.bat.")
        return False
    if client is None:
        from .feeds.betfair import BetfairClient
        client = BetfairClient(bf)
    client.login()
    funds = client.account_funds()
    bal = float(funds.get("availableToBetBalance") or 0)
    lim = load_yaml("risk_limits.yaml")
    out(f"\nSaldo disponibile su betfair.it: {bal:.2f} €\n")
    out("Cosa succede con i SOLDI VERI:")
    out(f"  • la strategia Divertimento punta 2 € alla volta, al massimo {lim.get('fun_max_bets_per_day', 3)} al giorno, "
        "una aperta alla volta, su calcio, tennis, basket, NFL e baseball;")
    out("  • sceglie il prezzo più vicino al giusto: in media si perde circa l'1-3% di ogni puntata (commissione e spread),")
    out("    il resto è fortuna. Non è un sistema per guadagnare;")
    out(f"  • si ferma da solo se il saldo scende sotto {lim.get('kill_below_bankroll', 20):.0f} € "
        f"e per il resto del giorno dopo {lim.get('max_daily_loss_eur', 4):.0f} € persi;")
    out("  • non deposita e non preleva mai. Le altre strategie restano in ombra (senza soldi).")
    if bal < 2:
        out("\nSaldo sotto i 2 €: non si può puntare. Deposita dal sito Betfair e riprova.")
        return False
    if str(ask('\nPer accendere le puntate reali scrivi SI (maiuscolo) e premi Invio: ')).strip() != "SI":
        out("Nessuna modifica: resti in paper.")
        return False
    event = test_order(client)
    out(f"Ordine di prova su {event}: accettato e annullato. Il conto può puntare.")
    bf.update(verified=True, test_done=True, live_enabled=True)
    local_settings.save(s)
    _write_override({"mode": "live", "execution": {"provider": "betfair"}, "feed": {"provider": "betfair"},
                     "live_strategies": LIVE_STRATEGIES})
    out("\nPUNTATE REALI ACCESE per: " + ", ".join(LIVE_STRATEGIES))
    out("Ora riavvia il bot: chiudi la finestra nera del bot e lancia di nuovo avvia.bat.")
    out("Per tornare ai soldi finti in qualunque momento: torna_paper.bat")
    return True


def disable(out=print) -> bool:
    s = local_settings.load()
    s["betfair"]["live_enabled"] = False
    local_settings.save(s)
    _write_override({"mode": "paper", "live_strategies": []})
    out("Puntate reali SPENTE: al prossimo avvio il bot torna in paper (soldi finti).")
    out("Le puntate vere già aperte restano su Betfair e si chiudono da sole a fine partita.")
    out("Riavvia il bot: chiudi la finestra nera del bot e lancia di nuovo avvia.bat.")
    return True
