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

- PC DAQ: alias SSH `daq-pc` → `192.168.99.104`, utente `daq`, repo in
  `/home/daq/daq-standalone`. Login a chiave.
- Digitizer V1742: `192.168.99.105` (non è il PC DAQ).
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
- **Il rate del generatore si prende dal plateau dello scan, non dallo
  scaler.** Il 2026-10-09 lo scaler dava 88 Hz, il plateau 91.6, e durante la
  messa a punto la DAQ ne ha visti 12.9 per un'ora: era il sync che precedeva
  l'impulso di 75 us, cinquantacinque finestre di acquisizione, quindi
  l'impulso non poteva essere dentro nessuna. Al plateau e' la DAQ stessa a
  contare, con la soglia ben sotto l'impulso.
- **`TriggerPolarity`, `DCOffset` e il segno dell'impulso sono una cosa
  sola.** Piedistallo in basso (DCOffset alto) significa impulsi positivi e
  quindi fronte di salita. Sbagliare uno dei tre non dà errori: la soglia si
  posa dove il segnale non va mai e la run acquisisce zero eventi. Costato una
  run di 52 s a vuoto passando dai PMT al SiPM.

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

**Commenti e messaggi di log in italiano**, come il resto del codice.
**Le pagine web invece sono in inglese** — monitor e controllore — perche'
vengono mostrate anche fuori dal gruppo. Il log della DAQ che compare nella
pagina resta com'e': viene dal C++ e non fa parte dell'interfaccia. I commenti
spiegano *perché*, non *cosa*: in particolare perché una scelta non ovvia è
necessaria, così nessuno la "risistema" più tardi.
