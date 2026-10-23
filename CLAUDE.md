# daq-standalone

DAQ per un CAEN V1742 (digitizer DRS4, 32 canali + 2 TR, 12 bit, 1 Vpp) che
legge fotomoltiplicatori. Scritta in C++ con la libreria CAENDigitizer, scrive
HDF5, con strumenti di analisi in Python sotto `tools/`.

Lo scopo del lavoro in corso: capire se il **self-trigger** del V1742 permette
una selezione ad alta efficienza, in vista di un sistema a ~200 canali che
acquisisce quando almeno uno supera soglia.

---

## Due macchine

Si scrive sul **Mac**, si compila e si prova solo sul **PC DAQ**, che è l'unico
ad avere hardware e librerie CAEN.

- PC DAQ: alias SSH `daq-pc` → `192.168.99.108` (al Gran Sasso, dal
  2026-10-23; a Frascati era .104), utente `daq`, repo in
  `/home/daq/daq-standalone`. Login a chiave. Il Mac ci arriva da una VPN
  (`utun`, 192.168.99.10).
- **Al Gran Sasso, 2026-10-23**: PLH250-P (bias SiPM) a `.104`, CPX400SP
  (amplificatore) a `.106` -- l'indirizzo di SERIE del bias, quindi il
  controllore va avviato con `-- --psu-host 192.168.99.104 --psu-ampli-host
  192.168.99.106`; ora comunque rifiuta un alimentatore del modello
  sbagliato. `.102` e' il controller di gestione (BMC) di un server
  Supermicro, `.107` risponde al ping e basta (sconosciuto). **Il bridge a
  `.105` non risponde al ping** ma risponde su 22/80 e al VME: non darlo per
  morto da un ping. Dal PC e dal Mac la rete non si vede uguale (il Mac via
  VPN vede .5 e .102, il PC no): le prove si fanno dal PC.
- **Dal Gran Sasso la posta non parte**: pmg1/pmg2.lnf.infn.it porta 25
  danno "No route to host" (2026-10-23). Nessuna mail di allarme finche' non
  si trova un'altra strada.
- Bridge VME V4718: `192.168.99.105` (non è il PC DAQ). I digitizer stanno
  sul bus VME dietro di lui, distinti dall'indirizzo di base: vedi "Due V1742".
- **Gli indirizzi sono assegnati da DHCP e cambiano.** Il PC DAQ è passato
  da `.108` a `.104` dopo un riavvio, il 2026-10-05. Se `daq-pc` non
  risponde, prima di dare per rotto qualcosa si cerca dove è finito:
  `for i in $(seq 1 254); do (ping -c1 -W1 192.168.99.$i >/dev/null && echo $i) & done; wait`
  L'alias sta in `~/.ssh/config` sul Mac, non nel repo.
- Sincronizzazione **solo via git** (`CUPID-NMV/daq-standalone`, branch `main`).
  Mai `scp`: fa divergere le copie, è già costato un allineamento manuale.
- Sul Mac **non si può compilare**: mancano libCAENDigitizer e HDF5.

## Compilare, e verificare di averlo fatto

```
build/        e' quella che l'utente lancia
build-swmr/   area di prova, per compilare mentre una run e' in corso
```

**`git pull` prima di `make`.** Il `make` senza pull dice "Built target" senza
ricompilare nulla, e si finisce per credere allineato un binario che non lo è.
È già successo.

**Verificare il contenuto del binario, non il timestamp:**

```bash
strings build/main/DAQ-WC | grep -c "<messaggio nuovo>"
```

Attenzione: `utils/` produce una libreria **condivisa** (`libdaqutils.so`), e le
sue stringhe non stanno nell'eseguibile. Cercarle lì dà zero anche quando la
modifica c'è.

Se una run è in corso non si può riscrivere `build/main/DAQ-WC` (*Text file
busy*): compilare in `build-swmr/` e riallineare `build/` appena la run si
ferma, dicendolo all'utente.

## Processi sulla macchina DAQ

I processi a lunga vita (DAQ, monitor) **li lancia l'utente**, salvo che dica
il contrario. Per le prove usare **porte separate** (es. 8799) e chiudere
sempre quello che si è avviato.

Mai `pkill -f`: un `pkill -f "tools/live_monitor.py"` ha già ucciso insieme la
shell che lo eseguiva e il monitor dell'utente. Trovare il PID e mandare un
segnale a quello.

## Configurazione

`config/template-daq.toml` è versionato. `config/run-local.toml` è **quello che
l'utente usa davvero**, è gitignorato e vive solo sul PC DAQ: `git pull` non lo
aggiorna. Le chiavi nuove vanno aggiunte lì a parte, con un backup, dicendolo.

---

## Cose misurate, da non ri-derivare

- **PMT passati dalla scheda di amplificazione (ch24-25, 1 GS/s): FWHM 7 ns**
  fino a ~100 mV, 8 ns fra 100 e 200, 9 ns sopra: si allargano con
  l'ampiezza (run 0189, 20000 eventi). Non i 1.8 ns dei PMT nudi.
- **Calibrazione del self-trigger per i PMT amplificati: ~0.8 mV per unita'
  di offset fra offset ~10 e 100** (attenuazione ~3.2-3.6), misurata il
  2026-10-18 con `calibra_selftrigger.py` su due scan della run 0199
  (`measurements/calib_selftrigger_20261018_*.json`). I due stimatori danno
  0.88 e 0.78 sullo scan fitto (14-100), cioe' ~3 sigma di distanza: c'e' un
  sistematico del 10-15% non identificato, il numero vale +-15%. **Sopra
  ~150 la relazione si incurva** (0.6-0.7 mV/offset): coerente con gli
  impulsi grandi piu' larghi, che il Transparent Mode attenua meno, ma non
  dimostrato. E' il primo punto misurato fra 1.6 ns (3.08 a 1 GS/s) e
  l'altopiano dei 47 ns (0.42).
- **Il fattore fra offset del self-trigger e soglia del CFD e' 0.2 mV per
  unita' di offset**, ricavato dall'utente sovrapponendo gli scan del V812 e
  del self-trigger. Questione CHIUSA per decisione dell'utente il
  2026-10-16: non riaprirla. Non va confuso con la calibrazione del
  self-trigger all'ingresso del digitizer (0.42 per gli impulsi larghi,
  sotto), che e' un'altra grandezza.
- **La calibrazione della soglia dipende dalla larghezza dell'impulso**, e
  molto: 0.46 mV per unità di offset a 96 ns, **3.93 a 1.6 ns**.
  L'attenuazione del Transparent Mode passa da 1.9 a 16.1. Usare la
  calibrazione degli impulsi larghi sottostima la soglia di un fattore 8.
- **Gli impulsi dei PMT hanno FWHM 1.80 ± 0.02 ns**, misurata a 2.5 GS/s su due
  soglie diverse: il punto di calibrazione applicabile è quello a 1.6 ns, non
  quello a 7.6 ns. Ma sono larghi 3.2 ns al 10% del picco e ~3.6 ns dove si
  staccano dal rumore, ed è quest'ultima la larghezza che si legge a occhio
  all'oscilloscopio: chi dice "5-6 ns" non è in disaccordo, sta guardando
  un'altra altezza. **A 1 GS/s la FWHM cade su 2 soli campioni e la misura non
  è risolta**: va fatta a 2.5 GS/s.
- **Dipende anche dalla frequenza di campionamento**: 3.96 mV/offset a
  2.5 GS/s contro 3.08 a 1 GS/s, il 29% di differenza. Il meccanismo non è
  capito. La risposta è invece **lineare nell'ampiezza**, verificata entro il
  3% a entrambe le frequenze.
- **A 1 GS/s l'ampiezza di un impulso da 1.6 ns è sottostimata del 14%**,
  perché il picco cade fra due campioni. Misurarla a 2.5 GS/s.
- **Il V1742 produce picchi spuri** larghi 1-3 ns e alti ~12.7 mV,
  **simultanei su tutti i canali e con la stessa ampiezza** (rapporto fra
  canali 1.02 ± 0.04). Difetto noto, segnalato a CAEN. Si vetano confrontando
  l'ampiezza fra canali: una coincidenza vera non dà mai rapporto 1.00 ± 4%.
- **La DAQ satura attorno agli 880 Hz** con due canali per evento. Oltre, il
  rate misurato non è quello vero e gli impulsi veri vengono persi.
- **Le correzioni DRS4** valgono un fattore 23 sul rumore registrato (1.5
  contro 34 conteggi). Riguardano i dati registrati, non il trigger: la
  decisione la prende l'hardware sul segnale in Transparent Mode, che non
  attraversa le celle di memoria.
- **Il pavimento di rumore va rimisurato con la sorgente collegata**: il
  generatore contribuiva più della board.
- **Il piedistallo si misura senza trigger esterno.** `MeasureBaseline` manda
  trigger software e legge quello che arriva: con TRG-IN attivo un LED a
  qualche centinaio di hertz riempie il buffer di eventi veri. Misurato: media
  380.7 conteggi e rms 183.5 con gli eventi LED dentro, 270.6 e 8.9 sulle sole
  istantanee pulite. La DAQ si rifiutava di partire con "rms = 176.65", e
  l'rms all'avvio ballava fra 6 e 177 da una run all'altra. Ora il trigger
  esterno si spegne per la durata della misura e si rimette su ogni uscita.
- **La mediana dell'intera traccia non è il piedistallo, se l'impulso è
  lungo.** Vale per i PMT (pochi ns su mille campioni), non per un SiPM col
  LED che ne occupa 400 su 1321: misurato 275.0 conteggi sulla traccia intera
  contro 271.0 sul solo pre-impulso e 272.0 sulla coda. Quei 4 conteggi
  integrati su 992 campioni valgono 26 pC su una carica di 21: l'errore era
  più grande del segnale. Il monitor lo prende da una finestra dichiarata
  (10-180 ns di serie), evento per evento.
- **La prova che una sottrazione di piedistallo è giusta è che l'integrale
  SATURA** allargando il cancello: 37.5, 41.5, 44.0, 45.1, 45.1 pC. Senza
  correzione calava — 32.0, 33.8, 33.3, 30.6, 24.5 — che per un impulso
  positivo è impossibile. Il piedistallo va preso PRIMA del segnale e non
  "fuori dal cancello": dopo l'impulso la coda contamina la stima del 6%.
- **La calibrazione della soglia per impulsi LARGHI e POSITIVI, cioe' quelli
  dei SiPM, vale 0.436 mV per unita' di offset** (attenuazione 1.79),
  misurata il 2026-10-09 a 750 MS/s su impulsi da 189.34 mV e 189 ns FWHM:
  d50 = 434.4 unita', turn-off strettissimo (sigma 0.24 mV). Il 2.7 della
  tabella di `plot_scan` vale per impulsi da 1.8 ns dei PMT e per i SiPM
  **sovrastima la soglia di sei volte** -- il sintomo era un asse che arrivava
  a 2400 mV su un ingresso da 1 Vpp.
- **A 46.7 ns la calibrazione vale 0.42 mV per unita' di offset**
  (attenuazione 1.7), misurata il 2026-10-13 a 750 MS/s sugli impulsi al buio
  dei SiPM. Misurata **senza generatore e senza CFD**: la scala in millivolt
  ce l'ha gia' il digitizer, che registra le forme d'onda degli eventi che ha
  triggerato. Due stimatori che non condividono ipotesi -- mappatura dei
  quantili fra i rate dei passi, e bordo basso della distribuzione di ampiezze
  a ogni passo -- danno 0.4252 +- 0.0080 e 0.4166 +- 0.0033, cioe' 1.0 sigma.
  Lo fa `tools/calibra_selftrigger.py` su un JSON di `noise_scan.py`.
- **La dipendenza dalla larghezza e' PIATTA fra 47 e 189 ns**: 0.42 a 46.7 ns,
  0.46 a 96, 0.436 a 189. Il fattore otto compare solo scendendo a 1.6 ns.
  Il modello giusto non e' "cresce al calare della larghezza" ma "c'e' un
  altopiano a ~1.8 di attenuazione per gli impulsi larghi, e un ginocchio
  sotto il quale l'attenuazione sale". **Dove sia il ginocchio non e'
  misurato**: sta fra 1.6 e 47 ns. Interpolare in legge di potenza fra i punti
  a 1.6 e 96 ns da' 0.66 a 47 ns, cioe' il 57% in piu' del vero: non si fa.
- **La tabella `MV_PER_OFFSET` e' indicizzata sulla sola frequenza di
  campionamento, e quella chiave non basta.** Quello che conta davvero e' la
  **larghezza**: un fattore 8 fra 1.6 e 96 ns, contro il 25% fra 2.5 e
  1 GS/s. I punti misurati sono due larghezze, non tre frequenze: 1.6 ns
  (negativi, PMT) e 46-189 ns (il punto a 96 ns non registra nemmeno a che
  frequenza fu preso). Per gli impulsi al buio dei SiPM, **20 ns**, non c'e'
  nessun punto applicabile.
- **Gli impulsi dei SiPM hanno due larghezze a seconda di cosa li produce**:
  col LED **156-165 ns** FWHM, al buio in self-trigger **~9 ns**. Misurati su
  ch16 a 750 MS/s. In tutti e due i casi si e' lontanissimi dagli 1.80 ns dei
  PMT, ed e' per questo che la calibrazione dei PMT non si applica.
- **La larghezza si misura EVENTO PER EVENTO, non sulla forma d'onda media.**
  Gli impulsi al buio di un SiPM non sono allineati nel tempo: il picco della
  mediana vale 20 conteggi contro i 48 di ampiezza tipica per evento, cioe' la
  media li impasta e ne allarga la forma. Misurata cosi', la larghezza ballava
  fra 9 e 61 ns a seconda di quanti eventi si guardavano -- i "20 ns" scritti
  qui il 2026-10-09 venivano da li'. Misurata attorno al massimo di ogni
  singolo evento vale 9 ns e non si muove piu'. Sugli impulsi allineati (LED,
  generatore) i due metodi concordano entro il 5%.
  **I 9 ns NON tengono sulle run al buio del 2026-10-06** (ch16, 750 MS/s,
  52-56.5 V), rimisurate il 2026-10-14: FWHM per evento 83-104 ns sul segnale
  filtrato a 50 MHz, salita ~30 ns, discesa di centinaia di ns. Il segnale
  grezzo porta un'oscillazione a ~98 MHz di +-10 conteggi, presente anche prima
  dell'impulso, i cui massimi sono larghi pochi ns: il sospetto e' che i 9 ns
  misurassero quella. Non verificato rifacendo quella misura. Il punto di
  calibrazione a 46.7 ns del 2026-10-13 va nella stessa direzione.
- **Il rate del generatore si prende dal plateau dello scan, non dallo
  scaler.** Il 2026-10-09 lo scaler dava 88 Hz, il plateau 91.6, e durante la
  messa a punto la DAQ ne ha visti 12.9 per un'ora: era il sync che precedeva
  l'impulso di 75 us, cinquantacinque finestre di acquisizione, quindi
  l'impulso non poteva essere dentro nessuna. Al plateau e' la DAQ stessa a
  contare, con la soglia ben sotto l'impulso.
- **Il self-trigger con offset 3 non registra fedelmente gli impulsi da 1 p.e.
  di un SiPM.** Run al buio del 2026-10-06 (S13360-6075PE su ch16, 52-56.5 V,
  PostTrigger 40), analizzate il 2026-10-14 in
  `tools/SiPM_Characterization_DC_Study.ipynb`:
  - nell'impulso che ha fatto scattare il trigger manca circa il 63% degli
    1 p.e. rispetto alla Borel col crosstalk del LED, e il deficit cresce con
    la tensione (a 56.5 V gli eventi da >=2 p.e. sono 3.6 volte quelli da 1,
    il crosstalk ne giustifica 0.5);
  - gli impulsi a caso PRIMA del trigger non hanno il deficit;
  - nei ~120 ns prima dell'impulso del trigger non c'e' mai un impulso da
    >=2 p.e., mentre gli 1 p.e. ci sono al rate normale: la scheda era gia'
    pronta e un 2 p.e. l'avrebbe fatta scattare. **Soglia effettiva fra 1 e
    2 p.e.**, contro quella attesa da 0.42-0.44 mV/offset (~1.3 mV, cioe'
    ~0.45 p.e. a 54 V e ~0.2 a 56.5 V): perche' non torni non e' capito;
  - esclusi come causa il rumore (i trigger sul rumore lasciano il gate vuoto,
    7-28% degli eventi, e non spostano 1 p.e. in 2) e gli impulsi a caso
    aggiunti nel gate (danno un crosstalk sbagliato).
  Q_pe e crosstalk misurati al buio coincidono col LED (entro 4% e 2% fino a
  55.5 V), V_bd entro 1.2 sigma: la distanza fra i picchi non risente della
  scelta del trigger, i pesi dei picchi si'.
- **Il DCR si misura con una run a TRIGGER SOFTWARE** (`SoftwareTrigger =
  true`, finestre a intervalli casuali, altre sorgenti spente) e
  `tools/dcr_scala.py`: la scala in soglia si fa offline su una sola run, e
  la scala in p.e. la danno i gradini stessi. Validato il 2026-10-20 su dati
  SIMULATI (750 MS/s, 40 conteggi/p.e., crosstalk 0.20): a 100 kHz DCR
  95.9/20.1/4.0 kHz contro 100/20/4 veri, crosstalk 0.209; a 1 MHz la stima
  di Poisson da' 979 kHz a 0.5 p.e., ma a 1.5 p.e. 262 contro 200: il
  pile-up di due 1 p.e. supera la soglia, e il crosstalk ne esce gonfiato
  (0.27). Senza isteresi la coda lenta degli impulsi, riattraversando la
  soglia col rumore, gonfiava il conteggio del 10%. Mai provato su dati veri.
- **Il DCR non si misura dalle run in self-trigger**, nemmeno contando gli
  impulsi nelle tracce: prima del trigger mancano quelli che lo avrebbero fatto
  scattare, dopo pesa la coda dell'impulso del trigger. Il rate della run e'
  quello della DAQ satura (~975 Hz a ogni tensione). Serve un trigger che non
  dipenda dal segnale: esterno, o le run dei pannelli col trigger dei muoni.
- **`TriggerPolarity`, `DCOffset` e il segno dell'impulso sono una cosa
  sola.** Piedistallo in basso (DCOffset alto) significa impulsi positivi e
  quindi fronte di salita. Sbagliare uno dei tre non dà errori: la soglia si
  posa dove il segnale non va mai e la run acquisisce zero eventi. Costato una
  run di 52 s a vuoto passando dai PMT al SiPM.

## Il rate di uno scan non deve contare la compressione

Fino al 2026-10-12 `scan_v812.sh` misurava la durata di ogni punto come
"istante dopo la morte del processo meno StartTime", e dentro ci finivano
chiusura e **compressione** del file, che dura quanto il file e' grosso.
Misurato: la DAQ acquisiva **57.3 s su ogni punto**, il conto ne dava 57, 58,
61 e **68**. Il punto da 68 era quello a rate piu' alto -- file piu' grande,
compressione piu' lunga -- e usciva a **449 Hz invece di 533**.

Il sintomo e' una curva con la **gobba iniziale**: il primo punto sembra avere
meno rate del secondo, che per uno scan in soglia e' impossibile. Se lo vedi,
e' questo.

Ora la durata la dichiara la DAQ (`Acquisition time`). Gli scan vecchi si
riparano con `tools/scan_ricalcola.py` finche' i `.log` delle run sono sul
disco; i punti chiusi dal timeout non dichiarano il tempo e prendono la
mediana degli altri, marcati `durata_stimata`.

Gli scan del **self-trigger non hanno il difetto**: li' il rate viene dai
confini in eventi dentro una sola run, senza compressioni in mezzo.

## Il conteggio per canale non e' il rate del canale

E' il rate con cui quel canale supera la soglia **fra gli eventi che qualcun
altro ha fatto registrare**, e il campione cambia con la soglia. Misurato il
2026-10-12 con il CFD in OR su ch16, 17, 18: a 5 mV **ch18 era sopra soglia in
tutti e 30542 gli eventi** e ch16 solo in 3220; a 10 mV ch16 saliva a 12230.
Il conteggio CRESCE alzando la soglia, e non e' un baco.

Il motivo: a soglia bassa triggera quasi solo il canale piu' rumoroso -- ch18
ha rms 3.8 mV contro gli 0.8 di ch16 -- e gli eventi registrati sono i suoi
lampi di rumore, dove gli altri non hanno niente. Per uno scan per canale
pulito va tolto dall'OR il canale rumoroso, o alzata la sua soglia.

## La maggioranza del V812 si verifica in 30 secondi

**Se la maggioranza non agisce, il colpevole e' il cavo, non il codice.** Il
2026-10-12 quattro run da 30 s con gli stessi segnali e le stesse soglie hanno
dato lo stesso rate per `Majority` = 1, 2, 3 e **20**: 4643, 4780, 4815, 4905
eventi. Chiedere una coincidenza a venti canali su tre deve dare ZERO; dava
tutto. Il modulo faceva l'OR, perche' TRG-IN non era sull'uscita OR/MAJ del
V812 -- qualcuno aveva spostato un cavo.

**La prova**: una run di 30 s con `Majority = 20`. Se il rate non cambia, il
trigger non viene dall'uscita di maggioranza e non c'e' niente da cercare nel
software.

Cosa NON e' il problema, verificato quel giorno: gli indirizzi dei registri
sono quelli del manuale (soglie `0x00+2n`, maggioranza `0x48`, inibizione
`0x4A`), la formula e' quella del §3.7 (`MAJTHR = NINT[(MAJLEV*50-25)/4]`), e
il modulo risponde -- si fa riconoscere dal codice fisso `0xFAF5` e **alle
soglie obbedisce**, come mostra qualunque scan in soglia.

Corollario generale: **prima di dare la colpa al software, guardare cosa e'
cambiato al banco.** Qui lavorano piu' persone sulla stessa catena.

---

## Le correzioni DRS4 non si accendono. Mai.

> **Attenzione, verificato il 2026-10-20: il codice NON fa piu' quello che
> dice questa sezione.** Il commit `f18a2a0` del 2026-09-28
> ("DRS4Correction: opzione esplicita nel TOML, default attiva") le ha
> riaccese apposta -- senza correzione il rumore registrato era 34 conteggi
> contro 1.5 -- e `run-local.toml` ha `DRS4Correction = true`: tutte le run
> recenti hanno nel log "DRS4 corrections ENABLED". Restano spente solo
> durante la misura del piedistallo in Transparent Mode. Quale delle due sia
> la decisione valida lo deve dire l'utente; fino ad allora, NON cambiare
> nessuna delle due cose. I sigma da 1.5 conteggi della tabella
> dell'efficienza sono misurati con le correzioni accese.

Decisione esplicita, presa il 2026-09-25: `CAEN_DGTZ_EnableDRS4Correction` non
va applicata in questo progetto, da nessuna parte.

**Perche'.** Le tabelle correggono le differenze fra le celle di memoria del
DRS4. Il comparatore del self-trigger lavora in **Transparent Mode**, dove il
segnale raggiunge l'ADC senza attraversare quelle celle: le correzioni non
possono cambiare la decisione del trigger, ma rendono la traccia registrata
diversa dal segnale su cui quella decisione e' stata presa. In piu' sono
calibrate sull'Output Mode, e applicate ai dati in Transparent Mode
**gonfiano l'RMS di circa 40 volte** -- 0.7 conteggi diventano ~28 -- e
quell'RMS e' esattamente il numero con cui si sceglie la soglia. Le parole
dell'utente: *se il segnale diretto ha delle oscillazioni pazienza, ma almeno
non aggiungiamocene noi.*

- In `src/Digitizer.cpp` sono disabilitate esplicitamente subito dopo
  `LoadDRS4CorrectionData`. Il caricamento resta solo perche' la stessa
  chiamata porta con se' la calibrazione del PLL.
- Si erano riaccese **di rimbalzo** dentro il ripristino che seguiva la misura
  del piedistallo in Transparent Mode, senza che nessuno l'avesse scelto. E'
  il modo in cui torneranno, se tornano: dentro un ripristino.
- **Spartiacque: fino alla run 0104 compresa le correzioni erano ATTIVE, dalla
  0105 sono spente.** Non confrontare cifre di rumore a cavallo di quel
  cambio. La 0104 aveva 1.48 conteggi di rumore con 0.68 di struttura residua
  agganciata all'indice di lettura; senza correzione quella struttura e' piu'
  grande.

---

## Il nome dei file e la tensione dei SiPM

Dal 2026-10-12 il prefisso dei file porta la tensione misurata
dell'alimentatore: `test_54-0V_0000_750Ms_40PT.h5`. Ce la mette il
**controllore** quando avvia una run, non il C++, che dell'alimentatore non
sa niente. La regola sta in `tools/nomi.py`, una sola implementazione.

Conseguenze da sapere:

- **La numerazione riparte per ogni tensione**: il numero di run lo calcola
  il C++ contando i file con quel prefisso.
- **Il prefisso resta scritto nel TOML** dopo la run. E' voluto -- si vede
  nella pagina cosa si sta scrivendo -- ma vuol dire che **chi avvia la DAQ
  senza passare dal controllore si porta dietro l'etichetta della run
  precedente**, cioe' una tensione vecchia in un nome che sembra autorevole.
- Per questo `tools/scan_v812.sh`, che lancia una DAQ per punto per conto
  suo, chiama prima `tools/prefisso_hv.py`: chiede la tensione al
  controllore e, se non risponde, **toglie** l'etichetta invece di lasciare
  quella vecchia. Stessa cosa va fatta per qualunque avvio a mano dal
  terminale.
- Lo scan del self-trigger non ha questo problema: non avvia niente, cambia
  le soglie su una run gia' in corso, quindi il file e' quello della run.

---

## Le run degli scan stanno in `data/scan/`

Dal 2026-10-13 `scan_v812.sh` scrive le sue run in `<OutputDir>/scan`: sono
decine di file che non contengono fisica -- servono a misurare un rate contro
una soglia -- e mescolate ai cosmici rendono impossibile portare via "solo i
dati" senza guardare un file per volta. La cartella la impone lo script
riscrivendo `OutputDir` nel TOML, e il `trap` che c'era gia' la rimette come
prima a fine scan.

- **L'e-log salta `data/scan/`** (`tools/elog.py`, si riprende con
  `--anche-scan`): cinquanta punti di uno scan sono UNA misura, e lasciati
  entrare spingono fuori dalle "ultime 10" tutte le run della serata.
- **Lo scan del self-trigger non puo' farlo**: la run la apre l'utente prima,
  e lo script muove solo le soglie. Se la si vuole separata, si cambia
  `OutputDir` *prima* di far partire la run; lo script lo ricorda a video
  quando vede che la run non e' in una cartella di scan.
- `tools/scan_ricalcola.py` cerca i `.log` anche nelle sottocartelle, altrimenti
  su uno scan nuovo direbbe "gia' a posto" senza aver letto niente.

---

## Le mail della DAQ

Dal 2026-10-14 il controllore manda tre tipi di mail, con una lista di persone
per tipo, gestita dal riquadro *Notifications* della pagina e salvata in
`data/notifiche.json` (sul PC DAQ, fuori da git): il **riepilogo** della
giornata col grafico del rate, all'ora scelta; le **run finite male**; i
guasti dell'**hardware**.

- **Si scrive solo a indirizzi @lnf.infn.it, per scelta.** La posta esce dai
  server di posta in ingresso dei Laboratori, `pmg1`/`pmg2` porta 25, senza
  login, che consegnano solo alle caselle LNF ("Relay access denied" per il
  resto, con un 454 che sembra temporaneo e non lo e'). Chi vuole le mail
  altrove se le inoltra con una regola della sua casella: l'utente fa cosi'.
- **Il server di invio `smtp.lnf.infn.it` invece risponde** dal PC DAQ
  (25/587/465, login dopo STARTTLS), e con delle credenziali scriverebbe a
  chiunque. Non si usa per decisione dell'utente (2026-10-14): vorrebbe dire
  una password personale in chiaro sulla macchina condivisa. L'avevo dato per
  chiuso con una prova che aspettava 120 byte di benvenuto da un server che ne
  manda una cinquantina: **le porte si provano parlando SMTP, non contando
  byte.**
- **Greylisting**: il primo invio a una coppia mittente/destinatario nuova
  prende "450 try later" e passa dopo ~5 minuti. `posta.invia` riprova da sola
  per un quarto d'ora. Una volta accettata, la posta arriva in pochi secondi.
- **Una run finita male si riconosce dal log, non dal codice di uscita.** Se
  il collegamento col digitizer cade, il C++ scrive "giving up", chiude il
  file col riepilogo normale ed esce con 0. Le regole stanno in
  `notifiche.esito_run`; provate sulle 320 run di `data/` al 2026-10-14: 185
  fermate a richiesta, 66 arrivate a NEvents, 61 senza riepilogo (quasi tutti
  punti di scan chiusi dal timeout) e **8 col collegamento perso**, di cui
  nessuno era stato avvisato.
- **I punti degli scan non si giudicano**: finiscono col timeout per
  costruzione, e sarebbero un allarme al minuto.
- Il log della run il controllore lo trova fra i file aperti dal processo
  (`/proc/<pid>/fd`), quindi vale anche per le run lanciate da terminale.
  L'uscita della console si copia nella mail **subito**: si sovrascrive alla
  run dopo, ed e' l'unico posto dove resta il motivo di una DAQ morta prima
  di aprire il log (un VME che non risponde all'avvio).
- **Alimentatori**: l'allarme principale e' l'uscita che si spegne senza che
  nessuno abbia premuto *Output OFF* nella pagina -- anche uno spegnimento da
  `psu_control.py off` da terminale lo fa scattare, ed e' giusto cosi'. In
  piu' i bit di scatto del registro `LSR1?` (2 = OVP, 3 = OCP, dal manuale,
  **non verificati con uno scatto vero**), il limite di corrente (bit 1, solo
  se la corrente letta e' davvero al 95% del limite: il bit scatta anche a
  ogni accensione) e un alimentatore muto per piu' di un minuto.
- **`LSR1?` si azzera a ogni lettura** e ora lo legge il controllore ogni 3 s:
  `psu_control.py status` da terminale non mostra piu' gli scatti gia' visti.
- **Il rate nel tempo** lo annota il controllore in `data/rate-log.csv`,
  ogni 15 s, contando gli eventi del file HDF5 in SWMR (o, se il file non si
  apre, dal contatore stampato dalla DAQ). Il riepilogo ne fa una curva a
  intervalli di 2 minuti sopra la media di ogni run. Il monitor disegna la
  stessa curva ma la tiene in memoria: non serve per una giornata intera.
- Un **controllore di prova** su un'altra porta va lanciato con
  `--notifiche /tmp/qualcosa.json`, se no manda le mail vere a tutta la lista.
  Le sue azioni finiscono comunque in `data/azioni.jsonl`, che e' unico.

## PMT e SiPM sulla stessa scheda

Dal 2026-10-16: SiPM su ch16-18 (gruppo 2), PMT su ch24-25 (gruppo 3), questi ultimi passati dalla scheda di amplificazione. Il
gruppo 0 (ch0-7) ha problemi e non si usa. Stesso trigger per tutti.

- **Frequenza 1 GS/s**, una sola per scheda: 2.5 GS/s taglierebbe la coda dei
  SiPM (finestra 410 ns), 750 MS/s e' troppo poco per i PMT. A 1 GS/s
  l'ampiezza dei PMT e' sottostimata del 14% e la FWHM non e' risolta; la
  carica si'.
- **PostTrigger 60%**, scelto dall'utente il 2026-10-16: SiPM a ~290 ns
  (sopra il 10% da ~220 a ~620), PMT a ~154 ns (136-184). A 70% i PMT cadono
  a ~50 ns e prima non resta piedistallo: la stima dell'efficienza li
  dichiara non stimabili. Il ritardo dei PMT rispetto ai SiPM e' ~135 ns.
- **Configurazione provvisoria**: in futuro PMT e SiPM staranno su
  digitizer diversi. Le cose fatte per la scheda mista (verso, cancello,
  piedistallo e finestre di efficienza per canale) restano valide anche con
  un tipo di rivelatore per scheda.
- **DCOffset per canale**, lista parallela a ChannelList: **0xB000 i SiPM,
  0x7000 i PMT**. La tabella del template (0xC000 -> piedistallo ~268) e'
  misurata su un canale VUOTO e per i SiPM amplificati non vale: l'uscita
  della scheda di amplificazione porta il piedistallo ~450 conteggi (~110 mV)
  piu' in basso. Misurato il 2026-10-16: a 0xC000 ch16-18 stavano a 0-8
  conteggi, tagliati dal fondo (run 0182, da non usare per i SiPM), mentre
  ch19-20 vuoti stavano a 293; a 0x8000 i SiPM stavano a ~2170. La risposta
  e' lineare, ~0.142 conteggi per unita' di DCOffset: 0xB000 da' ~420. I PMT
  a 0x7000 stanno a 3207 (ch24) e 2788 (ch25).
- **Nella pagina di controllo il DCOffset e' una colonna della tabella dei
  canali**, accanto al verso che ne segue e al piedistallo misurato
  nell'ultima run (dal file vivo, o dalla stima di fine run in
  `data/efficienza/`). La pagina lo scrive gia' in parallelo a ChannelList:
  uno scalare se tutti uguali, se no una lista. Sotto la tabella, "set DC
  offset ... on channels 16-18, 24" per piu' canali insieme.
- **Il verso dell'impulso e' per canale, e lo dice il DCOffset**: sopra
  0x9000 positivi, sotto 0x8000 negativi (`efficienza.verso_da_dcoffset`).
  **In mezzo non dice niente** -- il piedistallo e' a meta' dinamica -- e
  decide TriggerPolarity, non PulsePolarity: il C++ legge PulsePolarity ma
  non la usa, e nelle run dei PMT di settembre vale 0 (positivi) con impulsi
  negativi. Monitor ed efficienza lo leggono dal log della run.
  Anche `run_check.py` (dal 2026-10-16).

- **Nel monitor, cancello di carica e piedistallo sono AUTOMATICI e per
  canale** (decisione dell'utente, 2026-10-16, che supera per questo caso la
  regola "la finestra si dichiara"). Una volta per run, sui primi 200 eventi:
  la forma d'onda media degli eventi con impulso dice dove l'impulso supera il
  10% del picco; il cancello va da 10 ns prima a 20 ns dopo, il piedistallo
  finisce 20 ns prima. Il segno segue il verso del canale: tutte le
  distribuzioni di carica stanno sull'asse positivo, come si rappresentano gli
  ADC. Un cancello scritto a mano per il canale vince; quello comune resta per
  i canali senza impulsi. Misurato a PostTrigger 60%: SiPM 208-632 ns, PMT
  126-203 ns.
- **I PMT passati dalla scheda di amplificazione hanno una coda di verso
  opposto** lunga ~800 ns dopo l'impulso (~+4 conteggi), che ne restituisce
  quasi tutta l'area: -19 pC nell'impulso, +16 nella coda (run 0186). Firma di
  un accoppiamento in alternata. Un cancello che contiene la coda da' una
  carica vicina a zero o del segno sbagliato: e' quello che succedeva col
  cancello comune 190-1000 ns.

## Due V1742 (dal 2026-10-20)

Nello stesso crate, dietro lo stesso bridge V4718:

| scheda | seriale | base VME | rivelatori |
|---|---|---|---|
| `sipm` | 51639 | `0x32100000` | SiPM |
| `pmt` | 202 | `0x11110000` (commutatori 1111) | PMT |

- **Un indirizzo che non risponde si trova in 20 secondi**: leggere un
  registro della scheda (`base + 0x8140`, A32, D32, con `CAENVME_ReadCycle`
  su `cvETH_V4718` = 27) per tutte le 65536 posizioni dei commutatori. Cosi'
  e' venuto fuori che con i commutatori su F00A la scheda rispondeva a
  `0xD00A0000`: il bit 29 si leggeva 0. Forse un contatto del primo
  commutatore, non verificato; con 1111 il problema non si pone.
- **Il bridge accetta due programmi insieme**, uno per scheda: 16k e 22k
  letture di registro in 8 s in contemporanea, zero errori. Due letture di
  DATI insieme non sono ancora provate.
- **Un processo DAQ per scheda, un solo TOML** (scelta dell'utente: le due
  schede hanno frequenza, PostTrigger e polarita' diverse). Ogni scheda ha
  una sezione `[boards.<nome>]` con le sole chiavi che la distinguono, che
  `--board <nome>` fonde sopra `[digitizer]`; `Enabled = false` la esclude
  (richiesta dell'utente: poterne spegnere una); `BoardSerial` facoltativo
  blocca una scheda sbagliata a quell'indirizzo. `--run N` impone il numero
  di run, che con piu' schede deve darlo il controllore: contando i file
  ognuna lo calcolerebbe a seconda di chi ha gia' creato il suo. Il nome
  porta la scheda DOPO il numero: `test_0201_pmt_1Gs_60PT.h5`. File di stato
  e di soglie per scheda (`live-status-<scheda>.json`); il V812 lo configura
  solo la scheda scritta in `[cfd] Board`. Senza `--board` tutto come prima.
  Esempio commentato in fondo a `config/template-daq.toml`.
- **Formato v3**: per ogni evento `/events/event_counter`,
  `/events/trigger_time_tag` e `/events/start_index_cell` (la cella fisica
  del DRS4 da cui parte la lettura, una colonna per gruppo, 0xFFFF se il
  gruppo manca), riga per riga con le forme d'onda, e
  `Board`/`BoardSerial`/`VMEBaseAddress` nell'header. Senza questi due numeri
  gli eventi di due schede non si abbinano nemmeno offline. **Il Trigger
  Time Tag conta a ~17.07 ns, non agli 8.5 ns del datasheet**: misurato il
  2026-10-23 su 5461 trigger software in 27.84 s (intervallo medio 298755
  conteggi = 5.098 ms). Con 30 bit fa un giro ogni ~18 s.
- **Stato al 2026-10-20**: fase 1 (C++) committata e provata sulla scheda
  SiPM, **non installata in `build/`**. La scheda PMT risponde ma non
  acquisisce (reset in timeout, buffer non allocabile; registri: clock
  esterno, PLL sganciato, scheda non pronta): sospetto il commutatore
  meccanico della sorgente di clock. Il trigger delle due schede (comune o
  separato) non e' deciso; la sincronizzazione (clock CLK-OUT -> CLK-IN,
  partenza TRG-OUT -> S-IN) e' riassunta in `TODO.md` con le fonti. Prove
  rimaste, fase 2 (controllore) e fase 3 (monitor) sono in `TODO.md`.

## Prima prova al Gran Sasso (2026-10-23)

Programma nuovo (schede, trigger software, MIDAS) compilato e INSTALLATO in
`build/` il 2026-10-23, copia del precedente in
`build/main/DAQ-WC.rollback-20261023-1456` (e la libreria accanto). Provato
solo SENZA `--board`, sulla scheda dei SiPM: 30 s a trigger software 200 Hz.

- **5461 trigger mandati, 5461 eventi**, 196 Hz misurati. Intervalli meno
  dispersi di un'esponenziale (cv 0.84 contro 1): la lettura toglie gli
  intervalli piu' corti. Per finestre cieche non conta.
- **MIDAS identico all'HDF5**: 5461 eventi, zero campioni diversi. Ma pesa
  ~20 kB per evento contro ~3.7 dell'HDF5 compresso, perche' la scheda legge
  tutti e quattro i gruppi e nel .mid finiscono 32 canali veri.
- **Le SIC dei quattro gruppi NON coincidono**: differiscono dal gruppo 0 di
  -2..+3 celle (uguali in tutti e quattro solo in 491 eventi su 5461). Una
  SIC sola per scheda, come nel formato di CYGNO, sbaglia di qualche cella
  la correzione per cella sugli altri gruppi: usare `DSIC` /
  `start_index_cell`. Forse vale anche per i dati del test beam (quale SIC
  scrive CYGNO non lo so).
- **Col trigger software a 750 MS/s la SIC prende solo ~40 posizioni** (piu'
  la vicina), a passi di ~25.6 celle = 34.1 ns = 2 x 17.07 ns: il trigger e'
  agganciato a un orologio della scheda, e il giro dell'anello (1024 x
  1.333 ns = 1365 ns) ne contiene 40. Interpretazione, non verificata. Per il
  DCR non conta; per la calibrazione PADME si', perche' ogni cella viene vista
  solo in ~80 posizioni della traccia, e una struttura legata all'indice di
  lettura non si separa bene da quella legata alla cella.

- **Run di prova** (`SaveData = false`, chiesta dall'utente il 2026-10-23
  per non perdere tempo nella compressione): il file si scrive comunque,
  perche' il monitor lo legge in diretta, ma si chiama `TEST_...` (niente
  prefisso con la tensione, niente numeri di run consumati) e a fine run
  .h5 e .mid.gz si cancellano senza comprimerli. Resta il `.log`. La pagina
  avvisa in rosso; riepilogo ed e-log saltano le `TEST_`. Provata e
  installata il 2026-10-23.

## Formato MIDAS (dal 2026-10-21; installato il 2026-10-23)

Decisione dell'utente: i dati devono uscire in MIDAS, il formato della DAQ di
CYGNO, per usarli con gli strumenti del test beam (`wclib.py`,
`WC_analisys_tool.ipynb`). Prima INSIEME all'HDF5 (`OutputFormat = "both"`),
poi, quando monitor, controllore e strumenti sapranno leggere MIDAS, da solo.
I dati presi prima non si convertono.

- **Il formato e' copiato byte per byte da `run01339.mid.gz`** del test beam:
  BOR/EOR (0x8000/0x8001) col dump dell'ODB in JSON, eventi con ID 1, blocchi
  a 32 bit (flag 0x11); `DIG0` uint16 sempre 32 canali x campioni, `DGH0`
  uint32 con schede, modello 1742, campioni, canali, risoluzione, **passo di
  campionamento in ps** (il "400" del test beam = 2.5 GS/s), 32 DC offset,
  TTT e UNA sola SIC per scheda. Nostri in piu': `DSIC` (le 4 SIC), `DCNT`,
  `DCHL`. Nell'ODB c'e' il TOML intero come testo.
- `src/MidasWriter.h` non dipende dalla libreria MIDAS, e comprime mentre
  scrive (zlib, livello 1). **Provato sul Mac** con un programma di prova:
  100 eventi riletti da `midas.file_reader` e `wclib.py` senza un errore.
  **L'integrazione nella DAQ non e' compilata** (PC DAQ in trasloco).
- **Una SIC per scheda nel formato di CYGNO contro quattro DRS4**: se le
  quattro SIC di un evento coincidono non si perde niente; e' da verificare
  sui dati (`/events/start_index_cell` o `DSIC`).
- **Da misurare**: il costo della compressione nel ciclo di acquisizione
  (~64 kB per evento) sul rate massimo della DAQ, `HDF5` contro `both`.

## La stima dell'efficienza nel riepilogo

Una stima, non un'analisi: le analisi si fanno offline. `tools/efficienza.py`,
per canale, sulle forme d'onda **filtrate a 50 MHz** (lo stesso passa-basso
del notebook `SiPM_Characterization_DC_Study`): eventi con l'impulso sopra
4 sigma_ped, cercato in +-50 ns attorno all'istante del trigger, diviso
eventi totali. Misurato il 2026-10-15 su run intere (SiPM su ch16-18, 52 V):

| | ch16 | ch17 | ch18 |
|---|---|---|---|
| sigma_ped grezza / filtrata [conteggi] | 4.7 / 1.5 | 4.1 / 1.5 | 12.6 / 1.5 |
| 0062, CFD in maggioranza 2 (cavo giusto) | 94.5% | 99.7% | 93.8% |
| idem, sui soli eventi con gli altri due > 30 mV (`eff_tag`) | 94.0% | 99.6% | 93.3% |
| 0160, self-trigger OR a offset 300 | 88.5% | 85.7% | 82.0% |
| idem, `eff_tag` | 92.7% | 99.5% | 91.4% |

- **Sotto i 50 MHz i tre canali hanno lo stesso rumore**, 1.5 conteggi
  (0.37 mV), confermato dall'analisi offline sulle 0057-0066 (1.51, 1.50,
  1.56). Il resto e' pick-up sopra i 50 MHz (righe a ~98 e ~188 MHz): su ch18
  e' quasi tutto il rumore. Il comparatore del self-trigger vede il segnale
  NON filtrato, quindi per il rumore al trigger conta la sigma grezza.
- **La frazione semplice dipende da com'e' fatto il trigger**: in OR entrano
  eventi con un canale solo, e gli altri due risultano "inefficienti". La
  frazione sui tag (`eff_tag`) no, e le due serie concordano entro 2%.
  Stabile entro lo 0.3% su cinque ore. Comprende la geometria: non e'
  l'efficienza intrinseca dei rivelatori.
- **sigma_ped non con la MAD**: su campioni interi la MAD e' intera e
  sigma_ped saltava fra 2.97 e 4.45 conteggi da una run all'altra; con essa il
  "pavimento" estrapolato ballava fra 2% e 96%. Ora deviazione standard con
  esclusione a 3.5 sigma, e il fondo si MISURA in una finestra di solo
  piedistallo larga quanto la ricerca: 0.3-0.6%.
- **Le run con l'impulso prima di 180 ns non sono stimabili** (0155-0156,
  self-trigger a PostTrigger 70%, impulso a 115 ns): la finestra del
  piedistallo si restringe, ma il fondo esce quanto l'efficienza (~79%).
  Il grafico lo mostra da se': linea punteggiata sulla piena.
- **Le run 0163-0167 del 2026-10-14 sono in OR, non in maggioranza 2**, anche
  se il log dice maggioranza 2: il cavo di TRG-IN era sull'uscita OR. Lo
  dicono i dati: il 27% degli eventi ha un solo canale sopra 30 mV, contro
  1 evento su 200 000 nelle 0057-0066. **Questo conteggio e' la verifica
  della maggioranza su una run gia' presa**, senza rifarne una.

## Strumenti

| | |
|---|---|
| `tools/run_check.py` | riepilogo di una run: ampiezza, larghezza, purezza, rumore, deriva, rate |
| `tools/noise_scan.py` | scan in soglia a run in corso; scrive i confini in eventi di ogni passo |
| `tools/live_monitor.py` | monitor HTTP; `-b 0.0.0.0` per evitare inoltri di porta |
| `tools/check_page.py` | sulla pagina viva: ogni `getElementById` ha il suo elemento, ogni funzione chiamata e' definita, il JS compila |
| `tools/probe_link.py` | determina i parametri di apertura di un collegamento |
| `tools/daqio.py` | I/O condiviso; `channels=` legge solo alcuni canali |
| `tools/daq_control.py` | pagina di controllo (8766): configurazione, run, coda, scan |
| `tools/psu_control.py` | alimentatore Aim-TTi PLH250-P via LAN (porta 9221): stato, rampa in tensione, limiti, log su CSV. IP da DHCP: `find` lo ritrova |
| `tools/posta.py` | spedizione delle mail via pmg1/pmg2, con i tentativi per il greylisting; `--prova indirizzo` |
| `tools/riepilogo_giorno.py` | il riepilogo di un giorno dai .log delle run, col grafico del rate; `--png`, `--invia` |
| `tools/notifiche.py` | lista delle persone, invio in sottofondo, storico, riepilogo all'ora fissata, diagnosi di fine run |
| `tools/dcr_scala.py` | DCR dei SiPM contro soglia da una run a trigger software: scala a gradini, crosstalk, DCR a 0.5/1.5/2.5 p.e. (tre stime) |
| `tools/WCFastCheck.ipynb` | analisi offline; legge i due formati e i `.gz` via `daqio` |

Le misure stanno in `measurements/*.json` con i dati grezzi, e gli script di
plot li rileggono da lì: i grafici si rifanno, i numeri no.

Le due pagine web girano sul PC DAQ e sopravvivono alla caduta della rete:

```
python3 tools/daq_control.py -b 0.0.0.0        # controllo, porta 8766
```

Il monitor conviene accenderlo **da dentro la pagina** (*Start monitor*): così
si aggancia da solo a `OutputDir`, che si cambia dalla pagina e che altrimenti
resterebbe disallineato. `localhost:8766/monitor/` inoltra al monitor sulla
stessa porta del controllore: da VPN o da inoltro di porta quasi sempre è
inoltrata solo quella.

Il monitor ha **una scheda per canale** (ultime 100 forme d'onda col
cancello colorato e la finestra del piedistallo in grigio, e carica) con una rotella per le impostazioni di quel
canale: cancello, piedistallo, scale; vuote = automatiche. Dal 2026-10-19 le
impostazioni stanno in **`<data>/monitor-impostazioni.json`, uguali per
chiunque apra il monitor**, non piu' nel browser di ciascuno. Un monitor di
prova va lanciato con `--impostazioni /tmp/qualcosa.json`, se no cambia
quelle di tutti.

Nel monitor l'interruttore **`details`** accende cancello, finestra del
piedistallo, calibrazione e avvertimenti da messa a punto. Spento di serie:
durante la presa dati sono rumore sul grafico, quando si confrontano due
misure servono — una carica senza il suo cancello non è confrontabile.

Le cose decise e non ancora fatte stanno in `TODO.md`.

---

## Come lavorare qui

**Misurare prima di attribuire una causa.** Questo progetto ha punito ogni
ipotesi non verificata: un rate del generatore assunto invece che misurato ha
prodotto una conclusione sbagliata di un fattore cinque; i confini dei passi di
uno scan dedotti per aritmetica invece che registrati hanno fatto analizzare il
segmento sbagliato due volte.

**Se qualcosa smette di funzionare dopo una mia modifica, il sospetto sono io.**
L'utente sa usare bene il computer: quando dice "non va", il problema è quasi
sempre nel codice. Ho già dato la colpa al browser quando invece era un mio
riferimento a un elemento rimosso.

**Dire quando non si sa.** Una spiegazione plausibile ma non verificata va
dichiarata tale. È successo di proporre un meccanismo che, guardato meglio,
prevedeva il segno opposto a quello misurato.

**Si scrive per un fisico sperimentale, non per un programmatore.** Le
spiegazioni lunghe vanno sulla *fisica* e sulla misura -- perche' un
piedistallo per evento, perche' 2.5 GS/s invece di 1 -- non sulla meccanica
del codice. Il software qui si giudica da quello: se un numero e' plausibile,
se un grafico si legge, se la pagina dice la verita' su cosa sta mostrando.

**Per le interfacce, prima una proposta da guardare.** Un PNG, o la pagina su
una porta di prova, e poi si implementa su quello che e' stato approvato o
corretto. Discutere un layout a parole costa piu' giri che mostrarlo.

**Commenti e messaggi di log in italiano**, come il resto del codice.
**Le pagine web invece sono in inglese** — monitor e controllore — perche'
vengono mostrate anche fuori dal gruppo. Il log della DAQ che compare nella
pagina resta com'e': viene dal C++ e non fa parte dell'interfaccia. I commenti
spiegano *perché*, non *cosa*: in particolare perché una scelta non ovvia è
necessaria, così nessuno la "risistema" più tardi.
