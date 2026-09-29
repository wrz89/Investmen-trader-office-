# Bet_bot

Bot di scommesse **autonomo su Betfair Exchange Italia**, che gira in locale sul tuo PC Windows.
Nove "colleghi" lavorano in un ufficio 3D: leggono i prezzi, cercano occasioni ad alta probabilità di
vincita, le passano al Risk Manager, piazzano solo quelle approvate e ti scrivono su Telegram.

**Si parte in PAPER**: prezzi veri (o simulati), soldi finti, stesse regole dell'exchange vero.
I soldi veri si accendono solo aprendo cinque cancelli, uno per uno, quando i numeri lo giustificano.

| Chi | Cosa fa |
|---|---|
| Carlo · Direttore | decide quali strategie lavorano, apre e chiude i cicli, sblocca strategie quando il bankroll cresce |
| Sara · Quote | ogni minuto legge da Betfair partite, prezzi back/lay e denaro disponibile (calcio, tennis, basket); se i dati sono vecchi, nessuna puntata |
| Davide · Analista | cerca i favoriti netti con valore atteso positivo (pre-partita e nel finale) |
| Matteo · Trader | trade back→lay prima dell'inizio (in osservazione finché i dati veri non lo giustificano) |
| Giorgia · Sentiment | controlla se il mercato "scappa" contro la nostra scelta e legge le notizie; può solo frenare |
| Bruno · Risk Manager | EV netto di commissione > 0, Kelly frazionario, limiti, circuit breaker, **veto assoluto** |
| Pietro · Banco | piazza gli ordini (fill-or-kill), chiude i trade, registra gli esiti, ti avvisa su Telegram |
| Anna · Tesoriera | bankroll = capitale + profitti, base di puntata con reinvestimento, ROI, drawdown |
| Irene · Auditor | registro immutabile e report giornaliero |

## Installazione in D:\claude\Bet_bot

1. Installa **Python 3.10 o superiore** da python.org e spunta "Add python.exe to PATH".
2. Copia la cartella `Bet_bot` in `D:\claude\` (dallo zip, oppure dal repository GitHub: è la cartella `Bet_bot/`).
3. Doppio clic su **`installa.bat`** (una volta sola, 1-3 minuti).
4. Doppio clic su **`diagnosi.bat`**: controlla che sia tutto a posto.

Tutto quello che il bot produce (database, storico, registrazioni, report, chiavi) sta in `D:\claude\Bet_bot\runtime\`, che non va mai su GitHub.
Per aggiornare il programma: **`aggiorna.bat`** (scarica da GitHub solo il codice; runtime e .venv restano intatti).

## Il percorso consigliato (in quest'ordine)

| Passo | File | Cosa ottieni |
|---|---|---|
| 1 | `simula.bat` | 3 giorni simulati in un minuto e l'ufficio 3D sulla simulazione: vedi come lavora |
| 2 | `backtest.bat` | le strategie sui **prezzi veri di Betfair Exchange** 2024-2026 (calcio; tennis dal tuo PC) |
| 3 | `avvia.bat` con `feed.provider: betfair` e `feed.record: true` | paper sui prezzi veri di betfair.it per 2-4 settimane, registrando tutto |
| 4 | `python betbot.py replay` | tutte le strategie sui giorni registrati: quali funzionano davvero sul pool italiano |
| 5 | soldi veri, una strategia sola | solo se il passo 4 dà CLV positivo e ROI positivo su almeno 200 operazioni |

## Collegare Betfair Exchange Italia

1. Conto su **betfair.it** con la verifica dell'identità completata.
2. App key: su developer.betfair.com crea le chiavi. La **delayed** è gratuita: prezzi in ritardo da 1 a 180 secondi, va bene per il paper e per le puntate pre-partita. La **live** per i conti italiani è gratuita, si chiede dopo aver usato la delayed (serve per l'in-play e il trading).
3. Nella dashboard: **Impostazioni → Betfair Exchange Italia** → app key, utente, password → **Verifica il conto** → **Ordine di prova** (quota 1000, annullato subito: costa zero).
4. In `config/settings.yaml`: `feed.provider: betfair`. Per il confronto con i bookmaker: `feed.reference: odds_api` e la chiave di The Odds API nelle Impostazioni.

Regole di betfair.it che il bot rispetta da solo: puntata back minima **2 €, a multipli di 0,50 €**; lay pari a una puntata back di almeno 0,50 €; commissione **4,5%** sulla vincita netta di mercato; back e lay in richieste separate; la sessione scade dopo 20 minuti e il bot la rinnova ogni 10. **L'ippica su betfair.it non c'è**: il trading sui cavalli gira solo nel mondo simulato. Il pool italiano ha una liquidità sua, separata da quella internazionale: per questo esiste il registratore.

## Telegram

Impostazioni → Telegram: crea il bot con @BotFather, incolla il token, premi AVVIA sul bot, **Trova la mia chat**, **messaggio di prova**.
Ti arriva un messaggio a **ogni puntata** e a ogni chiusura, a ogni **blocco di sicurezza** (kill switch, stop giornaliero, serie negativa, limiti modificati, saldo Betfair che non torna) e il report serale.

Comandi dal telefono (solo dalla tua chat):

```
/stato      bankroll, profitti, drawdown, blocchi
/aperte     puntate in gioco
/oggi       riepilogo della giornata
/stop       kill switch immediato (il reset si fa solo dal PC)
/pausa 60   niente nuove puntate per 60 minuti
/riprendi   toglie la pausa
```

## Le strategie

| Strategia | Stato | Regola |
|---|---|---|
| S05 Favoriti su exchange | attiva | calcio, tennis, basket: probabilità giusta ≥ 75%, quota Betfair 1,10–1,40, **valore atteso al netto della commissione ≥ 1%** |
| S06 Live finale | attiva | squadra in vantaggio dal 70', quota 1,05–1,30, EV netto ≥ 1%; niente ingresso con un uomo in meno o se l'avversario assedia; chiusura con un lay se il vantaggio sparisce |
| S07 Scalping pre-partita | osservazione | back→lay prima dell'inizio sul lato con più denaro in attesa; target −2 tick, stop +3 tick, uscita 3 minuti prima |
| S04 Green-up cavalli | osservazione | solo mondo simulato: su betfair.it l'ippica non c'è |

**Alta probabilità, ma con valore.** Il bot cerca favoriti netti (tre vittorie su quattro o più), come chiesto, ma punta solo quando la quota Betfair, tolta la commissione, paga più di quanto "meriti" la probabilità stimata dai bookmaker. Senza questo filtro un favorito a quota 1,20 va indovinato l'84% delle volte solo per non perdere.

## Il rischio con 30 €

- **Puntate secche**: 1/4 di Kelly sulla quota netta. Con 30 € quasi sempre il risultato è sotto i 2 € minimi: la puntata minima passa solo se resta al massimo **metà del Kelly pieno**, cioè solo con un vantaggio netto. Tetto 10% del bankroll.
- **Trade con stop**: la puntata si sceglie dalla **perdita massima** allo stop, con 2 tick di scivolamento: al massimo 1,5% del bankroll (0,45 € su 30 €).
- **Rischio aperto** complessivo ≤ 8% del bankroll; massimo 4 posizioni aperte.
- **Circuit breaker**: −5% nel giorno → stop fino a domani; 6 perdite di fila → pausa di 2 ore; **−15% dal massimo → kill switch** (reset solo dal PC: `python betbot.py reset-kill-switch`).
- **Compounding**: la base di puntata è capitale + profitti; se il bankroll scende sotto il 90% del capitale, la base è il bankroll stesso e le puntate si riducono da sole.
- In live, ogni 10 cicli il bankroll viene confrontato col **saldo vero di Betfair**: se non torna, kill switch.

Tutti i limiti sono in `config/risk_limits.yaml`, sigillati all'avvio: se il file cambia a bot acceso, nessuna nuova puntata.

## Cosa dicono i dati veri

Backtest del 29/09/2026 su **11.400 partite di calcio con prezzi Betfair Exchange** (16 campionati, agosto 2024 – settembre 2026), commissione 4,5%, 30 € di partenza, regole di betfair.it:

| Strategia | Puntate | Vinte | Pareggio | ROI |
|---|---|---|---|---|
| "80% a quota 1,15–1,25" alla lettera | 375 | 82,9% | 83,7% | −0,9% |
| S05 favoriti con valore netto | 2 | 100% | 85,2% | campione troppo piccolo |

- **Vincere spesso non basta.** L'idea di partenza vince 83 volte su 100 e perde soldi, con drawdown del 53% se nessuno la ferma.
- **Il valore nel calcio è raro.** Due occasioni in due anni sui 16 campionati: la commissione del 4,5% cancella quasi tutti i vantaggi.
- **Tennis.** Su 51.000 partite ATP e WTA (2013-2023) i favoriti puntati al prezzo giusto perdono circa l'1% dopo la commissione: anche qui il guadagno può venire solo dai momenti in cui l'exchange paga più del giusto. I file 2025-2026 con i prezzi Betfair si scaricano dal tuo PC (`backtest.bat`).
- **Limiti dei dati.** I prezzi di football-data e tennis-data sono del pool internazionale e rilevati giorni prima. Sul pool italiano la verità si misura solo registrando: passi 3 e 4.

`python betbot.py rischio --quota 1.22 --vinte 0.80 --puntata 0.07` mostra con un Monte Carlo cosa succede a 1000 puntate con quella quota, quel win rate e quella puntata.

## Comandi

```
python betbot.py avvia | ciclo | simula --ore 72 | backtest [--senza-kill] [--csv file.csv]
python betbot.py replay [--da AAAA-MM-GG] [--a AAAA-MM-GG]
python betbot.py dashboard [--simulazione | --replay]
python betbot.py diagnosi | stato | report | prova-telegram | betfair-verifica | reset-kill-switch
python betbot.py rischio --quota 1.22 --vinte 0.80 --puntata 0.07
```

Dashboard: `http://localhost:8766` (solo sul tuo PC).

## Note pratiche

- **PC acceso e sveglio**: il bot lavora solo a PC acceso; mentre gira chiede a Windows di non andare in sospensione. Gli ordini sono fill-or-kill, quindi non restano ordini "appesi" se il PC si spegne; un trade aperto viene chiuso al riavvio successivo.
- **Un solo bot alla volta**: se lo rilanci mentre è acceso, si apre solo la dashboard.
- **Termini d'uso**: Betfair consente i bot personali tramite API ufficiale con la tua app key. Da novembre 2025 su betfair.it sono stati disattivati i software di terze parti: Bet_bot è tuo e usa solo le tue chiavi.
- **Gioco responsabile**: metti in Betfair un limite di deposito. Il bot non deposita e non preleva mai.
