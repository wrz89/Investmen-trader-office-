"""Collegamento guidato a Betfair Exchange Italia, dal PC dell'utente, senza strumenti per sviluppatori.

1. crea il certificato (se manca) e apre la pagina di betfair.it dove caricarlo: è l'unico passo a mano;
2. chiede utente e password (la password non si vede mentre la scrivi e resta cifrata sul PC);
3. fa il login con il certificato e crea (o ritrova) da solo la app key "delayed" sul conto;
4. salva tutto nelle impostazioni del bot e mostra il saldo.
Se Betfair rifiuta il login senza una app key valida, spiega il ripiego (lo strumento di Betfair).
"""
from __future__ import annotations

import getpass
import os
import uuid
import webbrowser

SECURITY_PAGE = "https://myaccount.betfair.it/accountdetails/mysecurity?showAPI=1"


def _pick_delayed(apps: list[dict]) -> str | None:
    """Tra le app del conto, la chiave 'delayed' (gratuita, attiva subito); se manca, la prima attiva."""
    versions = [v for a in apps or [] for v in a.get("appVersions") or []]
    for v in versions:
        if v.get("delayData") and v.get("applicationKey"):
            return v["applicationKey"]
    for v in versions:
        if v.get("active") and v.get("applicationKey"):
            return v["applicationKey"]
    return None


def app_key_for(client) -> tuple[str, bool]:
    """(chiave delayed, creata adesso?) usando la sessione del client già autenticato. Le due chiamate partono SENZA
    X-Application: Betfair non la richiede e rifiuterebbe il segnaposto usato per il login (AANGX-0004)."""
    key = _pick_delayed(client.developer_app_keys())
    if key:
        return key, False
    app = client.create_developer_app_keys(f"BetBot{uuid.uuid4().hex[:8]}")
    key = _pick_delayed([app] if isinstance(app, dict) else app)
    if not key:
        raise RuntimeError("Betfair non ha restituito una app key")
    return key, True


def verify_funds(client, created: bool, out, sleep, wait_s: float = 180, every_s: float = 20) -> dict | None:
    """Saldo del conto con la chiave nuova. Una chiave APPENA creata viene rifiutata per 1-3 minuti: si riprova solo
    getAccountFunds, con la stessa sessione e senza rifare il login (che ha un blocco di 60 s tra un tentativo e l'altro)."""
    from .feeds.betfair import INVALID_APP_KEY_MSG, BetfairError, is_invalid_app_key
    waited, told = 0.0, False
    while True:
        try:
            return client.account_funds()
        except BetfairError as exc:
            not_active = is_invalid_app_key(str(exc)) or INVALID_APP_KEY_MSG in str(exc)
            if not (created and not_active) or waited >= wait_s:
                out(f"   Verifica del saldo non riuscita: {exc}")
                return None
            if not told:
                out("   Betfair sta attivando la chiave appena creata, attendo (di solito 1-3 minuti)…")
                told = True
            sleep(every_s)
            waited += every_s


def use_betfair_feed() -> str:
    """Scrive (o aggiorna) runtime/impostazioni.yaml: prezzi veri di Betfair, sempre in paper (niente soldi veri)."""
    import yaml
    from .config import LOCAL_OVERRIDE
    data = {}
    if LOCAL_OVERRIDE.exists():
        data = yaml.safe_load(LOCAL_OVERRIDE.read_text(encoding="utf-8")) or {}
    data.setdefault("mode", "paper")
    data["feed"] = {**(data.get("feed") or {}), "provider": "betfair"}
    LOCAL_OVERRIDE.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_OVERRIDE.write_text("# Impostazioni personali di Bet_bot (gli aggiornamenti non le toccano)\n"
                              + yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return str(LOCAL_OVERRIDE)


def run(ask=input, ask_secret=getpass.getpass, open_url=webbrowser.open, out=print, sleep=None) -> bool:
    import time
    sleep = sleep or time.sleep
    from . import certificato, local_settings
    from .feeds.betfair import BetfairClient, BetfairError
    out("=== Collegamento a Betfair Exchange Italia ===\n")
    c = certificato.create()
    out(("1) Certificato creato: " if c["created"] else "1) Certificato già pronto: ") + c["crt"])
    out("\n2) UNICO PASSO A MANO: caricalo sul tuo conto Betfair.")
    out("   Si apre la pagina di betfair.it (accedi se te lo chiede). Alla voce 'Automated Betting Program Access'")
    out("   premi 'Modifica', scegli il file client-2048.crt dalla cartella che si apre e carica.")
    out(f"   Pagina: {SECURITY_PAGE}")
    try:
        open_url(SECURITY_PAGE)
        if os.name == "nt":
            os.startfile(os.path.dirname(c["crt"]))        # noqa: S606 — apre la cartella in Esplora risorse
    except Exception:
        pass
    ask("\n   Quando hai caricato il certificato premi INVIO… ")
    s = local_settings.load()
    bf = s["betfair"]
    user = ask(f"\n3) Nome utente betfair.it{' [' + bf['username'] + ']' if bf.get('username') else ''}: ").strip() or bf.get("username", "")
    pwd = ask_secret("   Password (non si vede mentre scrivi): ") or bf.get("password", "")
    creds = {**bf, "username": user, "password": pwd, "cert_file": c["crt"], "key_file": c["key"],
             "app_key": bf.get("app_key") or "BetBot"}   # per il login con certificato basta un nome qualsiasi
    client = BetfairClient(creds)
    try:
        client.login()
    except BetfairError as exc:
        out(f"\nLogin non riuscito: {exc}")
        out("Controlla utente e password e che il certificato risulti caricato sulla pagina di betfair.it, poi riprova.")
        return False
    out("   Login con certificato riuscito.")
    try:
        key, created = app_key_for(client)
    except Exception as exc:
        out(f"\nNon riesco a creare la app key da solo ({exc}).")
        out("Ripiego: crea la chiave 'Delayed' con lo strumento Accounts di Betfair (createDeveloperAppKeys) e incollala")
        out("nella dashboard, Impostazioni → Betfair Exchange Italia. Utente, password e certificato sono già salvati.")
        s["betfair"].update(username=user, password=pwd, cert_file=c["crt"], key_file=c["key"])
        local_settings.save(s)
        return False
    out(f"4) App key {'creata' if created else 'ritrovata'} sul tuo conto (versione delayed, gratuita).")
    client.app_key = key
    s["betfair"].update(username=user, password=pwd, cert_file=c["crt"], key_file=c["key"], app_key=key,
                        verified=False, test_done=False, live_enabled=False)
    funds = verify_funds(client, created, out, sleep)
    if funds is not None:
        s["betfair"]["verified"] = True
        out(f"5) Conto verificato: saldo disponibile {float(funds.get('availableToBetBalance') or 0):.2f} €.")
    else:
        out("5) Chiave salvata. Tra qualche minuto, nella dashboard, premi Impostazioni → Betfair → 'Verifica il conto'.")
    local_settings.save(s)
    path = use_betfair_feed()
    out(f"6) Impostazioni scritte in {path}: prezzi veri di Betfair, modalità paper (niente soldi veri).")
    out("\nFatto. Tutto è salvato sul tuo PC (password cifrata). Ora avvia.bat, poi nella dashboard")
    out("Impostazioni → Betfair → 'Ordine di prova' (costa zero).")
    return True
