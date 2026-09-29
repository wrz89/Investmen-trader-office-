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

Nello stesso repository c'è anche l'**ufficio sportivo** (scommesse, 9 agenti, dashboard su `http://localhost:8766`): vedi la sezione [Sports Betting Office](#sports-betting-office) in fondo.

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
- **Acquisti reali dell'accumulo** (`office/live_exchange.py`, decisione dell'utente del 29/09/2026): con una chiave API di Bybit EU inserita dalle Impostazioni. La chiave è rifiutata se permette prelievi o non è legata a un IP; serve un ordine di prova da 5 € e l'interruttore "Acquisti reali". Il modulo può solo comprare le monete del piano, con tetto mensile pari all'importo pianificato; soldi veri e prova hanno registri separati. Chiave e segreto restano in `runtime/`.
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

---

# Sports Betting Office

Ufficio gemello per le **scommesse sportive**, con la stessa filosofia di quello crypto: regole numeriche, veto del Risk Manager, registro immutabile, **paper di default**. Codice in `sport_office/`, configurazione in `config/sport/`, dati in `runtime/sport/`, dashboard su `http://localhost:8766`.

| # | Agente | Cosa fa | File |
|---|---|---|---|
| 1 | Carlo · Direttore | attiva le strategie, controlla i cancelli per i soldi veri, riepilogo del ciclo | `sport_office/agents/direttore.py` |
| 2 | Sara · Quote (Data Collector) | ogni ciclo scarica quote pre-partita e live, punteggi, statistiche e corse; salva tutto e scarta i dati vecchi | `sport_office/agents/quote.py`, `sport_office/feeds/` |
| 3 | Davide · Analista | strategie sport: favoriti, live scalping, sure bet | `sport_office/agents/analista.py` |
| 4 | Matteo · Trader cavalli | exchange: back-to-lay e green-up prima del via | `sport_office/strategies/s04_greenup_cavalli_v1.py` |
| 5 | Giorgia · Sentiment | movimento delle quote e notizie pubbliche; **può solo frenare** | `sport_office/agents/sentiment.py`, `config/sport/sentiment.yaml` |
| 6 | Bruno · Risk Manager (The Brain) | EV > 0, Kelly frazionario, limiti, circuit breaker, **veto assoluto** | `sport_office/agents/risk.py`, `config/sport/risk_limits.yaml` |
| 7 | Pietro · Banco (Esecuzione e notifica) | piazza, chiude, cash-out, green-up, CLV; Telegram a ogni giocata | `sport_office/agents/banco.py`, `sport_office/execution.py` |
| 8 | Anna · Tesoriera | bankroll = capitale + profitti, base di puntata con reinvestimento, ROI, drawdown | `sport_office/agents/tesoriere.py`, `sport_office/bankroll.py` |
| 9 | Irene · Auditor | report giornaliero | `sport_office/agents/auditor.py` |

## Avvio rapido (Windows)

1. `installa.bat` (una volta, se non l'hai già fatto per l'ufficio crypto).
2. `simula_sport.bat`: 3 giorni di ufficio simulati in circa un minuto, poi la dashboard.
3. `backtest_sport.bat`: scarica 10 stagioni di 9 campionati da football-data.co.uk e confronta le strategie.
4. `avvia_sport.bat`: l'ufficio vero, in paper, con un ciclo al minuto.

```
python sport.py avvia | ciclo | simula --ore 72 | backtest [--senza-kill] [--csv file.csv]
python sport.py rischio --quota 1.22 --vinte 0.80 --puntata 0.02
python sport.py dashboard [--simulazione] | report | stato | prova-telegram | betfair-verifica | reset-kill-switch
```

## Fonti dati (`config/sport/settings.yaml` → `feed`)

| Fonte | Cosa dà | Costo | Chiave |
|---|---|---|---|
| `mock` (predefinita) | bookmaker simulati con margine e ritardi realistici, partite e corse che avvengono davvero nel tempo | gratis | nessuna |
| `odds_api` | quote 1X2 di 20+ bookmaker europei, risultati | 500 richieste/mese gratis | the-odds-api.com |
| `betfair` | exchange: cavalli GB/IE e calcio, **stream in tempo reale** (socket SSL) o polling | app key Betfair | developer.betfair.com |
| `live_stats: api_football` | minuto, punteggio, tiri in porta, cartellini delle partite in corso | 100 richieste/giorno gratis | api-football.com |

Le chiavi si inseriscono dalla dashboard (**Impostazioni**) e restano in `runtime/sport/local_settings.json`.

**Perché solo Betfair per puntare in automatico.** È l'unico operatore con concessione ADM che offre un'API ufficiale per piazzare puntate (endpoint italiani: login su `identitysso.betfair.it`, chiamate su `api.betfair.com`). Sisal, Snai, Eurobet, Bet365 non hanno API pubbliche: automatizzarne il sito viola i termini d'uso e porta alla chiusura del conto. Per questo le occasioni trovate sulle loro quote vengono **registrate in paper e mandate su Telegram come "DA PIAZZARE A MANO"**.

## Il cervello: quando una puntata ha valore

- **Probabilità giusta** = consenso dei bookmaker senza margine, col metodo potenza (toglie meno margine ai favoriti, come avviene davvero) e peso triplo al bookmaker sharp (Pinnacle).
- **EV** = probabilità giusta × quota migliore − 1. Si punta solo se EV ≥ 1,5%, con almeno 3 bookmaker concordi e quote più fresche di 3 minuti.
- **Puntata** = ¼ di Kelly sulla **base di puntata**, con tetto al 2% del bankroll. La base è capitale iniziale + profitti × `reinvest_fraction` (1,0 = compounding pieno); se il bankroll scende sotto il 90% del capitale, la base è il bankroll stesso, così dopo una perdita le puntate si riducono da sole.
- **Circuit breaker**: −3% nel giorno → stop fino a domani; 6 perdite di fila → pausa di 2 ore; −12% dal massimo → **kill switch** (reset solo manuale); file dei limiti modificato a ufficio acceso → blocco.
- **Sentiment (Giorgia)**: se la probabilità della nostra squadra è scesa del 3% in 90 minuti la puntata si dimezza, del 6% c'è il veto. Una notizia grave (infortunio, squalifica, turnover) confermata da 2 testate diverse dà il veto; da una sola testata dà la cautela, che diventa veto se anche il mercato si muove contro. Rientri e smentite annullano l'allarme; i titoli "riassunto" (punto infermeria, probabili formazioni) vengono ignorati.

## Telegram

Pietro ti scrive a **ogni puntata piazzata** (paper, reale o da piazzare a mano) e a ogni chiusura; Bruno a ogni **blocco di sicurezza**; Giorgia a ogni veto o cautela. Se colleghi un bot dalle impostazioni sportive si usa quello, altrimenti quello già collegato all'ufficio crypto. Una notifica che fallisce non ferma mai l'ufficio.

## Soldi veri: cinque cancelli

Tutti aperti, altrimenti l'ufficio resta in paper e lo scrive nel registro: `mode: live`, `execution.provider: betfair`, conto Betfair verificato, ordine di prova riuscito (quota 1000, annullato subito, costo zero), interruttore **Puntate reali** acceso, strategia elencata in `live_strategies`. Gli ordini sono LIMIT **fill-or-kill**: abbinati subito per intero o annullati. Criteri minimi suggeriti in `config/sport/promotion_criteria.yaml`.

## Cosa dicono i dati veri (backtest del 29/09/2026)

33.574 partite di Serie A, Serie B, Premier, Championship, Liga, Bundesliga, Ligue 1, Eredivisie e Primeira Liga, stagioni 2015/16–2024/25. Quote di apertura, chiusura Pinnacle per il CLV.

| Strategia | Puntate | Vinte | Pareggio | ROI | CLV | Esito |
|---|---|---|---|---|---|---|
| "80% a quota 1,15–1,25" alla lettera | 757 | 83,6% | 83,1% | +0,5% | +0,2% | kill switch a febbraio 2021 |
| S01 favoriti con EV ≥ 1,5% | 126 | 84,9% | 80,5% | +6,0% | +3,3% | 100 → 116 € in 10 anni |
| S03 sure bet | 709 | 100% | 98,8% | +1,2% | — | 100 → 118 €, quasi tutto nel 2016–2018 |

- **Il win rate non è l'obiettivo.** A quota 1,20 serve l'83% solo per andare in pari: la strategia dell'80% "alla lettera" vince tanto e guadagna zero, con drawdown sufficienti a far scattare il kill switch.
- **Il valore c'è ma è raro.** S01 trova circa 13 occasioni l'anno su 9 campionati, sempre meno negli ultimi anni: il compounding di micro-puntate cresce lentamente.
- **Limiti del test.** Le quote d'apertura sono rilevate giorni prima e nella realtà alcune sparirebbero prima della puntata; i bookmaker limitano i conti che vincono con costanza, soprattutto chi fa sure bet.
- **Monte Carlo** (`python sport.py rischio`): 80% di vincite a quota 1,22 con puntate al 2% perde nel 95% degli scenari. Anche con l'84% (EV +2,5%) le puntate fisse al 2% fanno scattare il kill switch nel 93% dei casi: per questo il sizing è Kelly frazionario, che dimezza o annulla la puntata quando il vantaggio è piccolo.

## Simulazione e mondo simulato

Il feed `mock` ha bookmaker con margine realistico, un bookmaker sharp che si aggiorna subito e bookmaker soft che si aggiornano in ritardo dopo le notizie: quelle quote "vecchie" sono la fonte vera del valore. I profitti della simulazione servono a collaudare l'ufficio e **non sono una prova** delle strategie: la prova è il backtest sui dati veri e poi il paper trading sulle quote vere.

## Dal bot Betfair di riferimento (cavalli)

Lo zip `betfair-python-trading-bot-automation` contiene solo un README promozionale, senza codice. Le idee utili sono diventate `S04_greenup_cavalli_v1`: back-to-lay con obiettivo in tick, stop loss in tick, uscita forzata 60 secondi prima del via, weight of money, ordini fill-or-kill. Il "profitto garantito" del README vale solo quando il prezzo si muove a favore: con lo stop la perdita è reale.
