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
/stop       kill switch immediato, ordini non abbinati annullati (il reset si fa solo dal PC)
/chiudi     chiude subito i trade aperti
/pausa 60   niente nuove puntate per 60 minuti
/riprendi   toglie la pausa del telefono (i freni automatici restano)
```

## Le strategie

| Strategia | Stato | Regola |
|---|---|---|
| S05 v2 Favoriti su exchange | attiva | calcio, tennis ATP/WTA, basket: probabilità giusta ≥ 75% (basket 77%), quota Betfair 1,10–1,40 (tennis 1,35, basket 1,30), **valore atteso al netto della commissione ≥ 2%**, spread ≤ 2 tick, almeno 3 € sul prezzo, da 2 ore a 15 minuti prima dell'inizio; esclusi Challenger, ITF e doppi |
| S06 Live finale | osservazione | squadra in vantaggio dal 70', quota 1,05–1,30, EV netto ≥ 1%; niente ingresso con un uomo in meno o se l'avversario assedia; serve un riferimento live (piano a pagamento di The Odds API) |
| S07 Scalping pre-partita | osservazione | back→lay prima dell'inizio sul lato con più denaro in attesa; target −2 tick, stop +3 tick, uscita 5 minuti prima; misurata con trade ombra veri |
| S08 Basket +15 nel 4° quarto | osservazione | chi conduce di 15+ punti vince il 96,5–99,5% delle volte, ma paga 1,01–1,04: si punta solo se Betfair paga più della probabilità storica |
| S04 Green-up cavalli | osservazione | solo mondo simulato: su betfair.it l'ippica non c'è |

In **osservazione** una strategia lavora "in ombra": le puntate secche valgono 1 € virtuale, i trade sono trade veri dell'exchange simulato (2 €, stesse regole), ma il bankroll non si tocca. Si attiva spostandola in `active_strategies` quando i numeri in ombra la giustificano.

**Alta probabilità, ma con valore.** Il bot cerca favoriti netti (tre vittorie su quattro o più), come chiesto, ma punta solo quando la quota Betfair, tolta la commissione, paga più di quanto "meriti" la probabilità stimata dai bookmaker. Senza questo filtro un favorito a quota 1,20 va indovinato l'84% delle volte solo per non perdere.

## Il rischio con 30 €

- **Puntate secche**: 1/4 di Kelly sulla quota netta. Con 30 € quasi sempre il risultato è sotto i 2 € minimi: la puntata minima passa solo se resta al massimo **metà del Kelly pieno**, cioè solo con un vantaggio netto. Tetto 10% del bankroll.
- **Trade con stop**: la puntata si sceglie dalla **perdita massima** allo stop, con 6 tick di scivolamento; sotto i 100 € un solo trade aperto, sempre alla puntata minima.
- **Rischio aperto** complessivo ≤ 8% del bankroll; massimo 4 posizioni aperte.
- **Circuit breaker sotto i 100 €** (limiti assoluti): **kill switch se il bankroll scende sotto 20 €**; stop fino a domani dopo 4 € persi nel giorno; 6 perdite di fila → pausa di 2 ore.
- **Circuit breaker sopra i 100 €** (percentuali): −5% nel giorno → stop fino a domani; −15% dal massimo → kill switch. Il reset è solo dal PC: `python betbot.py reset-kill-switch`.
- **Quote di riferimento** più vecchie di 2,5 ore, o un vantaggio sopra l'8% (quasi sempre un errore di dato): veto.
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
- **Tennis.** Su 51.000 partite ATP e WTA (2013-2023) i favoriti a quota 1,10–1,35 vincono l'80% ma, puntati al prezzo giusto, perdono l'1,3% dopo la commissione (ritiri trattati con le regole Betfair). I file 2025-2026 con i prezzi Betfair si scaricano dal tuo PC (`backtest.bat`).
- **Basket.** NBA, 14.822 partite: i favoriti a 1,10–1,30 vincono l'81,8% contro l'81,4% previsto dalle quote. Dopo la commissione −0,4%. Chi conduce di 15+ punti nel quarto quarto vince il 98,5%, ma a quota 1,01–1,04.
- **Altri sport.** Hockey: favoriti netti rari (1,5% delle partite). Rugby league NRL su prezzi Betfair: −7,1%. Da evitare: tennistavolo ed eSports (integrità), pallavolo (nessun riferimento affidabile).
- **In sintesi.** In nessuno sport si vince spesso e si guadagna automaticamente: vincere l'80% è facile, guadagnare no. Il guadagno può venire solo dai momenti in cui Betfair paga più del giusto, ed è esattamente e solo quello che S05 cerca.
- **Limiti dei dati.** I prezzi di football-data e tennis-data sono del pool internazionale e rilevati giorni prima. Sul pool italiano la verità si misura solo registrando: passi 3 e 4.

`python betbot.py rischio --quota 1.22 --vinte 0.80 --puntata 0.07` mostra con un Monte Carlo cosa succede a 1000 puntate con quella quota, quel win rate e quella puntata.

## L'algoritmo "Mercurius"

Mercurius era il sistema di Mercurius BI srl (Milano, 2017-2021; app "Tradr" nella Betfair App Directory). Il codice e le formule **non sono mai stati pubblicati** e la società non è più attiva: chi oggi vende un "metodo Mercurius" non ha l'originale.
Dai webinar dei fondatori si conosce l'architettura:

1. **Modello proprio.** Quote giuste da un modello dei gol su dati Wyscout a pagamento.
2. **Confronto.** Quelle quote vengono confrontate col prezzo dell'exchange.
3. **Puntata.** 1% piatto, perché secondo i fondatori il Kelly non si fida di quote giuste incerte.
4. **Tempi.** Esecuzione da 2 ore a pochi minuti dall'inizio.

I risultati dichiarati, mai verificati da terzi, sono modesti:

| Anno | Risultato |
|---|---|
| 2019 | +34% |
| 2020 | −8,2% |

Il rendimento medio dichiarato è di circa +2% per puntata, con una commissione Betfair ridotta al 2%. Col 4,5% di betfair.it quel margine sparisce.

`python betbot.py mercurius` ricostruisce il metodo con strumenti pubblici e lo mette alla prova sui prezzi veri di Betfair. Usa un modello Dixon-Coles con decadimento nel tempo, stimato ogni giornata solo sulle partite precedenti, e un peso modello/mercato scelto sul 2024/25. Il test fuori campione è sulle stagioni successive: 6.014 partite, 16 campionati.

| Log-loss sulle stagioni di prova (più bassa = meglio) | Valore |
|---|---|
| Mercato (consenso dei bookmaker) | 0,995 |
| Modello dei gol | 1,017 |
| Peso ottimale del modello | 0 |

- **Il modello non aggiunge niente al mercato.** Da solo è peggiore, e il peso migliore da dargli è zero.
- **Resta solo lo scarto tra Betfair e il mercato.** Con EV netto ≥ 2% sono 67 puntate: ROI +5,6% ± 17,5%, cioè nessuna prova. È lo stesso segnale che già usa S05.
- **Cosa si può tenere.** Esecuzione vicino all'inizio, puntate piccole, CLV come metrica principale: in Bet_bot ci sono già.

## Comandi

```
python betbot.py avvia | ciclo | simula --ore 72 | backtest [--senza-kill] [--csv file.csv]
python betbot.py replay [--da AAAA-MM-GG] [--a AAAA-MM-GG]
python betbot.py dashboard [--simulazione | --replay]
python betbot.py diagnosi | stato | report | prova-telegram | betfair-verifica | reset-kill-switch
python betbot.py rischio --quota 1.22 --vinte 0.80 --puntata 0.07
python betbot.py mercurius
python betbot.py ferma | avvio-automatico on|off
```

Le tue scelte personali (modalità, feed, strategie attive) mettile in `runtime/impostazioni.yaml`: hanno la precedenza su `config/settings.yaml` e gli aggiornamenti non le toccano. Esempio:

```yaml
mode: paper
feed: {provider: betfair, reference: odds_api, record: true}
active_strategies: [S05_favoriti_exchange_v2]
```

Dashboard: `http://localhost:8766` (solo sul tuo PC).

## Note pratiche

- **PC acceso e sveglio**: il bot lavora solo a PC acceso; mentre gira chiede a Windows di non andare in sospensione. Gli ordini sono fill-or-kill, quindi non restano ordini "appesi" se il PC si spegne; un trade aperto viene chiuso al riavvio successivo.
- **Un solo bot alla volta**: se lo rilanci mentre è acceso, si apre solo la dashboard.
- **Termini d'uso**: Betfair consente i bot personali tramite API ufficiale con la tua app key. Da novembre 2025 su betfair.it sono stati disattivati i software di terze parti: Bet_bot è tuo e usa solo le tue chiavi.
- **Gioco responsabile**: metti in Betfair un limite di deposito. Il bot non deposita e non preleva mai.
