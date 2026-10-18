# Perline

Progetta schemi per perline (Miyuki Delica) in **peyote** o **telaio**: griglia colorata,
generatore di rombi / chevron / righe, conteggio perline per colore (con "rp."),
misure e filo stimati, foglio di lavoro da stampare o salvare in PDF.

## Installazione (Windows, solo la prima volta)
1. Serve Python 3 (python.org, spunta "Add python.exe to PATH").
2. Crea una cartella, ad es. `C:\Perline`, e salvaci dentro `aggiorna_perline.bat`
   (dal repository, cartella `perline`).
3. Doppio clic su `aggiorna_perline.bat`: scarica tutto.
4. Doppio clic su `avvia_perline.bat`: si apre l'app nel browser.

## Aggiornamenti
Rilancia `aggiorna_perline.bat`. I tuoi progetti (cartella `dati`) restano intatti.
Se il repository e' privato crea `dati\github_token.txt` con un token GitHub di sola lettura
(permesso *Contents: Read-only*), come per `aggiorna.bat` dell'ufficio.

## Eseguibile (.exe)
La GitHub Action `Perline exe` compila `Perline.exe` e lo pubblica nella release
`perline-latest`. Non richiede Python; i progetti si salvano in `dati` accanto all'exe.

## Uso rapido
- Colori: clic su una riga della palette, poi disegna. Codici DB e tinte sono modificabili.
- **Generatore**: scegli motivo, righe per modulo e i colori degli anelli (centro -> sfondo).
- **Stampa / PDF**: pagina 1 schema con lettere e legenda, pagina 2 lettura riga per riga
  (`A3 B A2` = 3 perline A, 1 B, 2 A).
- "1 rp." = 200 perline (dedotto dagli schemi di riferimento: 220 perline = 1,1 rp.).
  Misure perlina (1,6 x 1,3 mm) e filo (lunghezza x4 + 30 cm) sono **stime**.

## Sviluppo
```
python perline.py            # avvia l'app
python -m pytest perline/tests
node perline/tests/core.test.js
```
