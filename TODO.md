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

## Restyling del monitor — PROPOSTO, da approvare

Vedi la proposta del 2026-10-07. In sintesi: ridurre la panoramica e la media,
affiancare i due spettri, ingrandire le forme d'onda, aggiungere una scala
S/M/L e i grafici in finestra separata.
