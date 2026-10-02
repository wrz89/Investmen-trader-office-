# Bet_bot — conoscenza di base per chi ci lavora (umano o Claude)

Bot di scommesse automatiche per **Betfair Exchange Italia** (betfair.it), sul PC Windows dell'utente in D:\Claude\Bet_bot.
Capitale di partenza 30 €. L'utente è italiano e non tecnico: rispondi in italiano, con passi pratici e file .bat da
lanciare con doppio clic. Il PC si aggiorna con `aggiorna.bat` dal ramo scritto in `runtime/ramo.txt`.

## Regole che non si toccano
- Default **paper**. Mai mettere `mode: live`, `live_strategies`, `execution.lay_apertura` o "puntate reali" senza una
  richiesta esplicita dell'utente E un esame per il live superato (`esame.bat`, criteri in config/esame_live.yaml
  decisi prima dei risultati: non cambiarli dopo averli visti).
- Eccezione decisa dall'utente il 30/09/2026: soldi veri "per divertimento" SOLO con S10_divertimento_v2 (prima i lay
  di valore sul calcio con 0,50 € del backer, altrimenti back da 2 €; max 10 al giorno, una aperta, EV ≥ −3%, freni fun_*
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
- autotest.py: con `avvia` lancia in sottofondo orizzonti (1/giorno), allenamento (1/settimana), test rapido
  (1/settimana, ≥250 crediti). Live dalla dashboard: /api/live/on|off → live_switch + riavvio (runtime/riavvio.richiesta,
  `avvia` esce con 3, avvia.bat riparte subito).
- storico_betfair.py: legge lo storico ufficiale Betfair (piano Basic gratuito, LTP minuto per minuto, formato Stream
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
- bollettino.py: ogni mattina dalle 8 Leo manda (Telegram, kind "report") classifica dell'esame, soldi veri, misure e
  proposta; non cambia MAI da solo le live_strategies. `python betbot.py bollettino` lo mostra subito.
- betbot/core.py (ciclo), agents/risk.py (Risk Manager e freni), agents/banco.py (ordini e chiusure),
  execution.py (Betfair e paper), agents/coach.py (Leo: autopsie e regole che possono solo frenare),
  esame.py, test_rapido.py, palestra.py, backtest*.py, feeds/betfair.py, collega_betfair.py.
- Test: `python -m pytest -q tests` (devono restare tutti verdi).

## Da fare prima di rispondere "si può guadagnare X"
Controlla tu, senza aspettare che l'utente lo chieda: tutti i mercati sensati (1X2, Under/Over, handicap), back e lay,
dati puliti, margine d'errore, commissione, e il costo dei dati. Se il risultato è negativo, dillo chiaramente.
