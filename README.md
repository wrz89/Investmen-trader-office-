# Crypto Trading Office

Ufficio quantitativo con 8 agenti per il trading crypto di breve periodo.
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
| 8 | News Analyst (Nora) | legge notizie pubbliche e Fear & Greed; con una notizia ad alto rischio su un asset (o su USDC/Bybit) chiede il blocco dei nuovi ingressi per 12 ore. Non apre mai trade | `office/agents/news_analyst.py`, `config/news.yaml` |

Nessun LLM sta nel percorso che porta a un ordine: tutte le decisioni sono regole numeriche.

## Installazione su Windows

1. Installa **Python 3.10 o superiore** da python.org. Durante l'installazione spunta "Add Python to PATH".
2. Scarica questa cartella ed esegui un doppio clic su **`installa.bat`** (va fatto una volta sola).
3. Esegui un doppio clic su **`ricerca.bat`**: scarica circa 3 anni di storico da Bybit e valida le strategie.
4. Esegui un doppio clic su **`avvia_ufficio.bat`**: l'ufficio parte e si apre la dashboard su `http://localhost:8765`.

Per aggiornare il programma in seguito: doppio clic su **`aggiorna.bat`**. Scarica l'ultima versione da GitHub e sostituisce solo il codice: i tuoi dati in `runtime/` e l'installazione in `.venv/` restano intatti.

Se il repository è **privato**, `aggiorna.bat` ha bisogno di un token di sola lettura:

1. GitHub → foto profilo → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. *Repository access*: **Only select repositories** → questo repository. *Permissions → Repository permissions → Contents*: **Read-only**. Scadenza a piacere.
3. Copia il token e salvalo, con il Blocco note, nel file `runtime\github_token.txt` (solo il token, nient'altro).

Il token resta sul tuo PC (la cartella `runtime` non va mai su GitHub) e permette solo di leggere questo repository.

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
- **Le notizie possono solo frenare**: gli allarmi di Nora aggiungono un controllo al Risk Manager, non generano mai ordini. Solo fonti pubbliche.
- **Piano di accumulo** (`config/accumulation.yaml`, `office/accumulation.py`): 50 € al mese in BTC dal giorno 5, strada più economica tra EUR→BTC ed EUR→USDC→BTC, libro separato e immutabile. Franco può solo rimandare l'acquisto, mai ingrandirlo; niente vendite automatiche. Soldi veri solo dopo 2 acquisti paper riusciti e una decisione dell'utente.
- **Ordini limite nell'accumulo**: offerta al miglior prezzo di acquisto (maker 0,10%); dopo 24 ore senza esecuzione si annulla e si compra a mercato. In paper conta come eseguito solo se il prezzo scende sotto il limite.
- **Osservatorio funding** (`config/funding_watch.yaml`, `office/funding_watch.py`): solo lettura dei tassi di finanziamento dei perpetui, stima netta sul capitale del "compro + vendo il perpetuo". Nessun ordine; revisione fissata prima di osservare (90 giorni, ≥ 5% netto).
- **Strategie lente** (`SIZING = "allocation"`, es. STRATEGY_06): size fissa al tetto per asset, validate sui rendimenti giornalieri contro compra e tieni (criteri `slow` in `config/quant_gates.yaml`).
- **Costi reali**: se le commissioni in `settings.yaml` superano quelle della validazione, `ricerca` riverifica le strategie approvate (file separato `*.costaudit-*.json`); l'esito può solo bocciare.
- **Il piano d'investimento** (`config/investment_plan.yaml`) è mostrato nella dashboard: profilo, quote massime, tempi, proiezioni e rischi.
- **Live non disponibile**: il codice rifiuta la modalità live. Verrà aggiunta solo dopo un paper trading superato.

## Note pratiche

- **PC acceso**: l'ufficio lavora solo mentre il PC è acceso e la finestra resta aperta. Disattiva la sospensione automatica nelle impostazioni di alimentazione. Se il PC è spento, l'ufficio non opera, come previsto dalla regola 12.
- **Bybit EU**: se i dati pubblici non rispondono, in `config/settings.yaml` imposta `options: {hostname: bybit.eu}`. Se le coppie in EUR hanno spread alti, lo Scanner lo segnala e il Risk Manager blocca: valuta le coppie /USDC.
- **Commissioni**: verifica le tue commissioni reali e aggiorna `costs` in `config/settings.yaml`.
