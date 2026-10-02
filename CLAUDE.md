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

- PC DAQ: alias SSH `daq-pc` → `192.168.99.108`, utente `daq`, repo in
  `/home/daq/daq-standalone`. Login a chiave.
- Digitizer V1742: `192.168.99.105` (non è il PC DAQ).
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

## Strumenti

| | |
|---|---|
| `tools/run_check.py` | riepilogo di una run: ampiezza, larghezza, purezza, rumore, deriva, rate |
| `tools/noise_scan.py` | scan in soglia a run in corso; scrive i confini in eventi di ogni passo |
| `tools/live_monitor.py` | monitor HTTP; `-b 0.0.0.0` per evitare inoltri di porta |
| `tools/check_page.py` | verifica che ogni `getElementById` abbia il suo elemento |
| `tools/probe_link.py` | determina i parametri di apertura di un collegamento |
| `tools/daqio.py` | I/O condiviso; `channels=` legge solo alcuni canali |

Le misure stanno in `measurements/*.json` con i dati grezzi, e gli script di
plot li rileggono da lì: i grafici si rifanno, i numeri no.

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
