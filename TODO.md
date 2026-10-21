# Da fare

Cose decise ma non ancora applicate, con il motivo per cui aspettano.
Quando una e' fatta si toglie da qui: il posto della documentazione sono
CLAUDE.md e i commenti nel codice, questa e' solo una lista di lavoro.

## Calibrazione: il punto a 20 ns — da fare lunedi 2026-10-12

Nota del 2026-10-14: la premessa e' in dubbio. Sulle run al buio del
2026-10-06 gli impulsi del SiPM risultano larghi 83-104 ns, non 9-20
(CLAUDE.md, sotto "La larghezza si misura EVENTO PER EVENTO"), e il punto a
46.7 ns del 2026-10-13 sta gia' sull'altopiano. Prima di farlo, verificare la
larghezza vera.

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

## SiPM al buio: scan in soglia e run col trigger cieco — al banco

Aggiunto il 2026-10-14. Le run al buio del 2026-10-06 dicono che con offset 3
il self-trigger perde ~2/3 degli impulsi da 1 p.e. (CLAUDE.md, "Il
self-trigger con offset 3..."), ma non perche'. Due misure, nella stessa
sessione, con il SiPM al buio a una tensione fissa (54 V):

1. **Scan in `SelfTriggerThresholdOffset`** durante una sola run, con
   `tools/noise_scan.py` (registra i confini in eventi di ogni passo):
   offset 1, 2, 3, 4, 6, 8, 12, ~20 mila eventi per passo. Il rate non serve
   (DAQ satura); serve lo SPETTRO DI CARICA di ogni passo: la frazione di
   1 p.e. in funzione dell'offset e' la soglia effettiva in p.e.
2. **Una run con trigger che non dipende dal segnale**: generatore su TRG-IN,
   LED scollegato. Gli impulsi al buio sono allora davvero a caso: DCR sopra
   0.5/1.5/2.5 p.e., crosstalk indipendente, afterpulse dal rate dopo ogni
   impulso. Il generatore lontano dalla scheda: nelle run col LED il rumore
   e' 1.5-2 volte quello delle run al buio (sospetto il generatore, non
   verificato). Il trigger software in acquisizione oggi non c'e': il codice
   manda trigger software solo in `MeasureBaseline`.

Va fatto prima di usare il self-trigger sui pannelli o sul sistema a ~200
canali.

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

## Mail: cose rimaste dopo il 2026-10-14

1. **La stima dell'efficienza nel riepilogo giornaliero**, accanto al grafico
   del rate. Chiesta dall'utente, anche rozza; da definire con lui su quali
   dati e con che metodo.
2. **Verificare i bit di `LSR1?` con uno scatto vero**: abbassare l'OCP del
   bias sotto la corrente di lavoro con l'uscita accesa su un carico, e
   guardare quale bit si accende e cosa dice la mail. Il significato
   (2 = OVP, 3 = OCP) viene dal manuale e non e' mai stato visto accadere.

## Monitor: linea di base che non e' costante nella traccia — rimandato dall'utente il 2026-10-20

Il monitor toglie a ogni evento una COSTANTE, la mediana dei campioni nella
finestra del piedistallo del canale (scritta a mano, automatica, o la comune
10-180 ns). Un andamento lento della linea di base resta nella traccia e nel
cancello: nella media di ch24 (PMT amplificato, run del 2026-10-20) si vede
un'oscillazione di +-3 conteggi con periodo ~400 ns, che un cancello largo
(quello salvato era 150-500 ns, contro un impulso a ~220 ns) integra come se
fosse carica. Da decidere con l'utente se e come toglierla (andamento medio
sottratto, stima su piu' tratti, o solo cancelli stretti).

Insieme: la finestra di dettaglio potrebbe dire da dove viene la finestra del
piedistallo di quel canale (set / auto / common).

## Due V1742 — fase 1 (DAQ) scritta il 2026-10-20, da finire al banco rimontato

Scheda SiPM: seriale 51639, VME 0x32100000. Scheda PMT: seriale 202, VME
0x11110000 (commutatori 1111; con F00A rispondeva a 0xD00A0000, cioe' il bit
29 letto 0: forse un contatto del primo commutatore, non verificato).

Fatto e provato sulla scheda SiPM (trigger sul rumore, offset 3):
`DAQ-WC run.toml --board sipm --run N` fonde [boards.sipm] in [digitizer],
scrive `prova_9002_sipm_750Ms_40PT.h5` con Board/BoardSerial/VMEBaseAddress
nell'header, FormatVersion 3, e /events/event_counter e
/events/trigger_time_tag riga per riga con le forme d'onda (15620 eventi,
contatore senza salti). Rifiuta un numero di run gia' usato e una scheda che
non esiste. NON ancora in build/: installare dopo le prove sotto.

Da provare:
1. **La scheda PMT non e' pronta ad acquisire**: reset in timeout (-18),
   MallocReadoutBuffer fallisce. Registro 0x8104 = 0x420 contro 0x580 della
   scheda buona: letto col manuale a memoria, bit 5 = clock ESTERNO, bit 7 =
   PLL non agganciato, bit 8 = scheda non pronta; 0x8178 = 0x14, bit 4 =
   perdita del PLL. Ipotesi: sorgente di clock della scheda su esterno senza
   clock in ingresso (commutatore/ponticello sulla scheda). DA VERIFICARE sul
   manuale e sulla scheda, prima di tutto il resto.
2. Enabled = false rifiutato con --board (la prova si e' guastata per un mio
   errore nel file di prova, non per il codice).
3. Chiusura pulita con un solo SIGINT (nelle prove `timeout` ne mandava due).
4. Nuovo binario SENZA --board: file e log come prima, piu' i due dataset.
5. Le due schede insieme: banda del bridge con due letture di dati in
   contemporanea (finora provati solo registri: 16k e 22k letture in 8 s,
   zero errori).
6. Trigger Time Tag: dal datasheet DS3159 contatore a 30 bit, 8.5 ns, giro
   ogni ~9 s. (Il "~17 ns" scritto qui prima veniva da una durata della run
   assunta, non misurata: era sbagliato.) Da verificare sui dati: rate dal
   TTT contro rate dalla DAQ, e il giro a 2^30.
7. Sincronizzazione, dalla nota CAEN AN2862 e dal datasheet DS3159 (letti il
   2026-10-20): clock interno/esterno scelto da un COMMUTATORE MECCANICO
   sulla scheda; clock in daisy chain CLK-OUT -> CLK-IN (riferimento esterno
   50 o 58 MHz), con ritardo programmabile sul CLK-OUT; partenza comune con
   TRG-OUT -> S-IN (la slave armata su S-IN, la master avviata via software,
   ritardo di partenza da compensare) oppure con TRG-IN comune; S-IN puo'
   anche azzerare il TTT. CAEN distribuisce "SyncTest x742", un programma C
   di esempio che sincronizza due V1742: e' il riferimento per i registri.
   Manca ancora il manuale tecnico del V1742 (UM4270) con i registri.

8. **`/events/start_index_cell` scritta il 2026-10-20 a PC DAQ spento: NON
   COMPILATA.** Prima cosa al rientro: pull, compilare in un worktree,
   verificare che i valori stiano in 0-1023 e cambino evento per evento.
   Serve alla correzione PADME del piedistallo per cella: poi run sui PMT
   con sigma_ped (CAEN) contro (CAEN + PADME) e la media della traccia,
   per vedere se l'oscillazione lenta di ch24 resta (attesa: si', perche'
   gli offset per cella si mediano via con SIC casuale). Se la PADME rende,
   va in daqio con un interruttore nel monitor; la mediana per cella va
   presa FUORI dal cancello (i SiPM col LED occupano il 40% della traccia).
   Calibrazione PADME per i PMT da una run a TRIGGER SOFTWARE (decisione
   2026-10-21): ~10^4 eventi bastano (ogni cella ~N campioni, mediana a
   ~0.06 conteggi con 1000). Stessa frequenza, DCOffset e stato della
   correzione CAEN delle run da correggere; per scheda, canale e gruppo.
   Valutazione con calibrazione e verifica su META' DISGIUNTE degli eventi.
   Da misurare: stabilita' (due calibrazioni a ore di distanza) e accordo
   con la tabella ricavata dalle run di fisica stesse (PMT: impulso ~1%
   dei campioni); se coincidono, calibrazione automatica per run.
   Riferimento: workspace Testbeam2026_WC_unified (run 1339, wclib.py,
   SIC_RawView.ipynb, presentazioni/V1742 DRS4 correction.pdf): larghezza
   per cella ridotta ~4.3x, efficienza 75.5% -> 93.7%. Al test beam la
   correzione CAEN era ACCESA, la stessa delle nostre librerie (detto
   dall'utente il 2026-10-21): il confronto e' omogeneo. Ma il 4.3x e' sulla
   dispersione FRA LE CELLE, non sulla larghezza totale: li' la struttura
   residua sui PMT sani era ~1 mV (~4 conteggi), da noi nella run 0104
   (SiPM, settembre) 0.68 su 1.48 conteggi totali -- tolta tutta darebbe
   ~1.3. Il guadagno sui PMT amplificati di oggi va misurato.
   Se rende: dati grezzi + SIC nel file, tabella copiata nel file all'avvio,
   correzione applicata da daqio in lettura (reversibile), NON segnali
   corretti scritti nel file (decisione 2026-10-21).

9. Impulsatore lasciato a casa per la trasferta (2026-10-20). Per la PADME
   servono eventi di solo piedistallo: aggiungere una run a trigger
   SOFTWARE (oggi i trigger software si usano solo per il piedistallo
   all'avvio). Per la sincronizzazione delle schede: un segnale sdoppiato,
   o il generatore di impulsi interno del V4718 (Bridge::SetPulser, mai
   provato su queste schede). Il ginocchio della calibrazione della soglia
   fra 1.6 e 47 ns resta da fare al rientro, con l'impulsatore.

10. **Run a trigger software (`SoftwareTrigger`, `SoftwareTriggerRate`)
   scritta il 2026-10-20 a PC DAQ spento: NON COMPILATA.** Le due chiavi
   vanno aggiunte a mano in run-local.toml (con backup): la pagina modifica
   solo chiavi esistenti. Da verificare: rate effettivo contro richiesto
   (log: trigger mandati contro eventi registrati), TriggerMode = "Software"
   nell'header, poi `tools/dcr_scala.py` su una run vera; controllo del
   campione cieco (prima/seconda meta' della finestra, forma media degli
   eventi vuoti senza strutture).

Poi fase 2 (controllore: schede nella pagina, Enabled, start/stop insieme,
numero di run unico, mail per scheda -- prima un PNG da approvare) e fase 3
(monitor: due file per run, schede dei canali col nome della scheda, file di
stato live-status-<scheda>.json).

## Al Gran Sasso (setup trasferito il 2026-10-21) — da fare al primo collegamento

Sottoterra: pochissimi muoni (ordine di 1 per m² all'ora, stima di
letteratura, non misurata qui), molto fondo gamma dalla roccia. Niente
impulsatore. Il debug va costruito su quello che c'e'.

1. **Ritrovare il PC DAQ**: indirizzo nuovo dalla rete LNGS, aggiornare
   l'alias `daq-pc` in `~/.ssh/config` sul Mac e l'indirizzo in CLAUDE.md.
   Controllare che la rete privata 192.168.99.x (bridge .105) sia rimasta
   com'era.
2. **Le mail**: escono da pmg1/pmg2.lnf.infn.it porta 25. Dalla rete LNGS
   la 25 verso Frascati potrebbe essere chiusa. Provare con
   `python3 tools/posta.py --prova <indirizzo LNF>` (parlando SMTP, non
   contando byte: vedi "Le mail della DAQ"). Per run lunghe senza nessuno al
   banco sono l'unico allarme: se non passano, trovare un server di posta
   raggiungibile da LNGS, da decidere con l'utente.
3. **Fase 1 dei due V1742**: compilare (start_index_cell non e' mai stata
   compilata), provare, installare: vedi la sezione "Due V1742".
4. **Run al buio sui PMT** (self-trigger sul singolo fotoelettrone): guadagno
   dal picco dello SPE, verifica della catena PMT -> amplificatore ->
   digitizer, coda di verso opposto e oscillazione lenta di ch24, e la
   valutazione della correzione PADME (sigma_ped CAEN contro CAEN + PADME).
5. **Run sui pannelli EJ200 coi gamma della roccia**: spalle Compton del
   40K (riga 1.46 MeV) e del 208Tl (2.61 MeV) come calibrazione in energia
   dei pannelli.
6. **Trigger per i muoni**: con soglie basse il fondo gamma sui pannelli puo'
   arrivare a kHz e la DAQ satura sopra ~880 Hz. Per cercare i muoni:
   coincidenza/maggioranza (V812, o self-trigger su TRG-OUT con logica
   esterna), non OR a soglia bassa. Run lunghe (notti, giorni).
7. Eventuale sorgente di prova del laboratorio (137Cs, 60Co, 22Na) al posto
   dell'impulsatore per pannelli e coincidenze: da chiedere, regole di
   radioprotezione a cura dell'utente.
