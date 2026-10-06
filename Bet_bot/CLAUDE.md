# Bet_bot — conoscenza di base per chi ci lavora (umano o Claude)

Bot di scommesse automatiche per **Betfair Exchange Italia** (betfair.it), sul PC Windows dell'utente in D:\Claude\Bet_bot.
Capitale di partenza 30 €. L'utente è italiano e non tecnico: rispondi in italiano, con passi pratici e file .bat da
lanciare con doppio clic. Il PC si aggiorna con `aggiorna.bat` dal ramo scritto in `runtime/ramo.txt`.

## Regole che non si toccano
- Default **paper**. Mai mettere `mode: live`, `live_strategies`, `execution.lay_apertura` o "puntate reali" senza una
  richiesta esplicita dell'utente E un esame per il live superato (`esame.bat`, criteri in config/esame_live.yaml
  decisi prima dei risultati: non cambiarli dopo averli visti).
- Eccezione decisa dall'utente il 30/09/2026: soldi veri "per divertimento" SOLO con S10_divertimento_v2 (prima i lay
  di valore sul calcio con 0,50 € del backer, altrimenti back da 2 €; max 10 al giorno, fino a 3 aperte insieme (fun_open_risk_pct 10% del conto), EV ≥ −3%, freni fun_*
  in risk_limits.yaml), acceso da lui (dashboard "Passa ai soldi veri" o `vai_live.bat`, conferma scritta "SI"), spento
  con `torna_paper.bat` o dalla dashboard. Con la app key delayed va in live solo S10 (ordini fill-or-kill: un prezzo
  vecchio può solo annullare l'ordine). La puntata NON cresce col saldo (scelta dell'utente: solo dopo un esame
  superato). Versamenti: `deposito.bat`. Non aggiungere altre strategie a live_strategies senza esame superato.
- Mai chiedere, stampare o salvare in chiaro password, app key, token, chiave privata del certificato.
- `runtime/` sono i dati dell'utente: non va su GitHub. Le impostazioni personali stanno in runtime/impostazioni.yaml.
- In caso di dubbio sui soldi veri si blocca (kill switch), non si indovina.

## Betfair.it (verificato sul conto vero, 30/09/2026)
- Back minimo 2 € a multipli di 0,50; lay: puntata del backer ≥ 0,50 €. Commissione 4,5% sulla vincita netta di mercato.
- Niente ippica su .it. Liquidità separata dal mercato internazionale (spread più larghi).
- Football americano: eventTypeId 6423, Match Odds a due esiti (supplementari compresi). Pari dopo i supplementari =
  dead heat (due WINNER): metà puntata pagata a quota piena → result "tie" (feeds/betfair.py, banco.dead_heat_net).
  Nel bot l'NFL si legge, si registra e si misura (test rapido); nessuna strategia ci punta finché i dati non lo dicono
  (S05 v2 e S09 non lo includono: per aggiungerlo si crea una versione nuova della strategia).
- Login con certificato (`identitysso-cert.betfair.it`): quello con utente e password richiede una chiave abilitata.
- `getDeveloperAppKeys`/`createDeveloperAppKeys` vanno chiamate **senza** X-Application. Una chiave appena creata
  risponde AANGX-0004 per 1-3 minuti. La chiave "delayed" (gratuita) ha prezzi in ritardo fino a 3 minuti.

## Come si misura (errori già fatti: non ripeterli)
- **CLV = quota presa contro quota giusta di Pinnacle alla chiusura** (back: quota × p_chiusura − 1; lay:
  1/(quota × p_chiusura) − 1). NON contro la propria stima all'ingresso: è distorta verso il negativo per selezione.
- Probabilità giusta = Pinnacle senza margine (metodo potenza). Il consenso dei soft book è peggiore.
- Il ROI si misura sul rischio (per un lay la responsabilità = (quota − 1) × puntata del backer).
- football-data.co.uk: le colonne BFE/BFEC contengono ~11% di record rotti (prezzi incoerenti con Pinnacle). Filtrarli
  sempre (`odds.exchange_prices_sane`), altrimenti qualunque backtest di lay sembra vincente.
- Ogni prova su molte fasce/mercati va letta con il margine d'errore e ricordando quante prove sono state fatte.
- Se un modello "trova" molte occasioni col CLV negativo, sta leggendo rumore: freno forte (L2) e regola della fiducia.

## Cosa è già stato provato (dati reali)
- Favoriti "80% a quota 1,15-1,25": perdono dopo la commissione. Nessuno sport ha un vantaggio sistematico sui favoriti.
- Tennis, basket, NBA, rugby: in pari o in perdita. Hockey: pochi favoriti netti.
- Mercurius: proprietario e chiuso; la ricostruzione con Dixon-Coles non aggiunge nulla al mercato.
- Palestra di Leo (28.752 partite 2021-26, 9.125 con xG e formazioni Understat): Leo = mercato. Forma, Elo, tiri, xG,
  riposo, classifica, assenze pesate sono già nelle quote. Nessuna dinamica batte la chiusura in modo solido.
- S09 lay di valore (1X2, quote 3-8, EV ≥ 2%): dati puliti +5,8% ± 5,7% su 95 lay → non significativo. In ombra.
- Under/Over 2,5 su Betfair contro Pinnacle: nessun vantaggio (lay EV≥2%: −0,6% ± 3,1% su 1.094; back: pochi e negativi).
- NFL 2012-2025 (nflverse, 3.828 partite, 27 prove decise prima, `python betbot.py nfl`): testa a testa, handicap e
  totale punti contro la chiusura con exchange simulato (giusto −1%, commissione 4,5%): nessuna prova solida. Le più
  "promettenti" (under con vento ≥ 15 mph +8,9% ± 11,1% su 296; ospite sfavorita all'handicap +0,2%) non sono
  significative. È il mercato più efficiente al mondo: vale solo l'ipotesi "betfair.it in ritardo su Pinnacle".
- r/AutomatedBettingBots (letto il 30/09/2026 via archivio Arctic Shift, 554 post): quasi tutto promozione, nessun CLV
  verificato su campioni ampi. Idee tenute: filtro sui movimenti di prezzo (→ S05 v3, in ombra), orizzonte d'ingresso
  (→ `orizzonti.bat` sulle registrazioni), freno sul CLV mobile (Leo lo fa già per segmenti).
- Sentiment (Giorgia): le notizie (Google News) sono spente, nessuna prova di valore. Il "mercato contro" ora si misura
  sul prezzo Betfair di ogni ciclo: col feed Betfair le quote di riferimento (ogni 2-6 ore) erano ferme e non scattava mai.
- "90% di vinte": esiste (favoriti a 1,05-1,10 vincono il 90%) ma per andare in pari serve il 92-93%: −2,8% su 229
  (chiusura Pinnacle, football-data). L'obiettivo è il CLV/ROI, non la percentuale di vinte.
- Allenamento (`allenamento.py`, 30/09/2026): Pinnacle azzecca il favorito 51,5% (atteso 51,3%), calibrazione ok.
  Sui BFE 2024-26 puliti: S10 1.239 puntate ROI +0,2% ± 5,5%, CLV +0,2% ± 0,4%; S09 95 lay ROI +4,8% ± 11,3%,
  CLV +15% (prezzi del venerdì: i movimenti fino alla chiusura sono grandi, la causa "notizia" scatta spesso);
  S05 7 puntate.
- PPDA e "deep" (Understat getLeagueData → teams[].history: ppda att/def, ppda_allowed, deep, deep_allowed; "deep" è il
  sostituto gratuito del Field Tilt), media delle ultime 6, 5 campionati 2021-26, 7.333 partite, allenamento 2021-23 e
  prova 2024-26: log-loss modello 0,9688 contro mercato 0,9684 (peggio), chiusura 0,9666; correlazione con il movimento
  fino alla chiusura 0,00. Le "occasioni" con +3%: CLV +0,7% ± 0,4% al prezzo GIUSTO di Pinnacle (prima di spread e
  commissione): non sfruttabile. ATTENZIONE fuso orario: football-data e Understat hanno orari diversi; con `d < data`
  l'xG della partita stessa entrava nella media e il modello "batteva" la chiusura. Escludere tutto il giorno.
- Ritirate: S04 cavalli, S07 scalping pre-partita, S08 basket nel 4° quarto.
- L'unica ipotesi ancora aperta: su betfair.it i prezzi restano indietro rispetto a Pinnacle? Si verifica con
  `test_rapido.bat` (6 ore, CLV contro la chiusura, nessuna puntata; calcio e NFL con verdetti separati) e con le
  registrazioni + `replay` + `esame`. Il test rapido sceglie da solo la finestra con più partite (calendario /events
  gratuito) e misura calcio, NFL, tennis, basket e baseball con verdetti separati (tennis: orario indicativo,
  tolleranza 4 ore e nomi abbinati anche in ordine inverso).

## Dove guardare nel codice
- orizzonti.py: CLV e liquidità di betfair.it a 72/48/24/12/6/3/1 ore dall'inizio, dalle registrazioni (le registrazioni
  hanno `ref_ts` dal 30/09/2026: prima l'età di Pinnacle si stima dal primo momento in cui la quota cambia).
- autotest.py: con `avvia` lancia in sottofondo orizzonti (1/giorno), multiple (1/giorno), allenamento, backtest (1/settimana), NFL (1/mese), test rapido
  (1/settimana, ≥250 crediti). Live dalla dashboard: /api/live/on|off → live_switch + riavvio (runtime/riavvio.richiesta,
  `avvia` esce con 3, avvia.bat riparte subito).
- storico_betfair.py (NON usabile dall'Italia: historicdata.betfair.com blocca gli IP italiani e la VPN è vietata, non
  suggerirla; si usano le registrazioni di betfair.it + orizzonti.py): legge lo storico ufficiale Betfair (piano Basic gratuito, LTP minuto per minuto, formato Stream
  API .bz2/.tar in runtime/storico_betfair/), abbina football-data e misura scarto e lay di valore a 24h/6h/1h/15'.
- allenamento.py ha anche il tennis (tennis-data: risponde 403 dai server esteri, gira sul PC dell'utente).
- S10_misura_v1 (ombra, mai live): la logica di S10 v2 su tutte le partite adatte, senza limite al giorno, per arrivare
  in fretta alle 200 puntate dell'esame. Leo: chiusura = Pinnacle se fresco al via (≤ 90'), altrimenti prezzo medio di
  Betfair alla chiusura (track.p_close_bf, features.close_src); prima una Pinnacle vecchia dava CLV finto ≈ 0.
- multiple.py: doppie e triple VIRTUALI dalle puntate singole di S10 misura (ombra, stesso giorno, partite diverse,
  prodotto delle quote, commissione una volta): misura e "gambe fragili" per sport/fascia, nel bollettino. Le regole di
  Leo imparate su S10_misura_v1 frenano anche S10_divertimento_* (coach.check). Sport letti da Betfair: calcio, tennis,
  basket, NFL, baseball, hockey (7524), pallavolo (998917), freccette (3503), snooker (6422), ping pong (2593); gli ID
  sono da verificare sul conto .it (se mancano o hanno 3 esiti il feed li salta).
- S10 v2 con soldi veri esclude nazionali e amichevoli (regex NATIONAL in s10_divertimento_v2.py, 04/10/2026, dopo
  una perdita su Italia-Francia): i dati di verifica sono tutti di club. S10 misura le tiene, per misurarle in ombra.
  Lay solo fino a quota 5 (rischio ≤ 2 €).
- Audit del 04/10/2026: (1) i lay di S10 v2 usavano Pinnacle vecchio fino a 2,5 ore (il rinfresco su richiesta vale solo
  per i favoriti 1,10-1,40 di S05): lo "scarto" poteva essere solo Pinnacle rimasto indietro → ora lay solo con Pinnacle
  ≤ 90' (lay_max_ref_age_s); (2) l'esame mescolava lay e back del divertimento → ora righe separate "· solo lay" /
  "· solo back" (esame.evaluate_all). Molteplicità: con 7+ strategie in esame al 95% la probabilità che una passi per
  caso è ~30%: la prova principale è "S10 · solo lay" (CLV > 0), le altre sono esplorative (a 99%).
- S10 misura è LARGA di proposito (lay fino a quota 8, anche con Pinnacle fino a 2,5 ore) per far crescere il campione
  dei lay; Leo la separa col segmento "eta_riferimento" (e coach.check lo conosce: una regola può bloccare i lay con
  Pinnacle vecchio anche in S10 live), l'esame ha la riga "S10 · solo lay fresco" (Pinnacle ≤ 90'). Il collo di
  bottiglia dei lay è il riferimento Pinnacle (≈15 crediti/giorno col piano gratuito), non le partite. Non contare due
  esiti della stessa partita come campioni indipendenti (il shadow ne apre uno per partita).
- bollettino.py: ogni mattina dalle 8 Leo manda (Telegram, kind "report") classifica dell'esame, soldi veri, misure e
  proposta; non cambia MAI da solo le live_strategies. Esce anche all'avvio (avvia.bat) se alle 8 il bot era spento
  e quello di oggi non è ancora uscito: va a video, su Telegram e in runtime/reports/bollettino.md. `python betbot.py bollettino` lo mostra subito.
- betbot/core.py (ciclo), agents/risk.py (Risk Manager e freni), agents/banco.py (ordini e chiusure),
  execution.py (Betfair e paper), agents/coach.py (Leo: autopsie e regole che possono solo frenare),
  esame.py, test_rapido.py, palestra.py, backtest*.py, feeds/betfair.py, collega_betfair.py.
- Letteratura e forum (ricerca del 03/10/2026, nessun codice cambiato):
  • closingline (GitHub, Dixon-Coles su gol/xG 50-50 + Elo + GBM, 5.286 partite): NON batte la chiusura Pinnacle
    (Brier +2,1%, ROI simulato −10,8%); forma, tiri, formazioni perfette, rosa: nessun segnale in più. Conferma: non
    costruire un nostro modello di calcio, il vantaggio non è nei dati pubblici.
  • football-data.co.uk (35.570 partite, 22 campionati): il value rispetto a Pinnacle funziona sui BOOKMAKER morbidi
    (EV ≥ 5%: rendimento reale 119% su 1.024 puntate), non sull'exchange; per le regole del progetto resta fuori.
  • favourite-longshot bias: sugli exchange è assente o inverso (Betfair riflette le probabilità vere): il lay di
    quota 3-5 NON si regge su quel bias ma solo sullo scarto con Pinnacle (da misurare con CLV, non da dare per vero).
  • "Beating the market with a bad predictive model" (arXiv 2010.12508, NBA 2006-14, 9.093 partite): un modello
    decorrelato dal mercato rende +1-1,7% con stake tipo Sharpe, ma contro Pinnacle (margine 2,5%) e con reti
    neurali su 14 anni di box score; con commissione 4,5% e senza dati equivalenti non è replicabile da noi.
  • in-play xG (arXiv 2605.16066, 140 partite EPL, ROI 4,5% su 17.458 puntate): campione minuscolo e puntate correlate
    (125 a partita), con puntata fissa il ROI è −3,4%; gli autori lo chiamano preliminare. In più serve in-play con
    tempi reali (la nostra chiave app ha ritardo 1-180 s): scartato.
  • KellyBench (arXiv 2604.27865): tutti i modelli di frontiera perdono sulla Premier 2023-24, alcuni vanno a zero:
    conferma i freni (puntata fissa, stop giornaliero, kill switch) e che l'LLM non è un edge.
- sport_disponibili.py (`sport_disponibili.bat`): sola lettura, elenca gli sport del conto betfair.it con n. di Match
  Odds ed esiti e stampa le righe `extra_sports` per quelli non ancora letti (futsal, floorball, bandy…). Rugby a 15/13
  e pallamano sono già nell'elenco (id Betfair 5, 1477, 468328, da verificare col comando: se il conto non li ha o hanno
  un numero di esiti diverso si saltano da soli). Storico con quote: gratis solo calcio (football-data) e tennis
  (tennis-data); per basket, hockey, pallavolo, pallamano, rugby ecc. non esiste uno storico gratuito con quote
  affidabili (solo a pagamento): lì "allenarsi" = registrare i prezzi veri e misurare il CLV in ombra (S10 misura,
  segmento "sport" di Leo), non rigiocare il passato.
- Sport nuovi (auto_sports, ogni 6 h): il feed scopre da solo gli sport del conto con Match Odds a 2-3 esiti (chiave
  "x<nome>") e li registra; S10 live gioca SOLO l'elenco vagliato (v1.SPORTS), S10 misura (all_sports=True) li misura in
  ombra. Il tetto in live è 30 € (live_kill_below_bankroll / live_max_drawdown_small, solo con mode live: il paper parte
  da 30 € e con quel tetto sarebbe fermo). `deposito.bat` somma al bankroll del bot i soldi versati su Betfair (con "SI").
- perche.py (`perche.bat`, banner "Perché non punta" in dashboard in live): freni attivi, tetti del divertimento
  (aperte/giorno, puntate aperte da >6 h dopo l'inizio = incastrate), motivi dei veti delle ultime ore, lezioni di Leo.
- Regola "correggi" di Leo: confronta l'EV corretto con la soglia della strategia (fun_min_edge −3% per il
  divertimento, min_edge per le altre). Prima del 05/10/2026 confrontava con 0 e bloccava TUTTO il divertimento.
- 4fun (05/10/2026, nome nuovo del "divertimento"; gli id restano S10_divertimento_*): puntata fissa fun_stake_eur
  5 € dal 05/10/2026 (lay: rischio 5 €), live solo con EV ≥ −2,5% (fun_min_edge) e almeno 100 € al prezzo (S10 v2 DEFAULTS); niente
  puntate col mercato in uscita (sentiment "caution" = veto per il 4fun). S10 misura resta larga (−3%, 10 €).
- Crediti Pinnacle (05/10/2026): PRIMA 5 chiavi × ogni 2 h + punteggi = il budget di 15/giorno finiva in 2 giri, quindi
  Pinnacle era quasi sempre vecchio (>90') e i lay non partivano mai (0 lay su 100+ puntate). ORA: calcio solo su
  richiesta (feeds/__init__.lay_reference_needs: esito a quota 2,9-5,3, libro stretto, inizio 20'-4 h, Pinnacle assente
  o >40'), un campionato alla volta, ≥40' tra due letture e max 4 al giorno per campionato; NBA/NFL ogni 12 h; punteggi
  spenti. Chiave di campionato sconosciuta (404) = non si riprova. Limite: i campionati nella mappa LEAGUE_KEYS.
- Elo dai risultati NON aggiunge informazione oltre la chiusura Pinnacle: 46.874 partite 2015-2026, walk-forward per
  stagione, log-loss mercato 0,98464 / calibrato 0,98468 / +Elo 0,98460 (guadagno 0,00008, rumore). Conoscere "meglio
  le squadre" dai soli risultati non batte il mercato; non costruire un modello di squadre per scommettere.
- Over/Under 2.5 (05/10/2026, 33.467 partite, Pinnacle chiusura): mercato calibrato (0,5011 prevista vs 0,5044 reale);
  i gol medi delle ultime 10 partite delle due squadre aggiungono 0,0002 di log-loss (0,0003-0,0007 nelle ultime
  stagioni: sotto la commissione); back cieco su Betfair: over −5,7%, under −6,3%. Nessun vantaggio.
- perche.bat mostra anche i "Lay (ultimo ciclo)": partite, candidati a quota 3-5, quanti con Pinnacle fresco, crediti.
- fun_max_backs_per_day 4: i back (CLV −0,6%) non finiscono il budget giornaliero, il resto è dei lay.
- Dashboard 05/10/2026: Giorgia (sentiment) non è più nella sala 3D (il sentiment gira ancora nel bot come freno, la
  sua scheda non c'è più); al suo posto il fold "Calendario partite" (calendario.py, /api/calendario/*): mese, sport e
  campionato da Betfair (listCompetitions + listMarketCatalogue, fino a 31 giorni, 200 per richiesta), altrimenti le
  partite già viste dal bot; cache 20 minuti.
- stats4bets.it (05/10/2026, "SuperFoglio", Easy Over 2.5, Super Over 1.5, X-45, scala pura/favorita): nessun metodo ha
  campione, periodo o ROI pubblicati; il "95%" non è verificabile. Test sui nostri dati (51.009 partite): Over 2.5 con
  Pinnacle 1,48-1,72 vince il 61,1% contro il 62,0% implicito (ROI −1,6%; con la "migliore quota di mercato" +0,7%, ma è
  cherry-picking a posteriori); "scala pura" casa 1,50-1,70: 62,3% vs 62,4% (ROI −0,1%); pareggio al primo tempo con
  Under forte: 49,7% (quota equa 2,01) mentre il sito parla di 1,80-2,00 (EV ≤ 0). Le loro regole selezionano partite
  che il mercato già prezza così: nessun vantaggio. Unico spunto non ancora testato: il flusso di denaro (volume
  abbinato per esito) sull'exchange come segnale; oggi si registrano solo prezzi e quantità al miglior prezzo.
- 4fun raddoppiato (05/10/2026): fun_max_bets_per_day 20 (era 10), fun_max_backs_per_day 8, fun_max_open 6, rischio aperto
  del 4fun al 20% (fun_open_risk_pct), max_open_bets 7 e max_bets_per_day 20 generali. Resta tutto il resto (5 €, EV ≥ −2,5%,
  100 € al prezzo, stop del giorno 9%): le puntate in più arrivano solo se rispettano gli stessi vincoli.
- Lettura anticipata (06/10/2026): esame.evaluate dà `early` (POSITIVA/INCERTA/NEGATIVA) dai 20 casi, prima dei 200; il
  bollettino ha la sezione "Lay, lettura anticipata". NON è un verdetto: PRONTA/BOCCIATA restano ai 200 casi.
- Qualità dei dati (06/10/2026, qualita.py): punteggio 0-100 (liquidità 30, spread 20, riferimento 25, campionato 15,
  anticipo 10) per ogni puntata; filtro fun_min_quality 40 (solo il 4fun live), scritto nel "Perché" e salvato in Leo come
  segmento "qualita" (<40, 40-55, 55-70, 70-85, ≥85). Non misura la forza delle squadre ma l'errore nostro. Se Leo mostra
  che le fasce alte NON hanno un CLV migliore, il filtro va tolto (è un'idea da dimostrare, non un fatto).
- Pronostico di Leo (06/10/2026, leo_pronostico.py): Elo + curva 1X2 dal calcio storico (51.005 partite, 433 squadre dei 16
  campionati di football-data; bootstrap una volta, ~6 s) e Elo a due esiti per gli altri sport, che impara SOLO dai
  risultati osservati (nessuna previsione finché una squadra ha <5 partite). A ogni ciclo registra le probabilità di Leo e
  del mercato (tabella leo_pronostici) e a partita finita ne confronta la log-loss. Pannello nel calendario
  (/api/leo/pronostici) e righe nel bollettino. NON cambia le puntate. Limiti: la palestra (palestra.py, con xG e
  formazioni) è più ricca ma lavora solo sullo storico; qui Elo semplice. Nomi Betfair non abbinati = nessun pronostico.
- Test: `python -m pytest -q tests` (devono restare tutti verdi).

## Da fare prima di rispondere "si può guadagnare X"
Controlla tu, senza aspettare che l'utente lo chieda: tutti i mercati sensati (1X2, Under/Over, handicap), back e lay,
dati puliti, margine d'errore, commissione, e il costo dei dati. Se il risultato è negativo, dillo chiaramente.
