# Bet_bot — conoscenza di base per chi ci lavora (umano o Claude)

Bot di scommesse automatiche per **Betfair Exchange Italia** (betfair.it), sul PC Windows dell'utente in D:\Claude\Bet_bot.
Capitale di partenza 30 €. L'utente è italiano e non tecnico: rispondi in italiano, con passi pratici e file .bat da
lanciare con doppio clic. Il PC si aggiorna con `aggiorna.bat` dal ramo scritto in `runtime/ramo.txt`.

## Regole che non si toccano
- Default **paper**. Mai mettere `mode: live`, `live_strategies`, `execution.lay_apertura` o "puntate reali" senza una
  richiesta esplicita dell'utente E un esame per il live superato (`esame.bat`, criteri in config/esame_live.yaml
  decisi prima dei risultati: non cambiarli dopo averli visti).
- Mai chiedere, stampare o salvare in chiaro password, app key, token, chiave privata del certificato.
- `runtime/` sono i dati dell'utente: non va su GitHub. Le impostazioni personali stanno in runtime/impostazioni.yaml.
- In caso di dubbio sui soldi veri si blocca (kill switch), non si indovina.

## Betfair.it (verificato sul conto vero, 30/09/2026)
- Back minimo 2 € a multipli di 0,50; lay: puntata del backer ≥ 0,50 €. Commissione 4,5% sulla vincita netta di mercato.
- Niente ippica su .it. Liquidità separata dal mercato internazionale (spread più larghi).
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
- Ritirate: S04 cavalli, S07 scalping pre-partita, S08 basket nel 4° quarto.
- L'unica ipotesi ancora aperta: su betfair.it i prezzi restano indietro rispetto a Pinnacle? Si verifica con
  `test_rapido.bat` (6 ore, CLV contro la chiusura, nessuna puntata) e con le registrazioni + `replay` + `esame`.

## Dove guardare nel codice
- betbot/core.py (ciclo), agents/risk.py (Risk Manager e freni), agents/banco.py (ordini e chiusure),
  execution.py (Betfair e paper), agents/coach.py (Leo: autopsie e regole che possono solo frenare),
  esame.py, test_rapido.py, palestra.py, backtest*.py, feeds/betfair.py, collega_betfair.py.
- Test: `python -m pytest -q tests` (devono restare tutti verdi).

## Da fare prima di rispondere "si può guadagnare X"
Controlla tu, senza aspettare che l'utente lo chieda: tutti i mercati sensati (1X2, Under/Over, handicap), back e lay,
dati puliti, margine d'errore, commissione, e il costo dei dati. Se il risultato è negativo, dillo chiaramente.
