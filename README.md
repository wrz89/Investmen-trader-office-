# Crypto Trading Office

Ufficio quantitativo con 7 agenti per il trading crypto di breve periodo.
**Modalità attuale: PAPER TRADING**, cioè prezzi veri e soldi finti. Niente leva e niente short.

| # | Agente | Cosa fa | File |
|---|---|---|---|
| 1 | Portfolio Manager | autorizza, sospende e confronta le strategie | `office/agents/portfolio_manager.py` |
| 2 | Market Scanner | legge prezzi, spread, book, volatilità e correlazioni, poi trasforma i segnali in opportunità nette | `office/agents/market_scanner.py` |
| 3 | Strategy Researcher | libreria strategie versionata e idee in coda | `office/agents/strategy_researcher.py` |
| 4 | Quant Researcher | walk-forward, hold-out, Monte Carlo, Deflated Sharpe, stress costi | `office/agents/quant_researcher.py`, `office/validation.py` |
| 5 | Risk Manager | **veto assoluto**, sizing, kill switch | `office/agents/risk_manager.py` |
| 6 | Execution Agent | esegue solo ordini approvati (paper) e fa i controlli pre/post ordine | `office/agents/execution.py` |
| 7 | Auditor | registro trade immutabile e report giornaliero | `office/agents/auditor.py` |

Nessun LLM sta nel percorso che porta a un ordine: tutte le decisioni sono regole numeriche.

## Installazione su Windows

1. Installa **Python 3.10 o superiore** da python.org. Durante l'installazione spunta "Add Python to PATH".
2. Scarica questa cartella ed esegui un doppio clic su **`installa.bat`** (va fatto una volta sola).
3. Esegui un doppio clic su **`ricerca.bat`**: scarica circa 3 anni di storico da Bybit e valida le strategie.
4. Esegui un doppio clic su **`avvia_ufficio.bat`**: l'ufficio parte e si apre la dashboard su `http://localhost:8765`.

Per aggiornare il programma in seguito: doppio clic su **`aggiorna.bat`**. Scarica l'ultima versione da GitHub e sostituisce solo il codice: i tuoi dati in `runtime/` e l'installazione in `.venv/` restano intatti.

Da terminale, gli stessi comandi sono:

```
python ufficio.py ricerca       # valida le strategie nuove
python ufficio.py avvia         # ufficio + dashboard
python ufficio.py ciclo         # un solo ciclo
python ufficio.py report        # report del giorno
python ufficio.py stato         # riepilogo veloce
python ufficio.py reset-kill-switch
python ufficio.py mercati       # coppie EUR/USDC più liquide su Bybit
```

Tutti i dati prodotti (database, storico, registro strategie, report) finiscono in `runtime/`.

## Impostazioni dal browser

Nella dashboard, il pulsante **Impostazioni** in alto a destra permette di:

- **collegare Telegram**, seguendo i 4 passi guidati: si crea il bot con @BotFather, si incolla il token, si preme AVVIA sul bot e si invia un messaggio di prova. Arrivano notifiche su trade, veti, allarmi e il report serale;
- **scegliere quali notifiche ricevere**;
- **attivare l'avvio automatico**: l'ufficio parte da solo, ridotto a icona, quando accedi a Windows;
- **tenere sveglio il PC** mentre l'ufficio lavora;
- consultare i **criteri per passare al capitale reale** (`config/promotion_criteria.yaml`).

Il token del bot resta in `runtime/local_settings.json`, solo sul tuo PC. Può esistere un solo ufficio acceso alla volta: se lo riavvii mentre è già acceso, si apre solo la dashboard.

## Regole applicate dal codice

- **Una strategia opera solo se supera tutti i criteri** di `config/quant_gates.yaml`, calcolati fuori campione e al netto dei costi.
- **I limiti di rischio** stanno in `config/risk_limits.yaml` e vengono sigillati all'avvio: se il file cambia mentre l'ufficio è acceso, tutto viene bloccato.
- **Kill switch**: con un drawdown del 10% l'ufficio si ferma. Il reset è solo manuale.
- **Timeframe per strategia**: una strategia può dichiarare `TIMEFRAME = "4h"`; ricerca, scanner e gestione posizioni usano le sue candele.
- **Mai sovrascrivere una strategia**: ogni versione è un file (`s01_momentum_v1.py`). Se il codice di una versione registrata cambia, la strategia viene bloccata. Per modificarla si crea `s01_momentum_v2.py`.
- **Registro immutabile**: il database rifiuta modifiche e cancellazioni dei trade.
- **Dati dubbi = nessuna operazione**: candele vecchie, buchi, prezzi incoerenti, API instabile o movimenti anomali bloccano l'operatività.
- **Live non disponibile**: il codice rifiuta la modalità live. Verrà aggiunta solo dopo un paper trading superato.

## Note pratiche

- **PC acceso**: l'ufficio lavora solo mentre il PC è acceso e la finestra resta aperta. Disattiva la sospensione automatica nelle impostazioni di alimentazione. Se il PC è spento, l'ufficio non opera, come previsto dalla regola 12.
- **Bybit EU**: se i dati pubblici non rispondono, in `config/settings.yaml` imposta `options: {hostname: bybit.eu}`. Se le coppie in EUR hanno spread alti, lo Scanner lo segnala e il Risk Manager blocca: valuta le coppie /USDC.
- **Commissioni**: verifica le tue commissioni reali e aggiorna `costs` in `config/settings.yaml`.
