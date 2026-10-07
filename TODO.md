# Da fare

Cose decise ma non ancora applicate, con il motivo per cui aspettano.
Quando una e' fatta si toglie da qui: il posto della documentazione sono
CLAUDE.md e i commenti nel codice, questa e' solo una lista di lavoro.

## ~~Restyling della pagina di controllo~~ — FATTO il 2026-10-07

Applicato e in servizio. Resta da guardarlo sullo schermo vero: la struttura
e' verificata (div bilanciati, check_page passa), l'aspetto no -- per quello
servirebbe un browser.

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

## ~~Restyling del monitor~~ — FATTO il 2026-10-07

Applicato e in servizio. Dimensioni misurate dopo, su un file a un canale:
panoramica 770x550 -> 423x352, waveforms 900x290 -> 1100x330, media
900x400 -> 700x250, spettri 500x340 e 500x430 -> 480x300 e 480x340
affiancati. Impilati: 2010 -> 1272 px, -37%.

NOTA su un numero sbagliato nella proposta: li' la panoramica "di prima" era
data 900x250, ma quello era il PLACEHOLDER (9x2.5 a dpi 100), misurato in un
momento in cui la panoramica non aveva dati. La panoramica vera era 7x5.0 a
dpi 110, cioe' 770x550.

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

## Pannelli richiudibili — PROPOSTO, da approvare

Proposto il 2026-10-07. Mockup: `proposta-pannelli-richiudibili.png`.

Il bersaglio vero sono le due tabelle per canale dentro *Configuration*:
**32 righe** per il V1742 e **16** per il V812, contate nel codice
(`tabellaCanali`), non stimate. Sono di gran lunga la cosa piu' alta della
pagina, e quasi sempre non si guardano.

1. Le due tabelle diventano richiudibili dal loro stesso titolo.
2. Chiuse tengono una riga di RIEPILOGO -- "record 16 · self-trigger 16,
   offset 3" -- cosi' richiudere non fa perdere l'informazione per cui le si
   aprirebbe.
3. La tabella del CFD parte chiusa quando `cfd.Enabled = false`: quando il
   modulo non si usa sono sedici righe di peso morto.
4. Lo stesso meccanismo su tutti i riquadri di primo livello, con lo stato
   ricordato nel browser come per le spiegazioni col "?".

Stima: con le due tabelle e *Recent actions* chiusi, -56% di altezza.

## Da guardare sullo schermo

Delle due pagine rifatte si e' verificata la struttura, non l'aspetto.
Due punti su cui ho dei dubbi: le etichette degli assi della panoramica
stretta stanno larghe, e i due spettri affiancati con molti canali diventano
piu' larghi dello schermo e vanno a capo (e' voluto, ma va visto).

Riavviare il monitor NON tocca la presa dati: si fa da *Stop monitor* /
*Start monitor* nella pagina di controllo.
