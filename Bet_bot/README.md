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
| Davide · Analista calcio | cerca i favoriti netti con valore atteso positivo nel calcio (pre-partita e nel finale) |
| Matteo · Analista tennis e basket | stesso lavoro di Davide sul tennis (ATP/WTA) e sul basket; su betfair.it l'ippica non c'è, quindi niente cavalli |
| Giorgia · Sentiment | controlla se il mercato "scappa" contro la nostra scelta e legge le notizie; può solo frenare |
| Bruno · Risk Manager | EV netto di commissione > 0, Kelly frazionario, limiti, circuit breaker, **veto assoluto** |
| Pietro · Banco | piazza gli ordini (fill-or-kill), chiude i trade, registra gli esiti, ti avvisa su Telegram |
| Anna · Tesoriera | bankroll = capitale + profitti, base di puntata con reinvestimento, ROI, drawdown |
| Irene · Auditor | registro immutabile e report giornaliero |
| Leo · Allenatore | autopsia di ogni puntata chiusa (fortuna o bravura), lezioni dagli errori, regole che possono solo frenare |

## Installazione in D:\claude\Bet_bot

1. Installa **Python 3.10 o superiore** da python.org e spunta "Add python.exe to PATH".
2. Copia la cartella `Bet_bot` in `D:\claude\` (dallo zip, oppure dal repository GitHub: è la cartella `Bet_bot/`).
3. Doppio clic su **`installa.bat`** (una volta sola, 1-3 minuti).
4. Doppio clic su **`diagnosi.bat`**: controlla che sia tutto a posto.

Tutto quello che il bot produce (database, storico, registrazioni, report, chiavi) sta in `D:\claude\Bet_bot\runtime\`, che non va mai su GitHub.
Per aggiornare il programma: **`aggiorna.bat`** (scarica da GitHub solo il codice; runtime e .venv restano intatti).
**Non modificare `config/settings.yaml`**: ogni aggiornamento lo sostituisce con la versione di GitHub. Le tue scelte (modalità, feed, strategie, registrazione) vanno in **`runtime/impostazioni.yaml`**, che gli aggiornamenti non toccano (vedi [Comandi](#comandi)).

## Il percorso consigliato (in quest'ordine)

| Passo | File | Cosa ottieni |
|---|---|---|
| 1 | `simula.bat` | 3 giorni simulati in un minuto e l'ufficio 3D sulla simulazione: vedi come lavora |
| 2 | `backtest.bat`, `python betbot.py lay`, `python betbot.py palestra` | le strategie sui **prezzi veri di Betfair Exchange** 2024-2026, il lay di valore, e Leo che rivive 5 anni di partite |
| 3 | `python betbot.py copertura` | quante partite hanno Pinnacle su The Odds API e quanti crediti servono al giorno |
| 4 | `avvia.bat` con `feed: {provider: betfair, reference: odds_api}` in `runtime/impostazioni.yaml` | paper sui prezzi veri di betfair.it per 4 settimane; la registrazione dei prezzi (`record`) è già accesa |
| 5 | `python betbot.py replay` e `python betbot.py esame` | le strategie sui giorni registrati e l'**esame per il live** con i criteri decisi prima |
| 6 | soldi veri, una strategia sola | solo se l'esame dice PRONTA; per S09 serve anche `execution.lay_apertura: true` |

### Il test rapido (6 ore)

`test_rapido.bat` risponde in un pomeriggio alla domanda su cui si regge S09: quando su betfair.it un lay costa meno della quota giusta di Pinnacle, il mercato poi ci dà ragione?
- Lo lanci quando vuoi. Legge il calendario (gratis) e, se adesso ci sono poche partite, aspetta da solo la finestra di 6 ore con più partite nei 3 giorni successivi. `test_rapido.bat subito` parte comunque adesso.
- Per 6 ore segue calcio, football americano, tennis, basket e baseball: Betfair ogni 15 minuti, Pinnacle ogni ora (circa 150 crediti di The Odds API). Così si misura anche in settimana.
- Ogni sport ha il suo verdetto. Se lo interrompi con Ctrl+C, analizza comunque quello che ha raccolto.
- Misura il CLV di ogni esito contro la chiusura di Pinnacle, senza bisogno del risultato. Non punta nulla, nemmeno in simulazione.
- Il verdetto è una di tre possibilità: *segnale presente*, *segnale assente* o *non ancora chiaro*.
- È un'indicazione, non una prova. Ripetuto in giorni diversi, i numeri si sommano.

### Soldi veri per divertimento (`vai_live.bat` / `torna_paper.bat`)

La strategia **S10 Divertimento** punta ogni giorno su qualunque sport di betfair.it (calcio, tennis, basket, NFL, baseball), anche in settimana.
- **Quanto:** 2 € fissi, al massimo 3 puntate al giorno e una aperta alla volta.
- **Cosa sceglie:** quote tra 1,40 e 3,00, su un libro con back e lay vicini e almeno 10 € disponibili. Prende il prezzo più vicino al "giusto": Pinnacle se la quota è recente, altrimenti il prezzo medio di Betfair.
- **Cosa aspettarsi:** non cerca un vantaggio. In media perde l'1-3% di ogni puntata (commissione e spread), circa 1 € al mese; il resto è fortuna.
- **Quando si ferma da solo:** se il saldo scende sotto 20 €, e per il resto del giorno dopo 4 € persi.

`vai_live.bat` fa tutto: login, mostra il saldo, chiede di scrivere **SI**, fa l'ordine di prova (2 € a quota 1000, annullato subito), accende le puntate reali e scrive `runtime/impostazioni.yaml`. Poi si riavvia il bot con `avvia.bat`.

`torna_paper.bat` spegne tutto. Le puntate vere già aperte si chiudono da sole su Betfair. Le altre strategie restano in ombra, senza soldi.

### Quando conviene entrare (`orizzonti.bat`)

Legge le registrazioni dei prezzi veri di betfair.it e, per ogni partita, guarda i prezzi a 72, 48, 24, 12, 6, 3 e 1 ora dall'inizio.
- Per ogni orizzonte misura quanto denaro c'è al miglior prezzo e quanto il prezzo preso batte la chiusura, di Betfair e di Pinnacle.
- Il gruppo che conta è quello "di valore": gli esiti in cui betfair.it pagava più di Pinnacle. Se il loro CLV è sopra zero in modo solido a un certo orizzonte, lì c'è il vantaggio.
- Non punta nulla e non consuma crediti. Diventa affidabile con qualche settimana di registrazioni (bot acceso con il feed Betfair).

### S05 v3 (in ombra)

È S05 v2 con due controlli in più:
- se la probabilità di riferimento o il prezzo Betfair si sono mossi di oltre il 6% dalla prima lettura, salta: di solito è una notizia, e il "valore" è solo il riferimento rimasto indietro;
- chiede almeno 6 € al miglior prezzo invece di 3.

Gira in ombra accanto alla v2 e l'esame per il live le confronta con gli stessi criteri.

### Football americano (NFL)

Matteo segue anche l'NFL: il bot legge i mercati testa a testa di betfair.it, li registra e li misura nel test rapido. Nessuna strategia ci punta ancora.
- `python betbot.py nfl` rifà il controllo sullo storico: 3.828 partite dal 2012 al 2025, 27 prove su testa a testa, handicap e totale punti. Nessuna batte il mercato in modo solido.
- Pareggio dopo i supplementari: Betfair applica il dead heat (metà puntata pagata a quota piena) e il bot lo conteggia così.
- Fuori stagione (da febbraio ad agosto) l'NFL non consuma crediti di The Odds API.

### L'esame per il live

I criteri sono in `config/esame_live.yaml` e sono stati decisi **prima** di vedere i risultati. Cambiarli dopo averli visti invalida l'esame, ed è il modo più comune di illudersi.

- **Quantità:** almeno 200 puntate chiuse sui prezzi veri di betfair.it, cioè feed Betfair o replay delle registrazioni. Il mondo simulato non conta.
- **Bravura:** CLV positivo anche nel caso peggiore, al 95%.
- **Soldi:** ROI sul rischio positivo, commissione compresa.

Esiti: **PRONTA**, **IN ESAME** oppure **BOCCIATA** (CLV negativo anche nel caso migliore). Leo avvisa su Telegram quando l'esito cambia. Il bot non accende mai i soldi veri da solo: la decisione resta tua.

## Collegare Betfair Exchange Italia

**Il modo più semplice: `collega_betfair.bat` (doppio clic).**
1. Crea il certificato e apre la pagina di betfair.it dove caricarlo: premi "Modifica" su *Automated Betting Program Access* e scegli `client-2048.crt`. È l'unico passo a mano.
2. Ti chiede utente e password di betfair.it. La password non si vede mentre la scrivi e resta cifrata sul PC.
3. Fa il login con il certificato, crea da solo la app key "delayed" sul tuo conto e verifica il saldo.

Poi metti `feed: {provider: betfair}` in `runtime/impostazioni.yaml`, lancia `avvia.bat` e fai l'**Ordine di prova** dalla dashboard.

Il procedimento a mano, se preferisci:

1. Conto su **betfair.it** con la verifica dell'identità completata.
2. App key: su developer.betfair.com crea le chiavi. La **delayed** è gratuita: prezzi in ritardo da 1 a 180 secondi, va bene per il paper e per le puntate pre-partita. La **live** per i conti italiani è gratuita, si chiede dopo aver usato la delayed (serve per l'in-play e il trading).
3. Nella dashboard: **Impostazioni → Betfair Exchange Italia** → app key, utente, password → **Verifica il conto** → **Ordine di prova** (quota 1000, annullato subito: costa zero).
4. In `runtime/impostazioni.yaml` (non in `config/settings.yaml`, che gli aggiornamenti sovrascrivono): `feed: {provider: betfair}`. Per il confronto con i bookmaker: `feed: {provider: betfair, reference: odds_api}` e la chiave di The Odds API nelle Impostazioni.

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
| S09 Lay di valore (calcio) | osservazione | si banca un esito (si punta CONTRO) quando il lay di Betfair costa meno della quota giusta di Pinnacle; quote lay 3-8, EV sul rischio ≥ 2%, puntata del backer da 0,50 €; ~79% di vinte nel backtest, guadagno non ancora significativo. Il lay d'apertura con soldi veri è pronto ma spento (`execution.lay_apertura: false`) |

**Ritirate il 30/09/2026:**
- **S04 cavalli:** su betfair.it l'ippica non c'è.
- **S07 scalping pre-partita:** negativo nelle prove e mercato italiano troppo sottile.
- **S08 basket nel quarto quarto:** quote da 1,01 a 1,04 e servono dati live.

I file restano nella libreria come archivio, ma non girano più.

In **osservazione** una strategia lavora "in ombra": le puntate secche valgono 1 € virtuale, i trade sono trade veri dell'exchange simulato (2 €, stesse regole), ma il bankroll non si tocca. Si attiva spostandola in `active_strategies` (in `runtime/impostazioni.yaml`) quando i numeri in ombra la giustificano.

**Alta probabilità, ma con valore.** Il bot cerca favoriti netti (tre vittorie su quattro o più), come chiesto, ma punta solo quando la quota Betfair, tolta la commissione, paga più di quanto "meriti" la probabilità stimata dai bookmaker. Senza questo filtro un favorito a quota 1,20 va indovinato l'84% delle volte solo per non perdere.

## Il rischio con 30 €

- **Puntate secche**: 1/4 di Kelly sulla quota netta. Con 30 € quasi sempre il risultato è sotto i 2 € minimi: la puntata minima passa solo se resta al massimo **metà del Kelly pieno**, cioè solo con un vantaggio netto. Tetto 10% del bankroll.
- **Trade con stop**: la puntata si sceglie dalla **perdita massima** allo stop, con 6 tick di scivolamento; sotto i 100 € un solo trade aperto, sempre alla puntata minima.
- **Rischio aperto** complessivo ≤ 8% del bankroll; massimo 4 posizioni aperte. Una puntata che sforerebbe l'8% non viene scartata: si **riduce** allo spazio che resta, e il veto scatta solo se quello spazio è sotto i 2 € minimi. Sotto i 100 €, con niente di aperto, **una puntata da 2 € alla volta resta sempre possibile** (fino al kill switch).
- **Circuit breaker sotto i 100 €** (limiti assoluti): **kill switch se il bankroll scende sotto max(20 €, picco × 0,666)**: la soglia segue il picco, 20 € con 30 € di picco, circa 40 € con 60 €, così un profitto non si restituisce tutto; stop fino a domani dopo 4 € persi nel giorno; 6 perdite di fila → pausa di 2 ore.
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

**Lay di valore (30/09/2026, `python betbot.py lay`).** Stagioni 2024/25 e 2025/26, 16 campionati, Pinnacle e Betfair presi nello stesso momento, commissione 4,5%, lay stimato 2 tick sopra il back. Il 10,8% delle partite ha prezzi Betfair incoerenti con Pinnacle ed è stato **scartato**: erano record rotti del file (per esempio un outsider "a 2,0" che vale 8) e da soli gonfiavano il risultato.

| Quote lay 1X2 | Puntate | Vinte | ROI sul rischio |
|---|---|---|---|
| 1,5-3 | 127 | 61,4% | +12,7% ± 8,3% |
| 3-8 | 95 | 78,9% | +5,8% ± 5,7% |
| 8-15 | 17 | 88,2% | −4,2% ± 9,0% |

Con i dati puliti il segnale resta positivo ma **non è più statisticamente solido**: le occasioni sono poche e il margine d'errore è grande quanto il guadagno. La prima versione del backtest, fatta senza filtro, dava +7,2% su 393 puntate. Per questo S09 resta in ombra e decide l'esame sui prezzi veri di betfair.it.

**Palestra di Leo (30/09/2026, `python betbot.py palestra`).** 28.752 partite dal 2021 al 2026, 9.125 con xG e formazioni. Leo si è riaddestrato 52 volte senza mai vedere i risultati in anticipo.
- **Previsioni:** Leo e il mercato sono alla pari, sia due giorni prima sia con le formazioni. Forma, Elo, tiri, xG, riposo, classifica e assenze pesate sono già dentro le quote di Pinnacle.
- **Dove il mercato sbaglia:** nessuna dinamica batte il mercato in modo solido. La più forte, le squadre che "subiscono" negli xG, ha uno scarto di −1,3% con z = 2,2 su 18 prove: compatibile con il caso.
- **Autopsie:** i back scelti da Leo non battono la chiusura (CLV −0,1%). I lay sì (CLV +4,8%, mediana +2,6%), ma dopo la commissione il guadagno è quasi zero (+0,6% ± 2,0%).
- **Lezioni:** 9 regole, per esempio non puntare a favore due giorni prima nei campionati dove il mercato corregge sempre. Più una correzione: con le formazioni Leo era troppo ottimista dell'1,9%.

La conclusione onesta: con i dati pubblici nessun modello batte stabilmente la chiusura di Pinnacle nel calcio. L'unico spazio rimasto è nei prezzi di betfair.it che restano indietro rispetto a Pinnacle, ed è quello che misura la registrazione.

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

## Leo, l'allenatore: imparare dagli errori

Leo fa l'autopsia di ogni puntata chiusa: vera, paper o in ombra.

1. **Ingresso.** Salva la fotografia della decisione: probabilità, valore atteso, quota, da dove veniva il riferimento e quanto era vecchio, liquidità, anticipo sull'inizio, sport, campionato, lato (a favore o contro).
2. **Fino all'inizio.** Segue il prezzo. L'ultima probabilità prima del fischio d'inizio è la *chiusura*: il giudizio del mercato quando sa tutto (formazioni, notizie).
3. **Autopsia.** Separa la fortuna dalla bravura con il **CLV**: la probabilità di vincere alla chiusura meno quella all'ingresso. Positivo vuol dire che il mercato ci ha dato ragione.

| Causa | Quando | Cosa significa |
|---|---|---|
| Vinta con merito | vinta, mercato dalla nostra parte | decisione e risultato giusti |
| Vinta per fortuna | vinta, mercato contro | errore che ha pagato: va studiato come una perdita |
| Persa per varianza | persa, mercato dalla nostra parte | decisione giusta, nessuna correzione |
| Smentita dal mercato | persa, mercato contro | il vantaggio non c'era |
| Riferimento vecchio | mercato contro e riferimento di oltre un'ora | il "valore" era un prezzo superato |
| Movimento forte | la probabilità si è mossa di 5-6 punti | quasi sempre una notizia arrivata dopo |
| Esecuzione | trade chiusi dalla gestione | conta come è stato eseguito |

4. **Lezioni.** Per ogni strategia e caratteristica (campionato, fascia di quota, anticipo, fonte del riferimento, vantaggio, sport, lato, liquidità) Leo misura il CLV medio con il suo margine d'errore. Con almeno 30 puntate e CLV negativo anche nel caso migliore (limite al 90%), nasce una **regola**. Controlla anche la **calibrazione**: se una strategia vince meno di quanto stima, Leo abbassa le sue probabilità di quella differenza (al massimo 5 punti).
5. **Le regole possono solo frenare.** Il Risk Manager le usa come veto in più, mai per allargare i limiti, e il file dei limiti sigillato non viene toccato. Le proposte bloccate vengono seguite in ombra. Così si vede se la regola ha evitato perdite, e Leo la ritira da solo quando i dati nuovi la smentiscono.

Impostazioni in `coach:` (`min_n`, `apply_rules`, `max_rules`). Con `apply_rules: false` Leo osserva e basta. Nella fase di test lavora soprattutto sulle strategie in ombra: le sue lezioni sono pronte prima di mettere soldi veri.

## La palestra di Leo

`python betbot.py palestra` fa rivivere a Leo gli ultimi 5 campionati, partita per partita, **senza fargli vedere il risultato**. La prima volta scarica da Understat xG e formazioni dei 5 grandi campionati, in circa un'ora; poi usa la copia sul PC.

1. **Due giorni prima:** Leo vede le quote del momento e tutta la storia fino al giorno prima. Cioè:
   - Elo, forma in casa e fuori, dominio nei tiri, xG;
   - "fortuna recente" (gol meno xG), riposo, calendario fitto;
   - classifica, lotta salvezza e fase della stagione.
2. **Al fischio d'inizio:** vede in più le formazioni. Quanto valgono i titolari di oggi rispetto a quelli abituali, pesati con xG, xA e xGChain di ogni giocatore; quanti "big" mancano; se gioca il portiere di riserva.
3. **Il modello:** parte dalle probabilità del mercato e impara solo correzioni, con un freno forte: senza prove resta uguale al mercato. Si riaddestra ogni 4 settimane sul passato.
4. **Fiducia:** Leo punta solo se, nelle sue ultime 1.500 previsioni già verificate, è stato più preciso del mercato.
5. **Dopo ogni partita:** autopsia, lezioni per segmento, e una tabella di "dove il mercato sbaglia" per ogni dinamica.

Il resoconto finisce in `runtime/reports/palestra.md`.

## Comandi

```
python betbot.py avvia | ciclo | simula --ore 72 | backtest [--senza-kill] [--csv file.csv]
python betbot.py replay [--da AAAA-MM-GG] [--a AAAA-MM-GG]
python betbot.py dashboard [--simulazione | --replay]
python betbot.py diagnosi | stato | report | prova-telegram | betfair-verifica | reset-kill-switch
python betbot.py rischio --quota 1.22 --vinte 0.80 --puntata 0.07
python betbot.py mercurius | lay | palestra [--anni 5] [--senza-giocatori]
python betbot.py esame | copertura
python betbot.py ferma | avvio-automatico on|off
```

Le tue scelte personali (modalità, feed, strategie attive) mettile in `runtime/impostazioni.yaml`: hanno la precedenza su `config/settings.yaml` e gli aggiornamenti non le toccano. Esempio:

```yaml
mode: paper
feed: {provider: betfair, reference: odds_api, record: true}
active_strategies: [S05_favoriti_exchange_v2]
```

Dashboard: `http://localhost:8766` (solo sul tuo PC). In modalità live mostra il database dei soldi veri (`runtime/betbot_live.db`), in paper quello dei soldi finti (`runtime/betbot.db`).

Dal telefono: metti `dashboard_lan: true` in `runtime/impostazioni.yaml`, riavvia e apri l'indirizzo che Bet_bot scrive all'avvio (`http://192.168.x.x:8766`) con il telefono sulla stessa rete Wi-Fi. Dal telefono si guarda soltanto: impostazioni, chiavi e Betfair si cambiano solo dal PC. La prima volta Windows chiede se consentire l'accesso alla rete privata: rispondi sì solo per le reti private.

## Note pratiche

- **PC acceso e sveglio**: il bot lavora solo a PC acceso; mentre gira chiede a Windows di non andare in sospensione. Gli ordini sono fill-or-kill, quindi non restano ordini "appesi" se il PC si spegne; un trade aperto viene chiuso al riavvio successivo.
- **Un solo bot alla volta**: se lo rilanci mentre è acceso, si apre solo la dashboard. Se la porta 8766 è occupata da un'altra finestra (per esempio la dashboard di `simula.bat`), Bet_bot non parte e lo dice: chiudi quella finestra.
- **Spegnere**: `python betbot.py ferma` oppure CTRL+C nella finestra del bot. In entrambi i casi niente nuove puntate e i trade aperti vengono chiusi prima di uscire (al massimo 2 minuti; un secondo CTRL+C esce subito).
- **Termini d'uso**: Betfair consente i bot personali tramite API ufficiale con la tua app key. Da novembre 2025 su betfair.it sono stati disattivati i software di terze parti: Bet_bot è tuo e usa solo le tue chiavi.
- **Gioco responsabile**: metti in Betfair un limite di deposito. Il bot non deposita e non preleva mai.
