# Da fare

Cose decise ma non ancora applicate, con il motivo per cui aspettano.
Quando una e' fatta si toglie da qui: il posto della documentazione sono
CLAUDE.md e i commenti nel codice, questa e' solo una lista di lavoro.

## Calibrazione: il punto a 20 ns — da fare lunedi 2026-10-12

Il banco e' pronto e il generatore e' gia' su ch16 (SiPM scollegato da quel
canale, restano su ch17 e ch18). Serve **cambiare solo la larghezza**: da
200 ns a ~20 ns, che e' quella degli impulsi del SiPM al buio. Tutto il resto
e' gia' configurato e verificato.

Perche' serve: fra 1.6 ns (3.93 mV/offset) e 189 ns (0.436) ci sono otto
volte, e i 20 ns stanno in mezzo dove la dipendenza e' ripida e non ne
conosciamo la forma. Senza quel punto, il self-trigger sui singoli impulsi
— che e' la domanda del progetto — non ha una soglia in millivolt.

Procedura, la stessa di venerdi:

1. `SelfTrigger = true`, `ExternalTrigger = false`, `SelfTriggerChannels = [16]`,
   `SelfTriggerThresholdOffset = [8]`, `DCOffset = 0x8000`, `NEvents = 200000`.
   **`DCOffset` va lasciato a 0x8000**: con 0xC000 il generatore porta ch16
   sotto zero e il 48% dei campioni finisce schiacciato a 0.
2. Run breve a soglia bassa: da li' si misurano ampiezza e FWHM vere.
3. `tools/scan_v1742.sh -o "..." -s 45`, centrato su `d50 ~ ampiezza / k`
   atteso. Se k fosse ~1 mV/offset e l'ampiezza 190 mV, d50 ~ 190.
4. Il denominatore dell'efficienza **si prende dal plateau**, non dallo
   scaler: venerdi' lo scaler dava 88 Hz e il plateau 91.6.
5. Salvare in `measurements/`, disegnare con `tools/plot_calibrazione.py`.

Seconda ampiezza (meta') per la linearita', come a settembre: due punti che
concordano entro il 3% sono quello che dice che non c'e' un errore grossolano.

## Scelta automatica della calibrazione in plot_scan

Oggi `MV_PER_OFFSET` e' indicizzata sulla sola frequenza di campionamento, e
quella chiave non basta: conta la larghezza, che vale un fattore 8. Il
conteggio per canale **rilegge gia' le forme d'onda**, quindi puo' misurare
la FWHM degli impulsi che sta contando e scegliere da se' il punto giusto.

1. La tabella diventa un elenco di punti misurati: frequenza, larghezza,
   segno, valore, file della misura in `measurements/`.
2. `plot_scan` misura la FWHM nella run e prende il punto piu' vicino in
   larghezza, **solo se entro un fattore 2**; se no si rifiuta come adesso.
3. L'asse dichiara quale punto ha usato e che larghezza ha misurato, come
   fa gia' con `ASSUMED`.

La casella `mV/offset` nella pagina resta come via d'uscita, ma vuota vuol
dire "scegli tu" e nell'uso normale non si tocca.

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

## ~~Pannelli richiudibili~~ — FATTO il 2026-10-07

Approvato il 2026-10-07. Mockup: `proposta-pannelli-richiudibili.png`.

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

## ~~Pulizia dei grafici del monitor~~ — FATTO il 2026-10-07

Proposto il 2026-10-07. Mockup: `proposta-monitor-pulizia.png`, costruito
sulle immagini VERE del monitor.

1. I due spettri alla STESSA dimensione: oggi sono 480x300 e 480x340.
2. Via le didascalie in fondo, che durante una run normale non si leggono:
   la calibrazione sotto lo spettro di ampiezza, e le righe con cancello,
   impedenza, verso e finestra del piedistallo sotto quello di carica.
3. Via gli avvertimenti da messa a punto: "not enough statistics",
   "triggering on noise", "threshold inside the noise", "white-noise floor"
   nel sottotitolo, "hatched: no event above the cut" nella panoramica.

Fatto con l'interruttore `details`, spento di partenza, non cancellando. Il
motivo: una carica senza il suo cancello non e' confrontabile con un'altra, e
quelle righe sono l'unico posto dove sta scritto con quali numeri il grafico
e' stato fatto. Durante la presa dati sono rumore; quando si confrontano due
misure servono. Misurato dopo: i due spettri sono 480x310 tutti e due, con e senza
dettagli.

## Da guardare sullo schermo

Delle due pagine rifatte si e' verificata la struttura, non l'aspetto.
Due punti su cui ho dei dubbi: le etichette degli assi della panoramica
stretta stanno larghe, e i due spettri affiancati con molti canali diventano
piu' larghi dello schermo e vanno a capo (e' voluto, ma va visto).

Riavviare il monitor NON tocca la presa dati: si fa da *Stop monitor* /
*Start monitor* nella pagina di controllo.
