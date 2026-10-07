# Da fare

Cose decise ma non ancora applicate, con il motivo per cui aspettano.
Quando una e' fatta si toglie da qui: il posto della documentazione sono
CLAUDE.md e i commenti nel codice, questa e' solo una lista di lavoro.

## Restyling della pagina di controllo — APPROVATO, in attesa

Approvato il 2026-10-07. Non applicato perche' una collega sta prendendo
dati e il cambiamento richiede di riavviare il controllore: la presa dati non
si ferma (la DAQ gira in una sessione sua), ma la pagina resta irraggiungibile
per qualche secondo.

Mockup del prima/dopo: `proposta-layout-controller.png` (ignorato da git,
sta solo sul Mac). Stima: ~1446 px -> ~811 px di altezza, -44%.

1. Colonna destra con *Current run*, *Run queue* e *Threshold scan* accanto a
   *Configuration*, che e' alto e lascia spazio vuoto di fianco.
2. *DAQ log* e *Recent actions* affiancati.
3. Le due spiegazioni lunghe (coda e grafici degli scan) dietro un `(?)`
   richiudibile: servono a chi arriva nuovo, non ogni giorno.
4. Densita': pagina 18->12 px, riquadri 14->10, titoli 10->6,
   bottoni 9x20->6x14, log 260->170 px.

Varianti offerte e non ancora decise: sciogliere *Current run* dentro la barra
in alto; riquadri richiudibili con lo stato ricordato nel browser.

## Restyling del monitor — APPROVATO, in attesa

Approvato il 2026-10-07, con la riserva esplicita che va verificato sul campo:
le proporzioni si giudicano sullo schermo vero, non su un mockup.

Mockup del prima/dopo: `proposta-layout-monitor.png` (ignorato da git).
Le dimensioni di "ora" sono MISURATE scaricando le immagini dal monitor in
esecuzione, con un canale acquisito.

```
                        ora            proposta
panoramica          900 x 250        450 x 170
waveforms           900 x 290       1100 x 330   <- l'unica che cresce
average             900 x 400        700 x 250
amplitude spectrum  500 x 340        480 x 300   affiancati
charge spectrum     500 x 430        480 x 340
                      1804 px         1165 px    -35%
```

1. Panoramica e media piu' basse, i due spettri affiancati.
2. Forme d'onda piu' GRANDI, e in larghezza: l'asse dei tempi e' quello che
   si legge.
3. Scala S / M / L che moltiplica tutte le figure, cosi' la dimensione giusta
   la sceglie chi guarda invece di indovinarla nel codice.
4. Un `↗` per grafico che lo apre in una finestra separata che continua ad
   aggiornarsi: serve il secondo schermo in laboratorio. Ogni grafico e' gia'
   una URL sua, serve una rotta nuova e poche righe.

Non serve: scegliere i canali da plottare c'e' gia', e' la casella `channels`
(accetta `8,9,12-15`) e limita anche la lettura dal file, non solo il disegno.
La panoramica apposta non la rispetta: serve a vedere quali canali sono vivi.
Da decidere se farla seguire comunque.

Riavviare il monitor NON tocca la presa dati: si fa da *Stop monitor* /
*Start monitor* nella pagina di controllo.
